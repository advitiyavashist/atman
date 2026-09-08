"""Point another lane's conformance harness at this server.

`tests/acceptance/conformance/` is T-200's work, written before this API
existed and exercised until now only against its own fixture-replay stub. Its
own docstring says the real target arrives with T-180. This runs it against
this server -- adversarial in the useful sense, because the cases, the
credential handling and the pass criteria were all chosen by someone else.

Two properties are asserted rather than reported:

* every read screen in this lane answers with the declared status *and* a body
  that validates against the declared response schema;
* every mutation route in this lane refuses a body carrying `actor` with 400
  `malformed_request` -- frozen decision 1, checked by a harness this lane did
  not write.

The rest is reported honestly. The fixtures encode a fixed `expected_version`
chain (claim at 1, review at 4, decision at 5) that cannot all hold against one
real stateful board, so some mutations legitimately answer 409. What is
asserted about those is the thing that would actually be a defect: that the
refusal is still contract-shaped, and that nothing anywhere produced a body
that fails its schema.
"""

import json
import sys
import threading
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from ticket_board.server import BoardServer                       # noqa: E402
from ticket_board.server.auth import hash_secret, in_seconds      # noqa: E402
from ticket_board.server.httpd import make_server                 # noqa: E402

harness = pytest.importorskip("tests.acceptance.conformance.harness")
cases_module = pytest.importorskip("tests.acceptance.conformance.cases")

PROJECT_ID = "prj_demo0001"
AGENT_ID = "agt_backend01"
SESSION_ID = "ses_a1b2c3d4"
TICKET_ID = "DEMO-13"

# The operations this build implements. The messaging and runner cases belong to
# T-187 and T-188 and are excluded by name rather than by filtering on the
# result, so a route that starts working is not silently absorbed.
MY_LANE = {
    "getOverview", "listTickets", "getTicket", "listAgents", "getMasterPanel",
    "listActivity", "createTicket", "claimTicket", "createTicketUpdate",
    "requestReview", "decideReview", "setTicketBlocked", "revokeSessionLease",
    "createEnrollment", "exchangeEnrollment", "postHookEvent",
    "takeMasterLease", "setMasterPaused", "createAssignment",
}
READS = {"getOverview", "listTickets", "getTicket", "listAgents",
         "getMasterPanel", "listActivity"}

# The statuses `ErrorResponse.status` allows, plus the success ones.
CONTRACT_STATUSES = {200, 201, 400, 401, 403, 404, 409, 422, 429}


@pytest.fixture()
def board_factory(tmp_path):
    """Build a seeded board on demand; several tests need a *fresh* one."""
    running = []

    def make(name="board"):
        base = _seed(tmp_path / "{}.sqlite3".format(name))
        running.append(base)
        return base[0]

    try:
        yield make
    finally:
        for _, httpd, thread, board in running:
            httpd.shutdown()
            httpd.server_close()
            thread.join(timeout=5)
            board.close()


@pytest.fixture()
def seeded(board_factory):
    return board_factory()


def _seed(db_path):
    """A board carrying the ids the harness hard-codes, and its stub credentials.

    The credentials are inserted as hashes of the literal secrets the harness
    sends. Nothing about the server is relaxed to accept them -- they are real
    credentials that happen to have known values, which is what a fixture
    account is.
    """
    board = BoardServer(db_path)
    store = board.store
    store.create_project("Demo", project_id=PROJECT_ID)
    store.create_agent(PROJECT_ID, "seeded-backend", role="backend",
                       connection_mode="managed", agent_id=AGENT_ID)
    store.open_session(AGENT_ID, in_seconds(3600), session_id=SESSION_ID)
    store.create_ticket(PROJECT_ID, TICKET_ID, "Add the widget endpoint",
                        role="backend", outcome="Cursor page, covered by tests.",
                        acceptance=[{"text": "Cursor paging works",
                                     "checked": False, "checked_by": None,
                                     "checked_at": None}])
    store.create_ticket(PROJECT_ID, "DEMO-14", "Second ticket",
                        outcome="o",
                        acceptance=[{"text": "a", "checked": False,
                                     "checked_by": None, "checked_at": None}])

    # The claim fixture cites reservation `asg_00000001`; without it the route
    # correctly answers 404 (no such reservation) and the whole reservation
    # path goes unexercised by this harness.
    store.conn.execute(
        "INSERT INTO assignments (id, project_id, ticket_id, agent_id, state,"
        " reason, created_at, expires_at, lease_epoch, version)"
        " VALUES ('asg_00000001', ?, ?, ?, 'queued', 'Seeded reservation.',"
        " datetime('now'), ?, 0, 1)",
        (PROJECT_ID, TICKET_ID, AGENT_ID, in_seconds(3600)))

    store.conn.execute(
        "INSERT INTO agent_tokens (token_hash, agent_id, project_id, session_id,"
        " created_at) VALUES (?, ?, ?, ?, datetime('now'))",
        (hash_secret("stub-agent-token"), AGENT_ID, PROJECT_ID, SESSION_ID))
    store.conn.execute(
        "INSERT INTO operators (id, project_id, display_name, role, created_at)"
        " VALUES ('mem_operator1', ?, 'Operator', 'owner', datetime('now'))",
        (PROJECT_ID,))
    store.conn.execute(
        "INSERT INTO operator_sessions (token_hash, operator_id, project_id,"
        " csrf_hash, created_at, expires_at)"
        " VALUES (?, 'mem_operator1', ?, ?, datetime('now'), ?)",
        (hash_secret("stub-session"), PROJECT_ID, hash_secret("stub-csrf"),
         in_seconds(3600)))

    httpd = make_server(board, host="127.0.0.1", port=0)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    return "http://127.0.0.1:{}".format(httpd.server_address[1]), httpd, thread, board


@pytest.fixture()
def results(seeded):
    mine = [c for c in cases_module.ALL_CASES if c.operation_id in MY_LANE]
    # 19 operations, 22 cases: decideReview has two variants and postHookEvent
    # three. Pinned so a new case in another lane cannot slip in unnoticed.
    assert len(mine) == 22, "case selection drifted; re-read cases.py"
    return harness.run(seeded, cases=mine)


def test_every_read_screen_passes_someone_elses_harness(results):
    reads = [r for r in results if r.kind == "read"]
    assert len(reads) == len(READS)
    failed = [(r.label, r.errors) for r in reads if not r.passed]
    assert failed == [], failed


def test_every_mutation_route_refuses_an_actor_in_the_body(results):
    """Frozen decision 1, checked route by route by a harness from another lane."""
    rejections = [r for r in results if r.kind == "actor-reject"]
    assert rejections, "no mutation cases ran"
    failed = [(r.label, r.actual_status, r.errors)
              for r in rejections if not r.passed]
    assert failed == [], failed


def test_nothing_produced_a_body_that_fails_its_schema(results):
    """The failure that would matter, separated from the ones that do not.

    A `status 409 != expected 200` here is the fixtures' fixed version chain
    meeting a real stateful board. A schema error would be this server sending
    something the console cannot parse, and there are none.
    """
    schema_failures = [
        (r.label, r.errors) for r in results
        if not r.passed and any(not e.startswith("status ") for e in r.errors)
    ]
    assert schema_failures == [], schema_failures


def test_no_route_answered_outside_the_contracts_status_enum(results):
    """No 500s, no statuses `ErrorResponse` cannot describe."""
    outside = [(r.label, r.actual_status) for r in results
               if r.actual_status not in CONTRACT_STATUSES]
    assert outside == [], outside


def test_the_report_is_recorded_so_the_conflicts_are_visible(results, capsys):
    """Print the per-case outcome. Numbers a reviewer can reproduce beat a claim."""
    lines = ["{:<34} {:>4}  {}".format(
        r.label, r.actual_status, "ok" if r.passed else "; ".join(r.errors))
        for r in results]
    print("\n".join(lines))
    passed = [r for r in results if r.passed]
    # Stated rather than asserted upward: the exact number moves if the
    # fixtures' version chain changes, and pinning it would make this test
    # about the fixtures instead of about the server.
    assert len(passed) >= len(READS) + len(
        [r for r in results if r.kind == "actor-reject"])


# ------------------------------------------- why the mutations conflict

def test_the_harness_mints_a_fresh_request_id_per_mutation(board_factory):
    """The shared fixture key would conflict; the harness no longer shares it.

    Every request fixture in `tests/fixtures/` carries the *same*
    `request_id` -- `0f1e2d3c-4b5a-4968-8776-655443322110`. Against the
    fixture-replay stub that is invisible. Against a real board, replaying it
    means the first mutation spends the key and every later one with a
    different body is 409 `request_id_reused`, exactly as the contract
    requires -- which used to fail `claimTicket` whenever it ran after
    `createTicket` and said nothing about the route.

    T-210 gave each mutation its own generated key, so the cases are now
    independent of each other's order. Both halves are asserted here: the run
    is clean, and the contract behaviour the old failure relied on is still
    live on the wire when a key really is reused.
    """
    import urllib.request
    import urllib.error

    claim = next(c for c in cases_module.ALL_CASES
                 if c.operation_id == "claimTicket")
    create = next(c for c in cases_module.ALL_CASES
                  if c.operation_id == "createTicket")

    alone = harness.run(board_factory("alone"), cases=[claim])
    assert [r.passed for r in alone if r.kind == "mutate"] == [True], \
        [(r.label, r.errors) for r in alone]

    together = harness.run(board_factory("together"), cases=[create, claim])
    failing = [r for r in together if r.kind == "mutate" and not r.passed]
    assert failing == [], \
        "fresh request_ids should make the cases order-independent: " + str(
            [(r.label, r.errors) for r in failing])

    # The 409 itself is not gone, only the harness's collision with it: send the
    # fixture bodies raw, with their shared literal key, and read the code off
    # the wire.
    base = board_factory("witness")
    for case in (create, claim):
        route = harness.ROUTES[case.operation_id]
        url = base.rstrip("/") + route.path.format(**case.path_values)
        request = urllib.request.Request(
            url, data=json.dumps(case.request.load()).encode(),
            method=route.method,
            headers={"X-Project-Id": PROJECT_ID,
                     "Content-Type": "application/json",
                     "Authorization": "Bearer stub-agent-token",
                     "Cookie": "tb_session=stub-session",
                     "X-CSRF-Token": "stub-csrf",
                     "Origin": "http://127.0.0.1:4319"})
        try:
            urllib.request.urlopen(request, timeout=5).read()
            code = None
        except urllib.error.HTTPError as error:
            code = json.loads(error.read().decode())["error"]["code"]
    assert code == "request_id_reused"
