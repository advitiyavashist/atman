"""T-014: 'stuck' needs N rounds of evidence, then escalates to planner + CEO.

Why: T-976 sat in review six weeks after its seat posted 'stuck' four times
in prose and nobody acted. Done when the N-round gate and the escalation are
tested, and a stuck ticket cannot sit unacknowledged >24h without surfacing.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import tickets as tk  # noqa: E402
from src.ticket_board import stuck_gate as sg  # noqa: E402

EV1 = "pytest tests/test_x.py fails: ModuleNotFoundError setuptools in the uv venv"
EV2 = "installed setuptools into the venv; collection now aborts on a missing fixture"
T0 = datetime(2026, 11, 4, 12, 0, tzinfo=timezone.utc)
NO_GAP = {"TICKETS_STUCK_ROUND_SECS": "0"}


def stamp(mins_ago=0):
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(time.time() - mins_ago * 60))


def write(path, obj):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj))


def _board(tmp_path):
    board = tmp_path / "proj" / ".tickets"
    board.mkdir(parents=True)
    for name in ("worker", "planner", "ceo-seat", "other"):
        write(board / "agents" / (name + ".json"), {"owner": name, "seen": stamp(1)})
    write(board / "master.json", {"owner": "planner"})
    write(board / "aliases.json", {"ceo": "ceo-seat"})
    write(board / "T-001.json", dict(id="T-001", title="Ship the thing", status="claimed",
                                     owner="worker", role="backend", deps=[], notes=[],
                                     priority=2, claimed_at=stamp(30)))
    return board


TOOLS = {"tickets.py": ROOT / "tickets.py", "cli.py": ROOT / "src" / "ticket_board" / "cli.py"}
TOOL = {"path": TOOLS["tickets.py"]}


@pytest.fixture(params=sorted(TOOLS), autouse=False)
def tool(request):
    """Run the CLI-level tests against BOTH copies: root tickets.py and the
    packaged ticket_board.cli (the pip entry point)."""
    TOOL["path"] = TOOLS[request.param]
    yield request.param
    TOOL["path"] = TOOLS["tickets.py"]


def _cli(board, agent, *args, env_extra=None):
    env = os.environ.copy()
    env["PYTHONPATH"] = str(ROOT / "src") + os.pathsep + env.get("PYTHONPATH", "")
    env["TICKETS_DIR"] = str(board)
    env["TICKET_AGENT"] = agent
    for k in ("TICKET_SEAT", "TICKETS_WATCH_PINNED", "TICKET_SESSION_ID",
              "TICKETS_STUCK_ROUNDS", "TICKETS_STUCK_ROUND_SECS", "TICKETS_STUCK_ACK_HOURS"):
        env.pop(k, None)
    env.update(env_extra or {})
    return subprocess.run([sys.executable, str(TOOL["path"]), *args],
                          cwd=str(ROOT), env=env, capture_output=True, text=True, check=False)


def _stuck(board, agent, obstacle, evidence, env_extra=NO_GAP):
    return _cli(board, agent, "stuck", "T-001", "--obstacle", obstacle,
                "--evidence", evidence, env_extra=env_extra)


def _msgs(board):
    p = board / "messages.jsonl"
    return [json.loads(x) for x in p.read_text().splitlines() if x.strip()] if p.exists() else []


# ---- the N-round gate (pure) -------------------------------------------------

def test_one_round_records_but_does_not_escalate():
    rec, v = sg.record_round({}, "worker", "venv", EV1, T0, environ=NO_GAP)
    assert v == {"round": 1, "needed": 2, "escalate": False, "reset": "", "followup": False}
    assert rec["state"] == "reporting"


def test_second_consecutive_round_on_same_obstacle_escalates():
    rec, _ = sg.record_round({}, "worker", "venv setup", EV1, T0, environ=NO_GAP)
    rec, v = sg.record_round(rec, "worker", "  Venv   SETUP ", EV2, T0 + timedelta(minutes=20),
                             environ=NO_GAP)
    assert v["escalate"] and v["round"] == 2
    assert rec["state"] == "escalated" and rec["escalated_at"] == "2026-11-04T12:20:00Z"


def test_n_is_configurable():
    env = dict(NO_GAP, TICKETS_STUCK_ROUNDS="3")
    rec, _ = sg.record_round({}, "w", "venv", EV1, T0, environ=env)
    rec, v = sg.record_round(rec, "w", "venv", EV2, T0, environ=env)
    assert not v["escalate"] and v["needed"] == 3
    rec, v = sg.record_round(rec, "w", "venv", EV1 + " again after reinstall", T0, environ=env)
    assert v["escalate"]


def test_changed_obstacle_restarts_the_count():
    rec, _ = sg.record_round({}, "w", "venv", EV1, T0, environ=NO_GAP)
    rec, v = sg.record_round(rec, "w", "gh auth", EV2, T0, environ=NO_GAP)
    assert not v["escalate"] and v["round"] == 1 and "restarts" in v["reset"]
    assert rec["history"][-1]["obstacle"] == "venv"


@pytest.mark.parametrize("obstacle,evidence,needle", [
    ("", EV1, "--obstacle"),
    ("venv", "it broke", "--evidence"),
])
def test_report_without_obstacle_or_real_evidence_is_refused(obstacle, evidence, needle):
    with pytest.raises(sg.Refused, match=needle):
        sg.record_round({}, "w", obstacle, evidence, T0, environ=NO_GAP)


def test_repeated_evidence_is_not_a_new_round():
    rec, _ = sg.record_round({}, "w", "venv", EV1, T0, environ=NO_GAP)
    with pytest.raises(sg.Refused, match="new evidence"):
        sg.record_round(rec, "w", "venv", EV1.upper() + "!", T0, environ=NO_GAP)


def test_rounds_closer_than_the_gap_do_not_count():
    rec, _ = sg.record_round({}, "w", "venv", EV1, T0)
    with pytest.raises(sg.Refused, match="at least 600s apart"):
        sg.record_round(rec, "w", "venv", EV2, T0 + timedelta(seconds=30))
    _, v = sg.record_round(rec, "w", "venv", EV2, T0 + timedelta(minutes=11))
    assert v["escalate"]


def test_ack_must_come_from_a_different_seat_and_overdue_flips():
    rec, _ = sg.record_round({}, "w", "venv", EV1, T0, environ=NO_GAP)
    rec, _ = sg.record_round(rec, "w", "venv", EV2, T0, environ=NO_GAP)
    t = {"id": "T-1", "status": "blocked", "stuck": rec}
    assert not sg.escalation_view(t, T0 + timedelta(hours=23))["overdue"]
    assert sg.escalation_view(t, T0 + timedelta(hours=25))["overdue"]
    with pytest.raises(sg.Refused, match="different seat"):
        sg.acknowledge(rec, "w", "I'll look", T0)
    acked = sg.acknowledge(rec, "planner", "pairing with you at 3pm", T0)
    v = sg.escalation_view(dict(t, stuck=acked), T0 + timedelta(hours=72))
    assert v["state"] == "acknowledged" and not v["overdue"]
    gone = sg.resolve(acked, "planner", "venv rebuilt with setuptools", T0)
    assert sg.escalation_view(dict(t, stuck=gone), T0) is None


# ---- the CLI: escalation, visibility, resolution -----------------------------

def test_cli_two_rounds_escalate_to_planner_and_ceo(tmp_path, tool):
    board = _board(tmp_path)
    r1 = _stuck(board, "worker", "venv", EV1)
    assert r1.returncode == 0, r1.stderr
    assert "round 1 of 2" in r1.stdout
    t = tk.load(str(board), "T-001")
    assert t["status"] == "claimed" and _msgs(board) == []

    r2 = _stuck(board, "worker", "venv", EV2)
    assert r2.returncode == 0, r2.stderr
    assert "ESCALATED" in r2.stdout
    t = tk.load(str(board), "T-001")
    assert t["status"] == "blocked" and t["owner"] == "worker"
    assert t["stuck"]["escalated_to"] == ["planner", "ceo-seat"]
    sent = _msgs(board)
    assert sorted(m["to"] for m in sent) == ["ceo-seat", "planner"]
    for m in sent:
        assert m["text"].startswith("stuck: T-001 escalated after 2 rounds")
        assert m["re"] == "T-001" and m.get("kind") == "task"
        assert EV2[:40] in m["text"]

    # visible in health (WARN before the SLA) and in the operator view
    issues = tk.health(str(board), tk.load_all(str(board)))
    assert any(s == "WARN" and "T-001 stuck on 'venv'" in msg for s, msg, _ in issues)
    ny = tk.ui_needs_you(str(board), "operator", "proj")
    item = next(i for i in ny["items"] if i["id"] == "T-001")
    assert item["kind"] == "escalated" and "awaiting acknowledgement" in item["why"]
    assert item["label"] == "asked (unstructured)" and item["state"] == "asked"


def test_cli_unacknowledged_over_24h_is_crit_and_wakes_master(tmp_path, tool):
    board = _board(tmp_path)
    _stuck(board, "worker", "venv", EV1)
    _stuck(board, "worker", "venv", EV2)
    t = json.loads((board / "T-001.json").read_text())
    t["stuck"]["escalated_at"] = stamp(25 * 60)
    (board / "T-001.json").write_text(json.dumps(t))

    issues = tk.health(str(board), tk.load_all(str(board)))
    crit = [msg for s, msg, _ in issues if s == "CRIT"]
    assert any("T-001 stuck on 'venv'" in m and "nobody acknowledged" in m for m in crit)
    ny = tk.ui_needs_you(str(board), "operator", "proj")
    item = next(i for i in ny["items"] if i["id"] == "T-001")
    assert "unacknowledged for over 24h" in item["why"]
    # CRIT health is a master wake gate: the planner is re-woken until it answers
    pending = tk.pending_work(str(board), "planner")
    assert any("T-001 stuck" in c for c in pending.get("health_crit") or []), pending


def test_cli_ack_then_resolve_returns_ticket_to_owner(tmp_path, tool):
    board = _board(tmp_path)
    _stuck(board, "worker", "venv", EV1)
    _stuck(board, "worker", "venv", EV2)

    self_ack = _cli(board, "worker", "stuck", "T-001", "--ack", "--notes", "on it")
    assert self_ack.returncode != 0 and "different seat" in self_ack.stderr

    ack = _cli(board, "planner", "stuck", "T-001", "--ack", "--notes", "rebuilding the venv for you")
    assert ack.returncode == 0, ack.stderr
    assert any(m["to"] == "worker" and "acknowledged by planner" in m["text"] for m in _msgs(board))
    issues = tk.health(str(board), tk.load_all(str(board)))
    assert any(s == "WARN" and "acked by planner" in msg for s, msg, _ in issues)

    res = _cli(board, "planner", "stuck", "T-001", "--resolve", "--notes", "venv rebuilt; rerun")
    assert res.returncode == 0, res.stderr
    t = tk.load(str(board), "T-001")
    assert t["status"] == "claimed" and t["owner"] == "worker"
    assert t["stuck"]["state"] == "resolved"
    assert not any("T-001 stuck" in msg for _, msg, _ in tk.health(str(board), tk.load_all(str(board))))
    assert not any(i["id"] == "T-001" for i in tk.ui_needs_you(str(board), "operator", "proj")["items"])

    # a fresh obstacle after resolution starts a new cycle at round 1
    again = _stuck(board, "worker", "gh auth", EV1)
    assert again.returncode == 0 and "round 1 of 2" in again.stdout
    assert tk.load(str(board), "T-001")["stuck"]["history"][-1]["obstacle"] == "venv"


def test_cli_rounds_inside_the_gap_are_refused(tmp_path, tool):
    board = _board(tmp_path)
    assert _stuck(board, "worker", "venv", EV1, env_extra={}).returncode == 0
    r = _stuck(board, "worker", "venv", EV2, env_extra={})
    assert r.returncode != 0 and "apart" in r.stderr
    assert tk.load(str(board), "T-001")["status"] == "claimed"


def test_status_stuck_word_no_longer_bypasses_the_gate(tmp_path, tool):
    board = _board(tmp_path)
    r = _cli(board, "worker", "status", "T-001", "stuck", "--notes", "help")
    assert r.returncode != 0 and "atm stuck T-001" in r.stderr
    assert tk.load(str(board), "T-001")["status"] == "claimed"


def test_needs_you_payload_still_matches_its_schema(tmp_path):
    jsonschema = pytest.importorskip("jsonschema")
    board = _board(tmp_path)
    _stuck(board, "worker", "venv", EV1)
    _stuck(board, "worker", "venv", EV2)
    schema = json.loads((ROOT / "docs/api/schemas/needs-you.json").read_text())
    jsonschema.validate(tk.ui_needs_you(str(board), "operator", "proj"), schema)


def test_done_ticket_drops_off_the_views(tmp_path):
    board = _board(tmp_path)
    _stuck(board, "worker", "venv", EV1)
    _stuck(board, "worker", "venv", EV2)
    t = json.loads((board / "T-001.json").read_text())
    t["status"] = "done"
    (board / "T-001.json").write_text(json.dumps(t))
    assert not any("T-001 stuck" in msg for _, msg, _ in tk.health(str(board), tk.load_all(str(board))))


def test_packaged_health_carries_the_same_stuck_rows(tmp_path):
    from src.ticket_board import cli as pcli
    board = _board(tmp_path)
    _stuck(board, "worker", "venv", EV1)
    _stuck(board, "worker", "venv", EV2)
    t = json.loads((board / "T-001.json").read_text())
    t["stuck"]["escalated_at"] = stamp(25 * 60)
    (board / "T-001.json").write_text(json.dumps(t))
    root = [i for i in tk.health(str(board), tk.load_all(str(board))) if "stuck" in i[1]]
    pkg = [i for i in pcli.health(str(board), pcli.load_all(str(board))) if "stuck" in i[1]]
    assert root and root == pkg and root[0][0] == "CRIT"
