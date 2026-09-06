"""Transport-neutral request and response objects.

The router is a pure function of a `Request`, which is what makes almost every
test in `tests/server/` run without a socket. The only things that need a real
server are the ones where the socket *is* the behaviour: SSE reconnect and the
concurrent-claim race. `httpd.py` adapts these two types to
`http.server.ThreadingHTTPServer`; nothing else in the package knows that HTTP
has a wire format.
"""

from __future__ import annotations

import json
import re
from urllib.parse import parse_qs, unquote

# RFC 6265 cookie-octet, minus the characters a Set-Cookie header cannot carry
# unquoted. Session and CSRF values are minted from this alphabet, so a value
# never needs escaping and a parser never has to guess.
_COOKIE_SPLIT = re.compile(r";\s*")


class Headers:
    """Case-insensitive header access.

    HTTP header names are case-insensitive and clients genuinely do vary
    (`X-CSRF-Token` vs `x-csrf-token`); a dict lookup on the wrong case is a
    silent auth bypass in one direction and a spurious 403 in the other.
    """

    def __init__(self, items=None):
        self._items = {}
        for key, value in (items or {}).items():
            self._items[key.lower()] = value

    def get(self, name, default=None):
        return self._items.get(name.lower(), default)

    def __contains__(self, name):
        return name.lower() in self._items

    def items(self):
        return self._items.items()


class Request:
    def __init__(self, method, path, *, query=None, headers=None, body=b"",
                 remote_addr="127.0.0.1"):
        self.method = method.upper()
        self.path = path
        self.query = query if isinstance(query, dict) else parse_qs(query or "")
        self.headers = headers if isinstance(headers, Headers) else Headers(headers)
        self.body = body if isinstance(body, bytes) else str(body).encode("utf-8")
        self.remote_addr = remote_addr
        self._cookies = None

    # ------------------------------------------------------------- accessors

    def param(self, name, default=None):
        values = self.query.get(name)
        return values[0] if values else default

    @property
    def cookies(self):
        if self._cookies is None:
            self._cookies = _parse_cookies(self.headers.get("cookie", ""))
        return self._cookies

    def json_body(self):
        """Parse the body as a JSON object, or raise MalformedRequest.

        Imported lazily to keep this module free of the error hierarchy, which
        imports the storage package.
        """
        from .errors import MalformedRequest

        if not self.body:
            raise MalformedRequest("A JSON object body is required.")
        try:
            parsed = json.loads(self.body.decode("utf-8"))
        except (ValueError, UnicodeDecodeError):
            raise MalformedRequest("Body is not valid JSON.")
        if not isinstance(parsed, dict):
            raise MalformedRequest("Body must be a JSON object.")
        return parsed


class Response:
    """A finished response, or a streaming one.

    `stream` is an iterator of already-encoded chunks. When it is set, `body`
    is ignored and no Content-Length is sent -- that is the SSE path.
    """

    def __init__(self, status, body=None, *, headers=None, stream=None,
                 content_type="application/json"):
        self.status = status
        self.body = body
        self.headers = dict(headers or {})
        self.stream = stream
        self.content_type = content_type

    def encoded(self):
        if self.body is None:
            return b""
        if isinstance(self.body, bytes):
            return self.body
        return json.dumps(self.body).encode("utf-8")

    def json(self):
        """The parsed body, for tests and the in-process client."""
        if isinstance(self.body, (dict, list)) or self.body is None:
            return self.body
        return json.loads(self.encoded().decode("utf-8"))


def _parse_cookies(header):
    cookies = {}
    for part in _COOKIE_SPLIT.split(header or ""):
        if not part or "=" not in part:
            continue
        name, _, value = part.partition("=")
        cookies[name.strip()] = unquote(value.strip().strip('"'))
    return cookies
