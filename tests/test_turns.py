"""T-312: tickets turns -- table + frozen --json.

A TURN is one completed watch run (run_end). Backfill has no runs: turns is
JSON null, never 0. Aggregates skip unmeasured tickets.
"""

import json
import shutil
from pathlib import Path

import pytest

from ticket_board.turns import (
    ROW_KEYS,
    TrajectoryParseError,
    build_turns_report,
    load_trajectory_events,
)
from test_wakeup import board, run  # noqa: F401

TOOL = Path(__file__).resolve().parents[1] / "tickets.py"
FIXTURE = Path(__file__).resolve().parent / "fixtures" / "turns_trajectories.jsonl"
CLI = Path(__file__).resolve().parents[1] / "src" / "ticket_board" / "cli.py"


def _events():
    out = []
    for ln in FIXTURE.read_text().splitlines():
        ln = ln.strip()
        if ln:
            out.append(json.loads(ln))
    return out


def test_fixture_turns_null_for_backfill_never_zero():
    report = build_turns_report(_events())
    by = {r["ticket"]: r for r in report["tickets"]}
    assert by["T-010"]["turns"] == 2
    assert by["T-010"]["owner"] == "alice"
    assert by["T-010"]["model"] == "opus"
    assert by["T-010"]["outcome"] == "done"
    assert by["T-010"]["reopens"] == 0
    assert by["T-010"]["wall_clock_s"] == 50 * 60
    # reopen adds later runs; both run_ends count
    assert by["T-011"]["turns"] == 2
    assert by["T-011"]["reopens"] == 1
    assert by["T-011"]["model"] == "sonnet"
    # backfill only — no run_end
    assert "turns" in by["T-012"]
    assert by["T-012"]["turns"] is None
    assert by["T-012"]["outcome"] == "done"
    # orphan run_start is not a turn
    assert by["T-013"]["turns"] is None
    agg = report["aggregates"]
    assert agg["n"] == 2
    assert agg["n_unmeasured"] == 2
    assert agg["mean"] == 2.0
    assert agg["median"] == 2.0
    agents = {r["agent"]: r for r in agg["by_agent"]}
    assert agents["alice"]["n"] == 1 and agents["alice"]["mean"] == 2.0
    assert agents["bob"]["n"] == 1
    models = {r["model"]: r for r in agg["by_model"]}
    assert set(models) == {"opus", "sonnet"}
    for r in report["tickets"]:
        assert tuple(r.keys()) == ROW_KEYS


def test_stuck_msgs_counted_from_messages_not_traj():
    msgs = [
        {"at": "2026-09-01T10:15:00Z", "from": "alice", "to": "master",
         "re": "T-010", "text": "stuck: cannot merge"},
        {"at": "2026-09-01T10:16:00Z", "from": "alice", "to": "master",
         "re": "T-010", "text": "still going"},
        {"at": "2026-09-01T11:20:00Z", "from": "bob", "to": "master",
         "re": "T-011", "text": "Stuck: tests"},
    ]
    report = build_turns_report(_events(), messages=msgs)
    by = {r["ticket"]: r for r in report["tickets"]}
    assert by["T-010"]["stuck"] == 1
    assert by["T-011"]["stuck"] == 1
    assert by["T-012"]["stuck"] == 0


def test_cli_turns_json_and_table(board):
    shutil.copy(FIXTURE, board / "trajectories.jsonl")
    repo = board.parent
    # role/priority for aggregates
    t = json.loads((board / "T-001.json").read_text())
    # plant matching ticket files so --epic / role aggregates resolve
    for tid, owner, role, pri, epic, model in (
        ("T-010", "alice", "backend", 1, "E-011", "opus"),
        ("T-011", "bob", "backend", 2, "E-011", "sonnet"),
        ("T-012", "alice", "docs", 3, "E-009", "opus"),
        ("T-013", "alice", "backend", 2, "E-011", "opus"),
    ):
        rec = dict(t)
        rec.update({"id": tid, "owner": owner, "role": role, "priority": pri,
                    "epic": epic, "title": tid, "status": "done"})
        (board / ("%s.json" % tid)).write_text(json.dumps(rec, indent=2))
    wf = {"alice": {"model": "opus", "tool": "claude"},
          "bob": {"model": "sonnet", "tool": "claude"}}
    (board / "workforce.json").write_text(json.dumps(wf))
    r = run(board, "turns", "--json", cwd=repo)
    assert r.returncode == 0, r.stdout + r.stderr
    data = json.loads(r.stdout)
    assert data["v"] == 1
    by = {row["ticket"]: row for row in data["tickets"]}
    assert by["T-010"]["turns"] == 2
    assert by["T-012"]["turns"] is None
    roles = {x["role"]: x for x in data["aggregates"]["by_role"]}
    assert roles["backend"]["n"] == 2
    r2 = run(board, "turns", "--ticket", "T-010", cwd=repo)
    assert r2.returncode == 0, r2.stdout + r2.stderr
    assert "T-010" in r2.stdout and "T-011" not in r2.stdout
    assert "turns" in r2.stdout
    r3 = run(board, "turns", "--epic", "E-011", "--json", cwd=repo)
    ids = {row["ticket"] for row in json.loads(r3.stdout)["tickets"]}
    assert "T-012" not in ids
    assert "T-010" in ids


def test_packaged_cli_same_json(board):
    """Two-copy rule: src/ticket_board/cli.py must emit the same --json."""
    shutil.copy(FIXTURE, board / "trajectories.jsonl")
    repo = board.parent
    import os
    import subprocess
    import sys
    env = dict(os.environ, TICKETS_DIR=str(board), PYTHONPATH=str(TOOL.parent / "src"))
    env.pop("TICKETS_STOP_HOOK", None)
    p = subprocess.run([sys.executable, str(CLI), "turns", "--json"],
                       capture_output=True, text=True, env=env, cwd=repo)
    assert p.returncode == 0, p.stdout + p.stderr
    q = run(board, "turns", "--json", cwd=repo)
    a, b = json.loads(p.stdout), json.loads(q.stdout)
    assert a["tickets"] == b["tickets"]
    assert a["aggregates"]["mean"] == b["aggregates"]["mean"]


def test_load_events_from_board_dir(board):
    shutil.copy(FIXTURE, board / "trajectories.jsonl")
    ev = load_trajectory_events(str(board))
    assert any(e.get("ticket") == "T-010" and e.get("kind") == "run_end" for e in ev)


def test_malformed_jsonl_line_errors_with_line_number(board):
    """T-350: malformed trajectories.jsonl must error with line number, not skip."""
    repo = board.parent
    (board / "trajectories.jsonl").write_text(
        '{"v":1,"kind":"claim","ticket":"T-001"}\nNOT VALID JSON\n'
    )
    r = run(board, "turns", "--json", cwd=repo)
    assert r.returncode != 0, "expected malformed-line error, got silent skip: %r" % r.stdout
    assert ":2:" in r.stderr or "line 2" in r.stderr.lower() or "2:" in r.stderr


def test_load_trajectory_events_raises_on_malformed_line(board):
    (board / "trajectories.jsonl").write_text('{"v":1}\n{broken\n')
    with pytest.raises(TrajectoryParseError) as exc:
        load_trajectory_events(str(board))
    assert exc.value.line_no == 2
