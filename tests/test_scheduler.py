"""T-315: tickets route --shadow.

n=0 uses named priors (and prints 'no measured trajectories').
n<5 stays on the prior. n>=5 uses learned median turns.
turns=null is n_unmeasured, never 0. Malformed jsonl must error (T-350).
--apply is unimplemented.
"""

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from ticket_board.scheduler import (
    MIN_SUPPORT,
    build_estimates,
    prior_spec,
)
from ticket_board.turns import TrajectoryParseError, load_trajectory_events
from test_wakeup import board, run  # noqa: F401

TOOL = Path(__file__).resolve().parents[1] / "tickets.py"
CLI = Path(__file__).resolve().parents[1] / "src" / "ticket_board" / "cli.py"
ROOT = TOOL.parent


def _run_pkg(board, *args, agent=""):
    env = dict(os.environ, TICKETS_DIR=str(board), TICKET_AGENT=agent or "",
               HOME=str(board.parent.parent / "home"),
               PYTHONPATH=str(ROOT / "src"))
    env.pop("TICKETS_STOP_HOOK", None)
    return subprocess.run([sys.executable, str(CLI), *args],
                          capture_output=True, text=True, env=env,
                          cwd=board.parent)


def _join(board, name, role, model, cost, tool="claude"):
    r = run(board, "join", name, "--roles", role, "--model", model,
            "--cost", cost, "--tool", tool, agent=name)
    assert r.returncode == 0, r.stderr


def _stamp_done(board, tid, owner, role, pri, status="done"):
    path = board / ("%s.json" % tid)
    rec = json.loads(path.read_text()) if path.exists() else json.loads(
        (board / "T-001.json").read_text())
    rec.update({"id": tid, "owner": owner, "role": role, "priority": pri,
                "title": tid, "status": status, "deps": []})
    path.write_text(json.dumps(rec, indent=2))


def _event(kind, ticket, agent, model, at, **extra):
    rec = {"v": 1, "at": at, "kind": kind, "ticket": ticket, "agent": agent}
    if model:
        rec["model"] = model
    rec.update(extra)
    return rec


def _finish_with_turns(tid, agent, model, n_turns, day, reopens=0):
    """n_turns run_ends. n_turns is a real count — never used for unmeasured."""
    evs = [_event("claim", tid, agent, model, "%sT10:00:00Z" % day)]
    for i in range(n_turns):
        evs.append(_event("run_start", tid, agent, model,
                          "%sT10:%02d:00Z" % (day, i + 1), run_no=i + 1))
        evs.append(_event("run_end", tid, agent, model,
                          "%sT11:%02d:00Z" % (day, i + 1), run_no=i + 1, exit=0))
    for i in range(reopens):
        evs.append(_event("reopen", tid, agent, model, "%sT12:%02d:00Z" % (day, i),
                          outcome="reopened"))
    evs.append(_event("done", tid, agent, model, "%sT13:00:00Z" % day, outcome="done"))
    return evs


def _write_jsonl(board, events):
    (board / "trajectories.jsonl").write_text(
        "".join(json.dumps(e) + "\n" for e in events))


def test_prior_spec_tiers():
    assert prior_spec({"priority": 1, "role": "backend"})[0] == "opus/high"
    assert prior_spec({"priority": 2, "role": "backend"})[0] == "sonnet/medium"
    assert prior_spec({"priority": 3, "role": "docs"})[0] == "codex/cursor-low"
    assert prior_spec({"priority": 2, "role": "backend",
                       "title": "P1 contract review"})[0] == "opus/high"


def test_n0_shadow_prints_no_measured_and_prior(board):
    _join(board, "opus-a", "backend", "opus", "high")
    _join(board, "sonnet-a", "backend", "sonnet", "medium")
    _join(board, "docs-a", "docs", "gpt", "low", tool="codex")
    run(board, "create", "P1 backend hole", "--role", "backend", "--priority", "1")
    run(board, "create", "routine backend", "--role", "backend", "--priority", "2")
    run(board, "create", "write the tests", "--role", "docs", "--priority", "3")
    r = run(board, "route", "--shadow", cwd=board.parent)
    assert r.returncode == 0, r.stdout + r.stderr
    assert "no measured trajectories" in r.stdout
    lines = [ln for ln in r.stdout.splitlines() if ln.startswith("T-")]
    assert lines, r.stdout
    assert all("prior" in ln for ln in lines)
    assert all(ln.split()[2] == "prior" for ln in lines)
    # one shadow_decision per ready ticket; no suggested rewrite
    evs = [json.loads(ln) for ln in (board / "trajectories.jsonl").read_text().splitlines() if ln.strip()]
    shadows = [e for e in evs if e.get("kind") == "shadow_decision"]
    assert shadows
    assert all(e.get("source") == "prior" for e in shadows)
    t1 = json.loads((board / "T-001.json").read_text())
    assert t1.get("status") == "open"
    assert "suggested" not in t1 or t1.get("suggested") in (None, "")


def test_n_lt_5_stays_on_prior(board):
    _join(board, "alice", "backend", "opus", "high")
    _join(board, "bob", "backend", "sonnet", "medium")
    events = []
    for i in range(3):
        tid = "T-1%02d" % i
        _stamp_done(board, tid, "alice", "backend", 1)
        events.extend(_finish_with_turns(tid, "alice", "opus", 2, "2026-09-0%d" % (i + 1)))
    run(board, "create", "ready p1", "--role", "backend", "--priority", "1")
    _write_jsonl(board, events)
    est = build_estimates(load_trajectory_events(str(board)),
                          tickets=[json.loads((board / "T-100.json").read_text())]
                          + [json.loads((board / ("T-1%02d.json" % i)).read_text())
                             for i in range(3)],
                          workforce=json.loads((board / "workforce.json").read_text()))
    cell_ns = [c["n"] for c in est["cells"].values()]
    assert cell_ns and max(cell_ns) < MIN_SUPPORT
    r = run(board, "route", "--shadow", cwd=board.parent)
    assert r.returncode == 0, r.stdout + r.stderr
    ready_lines = [ln for ln in r.stdout.splitlines() if ln.startswith("T-") and "prior" in ln]
    assert ready_lines, r.stdout


def test_n_ge_5_uses_learned_median(board):
    _join(board, "alice", "backend", "opus", "high")
    _join(board, "bob", "backend", "sonnet", "medium")
    events = []
    for i in range(5):
        tid = "T-2%02d" % i
        _stamp_done(board, tid, "alice", "backend", 1)
        events.extend(_finish_with_turns(tid, "alice", "opus", 2, "2026-08-%02d" % (i + 1)))
    for i in range(5):
        tid = "T-3%02d" % i
        _stamp_done(board, tid, "bob", "backend", 1)
        events.extend(_finish_with_turns(tid, "bob", "sonnet", 8, "2026-07-%02d" % (i + 1)))
    r = run(board, "create", "ready learned", "--role", "backend", "--priority", "1")
    assert r.returncode == 0, r.stderr
    ready_id = None
    for p in sorted(board.glob("T-*.json")):
        rec = json.loads(p.read_text())
        if rec.get("status") == "open" and rec.get("title") == "ready learned":
            ready_id = rec["id"]
            break
    assert ready_id
    _write_jsonl(board, events)
    r = run(board, "route", "--shadow", cwd=board.parent)
    assert r.returncode == 0, r.stdout + r.stderr
    assert "no measured trajectories" not in r.stdout
    hit = [ln for ln in r.stdout.splitlines() if ln.startswith(ready_id)]
    assert hit, r.stdout
    assert "learned" in hit[0]
    assert "alice" in hit[0]
    evs = [json.loads(ln) for ln in (board / "trajectories.jsonl").read_text().splitlines() if ln.strip()]
    shadows = [e for e in evs if e.get("kind") == "shadow_decision" and e.get("ticket") == ready_id]
    assert shadows and shadows[0]["source"] == "learned"
    assert shadows[0]["learned_agent"] == "alice"
    assert shadows[0]["expected_turns"] == 2.0
    assert shadows[0]["n"] == 5
    assert shadows[0].get("runner_up") == "bob"


def test_null_turns_excluded_not_zero(board):
    _join(board, "alice", "backend", "opus", "high")
    events = []
    for i in range(5):
        tid = "T-4%02d" % i
        _stamp_done(board, tid, "alice", "backend", 1)
        events.extend(_finish_with_turns(tid, "alice", "opus", 3, "2026-06-%02d" % (i + 1)))
    # backfill-only sibling: done, no run_end
    _stamp_done(board, "T-499", "alice", "backend", 1)
    events.extend([
        _event("claim", "T-499", "alice", "opus", "2026-06-20T10:00:00Z", src="backfill"),
        _event("done", "T-499", "alice", "opus", "2026-06-20T11:00:00Z",
               src="backfill", outcome="done"),
    ])
    run(board, "create", "ready", "--role", "backend", "--priority", "1")
    _write_jsonl(board, events)
    tickets = [json.loads(p.read_text()) for p in board.glob("T-*.json")]
    est = build_estimates(load_trajectory_events(str(board)), tickets=tickets,
                          workforce=json.loads((board / "workforce.json").read_text()))
    assert est["n_unmeasured"] >= 1
    alice = [c for c in est["cells"].values() if c["agent"] == "alice" and c["n"]]
    assert alice
    assert alice[0]["n"] == 5
    assert alice[0]["n_unmeasured"] >= 1
    assert alice[0]["median_turns"] == 3


def test_malformed_line_errors(board):
    _join(board, "alice", "backend", "opus", "high")
    (board / "trajectories.jsonl").write_text(
        '{"v":1,"kind":"claim","ticket":"T-001"}\nNOT VALID JSON\n')
    r = run(board, "route", "--shadow", cwd=board.parent)
    assert r.returncode != 0, r.stdout
    assert ":2:" in r.stderr or "2:" in r.stderr
    with pytest.raises(TrajectoryParseError):
        load_trajectory_events(str(board))


def test_apply_unimplemented(board):
    r = run(board, "route", "--apply", cwd=board.parent)
    assert r.returncode != 0
    assert "unimplemented" in (r.stderr + r.stdout).lower()
    assert "shadow" in (r.stderr + r.stdout).lower()


def test_shadow_report_agreement(board):
    _join(board, "alice", "backend", "opus", "high")
    events = _finish_with_turns("T-001", "alice", "opus", 2, "2026-05-01")
    _stamp_done(board, "T-001", "alice", "backend", 1)
    events.append({
        "v": 1, "at": "2026-05-01T09:00:00Z", "kind": "shadow_decision",
        "ticket": "T-001", "rule_agent": "alice", "learned_agent": "alice",
        "source": "learned", "n": 5,
    })
    _write_jsonl(board, events)
    r = run(board, "route", "--shadow", "--report", cwd=board.parent)
    assert r.returncode == 0, r.stdout + r.stderr
    assert "agreement rate: 1.00" in r.stdout
    # --report must not append another shadow_decision
    kinds = [json.loads(ln)["kind"] for ln in (board / "trajectories.jsonl").read_text().splitlines() if ln.strip()]
    assert kinds.count("shadow_decision") == 1


def test_packaged_cli_same_shadow_table(board):
    _join(board, "alice", "backend", "opus", "high")
    r1 = run(board, "route", "--shadow", cwd=board.parent)
    # wipe the write so the second copy starts from the same jsonl
    if (board / "trajectories.jsonl").exists():
        (board / "trajectories.jsonl").unlink()
    r2 = _run_pkg(board, "route", "--shadow")
    assert r1.returncode == 0 and r2.returncode == 0, r1.stderr + r2.stderr
    # drop trailing writes; compare printed table
    t1 = "\n".join(ln for ln in r1.stdout.splitlines() if not ln.startswith("wrote"))
    t2 = "\n".join(ln for ln in r2.stdout.splitlines() if not ln.startswith("wrote"))
    assert t1 == t2
