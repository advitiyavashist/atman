"""T-1041: skip LIMITED seats; prefer headroom then cheaper --cost; hold all-limited."""
import json
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

from ticket_board import route_headroom as rh
from test_wakeup import board  # noqa: F401

ROOT = Path(__file__).resolve().parents[1]
TOOLS = [ROOT / "tickets.py", ROOT / "src" / "ticket_board" / "cli.py"]
TOOL_IDS = ["tickets.py", "cli.py"]
RESET = "2099-09-20T11:00:00Z"
RESET2 = "2099-09-21T00:00:00Z"


def run(tool, board, *args, agent="", env=None):
    e = dict(os.environ, TICKETS_DIR=str(board), TICKET_AGENT=agent or "",
             HOME=str(board.parent.parent / "home"),
             TICKETS_DISPATCH_NO_SPAWN="1",
             PYTHONPATH=str(ROOT / "src") + os.pathsep + os.environ.get("PYTHONPATH", ""))
    e.pop("TICKETS_STOP_HOOK", None)
    if env:
        e.update(env)
    return subprocess.run([sys.executable, str(tool), *args], capture_output=True,
                          text=True, env=e, cwd=str(board.parent))


def show(tool, board, tid, agent="alice"):
    r = run(tool, board, "show", tid, "--json", agent=agent)
    assert r.returncode == 0, r.stderr + r.stdout
    return json.loads(r.stdout)


def _stamp_seen(board, name):
    path = board / "agents" / ("%s.json" % name)
    rec = json.loads(path.read_text()) if path.exists() else {"owner": name}
    rec["seen"] = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    path.write_text(json.dumps(rec, indent=2))


def _limit(board, name, harness="claude", reset=RESET):
    path = board / "agents" / ("%s.json" % name)
    rec = json.loads(path.read_text()) if path.exists() else {"owner": name}
    rec["limit"] = {
        "at": "2026-09-16T02:00:00Z",
        "reset_at": reset,
        "until": "Sep 20",
        "note": "session limit",
        "harness": harness,
        "source": "provider",
    }
    rec["seen"] = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    path.parent.mkdir(exist_ok=True)
    path.write_text(json.dumps(rec, indent=2))


def _pair(tool, board):
    assert run(tool, board, "join", "alice", "--roles", "docs",
               "--cost", "high", "--harness", "claude", agent="alice").returncode == 0
    assert run(tool, board, "join", "bob", "--roles", "docs",
               "--cost", "low", "--harness", "codex", agent="bob").returncode == 0
    _stamp_seen(board, "alice")
    _stamp_seen(board, "bob")


def _write_ledger(board, providers):
    (board / "provider_usage.json").write_text(json.dumps({
        "providers": providers,
        "updated": "2026-09-16T12:00:00Z",
    }, indent=2))


def test_pick_seat_skips_limited_and_prefers_cheaper():
    limited, cheap, dear = (
        {"name": "lim", "limited": True, "remaining": 90, "cost": 0, "score": 99,
         "limit_label": "claude limited until 2099-09-20T11:00:00Z"},
        {"name": "cheap", "limited": False, "remaining": None, "cost": 0, "score": 10},
        {"name": "dear", "limited": False, "remaining": None, "cost": 2, "score": 10},
    )
    name, why = rh.pick_seat([limited, cheap, dear])
    assert name == "cheap"
    assert "skipped limited: lim" in why
    assert "claude limited until 2099-09-20T11:00:00Z" in why


def test_pick_seat_prefers_observed_headroom_then_cost():
    low_empty = {"name": "low", "limited": False, "remaining": None, "cost": 0, "score": 10}
    mid_room = {"name": "mid", "limited": False, "remaining": 40, "cost": 1, "score": 10}
    name, why = rh.pick_seat([low_empty, mid_room])
    assert name == "mid"
    assert "headroom 40%" in why


def test_seat_headroom_reads_ledger_unknown_stays_none(tmp_path):
    board = tmp_path / "board"
    board.mkdir()
    _write_ledger(board, {"codex": {"remaining": "0%"}})
    assert rh.seat_headroom(board, {"harness": "codex"}) == 0
    assert rh.seat_headroom(board, {"harness": "claude"}) is None
    assert rh.seat_headroom(board, {"harness": "codex", "tool": "ignored"}) == 0


def test_unknown_headroom_never_ranks_below_known_zero():
    """Regression: old rank_key treated remaining=None as -1, worse than 0."""
    confirmed_zero = {"name": "confirmed_zero_pct", "limited": False,
                      "remaining": 0, "cost": 1, "score": 10}
    unknown = {"name": "unknown_headroom", "limited": False,
               "remaining": None, "cost": 1, "score": 10}
    name, why = rh.pick_seat([confirmed_zero, unknown])
    assert name == "unknown_headroom"
    assert "headroom unknown" in why
    assert rh.rank_key(False, None, 1, 10) < rh.rank_key(False, 0, 1, 10)
    assert rh.rank_key(False, 40, 1, 10) < rh.rank_key(False, None, 1, 10)


def test_limited_never_selected_even_with_headroom():
    limited = {"name": "lim", "limited": True, "remaining": 100, "cost": 0, "score": 99,
               "limit_label": "claude limited until 2099-09-20T11:00:00Z"}
    unknown = {"name": "unknown", "limited": False, "remaining": None, "cost": 2, "score": 1}
    zero = {"name": "zero", "limited": False, "remaining": 0, "cost": 0, "score": 1}
    name, why = rh.pick_seat([limited, zero, unknown])
    assert name == "unknown"
    assert name != "lim"
    assert "skipped limited: lim" in why
    only_limited, hold = rh.pick_seat([limited])
    assert only_limited is None
    assert hold.startswith("HOLD:")


def test_pick_seat_all_limited_names_resets():
    rows = [
        {"name": "alice", "limited": True, "remaining": None, "cost": 0, "score": 10,
         "limit_label": "claude limited until 2099-09-20T11:00:00Z"},
        {"name": "bob", "limited": True, "remaining": None, "cost": 1, "score": 10,
         "limit_label": "codex limited until 2099-09-21T00:00:00Z"},
    ]
    name, why = rh.pick_seat(rows)
    assert name is None
    assert why.startswith("HOLD:")
    assert "alice (claude limited until 2099-09-20T11:00:00Z)" in why
    assert "bob (codex limited until 2099-09-21T00:00:00Z)" in why


@pytest.mark.parametrize("tool", TOOLS, ids=TOOL_IDS)
def test_route_never_selects_limited_and_records_reason(tool, board):
    _pair(tool, board)
    _limit(board, "alice", harness="claude")
    routed = run(tool, board, "route", "--only", "alice", "bob", agent="bob")
    assert routed.returncode == 0, routed.stderr + routed.stdout
    t = show(tool, board, "T-001", agent="bob")
    assert t.get("suggested") == "bob"
    assert not t.get("hold")
    notes = " ".join(n.get("text", "") for n in t.get("notes") or [])
    assert "route:" in notes
    assert "skipped limited: alice" in notes
    assert "claude limited until %s" % RESET in notes


@pytest.mark.parametrize("tool", TOOLS, ids=TOOL_IDS)
def test_route_tie_goes_to_cheaper_tier(tool, board):
    _pair(tool, board)
    routed = run(tool, board, "route", "--only", "alice", "bob", agent="bob")
    assert routed.returncode == 0, routed.stderr + routed.stdout
    t = show(tool, board, "T-001", agent="bob")
    assert t.get("suggested") == "bob"
    notes = " ".join(n.get("text", "") for n in t.get("notes") or [])
    assert "cost low" in notes


PAST = "2026-01-01T00:00:00Z"


@pytest.mark.parametrize("tool", TOOLS, ids=TOOL_IDS)
def test_all_limited_reports_resets_without_storing_hold(tool, board):
    _pair(tool, board)
    _limit(board, "alice", harness="claude", reset=RESET)
    _limit(board, "bob", harness="codex", reset=RESET2)
    for extra in ((), ("--redo",)):
        routed = run(tool, board, "route", "--only", "alice", "bob", *extra, agent="bob")
        assert routed.returncode == 0, routed.stderr + routed.stdout
        row = [ln for ln in routed.stdout.splitlines() if ln.startswith("T-001")]
        assert row and "(all limited)" in row[0]
        assert "HOLD: every candidate provider is limited" in row[0]
        assert RESET in row[0] and RESET2 in row[0]
        t = show(tool, board, "T-001", agent="bob")
        assert not t.get("hold")
        assert not t.get("hold_reason")
        assert not t.get("suggested")
        assert not t.get("reserved_for")
        assert not [n for n in t.get("notes") or [] if "HOLD:" in n.get("text", "")]
    _limit(board, "alice", harness="claude", reset=PAST)
    _limit(board, "bob", harness="codex", reset=PAST)
    routed = run(tool, board, "route", "--only", "alice", "bob", agent="bob")
    assert routed.returncode == 0, routed.stderr + routed.stdout
    t = show(tool, board, "T-001", agent="bob")
    assert t.get("suggested") in ("alice", "bob")
    assert "HOLD:" not in routed.stdout


@pytest.mark.parametrize("tool", TOOLS, ids=TOOL_IDS)
def test_next_dispatch_all_limited_reports_then_routes_after_reset(tool, board):
    _pair(tool, board)
    _limit(board, "alice", harness="claude")
    _limit(board, "bob", harness="codex", reset=RESET2)
    r = run(tool, board, "next", "--dispatch", agent="bob")
    assert r.returncode == 0, r.stderr + r.stdout
    assert "HOLD:" in r.stdout and RESET in r.stdout and RESET2 in r.stdout
    t = show(tool, board, "T-001", agent="bob")
    assert not t.get("hold")
    assert t["status"] == "open"
    assert not t.get("reserved_for")
    _limit(board, "alice", harness="claude", reset=PAST)
    _limit(board, "bob", harness="codex", reset=PAST)
    r = run(tool, board, "next", "--dispatch", agent="bob")
    assert r.returncode == 0, r.stderr + r.stdout
    t = show(tool, board, "T-001", agent="bob")
    assert t.get("reserved_for") in ("alice", "bob")


@pytest.mark.parametrize("tool", TOOLS, ids=TOOL_IDS)
def test_next_refuses_limited_seat(tool, board):
    _pair(tool, board)
    _limit(board, "alice", harness="claude")
    denied = run(tool, board, "next", agent="alice")
    assert denied.returncode != 0
    blob = denied.stdout + denied.stderr
    assert "claude limited until %s" % RESET in blob
    assert "not dispatching" in blob
    t = show(tool, board, "T-001", agent="bob")
    assert t["status"] == "open"


def test_dispatch_refuses_limited_target(board):
    tool = TOOLS[0]
    _pair(tool, board)
    _limit(board, "alice", harness="claude")
    r = run(tool, board, "dispatch", "T-001", "--to", "alice", "--harness", "cursor",
            agent="bob")
    assert r.returncode != 0
    blob = r.stdout + r.stderr
    assert "claude limited until %s" % RESET in blob
    t = show(tool, board, "T-001", agent="bob")
    assert not t.get("reserved_for")


def test_spawn_refuses_limited_seat(board):
    tool = TOOLS[0]
    _pair(tool, board)
    _limit(board, "alice", harness="claude")
    r = run(tool, board, "spawn", "alice", agent="alice")
    assert r.returncode != 0
    assert "claude limited until %s" % RESET in (r.stdout + r.stderr)


def test_both_entry_points_pick_same_seat_for_same_ledger(board):
    """Unknown (no claude reading) must beat known-zero codex; both CLIs agree.

    Old rank_key preferred remaining=0 over None. Old cli.py never read the
    ledger, so both seats looked unknown and the cheaper zero-codex seat won.
    """
    _pair(TOOLS[0], board)
    _write_ledger(board, {
        "codex": {"provider": "codex", "remaining": "0%", "status": "exhausted"},
    })
    picks = []
    for i, tool in enumerate(TOOLS):
        extra = ("--redo",) if i else ()
        routed = run(tool, board, "route", "--only", "alice", "bob", *extra, agent="bob")
        assert routed.returncode == 0, routed.stderr + routed.stdout
        t = show(tool, board, "T-001", agent="bob")
        picks.append(t.get("suggested"))
        notes = " ".join(n.get("text", "") for n in t.get("notes") or [])
        assert "headroom unknown" in notes
        assert t.get("suggested") != "bob"
    assert picks[0] == picks[1] == "alice"


@pytest.mark.parametrize("tool", TOOLS, ids=TOOL_IDS)
def test_route_never_selects_limited_when_unknown_is_open(tool, board):
    _pair(tool, board)
    _limit(board, "bob", harness="codex")
    _write_ledger(board, {
        "codex": {"provider": "codex", "remaining": "90%", "status": "ok"},
    })
    routed = run(tool, board, "route", "--only", "alice", "bob", agent="alice")
    assert routed.returncode == 0, routed.stderr + routed.stdout
    t = show(tool, board, "T-001", agent="alice")
    assert t.get("suggested") == "alice"
    assert t.get("suggested") != "bob"
    notes = " ".join(n.get("text", "") for n in t.get("notes") or [])
    assert "skipped limited: bob" in notes


def _stamp_dormant(board, name):
    path = board / "agents" / ("%s.json" % name)
    rec = json.loads(path.read_text()) if path.exists() else {"owner": name}
    rec["seen"] = "2020-01-01T00:00:00Z"
    path.write_text(json.dumps(rec, indent=2))


def _pick(tool, board, verb):
    if verb == "route":
        r = run(tool, board, "route", "--only", "alice", "bob", agent="alice")
    else:
        r = run(tool, board, "next", "--dispatch", agent="alice")
    assert r.returncode == 0, r.stderr + r.stdout
    t = show(tool, board, "T-001", agent="alice")
    return r, t.get("suggested") if verb == "route" else t.get("reserved_for")


VERBS = ["route", "next-dispatch"]


@pytest.mark.parametrize("verb", VERBS)
@pytest.mark.parametrize("tool", TOOLS, ids=TOOL_IDS)
def test_dormant_seat_with_better_headroom_and_cost_never_picked(tool, verb, board):
    """Regression: filter_eligible exclusions must not re-enter the ranking."""
    _pair(tool, board)
    _stamp_dormant(board, "bob")
    _write_ledger(board, {"codex": {"provider": "codex", "remaining": "90%"}})
    r, picked = _pick(tool, board, verb)
    if verb == "route":
        assert "dormant 1" in r.stdout
    assert picked == "alice"


@pytest.mark.parametrize("verb", VERBS)
@pytest.mark.parametrize("tool", TOOLS, ids=TOOL_IDS)
def test_busy_seat_with_better_headroom_and_cost_never_picked(tool, verb, board):
    _pair(tool, board)
    assert run(tool, board, "create", "Other docs", "--role", "docs",
               agent="bob").returncode == 0
    got = run(tool, board, "claim", "T-002", agent="bob")
    assert got.returncode == 0, got.stderr + got.stdout
    _stamp_seen(board, "bob")
    _write_ledger(board, {"codex": {"provider": "codex", "remaining": "90%"}})
    r, picked = _pick(tool, board, verb)
    if verb == "route":
        assert "busy 1" in r.stdout
    assert picked == "alice"


@pytest.mark.parametrize("tool", TOOLS, ids=TOOL_IDS)
def test_route_spreads_two_ready_tickets_over_two_equal_seats(tool, board):
    for name in ("alice", "bob"):
        assert run(tool, board, "join", name, "--roles", "docs", "--cost", "low",
                   "--harness", "codex", agent=name).returncode == 0
        _stamp_seen(board, name)
    assert run(tool, board, "create", "More docs", "--role", "docs",
               agent="alice").returncode == 0
    routed = run(tool, board, "route", "--only", "alice", "bob", agent="alice")
    assert routed.returncode == 0, routed.stderr + routed.stdout
    picks = [show(tool, board, tid).get("suggested") for tid in ("T-001", "T-002")]
    assert sorted(picks) == ["alice", "bob"]


def test_seat_limit_is_shared_and_expires_on_reset():
    now = datetime(2026, 9, 17, tzinfo=timezone.utc)
    live = {"limit": {"at": "2026-09-16T02:00:00Z", "reset_at": RESET}}
    past = {"limit": {"at": "2026-09-16T02:00:00Z", "reset_at": "2026-09-16T03:00:00Z"}}
    assert rh.seat_limit(live, now=now) == live["limit"]
    assert rh.seat_limit(past, now=now) is None
    assert rh.seat_limit({"limit": {}}, now=now) is None
    assert rh.rank_key(False, None, 0, 10, load=0.5) > rh.rank_key(False, None, 2, 1, load=0)


@pytest.mark.parametrize("tool", TOOLS, ids=TOOL_IDS)
def test_route_skips_non_ready_lane_in_both_clis(tool, board):
    _pair(tool, board)
    path = board / "T-001.json"
    rec = json.loads(path.read_text())
    rec["lane"] = "capture"
    path.write_text(json.dumps(rec, indent=2))
    routed = run(tool, board, "route", "--only", "alice", "bob", agent="alice")
    assert routed.returncode == 0, routed.stderr + routed.stdout
    t = show(tool, board, "T-001", agent="alice")
    assert not t.get("suggested")
    assert not t.get("reserved_for")
    assert "T-001" not in routed.stdout


@pytest.mark.parametrize("tool", TOOLS, ids=TOOL_IDS)
def test_busy_seat_plus_limited_dormant_seat_is_not_a_hold(tool, board):
    """Regression: a limited seat excluded for other reasons must not HOLD."""
    _pair(tool, board)
    assert run(tool, board, "create", "Other docs", "--role", "docs",
               agent="alice").returncode == 0
    got = run(tool, board, "claim", "T-002", agent="alice")
    assert got.returncode == 0, got.stderr + got.stdout
    _stamp_seen(board, "alice")
    _limit(board, "bob", harness="codex")
    _stamp_dormant(board, "bob")
    routed = run(tool, board, "route", "--only", "alice", "bob", agent="alice")
    assert routed.returncode == 0, routed.stderr + routed.stdout
    row = [ln for ln in routed.stdout.splitlines() if ln.startswith("T-001")]
    assert row and "(nobody fits)" in row[0]
    assert "HOLD" not in routed.stdout
    t = show(tool, board, "T-001", agent="alice")
    assert not t.get("hold")
    assert not t.get("hold_reason")
    assert not t.get("suggested")
    r = run(tool, board, "next", "--dispatch", agent="alice")
    assert r.returncode == 1, r.stderr + r.stdout
    assert "(nobody fits)" in r.stdout
    t = show(tool, board, "T-001", agent="alice")
    assert not t.get("hold")
    assert not t.get("reserved_for")


def _more_tickets(tool, board, n):
    for i in range(n):
        assert run(tool, board, "create", "More docs %d" % i, "--role", "docs",
                   agent="alice").returncode == 0


@pytest.mark.parametrize("tool", TOOLS, ids=TOOL_IDS)
def test_route_spreads_between_known_and_unknown_headroom_in_one_pass(tool, board):
    _pair(tool, board)
    _write_ledger(board, {"claude": {"provider": "claude", "remaining": "50%"}})
    _more_tickets(tool, board, 3)
    routed = run(tool, board, "route", "--only", "alice", "bob", agent="alice")
    assert routed.returncode == 0, routed.stderr + routed.stdout
    picks = [show(tool, board, "T-00%d" % i).get("suggested") for i in range(1, 5)]
    assert picks.count("alice") == 2, picks
    assert picks.count("bob") == 2, picks


@pytest.mark.parametrize("tool", TOOLS, ids=TOOL_IDS)
def test_next_dispatch_counts_reservations_as_load(tool, board):
    _pair(tool, board)
    _more_tickets(tool, board, 1)
    for _ in range(2):
        r = run(tool, board, "next", "--dispatch", agent="alice")
        assert r.returncode == 0, r.stderr + r.stdout
    picks = [show(tool, board, tid).get("reserved_for") for tid in ("T-001", "T-002")]
    assert sorted(picks) == ["alice", "bob"], picks


def test_rank_key_load_before_known_unknown_split_zero_last():
    known = rh.rank_key(False, 50, 1, 10, load=0.5)
    unknown = rh.rank_key(False, None, 1, 10, load=0)
    zero = rh.rank_key(False, 0, 0, 10, load=0)
    assert unknown < known < zero
