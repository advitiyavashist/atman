"""T-1094: tighten the usage-line timezone exemption; no limit_message tooltip.

Follow-up from the #249 fifth review (merged, no blockers):
1. _TZ_TOKEN must not exempt an arbitrary slashy letters-only token.
2. A Team-card tooltip must not carry the provider's limit_message.
3. A 14+ digit epoch is garbage, not a reset.
"""
from __future__ import annotations

import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

from test_wakeup import board, run  # noqa: F401

ROOT = Path(__file__).resolve().parents[1]
TOOL = ROOT / "tickets.py"
sys.path.insert(0, str(ROOT / "src"))
from ticket_board import provider_usage as pu  # noqa: E402


def iso(delta_h=0.0):
    return (datetime.now(timezone.utc) + timedelta(hours=delta_h)).strftime(
        "%Y-%m-%dT%H:%M:%SZ")


def test_tz_exemption_requires_an_iana_area_and_the_24_char_cap():
    for zone in ("America/Los_Angeles", "Europe/Paris", "Etc/UTC",
                 "Pacific/Auckland"):
        assert pu._is_tz_token(zone), zone
        assert zone in pu._scrub_reset("09:00 %s" % zone)
    assert pu.reset_label("09:00 America/Los_Angeles") == "09:00 America/Los_Angeles"
    assert pu.reset_label("09:00 Etc/UTC") == "09:00 Etc/UTC"

    # The comment used to cite Etc/GMT+3 as exempt. A digit in a segment
    # fails the token, so the whole reset is dropped rather than printed
    # with a redacted hole.
    assert not pu._is_tz_token("Etc/GMT+3")
    assert pu.reset_label("09:00 Etc/GMT+3") == ""

    leaked = (
        "Users/someone/secrets",
        "ghp/AAAAAAAAAAAA",
        "Bearer/mysecrettoken",
        "America/Argentina/Buenos_Aires",  # 30 chars: cap still applies
    )
    for token in leaked:
        assert not pu._is_tz_token(token), token
        assert pu.reset_label(token) == ""
        assert token not in pu._scrub_reset(token)
        line = pu.format_compact_line(pu.record_observed_limit(
            "claude", "cap", iso(-0.2), token))
        assert token not in line and "resets %s" % token not in line, line


def test_fourteen_plus_digit_epoch_is_not_a_reset():
    seconds = "1789145400"
    millis = "1789145400000"
    micros = "1789145400000000"   # 16 digits -- used to print
    nanos = "1789145400000000000"
    for epoch in (seconds, millis, micros, nanos):
        assert pu.reset_label(epoch) == ""
        line = pu.format_compact_line(pu.record_observed_limit(
            "claude", "cap", iso(-0.2), epoch))
        assert epoch not in line
        assert "resets" not in line


def test_ui_reading_drops_limit_message_and_keeps_the_ledger():
    rec = pu.record_observed_limit(
        "claude", "hit your session limit; try again at Sep 19th",
        iso(-0.2), iso(2))
    public = pu.public_reading(rec)
    ui = pu.ui_reading(rec)
    assert "session limit" in public["limit_message"]
    assert "limit_message" not in ui
    assert "session limit" in pu.format_usage_line(rec)
    compact = pu.format_compact_line(rec)
    assert "session limit" not in compact
    assert compact.startswith("claude LIMITED")


def test_team_tooltip_does_not_carry_limit_message(board):
    """Decision: tooltip matches the compact header -- hint or age only."""
    src = TOOL.read_text()
    assert "pu.hint||pu.age" in src
    assert "pu.hint||pu.limit_message||pu.age" not in src
    assert "ui_reading(" in src

    run(board, "join", "alice", "--roles", "docs", "--harness", "claude",
        agent="alice")
    led = {"updated": iso(), "providers": {
        "claude": pu.record_observed_limit(
            "claude", "You've hit your session limit", iso(-0.2), iso(2)),
    }}
    (Path(board) / "provider_usage.json").write_text(json.dumps(led, indent=2))

    import importlib.util
    spec = importlib.util.spec_from_file_location("tickets_t1094", TOOL)
    tk = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(tk)
    snap = tk.board_snapshot(str(board))
    (agent,) = [a for a in snap["agents"] if a["name"] == "alice"]
    pu_row = agent["provider_usage"]
    assert "limit_message" not in pu_row
    blob = json.dumps(pu_row)
    assert "session limit" not in blob
    assert pu_row["status"] == "limited"
    assert pu_row.get("hint") or pu_row.get("age") or pu_row["status"]


def test_tz_exemption_scrubs_unsafe_post_slash_segments():
    """UTC/GMT have no sub-zones; credential-shaped city segments are not tzs."""
    bad = (
        "UTC/Bearer_TOKEN",
        "GMT/sk_live_abcdefg",
        "Etc/" + ("A" * 19),
    )
    for token in bad:
        assert not pu._is_tz_token(token), token
        assert pu.reset_label(token) == ""
        assert token not in pu._scrub_reset(token)


def test_ui_reading_scrubs_reset_at():
    leaked = "Users/someone/secrets"
    rec = pu.record_observed_limit("claude", "cap", iso(-0.2), leaked)
    public = pu.public_reading(rec)
    ui = pu.ui_reading(rec)
    assert public["reset_at"] == leaked
    assert ui.get("reset_at") in (None, "")
    assert leaked not in json.dumps(ui)
