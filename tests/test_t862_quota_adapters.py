"""T-862: supported quota adapters and honest availability (fixtures only)."""
import importlib.util
import os
import stat
from pathlib import Path

import quota_adapters as qa
from ticket_board.sounding import catalog_dispatch_fail

ROOT = Path(__file__).resolve().parents[1]
TOOL = ROOT / "tickets.py"

CODEX_READ = '{"rateLimits": {"primary": {"used_percent": 20, "resets_at": "2026-09-13T08:00:00Z"}}}'
CLAUDE_STATUS = '{"rate_limits": {"five_hour": {"used_percentage": 25, "resets_at": "2026-09-13T09:00:00Z"}}}'
AGY_STATUS = '{"quota": {"gemini-flash": {"remaining_fraction": 0.4, "reset_time": "2026-09-13T10:00:00Z"}}}'
CURSOR_ADMIN = '{"pooled_usage": {"remaining_percent": 55, "resets_at": "2026-09-13T11:00:00Z"}}'
CODEX_EXHAUSTED = '{"rateLimits": {"primary": {"used_percent": 100, "resets_at": "2026-09-13T12:00:00Z"}}}'
GENERIC_REMAINING_RESET = '{"remaining": "80%", "reset": "2026-09-13T08:00"}'


def load_tickets():
    spec = importlib.util.spec_from_file_location("tickets_t862", TOOL)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def write_exec(path, body):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body)
    path.chmod(path.stat().st_mode | stat.S_IEXEC)
    return path


def test_catalog_is_single_registry():
    mod = load_tickets()
    ids = [row["id"] for row in mod.INTEGRATION_CATALOG]
    assert ids == ["cursor", "agy", "claude", "codex", "devin", "gemini", "grok"]
    by_id = {row["id"]: row for row in mod.INTEGRATION_CATALOG}
    assert by_id["codex"]["quota"] == "supported"
    assert by_id["claude"]["quota"] == "supported"
    assert by_id["agy"]["quota"] == "supported"
    assert by_id["cursor"]["quota"] == "optional-admin"
    for hid in ("devin", "gemini", "grok"):
        assert by_id[hid]["quota"] == "unsupported"


def test_codex_used_percent_becomes_remaining():
    remaining, reset = qa.parse_codex_rate_limits(CODEX_READ)
    assert remaining == "80%"
    assert reset == "2026-09-13T08:00:00Z"
    assert qa.remaining_from_used_percent(20) == "80%"
    assert qa.remaining_from_used_percent(101) is None


def test_claude_statusline_five_hour():
    remaining, reset = qa.parse_claude_statusline(CLAUDE_STATUS)
    assert remaining == "75%"
    assert reset == "2026-09-13T09:00:00Z"


def test_agy_remaining_fraction():
    remaining, reset = qa.parse_agy_quota(AGY_STATUS)
    assert remaining == "40%"
    assert reset == "2026-09-13T10:00:00Z"
    assert qa.remaining_from_fraction(0.4) == "40%"
    assert qa.remaining_from_fraction(1.2) is None


def test_cursor_admin_pooled_usage():
    remaining, reset = qa.parse_cursor_admin(CURSOR_ADMIN)
    assert remaining == "55%"
    assert reset == "2026-09-13T11:00:00Z"


def test_unsupported_providers_ignore_generic_remaining():
    for hid in ("cursor", "grok", "devin", "gemini"):
        assert qa.parse_provider_quota(hid, GENERIC_REMAINING_RESET) == (None, None)


def test_classify_unknown_vs_exhausted_vs_fail():
    assert qa.classify_catalog_usage("codex", None, None)[0] == "unknown"
    assert qa.classify_catalog_usage("cursor", "80%", "soon")[0] == "unknown"
    assert qa.classify_catalog_usage("gemini", "80%", "soon")[0] == "unknown"
    assert qa.classify_catalog_usage("codex", "0%", "soon")[0] == "exhausted"
    assert qa.classify_catalog_usage("codex", "0", "soon")[0] == "exhausted"
    status, reason = qa.classify_catalog_usage(
        "codex", None, None, reason="login required")
    assert status == "FAIL"
    assert "auth" in reason.lower() or "login" in reason.lower()
    assert qa.classify_catalog_usage("codex", None, None, on_disk=False)[0] == "unknown"
    assert qa.is_exhausted(None) is False
    assert qa.is_exhausted("0%") is True


def test_cursor_admin_gate_allows_ok():
    status, _ = qa.classify_catalog_usage(
        "cursor", "55%", "2026-09-13T11:00:00Z", cursor_admin=True)
    assert status == "ok"


def test_catalog_dispatch_fail_unknown_ok_exhausted():
    assert catalog_dispatch_fail({"id": "cursor", "usage": "unknown"}) == ""
    assert catalog_dispatch_fail({"id": "codex", "usage": "ok"}) == ""
    assert catalog_dispatch_fail({"id": "codex", "usage_status": "unknown"}) == ""
    assert "FAIL" in catalog_dispatch_fail({"id": "codex", "usage": "FAIL"})
    assert "exhausted" in catalog_dispatch_fail({"id": "codex", "usage": "exhausted"})


def echo_bin(path, payload):
    return write_exec(path, "#!/bin/sh\necho '%s'\n" % payload)


def test_probe_catalog_usage_codex_adapter(tmp_path):
    mod = load_tickets()
    echo_bin(tmp_path / "codex", CODEX_READ)
    row = {"id": "codex", "on_disk": True, "path": str(tmp_path / "codex")}
    got = mod.probe_catalog_usage(row, env={"PATH": str(tmp_path)})
    assert got["usage"] == "ok"
    assert got["remaining"] == "80%"
    assert got["reset"] == "2026-09-13T08:00:00Z"
    assert catalog_dispatch_fail(got) == ""


def test_probe_catalog_usage_exhausted_blocks(tmp_path):
    mod = load_tickets()
    echo_bin(tmp_path / "codex", CODEX_EXHAUSTED)
    row = {"id": "codex", "on_disk": True, "path": str(tmp_path / "codex")}
    got = mod.probe_catalog_usage(row, env={"PATH": str(tmp_path)})
    assert got["usage"] == "exhausted"
    assert got["remaining"] == "0%"
    assert catalog_dispatch_fail(got)


def test_probe_catalog_usage_auth_is_fail(tmp_path):
    mod = load_tickets()
    write_exec(tmp_path / "codex", "#!/bin/sh\necho 'Error: login required'\n")
    row = {"id": "codex", "on_disk": True, "path": str(tmp_path / "codex")}
    got = mod.probe_catalog_usage(row, env={"PATH": str(tmp_path)})
    assert got["usage"] == "FAIL"
    assert catalog_dispatch_fail(got)


def test_probe_personal_cursor_stays_unknown_even_with_stub(tmp_path):
    mod = load_tickets()
    write_exec(tmp_path / "agent", "#!/bin/sh\necho '%s'\n" % GENERIC_REMAINING_RESET)
    row = {"id": "cursor", "on_disk": True, "path": str(tmp_path / "agent")}
    got = mod.probe_catalog_usage(row, env={"PATH": str(tmp_path)})
    assert got["usage"] == "unknown"
    assert got["remaining"] is None
    assert catalog_dispatch_fail(got) == ""


def test_probe_cursor_admin_env_parses_pooled_usage(tmp_path):
    mod = load_tickets()
    echo_bin(tmp_path / "agent", CURSOR_ADMIN)
    row = {"id": "cursor", "on_disk": True, "path": str(tmp_path / "agent")}
    env = {"PATH": str(tmp_path), "ATMAN_CURSOR_ADMIN_USAGE": "1"}
    got = mod.probe_catalog_usage(row, env=env)
    assert got["usage"] == "ok"
    assert got["remaining"] == "55%"


def test_probe_missing_binary_is_unknown_not_fail():
    mod = load_tickets()
    got = mod.probe_catalog_usage({"id": "codex", "on_disk": False, "path": ""})
    assert got["usage"] == "unknown"
    assert got["usage_reason"] == "not installed"
    assert catalog_dispatch_fail(got) == ""


def test_package_and_root_adapters_match():
    from ticket_board import quota_adapters as packaged
    assert packaged.SUPPORTED_QUOTA == qa.SUPPORTED_QUOTA
    assert packaged.parse_codex_rate_limits(CODEX_READ) == qa.parse_codex_rate_limits(CODEX_READ)
    assert packaged.classify_catalog_usage("gemini", "80%", "soon")[0] == "unknown"
