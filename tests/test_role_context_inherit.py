"""A seeded role's context must survive the first takeover.

`take` used to require a fresh context on every handover, so the first agent to
claim a role that a coordinator had described DESTROYED that description -- the
opposite of what the requirement exists for. A reason is still mandatory, so a
takeover is still justified; only re-typing the context is optional now.
"""

import importlib.util
import pathlib

import pytest

_PATH = pathlib.Path(__file__).resolve().parents[1] / "ticket_coordination.py"
_SPEC = importlib.util.spec_from_file_location("ticket_coordination", _PATH)
tc = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(tc)


def _state():
    return {"schema": 1, "agents": {}, "roles": {}, "handovers": [], "history": []}


def test_takeover_inherits_the_existing_context():
    state = _state()
    tc.take(state, "backend", "coordinator", "none", "seeding", "what this role means")
    record = tc.take(state, "backend", "worker", "coordinator", "claiming", "")
    assert record["context"] == "what this role means"
    assert record["holder"] == "worker"


def test_takeover_may_still_replace_the_context():
    state = _state()
    tc.take(state, "backend", "coordinator", "none", "seeding", "original")
    record = tc.take(state, "backend", "worker", "coordinator", "claiming", "revised")
    assert record["context"] == "revised"


def test_a_role_with_no_context_still_requires_one():
    state = _state()
    with pytest.raises(ValueError):
        tc.take(state, "fresh", "worker", "none", "a reason but no context", "")


def test_reason_is_still_mandatory_even_when_context_is_inherited():
    state = _state()
    tc.take(state, "backend", "coordinator", "none", "seeding", "context exists")
    with pytest.raises(ValueError):
        tc.take(state, "backend", "worker", "coordinator", "", "")


def test_the_compare_and_swap_guard_is_unchanged():
    state = _state()
    tc.take(state, "backend", "coordinator", "none", "seeding", "context exists")
    with pytest.raises(ValueError, match="Role holder changed"):
        tc.take(state, "backend", "worker", "somebody-else", "claiming", "")
