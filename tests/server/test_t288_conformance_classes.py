"""T-288 finding 5, the durable one: the conformance suite was SCHEMA-ONLY.

`test_api_conformance.py` validates the SHAPE of what a route returns against
`openapi.yaml`. It never asserts that a route EXISTS, that a field the contract
marks optional is actually accepted, or that a requirement the contract states
is actually enforced. All four T-288 gaps sailed through a green conformance
bar for exactly that reason -- each was caught by a human reading two documents
side by side, which does not scale past one amendment.

These checks are derived FROM the contract rather than hand-listed, so the next
amendment is checked by machine. Adding a route to openapi.yaml with no server
route, marking a field optional while the server still requires it, or stating
a requirement the server never enforces, all fail here.
"""
import re

import pytest

from api_client import rid
from api_spec import SPEC

import sys
from pathlib import Path
SRC = Path(__file__).resolve().parents[2] / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))
from ticket_board.server.app import _ROUTE_TABLE  # noqa: E402

_HTTP_METHODS = ("get", "post", "put", "patch", "delete")

# Operations the contract specifies that this lane deliberately has not built
# yet, each tagged with the ticket that owns it. This is an ALLOWLIST, not a
# mute: an entry that becomes routed fails the staleness check below, so the
# list shrinks as those lanes land instead of silently outliving them.
PENDING_ROUTES = {
    ("POST", "/channels/{channel_id}/members"): "T-187 messaging API",
    ("GET", "/messages/{message_id}/deliveries"): "T-187 messaging API",
    ("POST", "/messages/{message_id}/task"): "T-187 messaging API",
    ("POST", "/runs/{run_id}/cancel"): "T-188 managed runner",
    ("POST", "/runs/{run_id}/events"): "T-188 managed runner",
}


def _contract_operations():
    for path, item in sorted(SPEC["paths"].items()):
        for method in item:
            if method.lower() in _HTTP_METHODS:
                yield method.upper(), path


def _is_routed(method, path):
    """Does some entry in the server's route table answer this contract path?"""
    sample = re.sub(r"\{[^}]+\}", "X", path)
    return any(m == method and re.match(rx, sample)
               for m, rx, _name, _auth, _proj in _ROUTE_TABLE)


# --------------------------------------------------------- class 1: routes

def test_every_contract_operation_has_a_server_route():
    """A route specified in the contract and absent from the server is a 404 to
    every conforming client, and the schema-only suite cannot see it -- there is
    no response to validate. This is how POST /tickets/{id}/reopen was fully
    specified, implemented in the CLI only, and shipped as a 404 to the API
    that E-010 exists to provide (T-288 gap 2)."""
    missing = [(m, p) for m, p in _contract_operations()
               if not _is_routed(m, p) and (m, p) not in PENDING_ROUTES]
    assert not missing, (
        "contract operations with no server route: %s -- implement them, or add "
        "them to PENDING_ROUTES with the ticket that will" % missing)


def test_the_pending_route_allowlist_has_no_stale_entries():
    """An allowlist that outlives its reason silently stops testing the thing it
    named. Anything in PENDING_ROUTES that IS now routed must come off the list."""
    stale = [(m, p, owner) for (m, p), owner in sorted(PENDING_ROUTES.items())
             if _is_routed(m, p)]
    assert not stale, "PENDING_ROUTES entries that are now implemented: %s" % stale


def test_the_pending_route_allowlist_only_names_real_contract_operations():
    """A typo'd entry would mute nothing and hide a genuinely missing route."""
    declared = set(_contract_operations())
    bogus = [k for k in sorted(PENDING_ROUTES) if k not in declared]
    assert not bogus, "PENDING_ROUTES names operations the contract does not: %s" % bogus


# ----------------------------------------- class 2: optional means optional

def _schema_of(name):
    return SPEC["components"]["schemas"][name]


def _envelope_parts(name):
    """Flatten an allOf-with-MutationEnvelope request schema to (required, props)."""
    schema = _schema_of(name)
    required, props = set(), {}
    for part in schema.get("allOf", [schema]):
        if "$ref" in part:
            continue
        required |= set(part.get("required", ()))
        props.update(part.get("properties", {}))
    return required, props


def test_optional_create_ticket_fields_are_actually_accepted_when_omitted(operator):
    """Dropping each field the contract does NOT list as required must still be
    a valid create. The server hard-required `outcome` and `acceptance` while
    the contract documented both as optional with defaults, so every conforming
    client that omitted them got a 400 (T-288 gap 1)."""
    required, props = _envelope_parts("CreateTicketRequest")
    full = {"request_id": None, "title": "conformance probe", "outcome": "o",
            "acceptance": [{"text": "a"}], "role": "backend",
            "dependencies": [], "files": ["src/"]}
    assert set(full) >= set(props), (
        "probe body is missing contract fields %s -- extend it" % (set(props) - set(full)))

    for optional in sorted(set(props) - required):
        body = {k: v for k, v in full.items() if k != optional}
        body["request_id"] = rid()
        response = operator.post("/tickets", body)
        assert response.status == 201, (
            "omitting optional field %r was rejected: %s %s"
            % (optional, response.status, response.json()))


# ------------------------------ class 3: a stated requirement is enforced

def test_documented_required_create_ticket_fields_are_enforced(operator):
    """The mirror of class 2: a field the contract lists as required must be
    refused when absent, or the `required` list is decoration."""
    required, props = _envelope_parts("CreateTicketRequest")
    full = {"request_id": None, "title": "conformance probe", "outcome": "o",
            "acceptance": [{"text": "a"}]}
    for name in sorted(required):
        body = {k: v for k, v in full.items() if k != name}
        body.setdefault("request_id", rid())
        if name == "request_id":
            body.pop("request_id", None)
        else:
            body["request_id"] = rid()
        response = operator.post("/tickets", body)
        assert response.status == 400, (
            "omitting REQUIRED field %r was accepted: %s %s"
            % (name, response.status, response.json()))


def test_documented_required_evidence_fields_are_enforced(operator, enrolled, ticket):
    """GitEvidence.required is [repository, branch, sha]. `repository` was
    listed, described in prose as required, and never read by the validator, so
    the server returned 201 on evidence with no repository identity at all --
    which is exactly how an unrelated merge in a different repo appears to
    "contain" this evidence and closes the wrong ticket (T-288 gap 3, the same
    defect class as T-272 one layer up)."""
    required = set(_schema_of("GitEvidence")["required"])
    assert required == {"repository", "branch", "sha"}, required
    full = {"repository": "acme/repo", "branch": "agent/work", "sha": "a" * 40}

    claimed = enrolled["client"].post("/tickets/%s/claim" % ticket["id"], {
        "request_id": rid(), "expected_version": ticket["version"],
        "session_id": enrolled["session_id"]})
    assert claimed.status == 200, claimed.json()
    version = claimed.json()["version"]

    for name in sorted(required):
        evidence = {k: v for k, v in full.items() if k != name}
        response = enrolled["client"].post("/tickets/%s/reviews" % ticket["id"], {
            "request_id": rid(), "expected_version": version, "evidence": evidence})
        assert response.status == 400, (
            "evidence omitting REQUIRED field %r was accepted: %s %s"
            % (name, response.status, response.json()))
        assert "evidence." + name in str(response.json()), (
            "the 400 for %r should name the field: %s" % (name, response.json()))


# ------------------- class 4: a contract pattern copied into code must match

# Every place the server hardcodes a regex that mirrors a contract schema's
# `pattern`. A copy is not a reference: when T-224 widened AgentId, the copy in
# app.py stayed narrow and revoke_session_lease -- a security-relevant verb --
# began failing CLOSED on ~35 legitimate live ids (T-288 gap 4). Add a row here
# whenever code copies a contract pattern, so the copy cannot drift silently.
MIRRORED_PATTERNS = [
    ("AgentId", "AGENT_ID_RE"),
]


def test_contract_patterns_copied_into_code_still_match_the_contract():
    from ticket_board.server import app as app_module

    for schema_name, attr in MIRRORED_PATTERNS:
        contract = _schema_of(schema_name)["pattern"]
        compiled = getattr(app_module, attr)
        assert compiled.pattern == contract, (
            "%s hardcodes %r but the contract's %s is %r -- a widened or narrowed "
            "contract pattern must be copied through, or the code silently "
            "accepts/refuses ids the contract disagrees about"
            % (attr, compiled.pattern, schema_name, contract))


def test_the_mirrored_pattern_list_names_real_schemas_and_real_symbols():
    """A typo in either column would make the check above vacuously pass."""
    from ticket_board.server import app as app_module

    for schema_name, attr in MIRRORED_PATTERNS:
        assert "pattern" in _schema_of(schema_name), (
            "%s has no pattern in the contract" % schema_name)
        assert hasattr(app_module, attr), "app.py has no %s" % attr
