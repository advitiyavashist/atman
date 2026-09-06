"""Read `docs/contracts/openapi.yaml` into a per-operation route index.

The conformance harness must never hardcode a status code or schema name that
the frozen spec already states -- that would be a second source of truth that
could silently drift from `openapi.yaml`. This module is the only place that
walks `paths:`; everything else in the harness looks operations up by
`operationId`.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import yaml

REPO = Path(__file__).resolve().parents[3]
SPEC_PATH = REPO / "docs" / "contracts" / "openapi.yaml"

_METHODS = {"get", "post", "put", "patch", "delete"}

# The global `security:` block at the document root -- either credential
# satisfies a route that does not override it.
_DEFAULT_SECURITY = [{"operatorSession": []}, {"agentToken": []}]


@dataclass(frozen=True)
class Route:
    operation_id: str
    method: str
    path: str  # e.g. "/tickets/{ticket_id}/claim"
    path_params: tuple[str, ...]
    query_params: tuple[str, ...]  # names of query params, required or not
    required_query_params: tuple[str, ...]
    security: tuple[frozenset[str], ...]  # each element is one acceptable credential set
    request_schema: str | None
    success_status: int
    success_schema: str | None  # None if the success response has no body


def load_spec() -> dict:
    with SPEC_PATH.open() as fh:
        return yaml.safe_load(fh)


def _security_to_tuple(security: list[dict]) -> tuple[frozenset[str], ...]:
    return tuple(frozenset(entry.keys()) for entry in security)


def _schema_ref(node: dict | None) -> str | None:
    if not node:
        return None
    ref = node.get("$ref")
    if not ref:
        return None
    return ref.rsplit("/", 1)[-1]


def build_routes(spec: dict) -> dict[str, Route]:
    routes: dict[str, Route] = {}
    for path, item in spec["paths"].items():
        # Resolve $ref parameters (only local component refs appear here).
        resolved_shared = [_resolve_param(spec, p) for p in item.get("parameters", [])]
        for method, operation in item.items():
            if method not in _METHODS:
                continue
            op_id = operation["operationId"]
            params = resolved_shared + [_resolve_param(spec, p) for p in operation.get("parameters", [])]
            path_params = tuple(p["name"] for p in params if p["in"] == "path")
            query_params = tuple(p["name"] for p in params if p["in"] == "query")
            required_query = tuple(p["name"] for p in params if p["in"] == "query" and p.get("required"))

            security = operation.get("security", _DEFAULT_SECURITY)

            request_body = operation.get("requestBody")
            request_schema = None
            if request_body:
                content = request_body.get("content", {}).get("application/json", {})
                request_schema = _schema_ref(content.get("schema"))

            success_status, success_schema = _pick_success_response(operation["responses"])

            routes[op_id] = Route(
                operation_id=op_id,
                method=method.upper(),
                path=path,
                path_params=path_params,
                query_params=query_params,
                required_query_params=required_query,
                security=_security_to_tuple(security),
                request_schema=request_schema,
                success_status=success_status,
                success_schema=success_schema,
            )
    return routes


def _resolve_param(spec: dict, param: dict) -> dict:
    ref = param.get("$ref")
    if not ref:
        return param
    target = spec
    for part in ref.lstrip("#/").split("/"):
        target = target[part]
    return target


def _pick_success_response(responses: dict) -> tuple[int, str | None]:
    for status in sorted(responses):
        if not status.isdigit() or not status.startswith("2"):
            continue
        body = responses[status]
        content = (body or {}).get("content", {})
        json_content = content.get("application/json") or content.get("text/event-stream")
        schema = _schema_ref((json_content or {}).get("schema"))
        return int(status), schema
    raise ValueError("no 2xx response declared")
