"""Single shared instance of the parsed spec and route index.

Both the harness and the stub need `ROUTES`; building it twice would risk the
two disagreeing about, say, a success status, which would make a stub pass
mean nothing.
"""

from __future__ import annotations

from .openapi_index import build_routes, load_spec

SPEC = load_spec()
ROUTES = build_routes(SPEC)
