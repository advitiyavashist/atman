"""T-1500: seat failure backoff, classification, and auth recovery."""
from __future__ import annotations

import importlib.util
import json
import os
import time
from pathlib import Path

import pytest

from test_wakeup import TOOL, board, run  # noqa: F401


def _sf():
    path = TOOL.parent / "seat_failures.py"
    spec = importlib.util.spec_from_file_location("seat_failures_t1500", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _auth():
    path = TOOL.parent / "auth_v2_contract.py"
    spec = importlib.util.spec_from_file_location("auth_v2_t1500", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_classify_terminal_vs_retryable():
    sf = _sf()
    assert sf.classify_error("codex is not installed", rc=127) == sf.TERMINAL
    assert sf.classify_error("permission denied") == sf.TERMINAL
    assert sf.classify_error("rate limit exceeded", rc=429) == sf.RETRYABLE
    assert sf.classify_error("upstream 503", rc=503) == sf.RETRYABLE
    assert sf.classify_error("connection timed out") == sf.RETRYABLE
    assert sf.classify_error("weird exit", rc=1) == sf.RETRYABLE
    assert sf.classify_error("", auth_state="unavailable") == sf.TERMINAL
    assert sf.classify_error("", auth_state="network") == sf.RETRYABLE


def test_backoff_1_to_30_and_identical_stop():
    sf = _sf()
    assert sf.backoff_seconds(1) == 1
    assert sf.backoff_seconds(2) == 2
    assert sf.backoff_seconds(3) == 4
    assert sf.backoff_seconds(6) == 30
    assert sf.backoff_seconds(99) == 30
    first = sf.next_failure_record({}, reason="upstream 503", rc=503, clock=1000.0)
    assert first["state"] == "retrying"
    assert first["identical"] == 1
    assert first["retry_epoch"] == 1001.0
    second = sf.next_failure_record(first, reason="upstream 503", rc=503, clock=1001.0)
    assert second["identical"] == 2
    assert second["state"] == "retrying"
    third = sf.next_failure_record(second, reason="upstream 503", rc=503, clock=1005.0)
    assert third["identical"] == 3
    assert third["state"] == "failed"
    assert third["retry_epoch"] == 0
    # Different error resets the identical counter.
    reset = sf.next_failure_record(third, reason="upstream 502", rc=502, clock=1010.0)
    assert reset["identical"] == 1
    assert reset["state"] == "retrying"
    # Terminal stops immediately.
    term = sf.next_failure_record({}, reason="codex is not installed", rc=127, clock=1.0)
    assert term["kind"] == sf.TERMINAL
    assert term["state"] == "failed"
    plain = sf.plain_failure_status(third, now_epoch=1010.0)
    assert "stopped after 3 identical" in plain


def test_fresh_ready_outranks_stored_unavailable():
    auth = _auth()
    host = {
        "hostname": "mac.local", "username": "op", "runner_kind": "host",
        "agent_id": "doc", "ticket_agent": "doc",
        "argv0": "codex", "binary": "/missing/codex",
        "runner_id": "old", "worktree": "/steer", "repo_root": "/steer",
        "origin_url": "https://example.com/steer.git",
        "expected_origin": "https://example.com/steer.git",
        "env_fingerprint": "abc", "lifecycle": "persistent",
    }
    # Make contexts complete enough for is_authoritative / fence helpers.
    for key in ("head",):
        host.setdefault(key, "")
    stored = {
        "state": "unavailable",
        "authoritative": True,
        "at": "2026-09-24T00:00:00Z",
        "detail": "codex is not installed",
        "execution_context": dict(host),
        "harness": "codex",
    }
    ready_ctx = dict(host)
    ready_ctx["binary"] = "/opt/homebrew/bin/codex"
    ready_ctx["runner_id"] = "new"
    ready_ctx["worktree"] = "/atman/.worktrees/doc"  # drifted worktree (T-1490 pin)
    incoming = {
        "state": "ready",
        "authoritative": True,
        "at": "2026-09-25T00:00:00Z",
        "detail": "ok",
        "execution_context": ready_ctx,
        "harness": "codex",
        "status_cmd": "codex login status",
        "login_cmd": "codex login",
    }
    runner = dict(ready_ctx)
    assert auth._fresh_ready_replaces_stored_failure(stored, incoming, runner)
    merged = auth.merge_auth_check(stored, incoming, runner)
    assert merged["state"] == "ready"
    assert merged["authoritative"] is True


def test_sandbox_ready_cannot_overwrite_host_failure():
    auth = _auth()
    host = {
        "hostname": "mac.local", "username": "op", "runner_kind": "host",
        "agent_id": "doc", "ticket_agent": "doc",
        "argv0": "codex", "binary": "/missing/codex",
        "runner_id": "old", "worktree": "/steer", "repo_root": "/steer",
        "origin_url": "https://example.com/steer.git",
        "expected_origin": "https://example.com/steer.git",
        "env_fingerprint": "abc", "lifecycle": "persistent", "head": "",
    }
    stored = {
        "state": "unavailable", "authoritative": True,
        "at": "2026-09-24T00:00:00Z", "detail": "codex is not installed",
        "execution_context": dict(host), "harness": "codex",
    }
    sand = dict(host)
    sand["runner_kind"] = "sandbox"
    sand["hostname"] = "sandbox"
    incoming = {
        "state": "ready", "authoritative": True,
        "at": "2026-09-25T00:00:00Z", "detail": "ok",
        "execution_context": sand, "harness": "codex",
        "status_cmd": "codex login status", "login_cmd": "codex login",
    }
    merged = auth.merge_auth_check(stored, incoming, sand)
    assert merged["state"] == "unavailable"


def test_spawn_missing_binary_then_restore(board, tmp_path, monkeypatch):
    """Remove harness binary → terminal spawn fail; restore → next spawn ok."""
    bindir = tmp_path / "bin"
    bindir.mkdir()
    fake = bindir / "codex"
    fake.write_text("#!/bin/sh\necho logged-in\nexit 0\n")
    fake.chmod(0o755)
    monkeypatch.setenv("PATH", "%s:/usr/bin:/bin" % bindir)
    run(board, "join", "doc", "--roles", "docs", "--harness", "codex")
    # Hide binary
    hidden = bindir / "codex.hidden"
    fake.rename(hidden)
    r = run(board, "spawn", "doc", "--every", "5", "--persist", agent="master",
            env={"PATH": "%s:/usr/bin:/bin" % bindir})
    assert r.returncode != 0
    assert "not installed" in (r.stdout + r.stderr).lower() or "terminal" in (r.stdout + r.stderr).lower()
    rec = json.loads((board / "agents" / "doc.json").read_text())
    fail = rec.get("adapter_failure") or {}
    assert fail.get("kind") == "terminal" or fail.get("state") == "failed"
    # Restore binary — must succeed without retiring the seat.
    hidden.rename(fake)
    r2 = run(board, "spawn", "doc", "--exec", "true", "--every", "5", "--persist",
             agent="master", env={"PATH": "%s:/usr/bin:/bin" % bindir})
    assert r2.returncode == 0, r2.stdout + r2.stderr
    assert "watcher for doc started" in r2.stdout or "desired=active" in r2.stdout
    run(board, "spawn", "doc", "--stop", agent="master")
