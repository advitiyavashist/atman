"""T-857: who/msg share native-wake reachability; leadership offline warn; retired forward."""

import json
import os
import time

import session_adapters as sa

from test_wakeup import board, run  # noqa: F401


def test_who_and_wake_agree_codex_thread_is_not_reachable(board, monkeypatch):
    monkeypatch.delenv("CODEX_APP_SERVER_CONTROL_SOCK", raising=False)
    monkeypatch.setenv("CODEX_HOME", str(board.parent / "codex-home"))
    run(board, "join", "planner", "--roles", "docs", "--harness", "codex")
    sa.write_endpoint(str(board), "planner", {
        "seat": "planner", "provider": "codex", "mode": "native",
        "thread": "thread-live", "pid": os.getpid(), "at": "now",
        "heartbeat_epoch": time.time(),
    })
    assert sa.native_wake_online(str(board), "planner") is False
    who = run(board, "who")
    assert who.returncode == 0, who.stderr
    assert "reachable=yes" not in who.stdout
    assert "reachable=no" in who.stdout
    snap = sa.public_adapter_state(str(board), "planner", "codex", False, True)
    assert snap["adapter_native_online"] is False
    assert snap["adapter_delivery"] == "queued-offline"


def test_master_take_warns_without_live_endpoint(board):
    run(board, "join", "sonnet-cos", "--roles", "ops", "--lifecycle", "persistent")
    taken = run(board, "master", "take", agent="sonnet-cos")
    assert taken.returncode == 0, taken.stderr
    assert "WARNING" in taken.stdout and "no live endpoint" in taken.stdout
    run(board, "master", "cos", "sonnet-cos", agent="sonnet-cos")
    # cos set prints its own warning
    cos = run(board, "master", "cos", "sonnet-cos", agent="sonnet-cos")
    assert "no live endpoint" in cos.stdout


def test_role_take_leadership_warns_without_live_endpoint(board, tmp_path):
    ctx = tmp_path / "handoff.md"
    ctx.write_text("staffing\n")
    run(board, "join", "sonnet-cos", "--roles", "ops", "--lifecycle", "persistent")
    taken = run(
        board, "role", "take", "steer.chief-of-staff",
        "--expected-holder", "none", "--reason", "staffing", "--context", str(ctx),
        agent="sonnet-cos",
    )
    assert taken.returncode == 0, taken.stderr
    assert "no live endpoint" in taken.stdout


def test_msg_to_retired_seat_forwards_to_durable_role_holder(board, tmp_path):
    ctx = tmp_path / "handoff.md"
    ctx.write_text("staffing\n")
    run(board, "join", "old-cos", "--roles", "ops", "--alias", "cos")
    run(board, "join", "new-cos", "--roles", "ops")
    run(board, "master", "take", agent="boss")
    first = run(
        board, "role", "take", "steer.chief-of-staff",
        "--expected-holder", "none", "--reason", "staffing", "--context", str(ctx),
        agent="old-cos",
    )
    assert first.returncode == 0, first.stderr
    retired = run(board, "retire", "old-cos", agent="boss")
    assert retired.returncode == 0, retired.stderr
    second = run(
        board, "role", "take", "steer.chief-of-staff",
        "--expected-holder", "old-cos", "--reason", "replacement", "--context", str(ctx),
        agent="new-cos",
    )
    assert second.returncode == 0, second.stderr
    posted = run(board, "msg", "please review T-857", "--to", "old-cos", "--task", agent="boss")
    assert posted.returncode == 0, posted.stderr
    assert "forward: old-cos -> new-cos" in posted.stdout
    assert "receipt=forwarded" in posted.stdout
    last = json.loads((board / "messages.jsonl").read_text().splitlines()[-1])
    assert last["to"] == "new-cos"
    assert last["forwarded_from"] == "old-cos"
    assert last["delivery"] == "forwarded"
