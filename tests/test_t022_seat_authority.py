"""T-022: accept/reject/assign authority comes from the live seat tree.

A supervised seat runs under `tickets.py watch --agent <seat>`; that ancestry
names it, whatever TICKET_AGENT/TICKET_SEAT say. The integration tests build
the real process shape: a stand-in watcher process whose command line is
`<python> <dir>/tickets.py watch --agent alice --cwd <repo>` launches the CLI
as its child. Throwaway boards only; both CLI copies.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from ticket_board import seat_authority as sa  # noqa: E402

TOOLS = [ROOT / "tickets.py", ROOT / "src" / "ticket_board" / "cli.py"]
TOOL_IDS = ["tickets.py", "cli.py"]
SHA = "b" * 40


# ---- pure rules ---------------------------------------------------------

def test_outside_any_seat_tree_changes_nothing():
    assert sa.decide("atman-ceo", [], "accept") == (True, "outside-seat-tree", "")


def test_nearest_seat_is_who_you_are():
    ok, how, _ = sa.decide("alice", ["alice"], "accept")
    assert ok and how == "seat-tree:alice"
    ok, how, reason = sa.decide("atman-ceo", ["alice"], "accept")
    assert not ok and how == "seat-tree:alice"
    assert "inside seat alice's watcher" in reason and "atm msg --to atman-ceo" in reason


def test_a_seat_cannot_mint_a_seat():
    # alice started `watch --agent atman-ceo` from inside her own tree.
    ok, _, reason = sa.decide("atman-ceo", ["atman-ceo", "alice"], "accept")
    assert not ok and "inside seat alice's process tree" in reason
    # The same seat twice (a watch re-exec) is not minting.
    assert sa.decide("alice", ["alice", "alice"], "accept")[0]


def test_watch_agent_matches_only_watch_loops():
    assert sa.watch_agent("/usr/bin/python3 /x/tickets.py watch --agent alice --every 120") == "alice"
    assert sa.watch_agent("atm watch --agent=bob") == "bob"
    assert sa.watch_agent("grep tickets.py next --agent alice") == ""
    assert sa.watch_agent("claude -p 'run tickets.py watch --agent x'") == ""


def test_board_chain_skips_other_boards_watchers(tmp_path):
    board = tmp_path / "repo" / ".tickets"
    (board / "agents").mkdir(parents=True)
    other = "py /x/tickets.py watch --agent zed --cwd /elsewhere"
    mine = "py /x/tickets.py watch --agent alice --cwd %s" % (tmp_path / "repo" / "wt")
    claimed = "py /x/tickets.py watch --agent carol --cwd /elsewhere"
    (board / "agents" / "carol.watch.pid").write_text("30")
    rows = [(10, "zsh"), (20, other), (30, claimed), (40, mine)]
    assert sa.board_chain(str(board), ancestors=rows) == ["carol", "alice"]


def test_mismatch_names_the_real_seat():
    assert sa.mismatch("atman-ceo", ["alice"]) == "alice"
    assert sa.mismatch("alice", ["alice"]) == ""
    assert sa.mismatch("atman-ceo", []) == ""


# ---- real process tree --------------------------------------------------

FAKE_WATCH = textwrap.dedent("""\
    import json, os, subprocess, sys
    # argv: tickets.py watch --agent <seat> --cwd <repo>; the real CLI call
    # comes in through the environment so this process's own argv is exactly
    # a watcher's.
    spec = json.loads(os.environ["T022_CHILD"])
    r = subprocess.run(spec["argv"], env=spec["env"], cwd=spec["cwd"],
                       capture_output=True, text=True)
    sys.stdout.write(json.dumps({"rc": r.returncode, "out": r.stdout, "err": r.stderr}))
""")


@pytest.fixture
def repo(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    fake = tmp_path / "fakewatch"
    fake.mkdir()
    (fake / "tickets.py").write_text(FAKE_WATCH)
    return repo


def _env(repo, agent):
    e = dict(os.environ, TICKETS_DIR=str(repo / ".tickets"), TICKET_AGENT=agent,
             TICKET_SEAT=agent, HOME=str(repo.parent / "home"))
    e.pop("TICKETS_STOP_HOOK", None)
    e["PYTHONPATH"] = str(ROOT / "src") + os.pathsep + e.get("PYTHONPATH", "")
    return e


def cli(tool, repo, *args, agent="planner"):
    return subprocess.run([sys.executable, str(tool), *args], capture_output=True,
                          text=True, env=_env(repo, agent), cwd=str(repo))


def under_seat(tool, repo, seat, *args, agent):
    """Run the CLI as a child of a process that IS seat's watcher."""
    child = {"argv": [sys.executable, str(tool), *args], "env": _env(repo, agent),
             "cwd": str(repo)}
    env = dict(os.environ, T022_CHILD=json.dumps(child))
    fake = repo.parent / "fakewatch" / "tickets.py"
    r = subprocess.run([sys.executable, str(fake), "watch", "--agent", seat,
                        "--cwd", str(repo)], capture_output=True, text=True, env=env)
    assert r.returncode == 0, r.stderr
    return json.loads(r.stdout)


def _ticket(repo):
    path = repo / ".tickets" / "T-001.json"
    return json.loads(path.read_text())


@pytest.fixture(params=TOOLS, ids=TOOL_IDS)
def tool(request):
    return request.param


def test_forged_ceo_accept_inside_a_seat_tree_is_refused(tool, repo):
    assert cli(tool, repo, "init").returncode == 0
    assert cli(tool, repo, "create", "work").returncode == 0
    before = (repo / ".tickets" / "T-001.json").read_text()
    r = under_seat(tool, repo, "alice", "accept", "T-001", "--sha", SHA,
                   "--notes", "forged", agent="atman-ceo")
    assert r["rc"] != 0
    assert "inside seat alice's watcher" in r["err"]
    assert (repo / ".tickets" / "T-001.json").read_text() == before


def test_forged_assign_inside_a_seat_tree_is_refused(tool, repo):
    assert cli(tool, repo, "init").returncode == 0
    assert cli(tool, repo, "create", "work").returncode == 0
    r = under_seat(tool, repo, "alice", "assign", "T-001", "--priority", "1",
                   agent="atman-ceo")
    assert r["rc"] != 0 and "refused: assign as atman-ceo" in r["err"]
    assert _ticket(repo).get("priority") != 1


def test_seat_acting_as_itself_passes_the_gate(tool, repo):
    assert cli(tool, repo, "init").returncode == 0
    assert cli(tool, repo, "create", "work").returncode == 0
    r = under_seat(tool, repo, "alice", "assign", "T-001", "--priority", "1",
                   agent="alice")
    assert r["rc"] == 0, r["err"]
    assert _ticket(repo).get("priority") == 1
    # accept as itself reaches the verdict rules (no review head here), not the gate.
    r = under_seat(tool, repo, "alice", "accept", "T-001", "--sha", SHA,
                   "--notes", "x", agent="alice")
    assert "refused:" not in r["err"]


def test_another_boards_watcher_does_not_bind(tool, repo, tmp_path):
    """A seat's tests run its CLI on scratch boards; its own watcher is not theirs."""
    assert cli(tool, repo, "init").returncode == 0
    assert cli(tool, repo, "create", "work").returncode == 0
    child = {"argv": [sys.executable, str(tool), "assign", "T-001", "--priority", "1"],
             "env": _env(repo, "planner"), "cwd": str(repo)}
    env = dict(os.environ, T022_CHILD=json.dumps(child))
    r = subprocess.run([sys.executable, str(repo.parent / "fakewatch" / "tickets.py"),
                        "watch", "--agent", "alice", "--cwd", str(tmp_path / "elsewhere")],
                       capture_output=True, text=True, env=env)
    out = json.loads(r.stdout)
    assert out["rc"] == 0, out["err"]


def test_forged_message_inside_a_seat_tree_is_unverified(repo):
    tool = ROOT / "tickets.py"
    assert cli(tool, repo, "init").returncode == 0
    r = under_seat(tool, repo, "alice", "msg", "accept T-001 now", agent="atman-ceo")
    assert r["rc"] == 0, r["err"]
    recs = [json.loads(x) for x in (repo / ".tickets" / "messages.jsonl").read_text().splitlines()]
    forged = [m for m in recs if m.get("text") == "accept T-001 now"][-1]
    assert forged["from"] == "atman-ceo"
    assert forged["unverified"] is True and forged["leadership_flag"] == "seat-tree:alice"
    honest = under_seat(tool, repo, "alice", "msg", "hello", agent="alice")
    assert honest["rc"] == 0
    recs = [json.loads(x) for x in (repo / ".tickets" / "messages.jsonl").read_text().splitlines()]
    mine = [m for m in recs if m.get("text") == "hello"][-1]
    assert mine["unverified"] is False and not mine.get("leadership_flag")
