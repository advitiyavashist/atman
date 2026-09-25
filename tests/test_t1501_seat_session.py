"""T-1501: headless seat session resume."""
from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

from test_wakeup import TOOL, board, run  # noqa: F401


def _ss():
    path = TOOL.parent / "seat_session.py"
    spec = importlib.util.spec_from_file_location("seat_session_t1501", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _tickets():
    spec = importlib.util.spec_from_file_location("tickets_t1501", TOOL)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_claude_plan_resume_then_fresh():
    ss = _ss()
    first = ss.plan_run("claude", {})
    assert first["mode"] == "fresh"
    assert first["session_id"]
    stored = {"harness": "claude", "id": first["session_id"]}
    second = ss.plan_run("claude", stored)
    assert second["mode"] == "resume"
    assert second["session_id"] == first["session_id"]
    cmd = ss.apply_plan_to_cmd(
        'claude -p "$(atm prompt)" --dangerously-skip-permissions',
        "claude", second)
    assert "--resume %s" % first["session_id"] in cmd
    forced = ss.plan_run("claude", stored, force_fresh=True, fresh_reason="resume failed")
    assert forced["mode"] == "fresh"
    assert "resume failed" in forced["notice"]


def test_resume_failed_detection_and_extract():
    ss = _ss()
    assert ss.resume_failed("claude", "error: session not found", 1) is True
    assert ss.resume_failed("claude", "ok", 0) is False
    assert ss.extract_session_id(
        "claude", '{"session_id": "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"}') == (
        "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee")
    assert ss.extract_session_id(
        "codex", "", env_thread="thread_abc") == "thread_abc"


def test_watch_persists_and_resumes_session(board, tmp_path):
    """Two successful runs: second command line resumes the first session id."""
    tk = _tickets()
    run(board, "join", "doc", "--roles", "docs", "--harness", "claude")
    # Seed a provider session as if a prior run recorded it.
    sid = "11111111-2222-3333-4444-555555555555"
    tk._store_provider_session(str(board), "doc", "claude", sid, mode="resume")
    cmd = 'claude -p "hi" --dangerously-skip-permissions'
    env = {"PATH": "/usr/bin:/bin"}
    new_cmd, new_env, plan = tk._prepare_watch_session(
        str(board), "doc", "claude", cmd, env)
    assert plan["mode"] == "resume"
    assert "--resume %s" % sid in new_cmd
    # Simulate resume failure → visible fresh for next wake.
    tk._finish_watch_session(
        str(board), "doc", "claude", plan,
        "error: cannot resume: session not found", 1, new_env)
    stored = tk._provider_session(str(board), "doc")
    assert stored.get("mode") == "fresh"
    assert "resume failed" in (stored.get("reason") or "")
    # Next plan is fresh (empty id).
    plan2 = _ss().plan_run("claude", stored)
    assert plan2["mode"] == "fresh"
    assert "fresh" in plan2["notice"]


def test_backoff_buffer_message(board):
    """A message posted while a seat is in failed backoff stays pending."""
    import json
    run(board, "join", "doc", "--roles", "docs")
    # Arm a failed adapter_failure in backoff window far in the future.
    rec_path = board / "agents" / "doc.json"
    rec = json.loads(rec_path.read_text())
    rec["adapter_failure"] = {
        "state": "failed", "trigger": "hold", "attempts": 3,
        "identical": 3, "reason": "upstream 503", "kind": "retryable",
        "retry_epoch": 9999999999, "retry_at": "2099-01-01T00:00:00Z",
        "harness": "claude", "provider": "claude",
    }
    rec_path.write_text(json.dumps(rec, indent=2))
    r = run(board, "msg", "do the thing", "--to", "doc", "--task", agent="master")
    assert r.returncode == 0
    # Inbox still has the message (buffered, not dropped).
    inbox = run(board, "inbox", agent="doc")
    assert "do the thing" in inbox.stdout
