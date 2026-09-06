"""Request-body validation, written by hand rather than driven by the schema.

The contract's request schemas are `additionalProperties: false`, so an unknown
field is a 400 and not something to ignore. Validating against `openapi.yaml`
at runtime would be the obvious way to get that, but it would make `jsonschema`
and `pyyaml` hard runtime dependencies of the server for a board whose whole
premise is that it runs locally with no install ceremony -- they are an
optional `[contracts]` extra today, and the CLI works without them.

So the shapes are declared here and the *tests* validate real responses and
real request bodies against the frozen spec (`tests/server/test_contract_
conformance.py`). If this file and the spec disagree, that test fails; the
duplication is checked rather than trusted.
"""

from __future__ import annotations

from ..storage import ids
from .errors import MalformedRequest

# Frozen decision 1. Every mutation body is checked for this before anything
# else, and the message is the one the published fixture carries.
ACTOR_MESSAGE = ("Request body contained an 'actor' field. "
                 "Actor is bound from the credential.")


def check_body(body, *, required=(), allowed=()):
    """Reject bodies the contract would reject, in the contract's own shape."""
    identifying = sorted(
        f for f in ("actor", "author", "submitted_by", "decided_by", "sender")
        if f in body
    )
    if identifying:
        raise MalformedRequest(ACTOR_MESSAGE, {"rejected_fields": identifying})

    permitted = set(allowed) | set(required)
    unexpected = sorted(f for f in body if f not in permitted)
    if unexpected:
        raise MalformedRequest(
            "Request body contained unexpected fields.",
            {"rejected_fields": unexpected},
        )
    missing = sorted(f for f in required if f not in body)
    if missing:
        raise MalformedRequest(
            "Request body is missing required fields.",
            {"missing_fields": missing},
        )
    return body


def request_id(body):
    value = body.get("request_id")
    if not isinstance(value, str) or not ids.REQUEST_ID_RE.match(value):
        raise MalformedRequest("request_id must be a UUID.",
                               {"rejected_fields": ["request_id"]})
    return value


def integer(body, key, *, minimum=None, maximum=None, default=None):
    if key not in body or body[key] is None:
        if default is None:
            raise MalformedRequest("{} is required.".format(key),
                                   {"missing_fields": [key]})
        return default
    value = body[key]
    # bool is an int subclass in Python; an `expected_version: true` that
    # silently becomes 1 would compare equal to a real version.
    if not isinstance(value, int) or isinstance(value, bool):
        raise MalformedRequest("{} must be an integer.".format(key),
                               {"rejected_fields": [key]})
    if minimum is not None and value < minimum:
        raise MalformedRequest("{} must be at least {}.".format(key, minimum),
                               {"rejected_fields": [key]})
    if maximum is not None and value > maximum:
        raise MalformedRequest("{} must be at most {}.".format(key, maximum),
                               {"rejected_fields": [key]})
    return value


def text(body, key, *, max_length, required=True, default=None, min_length=1):
    if key not in body or body[key] is None:
        if required:
            raise MalformedRequest("{} is required.".format(key),
                                   {"missing_fields": [key]})
        return default
    value = body[key]
    if not isinstance(value, str):
        raise MalformedRequest("{} must be a string.".format(key),
                               {"rejected_fields": [key]})
    if len(value) < min_length or len(value) > max_length:
        raise MalformedRequest(
            "{} must be between {} and {} characters.".format(
                key, min_length, max_length),
            {"rejected_fields": [key]},
        )
    return value


def boolean(body, key, *, default=None):
    if key not in body or body[key] is None:
        if default is None:
            raise MalformedRequest("{} is required.".format(key),
                                   {"missing_fields": [key]})
        return default
    if not isinstance(body[key], bool):
        raise MalformedRequest("{} must be a boolean.".format(key),
                               {"rejected_fields": [key]})
    return body[key]


def enum(body, key, choices, *, required=True, default=None):
    if key not in body or body[key] is None:
        if required:
            raise MalformedRequest("{} is required.".format(key),
                                   {"missing_fields": [key]})
        return default
    if body[key] not in choices:
        raise MalformedRequest(
            "{} must be one of {}.".format(key, ", ".join(sorted(choices))),
            {"rejected_fields": [key]},
        )
    return body[key]


def string_list(body, key, *, max_length=300, default=None):
    if key not in body or body[key] is None:
        return list(default or [])
    value = body[key]
    if not isinstance(value, list) or any(
            not isinstance(v, str) or len(v) > max_length for v in value):
        raise MalformedRequest("{} must be a list of strings.".format(key),
                               {"rejected_fields": [key]})
    return value


def acceptance(body):
    """`[{text: ...}]`, at least one item -- the contract's minItems: 1."""
    items = body.get("acceptance")
    if not isinstance(items, list) or not items:
        raise MalformedRequest("acceptance must have at least one item.",
                               {"rejected_fields": ["acceptance"]})
    out = []
    for item in items:
        if not isinstance(item, dict) or set(item) - {"text"} or not item.get("text"):
            raise MalformedRequest("acceptance items carry a text field only.",
                                   {"rejected_fields": ["acceptance"]})
        if not isinstance(item["text"], str) or len(item["text"]) > 500:
            raise MalformedRequest("acceptance text must be a string of <= 500.",
                                   {"rejected_fields": ["acceptance"]})
        # Stored in the checkable shape TicketAcceptanceItem requires, so a
        # freshly created ticket validates against the same schema as one that
        # has been worked on.
        out.append({"text": item["text"], "checked": False,
                    "checked_by": None, "checked_at": None})
    return out


_CHECK_STATUSES = ("passed", "failed", "pending", "skipped")


def git_evidence(body, key="evidence"):
    """A `GitEvidence`, with the SHA checked here rather than at accept time.

    A malformed SHA that reaches the store becomes evidence nobody can compare
    against later, which is exactly the failure decision 3 of the freeze
    exists to prevent.
    """
    value = body.get(key)
    if not isinstance(value, dict):
        raise MalformedRequest("evidence must be an object.",
                               {"rejected_fields": [key]})
    unexpected = sorted(
        set(value) - {"repository", "branch", "sha", "pr_url", "checks"})
    if unexpected:
        raise MalformedRequest("evidence contained unexpected fields.",
                               {"rejected_fields": unexpected})
    sha = value.get("sha")
    if not isinstance(sha, str) or len(sha) != 40 or \
            any(c not in "0123456789abcdef" for c in sha):
        raise MalformedRequest("evidence.sha must be a 40-character hex sha.",
                               {"rejected_fields": ["evidence.sha"]})
    branch = value.get("branch")
    if not isinstance(branch, str) or not branch or len(branch) > 200:
        raise MalformedRequest("evidence.branch is required.",
                               {"rejected_fields": ["evidence.branch"]})
    checks = value.get("checks")
    if checks is not None:
        if not isinstance(checks, list):
            raise MalformedRequest("evidence.checks must be a list.",
                                   {"rejected_fields": ["evidence.checks"]})
        for check in checks:
            if not isinstance(check, dict) or \
                    check.get("status") not in _CHECK_STATUSES or \
                    not isinstance(check.get("name"), str):
                raise MalformedRequest(
                    "evidence.checks items need a name and a known status.",
                    {"rejected_fields": ["evidence.checks"]},
                )
    return value


def sha(body, key):
    value = body.get(key)
    if not isinstance(value, str) or len(value) != 40 or \
            any(c not in "0123456789abcdef" for c in value):
        raise MalformedRequest("{} must be a 40-character hex sha.".format(key),
                               {"rejected_fields": [key]})
    return value


def ticket_id(value):
    if not isinstance(value, str) or not ids.TICKET_ID_RE.match(value):
        raise MalformedRequest("ticket id must look like DEMO-13.",
                               {"rejected_fields": ["ticket_id"]})
    return value
