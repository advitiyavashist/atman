"""A task DM reaches a managed runner, is claimed, runs, and is reported.

The design doc's first piece of required evidence is: "Send a task DM to an
idle managed Claude, see a claim and actual run start, then receive its reply
in the original thread." This module proves every link of that chain that the
integrated T-187 + T-188 code can prove, over a real socket, and marks the
links it cannot as strict xfails so they cannot be mistaken for passes.
"""

from __future__ import annotations

import pytest

from .live_board import FakeLauncher, FakeProcess, rid


def _dm_task(board, agent, outcome="Reply with the word PROOF."):
    """Operator DMs a managed agent and turns the message into a task."""
    me = board.operator_member()
    dm = board.dm(me["id"], agent.member_id)
    source = board.say(board.operator, dm["id"], "are you free?")
    assert source.status == 201, source.body
    task = board.task(board.operator, source.body["message"]["id"],
                      agent.agent_id, outcome=outcome)
    assert task.status == 201, task.body
    return dm, source.body["message"], task.body


def test_a_dm_task_wakes_claims_runs_and_reports_over_a_real_socket(board):
    agent = board.enroll("claude-a")
    dm, source, task = _dm_task(board, agent)

    # The task is a real record before anybody is woken: a message, a draft
    # ticket, one delivery, one wake job -- aimed at exactly this agent.
    assert task["message"]["intent"] == "task"
    assert task["message"]["causation_id"] == source["id"]
    assert task["ticket"]["id"] == task["message"]["ticket_id"]
    assert [d["recipient_agent_id"] for d in task["deliveries"]] == [agent.agent_id]
    assert task["wake_job"]["recipient_agent_id"] == agent.agent_id
    assert task["wake_job"]["state"] == "pending"

    launcher = FakeLauncher()
    sup = board.supervisor(agent, launcher=launcher)
    outcomes = sup.run_forever(wait_seconds=2, max_polls=1)

    assert [o.state for o in outcomes] == ["responded"], outcomes
    run = board.run(outcomes[0].run_id)
    assert run["state"] == "responded"
    assert run["wake_job_id"] == task["wake_job"]["id"]
    # "Started" was a real claim: the draft ticket is now this agent's.
    assert run["ticket_claim"] == task["ticket"]["id"]
    detail = agent.http.get("/tickets/" + task["ticket"]["id"]).body
    assert detail["ticket"]["state"] == "claimed"
    assert detail["ticket"]["owner"] == agent.agent_id, detail["ticket"]
    # Exactly one process was asked for, in the allowlisted worktree, with the
    # task on stdin and never on an argv.
    assert len(launcher.specs) == 1
    assert launcher.specs[0].worktree == sup.worktree
    assert launcher.specs[0].resume is False
    assert "Wake job {}".format(task["wake_job"]["id"]) in launcher.specs[0].prompt
    # The board's own trail says the run happened in order.
    actions = board.audit("run.", run["id"])
    assert actions[:1] == ["run.create"]
    assert actions.count("run.event") >= 3          # starting, started, responded
    assert "run.event.unattributed" not in actions
    assert board.wake_job(task["wake_job"]["id"])["state"] == "completed"


def test_a_plain_dm_or_channel_message_wakes_nobody(board):
    """Human messages alone do not fan out to all agents.

    Three managed agents, three registered runners all long-polling, one plain
    message in #general and one plain DM. Nobody gets a job; nothing is
    recorded against any agent.
    """
    agents = [board.enroll("worker-{}".format(i)) for i in range(3)]
    general = board.channel("general")
    me = board.operator_member()
    dm = board.dm(me["id"], agents[0].member_id)

    assert board.say(board.operator, general["id"], "morning all").status == 201
    assert board.say(board.operator, dm["id"], "hey, quick question").status == 201

    for agent in agents:
        sup = board.supervisor(agent)
        assert sup.run_forever(wait_seconds=0, max_polls=2) == []
    assert board.store.conn.execute(
        "SELECT COUNT(*) FROM wake_jobs WHERE project_id = ?",
        (board.project_id,)).fetchone()[0] == 0
    assert board.store.conn.execute(
        "SELECT COUNT(*) FROM deliveries WHERE project_id = ?",
        (board.project_id,)).fetchone()[0] == 0


def test_a_mention_delivers_to_the_named_agent_only_and_does_not_wake(board):
    """A mention is a delivery, not a dispatch.

    The design doc says "a channel mention wakes the named agent". The
    integrated code creates a `queued` delivery for the mentioned agent and no
    wake job -- so the mention is visible in the receipts, nobody else is
    touched, and NO runner starts. Recorded as finding F-6 in docs/runbook.md;
    this test pins the behaviour that is actually shipped so the two halves
    cannot drift apart silently.
    """
    named, other = board.enroll("named"), board.enroll("other")
    general = board.channel("general")
    sent = board.say(board.operator, general["id"], "ping", mentions=[named.member_id])
    assert sent.status == 201, sent.body
    assert [d["recipient_agent_id"] for d in sent.body["deliveries"]] == [named.agent_id]
    assert sent.body["deliveries"][0]["state"] == "queued"

    for agent in (named, other):
        assert board.supervisor(agent).run_forever(wait_seconds=0, max_polls=1) == []


def test_via_master_wakes_the_designated_master_once_and_nobody_else(board):
    """Channel master routing: an unaddressed task in #work wakes the channel's
    designated master exactly once. Two other capable workers are polling and
    get nothing."""
    master = board.enroll("master")
    workers = [board.enroll("cap-{}".format(i)) for i in range(2)]
    work = board.channel("work", designated_master=master.agent_id)

    source = board.say(board.operator, work["id"], "someone please document the API")
    task = board.task(board.operator, source.body["message"]["id"],
                      mode="via_master")
    assert task.status == 201, task.body
    assert task.body["wake_job"]["recipient_agent_id"] == master.agent_id
    assert [d["recipient_agent_id"] for d in task.body["deliveries"]] == [master.agent_id]

    for w in workers:
        assert board.supervisor(w).run_forever(wait_seconds=0, max_polls=1) == []
    outcomes = board.supervisor(master).run_forever(wait_seconds=1, max_polls=1)
    assert [o.state for o in outcomes] == ["responded"]
    # A second poll finds nothing: the master was woken once.
    assert board.supervisor(master).run_forever(wait_seconds=0, max_polls=1) == []


# --------------------------------------------------------------- findings

@pytest.mark.xfail(strict=True, reason=(
    "F-1: receipts stop at 'delivered'. Nothing in T-187 or T-188 transitions "
    "the delivery to started/responded or links run_id, so the receipt chain "
    "the design and the T-189 UI show (Sent -> Queued -> Delivered -> Agent "
    "started -> Responded) can never get past 'Delivered to runner'."))
def test_the_receipt_reaches_responded_when_the_run_does(board):
    agent = board.enroll("claude-a")
    _, _, task = _dm_task(board, agent)
    outcomes = board.supervisor(agent).run_forever(wait_seconds=1, max_polls=1)
    assert [o.state for o in outcomes] == ["responded"]

    receipt = board.deliveries(task["message"]["id"])[0]
    assert receipt["run_id"] == outcomes[0].run_id
    assert receipt["state"] == "responded"


@pytest.mark.xfail(strict=True, reason=(
    "F-2: no reply path. The supervisor reports run events, discards the "
    "child's stdout, and hands the child no board credential; there is no "
    "code that posts a reply into the source thread. 'Receive its reply in "
    "the original thread' is not reachable with the integrated code."))
def test_the_agents_reply_lands_in_the_original_thread(board):
    agent = board.enroll("claude-a")
    dm, source, task = _dm_task(board, agent)
    launcher = FakeLauncher(process=FakeProcess(stdout="PROOF"))
    outcomes = board.supervisor(agent, launcher=launcher).run_forever(
        wait_seconds=1, max_polls=1)
    assert [o.state for o in outcomes] == ["responded"]

    listing = board.messages(dm["id"])
    replies = [m for m in listing["items"]
               if m["intent"] == "reply" and m["author"].get("id") == agent.agent_id]
    assert replies, listing["items"]
    assert replies[0]["causation_id"] in (task["message"]["id"], source["id"])
