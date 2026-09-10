"""T-685 contract gates: context match, opaque profiles, repo identity."""

import json
import stat
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from auth_v2_contract import (  # noqa: E402
    AUTH_STATES,
    PROFILE_KINDS,
    alert_id,
    dumps_board_safe,
    is_authoritative,
    merge_auth_check,
    normalize_git_origin,
    pause_policy,
    profile_store_path,
    redact_auth_check,
    spawn_repo_identity_ok,
    validate_auth_check,
)

ATMAN = "https://github.com/advitiyavashist/atman.git"
ATMAN_SSH = "git@github.com:advitiyavashist/atman.git"
STEER = "https://github.com/advitiyavashist/steer.git"


def runner_ctx(**over):
    base = {
        "runner_id": "rnr_host_1",
        "runner_kind": "host",
        "hostname": "mac-1",
        "username": "kavana",
        "binary": "/opt/homebrew/bin/agent",
        "argv0": "agent",
        "env_fingerprint": "path",
        "repo_root": "/Users/kavana/Downloads/atman",
        "origin_url": ATMAN,
        "head": "920644ca10e8d3f4166769c631e7940829248cf0",
        "lifecycle": "persistent",
        "agent_id": "atman-auth-v2",
    }
    base.update(over)
    return base


def sandbox_ctx():
    return runner_ctx(
        runner_id="rnr_sandbox",
        runner_kind="sandbox",
        hostname="sandbox",
        binary="/usr/bin/agent",
        repo_root="/tmp/sandbox",
    )


def v2_ready(ctx=None):
    ctx = ctx or runner_ctx()
    return {
        "state": "ready",
        "harness": "cursor",
        "at": "2026-09-10T01:00:00Z",
        "exit": 0,
        "detail": "Logged in",
        "identity_label": "dev@example.test",
        "status_cmd": "agent status",
        "login_cmd": "agent login",
        "credential_profile_ref": "prf_cursor_browser_1",
        "profile_kind": "browser",
        "execution_context": {
            "runner_id": ctx["runner_id"],
            "runner_kind": ctx["runner_kind"],
            "hostname": ctx["hostname"],
            "username": ctx.get("username") or "kavana",
            "binary": ctx["binary"],
            "argv0": ctx.get("argv0") or "agent",
            "env_fingerprint": ctx.get("env_fingerprint") or "path",
            "worktree": "/Users/kavana/Downloads/atman/.worktrees/atman-auth-v2",
            "repo_root": ctx["repo_root"],
            "origin_url": ctx["origin_url"],
            "expected_origin": "advitiyavashist/atman",
            "head": ctx["head"],
            "agent_id": ctx.get("agent_id") or "atman-auth-v2",
            "ticket_agent": ctx.get("agent_id") or "atman-auth-v2",
        },
    }


def test_all_seven_states_and_provider_kinds_are_frozen():
    assert AUTH_STATES == (
        "ready", "login_required", "expired", "quota",
        "network", "unavailable", "unsupported",
    )
    assert PROFILE_KINDS["cursor"] == ("browser", "api_key", "auth_token")
    assert PROFILE_KINDS["claude"] == ("subscription", "api_key")
    assert PROFILE_KINDS["codex"] == ("chatgpt", "api_key")
    assert PROFILE_KINDS["remote"] == ("adapter",)
    assert PROFILE_KINDS["custom"] == ("adapter",)


def test_v2_record_validates_and_legacy_t610_blob_is_not_authoritative():
    rec = v2_ready()
    assert validate_auth_check(rec) == []
    legacy = {
        "state": "login_required",
        "harness": "cursor",
        "detail": "Not logged in",
        "login_cmd": "agent login",
        "status_cmd": "agent status",
    }
    assert "execution_context is required" in ";".join(validate_auth_check(legacy))
    assert is_authoritative(legacy, runner_ctx()) is False


def test_sandbox_ready_does_not_clobber_authoritative_host_quota():
    """P0: non-matching ready must not replace any authoritative non-ready blob."""
    host = runner_ctx()
    stored = v2_ready(host)
    stored["state"] = "quota"
    stored["detail"] = "usage limit"
    stored = merge_auth_check({}, stored, host)
    assert stored["state"] == "quota" and stored["authoritative"] is True
    incoming = v2_ready(sandbox_ctx())
    incoming["state"] = "ready"
    incoming["detail"] = "Logged in (sandbox)"
    merged = merge_auth_check(stored, incoming, host)
    assert merged["state"] == "quota"
    assert merged["detail"] == "usage limit"
    assert merged["authoritative"] is True


@pytest.mark.parametrize("host_state", [
    "ready", "login_required", "expired", "quota", "network",
    "unavailable", "unsupported",
])
def test_nonmatching_probe_never_replaces_any_authoritative_blob(host_state):
    host = runner_ctx()
    stored = v2_ready(host)
    stored["state"] = host_state
    stored = merge_auth_check({}, stored, host)
    incoming = v2_ready(sandbox_ctx())
    incoming["state"] = "login_required"
    incoming["detail"] = "Not logged in"
    merged = merge_auth_check(stored, incoming, host)
    assert merged["state"] == host_state
    assert merged["authoritative"] is True


def test_sandbox_login_required_does_not_clobber_host_ready():
    host = runner_ctx()
    stored = merge_auth_check({}, v2_ready(host), host)
    assert stored["state"] == "ready" and stored["authoritative"] is True
    sand = sandbox_ctx()
    incoming = v2_ready(sand)
    incoming["state"] = "login_required"
    incoming["detail"] = "Not logged in"
    merged = merge_auth_check(stored, incoming, host)
    assert merged["state"] == "ready"
    assert merged["identity_label"] == "dev@example.test"


def test_matching_host_login_required_does_replace_ready():
    host = runner_ctx()
    stored = merge_auth_check({}, v2_ready(host), host)
    incoming = v2_ready(host)
    incoming["state"] = "expired"
    incoming["detail"] = "token expired"
    merged = merge_auth_check(stored, incoming, host)
    assert merged["state"] == "expired"
    assert merged["authoritative"] is True
    assert merged["pause"]["retry_model"] is False
    assert merged["pause"]["retain_queue"] is True
    assert merged["alert_id"] == alert_id("atman-auth-v2", "expired", "prf_cursor_browser_1")


def test_alert_id_is_stable_across_polls():
    a = alert_id("atman-auth-v2", "login_required", "prf_x")
    b = alert_id("atman-auth-v2", "login_required", "prf_x")
    assert a == b
    assert a != alert_id("atman-auth-v2", "quota", "prf_x")


def test_ephemeral_failed_preflight_still_does_not_retry_model():
    pol = pause_policy("ephemeral", "login_required")
    assert pol["retry_model"] is False
    assert pol["paused"] is False
    assert pause_policy("persistent", "login_required")["paused"] is True


def test_persistent_unsupported_is_no_spend_and_not_login():
    """P1: persistent+unsupported pauses without retrying a model or login_cmd."""
    pol = pause_policy("persistent", "unsupported")
    assert pol["paused"] is True
    assert pol["retry_model"] is False
    assert pol["retain_queue"] is True
    assert pol["dedupe_alert"] is True
    assert pol["operator_path"] == "unsupported"
    rec = v2_ready()
    rec["harness"] = "remote"
    rec["profile_kind"] = "adapter"
    rec["state"] = "unsupported"
    rec["login_cmd"] = ""
    rec["credential_profile_ref"] = "prf_remote_adapter_1"
    merged = merge_auth_check({}, rec, runner_ctx())
    assert merged["pause"]["retry_model"] is False
    assert merged["pause"]["operator_path"] == "unsupported"
    assert merged["login_cmd"] == ""
    eph = pause_policy("ephemeral", "unsupported")
    assert eph["paused"] is False
    assert eph["retry_model"] is False
    assert eph["operator_path"] == "unsupported"


def test_username_or_env_fingerprint_change_is_mismatch():
    """Different OS user or credential-relevant env is not the enrolled runner."""
    from auth_v2_contract import (
        AUTH_CONTEXT_IDENTITY_FIELDS,
        contexts_match,
        preflight_failures,
    )
    assert AUTH_CONTEXT_IDENTITY_FIELDS == (
        "runner_id", "runner_kind", "hostname", "username", "binary",
        "argv0", "env_fingerprint", "repo_root", "origin_url", "agent_id",
    )
    assert "head" not in AUTH_CONTEXT_IDENTITY_FIELDS
    host = runner_ctx()
    other_user = runner_ctx(username="other")
    other_env = runner_ctx(env_fingerprint="homebrew-path")
    other_argv = runner_ctx(argv0="cursor-agent")
    assert contexts_match(host, other_user) is False
    assert contexts_match(host, other_env) is False
    assert contexts_match(host, other_argv) is False
    assert "runner_mismatch" in preflight_failures(
        "atman-auth-v2", "atman-auth-v2", "advitiyavashist/atman",
        ATMAN, ATMAN, other_user, host)
    stored = merge_auth_check({}, v2_ready(host), host)
    assert stored["state"] == "ready" and stored["authoritative"] is True
    for probe_ctx in (other_user, other_env):
        incoming = v2_ready(probe_ctx)
        incoming["state"] = "login_required"
        incoming["detail"] = "Not logged in"
        merged = merge_auth_check(stored, incoming, host)
        assert merged["state"] == "ready"
        assert merged["authoritative"] is True
        assert merged["detail"] == "Logged in"


def test_head_change_is_not_runner_mismatch():
    """Ordinary git HEAD motion is not auth authority."""
    from auth_v2_contract import contexts_match, preflight_failures
    a = runner_ctx(head="920644ca10e8d3f4166769c631e7940829248cf0")
    b = runner_ctx(head="aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa")
    assert contexts_match(a, b) is True
    assert preflight_failures(
        "atman-auth-v2", "atman-auth-v2", "advitiyavashist/atman",
        ATMAN, ATMAN, a, b) == []
    stored = merge_auth_check({}, v2_ready(a), b)
    assert stored["authoritative"] is True
    incoming = v2_ready(b)
    incoming["state"] = "quota"
    merged = merge_auth_check(stored, incoming, a)
    assert merged["state"] == "quota"
    assert merged["authoritative"] is True


def test_validate_requires_ticket_agent_equals_agent_id():
    rec = v2_ready()
    rec["execution_context"]["ticket_agent"] = "cursor"
    errs = validate_auth_check(rec)
    assert any("ticket_agent" in e for e in errs)
    rec["execution_context"]["ticket_agent"] = "atman-auth-v2"
    assert validate_auth_check(rec) == []
    rec["execution_context"]["ticket_agent"] = ""
    assert any("ticket_agent" in e for e in validate_auth_check(rec))


@pytest.mark.parametrize("url", [ATMAN, ATMAN_SSH, "ssh://git@github.com/advitiyavashist/atman.git"])
def test_origin_normalization_collapses_github_urls(url):
    assert normalize_git_origin(url) == "advitiyavashist/atman"
    assert normalize_git_origin(STEER) == "advitiyavashist/steer"


def test_wrong_repository_spawn_incident_is_rejected():
    """Steer git root + Atman-looking path must not satisfy Atman identity."""
    expected = "advitiyavashist/atman"
    assert spawn_repo_identity_ok(
        expected, STEER, STEER, worktree_exists=False) is False
    assert spawn_repo_identity_ok(
        expected, ATMAN, STEER, worktree_exists=True) is False
    # Shared Steer board is fine when spawn root and worktree are Atman.
    assert spawn_repo_identity_ok(
        expected, ATMAN_SSH, ATMAN, worktree_exists=True) is True
    assert spawn_repo_identity_ok(
        expected, "", ATMAN, worktree_exists=False) is True


def test_origin_mismatch_on_record_is_a_contract_error():
    rec = v2_ready()
    rec["execution_context"]["origin_url"] = STEER
    errs = validate_auth_check(rec)
    assert any("origin_url" in e for e in errs)


def test_secrets_never_survive_redaction_or_board_dump():
    dirty = v2_ready()
    dirty["api_key"] = "sk-live-this-must-not-be-stored-anywhere-ok"
    dirty["token"] = "tok_" + ("x" * 40)
    dirty["execution_context"]["authorization"] = "Bearer super-secret-value-here-ok"
    clean = redact_auth_check(dirty)
    blob = dumps_board_safe(dirty)
    assert "api_key" not in clean
    assert "token" not in clean
    assert "authorization" not in json.dumps(clean)
    assert "sk-live" not in blob
    assert "prf_cursor_browser_1" in blob
    assert validate_auth_check(dirty)  # forbidden keys


def test_profile_store_is_outside_git_and_private_mode():
    path = profile_store_path("/tmp/atman-cache", "boardhash", "prf_abc")
    assert path.endswith("credentials/boardhash/prf_abc.json")
    assert ".tickets" not in path
    with pytest.raises(ValueError):
        profile_store_path("/tmp/atman-cache", "boardhash", "not-opaque")
    # Documented modes (T-683 class): dir 0700, file 0600.
    from auth_v2_contract import PROFILE_DIR_MODE, PROFILE_STORE_MODE
    assert PROFILE_DIR_MODE == 0o700
    assert PROFILE_STORE_MODE == stat.S_IRUSR | stat.S_IWUSR


def test_adapter_without_declared_check_is_unsupported_not_login_required():
    rec = v2_ready()
    rec["harness"] = "remote"
    rec["profile_kind"] = "adapter"
    rec["state"] = "unsupported"
    rec["login_cmd"] = ""
    rec["credential_profile_ref"] = "prf_remote_adapter_1"
    assert validate_auth_check(rec) == []
    rec["profile_kind"] = "browser"
    assert any("profile_kind" in e for e in validate_auth_check(rec))


def test_generic_cursor_child_claiming_named_seat_fails_preflight():
    """Worktree can be correct Atman@920644c and still fail: TICKET_AGENT=cursor."""
    from auth_v2_contract import mismatch_auth_check, preflight_failures
    host = runner_ctx()
    reasons = preflight_failures(
        enrolled_agent="atman-auth-v2",
        ticket_agent="cursor",
        expected_origin="advitiyavashist/atman",
        worktree_origin=ATMAN,
        spawn_git_root_origin=ATMAN,
        probe_ctx=host,
        runner_ctx=host,
    )
    assert reasons == ["seat_mismatch"]
    rec = mismatch_auth_check(reasons)
    assert rec["state"] == "unavailable"
    assert rec["state"] != "login_required"
    assert "seat_mismatch" in rec["detail"]
    assert preflight_failures(
        "atman-auth-v2", "atman-auth-v2", "advitiyavashist/atman",
        ATMAN, ATMAN, host, host) == []


def test_t610_suite_still_imports_without_v2_probe_wiring():
    """T-685 must not implement T-686 spawn/probe behavior in tickets.py."""
    text = (ROOT / "tickets.py").read_text()
    assert "auth_v2_contract" not in text
    assert "def harness_auth_probe" in text
