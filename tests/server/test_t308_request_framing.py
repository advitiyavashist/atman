"""T-308: the T-301 byte cap is only as good as the Content-Length parse.

T-301 added `if length > MAX_REQUEST_BODY_BYTES: reject`. sonnet-tickets'
adversarial pass found that check never fires unless the declared length is a
plain positive integer, so the cap was bypassable by lying about the framing
rather than by exceeding it. Measured against the pre-fix tree:

  Content-Length: -1        -1 is not > the cap, and `rfile.read(-1)` reads
                            until EOF. 4 MiB streamed in and the handler was
                            still blocked, with no response sent -- the exact
                            unbounded read T-301 exists to close.
  Content-Length: 10, 99999999
                            duplicate headers are first-wins, so the cap was
                            evaluated against the 10 and the request was
                            dispatched. An intermediary honouring the LAST
                            value disagrees with this server about where the
                            body ends: request smuggling.
  Content-Length: abc       uncaught ValueError out of _dispatch; the
                            connection thread died with a traceback and the
                            client got a dropped socket, not an error.
  Transfer-Encoding: chunked
                            no Content-Length, so the handler read 0 bytes,
                            dispatched an EMPTY body, and left the chunked
                            octets in the buffer for the next keep-alive
                            request to parse as a request line.

Every case here is refused as 400, which the contract's closed
`ErrorResponse.status` enum declares -- these add no conformance departure.
Only T-301's pre-existing 413 sits outside the enum.

Real sockets, for the same reason the T-301 file uses them: the defect is in
what the handler does *before* there is a body to route, so driving
`BoardServer.handle` in process never reaches it.
"""

import json
import socket
import threading
import time

import pytest

from ticket_board.server.httpd import MAX_REQUEST_BODY_BYTES, make_server

from test_t301_bounded_request_body import _read_http_response  # noqa: F401


@pytest.fixture()
def live(server, project):
    httpd = make_server(server, host="127.0.0.1", port=0)
    httpd._t308_project_id = project["id"]
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    try:
        yield "127.0.0.1", httpd.server_address[1], project["id"]
    finally:
        httpd.shutdown()
        httpd.server_close()
        thread.join(timeout=5)


def _send(live, headers, body=b""):
    """Open a raw connection, send a POST with `headers`, return the socket."""
    sock = socket.create_connection((live[0], live[1]), timeout=5)
    request = (
        "POST /tickets HTTP/1.1\r\nHost: {}:{}\r\n"
        "Content-Type: application/json\r\nX-Project-Id: {}\r\n{}\r\n"
    ).format(live[0], live[1], live[2], headers)
    sock.sendall(request.encode("ascii") + body)
    return sock


def _refusal(live, headers, body=b"", deadline=4.0):
    """Send a malformed-framing request; assert a prompt refusal, return it.

    The two pre-fix failure modes are distinguished deliberately, because both
    look like "no response" from the client and they are different bugs:
    a blocking `rfile.read` times out, while a ValueError out of `_dispatch`
    kills the connection thread and drops the socket. Neither is allowed to
    surface as a parse crash inside the shared response helper -- thered has to
    name the harm.
    """
    sock = _send(live, headers, body)
    try:
        started = time.monotonic()
        try:
            response = _read_http_response(sock, deadline=deadline)
        except socket.timeout:
            raise AssertionError(
                "no response within %ss: the handler blocked reading a body it "
                "should have refused from the headers alone" % deadline)
        except (IndexError, ValueError):
            raise AssertionError(
                "connection dropped with no HTTP response: the handler thread "
                "died parsing the framing instead of answering")
        elapsed = time.monotonic() - started
    finally:
        sock.close()
    assert elapsed < deadline, "server blocked instead of refusing from the headers"
    return response


@pytest.mark.parametrize("declared", ["-1", "-4194304"])
def test_a_negative_content_length_cannot_trigger_an_unbounded_read(live, declared):
    """The critical T-308 finding.

    Pre-fix this reached `rfile.read(-1)`, i.e. read-until-EOF. The test sends
    NO body at all: a handler that started that read would block on this
    otherwise-silent socket until the deadline, so a prompt 400 is only
    possible if the read never began.
    """
    response = _refusal(live, "Content-Length: {}\r\n".format(declared))

    assert response["status"] == 400, response
    error = json.loads(response["body"])["error"]
    assert error["status"] == 400
    # The refusal names the offending value but must not echo body content.
    assert declared in error["message"]


def test_a_negative_length_does_not_swallow_a_streamed_body(live):
    """The stronger form: actually stream bytes after the bad header.

    Pre-fix the handler accepted 4 MiB this way without ever answering. Now
    the refusal lands and the connection closes, so the writer sees the socket
    go away long before that much data is transferred.
    """
    sock = _send(live, "Content-Length: -1\r\n")
    sent = 0
    refused_early = False
    try:
        sock.settimeout(4.0)
        for _ in range(64):  # up to 4 MiB in 64 KiB chunks
            try:
                sock.sendall(b"A" * 65536)
                sent += 65536
            except OSError:
                refused_early = True  # server closed on us: the point of the fix
                break
    finally:
        sock.close()

    assert refused_early or sent < 4 * 1024 * 1024, (
        "server absorbed %d bytes behind a negative Content-Length" % sent
    )


def test_conflicting_duplicate_content_length_headers_are_refused(live):
    """Smuggling shape: the cap was applied to whichever value came first.

    Both orders must be refused -- a fix that only rejects the pair when the
    large value happens to be first still lets `10, 99999999` through.
    """
    small_first = _refusal(
        live, "Content-Length: 10\r\nContent-Length: {}\r\n".format(MAX_REQUEST_BODY_BYTES + 1))
    assert small_first["status"] == 400, small_first

    large_first = _refusal(
        live, "Content-Length: {}\r\nContent-Length: 10\r\n".format(MAX_REQUEST_BODY_BYTES + 1))
    assert large_first["status"] == 400, large_first


def test_identical_duplicate_content_length_headers_are_still_accepted(live):
    """Both directions. Repeating the SAME value is not a disagreement, so
    refusing it would break a legitimate (if redundant) client."""
    payload = b'{"title": "dup"}'
    sock = _send(
        live,
        "Content-Length: {n}\r\nContent-Length: {n}\r\n".format(n=len(payload)),
        payload,
    )
    try:
        response = _read_http_response(sock, deadline=4.0)
    finally:
        sock.close()
    # Reaches the router: whatever it answers, it is not a framing refusal.
    assert response["status"] != 400 or "Content-Length" not in response["body"]


@pytest.mark.parametrize("declared", ["abc", "1e10", "+5", "0x10", ""])
def test_a_non_numeric_content_length_gets_an_error_not_a_dead_thread(live, declared):
    """Pre-fix these raised ValueError inside _dispatch: the connection thread
    died with a traceback to stderr and the client saw a dropped socket with
    no response at all. `+5` and `0x10` are accepted by int() but are not the
    1*DIGIT the spec requires -- exactly how a lenient parse and a strict
    intermediary come to disagree about framing."""
    response = _refusal(live, "Content-Length: {}\r\n".format(declared))
    assert response["status"] == 400, response
    assert json.loads(response["body"])["error"]["status"] == 400


def test_chunked_encoding_is_refused_not_silently_dropped(live):
    """Pre-fix: dispatched with an EMPTY body and the chunked octets left in
    the socket buffer, so the next request on the keep-alive connection parsed
    them as a request line and got a second, garbage response."""
    body = b"10\r\n" + b"B" * 16 + b"\r\n0\r\n\r\n"
    sock = _send(live, "Transfer-Encoding: chunked\r\n", body)
    try:
        raw = b""
        sock.settimeout(4.0)
        try:
            while True:
                chunk = sock.recv(4096)
                if not chunk:
                    break
                raw += chunk
        except socket.timeout:
            pass
    finally:
        sock.close()

    assert raw.startswith(b"HTTP/1.1 400"), raw[:120]
    # The desync symptom: exactly one response, not a second one parsed out of
    # the leftover chunk octets.
    assert raw.count(b"HTTP/1.") == 1, "connection desynced: %r" % raw[:200]


def test_repeated_transfer_encoding_headers_are_refused(live):
    """headers.get is first-wins; a later Transfer-Encoding must not skip refuse."""
    identity_first = _refusal(
        live,
        "Transfer-Encoding: identity\r\nTransfer-Encoding: chunked\r\n",
    )
    assert identity_first["status"] == 400, identity_first
    assert json.loads(identity_first["body"])["error"]["code"] == (
        "unsupported_transfer_encoding"
    )

    chunked_first = _refusal(
        live,
        "Transfer-Encoding: chunked\r\nTransfer-Encoding: identity\r\n",
    )
    assert chunked_first["status"] == 400, chunked_first
    assert json.loads(chunked_first["body"])["error"]["code"] == (
        "unsupported_transfer_encoding"
    )

    repeated_identity = _refusal(
        live,
        "Transfer-Encoding: identity\r\nTransfer-Encoding: identity\r\n"
        "Content-Length: 0\r\n",
    )
    assert repeated_identity["status"] == 400, repeated_identity
    assert json.loads(repeated_identity["body"])["error"]["code"] == (
        "unsupported_transfer_encoding"
    )


def test_an_ordinary_request_still_works(live):
    """The control that keeps all of the above honest: a normal
    Content-Length-framed body must still reach the router untouched. A fix
    that refuses everything would pass every other test in this file."""
    payload = json.dumps({"title": "ordinary", "role": "backend"}).encode("utf-8")
    sock = _send(live, "Content-Length: {}\r\n".format(len(payload)), payload)
    try:
        response = _read_http_response(sock, deadline=4.0)
    finally:
        sock.close()

    # It reached the router -- 401 here (no credential on this raw socket) is
    # still proof the body was framed, read and dispatched. What it must not
    # be is a refusal from _resolve_body_length.
    assert response["status"] != 413, response
    error = json.loads(response["body"]).get("error", {})
    assert error.get("code") != "unsupported_transfer_encoding", response
    assert "Content-Length" not in error.get("message", ""), (
        "ordinary request was refused at the framing layer: %s" % response["body"]
    )
