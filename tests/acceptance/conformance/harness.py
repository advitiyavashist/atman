"""Live-server-vs-fixture conformance harness (T-200).

For every case in `cases.py` this:

  1. issues the request against `base_url` with the credential headers the
     route's `security` requires (frozen decision: actor comes from the
     credential, never the body) -- this is (c);
  2. checks the response status matches the status `openapi.yaml` declares
     for that operation's success case -- this is (a) ("paired response
     fixture" here means "the fixture wired to this route in `cases.py`",
     since the manifest alone does not say which route a fixture belongs to);
  3. validates the response body against that operation's response schema --
     this is (b);
  4. for every mutation, also resends the same body with an injected
     top-level `actor` field and asserts it is refused 400
     `malformed_request` -- this is (c) again, the negative half.

This module only talks HTTP and reads `openapi.yaml` for the expected
status/schema; it does not know or care whether `base_url` is the fixture
stub (`stub_server.py`, for proving the harness itself) or a real server
(T-180, not built yet).
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from dataclasses import dataclass
from urllib.parse import urlencode

from .cases import PROJECT_ID, ALL_CASES, Case
from .openapi_index import Route
from .routes import ROUTES
from .validation import schema_errors

ACTOR_STUB = {"type": "human", "id": "mem_operator1", "display_name": "Operator"}


@dataclass
class Result:
    label: str
    method: str
    path: str
    kind: str  # "read" | "mutate" | "actor-reject"
    expected_status: int
    actual_status: int | None
    schema: str | None
    errors: list[str]
    passed: bool


def _credential_headers(route: Route) -> dict:
    schemes: set[str] = set()
    for alternative in route.security:
        schemes |= alternative
    headers = {}
    if "agentToken" in schemes:
        headers["Authorization"] = "Bearer stub-agent-token"
    if "operatorSession" in schemes:
        headers["Cookie"] = "tb_session=stub-session"
        headers["X-CSRF-Token"] = "stub-csrf"
        headers["Origin"] = "http://127.0.0.1:4319"
    return headers


def _send(method: str, url: str, headers: dict, body: object | None) -> tuple[int, object | None]:
    data = json.dumps(body).encode() if body is not None else None
    request = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(request, timeout=5) as response:
            raw = response.read().decode()
            return response.status, (json.loads(raw) if raw else None)
    except urllib.error.HTTPError as error:
        raw = error.read().decode()
        try:
            parsed = json.loads(raw) if raw else None
        except json.JSONDecodeError:
            parsed = None
        return error.code, parsed


def _url(base_url: str, case: Case, route: Route) -> str:
    path = route.path.format(**case.path_values)
    url = base_url.rstrip("/") + path
    if case.query_values:
        url += "?" + urlencode(case.query_values)
    return url


def _check(label: str, method: str, path: str, kind: str, expected_status: int,
           actual_status: int | None, schema: str | None, body: object | None,
           extra_ok: bool = True, extra_note: str = "") -> Result:
    errors: list[str] = []
    if actual_status != expected_status:
        errors.append(f"status {actual_status} != expected {expected_status}")
    elif schema is not None:
        errors.extend(schema_errors(schema, body))
    if not extra_ok:
        errors.append(extra_note)
    return Result(label, method, path, kind, expected_status, actual_status, schema,
                  errors, passed=not errors)


def run(base_url: str, cases: list[Case] = ALL_CASES) -> list[Result]:
    results: list[Result] = []
    for case in cases:
        route = ROUTES[case.operation_id]
        url = _url(base_url, case, route)
        headers = {"X-Project-Id": PROJECT_ID, "Accept": "application/json"}
        headers.update(_credential_headers(route))

        if case.request is None:
            status, body = _send(route.method, url, headers, None)
            results.append(_check(case.label, route.method, route.path, "read",
                                   route.success_status, status, route.success_schema, body))
            continue

        headers["Content-Type"] = "application/json"
        request_body = case.request.load()

        status, body = _send(route.method, url, headers, request_body)
        results.append(_check(case.label, route.method, route.path, "mutate",
                               route.success_status, status, route.success_schema, body))

        mutated = dict(request_body)
        mutated["actor"] = ACTOR_STUB
        reject_status, reject_body = _send(route.method, url, headers, mutated)
        code = (reject_body or {}).get("error", {}).get("code") if isinstance(reject_body, dict) else None
        results.append(_check(f"{case.label}[actor-reject]", route.method, route.path, "actor-reject",
                               400, reject_status, "ErrorResponse", reject_body,
                               extra_ok=(code == "malformed_request"),
                               extra_note=f"error.code {code!r} != 'malformed_request'"))
    return results
