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


def test_per_process_and_explicit_identity_survive_another_seat_join(board):
    _join(board, "alice", "continuous")
    _join(board, "grok-worker", "continuous", "remote")
    time.sleep(1.1)
    # A different process joining last must not become a board-wide identity.
    sent = run(board, "msg", "from process env", "--to", "grok-worker", agent="alice")
    assert sent.returncode == 0, sent.stderr
    # An explicit command identity is stronger still and is what pinned hook
    # wrappers use when their launching shell has the wrong ambient identity.
    explicit = run(board, "msg", "from explicit owner", "--to", "grok-worker",
                   "--owner", "alice", agent="wrong-ambient")
    assert explicit.returncode == 0, explicit.stderr
    records = [json.loads(line) for line in (board / "messages.jsonl").read_text().splitlines()]
    assert [record["from"] for record in records[-2:]] == ["alice", "alice"]
    queued = pending(board, "grok-worker")[1]
    assert len(queued["task_messages"]) == 2, queued


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


def test_persistent_local_dispatch_retries_three_times_then_new_wake_recovers(board, tmp_path):
    _join(board, "seat", "continuous", "custom")
    count = tmp_path / "attempts"
    recover = tmp_path / "recover"
    helper = tmp_path / "bounded_retry.py"
    helper.write_text(
        "import os, pathlib, subprocess, sys\n"
        "count, recover, tool = map(pathlib.Path, sys.argv[1:])\n"
        "n = int(count.read_text()) + 1 if count.exists() else 1\n"
        "count.write_text(str(n))\n"
        "if not recover.exists(): raise SystemExit(7)\n"
        "subprocess.run([sys.executable, str(tool), 'inbox'], check=True)\n"
    )
    command = shlex.join([sys.executable, str(helper), str(count), str(recover), str(TOOL)])
    time.sleep(1.1)
    run(board, "msg", "first wake", "--to", "seat", agent="sender")
    env = dict(os.environ, TICKETS_DIR=str(board), TICKET_AGENT="wrong-ambient")
    watcher = subprocess.Popen(
        [sys.executable, str(TOOL), "watch", "--agent", "seat", "--persist",
         "--every", "1", "--exec", command, "--cwd", str(board.parent)],
        cwd=str(board.parent), env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    try:
        def exhausted():
            if not count.exists() or int(count.read_text()) != 3:
                return False
            ui = run(board, "ui", "--json", agent="sender")
            if ui.returncode != 0:
                return False
            seat = next(a for a in json.loads(ui.stdout)["agents"] if a["name"] == "seat")
            return seat["adapter_state"] == "failed" and seat["wake_pending"] is True
        _wait_until(exhausted, timeout=15)
        time.sleep(2.2)
        assert int(count.read_text()) == 3, "terminal failure must not spin paid turns"

        recover.touch()
        run(board, "msg", "new wake after failure", "--to", "seat", agent="sender")
        _wait_until(lambda: count.exists() and int(count.read_text()) == 4 and pending(board, "seat")[0] == 1,
                    timeout=10)
        record = json.loads((board / "agents" / "seat.json").read_text())
        assert "adapter_failure" not in record
    finally:
        watcher.terminate()
        watcher.wait(timeout=10)


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
    assert manifest["schema"] == 2
    assert manifest["adapter"]["wake_mode"] == "continuous"
    assert manifest["adapter"]["protocol"] == "fenced-wake-v1"
    assert "remote register --agent grok-worker" in manifest["adapter"]["register_command"]
    assert "remote next --agent grok-worker" in manifest["adapter"]["next_command"]
    assert "--wait 25" in manifest["adapter"]["next_command"]


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


def _remote(wrapper, *args):
    env = dict(os.environ, TICKET_AGENT="wrong-ambient", TICKETS_DIR="/wrong-board")
    result = subprocess.run([str(wrapper), "remote", *map(str, args)],
                            capture_output=True, text=True, env=env, cwd=str(wrapper.parent))
    stream = result.stdout if result.returncode == 0 else result.stderr
    payload = json.loads(stream.strip().splitlines()[-1]) if stream.strip() else {}
    return result, payload


def _install_remote(board, agent, prompt_kind=""):
    wrapper = board.parent / ("tickets-" + agent)
    args = ["hooks", "remote", "--agent", agent, "--wrapper", str(wrapper)]
    if prompt_kind:
        args += ["--prompt-kind", prompt_kind]
    result = run(board, *args, agent="installer")
    assert result.returncode == 0, result.stderr
    return wrapper


def test_remote_bridge_lease_is_exclusive_fenced_and_reconnects(board):
    _join(board, "grok-worker", "continuous", "remote")
    wrapper = _install_remote(board, "grok-worker", "cos")
    first, lease1 = _remote(wrapper, "register", "--agent", "grok-worker",
                            "--bridge-id", "grok-session", "--ttl", "10")
    assert first.returncode == 0 and lease1["status"] == "online"
    wrong_target, wrong_body = _remote(wrapper, "register", "--agent", "other-seat",
                                        "--bridge-id", "grok-session", "--ttl", "10")
    assert wrong_target.returncode != 0 and "wrapper identity" in wrong_body["error"]
    duplicate, duplicate_body = _remote(wrapper, "register", "--agent", "grok-worker",
                                         "--bridge-id", "grok-session", "--ttl", "10")
    assert duplicate.returncode != 0 and "already registered" in duplicate_body["error"]
    refused, conflict = _remote(wrapper, "register", "--agent", "grok-worker",
                                "--bridge-id", "other-session", "--ttl", "10")
    assert refused.returncode != 0 and "already online" in conflict["error"]
    replaced, lease2 = _remote(wrapper, "register", "--agent", "grok-worker",
                               "--bridge-id", "grok-session", "--ttl", "10", "--replace")
    assert replaced.returncode == 0 and lease2["fence"] == lease1["fence"] + 1
    stale, stale_body = _remote(wrapper, "heartbeat", "--agent", "grok-worker",
                                "--lease-id", lease1["lease_id"], "--fence", lease1["fence"])
    assert stale.returncode != 0 and "stale" in stale_body["error"]
    live, heartbeat = _remote(wrapper, "heartbeat", "--agent", "grok-worker",
                              "--lease-id", lease2["lease_id"], "--fence", lease2["fence"])
    assert live.returncode == 0 and heartbeat["status"] == "online"

    time.sleep(1.1)
    run(board, "msg", "queued while bridge drops", "--to", "grok-worker", agent="sender")
    released, _ = _remote(wrapper, "release", "--agent", "grok-worker",
                           "--lease-id", lease2["lease_id"], "--fence", lease2["fence"])
    assert released.returncode == 0
    reconnected, lease3 = _remote(wrapper, "register", "--agent", "grok-worker",
                                  "--bridge-id", "replacement", "--ttl", "10")
    assert reconnected.returncode == 0 and lease3["fence"] > lease2["fence"]
    _, claimed = _remote(wrapper, "next", "--agent", "grok-worker",
                         "--lease-id", lease3["lease_id"], "--fence", lease3["fence"], "--wait", "0")
    assert claimed["status"] == "wake"


def test_remote_failure_retries_are_bounded_visible_and_manually_recoverable(board):
    _join(board, "grok-worker", "continuous", "remote")
    wrapper = _install_remote(board, "grok-worker", "cos")
    _, lease = _remote(wrapper, "register", "--agent", "grok-worker",
                       "--bridge-id", "grok-session", "--ttl", "20", "--max-attempts", "2")
    time.sleep(1.1)
    run(board, "msg", "retry this", "--to", "grok-worker", agent="sender")

    _, first = _remote(wrapper, "next", "--agent", "grok-worker",
                       "--lease-id", lease["lease_id"], "--fence", lease["fence"], "--wait", "0")
    _remote(wrapper, "start", "--agent", "grok-worker", "--lease-id", lease["lease_id"],
            "--fence", lease["fence"], "--claim-id", first["claim_id"])
    _, failed_once = _remote(wrapper, "end", "--agent", "grok-worker",
                             "--lease-id", lease["lease_id"], "--fence", lease["fence"],
                             "--claim-id", first["claim_id"], "--exit", "7", "--reason", "gateway reset")
    assert failed_once["status"] == "retrying" and failed_once["retry_at"]
    time.sleep(1.1)

    _, second = _remote(wrapper, "next", "--agent", "grok-worker",
                        "--lease-id", lease["lease_id"], "--fence", lease["fence"], "--wait", "0")
    assert second["status"] == "wake" and second["attempt"] == 2
    _remote(wrapper, "start", "--agent", "grok-worker", "--lease-id", lease["lease_id"],
            "--fence", lease["fence"], "--claim-id", second["claim_id"])
    _, exhausted = _remote(wrapper, "end", "--agent", "grok-worker",
                            "--lease-id", lease["lease_id"], "--fence", lease["fence"],
                            "--claim-id", second["claim_id"], "--exit", "7")
    assert exhausted["status"] == "failed" and exhausted["attempt"] == 2
    _, still_failed = _remote(wrapper, "next", "--agent", "grok-worker",
                              "--lease-id", lease["lease_id"], "--fence", lease["fence"], "--wait", "0")
    assert still_failed["status"] == "failed"
    ui = json.loads(run(board, "ui", "--json", agent="sender").stdout)
    grok = next(a for a in ui["agents"] if a["name"] == "grok-worker")
    assert grok["adapter_state"] == "failed" and grok["wake_pending"] is True
    assert grok["adapter_attempts"] == 2

    _, retry = _remote(wrapper, "retry", "--agent", "grok-worker",
                       "--lease-id", lease["lease_id"], "--fence", lease["fence"])
    assert retry["status"] == "retrying" and retry["attempts"] == 0
    _, third = _remote(wrapper, "next", "--agent", "grok-worker",
                       "--lease-id", lease["lease_id"], "--fence", lease["fence"], "--wait", "0")
    assert third["status"] == "wake" and third["attempt"] == 1
    _remote(wrapper, "start", "--agent", "grok-worker", "--lease-id", lease["lease_id"],
            "--fence", lease["fence"], "--claim-id", third["claim_id"])
    subprocess.run([str(wrapper), "inbox"], check=True, capture_output=True, text=True)
    _, complete = _remote(wrapper, "end", "--agent", "grok-worker",
                          "--lease-id", lease["lease_id"], "--fence", lease["fence"],
                          "--claim-id", third["claim_id"], "--exit", "0")
    assert complete["status"] == "completed" and pending(board, "grok-worker")[0] == 1


def test_started_remote_run_requires_explicit_recovery_after_fencing(board):
    _join(board, "grok-worker", "continuous", "remote")
    wrapper = _install_remote(board, "grok-worker", "cos")
    _, lease1 = _remote(wrapper, "register", "--agent", "grok-worker",
                        "--bridge-id", "grok-session", "--ttl", "20")
    time.sleep(1.1)
    run(board, "msg", "recover safely", "--to", "grok-worker", agent="sender")
    _, first = _remote(wrapper, "next", "--agent", "grok-worker",
                       "--lease-id", lease1["lease_id"], "--fence", lease1["fence"], "--wait", "0")
    _remote(wrapper, "start", "--agent", "grok-worker", "--lease-id", lease1["lease_id"],
            "--fence", lease1["fence"], "--claim-id", first["claim_id"])
    _, lease2 = _remote(wrapper, "register", "--agent", "grok-worker",
                        "--bridge-id", "grok-session", "--ttl", "20", "--replace")
    assert lease2["recovery_required"] is True
    stale, stale_body = _remote(wrapper, "end", "--agent", "grok-worker",
                                "--lease-id", lease1["lease_id"], "--fence", lease1["fence"],
                                "--claim-id", first["claim_id"], "--exit", "0")
    assert stale.returncode != 0 and "stale" in stale_body["error"]
    _, blocked = _remote(wrapper, "next", "--agent", "grok-worker",
                         "--lease-id", lease2["lease_id"], "--fence", lease2["fence"], "--wait", "0")
    assert blocked["status"] == "recovery-required"
    _remote(wrapper, "retry", "--agent", "grok-worker",
            "--lease-id", lease2["lease_id"], "--fence", lease2["fence"],
            "--claim-id", first["claim_id"])
    _, replacement = _remote(wrapper, "next", "--agent", "grok-worker",
                              "--lease-id", lease2["lease_id"], "--fence", lease2["fence"], "--wait", "0")
    assert replacement["status"] == "wake" and replacement["attempt"] == 2
    _remote(wrapper, "start", "--agent", "grok-worker", "--lease-id", lease2["lease_id"],
            "--fence", lease2["fence"], "--claim-id", replacement["claim_id"])
    subprocess.run([str(wrapper), "inbox"], check=True, capture_output=True, text=True)
    _remote(wrapper, "end", "--agent", "grok-worker", "--lease-id", lease2["lease_id"],
            "--fence", lease2["fence"], "--claim-id", replacement["claim_id"], "--exit", "0")
    starts = [e for e in _events(board, "run_start") if e.get("agent") == "grok-worker"]
    ends = [e for e in _events(board, "run_end") if e.get("agent") == "grok-worker"]
    assert len(starts) == 2 and len(ends) == 2
    assert sorted(e.get("outcome") for e in ends) == ["completed", "lease_lost"]


def test_leadership_stuck_context_uses_same_five_by_320_bound(board):
    run(board, "join", "master", "--roles", "leadership", agent="master")
    _join(board, "cos", "continuous")
    run(board, "join", "worker", "--roles", "backend", agent="worker")
    run(board, "master", "take", agent="master")
    run(board, "master", "cos", "cos", agent="master")
    time.sleep(1.1)
    for index in range(7):
        run(board, "msg", "stuck: %d %s" % (index, "x" * 5000), "--to", "master", agent="worker")
    queued = pending(board, "cos")[1]
    assert len(queued["stuck_messages"]) == 5
    assert all(len(message) <= 320 for message in queued["stuck_messages"])


def test_mobile_master_to_remote_cos_to_master_runs_once(board):
    """Two protocol bridges exercise mobile -> master -> Grok CoS -> master."""
    run(board, "join", "mobile-master", "--roles", "leadership",
        "--harness", "remote",
        "--wake-mode", "continuous", agent="mobile-master")
    run(board, "join", "grok-worker", "--roles", "leadership",
        "--harness", "remote",
        "--wake-mode", "continuous", agent="grok-worker")
    run(board, "master", "take", agent="mobile-master")
    run(board, "master", "cos", "grok-worker", agent="mobile-master")
    master_wrapper = _install_remote(board, "mobile-master", "master")
    grok_wrapper = _install_remote(board, "grok-worker", "cos")
    _, master_lease = _remote(master_wrapper, "register", "--agent", "mobile-master",
                              "--bridge-id", "mobile-session", "--ttl", "20")
    _, grok_lease = _remote(grok_wrapper, "register", "--agent", "grok-worker",
                            "--bridge-id", "grok-session", "--ttl", "20")
    ui = run(board, "ui", "--json", agent="mobile-master")
    assert ui.returncode == 0, ui.stderr
    snapshot = json.loads(ui.stdout)
    grok_ui = next(a for a in snapshot["agents"] if a["name"] == "grok-worker")
    assert grok_ui["adapter_online"] is True and grok_ui["adapter_bridge_id"] == "grok-session"
    assert "lease_id" not in grok_ui

    time.sleep(1.1)
    run(board, "msg", "@grok-worker Please follow up now", "--to", "grok-worker",
        agent="mobile-master")
    gate = threading.Barrier(3)
    claims = []

    def claim_grok():
        gate.wait()
        claims.append(_remote(grok_wrapper, "next", "--agent", "grok-worker",
                              "--lease-id", grok_lease["lease_id"], "--fence", grok_lease["fence"],
                              "--wait", "0", "--prompt-kind", "cos"))

    threads = [threading.Thread(target=claim_grok) for _ in range(2)]
    for thread in threads:
        thread.start()
    gate.wait()
    for thread in threads:
        thread.join(timeout=10)
    bodies = [body for result, body in claims if result.returncode == 0]
    assert sorted(body["status"] for body in bodies) == ["busy", "wake"], bodies
    grok_claim = next(body for body in bodies if body["status"] == "wake")
    assert grok_claim["wake"]["wake_reason"] == "task_messages"
    assert len(grok_claim["wake"]["task_messages"]) == 1
    assert "CHIEF OF STAFF" in grok_claim["prompt"]
    assert _remote(grok_wrapper, "start", "--agent", "grok-worker",
                   "--lease-id", grok_lease["lease_id"], "--fence", grok_lease["fence"],
                   "--claim-id", grok_claim["claim_id"])[0].returncode == 0
    subprocess.run([str(grok_wrapper), "inbox"], check=True, capture_output=True, text=True)
    subprocess.run([str(grok_wrapper), "msg", "@mobile-master Action: research follow-up queued",
                    "--to", "mobile-master"], check=True, capture_output=True, text=True)
    end_grok, _ = _remote(grok_wrapper, "end", "--agent", "grok-worker",
                          "--lease-id", grok_lease["lease_id"], "--fence", grok_lease["fence"],
                          "--claim-id", grok_claim["claim_id"], "--exit", "0",
                          "--input-tokens", "120", "--output-tokens", "24", "--cost-usd", "0.0123")
    assert end_grok.returncode == 0

    _, master_claim = _remote(master_wrapper, "next", "--agent", "mobile-master",
                              "--lease-id", master_lease["lease_id"], "--fence", master_lease["fence"],
                              "--wait", "0", "--prompt-kind", "master")
    assert master_claim["status"] == "wake"
    _remote(master_wrapper, "start", "--agent", "mobile-master",
            "--lease-id", master_lease["lease_id"], "--fence", master_lease["fence"],
            "--claim-id", master_claim["claim_id"])
    subprocess.run([str(master_wrapper), "inbox"], check=True, capture_output=True, text=True)
    subprocess.run([str(master_wrapper), "msg", "ACK: action received", "--to", "grok-worker"],
                   check=True, capture_output=True, text=True)
    _remote(master_wrapper, "end", "--agent", "mobile-master",
            "--lease-id", master_lease["lease_id"], "--fence", master_lease["fence"],
            "--claim-id", master_claim["claim_id"], "--exit", "0")
    _, quiet = _remote(grok_wrapper, "next", "--agent", "grok-worker",
                       "--lease-id", grok_lease["lease_id"], "--fence", grok_lease["fence"],
                       "--wait", "0")
    assert quiet["status"] == "idle", quiet

    starts = [e for e in _events(board, "run_start")
              if e.get("agent") in ("grok-worker", "mobile-master")]
    ends = [e for e in _events(board, "run_end")
            if e.get("agent") in ("grok-worker", "mobile-master")]
    assert len(starts) == 2 and len(ends) == 2
    grok_end = next(e for e in ends if e["agent"] == "grok-worker")
    assert grok_end["input_tokens"] == 120 and grok_end["output_tokens"] == 24
    assert grok_end["cost_usd"] == pytest.approx(0.0123)
    assert next(e for e in ends if e["agent"] == "mobile-master")["usage_error"] == "unreported"
