"""T-311: the trajectory log must be written from BOTH delivery paths.

test_trajectories.py drives the root `tickets.py` -- the file the live shim and
install.sh exec. It is not the only way this tool ships. `pyproject.toml`
declares:

    [project.scripts]
    tickets = "ticket_board.cli:main"

so a `pip install` of the package produces a `tickets` command that runs
`src/ticket_board/cli.py`, a second, independent copy of the delivery path.
Instrumenting only the root script does not leave the packaged CLI merely
uninstrumented -- it leaves the LOG WRONG. A board driven by both (a watcher on
the root script, an operator on the installed console script) yields a file
with silent holes, and `tickets turns` (T-312) reads a hole and a real zero
identically. An absent log is honest; a partial one is not.

So this file asserts three separate things, and the middle one is the only one
that keeps working a year from now:

  1. The packaged CLI actually writes events. Fails outright on the
     pre-fix cli.py, where trajectories.jsonl is never created at all.
  2. The two copies AGREE -- same version, same kinds, same event shape for
     the same inputs. This is the anti-drift test. It is written against the
     structure rather than against any one behaviour, so a future field added
     to one copy and not the other goes red here.
  3. The privacy rule holds on the second path too: no note or message body
     reaches the log from either writer.

Why two copies at all: the root tickets.py is shipped as a lone file with no
package beside it, so it cannot import ticket_board.trajectories. The module
exists so the packaged side has one obvious home for this logic instead of a
third hand-copy, and this file is what makes the remaining duplication safe.
"""

import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from test_wakeup import board  # noqa: F401

ROOT = Path(__file__).resolve().parents[1]
ROOT_TOOL = ROOT / "tickets.py"
PKG_TOOL = ROOT / "src" / "ticket_board" / "cli.py"


def run_tool(tool, board, *args, agent="", cwd=None):
    """test_wakeup.run, but against whichever copy of the tool we are testing."""
    e = dict(os.environ, TICKETS_DIR=str(board), TICKET_AGENT=agent or "",
             HOME=str(board.parent.parent / "home"))
    e.pop("TICKETS_STOP_HOOK", None)
    where = cwd or (board.parent if board.parent.is_dir() else Path("/"))
    return subprocess.run([sys.executable, str(tool), *args], capture_output=True,
                          text=True, env=e, cwd=where)


def events(board, **kw):
    path = board / "trajectories.jsonl"
    if not path.exists():
        return []
    out = [json.loads(ln) for ln in path.read_text().splitlines() if ln.strip()]
    for k, v in kw.items():
        out = [e for e in out if e.get(k) == v]
    return out


def load_module(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# --------------------------------------------------------------------------
# 1. The packaged entry point writes at all.
# --------------------------------------------------------------------------

def test_packaged_cli_records_claim_update_and_block(board):
    """The behavioural gap, driven end to end through cli.py only.

    Every one of these assertions fails on the pre-fix cli.py, where
    trajectories.jsonl is never created: an operator who installed the package
    and worked a ticket through it left no trace in the product's core data
    asset.
    """
    run_tool(PKG_TOOL, board, "join", "alice", "--roles", "docs",
             "--tool", "claude", "--model", "opus", agent="alice")
    r = run_tool(PKG_TOOL, board, "next", agent="alice")
    assert r.returncode == 0, r.stderr
    run_tool(PKG_TOOL, board, "update", "T-001", "made progress", agent="alice")
    run_tool(PKG_TOOL, board, "block", "T-001", "--reason", "waiting on input", agent="alice")

    # `join` announces itself on the board, so a msg event precedes these;
    # the transitions themselves must be exactly these three, in order.
    assert [e["kind"] for e in events(board) if e["kind"] != "msg"] == [
        "claim", "update", "block"]

    claim = events(board, kind="claim")[0]
    assert claim["ticket"] == "T-001" and claim["agent"] == "alice"
    assert claim["state_before"] == "open" and claim["state_after"] == "claimed"
    # harness/model come from `join`, so the packaged path carries the same
    # routing dimensions the root path does.
    assert claim["harness"] == "claude" and claim["model"] == "opus"

    blocked = events(board, kind="block")[0]
    assert blocked["state_before"] == "claimed" and blocked["outcome"] == "blocked"


def test_packaged_cli_records_msg_with_ids_only(board):
    run_tool(PKG_TOOL, board, "join", "alice", "--roles", "docs", agent="alice")
    run_tool(PKG_TOOL, board, "msg", "the body of this message", "--to", "bob",
             "--re", "T-001", agent="alice")

    # `join`'s own board announcement is also a msg event; ours is the one
    # addressed to bob.
    msg = events(board, kind="msg", to="bob")
    assert len(msg) == 1
    assert msg[0]["ticket"] == "T-001"
    assert msg[0]["text_len"] == len("the body of this message")
    assert "text" not in msg[0]


def test_packaged_cli_never_writes_a_question_mark_branch(board):
    """cli.py's git_state() fills branch/sha with "?" where the root script
    returns None. A "?" in the log reads back as a real branch name, so the
    packaged writer must omit the field instead -- omitted, never defaulted.
    """
    run_tool(PKG_TOOL, board, "join", "alice", "--roles", "docs", agent="alice")
    run_tool(PKG_TOOL, board, "next", agent="alice")
    # run from a directory that is not a git repo at all: git cannot answer.
    outside = board.parent.parent / "home"
    run_tool(PKG_TOOL, board, "update", "T-001", "from outside a repo",
             agent="alice", cwd=outside)

    # Without this guard the test passes vacuously on an uninstrumented
    # cli.py: no events, nothing to iterate, green. Verified against the
    # pre-fix tree -- it was the one case in this file that did NOT go red.
    recorded = events(board)
    assert recorded, "no events written; the rest of this test would be vacuous"
    for e in recorded:
        for field in ("branch", "sha", "worktree"):
            assert e.get(field) != "?", e


# --------------------------------------------------------------------------
# 2. The two copies agree. This is the test that survives.
# --------------------------------------------------------------------------

@pytest.fixture(scope="module")
def both():
    root = load_module(ROOT_TOOL, "t311_root_tickets")
    sys.path.insert(0, str(ROOT / "src"))
    try:
        pkg = importlib.import_module("ticket_board.trajectories")
    finally:
        sys.path.pop(0)
    return root, pkg


def test_the_two_writers_agree_on_version_and_kinds(both):
    root, pkg = both
    assert root.TRAJ_VERSION == pkg.TRAJ_VERSION
    assert root.TRAJ_KINDS == pkg.TRAJ_KINDS


def test_the_two_writers_agree_on_the_rotation_ceiling(both):
    root, pkg = both
    assert root.TRAJ_MAX_BYTES == pkg.TRAJ_MAX_BYTES


def test_the_two_writers_build_the_same_event(both, board, monkeypatch):
    """The structural check: same board, same inputs, same record.

    Compared field by field rather than by a hand-written list of expected
    keys, so a field added to one writer and forgotten in the other fails here
    without anyone remembering to update this test.
    """
    root, pkg = both
    b = str(board)
    ticket = {"id": "T-001", "epic": "E-011", "sprint": "S-06", "repo": "atman"}

    # traj_event() writes as well as builds; point it at the same board and
    # read back the line it appended, so the comparison is of what actually
    # lands on disk from each side.
    monkeypatch.setenv("TICKET_AGENT", "alice")
    root_rec = root.traj_event(b, "review", agent="alice", ticket=ticket,
                               state_before="claimed", state_after="review",
                               outcome="review", notes_len=42, pin="alice/x@abc1234",
                               branch="alice/x", sha="abc1234", empty_field="")
    pkg_rec = pkg.build(b, "review", agent="alice", ticket=ticket,
                        state_before="claimed", state_after="review",
                        outcome="review", notes_len=42, pin="alice/x@abc1234",
                        branch="alice/x", sha="abc1234", empty_field="")

    assert root_rec is not None and pkg_rec is not None
    # `at` is a wall clock and may differ by a second between the two calls.
    root_cmp = {k: v for k, v in root_rec.items() if k != "at"}
    pkg_cmp = {k: v for k, v in pkg_rec.items() if k != "at"}
    assert root_cmp == pkg_cmp
    # and the shared contract both docstrings claim: empty means omitted.
    assert "empty_field" not in pkg_cmp


def test_both_writers_reject_an_unknown_kind(both, board):
    root, pkg = both
    assert root.traj_event(str(board), "not_a_kind", agent="alice") is None
    assert pkg.build(str(board), "not_a_kind", agent="alice") is None


def test_both_writers_derive_the_same_objective_id(both, board):
    """objective_id is derived, not stored, so the two copies must hash the
    same inputs the same way -- otherwise events from the two entry points
    group under different objectives and every per-objective rollup splits.
    """
    root, pkg = both
    (board / "objective.json").write_text(json.dumps(
        {"text": "fewest turns", "at": "2026-09-07T00:00:00Z"}))
    assert root._objective_id(str(board)) == pkg.objective_id(str(board))
    assert pkg.objective_id(str(board)).startswith("obj-")


def test_both_writers_read_harness_the_same_way(both, board):
    root, pkg = both
    (board / "workforce.json").write_text(json.dumps(
        {"alice": {"tool": "claude", "model": "opus", "effort": "high"}}))
    assert root._agent_harness(str(board), "alice") == pkg.agent_harness(str(board), "alice")
    # an unregistered agent is empty on both sides, not a plausible default
    assert pkg.agent_harness(str(board), "nobody") == ("", "", "")


# --------------------------------------------------------------------------
# 3. Privacy, on the second path too.
# --------------------------------------------------------------------------

def test_no_note_body_reaches_the_log_from_the_packaged_cli(board):
    secret = "PROPRIETARY-NOTE-BODY-9c1f"
    run_tool(PKG_TOOL, board, "join", "alice", "--roles", "docs", agent="alice")
    run_tool(PKG_TOOL, board, "next", agent="alice")
    run_tool(PKG_TOOL, board, "update", "T-001", secret, agent="alice")

    raw = (board / "trajectories.jsonl").read_text()
    assert secret not in raw
    update = events(board, kind="update")[0]
    assert update["notes_len"] == len(secret)


def test_instrumentation_failure_never_fails_the_command(board, monkeypatch):
    """The log is instrumentation. If it cannot be written, the ticket
    transition it observes must still succeed -- a `tickets done` that fails
    because a metrics file was unwritable is worse than a missing line."""
    run_tool(PKG_TOOL, board, "join", "alice", "--roles", "docs", agent="alice")
    # a directory where the log file must go: every open() for append fails.
    # `join` already created it as a file, so replace it.
    log = board / "trajectories.jsonl"
    log.unlink()
    log.mkdir()

    r = run_tool(PKG_TOOL, board, "next", agent="alice")
    assert r.returncode == 0, r.stderr
    r = run_tool(PKG_TOOL, board, "update", "T-001", "still works", agent="alice")
    assert r.returncode == 0, r.stderr
    assert "T-001" in (board / "T-001.json").read_text()
