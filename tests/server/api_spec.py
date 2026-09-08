"""Validate live responses against the frozen contract.

`tests/test_contracts.py` proves the published *fixtures* match
`openapi.yaml`. That says nothing about what the server actually sends, which is
the thing the console will consume. This module points the same validator at
real responses, so a route that drifts from the spec fails here rather than in
T-184's browser.
"""

import json
import os
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
SPEC_PATH = REPO / "docs" / "contracts" / "openapi.yaml"

_REQUIRED = os.environ.get("TICKET_BOARD_CONTRACTS_REQUIRED") == "1"
_HINT = "install the contract toolchain: pip install -e '.[contracts]'"

try:
    import yaml
    from jsonschema import Draft202012Validator
    from referencing import Registry, Resource
    from referencing.jsonschema import DRAFT202012
except ImportError as exc:  # pragma: no cover - environment-dependent
    if _REQUIRED:
        raise RuntimeError(
            "TICKET_BOARD_CONTRACTS_REQUIRED=1 but the contract toolchain is "
            "missing ({}). {}".format(exc, _HINT)
        )
    pytest.skip(_HINT, allow_module_level=True)

SPEC_URI = "urn:ticket-board:openapi"
with SPEC_PATH.open() as _fh:
    SPEC = yaml.safe_load(_fh)
REGISTRY = Registry().with_resource(
    SPEC_URI, Resource.from_contents(SPEC, default_specification=DRAFT202012)
)
_CACHE = {}


def validator(schema_name):
    if schema_name not in _CACHE:
        _CACHE[schema_name] = Draft202012Validator(
            {"$ref": "{}#/components/schemas/{}".format(SPEC_URI, schema_name)},
            registry=REGISTRY,
        )
    return _CACHE[schema_name]


def check(schema_name, payload, *, label=""):
    """Assert `payload` validates, reporting every error rather than the first."""
    errors = sorted(validator(schema_name).iter_errors(payload),
                    key=lambda e: list(e.absolute_path))
    if errors:
        detail = "\n".join(
            "  {}: {}".format("/".join(str(p) for p in e.absolute_path) or "<root>",
                              e.message)
            for e in errors
        )
        raise AssertionError(
            "{} does not validate as {}{}\n{}\n{}".format(
                label or "payload", schema_name,
                " ({})".format(label) if label else "", detail,
                json.dumps(payload, indent=2)[:2000],
            )
        )
    return payload


def is_valid(schema_name, payload):
    return validator(schema_name).is_valid(payload)
