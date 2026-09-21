"""T-1112: remaining or-claude defaults outside the UI.

Display sites show unknown. Launch sites that still need a provider say
claude (default) in their output. The usage ledger still keys off that
launch default, so a bare join keeps its USAGE line.
"""
import importlib.util
import json
from pathlib import Path

from test_wakeup import board, run  # noqa: F401
from ticket_board import provider_usage as pu
from ticket_board import seat_brief


def _tickets_mod():
    spec = importlib.util.spec_from_file_location(
        "tickets_t1112", Path(__file__).resolve().parents[1] / "tickets.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_who_does_not_invent_claude_for_a_bare_seat(board):
    run(board, "join", "bare", agent="bare")
    run(board, "join", "coded", "--harness", "codex", agent="coded")
    who = run(board, "who")
    assert who.returncode == 0, who.stderr
    assert "provider=unknown" in who.stdout
    assert "provider=codex" in who.stdout
    # A bare seat must not be reported as claude.
    lines = [ln for ln in who.stdout.splitlines() if "lifecycle=" in ln]
    bare = next(ln for ln in lines if "provider=unknown" in ln)
    assert "provider=claude" not in bare


def test_spawn_and_harness_list_show_unknown(board):
    run(board, "join", "bare", agent="bare")
    listed = run(board, "spawn", "--list")
    assert listed.returncode == 0, listed.stderr
    assert "unknown" in listed.stdout
    harness = run(board, "harness", "list")
    assert harness.returncode == 0, harness.stderr
    assert "unknown" in harness.stdout


def test_self_does_not_probe_claude_for_unknown_harness(board):
    run(board, "join", "bare", agent="bare")
    self = run(board, "self", agent="bare")
    assert self.returncode == 0, self.stderr
    assert "no harness recorded (unknown)" in self.stdout
    assert "will not probe claude" in self.stdout


def test_join_persistent_without_harness_names_the_claude_default(board):
    r = run(board, "join", "bare", "--persistent", agent="bare")
    assert r.returncode == 0, r.stderr
    assert "claude (default)" in r.stdout
    assert "pass --harness" in r.stdout


def test_harness_check_names_the_claude_default(board):
    run(board, "join", "bare", agent="bare")
    r = run(board, "harness", "check", "bare", "--timeout", "2", agent="bare")
    # Probe may fail (no claude binary); the label must still say default.
    assert "harness=claude (default)" in r.stdout


def test_seat_brief_says_unknown_when_no_harness_recorded():
    text = seat_brief.compose("alice", roles="backend")
    assert "harness unknown" in text
    assert "harness claude" not in text


def test_seat_brief_text_uses_recorded_harness(board):
    mod = _tickets_mod()
    run(board, "join", "bare", agent="bare")
    run(board, "join", "coded", "--harness", "codex", agent="coded")
    assert "harness unknown" in mod.seat_brief_text(str(board), "bare")
    assert "harness codex" in mod.seat_brief_text(str(board), "coded")


def test_bare_seat_brief_keeps_unknown_and_claude_usage(board):
    mod = _tickets_mod()
    run(board, "join", "bare", agent="bare")
    rec = pu.parse_claude_oauth_usage(json.dumps({
        "five_hour": {"used_percent": 20, "resets_at": "2026-09-16T18:00:00Z"},
    }), checked_at="2026-09-16T10:00:00Z")
    pu.put_reading(str(board), rec)
    text = mod.seat_brief_text(str(board), "bare")
    assert "harness unknown" in text
    assert "harness claude" not in text
    assert "claude ok" in text
    assert "remaining 80%" in text
