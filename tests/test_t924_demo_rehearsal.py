"""Reject demo receipts that contain convincing text but lack real transitions."""
import copy
import importlib.util
from pathlib import Path
import sys

import pytest

KIT = Path(__file__).resolve().parents[1] / "docs/demo"
sys.path.insert(0, str(KIT))
spec = importlib.util.spec_from_file_location("demo_verify", KIT / "verify.py")
verify = importlib.util.module_from_spec(spec)
spec.loader.exec_module(verify)

SHA = "a" * 40
HANDOFF = "Accepted " + SHA + "; summarize(path)"


def fixture_events():
    parent = {"status": "open", "owner": None}
    child = {"status": "open", "owner": None, "deps": ["T-001"]}
    events = []
    def add(cmd, seat, out=""):
        t = len(events) * 2
        events.append({"kind": "cli", "argv": [cmd], "seat": seat, "exit": 0,
                       "start": t, "end": t + 1, "stdout": out,
                       "tickets": copy.deepcopy({"T-001": parent, "T-002": child})})
    add("plan", "ceo")
    parent.update(status="claimed", owner="codex-worker")
    add("next", "codex-worker")
    parent.update(status="review", review_head=SHA)
    add("review", "codex-worker")
    parent["review_events"] = [{"kind": "accept", "sha": SHA, "by": "ceo"}]
    add("accept", "ceo")
    parent.update(status="done", notes=[{"text": HANDOFF}])
    add("done", "ceo")
    child.update(status="claimed", owner="cursor-worker")
    add("next", "cursor-worker", "Handoff from dependencies:\n" + HANDOFF)
    return events


def test_valid_state_transition_receipt():
    assert verify.verify_events(fixture_events()) == SHA


def test_echoed_success_is_not_execution():
    with pytest.raises(AssertionError, match="missing successful"):
        verify.verify_events([{"kind": "caption", "stdout": "accepted pinned IN REVIEW Handoff from dependencies"}])


@pytest.mark.parametrize("damage", ["wrong_sha", "wrong_owner", "early_claim", "missing_handoff", "nonzero_exit"])
def test_forged_or_incomplete_receipts_fail(damage):
    events = fixture_events()
    if damage == "wrong_sha":
        events[3]["tickets"]["T-001"]["review_events"][0]["sha"] = "b" * 40
    elif damage == "wrong_owner":
        events[-1]["tickets"]["T-002"]["owner"] = "codex-worker"
    elif damage == "early_claim":
        events[-1]["start"] = 0
    elif damage == "missing_handoff":
        events[-1]["stdout"] = "Handoff from dependencies: a convincing but unrelated note"
    else:
        events[3]["exit"] = 1
    with pytest.raises(AssertionError):
        verify.verify_events(events)
