"""OpenAPI helpers for the render-live UI mock (T-463).

Invoked from TypeScript tests; keeps request validation aligned with
docs/contracts/openapi.yaml without adding ajv to the ui package.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import yaml
from jsonschema import Draft202012Validator
from referencing import Registry, Resource
from referencing.jsonschema import DRAFT202012

SPEC_URI = "urn:ticket-board:openapi:ui-mock"


def created_route_suffix(path_template: str) -> str:
    segments = [s for s in path_template.split("/") if s]
    tail: list[str] = []
    for i in range(len(segments) - 1, -1, -1):
        if segments[i].startswith("{"):
            break
        tail.insert(0, segments[i])
    return "/" + "/".join(tail)


def load_spec(spec_path: Path) -> dict:
    return yaml.safe_load(spec_path.read_text())


def post201_request_schemas(spec: dict) -> dict[str, str]:
    mapping: dict[str, str] = {}
    for path, item in spec["paths"].items():
        post = item.get("post")
        if not post or "201" not in post.get("responses", {}):
            continue
        rb = post.get("requestBody") or {}
        content = (rb.get("content") or {}).get("application/json") or {}
        ref = (content.get("schema") or {}).get("$ref", "")
        if not ref:
            continue
        schema_name = ref.rsplit("/", 1)[-1]
        mapping[created_route_suffix(path)] = schema_name
    return mapping


def schema_errors(spec: dict, schema_name: str, payload: object) -> list[str]:
    registry = Registry().with_resource(
        SPEC_URI, Resource.from_contents(spec, default_specification=DRAFT202012)
    )
    validator = Draft202012Validator(
        {"$ref": f"{SPEC_URI}#/components/schemas/{schema_name}"},
        registry=registry,
    )
    errors = sorted(validator.iter_errors(payload), key=lambda e: list(e.path))
    return [f"{'/'.join(str(p) for p in e.path) or '<root>'}: {e.message}" for e in errors]


def main() -> None:
    if len(sys.argv) < 3:
        raise SystemExit("usage: openapi_request_validate.py <spec> schemas|validate <schema>")
    spec_path = Path(sys.argv[1])
    spec = load_spec(spec_path)
    command = sys.argv[2]
    if command == "schemas":
        print(json.dumps(post201_request_schemas(spec)))
        return
    if command == "validate":
        schema_name = sys.argv[3]
        payload = json.load(sys.stdin)
        print(json.dumps(schema_errors(spec, schema_name, payload)))
        return
    raise SystemExit(f"unknown command: {command}")


if __name__ == "__main__":
    main()
