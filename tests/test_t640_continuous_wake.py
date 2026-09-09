"""T-640: continuous seat messages drive one bounded persistent adapter turn."""

import importlib.util
import json
import os
import shlex
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest

from test_wakeup import board, pending, run  # noqa: F401


TOOL = Path(__file__).resolve().parents[1] / "tickets.py"


def _module():
    spec = importlib.util.spec_from_file_location("tickets_t640", TOOL)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _join(board, name, mode, harness="claude"):
    args = ["join", name, "--roles", "leadership", "--wake-mode", mode,
            "--harness", harness]
    if harness == "custom":
        args += ["--cmd", "cat {prompt_file} >/dev/null"]
    result = run(board, *args, agent=name)
    assert result.returncode == 0, result.stderr
    return result


@pytest.mark.parametrize("harness", ["claude", "codex", "cursor", "custom"])
def test_continuous_policy_is_harness_neutral(board, harness):
    _join(board, "seat", "continuous", harness)
    time.sleep(1.1)
    run(board, "msg", "Please act on the release", "--to", "seat", agent="sender")
    rc, queued = pending(board, "seat")
    assert rc == 0 and queued["wake_reason"] == "task_messages", queued
    assert queued["pending"] is True


@pytest.mark.parametrize("mode", ["task-only", "scheduled"])
def test_noncontinuous_modes_keep_ordinary_dm_notification_only(board, mode):
    _join(board, "seat", mode)
    time.sleep(1.1)
    run(board, "msg", "Please look when convenient", "--to", "seat", agent="sender")
    rc, quiet = pending(board, "seat")
    assert rc == 1 and quiet["pending"] is False, quiet
    assert quiet.get("messages_to_me") and not quiet.get("task_messages")
    run(board, "inbox", agent="seat")
    time.sleep(1.1)
    run(board, "msg", "Run this now", "--to", "seat", "--task", agent="sender")
    assert pending(board, "seat")[0] == 0


def test_existing_master_and_cos_migrate_to_continuous_unless_overridden(board):
    run(board, "join", "master", "--roles", "leadership", agent="master")
    run(board, "join", "cos", "--roles", "leadership", agent="cos")
    run(board, "master", "take", agent="master")
    run(board, "master", "cos", "cos", agent="master")
    mod = _module()
    assert mod.wake_mode_of(str(board), "master") == "continuous"
    assert mod.wake_mode_of(str(board), "cos") == "continuous"

    run(board, "join", "master", "--wake-mode", "task-only", agent="master")
    assert mod.wake_mode_of(str(board), "master") == "task-only"
    time.sleep(1.1)
    run(board, "msg", "ordinary decision note", "--to", "master", agent="cos")
    assert pending(board, "master")[0] == 1


def test_dm_plus_mention_is_one_bounded_wake_and_loops_are_suppressed(board):
    _join(board, "seat", "continuous")
    time.sleep(1.1)
    body = "@seat decide " + ("x" * 5000)
    run(board, "msg", body, "--to", "seat", agent="sender")
    rc, queued = pending(board, "seat")
    assert rc == 0 and len(queued["task_messages"]) == 1, queued
    assert len(queued["task_messages"][0]) <= 320

    run(board, "inbox", agent="seat")
    time.sleep(1.1)
    run(board, "msg", "ACK", "--to", "seat", agent="sender")
    assert pending(board, "seat")[0] == 1
    run(board, "inbox", agent="seat")
    time.sleep(1.1)
    run(board, "msg", "@everyone board pulse", agent="sender")
    assert pending(board, "seat")[0] == 1
    run(board, "msg", "self note", "--to", "seat", agent="seat")
    assert pending(board, "seat")[0] == 1


def test_replayed_delivery_id_does_not_enqueue_a_second_wake(board):
    _join(board, "seat", "continuous")
    time.sleep(1.1)
    run(board, "msg", "@seat one delivery", "--to", "seat", agent="sender")
    message_path = board / "messages.jsonl"
    record = json.loads(message_path.read_text().splitlines()[-1])
    assert record.get("id", "").startswith("msg_")
    with message_path.open("a") as stream:
        stream.write(json.dumps(record) + "\n")  # at-least-once transport replay
    rc, queued = pending(board, "seat")
    assert rc == 0 and len(queued["task_messages"]) == 1, queued
    run(board, "inbox", agent="seat")
    assert pending(board, "seat")[0] == 1


def test_new_directed_tasks_outrank_stale_leadership_stuck_wake(board):
    run(board, "join", "master", "--roles", "leadership", agent="master")
    _join(board, "grok-worker", "continuous", "remote")
    run(board, "join", "blocked-worker", "--roles", "backend", agent="blocked-worker")
    run(board, "master", "take", agent="master")
    run(board, "master", "cos", "grok-worker", agent="master")
    time.sleep(1.1)
    # CoS sees board-wide escalations even when the original DM names master.
    run(board, "msg", "stuck: old escalation", "--to", "master", agent="blocked-worker")
    rc, old = pending(board, "grok-worker")
    assert rc == 0 and old["wake_reason"] == "stuck_messages", old
    old_fingerprint = _module()._watch_trigger_fingerprint(str(board), "grok-worker", old)

    time.sleep(1.1)
    for text in ("Run research A", "Run research B"):
        run(board, "msg", text, "--to", "grok-worker", "--task", agent="master")
    rc, fresh = pending(board, "grok-worker")
    assert rc == 0 and fresh["wake_reason"] == "task_messages", fresh
    assert len(fresh["task_messages"]) == 2
    fresh_fingerprint = _module()._watch_trigger_fingerprint(str(board), "grok-worker", fresh)
    assert fresh_fingerprint != old_fingerprint
    run(board, "msg", "ACK", "--to", "grok-worker", agent="master")
    with_ack = pending(board, "grok-worker")[1]
    assert _module()._watch_trigger_fingerprint(str(board), "grok-worker", with_ack) == fresh_fingerprint
    snap = json.loads(run(board, "ui", "--json", agent="master").stdout)
    grok = next(a for a in snap["agents"] if a["name"] == "grok-worker")
    assert grok["wake_pending"] is True
    assert grok["wake_reason"] == "task_messages"
    assert grok["adapter_state"] == "queued-offline"


def test_concurrent_senders_commit_distinct_wakes_without_corrupting_queue(board):
    _join(board, "seat", "continuous")
    time.sleep(1.1)
    mod = _module()
    gate = threading.Barrier(3)

    def send(sender):
        gate.wait()
        mod.post_message(str(board), sender, "@seat from " + sender, to="seat")

    threads = [threading.Thread(target=send, args=(name,)) for name in ("one", "two")]
    for thread in threads:
        thread.start()
    gate.wait()
    for thread in threads:
        thread.join(timeout=5)
        assert not thread.is_alive()
    rc, queued = pending(board, "seat")
    assert rc == 0 and len(queued["task_messages"]) == 2, queued
    ids = [json.loads(line).get("id") for line in (board / "messages.jsonl").read_text().splitlines()
           if '"from": "one"' in line or '"from": "two"' in line]
    assert len(ids) == 2 and len(set(ids)) == 2


def test_failed_adapter_leaves_visible_queue_and_restart_recovers(board, tmp_path):
    _join(board, "seat", "continuous", "custom")
    time.sleep(1.1)
    run(board, "msg", "recover this request", "--to", "seat", agent="sender")
    failed = run(board, "watch", "--agent", "seat", "--once", "--exec", "exit 7",
                 agent="wrong-ambient")
    assert failed.returncode == 0  # --once reports that work existed; run_end carries exit=7
    snap = json.loads(run(board, "ui", "--json").stdout)
    seat = next(a for a in snap["agents"] if a["name"] == "seat")
    assert seat["wake_pending"] is True and seat["adapter_state"] == "queued-offline"

    helper = tmp_path / "recover.py"
    helper.write_text(
        "import subprocess, sys\n"
        "subprocess.run([sys.executable, sys.argv[1], 'inbox'], check=True)\n"
    )
    recovered = run(board, "watch", "--agent", "seat", "--once", "--exec",
                    shlex.join([sys.executable, str(helper), str(TOOL)]),
                    agent="another-wrong-ambient")
    assert recovered.returncode == 0, recovered.stderr + recovered.stdout
    assert pending(board, "seat")[0] == 1
    ends = [e for e in _events(board, "run_end") if e.get("agent") == "seat"]
    assert [e.get("exit") for e in ends] == [7, 0]


def test_spawn_defaults_follow_durable_mode_not_harness_or_title():
    mod = _module()
    assert mod.spawn_watch_max_runs("continuous") == 0
    assert mod.spawn_watch_max_runs("scheduled") == 0
    assert mod.spawn_watch_max_runs("task-only") == 1
    assert mod.spawn_watch_max_runs("continuous", max_runs=1) == 1
    assert mod.spawn_watch_max_runs("task-only", persist=True) == 0


def test_remote_adapter_never_falls_back_to_a_local_model_and_queue_is_visible(board):
    run(board, "master", "take", agent="master")
    _join(board, "grok-worker", "continuous", "remote")
    run(board, "master", "cos", "grok-worker", agent="master")
    time.sleep(1.1)
    run(board, "msg", "@grok-worker follow up the research", "--to", "grok-worker",
        agent="master")
    attempt = run(board, "spawn", "grok-worker", "--cos", "--worktree", str(board.parent),
                  agent="master")
    assert attempt.returncode != 0
    assert "remote adapter is offline" in attempt.stderr
    assert "Cursor" not in attempt.stderr

    snap = json.loads(run(board, "ui", "--json", agent="master").stdout)
    grok = next(a for a in snap["agents"] if a["name"] == "grok-worker")
    assert grok["harness"] == "remote" and grok["wake_mode"] == "continuous"
    assert grok["wake_pending"] is True and grok["adapter_online"] is False
    assert grok["adapter_state"] == "queued-offline"
    assert "reconnects" in grok["adapter_reason"]

    wrapper = board.parent / "tickets-grok-worker"
    hook = run(board, "hooks", "remote", "--agent", "grok-worker", "--prompt-kind", "cos",
               "--wrapper", str(wrapper), agent="master")
    assert hook.returncode == 0, hook.stderr
    manifest = json.loads(Path(str(wrapper) + ".hooks.json").read_text())
    assert manifest["adapter"]["wake_mode"] == "continuous"
    assert "pending --agent grok-worker --json" in manifest["adapter"]["pending_command"]


def test_ui_marks_multiple_adapter_processes_as_a_lease_conflict(board, monkeypatch):
    _join(board, "seat", "continuous")
    mod = _module()
    monkeypatch.setattr(mod, "_watcher_count", lambda owner, selected_board=None: 2)
    snapshot = mod.board_snapshot(str(board))
    seat = next(a for a in snapshot["agents"] if a["name"] == "seat")
    assert seat["adapter_online"] is False
    assert seat["adapter_state"] == "conflict"
    assert "Multiple adapters" in seat["adapter_reason"]


def _wait_until(predicate, timeout=12):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if predicate():
            return
        time.sleep(0.1)
    assert predicate()


def _events(board, kind=""):
    path = board / "trajectories.jsonl"
    if not path.exists():
        return []
    rows = [json.loads(line) for line in path.read_text().splitlines() if line.strip()]
    return [row for row in rows if not kind or row.get("kind") == kind]


def test_mobile_master_to_fake_remote_cos_to_master_runs_once(board, tmp_path):
    """No model/network: two persistent custom adapters exercise the mobile flow.

    The sender uses both --to and @mention, which must still produce one turn.
    The fake Grok adapter posts an action back; the master adapter consumes it.
    """
    run(board, "join", "mobile-master", "--roles", "leadership",
        "--harness", "custom", "--cmd", "cat {prompt_file} >/dev/null",
        "--wake-mode", "continuous", agent="mobile-master")
    run(board, "join", "grok-worker", "--roles", "leadership",
        "--harness", "custom", "--cmd", "cat {prompt_file} >/dev/null",
        "--wake-mode", "continuous", agent="grok-worker")
    run(board, "master", "take", agent="mobile-master")
    run(board, "master", "cos", "grok-worker", agent="mobile-master")

    receipt = tmp_path / "adapter-receipts.jsonl"
    helper = tmp_path / "fake_remote_adapter.py"
    helper.write_text(
        "import json, os, subprocess, sys\n"
        "tool, receipt = sys.argv[1:]\n"
        "agent = os.environ['TICKET_AGENT']\n"
        "subprocess.run([sys.executable, tool, 'inbox'], check=True)\n"
        "with open(receipt, 'a') as f:\n"
        " f.write(json.dumps({'agent': agent, 'cwd': os.getcwd(), 'ambient': agent}) + '\\n')\n"
        "if agent == 'grok-worker':\n"
        " subprocess.run([sys.executable, tool, 'msg', '@mobile-master Action: research follow-up queued', '--to', 'mobile-master'], check=True)\n"
    )
    command = shlex.join([sys.executable, str(helper), str(TOOL), str(receipt)])
    env = dict(os.environ, TICKETS_DIR=str(board), TICKET_AGENT="wrong-ambient",
               HOME=str(board.parent.parent / "home"))
    master = subprocess.Popen(
        [sys.executable, str(TOOL), "watch", "--agent", "mobile-master", "--persist",
         "--every", "1", "--exec", command, "--cwd", str(board.parent)],
        cwd=str(board.parent), env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    cos = subprocess.Popen(
        [sys.executable, str(TOOL), "watch", "--agent", "grok-worker", "--persist",
         "--every", "1", "--exec", command, "--cwd", str(board.parent)],
        cwd=str(board.parent), env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    try:
        _wait_until(lambda: (board / "agents" / "mobile-master.watch.pid").exists()
                    and (board / "agents" / "grok-worker.watch.pid").exists())
        duplicate = run(board, "watch", "--agent", "grok-worker", "--once", "--dry-run",
                        agent="grok-worker")
        assert duplicate.returncode != 0 and "another watcher" in duplicate.stderr

        time.sleep(1.1)
        posted = run(board, "msg", "@grok-worker Please follow up now", "--to", "grok-worker",
                     agent="mobile-master")
        assert posted.returncode == 0, posted.stderr
        _wait_until(lambda: receipt.exists() and len(receipt.read_text().splitlines()) >= 2)
        # Wait past a second poll to prove neither the DM+mention nor the reply
        # is replayed after each adapter consumed its inbox.
        time.sleep(2.2)
        rows = [json.loads(line) for line in receipt.read_text().splitlines()]
        assert [r["agent"] for r in rows].count("grok-worker") == 1, rows
        assert [r["agent"] for r in rows].count("mobile-master") == 1, rows
        assert all(r["ambient"] == r["agent"] for r in rows)
        assert all(r["cwd"] == str(board.parent) for r in rows)

        starts = _events(board, "run_start")
        ends = _events(board, "run_end")
        assert len([e for e in starts if e.get("agent") == "grok-worker"]) == 1
        assert len([e for e in starts if e.get("agent") == "mobile-master"]) == 1
        assert len([e for e in ends if e.get("agent") in ("grok-worker", "mobile-master")]) == 2
        assert all("cost_usd" not in e for e in ends), "unreported fake cost must stay unmeasured"
        messages = [json.loads(line) for line in (board / "messages.jsonl").read_text().splitlines()]
        assert any(m.get("from") == "grok-worker" and "Action:" in m.get("text", "")
                   and m.get("to") == "mobile-master" for m in messages)
    finally:
        master.terminate()
        cos.terminate()
        master.wait(timeout=10)
        cos.wait(timeout=10)
