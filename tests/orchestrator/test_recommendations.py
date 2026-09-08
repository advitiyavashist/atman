"""Model suggestions may reorder eligible candidates and do nothing else.

The contract's own words: suggestions "may only reorder among already-eligible
candidates". A recommender is the one input to this loop nobody reviewed, so
every test here is written from the position that it may be broken or hostile.
"""

from ticket_board.orchestrator import Sweeper
from ticket_board.server import master as master_ops
from ticket_board.server.auth import in_seconds


def _suggestions_on(store, project):
    store.conn.execute(
        "UPDATE master_lease SET routing_mode = 'deterministic_plus_suggestions'"
        " WHERE project_id = ?", (project,))
    store.conn.commit()


def _sweeper(store, project, operator, lease, recommender):
    return Sweeper(store, project, operator, lease, recommender=recommender)


def test_a_suggestion_can_reorder_two_eligible_agents(
        store, project, operator, lease, make_agent, make_ticket):
    first = make_agent("backend-1", role="backend")
    second = make_agent("backend-2", role="backend")
    ticket = make_ticket("work", role="backend")
    _suggestions_on(store, project)

    deterministic = min(first, second)   # least loaded, then id
    other = max(first, second)
    sweeper = _sweeper(store, project, operator, lease,
                       lambda t, candidates: [other])
    result = sweeper.run_once(force=True)

    assigned = result.assigned[0]
    assert assigned.ticket_id == ticket
    assert assigned.agent_id == other != deterministic
    assert "suggested over %s" % deterministic in assigned.reason


def test_a_suggestion_naming_an_ineligible_agent_is_dropped(
        store, project, operator, lease, make_agent, make_ticket):
    """The load-bearing case: a model must not be able to widen eligibility."""
    eligible = make_agent("backend-1", role="backend")
    wrong_role = make_agent("console-1", role="console")
    make_ticket("work", role="backend")
    _suggestions_on(store, project)

    sweeper = _sweeper(store, project, operator, lease,
                       lambda t, candidates: [wrong_role])
    result = sweeper.run_once(force=True)

    assert result.assigned[0].agent_id == eligible
    assert any("dropped ineligible suggestion %s" % wrong_role in n
               for n in result.notes)


def test_a_suggestion_naming_an_agent_that_does_not_exist_is_dropped(
        store, project, operator, lease, make_agent, make_ticket):
    eligible = make_agent("backend-1", role="backend")
    make_ticket("work", role="backend")
    _suggestions_on(store, project)

    sweeper = _sweeper(store, project, operator, lease,
                       lambda t, candidates: ["agt_hallucinated"])
    result = sweeper.run_once(force=True)
    assert result.assigned[0].agent_id == eligible
    assert any("agt_hallucinated" in n for n in result.notes)


def test_a_recommender_that_raises_does_not_stop_the_sweep(
        store, project, operator, lease, make_agent, make_ticket):
    eligible = make_agent("backend-1", role="backend")
    ticket = make_ticket("work", role="backend")
    _suggestions_on(store, project)

    def boom(ticket_payload, candidates):
        raise RuntimeError("model unavailable")

    result = _sweeper(store, project, operator, lease, boom).run_once(force=True)
    assert result.status == "swept"
    assert result.assigned[0].agent_id == eligible
    assert any("recommender raised RuntimeError" in n for n in result.notes)


def test_a_recommender_returning_nonsense_degrades_to_deterministic(
        store, project, operator, lease, make_agent, make_ticket):
    eligible = make_agent("backend-1", role="backend")
    make_ticket("work", role="backend")
    _suggestions_on(store, project)

    for nonsense in ("not a list", 42, [None], [{"agent": "x"}]):
        store.conn.execute("DELETE FROM assignments WHERE project_id = ?",
                           (project,))
        store.conn.execute(
            "UPDATE master_lease SET next_sweep_at = ? WHERE project_id = ?",
            (in_seconds(-1), project))
        store.conn.commit()
        result = _sweeper(store, project, operator, lease,
                          lambda t, c, v=nonsense: v).run_once(force=True)
        assert result.status == "swept"
        assert result.assigned[0].agent_id == eligible, nonsense


def test_a_suggestion_cannot_route_a_ticket_the_deterministic_pass_refused(
        store, project, operator, lease, make_agent, make_ticket):
    """Reordering is offered only for tickets already marked `assigned`."""
    make_agent("console-1", role="console")
    blocked = make_ticket("backend work", role="backend")
    _suggestions_on(store, project)

    called = []

    def recommender(ticket_payload, candidates):
        called.append(ticket_payload["id"])
        return candidates

    result = _sweeper(store, project, operator, lease,
                      recommender).run_once(force=True)
    assert blocked not in called, "no candidates means nothing to reorder"
    assert master_ops.queue(store, project) == []


def test_suggestions_are_not_consulted_in_deterministic_mode(
        store, project, operator, lease, make_agent, make_ticket):
    """routing_mode is the switch, and it is server state, not a caller's flag."""
    first = make_agent("backend-1", role="backend")
    second = make_agent("backend-2", role="backend")
    make_ticket("work", role="backend")

    called = []
    sweeper = _sweeper(store, project, operator, lease,
                       lambda t, c: called.append(1) or [max(first, second)])
    result = sweeper.run_once(force=True)

    assert called == []
    assert result.assigned[0].agent_id == min(first, second)
    assert any("routing_mode is deterministic" in n for n in result.notes)


def test_a_partial_suggestion_keeps_the_rest_in_deterministic_order(
        store, project, operator, lease, make_agent, make_ticket):
    agents = sorted(make_agent("backend-%d" % i, role="backend")
                    for i in range(3))
    make_ticket("work", role="backend")
    _suggestions_on(store, project)

    result = _sweeper(store, project, operator, lease,
                      lambda t, c: [agents[2]]).run_once(force=True)
    assert result.assigned[0].agent_id == agents[2]
