"""T-1072: agent map -- `atm agents` and the UI panel.

One fixture board: T-002 author run -> REJECT -> fix run -> ACCEPT, T-003
running with a limited reviewer, T-004 behind a dead watcher. Read-only is
checked in-process with an audit hook (no subprocess, no write-mode open) and
a hash snapshot of the board.
"""
from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

from test_wakeup import board  # noqa: F401
from ui_server_harness import make_ui_server_fixture

ROOT = Path(__file__).resolve().parents[1]
TOOL = ROOT / "tickets.py"
CLI = ROOT / "src" / "ticket_board" / "cli.py"
ui_server = make_ui_server_fixture("t1072-probe")
LONG = "Make the login flow survive a provider outage without dropping queued work " * 3
DEAD_PID = 999999


def stamp(mins_ago):
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(time.time() - mins_ago * 60))


def write(path, obj):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj))


def ticket(board, tid, title, **kw):
    write(board / (tid + ".json"), dict(dict(id=tid, title=title, status="claimed",
                                             role="backend", deps=[], notes=[]), **kw))


def run_pair(agent, tid, n, start, end=None, **end_fields):
    rid = "r-%s-%d-x" % (agent, n)
    out = [{"v": 1, "at": stamp(start), "kind": "run_start", "agent": agent, "ticket": tid,
            "run_no": n, "run_id": rid, "harness": end_fields.pop("harness", "claude"),
            "trigger": end_fields.pop("trigger", ["holding"])}]
    if end is not None:
        out.append(dict({"v": 1, "at": stamp(end), "kind": "run_end", "agent": agent,
                         "ticket": tid, "run_no": n, "run_id": rid, "exit": 0,
                         "started_at": stamp(start), "ended_at": stamp(end)}, **end_fields))
    return out


def receipt(board, seat, tid, n, start, pid, beat, active=True):
    write(board / "agents" / (seat + ".run"),
          {"pid": pid, "run": n, "run_id": "r-%s-%d-x" % (seat, n), "cwd": str(board.parent),
           "ticket": tid, "started": stamp(start), "active": active, "beat": stamp(beat),
           "generation": 1})
    write(board / "agents" / (seat + ".json"), {"owner": seat, "seen": stamp(beat)})


def seed(board):
    ticket(board, "T-002", LONG, owner="alice", pr="https://github.com/o/r/pull/41",
           status="done", review_head="b" * 40,
           review_events=[{"kind": "reject", "by": "bob", "at": stamp(50), "sha": "a" * 40,
                           "reason": "tests"},
                          {"kind": "accept", "by": "bob", "at": stamp(10), "sha": "b" * 40,
                           "notes": "ok"}])
    ticket(board, "T-003", "Running work", owner="carol", pr="17")
    ticket(board, "T-004", "Orphaned work", reserved_for="dave",
           notes=[{"by": "cos", "at": stamp(40), "text": "dispatch: reserved for dave harness=codex"}])
    events = []
    events += run_pair("alice", "T-002", 1, 90, 60, tokens_in=1000, tokens_out=234)
    events += run_pair("bob", "T-002", 1, 55, 49, harness="codex", trigger=["review_queue"])
    events += run_pair("alice", "T-002", 2, 40, 20, tokens_in=500, tokens_out=500)
    events += run_pair("bob", "T-002", 2, 15, 9, harness="codex", trigger=["review_queue"])
    events += run_pair("carol", "T-003", 1, 5)
    events += run_pair("erin", "T-003", 1, 3, trigger=["review_queue"])
    events += run_pair("dave", "T-004", 1, 30, harness="codex")
    events += run_pair("frank", "T-001", 1, 60 * 72, 60 * 71)  # older than 24h
    (board / "trajectories.jsonl").write_text("".join(json.dumps(e) + "\n" for e in events))
    receipt(board, "carol", "T-003", 1, 5, os.getpid(), 0)
    receipt(board, "erin", "T-003", 1, 3, os.getpid(), 0)
    agent = json.loads((board / "agents" / "erin.json").read_text())
    agent["limit"] = {"at": stamp(2), "until": "tomorrow 09:00", "note": "weekly cap"}
    write(board / "agents" / "erin.json", agent)
    receipt(board, "dave", "T-004", 1, 30, DEAD_PID, 25)
    (board / "agents" / "dave.watch.pid").write_text(str(DEAD_PID))


def tk_module(monkeypatch, home):
    monkeypatch.setenv("HOME", str(home))
    spec = importlib.util.spec_from_file_location("tickets_t1072", TOOL)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def cli(board, *args, tool=TOOL):
    e = dict(os.environ, TICKETS_DIR=str(board), HOME=str(board.parent.parent / "home"),
             PYTHONPATH=str(ROOT / "src"))
    e.pop("TICKET_AGENT", None)
    return subprocess.run([sys.executable, str(tool), *args], capture_output=True, text=True,
                          env=e, cwd=str(board.parent))


def tree_hash(root):
    h = {}
    for p in sorted(Path(root).rglob("*")):
        if p.is_file():
            h[str(p)] = hashlib.sha256(p.read_bytes()).hexdigest()
    return h


def rows_by_seat(data):
    return {(r["seat"], r["run_no"]): r for g in data["groups"] for r in g["rows"]}


def test_loop_running_dead_and_limited_rows(board):
    seed(board)
    r = cli(board, "agents", "--json")
    assert r.returncode == 0, r.stderr
    data = json.loads(r.stdout)
    groups = {g["ticket"]: g for g in data["groups"]}
    assert set(groups) == {"T-002", "T-003", "T-004"}  # T-001 run is >24h old
    loop = groups["T-002"]
    assert [(x["seat"], x["role"], x["state"]) for x in loop["rows"]] == [
        ("alice", "author", "done"), ("bob", "reviewer", "done"),
        ("alice", "fixer", "done"), ("bob", "reviewer", "done")]
    assert [x["verdict"] for x in loop["rows"]] == [
        None, {"kind": "REJECT", "sha": "aaaaaaa"}, None, {"kind": "ACCEPT", "sha": "bbbbbbb"}]
    assert loop["pr"] == "#41" and loop["runs"] == 4
    assert loop["tokens"] == 2234 and loop["tokens_unknown"] == 2
    assert loop["rows"][1]["tokens"] is None and loop["rows"][1]["harness"] == "codex"
    assert 30 * 60 <= loop["rows"][0]["elapsed_s"] <= 30 * 60 + 5

    rows = rows_by_seat(data)
    assert rows[("carol", 1)]["state"] == "running" and rows[("carol", 1)]["role"] == "author"
    assert rows[("carol", 1)]["liveness"]["source"] == "heartbeat"
    assert rows[("erin", 1)]["state"] == "limited" and rows[("erin", 1)]["role"] == "reviewer"
    assert rows[("dave", 1)]["state"] == "dead" and rows[("dave", 1)]["role"] == "author"
    assert data["running"] == 1 and groups["T-003"]["pr"] == "#17"
    assert data["groups"][0]["ticket"] == "T-003"  # running groups first

    every = json.loads(cli(board, "agents", "--json", "--all").stdout)
    assert "T-001" in {g["ticket"] for g in every["groups"]}


def test_text_output_truncates_long_titles_and_says_unknown(board):
    seed(board)
    r = cli(board, "agents")
    assert r.returncode == 0, r.stderr
    out = r.stdout
    # T-1076 put a per-provider usage header above the map; the map itself is
    # still the first thing after it.
    body = [ln for ln in out.splitlines() if not ln.startswith("usage  ")]
    assert body[0].startswith("1 agents running")
    assert LONG not in out and LONG.strip()[:30] in out and "..." in out
    assert max(len(ln) for ln in out.splitlines()) < 120
    assert "REJECT aaaaaaa" in out and "ACCEPT bbbbbbb" in out
    assert "unknown" in out and "1,234" in out and "tokens 2,234 (+2 unknown)" in out
    assert "PR #41" in out


def test_missing_usage_records_are_unknown_not_zero(board):
    ticket(board, "T-003", "Running work", owner="carol")
    receipt(board, "carol", "T-003", 1, 5, os.getpid(), 0)
    assert not (board / "trajectories.jsonl").exists()
    assert not (board / "provider_usage.json").exists()
    data = json.loads(cli(board, "agents", "--json").stdout)
    (g,) = data["groups"]
    assert g["tokens"] is None and g["tokens_unknown"] == 1
    assert g["rows"][0]["tokens"] is None and g["rows"][0]["state"] == "running"
    assert "unknown" in cli(board, "agents").stdout


def test_remote_token_names_and_old_receipt_do_not_split_a_run():
    sys.path.insert(0, str(ROOT / "src"))
    from ticket_board import agent_map
    ev = run_pair("rem", "T-009", 1, 20, 10, input_tokens=7, output_tokens=3)
    for e in ev:
        e.pop("run_id")
    old = {"pid": 1, "run": 1, "started": stamp(20), "active": False, "ended": stamp(10), "rc": 0}
    data = agent_map.build([{"id": "T-009", "title": "x", "owner": "rem"}], ev, {"rem": old}, {})
    (g,) = data["groups"]
    assert g["runs"] == 1 and g["tokens"] == 10 and g["rows"][0]["state"] == "done"


def test_empty_board_prints_zero(board):
    r = cli(board, "agents")
    assert r.returncode == 0
    # T-1076 usage header first, then the map. A board with nothing read says
    # so; it never prints a number it does not have.
    lines = r.stdout.splitlines()
    assert lines[0] == "usage  no provider read yet (atm harness usage)"
    assert lines[1].startswith("0 agents running")
    assert "no seat runs recorded" in r.stdout


def test_read_only_no_subprocess_no_writes(board, monkeypatch):
    seed(board)
    # An expired limit is what _active_seat_limit would clear on disk.
    agent = json.loads((board / "agents" / "dave.json").read_text())
    agent["limit"] = {"at": stamp(120), "reset_at": stamp(60)}
    write(board / "agents" / "dave.json", agent)
    monkeypatch.setenv("TICKETS_DIR", str(board))
    monkeypatch.chdir(board.parent)
    tk = tk_module(monkeypatch, board.parent.parent / "home")
    before = tree_hash(board)
    seen = []
    armed = [True]

    def hook(event, args):
        if not armed[0]:
            return
        if event in ("subprocess.Popen", "os.system", "os.posix_spawn", "os.exec", "os.fork",
                     "os.spawn", "pty.spawn"):
            seen.append((event, args))
        elif event == "open" and len(args) > 2:
            mode, flags = args[1], args[2] or 0
            if (isinstance(mode, str) and any(c in mode for c in "wax+")) or \
                    flags & (os.O_WRONLY | os.O_RDWR | os.O_CREAT):
                seen.append((event, args[0]))
    sys.addaudithook(hook)
    try:
        for argv in (["atm", "agents"], ["atm", "agents", "--all", "--json"]):
            monkeypatch.setattr(sys, "argv", argv)
            tk.main()
    finally:
        armed[0] = False
    assert seen == []
    assert tree_hash(board) == before


def test_command_lives_on_the_installed_entry_point_only_once():
    install = (ROOT / "install.sh").read_text()
    assert 'ln -sf "$HERE/tickets.py" "$BIN/atm"' in install
    src = TOOL.read_text()
    assert 'sub.add_parser("agents"' in src
    # One implementation, in the shared module. The packaged CLI has neither
    # agent_liveness nor `ui`, so it gets no second, weaker copy.
    packaged = CLI.read_text()
    assert 'add_parser("agents"' not in packaged and "agent_map" not in packaged
    assert "_agent_map_mod().build(" in src and "def _runs(" not in src


def test_ui_panel_and_board_json_carry_the_same_map(board, ui_server):
    seed(board)
    page = ui_server.get("/", raw=True).decode()
    assert 'id="agentMapPanel"' in page and 'id="agentMapPill"' in page
    assert "renderAgentMap(d.agent_map)" in page
    snap = ui_server.get()
    m = snap["agent_map"]
    cli_map = json.loads(cli(board, "agents", "--json").stdout)
    assert set(m) == {"v", "scope", "running", "runs", "groups"}
    shape = lambda d: [(g["ticket"], [(r["seat"], r["role"], r["state"], r["verdict"], r["tokens"])
                                      for r in g["rows"]]) for g in d["groups"]]
    assert shape(m) == shape(cli_map)
    row_keys = {"seat", "harness", "ticket", "title", "pr", "role", "state", "liveness", "verdict",
                "run_id", "run_no", "started", "ended", "elapsed_s", "tokens", "tokens_in",
                "tokens_out"}
    assert all(set(r) == row_keys for g in m["groups"] for r in g["rows"])
    assert all({"ticket", "title", "pr", "status", "rows", "runs", "running", "elapsed_s",
                "tokens", "tokens_unknown"} == set(g) for g in m["groups"])
