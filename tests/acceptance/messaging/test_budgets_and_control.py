"""Dependency blocks, permission requests, reply loops, cancel and budgets.

Design doc: "Reaching a budget pauses with a visible reason; operators can
resume. ... cancel is cooperative and does not delete artifacts or mark
tickets complete. ... A reply/receipt does not wake its author or generate
another auto-reply. Agent-to-agent task requests may trigger another agent
within ... a bounded hop/turn budget."
"""

from __future__ import annotations

import threading
import time

import pytest

from .live_board import FakeLauncher, FakeProcess, rid
from ticket_board.runners.state import board_session_id, new_runtime_session_id


def _task_on(board, http, channel_id, agent_id, **kw):
    source = board.say(http, channel_id, "task").body["message"]
    task = board.task(http, source["id"], agent_id, **kw)
    assert task.status == 201, task.body
    return source, task.body


def _start_run(board, agent, sup, wake_job_id):
    """Drive a run to `running` by hand, the way the supervisor does."""
    client = board.runner_client(agent)
    leased = client.jobs(sup.state.runner_id, wait_seconds=0)["items"]
    assert [j["id"] for j in leased] == [wake_job_id]
    run_id = "run_" + wake_job_id[4:]
    ses = board_session_id(new_runtime_session_id())
    run = client.run_event(run_id, "starting", expected_version=1, session_id=ses)
    run = client.run_event(run_id, "started", expected_version=run["version"],
                           session_id=ses)
    assert run["state"] == "running"
    return client, run


# ---------------------------------------------------------- dependency block

def test_dependency_blocked_work_is_never_reported_started(board):
    """The linked ticket waits on another. The run must not paint itself green:
    the claim is refused (dependency_unmet), the run ends `failed` with the
    refusal named, and no process is started."""
    agent = board.enroll("claude-a")
    first = board.ticket("T-190 first")
    second = board.ticket("T-190 second", dependencies=[first["id"]])
    general = board.channel("general")
    _, task = _task_on(board, board.operator, general["id"], agent.agent_id,
                       ticket={"existing_ticket_id": second["id"]})

    launcher = FakeLauncher()
    outcomes = board.supervisor(agent, launcher=launcher).run_forever(
        wait_seconds=0, max_polls=1)
    assert [o.state for o in outcomes] == ["failed"]
    assert "Could not claim {}".format(second["id"]) in outcomes[0].reason
    assert launcher.specs == []
    run = board.run(outcomes[0].run_id)
    assert run["state"] == "failed" and run["started_at"] is None
    # The refusal the claim actually got, over the wire, is the contract's own.
    detail = agent.http.get("/tickets/" + second["id"]).body
    refused = agent.http.post("/tickets/{}/claim".format(second["id"]), {
        "request_id": rid(), "expected_version": detail["ticket"]["version"],
        "session_id": agent.session_id})
    assert refused.status == 422 and refused.code() == "dependency_unmet"
    assert refused.body["error"]["details"]["unmet"] == [first["id"]]


@pytest.mark.xfail(strict=True, reason=(
    "F-8: a dependency-blocked task is dispatched anyway and its receipt reads "
    "'delivered'. The design wants 'Waiting on <ticket>' (delivery `blocked`, "
    "reason `dependency_unmet`, `blocking_ticket_id`) and no wake. Instead a "
    "wake job is minted, the supervisor burns a run on a claim it cannot win, "
    "and the job lands in the FAILED queue -- a blocked task is recorded as a "
    "failure, not as waiting."))
def test_a_dependency_blocked_task_shows_waiting_on_the_blocker(board):
    agent = board.enroll("claude-a")
    first = board.ticket("T-190 first")
    second = board.ticket("T-190 second", dependencies=[first["id"]])
    general = board.channel("general")
    _, task = _task_on(board, board.operator, general["id"], agent.agent_id,
                       ticket={"existing_ticket_id": second["id"]})
    receipt = board.deliveries(task["message"]["id"])[0]
    assert receipt["state"] == "blocked"
    assert receipt["reason"] == "dependency_unmet"
    assert receipt["blocking_ticket_id"] == first["id"]
    assert task["wake_job"] is None


# ------------------------------------------------------- permission request

def test_a_needs_approval_run_holds_the_agent_until_an_operator_acts(board):
    """The board half of 'Needs approval': a paused run is visible with its
    reason, is not reaped as an orphan, keeps the agent busy so nothing else
    starts behind it, and an operator can cancel it."""
    agent = board.enroll("claude-a", max_active_tickets=2)
    general = board.channel("general")
    _, first = _task_on(board, board.operator, general["id"], agent.agent_id)
    _, second = _task_on(board, board.operator, general["id"], agent.agent_id)
    sup = board.supervisor(agent)
    sup.register()
    client, run = _start_run(board, agent, sup, first["wake_job"]["id"])

    paused = client.run_event(run["id"], "needs_approval",
                              expected_version=run["version"],
                              reason="Wants to run `git push`.")
    assert paused["state"] == "paused" and paused["needs_approval"] is True
    assert paused["terminal_reason"] == "Wants to run `git push`."
    assert client.jobs(sup.state.runner_id, wait_seconds=0)["items"] == []
    assert board.wake_job(second["wake_job"]["id"])["state"] == "pending"

    canceled = board.operator.post("/runs/{}/cancel".format(run["id"]), {
        "request_id": rid(), "expected_version": paused["version"],
        "reason": "Operator declined the push."})
    assert canceled.status == 200, canceled.body
    assert canceled.body["state"] == "canceled"
    nxt = client.jobs(sup.state.runner_id, wait_seconds=0)["items"]
    assert [j["id"] for j in nxt] == [second["wake_job"]["id"]]


@pytest.mark.xfail(strict=True, reason=(
    "F-9: the supervisor never emits needs_approval. Permission policy `prompt` "
    "maps to no flag, the child runs `claude -p` non-interactively, and the "
    "supervisor reads only the exit code: a child that stopped because it "
    "needed permission and exited 0 is reported `responded` (a false green), "
    "one that exited non-zero is `failed`. No path leads to `paused`."))
def test_a_child_that_needs_permission_pauses_the_run(board):
    agent = board.enroll("claude-a")
    general = board.channel("general")
    _, task = _task_on(board, board.operator, general["id"], agent.agent_id)
    asks = FakeProcess(returncode=0, stdout="",
                       stderr="Permission required to run Bash(git push); not granted.")
    outcomes = board.supervisor(agent, launcher=FakeLauncher(process=asks)).run_forever(
        wait_seconds=0, max_polls=1)
    run = board.run(outcomes[0].run_id)
    assert run["state"] == "paused" and run["needs_approval"] is True


# ------------------------------------------------------------- reply loops

def test_a_reply_and_a_receipt_wake_nobody_and_never_their_author(board):
    a, b = board.enroll("a"), board.enroll("b")
    general = board.channel("general")
    root = board.say(board.operator, general["id"], "start").body["message"]
    thread = board.store.create_thread(board.project_id, general["id"], root["id"])

    # A replies in the thread, mentioning itself and B. It gets no delivery to
    # itself; B gets a queued delivery and NO wake job.
    reply = board.say(a.http, general["id"], "done", intent="reply",
                      thread_id=thread["id"], causation_id=root["id"],
                      mentions=[a.member_id, b.member_id])
    assert reply.status == 201, reply.body
    assert [d["recipient_agent_id"] for d in reply.body["deliveries"]] == [b.agent_id]
    for agent in (a, b):
        assert board.supervisor(agent).run_forever(wait_seconds=0, max_polls=1) == []
    assert board.store.conn.execute("SELECT COUNT(*) FROM wake_jobs").fetchone()[0] == 0


def test_an_agent_can_task_another_agent_and_the_chain_is_causally_linked(board):
    """Agent-to-agent task requests are allowed: A turns B's message into work
    for B, B's runner executes it, and the task message names its cause."""
    a, b = board.enroll("a"), board.enroll("b")
    work = board.channel("work")
    source = board.say(b.http, work["id"], "anyone know the API?").body["message"]
    task = board.task(a.http, source["id"], b.agent_id,
                      outcome="B, document the API.")
    assert task.status == 201, task.body
    assert task.body["message"]["author"]["id"] == a.agent_id
    assert task.body["message"]["causation_id"] == source["id"]
    outcomes = board.supervisor(b).run_forever(wait_seconds=0, max_polls=1)
    assert [o.state for o in outcomes] == ["responded"]


@pytest.mark.xfail(strict=True, reason=(
    "F-10: no hop budget is enforced. `max_hops: 3` is carried on every run's "
    "budget and `hops_used` is never incremented or checked anywhere in T-187 "
    "or T-188: two agents tasking each other back and forth are dispatched on "
    "every hop, indefinitely. The design's reply-loop bound does not exist."))
def test_an_agent_to_agent_task_chain_stops_at_the_hop_budget(board):
    a, b = board.enroll("a", max_active_tickets=10), board.enroll("b", max_active_tickets=10)
    work = board.channel("work")
    source = board.say(a.http, work["id"], "ping").body["message"]
    dispatched = 0
    for hop in range(6):
        sender, target = ((b, a) if hop % 2 else (a, b))
        task = board.task(sender.http, source["id"], target.agent_id,
                          outcome="hop {}".format(hop))
        if task.status != 201:
            break
        payload = task.body
        if payload["wake_job"] is None:
            # Queued with a budget reason is also an acceptable stop.
            assert payload["deliveries"][0]["reason"] == "budget_exceeded"
            break
        dispatched += 1
        source = payload["message"]
    assert dispatched <= 3, "hop budget of 3 was exceeded: {} dispatched".format(dispatched)


# ----------------------------------------------------------- cancel/budgets

def test_a_run_over_its_time_budget_pauses_visibly_and_holds_the_agent(board):
    agent = board.enroll("claude-a", max_active_tickets=2)
    general = board.channel("general")
    _, first = _task_on(board, board.operator, general["id"], agent.agent_id)
    _, second = _task_on(board, board.operator, general["id"], agent.agent_id)

    hung = FakeProcess(hang=True)
    sup = board.supervisor(agent, launcher=FakeLauncher(process=hung))
    outcomes = sup.run_forever(wait_seconds=0, max_polls=2)
    assert [o.state for o in outcomes] == ["budget_reached"], outcomes
    run = board.run(outcomes[0].run_id)
    assert run["state"] == "paused"
    assert "budget" in run["terminal_reason"].lower()
    assert hung.terminated is True
    # The paused run holds the agent; the second job was not handed out.
    assert board.wake_job(second["wake_job"]["id"])["state"] == "pending"
    # An operator resolves it by cancel, which frees the queue.
    canceled = board.operator.post("/runs/{}/cancel".format(run["id"]), {
        "request_id": rid(), "expected_version": run["version"],
        "reason": "Too long."})
    assert canceled.status == 200, canceled.body
    nxt = sup.run_forever(wait_seconds=0, max_polls=1)
    assert [o.wake_job_id for o in nxt] == [second["wake_job"]["id"]]


def test_cancel_is_cooperative_retains_the_ticket_and_closes_the_job(board):
    agent = board.enroll("claude-a")
    general = board.channel("general")
    _, task = _task_on(board, board.operator, general["id"], agent.agent_id)
    sup = board.supervisor(agent)
    sup.register()
    client, run = _start_run(board, agent, sup, task["wake_job"]["id"])
    claimed = client.claim_ticket(task["ticket"]["id"], expected_version=1,
                                  session_id=agent.session_id)

    canceled = board.operator.post("/runs/{}/cancel".format(run["id"]), {
        "request_id": rid(), "expected_version": run["version"],
        "reason": "Changed my mind."})
    assert canceled.status == 200, canceled.body
    assert canceled.body["state"] == "canceled"
    assert canceled.body["terminal_reason"] == "Changed my mind."
    assert board.wake_job(task["wake_job"]["id"])["state"] == "canceled"
    # The ticket is neither completed nor unclaimed by a cancel.
    detail = board.operator.get("/tickets/" + task["ticket"]["id"]).body["ticket"]
    assert detail["state"] == "claimed" and detail["owner"] == agent.agent_id
    # A late 'responded' from the runner cannot resurrect a canceled run.
    from ticket_board.runners.client import ApiError
    with pytest.raises(ApiError) as late:
        client.run_event(run["id"], "responded", expected_version=canceled.body["version"])
    assert late.value.code == "invalid_state_transition"


@pytest.mark.xfail(strict=True, reason=(
    "F-11: cancel never reaches the running child. The supervisor blocks in "
    "handle.wait() and consults the board only when the process ends, so "
    "POST /runs/{id}/cancel marks the run canceled on the board while the "
    "process keeps running until its own time budget; the eventual terminal "
    "report then 422s against the canceled run. 'Cooperative cancel' has no "
    "channel to the thing it is cancelling."))
def test_an_operator_cancel_stops_the_running_process(board):
    agent = board.enroll("claude-a")
    general = board.channel("general")
    _, task = _task_on(board, board.operator, general["id"], agent.agent_id)
    hung = FakeProcess(hang=True)
    sup = board.supervisor(agent, launcher=FakeLauncher(process=hung),
                           budget={"max_seconds": 900})

    # Make the hung child block like a real one: communicate() waits until
    # terminate() is called instead of raising TimeoutExpired at once.
    released = threading.Event()

    def communicate(timeout=None):
        if not released.wait(timeout=8):
            import subprocess
            raise subprocess.TimeoutExpired("claude", timeout or 0)
        return "", ""
    hung.communicate = communicate
    original_terminate = hung.terminate

    def terminate():
        original_terminate()
        released.set()
    hung.terminate = terminate

    sup.register()
    worker = threading.Thread(target=lambda: sup.poll_once(wait_seconds=1), daemon=True)
    worker.start()
    run_id = "run_" + task["wake_job"]["id"][4:]

    def run_state():
        row = board.store.conn.execute(
            "SELECT state FROM runs WHERE id = ?", (run_id,)).fetchone()
        return row["state"] if row is not None else None
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline and run_state() != "running":
        time.sleep(0.05)
    run = board.run(run_id)
    assert run["state"] == "running"

    canceled = board.operator.post("/runs/{}/cancel".format(run_id), {
        "request_id": rid(), "expected_version": run["version"], "reason": "Stop."})
    assert canceled.status == 200, canceled.body
    try:
        assert released.wait(timeout=2), "the child was not terminated within 2s of cancel"
    finally:
        released.set()
        worker.join(timeout=10)
