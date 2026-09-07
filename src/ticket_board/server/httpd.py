"""Loopback HTTP adapter.

This is the only module that knows HTTP has a wire format. It is deliberately
thin -- the router is where the behaviour lives -- and it exists mainly so the
SSE path can be exercised over a real socket, which is the only way to test
that a reconnect with `Last-Event-ID` actually resumes.

V1 binds loopback. Not a placeholder for a hardening step later: nothing in this
build authenticates a remote operator (there is no sign-in route to
authenticate them *with*), so a bind to 0.0.0.0 would publish an unauthenticated
board to the network. `serve()` refuses a non-loopback host unless the caller
passes `allow_remote=True` and has therefore said so out loud.
"""

from __future__ import annotations

import http.server
import ipaddress
import json
import os
import socketserver
import threading
from urllib.parse import urlsplit

from .app import BoardServer
from .wire import Request

DEFAULT_PORT = 4319

# T-301: the mirror of BoardClient's unbounded response read (adapter.py) is
# this handler trusting a client-declared Content-Length and handing it
# straight to rfile.read(length). No contract request body multiplies a
# maxLength field by a maxItems array (the schema has exactly one maxItems,
# on a response), and the single largest field is a 16000-char message body
# -- so any legitimate request stays in the tens of KB. This cap is checked
# BEFORE the read, so a declared length above it is refused without ever
# blocking on -- or buffering -- the bytes behind it.
MAX_REQUEST_BODY_BYTES = int(
    os.environ.get("TICKET_BOARD_SERVER_MAX_REQUEST_BYTES", 1024 * 1024)
)


class _Handler(http.server.BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server_version = "TicketBoard/1.0"

    # The default handler logs every request to stderr, which on a local board
    # means the operator's terminal fills with noise. Silence it; the audit
    # trail is the record that matters.
    def log_message(self, fmt, *args):
        pass

    def _dispatch(self):
        split = urlsplit(self.path)
        length = int(self.headers.get("Content-Length") or 0)
        if length > MAX_REQUEST_BODY_BYTES:
            self._reject_oversized_body(length)
            return
        body = self.rfile.read(length) if length else b""
        request = Request(
            self.command, split.path, query=split.query,
            headers={k: v for k, v in self.headers.items()}, body=body,
            remote_addr=self.client_address[0],
        )
        response = self.server.board.handle(request)

        if response.stream is not None:
            self._write_stream(response)
        else:
            self._write_body(response)

    do_GET = do_POST = do_DELETE = do_PUT = do_PATCH = _dispatch

    def _reject_oversized_body(self, declared_length):
        """Refuse a request body over the cap without ever reading it.

        Mirrors the 500 path's departure from the contract's closed
        `ErrorResponse.status` enum (400/401/403/404/409/422/429, see
        docs/api-notes.md): there is no member for "the body was too large to
        accept", and 413 is the honest HTTP status for that, so this uses the
        same envelope shape with a status the schema cannot validate rather
        than force-fitting one that lies about the cause.

        The connection is closed rather than kept alive: the client's
        undrained body bytes are still arriving on this socket, and reading
        them to get back to a clean request boundary is exactly the unbounded
        read this cap exists to avoid.
        """
        self.close_connection = True
        message = "Request body of %d bytes exceeds the %d byte cap." % (
            declared_length, MAX_REQUEST_BODY_BYTES,
        )
        payload = json.dumps({"error": {
            "code": "request_too_large", "status": 413, "message": message,
        }}).encode("utf-8")
        self.send_response(413)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.send_header("Connection", "close")
        self.end_headers()
        self.wfile.write(payload)

    def _write_body(self, response):
        payload = response.encoded()
        self.send_response(response.status)
        self.send_header("Content-Type", response.content_type)
        self.send_header("Content-Length", str(len(payload)))
        for key, value in response.headers.items():
            self.send_header(key, value)
        self.end_headers()
        self.wfile.write(payload)

    def _write_stream(self, response):
        # No Content-Length and no chunked framing: an event stream ends when
        # the connection does, so the connection must not be reused.
        self.close_connection = True
        self.send_response(response.status)
        self.send_header("Content-Type", response.content_type)
        for key, value in response.headers.items():
            self.send_header(key, value)
        self.send_header("Connection", "close")
        self.end_headers()
        try:
            for chunk in response.stream:
                self.wfile.write(chunk)
                self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError):
            # The dashboard navigated away. Not an error worth a trace.
            pass


class _Server(socketserver.ThreadingMixIn, http.server.HTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def handle_error(self, request, client_address):
        """A client that hangs up is not a server error.

        The default prints a stack trace. On a local board that means closing a
        dashboard tab -- or ending an SSE subscription, which is how every SSE
        connection ends -- fills the operator's terminal with tracebacks for
        something entirely normal. Anything else still surfaces.
        """
        import sys

        kind = sys.exc_info()[0]
        if kind is not None and issubclass(kind, (BrokenPipeError,
                                                  ConnectionResetError,
                                                  ConnectionAbortedError)):
            return
        super().handle_error(request, client_address)


def make_server(board=None, *, host="127.0.0.1", port=DEFAULT_PORT):
    """Bind a server socket, optionally attaching the board now.

    `board` is optional so a caller can bind *first* and build the board
    afterwards: until the socket exists, nobody knows what port the OS handed
    out for `port=0`, and the board needs that port to describe itself. Attach
    it to `httpd.board` before `serve_forever()` -- a bound-but-not-serving
    socket accepts nothing, so there is no window in which a request can arrive
    without a board to answer it.
    """
    httpd = _Server((host, port), _Handler)
    httpd.board = board
    return httpd


# 0.0.0.0 and :: mean "every interface". They are bind targets, not addresses:
# nothing can dial them, so neither may be advertised as a URL.
_WILDCARD_HOSTS = {"0.0.0.0", "::", ""}


def advertised_url(server_address):
    """The URL a client can actually dial, taken from the *bound* socket.

    Never build this from the requested host and port. `port=0` means "any free
    port" and the literal 0 is not an address -- persisting it yields
    `http://127.0.0.1:0`, which cannot be connected to and does not match the
    Origin a browser will send, so every cookie-authenticated write from the
    board's own dashboard is refused. `socket.getsockname()`, which is what
    `server_address` holds after binding, is the only thing that knows the
    truth. The same hazard applies to the host: a wildcard bind is not a
    dialable address either.
    """
    host, port = server_address[0], server_address[1]
    if host in _WILDCARD_HOSTS:
        # Loopback is the honest choice: it is the one interface a wildcard
        # bind is certainly reachable on, and this build authenticates no
        # remote operator anyway.
        host = "127.0.0.1"
    if ":" in host:  # an IPv6 literal has to be bracketed in a URL
        host = "[{}]".format(host)
    return "http://{}:{}".format(host, port)


def is_loopback(host):
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return host in ("localhost",)


def _origins_for(base_url):
    """Origins a browser may send for *this* board, and no others.

    `BoardServer`'s default allowlist is `{base_url, 127.0.0.1:4319,
    localhost:4319}`. It already contains this board's own origin, so it never
    refused the dashboard -- the 403 in this ticket came from `base_url` itself
    being `:0`, not from the allowlist. What the default does do is keep
    trusting 4319 on a board that is not on 4319, so a dashboard served by a
    *different* board on the default port can drive this one. That is a
    narrower defect than the port bug and it is fixed here because the bound
    address is now known at exactly the point the allowlist is built.

    `127.0.0.1` and `localhost` are listed as a pair because they are the same
    origin to everyone except a string comparison, and an operator who types
    one should not get a 403 for not having typed the other. This is the same
    trust the default already granted them on 4319 -- applied to the real port
    rather than a guessed one -- not a widening of it.
    """
    origins = {base_url}
    split = urlsplit(base_url)
    aliases = {"127.0.0.1": "localhost", "localhost": "127.0.0.1"}
    twin = aliases.get(split.hostname or "")
    if twin:
        origins.add("{}://{}:{}".format(split.scheme, twin, split.port))
    return origins


def serve(db_path, *, host="127.0.0.1", port=DEFAULT_PORT, project_name="Board",
          state_dir=None, allow_remote=False, block=True):
    """Start a board server and hand the operator a session.

    Returns `(httpd, thread, credentials)`. The credential pair is written to
    `<state_dir>/operator-session.json` with mode 0600, the way a local notebook
    server hands out its token -- the contract has no sign-in route, so this is
    the bootstrap and it is deliberately visible rather than hidden.
    """
    if not is_loopback(host) and not allow_remote:
        raise ValueError(
            "Refusing to bind {}: this build has no remote operator "
            "authentication. Pass allow_remote=True if you have put it behind "
            "something that does.".format(host)
        )
    # Bind BEFORE describing the board. With port=0 the OS picks the port, and
    # it is only knowable from the bound socket -- so the board's base_url, its
    # CSRF allowlist and the url handed to the dashboard are all derived from
    # `httpd.server_address`, never from the `port` argument.
    httpd = make_server(host=host, port=port)
    try:
        base_url = advertised_url(httpd.server_address)
        board = BoardServer(db_path, base_url=base_url,
                            allowed_origins=_origins_for(base_url))
        httpd.board = board
        project = _ensure_project(board, project_name)
        session = board.bootstrap_operator(project["id"])

        state_dir = state_dir or os.path.dirname(os.path.abspath(str(db_path)))
        path = os.path.join(state_dir, "operator-session.json")
        _write_private(path, {
            "project_id": project["id"],
            "session_token": session["session_token"],
            "csrf_token": session["csrf_token"],
            "url": base_url,
        })
    except BaseException:
        # The socket is already bound at this point; failing without closing it
        # leaks the port for the life of the process.
        httpd.server_close()
        raise

    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    if block:  # pragma: no cover - interactive path
        try:
            thread.join()
        except KeyboardInterrupt:
            httpd.shutdown()
    return httpd, thread, {"path": path, "project_id": project["id"], **session}


def _ensure_project(board, name):
    row = board.store.conn.execute(
        "SELECT id FROM projects ORDER BY created_at LIMIT 1"
    ).fetchone()
    if row is not None:
        return board.store.get_project(row["id"])
    return board.store.create_project(name)


def _write_private(path, payload):
    """Write 0600 before any bytes land, not after.

    Two details, both load-bearing:

    * The mode goes to `os.open`, not to a `chmod` afterwards. Creating the file
      readable and tightening it later leaves a window in which the session
      token is readable by anyone on the machine.
    * The old file is *unlinked* first, because `O_CREAT`'s mode is ignored for a
      file that already exists. A restart onto a previously loosened file would
      otherwise keep its permissions, and `O_EXCL` makes that a hard failure
      rather than a silent one.
    """
    if os.path.lexists(path):
        os.unlink(path)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w") as fh:
        json.dump(payload, fh, indent=2)
    return path
