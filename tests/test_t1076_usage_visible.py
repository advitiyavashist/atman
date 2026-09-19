"""T-1076: provider usage where users already look.

Four surfaces, one reader: the default `atm` screen and `atm next`, the
`atm agents` header, the local UI / board.json Team header, and the line a
seat gets after `spawn` / `dispatch`. All of it comes off the T-1056 ledger
through `provider_usage.get_reading` -- so every surface is checked in three
states (known, unknown, limited), plus the two states that are easy to get
wrong: an expired limit (must not read as limited) and a board where nothing
has ever been read (must not fabricate a number or raise an alarm).

Read-only is checked the T-1055/T-1072 way: an audit hook for
subprocess/write-mode opens, plus a hash snapshot of the whole board.
"""
from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import subprocess
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from test_wakeup import board, run  # noqa: F401
from ui_server_harness import make_ui_server_fixture

ROOT = Path(__file__).resolve().parents[1]
TOOL = ROOT / "tickets.py"
CLI = ROOT / "src" / "ticket_board" / "cli.py"
sys.path.insert(0, str(ROOT / "src"))
from ticket_board import provider_usage as pu  # noqa: E402

ui_server = make_ui_server_fixture("t1076-probe")
NARROW = 80


# ---- ledger fixtures: fake records only, never a real credential ----------

def iso(delta_h=0.0):
    return (datetime.now(timezone.utc) + timedelta(hours=delta_h)).strftime(
        "%Y-%m-%dT%H:%M:%SZ")


def reading(provider, status, **kw):
    rec = pu.empty_reading(provider, status=status)
    rec.update(kw)
    return rec


def http_reading(provider, remaining_pct, reset_h=2.0, checked_h=-0.05):
    """A successful credentialed read, shaped like parse_*_usage output."""
    reset = iso(reset_h)
    win = {"name": "five_hour", "used_percent": 100.0 - remaining_pct,
           "remaining_percent": float(remaining_pct), "reset_at": reset}
    return reading(provider, "ok", remaining="%g%%" % remaining_pct, reset_at=reset,
                   windows=[win], source="oauth_usage", account_state="ready",
                   checked_at=iso(checked_h))


def signed_out_reading(provider="codex"):
    return pu.empty_credential_reading(provider, iso(-0.05))


def limited_reading(provider="claude", reset_h=2.0, message="5-hour limit reached"):
    return pu.record_observed_limit(provider, message, iso(-0.2), iso(reset_h))


def expired_limit_reading(provider="claude"):
    """A limit whose reset has already passed: honest answer is 'unknown'."""
    return pu.record_observed_limit(provider, "5-hour limit reached",
                                    iso(-4), iso(-1))


def stale_limit_reading(provider="claude", observed_h=-24 * 6, reset_h=None,
                        reset_at=""):
    """The real watch-rejection shape (tickets.py cmd_watch): an observed limit
    with NO provider reset, so nothing can ever expire it. Six days old.
    """
    when = reset_at or (iso(reset_h) if reset_h is not None else "")
    return pu.record_observed_limit(provider, "5-hour limit reached",
                                    iso(observed_h), when)


def seed_ledger(board, **by_provider):
    led = {"updated": iso(), "providers": {}}
    for hid, rec in by_provider.items():
        led["providers"][hid] = dict(rec, provider=rec.get("provider") or hid)
    (Path(board) / "provider_usage.json").write_text(json.dumps(led, indent=2))
    return led


def three_states(board):
    """claude known+low, codex signed out, and a limited seat provider."""
    return seed_ledger(board, claude=http_reading("claude", 12), codex=signed_out_reading())


def usage_lines(text):
    return [ln for ln in text.splitlines() if ln.startswith("usage  ")]


def tk_module(monkeypatch, home):
    monkeypatch.setenv("HOME", str(home))
    spec = importlib.util.spec_from_file_location("tickets_t1076", TOOL)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _mk(path):
    path.mkdir(parents=True, exist_ok=True)
    return path


def tree_hash(root):
    return {str(p): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in sorted(Path(root).rglob("*")) if p.is_file()}


# ---- the copy, one provider at a time ------------------------------------

def test_known_state_names_the_number_and_the_reset():
    line = pu.format_compact_line(http_reading("claude", 88))
    assert line.startswith("claude 88% left, resets ")
    assert "low" not in line and "LIMITED" not in line
    assert pu.compact_reading(http_reading("claude", 88))["level"] == "ok"


def test_low_provider_is_called_out_plainly():
    rec = pu.compact_reading(http_reading("claude", 12))
    assert rec["level"] == "low" and rec["text"].endswith("-- low")
    assert rec["text"].startswith("claude 12% left, resets ")
    # The threshold is a boundary, not a vibe: 20% is not yet low.
    assert pu.compact_reading(http_reading("claude", 20))["level"] == "ok"
    assert pu.compact_reading(http_reading("claude", 19))["level"] == "low"


def test_unknown_read_says_re_login_and_never_a_number():
    line = pu.format_compact_line(signed_out_reading("claude"))
    assert line == "claude unknown -- re-login required"
    assert "%" not in line and "0" not in line
    # A 200 with an unreadable body is also unknown, not zero.
    blank = pu.format_compact_line(reading("claude", "ok", checked_at=iso(-0.1)))
    assert blank == "claude unknown" and "%" not in blank


def test_limited_says_so_with_its_reset_time():
    line = pu.format_compact_line(limited_reading())
    assert line.startswith("claude LIMITED, resets ")
    assert "UTC" in line
    # No provider reset given: say the reset is unknown rather than invent one.
    assert pu.format_compact_line(pu.record_observed_limit(
        "codex", "cap", iso(-0.2))) == "codex LIMITED, reset unknown"


def test_expired_limit_does_not_read_as_limited():
    rec = pu.compact_reading(expired_limit_reading())
    assert rec["level"] == "unknown"
    assert "limit" not in rec["text"].lower()
    assert rec["text"].startswith("claude unknown -- reset elapsed")
    # 4h since the limit was observed: the age rides along (blocker 1).
    assert rec["text"] == "claude unknown -- reset elapsed (read 4h ago)"


def test_a_stale_reading_never_reads_as_current(board, monkeypatch, capsys):
    """REVIEW blocker 1: a reset-less observed limit can never expire, so
    LIMITED -- the word that stops a dispatch -- has to carry its own age.
    Every state does, once the reading is older than STALE_READING_SECS.
    """
    six_days = stale_limit_reading()
    assert six_days["reset_at"] is None  # nothing for expire_stale_resets to do
    assert pu.expire_stale_resets(six_days)["status"] == "limited"
    line = pu.format_compact_line(six_days)
    assert line == "claude LIMITED, reset unknown (read 6d ago)"
    # The ledger view's own staleness wording is still there to agree with.
    assert "last read 6d ago" in pu.format_usage_line(six_days)

    aged = {
        "limited + reset": (stale_limit_reading(reset_h=2), "read 6d ago"),
        "ok": (http_reading("claude", 88, checked_h=-9), "read 9h ago"),
        "low": (http_reading("claude", 12, checked_h=-3), "read 3h ago"),
        "unknown": (reading("claude", "unknown", checked_at=iso(-50),
                            hint="re-login required"), "read 2d ago"),
    }
    for name, (rec, age) in aged.items():
        assert age in pu.format_compact_line(rec), name
    # A fresh reading is not annotated: the age is a warning, not decoration.
    for rec in (limited_reading(), http_reading("claude", 88),
                signed_out_reading("claude")):
        assert "read " not in pu.format_compact_line(rec).replace("re-login", "")
    # A provider with no usage source at all has no age to report.
    assert "old" not in pu.format_compact_line(pu.empty_reading("cursor"))

    # All four surfaces, not just the formatter.
    tk = tk_module(monkeypatch, board.parent.parent / "home")
    seed_ledger(board, claude=six_days)
    run(board, "join", "alice", "--roles", "docs", "--harness", "claude", agent="alice")
    for args in ((), ("agents",), ("next", "--owner", "alice")):
        head = usage_lines(run(board, *args, agent="alice").stdout)
        assert head[0] == "usage  claude LIMITED, reset unknown (read 6d ago)", args
    (row,) = [r for r in tk.provider_usage_snapshot(str(board))
              if r["provider"] == "claude"]
    assert row["text"].endswith("(read 6d ago)") and row["level"] == "limited"
    tk.print_seat_usage(str(board), "claude")
    assert capsys.readouterr().out.strip() == \
        "usage  claude LIMITED, reset unknown (read 6d ago)"


def test_the_age_outlives_the_reset_when_the_line_is_tight(board):
    """REVIEW blocker 2: the reset is shed, never the staleness marker."""
    wordy = "next Tuesday at midnight when the weekly window rolls over"
    rec = reading("claude", "ok", remaining="100%", checked_at=iso(-9),
                  reset_at=wordy,
                  windows=[{"name": "weekly", "used_percent": 0.0,
                            "remaining_percent": 100.0, "reset_at": wordy}])
    line = pu.format_compact_line(rec)
    assert line == "claude 100% left (read 9h ago)"
    assert "next Tuesday" not in line  # shed whole: half a phrase is not a time

    # When both fit, both are shown; when only one can, the age wins and the
    # reset is never half-printed (no bare "09:00" without its UTC).
    tight = stale_limit_reading(reset_at="next Tuesday at midnight")
    assert pu.format_compact_line(tight) == \
        "claude LIMITED, resets next Tuesday at midnight (6d old)"
    far = http_reading("claude", 74, reset_h=20, checked_h=-14)
    far_line = pu.format_compact_line(far)
    assert "read 14h ago" in far_line and far_line.count("UTC") == 1
    for rec in (rec, tight, far):
        shown = pu.format_compact_line(rec)
        assert ("UTC" in shown) == ("UTC" in (pu.reset_label(rec["reset_at"]) or "")
                                    and "resets" in shown)

    # Through a surface, with the age intact.
    seed_ledger(board, claude=reading("claude", "ok", remaining="3%", checked_at=iso(-9),
                                      reset_at=wordy,
                                      windows=[{"name": "w", "used_percent": 97.0,
                                                "remaining_percent": 3.0,
                                                "reset_at": wordy}]))
    head = usage_lines(run(board, "agents").stdout)
    assert head == ["usage  claude 3% left -- low (read 9h ago)"]

    # REVIEW blocker 4: `_reset_of` copies the provider's own resets_at
    # verbatim, so these are live shapes -- and a reset clipped mid-string is
    # a WRONG time that still looks complete: "2026-09-19 09:00:00" without
    # its zone reads as UTC and is 7h out; "...09:00:0" has simply lost a
    # digit. Whole phrase, or no reset at all, in every state.
    live = ["2026-09-19 09:00:00 America/Los_Angeles",
            "Fri, 19 Sep 2026 09:00:00 GMT",
            "09:00 America/Los_Angeles",
            wordy,
            "2026-09-19T09:00:00-07:00"]
    for text in live:
        phrase = pu.reset_label(text)
        for rec in (pu.record_observed_limit("claude", "cap", iso(-0.2), text),
                    stale_limit_reading(reset_at=text),
                    reading("claude", "ok", remaining="88%", checked_at=iso(-0.05),
                            reset_at=text,
                            windows=[{"name": "w", "used_percent": 12.0,
                                      "remaining_percent": 88.0, "reset_at": text}])):
            shown = pu.format_compact_line(rec)
            assert (phrase and phrase in shown) or "resets" not in shown, (text, shown)
            assert "09:00:0 " not in shown and not shown.endswith("09:00:0")
            assert len("usage  " + shown) <= NARROW, shown
    # The zone is part of the time: kept when the phrase is, never trimmed off.
    assert pu.reset_label("09:00 America/Los_Angeles") == "09:00 America/Los_Angeles"
    assert pu.reset_label("2026-09-19 09:00:00 America/Los_Angeles") == \
        "2026-09-19 09:00:00 America/Los_Angeles"
    assert pu.reset_label(wordy) == ""  # too long to print whole -> not printed
    # A raw epoch is a number, not a reset a user can read.
    # 9–13 was the old cap; 14+ (microseconds and up) must also stay silent.
    assert pu.reset_label("1789145400") == "" and pu.reset_label("1789145400000") == ""
    assert pu.reset_label("1789145400000000") == ""
    assert "1789145400" not in pu.format_compact_line(
        pu.record_observed_limit("claude", "cap", iso(-0.2), "1789145400"))
    # And a credentials path in the field is still never printed.
    assert pu.reset_label("reset=/Users/nobody/.claude/.credentials.json") == ""


def test_an_elapsed_reset_is_not_printed_as_if_it_were_ahead():
    """A window that has already rolled over is not a promise about the future."""
    rec = http_reading("claude", 74, reset_h=-3, checked_h=-0.05)
    line = pu.format_compact_line(rec)
    assert "resets" not in line
    # The number is from a rolled-over window: say when it was read, even
    # though the ledger itself was refreshed minutes ago.
    assert line.startswith("claude 74% left (read ") and line.endswith(" ago)")
    assert pu.compact_reading(rec)["reset"] == ""


def test_a_provider_with_no_usage_source_says_so():
    assert pu.format_compact_line(pu.empty_reading("cursor")) == \
        "cursor no usage data (no source to read)"


def test_never_read_is_not_a_failed_read():
    fresh = pu.get_reading("/nonexistent-board-t1076", "claude")
    line = pu.format_compact_line(fresh)
    assert line == "claude unknown -- not read yet (atm harness usage)"
    assert "re-login" not in line and "%" not in line


def test_a_corrupt_ledger_entry_degrades_to_unknown(board):
    """A hand-edited ledger is not a reading. Unknown, not a crash, not a number."""
    (Path(board) / "provider_usage.json").write_text(
        json.dumps({"updated": iso(), "providers": {"claude": "88%", "codex": 7}}))
    assert pu.format_compact_line(pu.get_reading(str(board), "claude")) == \
        "claude unknown -- unreadable record"
    out = run(board, "agents").stdout
    assert "usage  claude unknown -- unreadable record" in out
    assert "88%" not in out
    assert run(board, "agents").returncode == 0

    (Path(board) / "provider_usage.json").write_text("{not json at all")
    head = usage_lines(run(board, "agents").stdout)
    assert head == ["usage  no provider read yet (atm harness usage)"]


def test_an_unrecognised_status_is_never_read_as_available(board):
    seed_ledger(board, claude=reading("claude", "available", checked_at=iso(-0.1),
                                      hint="", remaining=None))
    line = usage_lines(run(board, "agents").stdout)[0]
    assert line == "usage  claude unknown"
    assert "available" not in line


def test_remaining_percent_is_the_worst_window_and_never_invented():
    rec = http_reading("claude", 80)
    rec["windows"].append({"name": "seven_day", "used_percent": 95.0,
                           "remaining_percent": 5.0, "reset_at": iso(40)})
    assert pu.remaining_percent(rec) == 5.0
    assert pu.compact_reading(rec)["level"] == "low"
    assert pu.remaining_percent(reading("claude", "ok")) is None
    assert pu.remaining_percent({"remaining": "not a number"}) is None
    assert pu.remaining_percent({"remaining": "512%"}) is None
    # Floor, so a header never overstates what the provider reported.
    assert pu.pct_label(88.9) == "88%" and pu.pct_label(0.4) == "<1%"


# ---- surface 1: the default `atm` screen and `atm next` -------------------

def test_status_and_next_headers_show_each_state(board):
    run(board, "join", "alice", "--roles", "docs", "--harness", "claude", agent="alice")
    three_states(board)
    bare = run(board)
    assert bare.returncode == 0
    head = usage_lines(bare.stdout)
    assert head and head[0].startswith("usage  claude 12% left, resets ")
    assert head[0].endswith("-- low")
    assert "usage  codex unknown -- re-login required" in head
    # The header is a header: it precedes the output the user came for.
    assert bare.stdout.index("usage  claude") < bare.stdout.index("usage:")

    nxt = run(board, "next", "--owner", "alice", agent="alice")
    assert nxt.returncode == 0, nxt.stderr
    assert usage_lines(nxt.stdout) == head
    assert nxt.stdout.startswith("usage  ")
    assert "T-001" in nxt.stdout

    seed_ledger(board, claude=limited_reading(), codex=signed_out_reading())
    limited = usage_lines(run(board).stdout)
    assert any(ln.startswith("usage  claude LIMITED, resets ") for ln in limited)

    seed_ledger(board, claude=http_reading("claude", 91))
    known = usage_lines(run(board).stdout)
    assert any(ln.startswith("usage  claude 91% left") for ln in known)
    assert not any("low" in ln for ln in known)


def test_next_header_survives_a_refusal(board):
    """The quota is the thing a refused `next` most needs to explain."""
    three_states(board)
    r = run(board, "next", "--owner", "nobody", "--role", "docs", agent="nobody")
    assert usage_lines(r.stdout)


# ---- surface 2: the `atm agents` header ----------------------------------

def test_agents_header_shows_each_state(board):
    three_states(board)
    out = run(board, "agents").stdout
    assert out.splitlines()[0].startswith("usage  claude 12% left")
    assert "usage  codex unknown -- re-login required" in out
    assert "0 agents running" in out  # the map itself is untouched

    seed_ledger(board, claude=limited_reading())
    assert run(board, "agents").stdout.startswith("usage  claude LIMITED, resets ")

    seed_ledger(board, claude=expired_limit_reading())
    expired = run(board, "agents").stdout.splitlines()[0]
    assert expired == "usage  claude unknown -- reset elapsed (read 4h ago)"
    assert "limited" not in expired.lower()


def test_agents_json_keeps_the_t1072_shape(board):
    three_states(board)
    data = json.loads(run(board, "agents", "--json").stdout)
    assert set(data) == {"v", "scope", "running", "runs", "groups"}


# ---- surface 3: the local UI header / board.json -------------------------

def test_ui_header_and_board_json_carry_the_cli_lines(board, ui_server):
    three_states(board)
    page = ui_server.get("/", raw=True).decode()
    assert 'id="providerUsage"' in page
    assert "renderProviderUsage(d.provider_usage)" in page

    rows = ui_server.get()["provider_usage"]
    assert [r["provider"] for r in rows] == ["claude", "codex"]
    assert [r["level"] for r in rows] == ["low", "unknown"]
    assert all(set(r) == {"provider", "level", "remaining_pct", "remaining",
                          "reset", "text"} for r in rows)
    # Same words as the CLI header, from the same reader.
    cli_head = [ln[len("usage  "):] for ln in usage_lines(run(board, "agents").stdout)]
    assert [r["text"] for r in rows] == cli_head
    # The UI is handed the number, never a credential or a limit message.
    blob = json.dumps(rows)
    assert "credentials" not in blob and "Bearer" not in blob


def test_board_json_states_and_fresh_board(board, monkeypatch):
    tk = tk_module(monkeypatch, board.parent.parent / "home")
    run(board, "join", "alice", "--roles", "docs", "--harness", "claude", agent="alice")

    fresh = tk.provider_usage_snapshot(str(board))
    assert [r["level"] for r in fresh] == ["unknown"]
    assert fresh[0]["remaining_pct"] is None and fresh[0]["remaining"] == ""
    assert "not read yet" in fresh[0]["text"]

    seed_ledger(board, claude=http_reading("claude", 88), codex=limited_reading("codex"))
    by = {r["provider"]: r for r in tk.provider_usage_snapshot(str(board))}
    assert by["claude"]["level"] == "ok" and by["claude"]["remaining_pct"] == 88.0
    assert by["codex"]["level"] == "limited" and by["codex"]["reset"].endswith("UTC")

    seed_ledger(board, claude=expired_limit_reading())
    (row,) = [r for r in tk.provider_usage_snapshot(str(board)) if r["provider"] == "claude"]
    assert row["level"] == "unknown" and "limit" not in row["text"].lower()


# ---- surface 4: after spawn / dispatch -----------------------------------

def test_seat_line_after_spawn_and_dispatch(board, monkeypatch, capsys):
    tk = tk_module(monkeypatch, board.parent.parent / "home")
    seed_ledger(board, claude=http_reading("claude", 12), codex=limited_reading("codex"))

    tk.print_seat_usage(str(board), "claude")
    first = capsys.readouterr().out.strip()
    assert first.startswith("usage  claude 12% left, resets ")
    assert first.endswith("-- low") and "UTC" in first

    tk.print_seat_usage(str(board), "codex")
    assert capsys.readouterr().out.startswith("usage  codex LIMITED, resets ")

    # A cursor+claude seat spends Claude's quota; say Claude's number.
    tk.print_seat_usage(str(board), "cursor+claude")
    assert "claude 12% left" in capsys.readouterr().out

    # remote/custom is not a metered provider: not "unread", just no source.
    tk.print_seat_usage(str(board), "remote")
    assert capsys.readouterr().out.strip() == \
        "usage  remote no usage data (no source to read)"

    # Unknown state, and an expired limit that must not read as limited.
    seed_ledger(board, claude=signed_out_reading("claude"),
                codex=expired_limit_reading("codex"))
    tk.print_seat_usage(str(board), "claude")
    assert capsys.readouterr().out.strip() == "usage  claude unknown -- re-login required"
    tk.print_seat_usage(str(board), "codex")
    seat = capsys.readouterr().out.strip()
    assert seat == "usage  codex unknown -- reset elapsed (read 4h ago)"
    assert "limit" not in seat.lower()

    # One line, for the seat's provider only.
    tk.print_seat_usage(str(board), "claude")
    assert len(capsys.readouterr().out.strip().splitlines()) == 1


def _fake_harness_bin(dirpath):
    """Logged-in stubs so dispatch preflight reads no real credential."""
    dirpath.mkdir(parents=True, exist_ok=True)
    for name in ("claude", "codex", "agent"):
        p = dirpath / name
        p.write_text("#!/bin/sh\necho 'Logged in as fixture@example.test'\nexit 0\n")
        p.chmod(0o700)
    return dirpath


def test_dispatch_prints_exactly_one_line_for_the_seat(board):
    bindir = _fake_harness_bin(board.parent.parent / "fake-bin")
    env = {"TICKETS_DISPATCH_NO_SPAWN": "1",
           "PATH": str(bindir) + os.pathsep + os.environ.get("PATH", "")}
    run(board, "join", "alice", "--roles", "docs", "--harness", "claude", agent="alice")
    seed_ledger(board, claude=http_reading("claude", 12))
    r = run(board, "dispatch", "T-001", "--to", "alice", "--harness", "claude",
            agent="cos", env=env)
    assert r.returncode == 0, r.stderr + r.stdout
    head = usage_lines(r.stdout)
    assert len(head) == 1 and head[0].startswith("usage  claude 12% left, resets ")
    assert head[0].endswith("-- low")
    # It lands next to the reservation it is about, not at the top.
    assert r.stdout.index("reserved for alice") < r.stdout.index("usage  ")

    # A limited provider says so on the same surface, with its reset.
    run(board, "create", "More docs", "--role", "docs", agent="cos")
    run(board, "join", "bob", "--roles", "docs", "--harness", "claude", agent="bob")
    seed_ledger(board, claude=limited_reading())
    r2 = run(board, "dispatch", "T-002", "--to", "bob", "--harness", "claude",
             agent="cos", env=env)
    assert r2.returncode == 0, r2.stderr + r2.stdout
    assert usage_lines(r2.stdout) == [
        "usage  " + pu.format_compact_line(pu.get_reading(str(board), "claude"))]
    assert usage_lines(r2.stdout)[0].startswith("usage  claude LIMITED, resets ")


def test_spawn_and_dispatch_are_wired_to_the_seat_line():
    src = TOOL.read_text()
    spawn = src[src.index("def cmd_spawn("):src.index("HARNESS_PROBE_PROMPT")]
    dispatch = src[src.index("def cmd_dispatch("):src.index("def cmd_pr_sync(")]
    assert "print_seat_usage(board, harness)" in spawn
    assert "print_seat_usage(board, harness)" in dispatch
    # A dispatch that goes on to spawn must not say the same thing twice.
    assert 'getattr(a, "usage_line", True)' in spawn
    assert "usage_line=False" in dispatch


# ---- a fresh install: no number, no alarm --------------------------------

def test_fresh_install_says_nothing_scary(board):
    """No ledger, no seat: honest, actionable, and not a single number."""
    assert not (Path(board) / "provider_usage.json").exists()
    for args in ((), ("agents",)):
        out = run(board, *args).stdout
        head = usage_lines(out)
        assert head == ["usage  no provider read yet (atm harness usage)"], args
        assert "%" not in head[0] and "0" not in head[0]
        for word in ("LIMITED", "limited", "low", "exhausted", "re-login", "!!"):
            assert word not in head[0]

    # A registered seat whose provider was never read is "not read yet".
    run(board, "join", "alice", "--roles", "docs", "--harness", "claude", agent="alice")
    head = usage_lines(run(board, "agents").stdout)
    assert head == ["usage  claude unknown -- not read yet (atm harness usage)"]
    assert "%" not in head[0]
    assert not (Path(board) / "provider_usage.json").exists()  # reading wrote nothing


def test_header_can_be_turned_off_for_machine_callers(board):
    three_states(board)
    env = dict(os.environ, TICKETS_USAGE_HEADER="0")
    out = run(board, "agents", env=env).stdout
    assert not usage_lines(out) and out.startswith("0 agents running")


# ---- read-only: no subprocess, no write, on all four read paths ----------

SPAWN_EVENTS = ("subprocess.Popen", "os.system", "os.posix_spawn", "os.exec",
                "os.fork", "os.spawn", "pty.spawn", "socket.connect",
                "urllib.Request")


@pytest.fixture
def audit():
    """Collect spawn/connect events and write-mode opens while armed."""
    seen, armed = [], [False]

    def hook(event, args):
        if not armed[0]:
            return
        if event in SPAWN_EVENTS:
            seen.append((event, args[0] if args else None))
        elif event == "open" and len(args) > 2:
            mode, flags = args[1], args[2] or 0
            if (isinstance(mode, str) and any(c in mode for c in "wax+")) or \
                    flags & (os.O_WRONLY | os.O_RDWR | os.O_CREAT):
                seen.append((event, args[0]))
    sys.addaudithook(hook)

    class Audit:
        def record(self, fn):
            del seen[:]
            armed[0] = True
            try:
                fn()
            finally:
                armed[0] = False
            return list(seen)

    return Audit()


def test_read_only_no_subprocess_no_writes(board, monkeypatch, audit):
    three_states(board)
    run(board, "join", "alice", "--roles", "docs", "--harness", "claude", agent="alice")
    monkeypatch.setenv("TICKETS_DIR", str(board))
    monkeypatch.chdir(board.parent)
    tk = tk_module(monkeypatch, board.parent.parent / "home")
    before = tree_hash(board)

    def read_paths():
        assert tk.usage_header_lines(str(board))
        assert tk.provider_usage_snapshot(str(board))
        assert tk.print_seat_usage(str(board), "claude") is None
        for argv in (["atm", "agents"], ["atm"]):
            monkeypatch.setattr(sys, "argv", argv)
            tk.main()

    assert audit.record(read_paths) == []
    assert tree_hash(board) == before


def test_board_json_usage_adds_no_spawn_and_no_write(board, monkeypatch, audit):
    """board.json already scans `ps` for the watch table; usage adds nothing."""
    three_states(board)
    run(board, "join", "alice", "--roles", "docs", "--harness", "claude", agent="alice")
    monkeypatch.setenv("TICKETS_DIR", str(board))
    monkeypatch.chdir(board.parent)
    tk = tk_module(monkeypatch, board.parent.parent / "home")
    before = tree_hash(board)

    def snap():
        assert tk.board_snapshot(str(board))["provider_usage"]

    def snap_without_usage():
        real = tk.provider_usage_snapshot
        tk.provider_usage_snapshot = lambda b: []
        try:
            assert tk.board_snapshot(str(board))["provider_usage"] == []
        finally:
            tk.provider_usage_snapshot = real

    with_usage = audit.record(snap)
    without = audit.record(snap_without_usage)
    assert sorted(e for e, _ in with_usage) == sorted(e for e, _ in without)
    assert [a for e, a in with_usage if e == "open"] == []
    assert tree_hash(board) == before


def test_no_fresh_read_is_triggered_by_a_read_path(board, monkeypatch):
    """Only the surfaces that already refresh may refresh (T-1040/T-1056)."""
    tk = tk_module(monkeypatch, board.parent.parent / "home")
    three_states(board)
    calls = []
    monkeypatch.setattr(tk, "_maybe_refresh_provider_usage",
                        lambda b: calls.append(b))
    tk.usage_header_lines(str(board))
    tk.provider_usage_snapshot(str(board))
    tk.board_snapshot(str(board))
    tk.print_seat_usage(str(board), "claude")
    assert calls == []
    src = TOOL.read_text()
    for fn in ("def usage_header_lines(", "def provider_usage_snapshot(",
               "def print_seat_usage(", "def _usage_header_board("):
        body = src[src.index(fn):src.index("\n\n\n", src.index(fn))]
        code = "".join(body.split('"""')[::2])  # drop the docstring, keep the code
        for banned in ("refresh_http_providers", "_maybe_refresh_provider_usage(",
                       "fetch_provider_usage", "subprocess.", "put_reading"):
            assert banned not in code, (fn, banned)
    # The bare-`atm` board comes from board_dir itself, so the header can
    # never name a board the next command refuses -- run with the no-spawn
    # flag set, which makes its helpers take the filesystem answer instead of
    # shelling out to git.
    resolver = src[src.index("def _usage_header_board("):
                   src.index("\n\n\n", src.index("def _usage_header_board("))]
    assert "board_dir(discover_children=True)" in resolver
    assert "_NO_SPAWN = True" in resolver and "_NO_SPAWN = was_no_spawn" in resolver


def test_bare_atm_reads_the_same_board_every_command_resolves(tmp_path):
    """REVIEW blocker 3: the header must not name a board `atm agents` refuses.

    T-959 shape: a configured shared board plus a non-empty, unmarked local
    .tickets. Every other command refuses that repo outright, so the default
    screen prints nothing rather than the local board's number.
    """
    repo = tmp_path / "repo"
    (repo / ".tickets" / "agents").mkdir(parents=True)
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    shared = tmp_path / "shared" / ".tickets"
    shared.mkdir(parents=True)
    # local board: non-empty (a real agent registration) and 3% left
    (repo / ".tickets" / "agents" / "alice.json").write_text(
        json.dumps({"owner": "alice", "seen": iso()}))
    seed_ledger(repo / ".tickets", claude=http_reading("claude", 3))
    seed_ledger(shared, claude=http_reading("claude", 91))
    cfg = tmp_path / "board.json"
    cfg.write_text(json.dumps({"boards": {str(repo): str(shared)}}))

    def atm(*args, cwd):
        env = dict(os.environ, HOME=str(tmp_path / "home"),
                   ATMAN_BOARD_CONFIG=str(cfg),
                   PYTHONPATH=str(ROOT / "src"))
        env.pop("TICKETS_DIR", None)
        env.pop("TICKET_AGENT", None)
        return subprocess.run([sys.executable, str(TOOL), *args], cwd=str(cwd),
                              capture_output=True, text=True, env=env)

    refused = atm("agents", cwd=repo)
    assert refused.returncode != 0 and "REFUSING BOARD" in refused.stderr
    bare = atm(cwd=repo)
    assert bare.returncode == 0 and "usage:" in bare.stdout  # help still prints
    assert usage_lines(bare.stdout) == []
    assert "3%" not in bare.stdout and "91%" not in bare.stdout
    assert "REFUSING" not in bare.stdout  # and the refusal is not shown either
    assert bare.stderr == ""

    # A .tickets under a directory that is not a git repo is not this repo's
    # board: board_dir walks past it, and so does the header.
    plain = tmp_path / "plain"
    (plain / "sub").mkdir(parents=True)
    seed_ledger(_mk(plain / ".tickets"), claude=http_reading("claude", 3))
    assert usage_lines(atm(cwd=plain / "sub").stdout) == []

    # And the ordinary case still works: a git repo with its own board.
    ok_repo = tmp_path / "ok"
    (ok_repo / ".tickets").mkdir(parents=True)
    subprocess.run(["git", "init", "-q", str(ok_repo)], check=True)
    seed_ledger(ok_repo / ".tickets", claude=http_reading("claude", 12))
    head = usage_lines(atm(cwd=ok_repo).stdout)
    assert len(head) == 1 and head[0].startswith("usage  claude 12% left")
    assert usage_lines(atm(cwd=ok_repo / ".tickets").stdout) == head


def test_bare_atm_resolves_its_board_without_spawning(board, monkeypatch, audit):
    """No TICKETS_DIR: the resolver that would run git must not, on this path."""
    seed_ledger(board, claude=http_reading("claude", 12))
    monkeypatch.delenv("TICKETS_DIR", raising=False)
    monkeypatch.chdir(board.parent)
    tk = tk_module(monkeypatch, board.parent.parent / "home")
    before = tree_hash(board)

    def bare():
        assert tk._usage_header_board() == str(board)
        monkeypatch.setattr(sys, "argv", ["atm"])
        tk.main()

    assert audit.record(bare) == []
    assert tree_hash(board) == before
    # The flag is restored, so a later command still cross-checks with git.
    assert tk._NO_SPAWN is False

    # A Ctrl-C during resolution belongs to the user, not to a usage header.
    def interrupted(*_a, **_kw):
        raise KeyboardInterrupt
    monkeypatch.setattr(tk, "board_dir", interrupted)
    held = sys.stderr
    with pytest.raises(KeyboardInterrupt):
        tk._usage_header_board()
    # ...and it still hands stderr and the no-spawn flag back on the way out.
    assert tk._NO_SPAWN is False and sys.stderr is held


# ---- narrow terminal ----------------------------------------------------

def test_narrow_terminal_is_not_mangled(board):
    seed_ledger(board, claude=http_reading("claude", 12), codex=signed_out_reading())
    run(board, "join", "alice", "--roles", "docs", "--harness", "claude", agent="alice")
    for args in ((), ("agents",), ("next", "--owner", "alice")):
        for ln in usage_lines(run(board, *args, agent="alice").stdout):
            assert len(ln) <= NARROW, (args, ln)

    stale = http_reading("claude", 74, reset_h=40, checked_h=-14)
    weird = reading("claude", "ok", remaining="3%", checked_at=iso(-9),
                    reset_at="whenever the weekly window rolls over next Tuesday",
                    windows=[{"name": "w", "used_percent": 97.0, "remaining_percent": 3.0,
                              "reset_at": "whenever the weekly window rolls over next Tuesday"}])
    longhint = reading("a-very-long-provider-name", "unknown", checked_at=iso(-1),
                       hint="usage request failed " + "x" * 200)
    for rec in (stale, weird, longhint, limited_reading(), expired_limit_reading(),
                signed_out_reading(), pu.empty_reading("cursor"),
                stale_limit_reading(), stale_limit_reading(reset_h=2),
                stale_limit_reading(reset_at="next Tuesday at midnight")):
        line = "usage  " + pu.format_compact_line(rec)
        assert len(line) <= NARROW, line
        assert "\n" not in line
        # The realistic budget is tighter than the hard cap: a header sits
        # next to output the user came for, and 80 is the wrap point.
        assert len(line) <= 64, line
    # A stale but real number keeps its age; the same line stays inside 80.
    assert "read 14h ago" in pu.format_compact_line(stale)


# ---- never a credential -------------------------------------------------

def test_no_credential_value_or_path_reaches_a_surface(board):
    secret = "sk-ant-oat01-NOTAREALTOKEN00000000"
    credpath = "/Users/nobody/.claude/.credentials.json"
    seed_ledger(
        board,
        claude=limited_reading(message="limit hit; token %s from %s" % (secret, credpath)),
        codex=reading("codex", "unknown", checked_at=iso(-0.1), source="network",
                      hint="usage request failed reading %s (%s)" % (credpath, secret)),
    )
    for args in ((), ("agents",)):
        out = run(board, *args).stdout
        assert secret not in out and credpath not in out
        assert ".credentials" not in out and "sk-ant" not in out
        assert usage_lines(out)
    # The ledger view is where a limit message belongs; the header is not.
    assert secret not in json.dumps(
        [pu.compact_reading(pu.get_reading(str(board), h)) for h in ("claude", "codex")])


# ---- one implementation -------------------------------------------------

def test_one_implementation_reuses_the_existing_reader():
    src = TOOL.read_text()
    assert "_provider_usage().seat_usage_line(" in src
    assert "pu.header_lines(board, harnesses=harnesses)" in src
    # The ledger view keeps its own, fuller line; the header does not
    # re-implement formatting in the CLI.
    assert "% left" not in src and "LIMITED," not in src
    assert "def format_compact_line(" in (
        ROOT / "src" / "ticket_board" / "provider_usage.py").read_text()
    # The packaged CLI has no usage ledger at all; it gets no weaker copy.
    packaged = CLI.read_text()
    assert "provider_usage" not in packaged and "usage_header_lines" not in packaged
