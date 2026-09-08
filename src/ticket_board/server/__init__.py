"""Ticket Board API server (T-180).

Routes, project-scoped credentials and the SSE stream for the frozen T-178
contract, on top of the T-179 board store.

    from ticket_board.server import BoardServer, serve

`BoardServer.handle(request)` is the whole API; `serve()` puts it on a loopback
socket. What this package does *not* contain: the master routing loop (T-182),
messaging and delivery (T-187) and the runner surface (T-188/T-192). Those
routes are declared and answer a contract-shaped 404 naming their owner.
"""

from .app import BoardServer
from .httpd import DEFAULT_PORT, make_server, serve
from .wire import Request, Response

__all__ = ["BoardServer", "Request", "Response", "serve", "make_server",
           "DEFAULT_PORT"]
