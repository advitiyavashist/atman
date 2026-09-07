"""T-301: the server-side mirror of BoardClient's unbounded response read.

`httpd._Handler._dispatch` used to hand a client-declared `Content-Length`
straight to `self.rfile.read(length)`. A caller that declares a huge length
and either sends that many bytes, or sends none at all and leaves the
connection open, gets the handler to allocate or block on bytes it never
needed -- before a single line of routing or validation runs.

These tests use a real socket (like `test_live_http.py`'s reconnect and race
tests) because the bug is in what the handler does *before* it has a body to
hand to the router; driving `BoardServer.handle` in process, the way the rest
of `tests/server/` does, never exercises `rfile.read` at all.
"""

import http.client
import json
import socket
import threading
import time

import pytest

from ticket_board.server.httpd import MAX_REQUEST_BODY_BYTES, make_server


@pytest.fixture()
def live(server, project):
    httpd = make_server(server, host="127.0.0.1", port=0)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    try:
        yield "127.0.0.1", httpd.server_address[1]
    finally:
        httpd.shutdown()
        httpd.server_close()
        thread.join(timeout=5)


def test_oversized_declared_length_is_refused_without_reading_the_body(live):
    """The proof this isn't just a post-hoc check: no body is ever sent.

    A declared length past the cap is refused from the header alone. If the
    handler still called `rfile.read(length)` for the undelivered bytes, that
    call would block on this open, otherwise-silent socket -- there is
    nothing else to receive -- and the `recv` below would hit its timeout
    instead of returning a response. Getting a prompt 413 is only possible if
    the oversized read never happened.
    """
    declared = MAX_REQUEST_BODY_BYTES + 1
    sock = socket.create_connection(live, timeout=5)
    try:
        sock.sendall(
            ("POST /tickets HTTP/1.1\r\n"
             "Host: {}:{}\r\n"
             "Content-Type: application/json\r\n"
             "Content-Length: {}\r\n"
             "\r\n").format(live[0], live[1], declared).encode("ascii")
        )
        # Deliberately send zero body bytes. A blocking rfile.read(declared)
        # would hang here for the whole timeout; getting a reply proves it
        # didn't run.
        started = time.monotonic()
        response = _read_http_response(sock, deadline=4.0)
        elapsed = time.monotonic() - started
    finally:
        sock.close()

    assert elapsed < 4.0, "server blocked waiting for a body it should have refused"
    assert response["status"] == 413
    body = json.loads(response["body"])
    assert body["error"]["status"] == 413
    assert str(declared) in body["error"]["message"]
    assert str(MAX_REQUEST_BODY_BYTES) in body["error"]["message"]


def test_negative_declared_length_does_not_trigger_unbounded_read(live):
    """T-308: a declared length of -1 used to bypass the cap check entirely.

    `-1 > MAX_REQUEST_BODY_BYTES` is false, so the pre-fix gate let it through
    unchanged into `self.rfile.read(-1)` -- which on a `BufferedReader` means
    read-until-EOF, i.e. block until the client closes the connection. This
    socket is deliberately left open and no body is sent, so a prompt
    response is only possible if the negative length was rejected (treated
    as zero) before the read, never handed to it.
    """
    sock = socket.create_connection(live, timeout=5)
    try:
        sock.sendall(
            ("POST /tickets HTTP/1.1\r\n"
             "Host: {}:{}\r\n"
             "Content-Type: application/json\r\n"
             "Content-Length: -1\r\n"
             "\r\n").format(*live).encode("ascii")
        )
        started = time.monotonic()
        response = _read_http_response(sock, deadline=4.0)
        elapsed = time.monotonic() - started
    finally:
        sock.close()

    assert elapsed < 4.0, "server blocked on rfile.read(-1) instead of rejecting it"
    assert response["status"] != 0, "no response arrived before the deadline"


def test_non_integer_declared_length_does_not_crash_the_handler(live):
    """A garbage Content-Length used to raise ValueError straight out of
    `int(...)`, unhandled, before the cap check ever ran. It must be treated
    as if no length were declared (0 bytes), not crash and not be handed to
    `rfile.read`.
    """
    sock = socket.create_connection(live, timeout=5)
    try:
        sock.sendall(
            ("POST /tickets HTTP/1.1\r\n"
             "Host: {}:{}\r\n"
             "Content-Type: application/json\r\n"
             "Content-Length: not-a-number\r\n"
             "\r\n").format(*live).encode("ascii")
        )
        started = time.monotonic()
        response = _read_http_response(sock, deadline=4.0)
        elapsed = time.monotonic() - started
    finally:
        sock.close()

    assert elapsed < 4.0, "server hung instead of treating the garbage header as 0"
    assert response["status"] != 0, "no response arrived before the deadline"


def test_oversized_body_is_refused_even_when_the_client_sends_it_all(live):
    """Belt and suspenders: refused too when the bytes really are there.

    The server answers 413 and closes the connection as soon as it sees the
    declared length, without waiting for the body. A real client sending that
    body over a real socket can therefore see its own `send` fail with a
    reset partway through -- itself evidence the server did not sit there
    absorbing the oversized body -- so that outcome is treated as a pass
    here, same as a clean 413 read back on a connection where the OS
    buffered enough of the reply first.
    """
    declared = MAX_REQUEST_BODY_BYTES + 1024
    sock = socket.create_connection(live, timeout=5)
    try:
        sock.sendall(
            ("POST /tickets HTTP/1.1\r\n"
             "Host: {}:{}\r\n"
             "Content-Type: application/json\r\n"
             "Content-Length: {}\r\n"
             "\r\n").format(live[0], live[1], declared).encode("ascii")
        )
        try:
            sock.sendall(b"x" * declared)
        except (BrokenPipeError, ConnectionResetError):
            return  # the server hung up rather than accept the oversized body
        response = _read_http_response(sock, deadline=5.0)
    finally:
        sock.close()

    assert response["status"] == 413
    body = json.loads(response["body"])
    assert body["error"]["code"] == "request_too_large"


def test_body_exactly_at_the_cap_is_not_rejected_by_the_length_gate(live):
    """At the cap, the request reaches the router instead of stopping at 413.

    The payload is nonsense as far as the ticket schema is concerned, so this
    still fails -- but with a contract validation error, not a 413. That
    difference is the proof the length gate itself has an off-by-one-free
    boundary: it does not reject the exact cap.
    """
    prefix, suffix = b'{"request_id":"r","filler":"', b'"}'
    filler = b"A" * (MAX_REQUEST_BODY_BYTES - len(prefix) - len(suffix))
    payload = prefix + filler + suffix
    assert len(payload) == MAX_REQUEST_BODY_BYTES

    conn = http.client.HTTPConnection(*live, timeout=15)
    try:
        conn.putrequest("POST", "/tickets")
        conn.putheader("Content-Type", "application/json")
        conn.putheader("Content-Length", str(len(payload)))
        conn.endheaders()
        conn.send(payload)
        response = conn.getresponse()
        status = response.status
        response.read()
    finally:
        conn.close()

    assert status != 413


def _read_http_response(sock, *, deadline):
    sock.settimeout(deadline)
    raw = b""
    while b"\r\n\r\n" not in raw:
        chunk = sock.recv(4096)
        if not chunk:
            break
        raw += chunk
    head, _, rest = raw.partition(b"\r\n\r\n")
    lines = head.decode("ascii").split("\r\n")
    status = int(lines[0].split(" ", 2)[1])
    headers = {}
    for line in lines[1:]:
        name, _, value = line.partition(":")
        headers[name.strip().lower()] = value.strip()
    length = int(headers.get("content-length", "0"))
    while len(rest) < length:
        chunk = sock.recv(4096)
        if not chunk:
            break
        rest += chunk
    return {"status": status, "headers": headers, "body": rest[:length].decode("utf-8")}
