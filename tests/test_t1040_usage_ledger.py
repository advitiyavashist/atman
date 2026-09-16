"""T-1040: credentialed usage ledger. Throwaway boards only. HTTP is stubbed."""
import io
import json
from pathlib import Path

import pytest

from ticket_board import usage_ledger as ul

SECRET = "sk-ant-secret-test-token-DO-NOT-LEAK"
CODEX_SECRET = "codex-secret-test-token-DO-NOT-LEAK"
REJECTION = "You've hit your session limit · resets 1:40am (Asia/Singapore)"

CLAUDE_OK = {
    "five_hour": {"utilization": 62, "resets_at": "2026-09-16T03:10:00Z"},
    "seven_day": {"utilization": 41, "resets_at": "2026-09-20T00:00:00Z"},
    "limits": [{
        "kind": "weekly_scoped",
        "percent": 10,
        "resets_at": "2026-09-20T00:00:00Z",
        "scope": {"model": {"display_name": "fable"}},
    }],
}
CODEX_OK = {
    "plan_type": "plus",
    "rate_limit": {
        "primary_window": {
            "used_percent": 20,
            "limit_window_seconds": 18000,
            "reset_at": 1789531800,
        },
        "secondary_window": {
            "used_percent": 80,
            "limit_window_seconds": 604800,
            "reset_at": 1789920000,
        },
    },
}


def _board(tmp_path):
    board = tmp_path / ".tickets"
    (board / "agents").mkdir(parents=True)
    return board


def _write_agent(board, owner="alice", harness="claude", limit=None):
    rec = {
        "owner": owner,
        "seen": "2026-09-16T00:00:00Z",
        "branch": "%s/work" % owner,
        "sha": "abc1234",
        "ticket": "",
        "cwd": str(board.parent),
    }
    if limit:
        rec["limit"] = limit
    (board / "agents" / ("%s.json" % owner)).write_text(json.dumps(rec))
    wf_path = board / "workforce.json"
    wf = json.loads(wf_path.read_text()) if wf_path.exists() else {}
    wf[owner] = {"harness": harness, "tool": harness}
    wf_path.write_text(json.dumps(wf))
    return rec


def _assert_clean(text, *extras):
    blob = text if isinstance(text, str) else json.dumps(text)
    for secret in (SECRET, CODEX_SECRET) + extras:
        assert secret not in blob


def _http(status_by_url, bodies=None, captured=None):
    bodies = bodies or {}

    def getter(url, headers, timeout=10):
        if captured is not None:
            captured.append({"url": url, "headers": dict(headers), "timeout": timeout})
        if url == ul.CLAUDE_USAGE_URL:
            body = bodies.get("claude", CLAUDE_OK)
        elif url == ul.CODEX_USAGE_URL:
            body = bodies.get("codex", CODEX_OK)
        else:
            return 404, b"{}"
        status = status_by_url.get(url, 200)
        if status == "timeout":
            return "timeout", b""
        if isinstance(body, (bytes, bytearray)):
            return status, bytes(body)
        if isinstance(body, str):
            return status, body.encode()
        return status, json.dumps(body).encode()

    return getter


def test_good_response_maps_windows(tmp_path):
    board = _board(tmp_path)
    captured = []
    ledger = ul.refresh_usage_ledger(
        str(board),
        http_get_fn=_http({}, captured=captured),
        claude_creds={"present": True, "token": SECRET, "account": "acct-claude"},
        codex_creds={"present": True, "token": CODEX_SECRET, "account_id": "acct-codex"},
        now_stamp="2026-09-16T03:00:00Z",
    )
    claude = ledger["providers"]["claude"]
    assert claude["usage_state"] == "ok"
    assert claude["account_state"] == "present"
    assert claude["account"] == "acct-claude"
    assert claude["windows"]["five_hour"]["percent_used"] == 62
    assert claude["windows"]["five_hour"]["resets_at"] == "2026-09-16T03:10:00Z"
    assert claude["windows"]["seven_day"]["percent_used"] == 41
    assert claude["windows"]["weekly_fable"]["percent_used"] == 10
    assert claude["windows"]["weekly_fable"]["model"] == "fable"
    codex = ledger["providers"]["codex"]
    assert codex["usage_state"] == "ok"
    assert "five_hour" in codex["windows"]
    assert "seven_day" in codex["windows"]
    assert ledger["providers"]["cursor"]["reason"] == "no data"
    assert ledger["providers"]["agy"]["reason"] == "no data"
    line = ul.format_provider_line(claude, now_stamp="2026-09-16T03:02:00Z")
    assert "claude 5h 62% used, resets 03:10" in line
    assert "7d 41%" in line
    assert captured and captured[0]["url"] == ul.CLAUDE_USAGE_URL
    assert SECRET in captured[0]["headers"]["Authorization"]
    dumped = (board / "usage-ledger.json").read_text()
    _assert_clean(dumped)
    _assert_clean(line)
    _assert_clean(ul.format_usage_section(ledger))


@pytest.mark.parametrize("kind", ["http_401", "empty_creds"])
def test_401_or_empty_credentials_unknown_with_relogin(tmp_path, kind):
    board = _board(tmp_path)
    if kind == "empty_creds":
        ledger = ul.refresh_usage_ledger(
            str(board),
            http_get_fn=_http({}),
            claude_creds={"present": False, "token": "", "account": ""},
            codex_creds={"present": False, "token": "", "account_id": ""},
            now_stamp="2026-09-16T03:00:00Z",
        )
        reason = ledger["providers"]["claude"]["reason"]
        assert "no credentials" in reason
    else:
        ledger = ul.refresh_usage_ledger(
            str(board),
            http_get_fn=_http({
                ul.CLAUDE_USAGE_URL: 401,
                ul.CODEX_USAGE_URL: 401,
            }),
            claude_creds={"present": True, "token": SECRET, "account": "acct"},
            codex_creds={"present": True, "token": CODEX_SECRET, "account_id": "acct"},
            now_stamp="2026-09-16T03:00:00Z",
        )
        reason = ledger["providers"]["claude"]["reason"]
        assert "401" in reason
    assert ledger["providers"]["claude"]["usage_state"] == "unknown"
    assert ul.RELOGIN_HINT in reason
    assert ledger["providers"]["claude"]["account_state"] == (
        "missing" if kind == "empty_creds" else "present")
    line = ul.format_usage_section(ledger)
    assert "unknown" in line
    assert ul.RELOGIN_HINT in line
    _assert_clean(line)
    _assert_clean((board / "usage-ledger.json").read_text())


def test_unparseable_body_is_unknown(tmp_path):
    board = _board(tmp_path)
    ledger = ul.refresh_usage_ledger(
        str(board),
        http_get_fn=_http({}, bodies={"claude": "not-json<<<", "codex": b""}),
        claude_creds={"present": True, "token": SECRET, "account": "acct"},
        codex_creds={"present": True, "token": CODEX_SECRET, "account_id": "acct"},
    )
    assert ledger["providers"]["claude"]["usage_state"] == "unknown"
    assert "unparseable" in ledger["providers"]["claude"]["reason"]
    assert ledger["providers"]["codex"]["usage_state"] == "unknown"
    _assert_clean(ul.format_usage_section(ledger))


def test_missing_field_is_unknown(tmp_path):
    board = _board(tmp_path)
    ledger = ul.refresh_usage_ledger(
        str(board),
        http_get_fn=_http({}, bodies={
            "claude": {"five_hour": {"utilization": 10}},
            "codex": {"rate_limit": {"primary_window": {"used_percent": 4}}},
        }),
        claude_creds={"present": True, "token": SECRET, "account": "acct"},
        codex_creds={"present": True, "token": CODEX_SECRET, "account_id": "acct"},
    )
    assert ledger["providers"]["claude"]["usage_state"] == "unknown"
    assert "missing field" in ledger["providers"]["claude"]["reason"]
    assert ledger["providers"]["codex"]["usage_state"] == "unknown"
    assert ledger["providers"]["claude"]["usage_state"] != "ok"


def test_timeout_is_unknown(tmp_path):
    board = _board(tmp_path)
    ledger = ul.refresh_usage_ledger(
        str(board),
        http_get_fn=_http({
            ul.CLAUDE_USAGE_URL: "timeout",
            ul.CODEX_USAGE_URL: "timeout",
        }),
        claude_creds={"present": True, "token": SECRET, "account": "acct"},
        codex_creds={"present": True, "token": CODEX_SECRET, "account_id": "acct"},
    )
    assert ledger["providers"]["claude"]["usage_state"] == "unknown"
    assert ledger["providers"]["claude"]["reason"] == "timeout"
    assert ledger["providers"]["codex"]["reason"] == "timeout"
    _assert_clean(ul.format_usage_section(ledger))


def test_persisted_reading_is_labelled_with_age(tmp_path):
    board = _board(tmp_path)
    ul.refresh_usage_ledger(
        str(board),
        http_get_fn=_http({}),
        claude_creds={"present": True, "token": SECRET, "account": "acct"},
        codex_creds={"present": True, "token": CODEX_SECRET, "account_id": "acct"},
        now_stamp="2026-09-16T03:00:00Z",
    )
    loaded = ul.load_usage_ledger(str(board))
    line = ul.format_provider_line(
        loaded["providers"]["claude"], now_stamp="2026-09-16T03:02:00Z")
    assert "(read 2m ago)" in line
    section = ul.format_usage_section(loaded, now_stamp="2026-09-16T03:02:00Z")
    assert "(read 2m ago)" in section
    _assert_clean(line)


def test_observed_limited_survives_failed_credentialed_read(tmp_path):
    board = _board(tmp_path)
    _write_agent(board, "alice", "claude", limit={
        "at": "2026-09-16T01:00:00Z",
        "until": "1:40am (Asia/Singapore)",
        "note": REJECTION,
        "source": "provider",
        "harness": "claude",
        "reset_at": "2026-09-16T17:40:00Z",
    })
    good = ul.refresh_usage_ledger(
        str(board),
        http_get_fn=_http({}),
        claude_creds={"present": True, "token": SECRET, "account": "acct"},
        codex_creds={"present": True, "token": CODEX_SECRET, "account_id": "acct"},
        now_stamp="2026-09-16T02:00:00Z",
    )
    assert good["providers"]["claude"]["usage_state"] == "ok"
    observed = ul.collect_observed_limits(
        str(board),
        load_agents_fn=lambda _b: [json.loads((board / "agents/alice.json").read_text())],
        load_workforce_fn=lambda _b: json.loads((board / "workforce.json").read_text()),
    )
    assert observed["claude"]["note"] == REJECTION
    failed = ul.refresh_usage_ledger(
        str(board),
        http_get_fn=_http({ul.CLAUDE_USAGE_URL: 401, ul.CODEX_USAGE_URL: 401}),
        claude_creds={"present": True, "token": SECRET, "account": "acct"},
        codex_creds={"present": True, "token": CODEX_SECRET, "account_id": "acct"},
        now_stamp="2026-09-16T03:00:00Z",
        observed=observed,
    )
    claude = failed["providers"]["claude"]
    assert claude["usage_state"] == "limited"
    assert claude["last_limit_rejection"] == REJECTION
    assert claude["windows"]["five_hour"]["percent_used"] == 62
    assert claude["reason"]
    line = ul.format_provider_line(claude)
    assert "limited" in line
    assert "62%" in line
    _assert_clean(line)
    _assert_clean((board / "usage-ledger.json").read_text())


def test_failed_read_does_not_claim_available(tmp_path):
    board = _board(tmp_path)
    ledger = ul.refresh_usage_ledger(
        str(board),
        http_get_fn=_http({ul.CLAUDE_USAGE_URL: 500}),
        claude_creds={"present": True, "token": SECRET, "account": "acct"},
        codex_creds={"present": False, "token": ""},
    )
    assert ledger["providers"]["claude"]["usage_state"] == "unknown"
    assert ledger["providers"]["claude"]["usage_state"] != "available"
    assert "available" not in ul.format_provider_line(ledger["providers"]["claude"])


def test_no_credential_value_in_output_or_log(tmp_path, capsys, caplog):
    board = _board(tmp_path)
    captured = []
    with caplog.at_level("DEBUG"):
        ledger = ul.refresh_usage_ledger(
            str(board),
            http_get_fn=_http({}, captured=captured),
            claude_creds={"present": True, "token": SECRET, "account": "acct"},
            codex_creds={"present": True, "token": CODEX_SECRET, "account_id": "acct"},
        )
        print(ul.format_usage_section(ledger))
        print(ul.format_seat_usage_line("claude", ledger))
        print(json.dumps(ledger))
    out = capsys.readouterr()
    combined = out.out + out.err + caplog.text + (board / "usage-ledger.json").read_text()
    _assert_clean(combined)
    assert SECRET in captured[0]["headers"]["Authorization"]
    assert CODEX_SECRET in captured[1]["headers"]["Authorization"]
    assert captured[0]["url"] == ul.CLAUDE_USAGE_URL
    assert captured[1]["url"] == ul.CODEX_USAGE_URL


def test_account_state_stays_separate_from_usage_state(tmp_path):
    board = _board(tmp_path)
    ledger = ul.refresh_usage_ledger(
        str(board),
        http_get_fn=_http({ul.CLAUDE_USAGE_URL: 401}),
        claude_creds={"present": True, "token": SECRET, "account": "acct"},
        codex_creds={"present": False, "token": ""},
    )
    assert ledger["providers"]["claude"]["account_state"] == "present"
    assert ledger["providers"]["claude"]["usage_state"] == "unknown"
    assert ledger["providers"]["codex"]["account_state"] == "missing"
    assert ledger["providers"]["codex"]["usage_state"] == "unknown"


def test_cursor_and_agy_are_no_data():
    assert ul.format_provider_line(ul.no_data_reading("cursor")) == "cursor no data"
    assert ul.format_provider_line(ul.no_data_reading("agy")) == "agy no data"
    assert ul.format_seat_usage_line("cursor", {"providers": {}}) == "cursor no data"
    assert ul.format_seat_usage_line("antigravity", {"providers": {}}) == "agy no data"


def test_who_and_harness_available_surface_ledger(tmp_path, monkeypatch):
    import importlib.util
    tool = Path(__file__).resolve().parents[1] / "tickets.py"
    spec = importlib.util.spec_from_file_location("tickets_t1040", tool)
    cli = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(cli)

    board = _board(tmp_path)
    _write_agent(board, "alice", "claude", limit={
        "at": "2026-09-16T01:00:00Z",
        "until": "1:40am (Asia/Singapore)",
        "note": REJECTION,
        "source": "provider",
        "harness": "claude",
        "reset_at": "2026-09-16T17:40:00Z",
    })
    monkeypatch.setattr(ul, "live_io_allowed", lambda: False)
    monkeypatch.setattr(cli, "_usage_ledger_mod", lambda: ul)
    monkeypatch.setattr(cli, "_refresh_usage_ledger", lambda _board: ul.refresh_usage_ledger(
        str(board),
        http_get_fn=_http({}),
        claude_creds={"present": True, "token": SECRET, "account": "acct"},
        codex_creds={"present": True, "token": CODEX_SECRET, "account_id": "acct"},
        now_stamp="2026-09-16T03:00:00Z",
        observed=ul.collect_observed_limits(
            str(board),
            load_agents_fn=cli.load_agents,
            load_workforce_fn=cli.load_workforce,
        ),
    ))
    buf = io.StringIO()
    monkeypatch.setattr(cli.sys, "stdout", buf)
    cli.print_recorded_usage(str(board))
    usage = buf.getvalue()
    assert "claude 5h 62% used, resets 03:10" in usage
    assert "7d 41%" in usage
    assert "cursor no data" in usage
    assert "agy no data" in usage
    assert REJECTION in usage
    _assert_clean(usage)

    class Args:
        no_liveness = True

    buf2 = io.StringIO()
    monkeypatch.setattr(cli.sys, "stdout", buf2)
    cli.cmd_who(Args(), str(board))
    who = buf2.getvalue()
    assert "alice" in who
    assert "claude 5h 62% used, resets 03:10" in who
    assert "USAGE LIMIT" in who
    assert REJECTION in usage
    _assert_clean(who)


def test_http_get_default_is_not_used_when_stubbed(tmp_path, monkeypatch):
    def boom(*_a, **_k):
        raise AssertionError("live http_get must not run in tests")

    monkeypatch.setattr(ul, "http_get", boom)
    board = _board(tmp_path)
    ul.refresh_usage_ledger(
        str(board),
        http_get_fn=_http({}),
        claude_creds={"present": True, "token": SECRET, "account": "acct"},
        codex_creds={"present": True, "token": CODEX_SECRET, "account_id": "acct"},
    )


def test_credential_paths_are_the_ones_we_found(tmp_path, monkeypatch):
    home = tmp_path / "home"
    (home / ".claude").mkdir(parents=True)
    (home / ".codex").mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.delenv("CODEX_HOME", raising=False)
    assert ul.claude_credentials_file(str(home)) == str(home / ".claude" / ".credentials.json")
    assert ul.codex_auth_file(str(home)) == str(home / ".codex" / "auth.json")
    assert ul.KEYCHAIN_SERVICE == "Claude Code-credentials"
