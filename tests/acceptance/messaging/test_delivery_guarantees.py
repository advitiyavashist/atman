"""At-least-once delivery, exactly-once-or-reviewed execution.

Design doc: "Duplicate delivery/reconnect produces one active run; busy agent
has no parallel run. Offline and hook-only connections show the correct
reason. ... restart between claim and spawn."
"""

from __future__ import annotations

import pytest

from .live_board import FakeLauncher, FakeProcess, rid


def _channel_task(board, agent, **kw):
    work = board.channel("work-" + rid()[:6])
    source = board.say(board.operator, work["id"], "task incoming")
    task = board.task(board.operator, source.body["message"]["id"],
                      agent.agent_id, **kw)
    assert task.status == 201, task.body
    return work, source.body["message"], task.body


# ------------------------------------------------------- duplicate delivery

def test_a_retried_send_task_wakes_the_agent_once(board):
    """The operator's client retries `POST /messages/{id}/task` with the same
    request_id after a lost 201. One message, one wake job, one run."""
    agent = board.enroll("claude-a")
    work = board.channel("work")
    source = board.say(board.operator, work["id"], "please").body["message"]
    request_id = rid()
    first = board.task(board.operator, source["id"], agent.agent_id,
                       request_id=request_id)
    second = board.task(board.operator, source["id"], agent.agent_id,
                        request_id=request_id)
    assert first.status == second.status == 201
    assert first.body["wake_job"]["id"] == second.body["wake_job"]["id"]
    assert first.body["message"]["id"] == second.body["message"]["id"]

    launcher = FakeLauncher()
    outcomes = board.supervisor(agent, launcher=launcher).run_forever(
        wait_seconds=0, max_polls=3)
    assert [o.state for o in outcomes] == ["responded"]
    assert len(launcher.specs) == 1
    assert board.store.conn.execute(
        "SELECT COUNT(*) FROM runs WHERE recipient_agent_id = ?",
        (agent.agent_id,)).fetchone()[0] == 1


def test_a_redelivered_wake_job_is_recognised_and_not_executed_twice(board):
    """Reconnect replay: the board hands the same wake job out again (its
    lease lapsed after the run finished but before the job was closed). The
    supervisor's local ledger recognises the dedupe key and launches nothing."""
    agent = board.enroll("claude-a")
    _, _, task = _channel_task(board, agent)
    launcher = FakeLauncher()
    sup = board.supervisor(agent, launcher=launcher)
    first = sup.run_forever(wait_seconds=0, max_polls=1)
    assert [o.state for o in first] == ["responded"]

    # Force the redelivery the board would produce after a lost close-out.
    board.store.conn.execute(
        "UPDATE wake_jobs SET state = 'pending', lease_expires_at = NULL"
        " WHERE id = ?", (task["wake_job"]["id"],))
    board.store.conn.execute(
        "UPDATE runs SET state = 'pending', version = 1 WHERE wake_job_id = ?",
        (task["wake_job"]["id"],))
    board.store.conn.commit()

    again = sup.run_forever(wait_seconds=0, max_polls=1)
    assert [o.state for o in again] == ["skipped"], again
    assert len(launcher.specs) == 1, "a duplicate delivery must not spawn"


# ----------------------------------------------------------- crash replay

def test_a_restart_between_claim_and_spawn_is_reviewed_not_rerun(board):
    """The supervisor dies after retaining the run and claiming the ticket but
    before the process exists. The next supervisor reconciles: the run is
    FAILED with 'uncertain' stated, the job goes to the failed queue, and no
    second process is started. It is NOT silently re-run."""
    agent = board.enroll("claude-a")
    _, _, task = _channel_task(board, agent)

    class DiesBeforeSpawn(Exception):
        pass

    def crash(spec):
        raise DiesBeforeSpawn()

    crashing = FakeLauncher(on_start=crash)
    first = board.supervisor(agent, launcher=crashing)
    first.register()
    with pytest.raises(DiesBeforeSpawn):
        first.poll_once(wait_seconds=1)
    # The durable in-flight record was written before the spawn attempt.
    run_id = "run_" + task["wake_job"]["id"][4:]
    assert board.run(run_id)["state"] == "starting"

    replacement = FakeLauncher()
    second = board.supervisor(agent, launcher=replacement,
                              state_dir=first.state_dir)
    outcomes = second.run_forever(wait_seconds=0, max_polls=2)
    assert [o.state for o in outcomes] == ["failed"], outcomes
    assert "uncertain" in (outcomes[0].reason or "").lower()
    run = board.run(run_id)
    assert run["state"] == "failed"
    assert "uncertain" in (run["terminal_reason"] or "").lower()
    assert board.wake_job(task["wake_job"]["id"])["state"] == "failed"
    assert replacement.specs == [], "the reconciled run must not be re-spawned"


def test_a_supervisor_that_finds_the_previous_process_alive_does_not_double_start(board):
    """Interactive/managed session collision, the structural half: a state file
    naming a live pid means somebody else still owns that session. The new
    supervisor refuses to start a second process for it."""
    import os

    agent = board.enroll("claude-a")
    _, _, task = _channel_task(board, agent)
    sup = board.supervisor(agent)
    sup.register()
    # Simulate a predecessor that spawned and is still running: our own pid
    # is unarguably alive.
    from ticket_board.runners.state import InFlight, board_session_id, new_runtime_session_id
    runtime = new_runtime_session_id()
    run_id = "run_" + task["wake_job"]["id"][4:]
    board.runner_client(agent).jobs(sup.state.runner_id, wait_seconds=0)
    board.runner_client(agent).run_event(run_id, "starting", expected_version=1,
                                         session_id=board_session_id(runtime))
    sup.state.in_flight = InFlight(run_id=run_id, wake_job_id=task["wake_job"]["id"],
                                   dedupe_key=task["wake_job"]["dedupe_key"],
                                   session_id=board_session_id(runtime),
                                   runtime_session_id=runtime,
                                   ticket_id=task["ticket"]["id"],
                                   pid=os.getpid(), spawned=True)
    sup._save()

    fresh = FakeLauncher()
    successor = board.supervisor(agent, launcher=fresh, state_dir=sup.state_dir)
    outcomes = successor.run_forever(wait_seconds=0, max_polls=1)
    assert outcomes and outcomes[0].state == "failed"
    assert "alive" in outcomes[0].reason and "not started twice" in outcomes[0].reason
    assert fresh.specs == []


def test_a_fresh_job_is_never_started_with_resume(board):
    """The other half of the collision rule: a managed session is minted, not
    adopted. Every first turn is `--session-id <new uuid>`, never `--resume`."""
    # Two claims are needed for two runs; see F-5 for what happens at 1.
    agent = board.enroll("claude-a", max_active_tickets=2)
    _channel_task(board, agent)
    _channel_task(board, agent)
    launcher = FakeLauncher()
    outcomes = board.supervisor(agent, launcher=launcher).run_forever(
        wait_seconds=0, max_polls=2)
    assert [o.state for o in outcomes] == ["responded", "responded"]
    assert all(spec.resume is False for spec in launcher.specs)
    assert len({spec.session_id for spec in launcher.specs}) == 2


# ------------------------------------------------------------ busy / offline

def test_a_busy_agent_has_no_parallel_run_and_the_second_task_waits(board):
    agent = board.enroll("claude-a")
    _, _, first = _channel_task(board, agent)
    _, _, second = _channel_task(board, agent)

    client = board.runner_client(agent)
    sup = board.supervisor(agent)
    sup.register()
    leased = client.jobs(sup.state.runner_id, wait_seconds=0)["items"]
    assert [j["id"] for j in leased] == [first["wake_job"]["id"]]
    run_id = "run_" + first["wake_job"]["id"][4:]
    from ticket_board.runners.state import board_session_id, new_runtime_session_id
    ses = board_session_id(new_runtime_session_id())
    run = client.run_event(run_id, "starting", expected_version=1, session_id=ses)
    run = client.run_event(run_id, "started", expected_version=run["version"],
                           session_id=ses)
    assert run["state"] == "running"

    # While that run is live, the board hands out nothing more for this agent.
    assert client.jobs(sup.state.runner_id, wait_seconds=0)["items"] == []
    assert board.wake_job(second["wake_job"]["id"])["state"] == "pending"

    client.run_event(run_id, "responded", expected_version=run["version"])
    nxt = client.jobs(sup.state.runner_id, wait_seconds=0)["items"]
    assert [j["id"] for j in nxt] == [second["wake_job"]["id"]]


@pytest.mark.xfail(strict=True, reason=(
    "F-5: a managed agent can execute exactly ONE new-ticket task. Its run "
    "responds but the ticket stays `claimed` (completion needs review), so with "
    "the design's concurrency=1 (`max_active_tickets: 1`) the NEXT task's claim "
    "answers 409 capacity_exhausted, the supervisor reports the run failed "
    "('Could not claim ...; not started') and the wake job goes to the failed "
    "queue. The design says new tasks QUEUE behind the current claim; they "
    "fail instead."))
def test_a_second_task_after_a_finished_run_queues_rather_than_failing(board):
    agent = board.enroll("claude-a")                    # max_active_tickets=1
    _channel_task(board, agent)
    _, _, second = _channel_task(board, agent)
    outcomes = board.supervisor(agent).run_forever(wait_seconds=0, max_polls=2)
    assert outcomes[0].state == "responded"
    assert board.wake_job(second["wake_job"]["id"])["state"] in ("pending", "leased", "completed")
    assert outcomes[1].state != "failed", outcomes[1].reason


def test_a_hook_only_agent_is_queued_with_manual_resume_required(board):
    hooky = board.enroll("hooky", connection_mode="hook_only")
    _, _, task = _channel_task(board, hooky)
    assert task["wake_job"] is None
    receipt = task["deliveries"][0]
    assert receipt["state"] == "queued"
    assert receipt["reason"] == "manual_resume_required"


@pytest.mark.xfail(strict=True, reason=(
    "F-3: no runner_offline receipt. `_dispatch` marks the delivery "
    "'delivered' the moment a wake job exists, without checking whether any "
    "runner holds this agent's lease. A managed agent whose supervisor is not "
    "running reads 'Delivered to runner' forever instead of 'Queued -- runner "
    "offline'."))
def test_a_managed_agent_with_no_live_runner_is_queued_runner_offline(board):
    agent = board.enroll("claude-a")          # enrolled, never registered a runner
    _, _, task = _channel_task(board, agent)
    receipt = board.deliveries(task["message"]["id"])[0]
    assert receipt["state"] == "queued"
    assert receipt["reason"] == "runner_offline"


@pytest.mark.xfail(strict=True, reason=(
    "F-4: a queued-behind-a-busy-agent task also reads 'delivered' with no "
    "reason, never 'Queued -- agent is busy with another claim'. Same root "
    "cause as F-3: the receipt is written at dispatch time and never again."))
def test_a_task_behind_a_live_run_is_queued_agent_busy(board):
    agent = board.enroll("claude-a")
    _, _, first = _channel_task(board, agent)
    client = board.runner_client(agent)
    sup = board.supervisor(agent)
    sup.register()
    client.jobs(sup.state.runner_id, wait_seconds=0)
    from ticket_board.runners.state import board_session_id, new_runtime_session_id
    ses = board_session_id(new_runtime_session_id())
    run_id = "run_" + first["wake_job"]["id"][4:]
    run = client.run_event(run_id, "starting", expected_version=1, session_id=ses)
    client.run_event(run_id, "started", expected_version=run["version"], session_id=ses)

    _, _, second = _channel_task(board, agent)
    receipt = board.deliveries(second["message"]["id"])[0]
    assert receipt["state"] == "queued"
    assert receipt["reason"] == "agent_busy"
