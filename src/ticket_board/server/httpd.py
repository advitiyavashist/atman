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


def make_server(board, *, host="127.0.0.1", port=DEFAULT_PORT):
    httpd = _Server((host, port), _Handler)
    httpd.board = board
    return httpd


def is_loopback(host):
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return host in ("localhost",)


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
    board = BoardServer(db_path, base_url="http://{}:{}".format(host, port))
    project = _ensure_project(board, project_name)
    session = board.bootstrap_operator(project["id"])

    state_dir = state_dir or os.path.dirname(os.path.abspath(str(db_path)))
    path = os.path.join(state_dir, "operator-session.json")
    _write_private(path, {
        "project_id": project["id"],
        "session_token": session["session_token"],
        "csrf_token": session["csrf_token"],
        "url": "http://{}:{}".format(host, port),
    })

    httpd = make_server(board, host=host, port=port)
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
