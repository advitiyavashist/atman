"""T-023: tokens per ticket from session logs, split by claim windows.

Throwaway boards and a throwaway HOME only.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from ticket_board import ticket_meter as tm  # noqa: E402

T0 = datetime(2026, 11, 4, 12, 0, 0, tzinfo=timezone.utc)


def at(sec):
    return (T0 + timedelta(seconds=sec)).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"


def claude_line(sec, mid, block, inp=10, out=5, cw=0, cr=100):
    return json.dumps({"timestamp": at(sec), "type": "assistant", "message": {
        "id": mid, "content": [{"type": block}],
        "usage": {"input_tokens": inp, "output_tokens": out,
                  "cache_creation_input_tokens": cw, "cache_read_input_tokens": cr}}})


def codex_line(sec, inp, cached, out, cwd=None):
    if cwd is not None:
        return json.dumps({"timestamp": at(sec), "type": "session_meta", "payload": {"cwd": cwd}})
    return json.dumps({"timestamp": at(sec), "type": "event_msg", "payload": {
        "type": "token_count", "info": {"total_token_usage": {
            "input_tokens": inp, "cached_input_tokens": cached, "output_tokens": out,
            "cache_write_input_tokens": 0}}}})


def codex_total(rec):
    p = rec.get("payload") or {}
    if p.get("type") != "token_count":
        return None
    t = p["info"]["total_token_usage"]
    return {"tokens_in": t["input_tokens"], "tokens_out": t["output_tokens"],
            "tokens_cache_read": t["cached_input_tokens"],
            "tokens_cache_write": t["cache_write_input_tokens"]}


def ev(sec, kind, ticket, agent="alice"):
    return {"at": at(sec)[:-5] + "Z", "kind": kind, "ticket": ticket, "agent": agent}


# ---- pure ---------------------------------------------------------------

def test_claude_message_split_across_blocks_counts_once(tmp_path):
    p = tmp_path / "s.jsonl"
    p.write_text("\n".join([claude_line(1, "m1", "thinking"), claude_line(1, "m1", "text"),
                            claude_line(2, "m1", "tool_use"), claude_line(5, "m2", "text")]) + "\n")
    got = tm.claude_samples([str(p)])
    assert len(got) == 2
    assert got[0][1] == {"tokens_in": 10, "tokens_out": 5, "tokens_cache_write": 0,
                         "tokens_cache_read": 100}


def test_request_is_billed_at_its_first_line(tmp_path):
    """The request that ran `atm next` was made before the claim it caused."""
    p = tmp_path / "s.jsonl"
    p.write_text("\n".join([claude_line(9, "m1", "thinking"), claude_line(11, "m1", "tool_use")]))
    wins = tm.windows([ev(10, "claim", "T-1")], "alice")
    assert set(tm.attribute(tm.claude_samples([str(p)]), wins)) == {tm.UNATTRIBUTED}


def test_codex_cumulative_becomes_disjoint_deltas(tmp_path):
    p = tmp_path / "r.jsonl"
    p.write_text("\n".join([
        codex_line(0, 0, 0, 0, cwd="/wt"),
        codex_line(1, 1000, 900, 10),        # +100 fresh, +900 cached
        codex_line(2, 1000, 900, 10),        # repeat: no delta
        codex_line(3, 3000, 2500, 30),       # +400 fresh, +1600 cached
        codex_line(4, 500, 400, 5),          # counter reset: whole value
    ]))
    got = tm.codex_samples([str(p)], "/wt", codex_total)
    assert [s[1]["tokens_in"] for s in got] == [100, 400, 100]
    assert [s[1]["tokens_cache_read"] for s in got] == [900, 1600, 400]
    assert tm.codex_samples([str(p)], "/other", codex_total) == []


def test_windows_follow_claim_and_close_events():
    events = [ev(0, "claim", "T-1"), ev(10, "review", "T-1"),
              ev(20, "claim", "T-2"), ev(30, "claim", "T-3"),   # switch closes T-2
              ev(40, "block", "T-3"),
              ev(50, "update", "T-4"),                          # held without a seen claim
              ev(55, "claim", "T-9", agent="bob")]
    wins = tm.windows(events, "alice")
    ts = lambda s: tm.epoch(at(s)[:-5] + "Z")
    assert wins == [("T-1", ts(0), ts(10)), ("T-2", ts(20), ts(30)),
                    ("T-3", ts(30), ts(40)), ("T-4", ts(50), None)]


def test_attribute_and_budget_state():
    wins = [("T-1", 0, 10), ("T-2", 20, None)]
    u = {k: 1 for k in tm.FIELDS}
    got = tm.attribute([(5, u), (15, u), (25, u), (99, u)], wins)
    assert got["T-1"]["total"] == 4 and got["T-2"]["requests"] == 2
    assert got[tm.UNATTRIBUTED]["requests"] == 1
    assert tm.budget_state(10, 0) == ""
    assert tm.budget_state(79, 100) == "ok"
    assert tm.budget_state(80, 100) == "warn"
    assert tm.budget_state(101, 100) == "over"


# ---- CLI ----------------------------------------------------------------

def _env(tmp_path, agent="alice"):
    e = dict(os.environ, TICKETS_DIR=str(tmp_path / "repo" / ".tickets"), TICKET_AGENT=agent,
             TICKET_SEAT=agent, HOME=str(tmp_path / "home"))
    for k in ("TICKETS_STOP_HOOK", "TICKET_SESSION_ID", "CLAUDE_CODE_SESSION_ID"):
        e.pop(k, None)
    return e


def cli(tmp_path, *args, agent="alice"):
    return subprocess.run([sys.executable, str(ROOT / "tickets.py"), *args],
                          capture_output=True, text=True, env=_env(tmp_path, agent),
                          cwd=str(tmp_path / "repo"))


def test_meter_bills_each_ticket_its_own_requests(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    assert cli(tmp_path, "init").returncode == 0
    assert cli(tmp_path, "create", "one").returncode == 0
    assert cli(tmp_path, "create", "two").returncode == 0
    board = repo / ".tickets"
    (board / "agents").mkdir(exist_ok=True)
    (board / "agents" / "alice.json").write_text(json.dumps(
        {"owner": "alice", "cwd": str(repo), "worktree": str(repo)}))
    (board / "trajectories.jsonl").write_text("\n".join(json.dumps(dict(e, v=1)) for e in [
        ev(0, "claim", "T-001"), ev(100, "review", "T-001"),
        ev(200, "claim", "T-002")]) + "\n")
    slug = re.sub(r"[^A-Za-z0-9]", "-", os.path.abspath(str(repo)))
    tdir = tmp_path / "home" / ".claude" / "projects" / slug
    tdir.mkdir(parents=True)
    (tdir / "sess.jsonl").write_text("\n".join([
        claude_line(10, "a", "text", inp=100, out=50, cr=1000),
        claude_line(10, "a", "tool_use", inp=100, out=50, cr=1000),   # same request
        claude_line(150, "b", "text", inp=7, out=3, cr=0),            # holding nothing
        claude_line(250, "c", "text", inp=1, out=1, cr=2000),
    ]) + "\n")
    os.utime(tdir / "sess.jsonl", (T0.timestamp() + 300,) * 2)
    r = cli(tmp_path, "assign", "T-002", "--token-budget", "1500")
    assert r.returncode == 0, r.stderr
    r = cli(tmp_path, "meter", "--json")
    assert r.returncode == 0, r.stderr
    rows = {x["ticket"]: x for x in json.loads(r.stdout)["tickets"]}
    assert rows["T-001"]["total"] == 1150 and rows["T-001"]["requests"] == 1
    assert rows["T-002"]["total"] == 2002 and rows["T-002"]["state"] == "over"
    assert rows[None]["total"] == 10
    text = cli(tmp_path, "meter").stdout
    assert "OVER BUDGET" in text and "T-001" in text
    assert cli(tmp_path, "assign", "T-002", "--token-budget", "0").returncode == 0
    rows = {x["ticket"]: x for x in json.loads(cli(tmp_path, "meter", "--json").stdout)["tickets"]}
    assert rows["T-002"]["budget"] is None and rows["T-002"]["state"] == ""


def test_run_usage_counts_a_split_claude_message_once(tmp_path, monkeypatch):
    sys.path.insert(0, str(ROOT))
    import tickets
    slug = re.sub(r"[^A-Za-z0-9]", "-", os.path.abspath(str(tmp_path)))
    d = tmp_path / "projects" / slug
    d.mkdir(parents=True)
    (d / "s.jsonl").write_text("\n".join([claude_line(1, "m", "thinking"),
                                          claude_line(1, "m", "text")]) + "\n")
    monkeypatch.setattr(tickets, "CLAUDE_PROJECTS_DIR", str(tmp_path / "projects"))
    got = tickets._session_usage_claude(str(tmp_path), T0.timestamp(), T0.timestamp() + 5)
    assert got["tokens_in"] == 10 and got["tokens_cache_read"] == 100
