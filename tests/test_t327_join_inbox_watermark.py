"""T-327: a newly joined agent must not inherit the whole broadcast history.

A brand-new agent has no `inbox_seen`, so `unread()` returns every message ever
posted -- measured at 1392 broadcasts on a real seat's first wake. The agent
burns its first turn reading mail addressed to nobody, which is a direct hit on
the fewest-turns objective.

The fix hides pre-join BROADCASTS only. It deliberately does NOT stamp
inbox_seen = now() at join, because that destroys mail two ways this board
would actually feel -- see the docstrings on the two regression tests below.
Both entry points are exercised: the root monolith AND the packaged
ticket_board.cli, because T-228 shipped a fix to one copy of the delivery path
and not the other and no test could see it.
"""

import json
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
TOOL = ROOT / "tickets.py"


def _env(board, agent=""):
    e = dict(os.environ, TICKETS_DIR=str(board), HOME=str(board.parent.parent / "home"))
    e.pop("TICKETS_STOP_HOOK", None)
    # Strip whatever session-id vars this test process itself happens to be
    # sitting in (e.g. CLAUDE_CODE_SESSION_ID from an outer coding-agent
    # session) and give this call its OWN, keyed on the actor it names.
    # Every call in this file already passes `agent=` for exactly the actor
    # it means to be, so that name is the right anchor: without it, every
    # call here would share one identical session key, and a session's
    # RECORDED identity (from `join`) deliberately outranks an explicit
    # per-invocation TICKET_AGENT (see tests/test_identity_precedence.py) --
    # so "join master" then "join old" then "TICKET_AGENT=master tickets
    # msg" would post as "old", the last name that session joined as, not
    # the name this specific call asked for. Same isolation fix as
    # tests/test_wakeup.py's `run()`, for the same reason.
    for var in ("CLAUDE_CODE_SESSION_ID", "CODEX_SESSION_ID", "CURSOR_SESSION_ID", "TERM_SESSION_ID"):
        e.pop(var, None)
    e["TICKET_SESSION_ID"] = "test-session-" + (agent or "__anonymous__")
    return e


def run(board, *args, agent="", entry="root"):
    """Drive the board. entry='root' -> tickets.py; entry='pkg' -> ticket_board.cli."""
    e = _env(board, agent=agent)
    e["TICKET_AGENT"] = agent or ""
    if entry == "pkg":
        # The packaged console script (pyproject: tickets = ticket_board.cli:main).
        # cli.py's main() imports top-level `ticket_coordination`, which lives at
        # the repo root, so the root is on PYTHONPATH alongside src/ -- that is a
        # pre-existing packaging wart on main, not something this ticket changes.
        e["PYTHONPATH"] = os.pathsep.join([str(ROOT / "src"), str(ROOT)])
        cmd = [sys.executable, "-m", "ticket_board", *args]
    else:
        cmd = [sys.executable, str(TOOL), *args]
    where = board.parent if board.parent.is_dir() else Path("/")
    return subprocess.run(cmd, capture_output=True, text=True, env=e, cwd=where)


@pytest.fixture
def board(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    (tmp_path / "home").mkdir()
    b = repo / ".tickets"
    b.mkdir(parents=True)
    return b


def _history(board, n=12, entry="root"):
    """Post n broadcasts addressed to nobody, from an agent that already exists."""
    run(board, "join", "master", "--roles", "backend", agent="master", entry=entry)
    for i in range(n):
        run(board, "msg", "broadcast history line %d" % i, agent="master", entry=entry)


# ---- the defect itself --------------------------------------------------

@pytest.mark.parametrize("entry", ["root", "pkg"])
def test_new_agent_does_not_inherit_the_broadcast_history(board, entry):
    """THE ticket. RED on main: the newcomer's first inbox carries all of it."""
    _history(board, 12, entry=entry)
    time.sleep(1.1)
    run(board, "join", "newbie", "--roles", "backend", agent="newbie", entry=entry)
    out = run(board, "inbox", agent="newbie", entry=entry).stdout
    assert "broadcast history line" not in out, out


# `pending` is a ROOT-ONLY command: the packaged cli.py has no cmd_pending and
# no "pending" subparser at all, so it has no wake path to test. Recorded here
# because it bears on the two-entry-point story -- the copies have diverged in
# COMMAND SURFACE, not just in the body of shared functions.
def test_new_agent_pending_has_no_broadcasts(board):
    """The wake path, not just the display: `pending` must not fire on history."""
    _history(board, 12)
    time.sleep(1.1)
    run(board, "join", "newbie", "--roles", "backend", agent="newbie")
    r = run(board, "pending", "--agent", "newbie", "--json")
    assert json.loads(r.stdout).get("broadcasts") is None, r.stdout


# ---- the two regressions the tempting fix would cause -------------------
# NOTE ON TEETH, stated rather than implied: these two PASS against pre-fix
# main. They are not regression tests for the defect above -- they guard the
# tempting minimal fix (stamp inbox_seen = now() at join), and they go RED on
# an arm built that way. Verified on all three arms; see the ticket notes.

@pytest.mark.parametrize("entry", ["root", "pkg"])
def test_join_does_not_destroy_a_brief_posted_before_the_seat_existed(board, entry):
    """`tickets msg --to newbie` BEFORE the seat joins is how seats get briefed."""
    _history(board, 12, entry=entry)
    run(board, "msg", "your brief: work T-999", "--to", "newbie", agent="master", entry=entry)
    time.sleep(1.1)
    run(board, "join", "newbie", "--roles", "backend", agent="newbie", entry=entry)
    out = run(board, "inbox", agent="newbie", entry=entry).stdout
    assert "your brief" in out, out


@pytest.mark.parametrize("entry", ["root", "pkg"])
def test_rejoin_does_not_wipe_pending_directed_mail(board, entry):
    """`tickets spawn` calls cmd_join, so a RESPAWNED seat re-runs join.

    If join moved the watermark unconditionally, restarting an agent would
    silently drop the master's answer to that agent's own `stuck:` message.
    """
    run(board, "join", "master", "--roles", "backend", agent="master", entry=entry)
    run(board, "join", "worker", "--roles", "backend", agent="worker", entry=entry)
    run(board, "inbox", agent="worker", entry=entry)  # worker drains; watermark set
    time.sleep(1.1)
    run(board, "msg", "STUCK-ANSWER: use the flock", "--to", "worker", agent="master", entry=entry)
    time.sleep(1.1)
    run(board, "join", "worker", "--roles", "backend", agent="worker", entry=entry)  # respawn
    out = run(board, "inbox", agent="worker", entry=entry).stdout
    assert "STUCK-ANSWER" in out, out


# ---- the acceptance case the ticket named --------------------------------

def test_join_then_dm_still_wakes_the_agent(board):
    """The ticket's own named regression watch: join -> DM -> pending fires.

    Root-only for the same reason as above: cli.py ships no `pending`.
    """
    _history(board, 12)
    time.sleep(1.1)
    run(board, "join", "newbie", "--roles", "backend", agent="newbie")
    run(board, "msg", "please look at this", "--to", "newbie", agent="master")
    r = run(board, "pending", "--agent", "newbie", "--json")
    p = json.loads(r.stdout)
    assert p.get("messages_to_me"), r.stdout
    assert "please look at this" in " ".join(p["messages_to_me"])
    assert p.get("pending") is False, "ordinary DMs are notification-only after T-611"


def test_pending_counts_post_join_broadcast_without_sleep(board):
    """CI: test_wakeup::test_pending_direct_message_and_broadcast_only.

    Join then immediately broadcast -- same ISO second as joined_at. Strict `>`
    hid that post-join line. Only broadcasts strictly before joined_at are
    history.
    """
    run(board, "join", "bob", "--roles", "backend", agent="bob")
    run(board, "msg", "hello everyone", agent="master")
    r = run(board, "pending", "--agent", "bob", "--json")
    p = json.loads(r.stdout)
    assert p.get("broadcasts") == 1, r.stdout


@pytest.mark.parametrize("entry", ["root", "pkg"])
def test_broadcasts_after_join_are_delivered(board, entry):
    """Suppression is scoped to history: ordinary broadcasts must still arrive.

    No sleep after join: now() is second-precision, and a same-second
    announcement must still be delivered (wakeup pending: broadcasts==1).
    """
    _history(board, 12, entry=entry)
    time.sleep(1.1)
    run(board, "join", "newbie", "--roles", "backend", agent="newbie", entry=entry)
    run(board, "msg", "AFTER-THE-JOIN announcement", agent="master", entry=entry)
    out = run(board, "inbox", agent="newbie", entry=entry).stdout
    assert "AFTER-THE-JOIN" in out, out


# ---- properties the fix must not break -----------------------------------

@pytest.mark.parametrize("entry", ["root", "pkg"])
def test_history_is_hidden_not_deleted(board, entry):
    """`inbox --all` is the deliberate way to read history; it must still work."""
    _history(board, 12, entry=entry)
    time.sleep(1.1)
    run(board, "join", "newbie", "--roles", "backend", agent="newbie", entry=entry)
    out = run(board, "inbox", "--all", agent="newbie", entry=entry).stdout
    assert out.count("broadcast history line") == 12, out


def test_existing_agents_are_not_stamped_and_keep_their_backlog(board):
    """An agent that already has a record must not acquire joined_at on re-join."""
    run(board, "join", "master", "--roles", "backend", agent="master")
    run(board, "join", "old", "--roles", "backend", agent="old")
    rec = json.loads((board / "agents" / "old.json").read_text())
    first = rec.get("joined_at")
    assert first, "a new agent should be stamped"
    time.sleep(1.1)
    run(board, "msg", "later broadcast", agent="master")
    run(board, "join", "old", "--roles", "backend", agent="old")  # re-join
    rec2 = json.loads((board / "agents" / "old.json").read_text())
    assert rec2.get("joined_at") == first, "re-join must not move the watermark"
    assert "later broadcast" in run(board, "inbox", agent="old").stdout


def test_existing_peer_inbox_seen_untouched_when_newbie_joins(board):
    """Ticket: existing agents' inbox_seen must stay put when someone else joins."""
    run(board, "join", "old", "--roles", "backend", agent="old")
    run(board, "inbox", agent="old")
    rec = json.loads((board / "agents" / "old.json").read_text())
    seen = rec.get("inbox_seen")
    joined = rec.get("joined_at")
    assert seen
    time.sleep(1.1)
    run(board, "join", "newbie", "--roles", "backend", agent="newbie")
    rec2 = json.loads((board / "agents" / "old.json").read_text())
    assert rec2.get("inbox_seen") == seen
    assert rec2.get("joined_at") == joined


def test_agent_predating_the_fix_has_no_joined_at_and_sees_everything(board):
    """Upgrade path: a record written before this fix has no joined_at at all.

    That agent must behave exactly as it did on main -- the suppression is
    opt-in by the presence of the field, never retroactive.
    """
    run(board, "join", "master", "--roles", "backend", agent="master")
    run(board, "join", "legacy", "--roles", "backend", agent="legacy")
    p = board / "agents" / "legacy.json"
    rec = json.loads(p.read_text())
    rec.pop("joined_at", None)          # simulate a pre-fix record
    rec.pop("inbox_seen", None)
    p.write_text(json.dumps(rec))
    run(board, "msg", "broadcast for legacy", agent="master")
    assert "broadcast for legacy" in run(board, "inbox", agent="legacy").stdout
