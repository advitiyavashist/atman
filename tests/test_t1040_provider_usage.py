"""T-1040: provider usage ledger — observed limits + credentialed reads."""
from __future__ import annotations

import json
import os
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from ticket_board import provider_usage as pu  # noqa: E402

TOOL = ROOT / "tickets.py"
# T-1056: isolate T-1040 file-path tests from the real macOS keychain.
_NO_KEYCHAIN = lambda: ""  # noqa: E731


def utc(ts):
    return datetime.fromisoformat(ts.replace("Z", "+00:00"))


def test_unknown_stays_unknown():
    rec = pu.empty_reading("claude")
    assert rec["status"] == "unknown"
    assert rec["remaining"] is None
    line = pu.format_usage_line(rec)
    assert "UNKNOWN" in line
    assert "available" not in line.lower()
    assert "fine" not in line.lower()
    assert " 0%" not in line
    cursor = pu.empty_reading("cursor")
    assert cursor["status"] == "no_data"
    assert "no data" in pu.format_usage_line(cursor)
    assert "available" not in pu.format_usage_line(cursor).lower()


def test_record_limit_with_and_without_reset(tmp_path):
    board = tmp_path / ".tickets"
    board.mkdir()
    with_reset = pu.record_observed_limit(
        "claude",
        "You've hit your session limit · resets 1:40am (Asia/Singapore)",
        "2026-09-16T02:00:00Z",
        reset_at="2026-09-16T17:40:00Z",
    )
    now = utc("2026-09-16T03:00:00Z")
    pu.put_reading(str(board), with_reset, now=now)
    got = pu.get_reading(str(board), "claude", now=now)
    assert got["status"] == "limited"
    assert got["reset_at"] == "2026-09-16T17:40:00Z"
    assert "You've hit your session limit" in got["limit_message"]

    bare = pu.record_observed_limit(
        "codex",
        "ERROR: You've hit your usage limit. Visit ... or try again at Sep 19th, 2026 11:17 PM.",
        "2026-09-16T00:01:00Z",
        reset_at="",
    )
    assert bare["status"] == "limited"
    assert bare["reset_at"] is None
    assert "try again at Sep 19th" in bare["limit_message"]


def test_stale_reset_expires_to_unknown_not_available():
    rec = pu.record_observed_limit(
        "claude", "limited", "2026-09-16T02:00:00Z",
        reset_at="2026-09-16T03:00:00Z")
    still = pu.expire_stale_resets(rec, now=utc("2026-09-16T02:59:00Z"))
    assert still["status"] == "limited"
    gone = pu.expire_stale_resets(rec, now=utc("2026-09-16T03:00:01Z"))
    assert gone["status"] == "unknown"
    assert gone["hint"] == "reset elapsed"
    assert "available" not in pu.format_usage_line(gone).lower()


def test_parse_codex_tokens_used_line():
    text = "task complete\ntokens used\n18432\n"
    assert pu.parse_codex_tokens_used(text) == 18432
    assert pu.parse_codex_tokens_used("no usage here") is None


def test_parse_claude_session_usage_record():
    rec = {"type": "assistant", "usage": {
        "input_tokens": 120, "output_tokens": 40,
        "cache_read_input_tokens": 10, "cache_creation_input_tokens": 5}}
    assert pu.parse_claude_session_usage(rec) == 175
    assert pu.parse_claude_session_usage({"type": "assistant"}) is None
    assert pu.parse_claude_session_usage("not a dict") is None


def test_good_claude_oauth_maps_windows():
    body = json.dumps({
        "five_hour": {"used_percent": 40, "resets_at": "2026-09-16T17:40:00Z"},
        "seven_day": {"used_percent": 12, "resets_at": "2026-09-20T00:00:00Z"},
    })
    rec = pu.parse_claude_oauth_usage(body, checked_at="2026-09-16T10:00:00Z")
    assert rec["status"] == "ok"
    assert rec["source"] == "oauth_usage"
    assert rec["remaining"] == "60%"
    names = [w["name"] for w in rec["windows"]]
    assert "five_hour" in names and "seven_day" in names
    limited = pu.parse_claude_oauth_usage(json.dumps({
        "five_hour": {"used_percent": 100, "resets_at": "2026-09-16T17:40:00Z"},
    }))
    assert limited["status"] == "limited"
    assert limited["reset_at"] == "2026-09-16T17:40:00Z"


def test_good_codex_wham_maps_windows():
    body = json.dumps({
        "rateLimits": {
            "primary": {"used_percent": 25, "resets_at": "2026-09-19T15:17:00Z"},
        }
    })
    rec = pu.parse_codex_wham_usage(body)
    assert rec["status"] == "ok"
    assert rec["remaining"] == "75%"
    assert rec["windows"][0]["name"] == "primary"


def test_unparseable_body_is_unknown():
    for raw in ("", "not-json", "[]", json.dumps({"unexpected": True})):
        rec = pu.parse_claude_oauth_usage(raw)
        assert rec["status"] == "unknown"
        assert rec["remaining"] is None
        assert rec["hint"] == "unparseable usage body"


def test_http_401_and_empty_credential_unknown_with_relogin():
    checked = "2026-09-16T10:00:00Z"
    empty = pu.empty_credential_reading("claude", checked)
    assert empty["status"] == "unknown"
    assert empty["account_state"] == "signed_out"
    assert "re-login" in empty["hint"]
    http = pu.reading_from_http("codex", 401, "{}", checked_at=checked)
    assert http["status"] == "unknown"
    assert http["account_state"] == "signed_out"
    assert "re-login" in http["hint"]
    # account ready must not become usage ok
    ok_http = pu.reading_from_http(
        "claude", 200,
        json.dumps({"five_hour": {"used_percent": 10, "resets_at": "2026-09-16T18:00:00Z"}}),
        checked_at=checked, account_state="ready")
    assert ok_http["account_state"] == "ready"
    assert ok_http["status"] == "ok"


PARSEABLE_CLAUDE_BODY = json.dumps({
    "five_hour": {"used_percent": 40, "resets_at": "2026-09-16T17:40:00Z"},
})


@pytest.mark.parametrize("code", [500, 503, 429, 302])
def test_non_200_parseable_body_is_unknown(code):
    rec = pu.reading_from_http("claude", code, PARSEABLE_CLAUDE_BODY)
    assert rec["status"] == "unknown"
    assert rec["remaining"] is None
    assert rec["windows"] == []
    assert str(code) in rec["hint"]
    assert "ok" not in rec["status"]


@pytest.mark.parametrize("code", [500, 503, 429, 302])
def test_fetch_non_200_parseable_body_is_unknown(tmp_path, code):
    home = tmp_path / "home"
    cred = home / ".claude" / ".credentials.json"
    cred.parent.mkdir(parents=True)
    cred.write_text(json.dumps({"claudeAiOauth": {"accessToken": "secret-token"}}))

    def transport(url, headers, timeout):
        return code, PARSEABLE_CLAUDE_BODY

    rec = pu.fetch_provider_usage(
        "claude", home=str(home), transport=transport,
        environ={}, now=utc("2026-09-16T10:00:00Z"),
        keychain_reader=_NO_KEYCHAIN)
    assert rec["status"] == "unknown"
    assert rec["remaining"] is None
    assert str(code) in rec["hint"]
    assert "secret-token" not in json.dumps(rec)


def test_timeout_is_unknown():
    rec = pu.timeout_reading("codex", "2026-09-16T10:00:00Z")
    assert rec["status"] == "unknown"
    assert "timed out" in rec["hint"]


def test_stale_persisted_reading_labelled_with_age(tmp_path):
    board = tmp_path / ".tickets"
    board.mkdir()
    rec = pu.parse_claude_oauth_usage(json.dumps({
        "five_hour": {"used_percent": 30, "resets_at": "2026-09-16T18:00:00Z"},
    }), checked_at="2026-09-16T09:00:00Z")
    pu.put_reading(str(board), rec)
    now = utc("2026-09-16T09:20:00Z")
    got = pu.public_reading(pu.get_reading(str(board), "claude", now), now)
    assert got["age"] == "last read 20m ago"
    assert "last read 20m ago" in pu.format_usage_line(got, now)


def test_fetch_uses_injected_transport_and_never_returns_token(tmp_path):
    home = tmp_path / "home"
    cred = home / ".claude" / ".credentials.json"
    cred.parent.mkdir(parents=True)
    cred.write_text(json.dumps({"claudeAiOauth": {"accessToken": "secret-token"}}))
    seen = {}

    def transport(url, headers, timeout):
        seen["url"] = url
        seen["auth"] = headers.get("Authorization")
        seen["timeout"] = timeout
        body = json.dumps({
            "five_hour": {"used_percent": 5, "resets_at": "2026-09-16T18:00:00Z"},
        })
        return 200, body

    rec = pu.fetch_provider_usage(
        "claude", home=str(home), transport=transport,
        environ={}, now=utc("2026-09-16T10:00:00Z"),
        keychain_reader=_NO_KEYCHAIN)
    assert rec["status"] == "ok"
    assert seen["url"] == pu.CLAUDE_USAGE_URL
    assert seen["auth"] == "Bearer secret-token"
    dumped = json.dumps(rec)
    assert "secret-token" not in dumped
    assert pu.read_claude_oauth_token(
        str(home), environ={}, keychain_reader=_NO_KEYCHAIN) == "secret-token"


def test_fetch_empty_credential_does_not_call_transport(tmp_path):
    called = []
    rec = pu.fetch_provider_usage(
        "claude", home=str(tmp_path / "empty"),
        transport=lambda *a: called.append(a) or (200, "{}"),
        environ={}, now=utc("2026-09-16T10:00:00Z"),
        keychain_reader=_NO_KEYCHAIN)
    assert called == []
    assert rec["status"] == "unknown"
    assert rec["hint"] == "re-login required"


def test_fetch_timeout_and_cursor_no_data(tmp_path):
    def boom(url, headers, timeout):
        raise TimeoutError("timed out")

    home = tmp_path / "home"
    (home / ".codex").mkdir(parents=True)
    (home / ".codex" / "auth.json").write_text(json.dumps({
        "tokens": {"access_token": "codex-secret"}}))
    rec = pu.fetch_provider_usage(
        "codex", home=str(home), transport=boom, environ={})
    assert rec["status"] == "unknown"
    assert "timed out" in rec["hint"]
    assert "codex-secret" not in json.dumps(rec)
    nodata = pu.fetch_provider_usage("cursor", home=str(home), transport=boom)
    assert nodata["status"] == "no_data"


def test_refresh_persists_and_skips_busy_gate(tmp_path):
    board = tmp_path / ".tickets"
    board.mkdir()
    home = tmp_path / "home"
    (home / ".claude").mkdir(parents=True)
    (home / ".claude" / ".credentials.json").write_text(json.dumps({
        "claudeAiOauth": {"accessToken": "t"}}))
    (home / ".codex").mkdir()
    (home / ".codex" / "auth.json").write_text(json.dumps({
        "access_token": "c"}))

    def transport(url, headers, timeout):
        if "anthropic" in url:
            return 200, json.dumps({
                "five_hour": {"used_percent": 8, "resets_at": "2026-09-16T18:00:00Z"}})
        return 200, json.dumps({
            "rateLimits": {"primary": {"used_percent": 50,
                                       "resets_at": "2026-09-19T15:17:00Z"}}})

    out = pu.refresh_http_providers(
        str(board), home=str(home), transport=transport, environ={},
        keychain_reader=_NO_KEYCHAIN)
    assert out["claude"]["status"] == "ok"
    assert out["codex"]["status"] == "ok"
    assert out["cursor"]["status"] == "no_data"
    assert out["agy"]["status"] == "no_data"
    lines = "\n".join(pu.format_ledger_lines(str(board)))
    assert "claude ok" in lines
    assert "cursor no_data" in lines
    assert "agy no_data" in lines


def test_failed_read_does_not_erase_observed_limit(tmp_path):
    """Observed limit, then failed read, then successful 200 — merge, not replace."""
    board = tmp_path / ".tickets"
    board.mkdir()
    now_obs = utc("2026-09-16T02:00:00Z")
    observed = pu.record_observed_limit(
        "claude",
        "You've hit your limit, resets 5pm",
        "2026-09-16T02:00:00Z",
        reset_at="2026-09-16T09:00:00Z",
    )
    pu.put_reading(str(board), observed, now=now_obs)
    before = pu.get_reading(str(board), "claude", now=now_obs)
    assert before["status"] == "limited"
    assert "You've hit your limit" in before["limit_message"]
    assert before["reset_at"] == "2026-09-16T09:00:00Z"
    before_line = pu.format_usage_line(before, now=now_obs)
    assert "limited" in before_line
    assert "You've hit your limit" in before_line

    failed = pu.empty_credential_reading("claude", "2026-09-16T02:05:00Z")
    assert failed["status"] == "unknown"
    now_fail = utc("2026-09-16T02:05:00Z")
    pu.put_reading(str(board), failed, now=now_fail)
    mid = pu.get_reading(str(board), "claude", now=now_fail)
    assert mid["status"] == "limited"
    assert "You've hit your limit" in mid["limit_message"]
    assert mid["reset_at"] == "2026-09-16T09:00:00Z"
    assert "re-login" in mid["hint"]
    mid_line = pu.format_usage_line(mid, now=now_fail)
    assert "limited" in mid_line
    assert mid["status"] != "unknown"

    ok = pu.reading_from_http(
        "claude", 200,
        json.dumps({
            "five_hour": {"used_percent": 10, "resets_at": "2026-09-16T18:00:00Z"},
        }),
        checked_at="2026-09-16T02:10:00Z",
        account_state="ready")
    assert ok["status"] == "ok"
    now_ok = utc("2026-09-16T02:10:00Z")
    pu.put_reading(str(board), ok, now=now_ok)
    after = pu.get_reading(str(board), "claude", now=now_ok)
    assert after["status"] == "ok"
    assert after["remaining"] == "90%"
    assert after["reset_at"] == "2026-09-16T18:00:00Z"


def run(board, *args, env=None):
    e = dict(os.environ, TICKETS_DIR=str(board), TICKET_AGENT="boss",
             HOME=str(board.parent.parent / "home"),
             TICKETS_USAGE_REFRESH="0")
    e.pop("TICKETS_STOP_HOOK", None)
    e.pop("TICKET_SEAT", None)
    if env:
        e.update(env)
    return subprocess.run(
        [sys.executable, str(TOOL), *args],
        capture_output=True, text=True, cwd=str(board.parent), env=e)


def test_credential_paths_are_the_ones_we_found(tmp_path, monkeypatch):
    home = tmp_path / "home"
    (home / ".claude").mkdir(parents=True)
    (home / ".codex").mkdir()
    monkeypatch.delenv("CODEX_HOME", raising=False)
    assert pu.CLAUDE_CREDENTIALS_RELPATH == os.path.join(".claude", ".credentials.json")
    assert pu.CODEX_AUTH_RELPATH == os.path.join(".codex", "auth.json")
    assert pu.claude_credentials_file(str(home)) == str(home / ".claude" / ".credentials.json")
    assert pu.codex_auth_file(str(home), environ={}) == str(home / ".codex" / "auth.json")
    assert pu.codex_auth_file(str(home), environ={"CODEX_HOME": str(tmp_path / "alt-codex")}) == str(
        tmp_path / "alt-codex" / "auth.json")


def test_who_and_harness_surface_ledger(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    subprocess.run(
        ["git", "-C", str(repo), "commit", "-q", "--allow-empty", "-m", "init"],
        check=True, env=dict(os.environ, GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t@t",
                             GIT_COMMITTER_NAME="t", GIT_COMMITTER_EMAIL="t@t"))
    board = repo / ".tickets"
    assert run(board, "init").returncode == 0
    assert run(board, "join", "boss", "--roles", "backend").returncode == 0
    rec = pu.parse_claude_oauth_usage(json.dumps({
        "five_hour": {"used_percent": 20, "resets_at": "2026-09-16T18:00:00Z"},
    }), checked_at="2026-09-16T10:00:00Z")
    pu.put_reading(str(board), rec)
    who = run(board, "who")
    assert who.returncode == 0, who.stderr + who.stdout
    assert "claude ok" in who.stdout
    assert "remaining 80%" in who.stdout
    avail = run(board, "harness", "available")
    assert avail.returncode == 0, avail.stderr + avail.stdout
    assert "USAGE (observed + credentialed read" in avail.stdout
    assert "claude ok" in avail.stdout
    assert "cursor no_data" in avail.stdout
