"""Validate a JSON body against a named component schema in openapi.yaml."""

from __future__ import annotations

from jsonschema import Draft202012Validator
from referencing import Registry, Resource
from referencing.jsonschema import DRAFT202012

from .openapi_index import load_spec

SPEC_URI = "urn:ticket-board:openapi:conformance"

SPEC = load_spec()
_REGISTRY = Registry().with_resource(
    SPEC_URI, Resource.from_contents(SPEC, default_specification=DRAFT202012)
)


def validator_for(schema_name: str) -> Draft202012Validator:
    return Draft202012Validator(
        {"$ref": f"{SPEC_URI}#/components/schemas/{schema_name}"}, registry=_REGISTRY
    )


def schema_errors(schema_name: str, payload: object) -> list[str]:
    errors = sorted(validator_for(schema_name).iter_errors(payload), key=lambda e: list(e.path))
    return [f"{'/'.join(str(p) for p in e.path) or '<root>'}: {e.message}" for e in errors]
