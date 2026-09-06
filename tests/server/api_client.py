"""The in-process API client the server tests drive.

It lives outside `conftest.py` on purpose. Test directories in this repo have no
`__init__.py`, so pytest imports each `conftest.py` under the bare name
`conftest` -- and with two of them (`tests/storage/` and here) a *deferred*
`from conftest import ...` inside a test function can resolve to the other
directory's module by the time it runs. A uniquely named module cannot.
"""

import json
import uuid

from ticket_board.server.wire import Request

ORIGIN = "http://127.0.0.1:4319"


def rid():
    """A fresh idempotency key. Reusing one across calls is itself a 409."""
    return str(uuid.uuid4())


class Client:
    """A credentialled caller. `as_operator()` / `as_agent()` return new ones."""

    def __init__(self, server, *, project_id=None, cookie=None, csrf=None,
                 token=None, origin=ORIGIN):
        self.server = server
        self.project_id = project_id
        self.cookie = cookie
        self.csrf = csrf
        self.token = token
        self.origin = origin

    def with_(self, **kwargs):
        merged = {"project_id": self.project_id, "cookie": self.cookie,
                  "csrf": self.csrf, "token": self.token, "origin": self.origin}
        merged.update(kwargs)
        return Client(self.server, **merged)

    def headers(self, extra=None):
        headers = {}
        if self.project_id:
            headers["X-Project-Id"] = self.project_id
        if self.token:
            headers["Authorization"] = "Bearer " + self.token
        if self.cookie:
            headers["Cookie"] = "tb_session=" + self.cookie
        if self.csrf:
            headers["X-CSRF-Token"] = self.csrf
        if self.origin:
            headers["Origin"] = self.origin
        headers.update(extra or {})
        return {k: v for k, v in headers.items() if v is not None}

    def request(self, method, path, *, body=None, query=None, headers=None):
        return self.server.handle(Request(
            method, path, query=query or "",
            headers=self.headers(headers),
            body=json.dumps(body).encode() if body is not None else b"",
        ))

    def get(self, path, **kwargs):
        return self.request("GET", path, **kwargs)

    def post(self, path, body=None, **kwargs):
        return self.request("POST", path, body=body or {}, **kwargs)

    def delete(self, path, body=None, **kwargs):
        return self.request("DELETE", path, body=body or {}, **kwargs)
