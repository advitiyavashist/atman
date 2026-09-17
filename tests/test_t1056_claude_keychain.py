"""T-1056: Claude usage reads the macOS login keychain first.

Honesty: unknown stays unknown. The credential value never reaches stdout,
a log, the board, or a diagnostic. Tests assert that with a sentinel.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from ticket_board import provider_usage as pu  # noqa: E402

SENTINEL = "t1056-sentinel-oauth-token-do-not-leak"
SENTINEL_BLOB = json.dumps({"claudeAiOauth": {"accessToken": SENTINEL}})


def utc(ts):
    return datetime.fromisoformat(ts.replace("Z", "+00:00"))


def _blob(token):
    return json.dumps({"claudeAiOauth": {"accessToken": token}})


def test_keychain_service_is_the_one_claude_code_uses():
    assert pu.CLAUDE_KEYCHAIN_SERVICE == "Claude Code-credentials"


def test_keychain_reader_invokes_security_without_logging_secret():
    seen = {}

    class _Proc:
        returncode = 0
        stdout = SENTINEL_BLOB.encode("utf-8")
        stderr = b""

    def runner(cmd, capture_output=True, timeout=5):
        seen["cmd"] = list(cmd)
        seen["capture_output"] = capture_output
        seen["timeout"] = timeout
        return _Proc()

    blob = pu.macos_keychain_generic_password(
        pu.CLAUDE_KEYCHAIN_SERVICE, runner=runner)
    assert seen["cmd"] == [
        "security", "find-generic-password",
        "-s", "Claude Code-credentials", "-w",
    ]
    assert seen["capture_output"] is True
    token = pu._oauth_token_from_blob(blob)
    assert token == SENTINEL
    # The helper itself must not have printed the value.
    dumped = json.dumps(seen)
    assert SENTINEL not in dumped


def test_absent_keychain_item_is_empty_not_an_exception():
    class _Proc:
        returncode = 44
        stdout = b""
        stderr = b"The specified item could not be found in the keychain."

    blob = pu.macos_keychain_generic_password(
        pu.CLAUDE_KEYCHAIN_SERVICE, runner=lambda *a, **k: _Proc())
    assert blob == ""
    token = pu.read_claude_oauth_token(
        home="/no-t1056-home", environ={},
        keychain_reader=lambda: "", platform="darwin")
    assert token == ""


def test_keychain_backed_install_yields_a_reading():
    seen = {}

    def transport(url, headers, timeout):
        seen["url"] = url
        seen["auth"] = headers.get("Authorization")
        return 200, json.dumps({
            "five_hour": {"used_percent": 15, "resets_at": "2026-09-16T18:00:00Z"},
        })

    rec = pu.fetch_provider_usage(
        "claude", home="/no-t1056-home",
        transport=transport, environ={},
        now=utc("2026-09-16T10:00:00Z"),
        keychain_reader=lambda: SENTINEL_BLOB,
        platform="linux")
    assert rec["status"] == "ok"
    assert rec["remaining"] == "85%"
    assert seen["url"] == pu.CLAUDE_USAGE_URL
    assert seen["auth"] == "Bearer %s" % SENTINEL
    dumped = json.dumps(rec)
    line = pu.format_usage_line(rec)
    assert SENTINEL not in dumped
    assert SENTINEL not in line
    assert "available" not in line.lower()
    assert "UNKNOWN" not in line


def test_absent_keychain_and_file_is_unknown_with_relogin(tmp_path):
    called = []
    rec = pu.fetch_provider_usage(
        "claude", home=str(tmp_path / "empty"),
        transport=lambda *a: called.append(a) or (200, "{}"),
        environ={}, now=utc("2026-09-16T10:00:00Z"),
        keychain_reader=lambda: "",
        platform="darwin")
    assert called == []
    assert rec["status"] == "unknown"
    assert rec["remaining"] is None
    assert rec["account_state"] == "signed_out"
    assert "re-login" in rec["hint"]
    line = pu.format_usage_line(rec)
    assert "UNKNOWN" in line
    assert "available" not in line.lower()
    assert " 0%" not in line
    assert SENTINEL not in json.dumps(rec)
    assert SENTINEL not in line


def test_keychain_wins_over_credentials_file(tmp_path):
    home = tmp_path / "home"
    cred = home / ".claude" / ".credentials.json"
    cred.parent.mkdir(parents=True)
    cred.write_text(_blob("file-token-must-not-win"))
    got = pu.read_claude_oauth_token(
        str(home), environ={},
        keychain_reader=lambda: SENTINEL_BLOB,
        platform="darwin")
    assert got == SENTINEL
    got = ""


def test_file_fallback_when_keychain_empty(tmp_path):
    home = tmp_path / "home"
    cred = home / ".claude" / ".credentials.json"
    cred.parent.mkdir(parents=True)
    cred.write_text(_blob(SENTINEL))
    got = pu.read_claude_oauth_token(
        str(home), environ={},
        keychain_reader=lambda: "",
        platform="darwin")
    assert got == SENTINEL
    got = ""


def test_keychain_skipped_on_non_darwin_without_inject(tmp_path):
    home = tmp_path / "home"
    cred = home / ".claude" / ".credentials.json"
    cred.parent.mkdir(parents=True)
    cred.write_text(_blob(SENTINEL))
    got = pu.read_claude_oauth_token(
        str(home), environ={}, platform="linux")
    assert got == SENTINEL
    got = ""


def test_env_token_still_wins_over_keychain():
    got = pu.read_claude_oauth_token(
        home="/no-t1056-home",
        environ={"ANTHROPIC_OAUTH_TOKEN": SENTINEL},
        keychain_reader=lambda: _blob("keychain-must-not-win"),
        platform="darwin")
    assert got == SENTINEL
    got = ""


def test_security_timeout_or_oserror_is_empty():
    def boom(*a, **k):
        raise subprocess.TimeoutExpired(cmd="security", timeout=5)

    assert pu.macos_keychain_generic_password(
        pu.CLAUDE_KEYCHAIN_SERVICE, runner=boom) == ""

    def missing(*a, **k):
        raise FileNotFoundError("security")

    assert pu.macos_keychain_generic_password(
        pu.CLAUDE_KEYCHAIN_SERVICE, runner=missing) == ""


def test_live_oauth_body_uses_utilization_not_used_percent():
    """Real /api/oauth/usage (this Mac, 2026-09-16) has utilization 0..1."""
    body = json.dumps({
        "five_hour": {"utilization": 0.15, "resets_at": "2026-09-16T18:00:00Z"},
        "seven_day": {"utilization": 0.08, "resets_at": "2026-09-20T00:00:00Z"},
    })
    rec = pu.parse_claude_oauth_usage(body, checked_at="2026-09-16T10:00:00Z")
    assert rec["status"] == "ok"
    assert rec["remaining"] == "85%"
    assert rec["reset_at"] == "2026-09-16T18:00:00Z"
    names = [w["name"] for w in rec["windows"]]
    assert "five_hour" in names and "seven_day" in names
    line = pu.format_usage_line(rec)
    assert "remaining 85%" in line
    assert "UNKNOWN" not in line
    assert "available" not in line.lower()


def test_readme_does_not_imply_file_only_claude_usage_on_macos():
    text = (ROOT / "README.md").read_text(encoding="utf-8")
    assert "Claude Code-credentials" in text
    assert "login keychain" in text
    # File path is a fallback, not the macOS source of truth.
    assert "~/.claude/.credentials.json" in text
    row = [ln for ln in text.splitlines() if "Claude usage remaining" in ln]
    assert row, "limitations table needs a Claude usage remaining row"
    assert "unknown" in row[0].lower()
    assert "re-login" in row[0]
    assert "available" not in row[0].lower()


@pytest.mark.skipif(sys.platform != "darwin", reason="real macOS keychain")
def test_real_macos_keychain_backed_install_yields_a_reading():
    """This machine stores Claude Code credentials in the login keychain.

    The file ~/.claude/.credentials.json is absent. Prove the keychain path
    yields a reading without leaking the credential into the result.
    """
    home = "/no-t1056-real-home"
    assert not os.path.exists(os.path.expanduser("~/.claude/.credentials.json"))
    token = pu.read_claude_oauth_token(
        home, environ={}, platform="darwin")
    present = bool(token) and len(token) > 8
    n = len(token)
    token = ""
    if not present:
        pytest.skip("login keychain has no Claude Code-credentials item")
    assert n > 8

    def transport(url, headers, timeout):
        auth = headers.get("Authorization") or ""
        assert auth.startswith("Bearer ")
        assert len(auth) > len("Bearer ")
        return 200, json.dumps({
            "five_hour": {"used_percent": 15, "resets_at": "2026-09-16T18:00:00Z"},
        })

    rec = pu.fetch_provider_usage(
        "claude", home=home, transport=transport, environ={},
        now=utc("2026-09-16T10:00:00Z"), platform="darwin")
    assert rec["status"] == "ok"
    assert rec["remaining"] == "85%"
    dumped = json.dumps(rec)
    line = pu.format_usage_line(rec)
    assert "Bearer " not in dumped
    assert SENTINEL not in dumped
    assert "available" not in line.lower()
