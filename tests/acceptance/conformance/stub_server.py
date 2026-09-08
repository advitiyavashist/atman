"""A tiny fixture-replay stub: proves the harness catches a broken contract.

T-180 (the real board API) does not exist yet, so there is nothing live to
point this harness at. This stub serves the exact fixtures wired up in
`cases.py` back verbatim, keyed on (method, path) plus -- where one route has
several request variants, e.g. `postRunEvent` -- which known request fixture
the body matches byte-for-byte. It also enforces frozen decision 1 (a body
`actor` field is 400 `malformed_request`) since that's part of what the
harness checks.

It is deliberately dumb: it does not implement claims, versions, leases or
any other business rule from `docs/contracts/dependent-notes.md`. Its only
job is to be a known-good target so `run_conformance.py` and
`test_self_check.py` can prove the harness itself is sound, and a known-BAD
target (via `mutations=`) so they can prove it fails loudly on a broken
contract.
"""

from __future__ import annotations

import json
import re
from collections import defaultdict
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Thread
from typing import Callable
from urllib.parse import urlsplit

from .cases import ALL_CASES, Case
from .fixtures import FIXTURES
from .routes import ROUTES

REJECTED_ACTOR_BODY = json.loads((FIXTURES / "errors" / "400-malformed-request.json").read_text())

MutationFn = Callable[[object], object]


def _bodies_match(fixture_body: object, sent_body: object) -> bool:
    """The harness generates a fresh `request_id` per call (see harness.py),
    so it never equals the literal one baked into the fixture. Everything
    else must still match byte-for-byte."""
    if isinstance(fixture_body, dict) and isinstance(sent_body, dict):
        fixture_body = {k: v for k, v in fixture_body.items() if k != "request_id"}
        sent_body = {k: v for k, v in sent_body.items() if k != "request_id"}
    return fixture_body == sent_body


def _path_pattern(path_template: str) -> re.Pattern:
    pattern = re.sub(r"\{\w+\}", "[^/]+", path_template)
    return re.compile(f"^{pattern}$")


class FixtureReplayStub:
    """Serves `cases.py` fixtures over HTTP. Not thread-safe to mutate while running."""

    def __init__(self, cases: list[Case] = ALL_CASES, mutations: dict[str, MutationFn] | None = None):
        self.mutations = mutations or {}
        self._table: dict[tuple[str, re.Pattern], list[Case]] = defaultdict(list)
        for case in cases:
            route = ROUTES[case.operation_id]
            self._table[(route.method, _path_pattern(route.path))].append(case)
        self._httpd: ThreadingHTTPServer | None = None
        self._thread: Thread | None = None

    def _lookup(self, method: str, path: str) -> list[Case] | None:
        for (route_method, pattern), cases in self._table.items():
            if route_method == method and pattern.match(path):
                return cases
        return None

    def resolve(self, method: str, path: str, body: object | None) -> tuple[int, object]:
        cases = self._lookup(method, path)
        if cases is None:
            return 404, {"error": {"code": "not_found", "status": 404, "message": "stub: no such route"}}

        if isinstance(body, dict) and "actor" in body:
            return 400, REJECTED_ACTOR_BODY

        if body is None:
            case = cases[0]
        else:
            case = next((c for c in cases if c.request is not None
                         and _bodies_match(c.request.load(), body)), None)
            if case is None:
                return 500, {"error": {"code": "malformed_request", "status": 500,
                                        "message": "stub: request body did not match any known fixture"}}

        route = ROUTES[case.operation_id]
        response_body = case.response.load()
        mutate = self.mutations.get(case.label)
        if mutate is not None:
            response_body = mutate(response_body)
        return route.success_status, response_body

    def start(self) -> str:
        stub = self

        class Handler(BaseHTTPRequestHandler):
            def _handle(self) -> None:
                parsed = urlsplit(self.path)
                length = int(self.headers.get("Content-Length") or 0)
                raw = self.rfile.read(length) if length else b""
                body = json.loads(raw) if raw else None
                status, payload = stub.resolve(self.command, parsed.path, body)
                encoded = json.dumps(payload).encode()
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(encoded)))
                self.end_headers()
                self.wfile.write(encoded)

            do_GET = _handle
            do_POST = _handle
            do_DELETE = _handle

            def log_message(self, *_args) -> None:
                pass

        self._httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self._thread = Thread(target=self._httpd.serve_forever, daemon=True)
        self._thread.start()
        port = self._httpd.server_address[1]
        return f"http://127.0.0.1:{port}"

    def stop(self) -> None:
        if self._httpd is not None:
            self._httpd.shutdown()
            self._httpd.server_close()
        if self._thread is not None:
            self._thread.join(timeout=5)
