"""The deterministic half: the four gates, in the contract's own order.

Each gate gets a test that fails for exactly one reason, and each asserts the
*reason string* as well as the outcome -- `master_decisions` is the operator's
only account of a quiet board, so a right answer with a wrong explanation is
still a defect.
"""

from ticket_board.orchestrator import Snapshot, eligible_agents, plan


def _snap(store, project):
    return Snapshot(store, project)


# ------------------------------------------------------------ gate 1: deps

def test_a_ticket_waiting_on_an_unfinished_dependency_is_not_routed(
        store, project, make_agent, make_ticket):
    make_agent("backend-1", role="backend")
    blocker = make_ticket("first", role="backend")
    dependent = make_ticket("second", role="backend", dependencies=[blocker])

    decisions = {d.ticket_id: d for d in plan(_snap(store, project))}
    assert decisions[blocker].decision == "assigned"
    assert decisions[dependent].decision == "skipped"
    assert decisions[dependent].reason == "waiting on %s" % blocker


def test_a_dependency_that_does_not_exist_blocks_rather_than_disappears(
        store, project, make_agent, make_ticket):
    make_agent("backend-1", role="backend")
    ticket = make_ticket("orphan dep", role="backend", dependencies=["DEMO-999"])

    decision = {d.ticket_id: d for d in plan(_snap(store, project))}[ticket]
    assert decision.decision == "skipped"
    assert "DEMO-999" in decision.reason


def test_a_dependency_that_is_done_no_longer_blocks(
        store, project, make_agent, make_ticket):
    make_agent("backend-1", role="backend")
    blocker = make_ticket("first", role="backend", state="done")
    dependent = make_ticket("second", role="backend", dependencies=[blocker])

    decision = {d.ticket_id: d for d in plan(_snap(store, project))}[dependent]
    assert decision.decision == "assigned"


# ---------------------------------------------------- gate 2: capabilities

def test_a_role_is_matched_by_role_or_by_capability_but_not_by_neither(
        store, project, make_agent, make_ticket):
    make_agent("console-1", role="console")
    ticket = make_ticket("backend work", role="backend")

    decision = {d.ticket_id: d for d in plan(_snap(store, project))}[ticket]
    assert decision.decision == "no_eligible_agent"
    assert "does not cover role backend" in decision.reason


def test_a_capability_covers_a_role_the_agent_is_not_named_for(
        store, project, make_agent, make_ticket):
    agent = make_agent("generalist", role="console", capabilities=["backend"])
    ticket = make_ticket("backend work", role="backend")

    decision = {d.ticket_id: d for d in plan(_snap(store, project))}[ticket]
    assert decision.decision == "assigned"
    assert decision.agent_id == agent
    assert "capability backend" in decision.reason


def test_a_ticket_with_no_role_may_go_to_anyone(
        store, project, make_agent, make_ticket):
    make_agent("console-1", role="console")
    ticket = make_ticket("anyone can do this")

    decision = {d.ticket_id: d for d in plan(_snap(store, project))}[ticket]
    assert decision.decision == "assigned"
    assert decision.reason.startswith("no role required")


# -------------------------------------------------------- gate 3: capacity

def test_capacity_counts_queued_reservations_not_only_claimed_tickets(
        store, project, make_agent, make_ticket, sweeper):
    make_agent("backend-1", role="backend", max_active=1)
    first = make_ticket("first", role="backend")
    second = make_ticket("second", role="backend")

    swept = sweeper.run_once(force=True)
    assigned = [d.ticket_id for d in swept.assigned]
    assert assigned == [first], "one slot, one reservation"

    decision = {d.ticket_id: d for d in plan(_snap(store, project))}[second]
    assert decision.decision == "no_eligible_agent"
    assert "at capacity (1/1)" in decision.reason


def test_one_sweep_cannot_overfill_an_agent(
        store, project, make_agent, make_ticket):
    make_agent("backend-1", role="backend", max_active=2)
    tickets = [make_ticket("t%d" % i, role="backend") for i in range(4)]

    decisions = plan(_snap(store, project))
    assigned = [d for d in decisions if d.decision == "assigned"]
    assert len(assigned) == 2, "the plan must respect the limit within one pass"
    assert [d.ticket_id for d in assigned] == tickets[:2]


def test_work_spreads_to_the_least_loaded_agent(
        store, project, make_agent, make_ticket):
    busy = make_agent("backend-1", role="backend", max_active=2)
    idle = make_agent("backend-2", role="backend", max_active=2)
    make_ticket("held", role="backend", state="claimed", owner=busy)
    ticket = make_ticket("new", role="backend")

    decision = {d.ticket_id: d for d in plan(_snap(store, project))}[ticket]
    assert decision.agent_id == idle


# ------------------------------------------- gate 4: file/worktree conflicts

def test_a_ticket_touching_files_someone_is_working_is_not_routed(
        store, project, make_agent, make_ticket):
    other = make_agent("backend-1", role="backend", max_active=5)
    make_agent("backend-2", role="backend", max_active=5)
    held = make_ticket("in flight", role="backend", files=["src/app.py"],
                       state="claimed", owner=other)
    clashing = make_ticket("same file", role="backend", files=["src/app.py"])

    decision = {d.ticket_id: d for d in plan(_snap(store, project))}[clashing]
    assert decision.decision == "no_eligible_agent"
    assert held in decision.reason and "src/app.py" in decision.reason


def test_a_worktree_in_use_by_another_agent_is_never_taken_over(
        store, project, make_agent, make_ticket):
    resident = make_agent("backend-1", role="backend", worktree="/w/lane-a")
    make_agent("backend-2", role="backend")
    ticket = make_ticket("lane a work", role="backend", worktree="/w/lane-a")

    candidates, rejected = eligible_agents(
        _snap(store, project), _snap(store, project).by_id[ticket])
    assert [c.agent_id for c in candidates] == [resident], \
        "only the agent already in that worktree may take it"
    assert any("worktree /w/lane-a is in use by %s" % resident in why
               for _, why in rejected)


# ------------------------------------------------------- gate 0: liveness

def test_an_agent_that_never_connected_is_not_given_work(
        store, project, make_agent, make_ticket):
    """Enrolled is not connected.

    `derive_agent_state` calls this agent `idle`, which is right for a
    dashboard; routing has to ask the stricter question, because `POST /claim`
    would refuse it. Asserting the reason matters here: "idle" would have been
    the wrong story to tell an operator.
    """
    make_agent("backend-1", role="backend", live=False)
    ticket = make_ticket("work", role="backend")

    decision = {d.ticket_id: d for d in plan(_snap(store, project))}[ticket]
    assert decision.decision == "no_eligible_agent"
    assert "has no live session lease" in decision.reason


def test_an_agent_whose_heartbeat_went_stale_is_not_given_work(
        store, project, make_agent, make_ticket):
    """A live lease is not enough if the adapter stopped delivering."""
    make_agent("backend-1", role="backend", heartbeat_age=3600)
    ticket = make_ticket("work", role="backend")

    decision = {d.ticket_id: d for d in plan(_snap(store, project))}[ticket]
    assert decision.decision == "no_eligible_agent"
    assert "agent is offline" in decision.reason


def test_an_empty_board_says_so_rather_than_blaming_an_agent(
        store, project, make_ticket):
    ticket = make_ticket("work", role="backend")

    decision = {d.ticket_id: d for d in plan(_snap(store, project))}[ticket]
    assert decision.decision == "no_eligible_agent"
    assert decision.reason == "no agents are enrolled on this board"


# ---------------------------------------------------------- no takeovers

def test_an_open_ticket_that_already_has_an_owner_is_skipped_not_reassigned(
        store, project, make_agent, make_ticket):
    holder = make_agent("backend-1", role="backend")
    make_agent("backend-2", role="backend")
    ticket = make_ticket("odd", role="backend", state="open", owner=holder)

    decision = {d.ticket_id: d for d in plan(_snap(store, project))}[ticket]
    assert decision.decision == "skipped"
    assert "already owned by %s" % holder in decision.reason


# ------------------------------------------------------------ determinism

def test_the_same_board_always_produces_the_same_plan(
        store, project, make_agent, make_ticket):
    for i in range(3):
        make_agent("backend-%d" % i, role="backend", max_active=2)
    for i in range(5):
        make_ticket("t%d" % i, role="backend")

    runs = [[(d.ticket_id, d.decision, d.agent_id)
             for d in plan(_snap(store, project))] for _ in range(5)]
    assert all(run == runs[0] for run in runs)


def test_an_agent_already_working_still_gets_its_second_slot(
        store, project, make_agent, make_ticket, sweeper):
    """Regression: liveness must not silently cap everyone at one ticket.

    `derive_agent_state` calls any agent holding a ticket `working`. When
    `working` was missing from the assignable set, an agent with
    `max_active_tickets: 2` could take a second ticket inside one sweep (the
    snapshot was taken while it was still idle) but never in the next one -- so
    the limit worked in tests and not on a live board.
    """
    agent = make_agent("backend-1", role="backend", max_active=2)
    first = make_ticket("first", role="backend")
    second = make_ticket("second", role="backend")

    assert [d.ticket_id for d in sweeper.run_once(force=True).assigned] == \
        [first, second]

    store.conn.execute(
        "UPDATE assignments SET state = 'claimed' WHERE ticket_id = ?", (first,))
    store.conn.execute(
        "UPDATE tickets SET state = 'claimed', owner = ? WHERE project_id = ?"
        " AND id = ?", (agent, project, first))
    store.conn.commit()
    assert _snap(store, project).agent_state(agent) == "working"

    third = make_ticket("third", role="backend")
    decision = {d.ticket_id: d for d in plan(_snap(store, project))}[third]
    assert decision.decision == "no_eligible_agent"
    assert "at capacity (2/2)" in decision.reason, \
        "refused for capacity, which is the true reason, not for liveness"
