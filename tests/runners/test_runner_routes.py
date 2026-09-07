"""The board's half of the runner surface: fencing, leasing and refusals.

These drive the real router through the real client, so an assertion here is
about the behaviour a supervisor will actually meet -- including the error
codes, which are the part a runner has to branch on.
"""

from __future__ import annotations

import pytest

from conftest import enroll, in_process_opener, make_wake_job, rid  # noqa: F401
from ticket_board.runners import ApiError, RunnerClient


def test_register_takes_a_fenced_lease_and_a_second_runner_is_refused(
        server, project, agent, client):
    lease = client.register("rnr_aaaa1111", agent["agent_id"], "/tmp/wt")
    assert lease["epoch"] == 1
    assert lease["concurrency"] == 1
    assert lease["allowlisted_worktree"] == "/tmp/wt"

    # A second supervisor for the same agent, arriving while the first lease is
    # live, is the failure this whole mechanism exists to prevent.
    with pytest.raises(ApiError) as caught:
        client.register("rnr_bbbb2222", agent["agent_id"], "/tmp/wt")
    assert caught.value.status == 409
    assert caught.value.code == "run_already_active"


def test_a_runner_cannot_register_for_another_agent(server, project, operator,
                                                    client, agent):
    other = enroll(server, project, operator, name="runner-2")
    with pytest.raises(ApiError) as caught:
        client.register("rnr_cccc3333", other["agent_id"], "/tmp/wt")
    assert caught.value.status == 403
    assert caught.value.code == "forbidden_scope"

    # And the body's agent_id did not quietly take a lease for the other agent.
    from ticket_board.storage.errors import NotFound
    with pytest.raises(NotFound):
        server.store.get_runner_lease(project["id"], other["agent_id"])


def test_jobs_before_register_is_refused_not_silently_empty(client, agent):
    with pytest.raises(ApiError) as caught:
        client.jobs("rnr_aaaa1111", wait_seconds=0)
    assert caught.value.status == 403
    assert "register" in caught.value.message


def test_jobs_from_a_superseded_runner_id_is_409(server, project, agent, client):
    client.register("rnr_aaaa1111", agent["agent_id"], "/tmp/wt")
    with pytest.raises(ApiError) as caught:
        client.jobs("rnr_dddd4444", wait_seconds=0)
    assert caught.value.code == "run_already_active"


def test_leasing_mints_a_run_and_the_id_is_derivable(server, project, agent,
                                                     client):
    client.register("rnr_aaaa1111", agent["agent_id"], "/tmp/wt")
    job = make_wake_job(server, project, agent["agent_id"])

    page = client.jobs("rnr_aaaa1111", wait_seconds=0)
    assert [j["id"] for j in page["items"]] == [job["id"]]
    assert page["items"][0]["state"] == "leased"
    assert page["items"][0]["attempts"] == 1

    # WakeJobListResponse cannot carry the run, so the runner derives it.
    run = server.store.get_run(project["id"], "run_" + job["id"][4:])
    assert run["wake_job_id"] == job["id"]
    assert run["state"] == "pending"
    assert run["session_id"] is None       # retained only at `starting`


def test_a_busy_agent_gets_an_empty_page_and_the_job_stays_queued(
        server, project, agent, client):
    client.register("rnr_aaaa1111", agent["agent_id"], "/tmp/wt")
    first = make_wake_job(server, project, agent["agent_id"], body="one")
    second = make_wake_job(server, project, agent["agent_id"], body="two")

    assert [j["id"] for j in client.jobs("rnr_aaaa1111", wait_seconds=0)["items"]] \
        == [first["id"]]
    # Concurrency is 1. The second job is not handed out while the first run is
    # live -- it queues behind the claim rather than starting a parallel run.
    page = client.jobs("rnr_aaaa1111", wait_seconds=0)
    assert page["items"] == []
    assert page["poll_after_seconds"] >= 1
    states = {j["id"]: j["state"]
              for j in server.store.list_wake_jobs(project["id"])["items"]}
    assert states[second["id"]] == "pending"


def test_a_terminal_run_releases_the_next_job(server, project, agent, client):
    client.register("rnr_aaaa1111", agent["agent_id"], "/tmp/wt")
    first = make_wake_job(server, project, agent["agent_id"], body="one")
    second = make_wake_job(server, project, agent["agent_id"], body="two")
    client.jobs("rnr_aaaa1111", wait_seconds=0)

    run_id = "run_" + first["id"][4:]
    client.run_event(run_id, "starting", expected_version=1,
                     session_id="ses_child001")
    run = client.run_event(run_id, "started", expected_version=2,
                           session_id="ses_child001")
    client.run_event(run_id, "responded", expected_version=run["version"],
                     reason="Replied in thread.")

    # The finished job is closed out, not left to be redelivered...
    states = {j["id"]: j["state"]
              for j in server.store.list_wake_jobs(project["id"])["items"]}
    assert states[first["id"]] == "completed"
    # ...and the queued one is now startable.
    assert [j["id"] for j in client.jobs("rnr_aaaa1111", wait_seconds=0)["items"]] \
        == [second["id"]]


def test_a_lease_that_expires_before_the_spawn_is_an_honest_retry(
        server, project, agent, client):
    """Nothing was started, so nothing outside the board happened: retry it.

    And it is bounded -- three attempts, then the inspectable failed queue,
    not a job that is handed out forever.
    """
    store = server.store
    client.register("rnr_aaaa1111", agent["agent_id"], "/tmp/wt")
    job = make_wake_job(server, project, agent["agent_id"])
    lease = store.get_runner_lease(project["id"], agent["agent_id"])

    for attempt in (1, 2, 3):
        leased = store.lease_wake_jobs(project["id"], agent["agent_id"],
                                       "rnr_aaaa1111", epoch=lease["epoch"],
                                       lease_seconds=0)
        assert [j["id"] for j in leased["items"]] == [job["id"]], attempt
        assert leased["items"][0]["attempts"] == attempt
        # One run, reused across attempts -- the id is derived from the job.
        assert store.get_run(project["id"], "run_" + job["id"][4:])["state"] \
            == "pending"

    exhausted = store.lease_wake_jobs(project["id"], agent["agent_id"],
                                      "rnr_aaaa1111", epoch=lease["epoch"])
    assert exhausted["items"] == []
    states = {j["id"]: j["state"]
              for j in store.list_wake_jobs(project["id"])["items"]}
    assert states[job["id"]] == "failed"


def test_a_lease_that_expires_after_the_spawn_is_not_retried(
        server, project, agent, client):
    """The opposite case, and the one that matters more.

    Once a run has left `pending` a Claude session was retained and a process
    was probably started. What it did outside the board is now unknowable, so
    the honest answer is a failed run that says so -- not a second execution of
    a task that may have half-finished.
    """
    store = server.store
    client.register("rnr_aaaa1111", agent["agent_id"], "/tmp/wt")
    job = make_wake_job(server, project, agent["agent_id"])
    lease = store.get_runner_lease(project["id"], agent["agent_id"])
    store.lease_wake_jobs(project["id"], agent["agent_id"], "rnr_aaaa1111",
                          epoch=lease["epoch"], lease_seconds=0)
    run_id = "run_" + job["id"][4:]
    client.run_event(run_id, "starting", expected_version=1,
                     session_id="ses_child001")

    store.lease_wake_jobs(project["id"], agent["agent_id"], "rnr_aaaa1111",
                          epoch=lease["epoch"])

    run = store.get_run(project["id"], run_id)
    assert run["state"] == "failed"
    assert "uncertain" in run["terminal_reason"]
    assert "needs review" in run["terminal_reason"]
    states = {j["id"]: j["state"]
              for j in store.list_wake_jobs(project["id"])["items"]}
    assert states[job["id"]] == "failed"


def test_cancel_is_cooperative_and_retains_the_run(server, project, agent,
                                                   client):
    client.register("rnr_aaaa1111", agent["agent_id"], "/tmp/wt")
    job = make_wake_job(server, project, agent["agent_id"])
    client.jobs("rnr_aaaa1111", wait_seconds=0)
    run_id = "run_" + job["id"][4:]

    canceled = client.cancel(run_id, expected_version=1,
                             reason="Operator canceled.")
    assert canceled["state"] == "canceled"
    assert canceled["terminal_reason"] == "Operator canceled."
    assert canceled["ended_at"] is not None
    # The record is retained, and the ticket claim on it is untouched.
    assert server.store.get_run(project["id"], run_id)["state"] == "canceled"


def test_an_agent_cannot_cancel_another_agents_run(server, project, operator,
                                                   agent, client):
    client.register("rnr_aaaa1111", agent["agent_id"], "/tmp/wt")
    job = make_wake_job(server, project, agent["agent_id"])
    client.jobs("rnr_aaaa1111", wait_seconds=0)

    other = enroll(server, project, operator, name="runner-2")
    intruder = RunnerClient("http://board.test", project["id"], other["token"],
                            opener=in_process_opener(server), retries=0)
    with pytest.raises(ApiError) as caught:
        intruder.cancel("run_" + job["id"][4:], expected_version=1,
                        reason="not mine")
    assert caught.value.status == 403


def test_wait_seconds_out_of_range_is_refused_not_clamped(server, project,
                                                          agent, client):
    client.register("rnr_aaaa1111", agent["agent_id"], "/tmp/wt")
    with pytest.raises(ApiError) as caught:
        client.jobs("rnr_aaaa1111", wait_seconds=600)
    assert caught.value.status == 400
    assert caught.value.details["rejected_fields"] == ["wait_seconds"]


def test_a_run_event_for_someone_elses_run_is_refused(server, project, operator,
                                                      agent, client):
    client.register("rnr_aaaa1111", agent["agent_id"], "/tmp/wt")
    job = make_wake_job(server, project, agent["agent_id"])
    client.jobs("rnr_aaaa1111", wait_seconds=0)

    other = enroll(server, project, operator, name="runner-2")
    intruder = RunnerClient("http://board.test", project["id"], other["token"],
                            opener=in_process_opener(server), retries=0)
    with pytest.raises(ApiError) as caught:
        intruder.run_event("run_" + job["id"][4:], "started",
                           expected_version=1, session_id="ses_child001")
    assert caught.value.status == 403


def test_the_body_may_not_carry_an_actor(server, project, agent, client):
    """Frozen decision 1 reaches the runner surface too."""
    status, payload = client._call(
        "POST", "/runners/register",
        body={"request_id": rid(), "runner_id": "rnr_aaaa1111",
              "agent_id": agent["agent_id"], "allowlisted_worktree": "/tmp/wt",
              "actor": {"type": "agent", "id": agent["agent_id"]}})
    assert status == 400
    assert payload["error"]["details"]["rejected_fields"] == ["actor"]


def test_the_wake_queue_is_fifo_within_a_single_second(server, project, agent,
                                                       client):
    """T-181's D2, one layer up.

    Board timestamps are second-granular, and real traffic bursts inside one
    second. With `ORDER BY created_at, id` the tie is broken by a *random*
    `wjb_...` suffix, so two task messages sent in the same second are executed
    in whichever order their ids happened to sort -- silently, and only
    sometimes. The ids here are pinned so the wrong ordering is not a coin
    flip: `wjb_zzzz9999` is committed first and sorts last.
    """
    store = server.store
    client.register("rnr_aaaa1111", agent["agent_id"], "/tmp/wt")
    first = make_wake_job(server, project, agent["agent_id"], body="first",
                          wake_job_id="wjb_zzzz9999")
    second = make_wake_job(server, project, agent["agent_id"], body="second",
                           wake_job_id="wjb_aaaa0000")
    assert first["created_at"] == second["created_at"], \
        "this test is only meaningful for jobs inside one second"

    leased = client.jobs("rnr_aaaa1111", wait_seconds=0)["items"]
    assert [j["id"] for j in leased] == [first["id"]], (
        "the wake queue must be first-in-first-out; it ordered by id instead")
    assert [j["id"] for j in store.list_wake_jobs(project["id"])["items"]] == \
        [first["id"], second["id"]]


def test_a_paused_run_is_not_reaped_as_an_orphan(server, project, agent, client):
    """`needs_approval` and `budget_reached` pause on purpose.

    A pause is a run waiting for a person. Sweeping it up as an orphan when the
    lease window passes would quietly discard the approval the pause exists to
    obtain -- and letting the agent move on to the next job would be a way to
    bypass an approval simply by waiting long enough.
    """
    store = server.store
    client.register("rnr_aaaa1111", agent["agent_id"], "/tmp/wt")
    job = make_wake_job(server, project, agent["agent_id"])
    lease = store.get_runner_lease(project["id"], agent["agent_id"])
    store.lease_wake_jobs(project["id"], agent["agent_id"], "rnr_aaaa1111",
                          epoch=lease["epoch"], lease_seconds=0)
    run_id = "run_" + job["id"][4:]
    client.run_event(run_id, "starting", expected_version=1,
                     session_id="ses_child001")
    run = client.run_event(run_id, "started", expected_version=2)
    client.run_event(run_id, "needs_approval", expected_version=run["version"],
                     reason="Permission request needs approval.")

    # A later poll, long after the job's lease window.
    later = make_wake_job(server, project, agent["agent_id"], body="next")
    assert client.jobs("rnr_aaaa1111", wait_seconds=0)["items"] == []

    paused = store.get_run(project["id"], run_id)
    assert paused["state"] == "paused"
    assert paused["needs_approval"] is True
    assert paused["terminal_reason"] == "Permission request needs approval."
    states = {j["id"]: j["state"]
              for j in store.list_wake_jobs(project["id"])["items"]}
    assert states[later["id"]] == "pending"

    # An operator cancelling it is what releases the agent.
    client.cancel(run_id, expected_version=paused["version"],
                  reason="Operator declined.")
    assert [j["id"] for j in client.jobs("rnr_aaaa1111", wait_seconds=0)["items"]] \
        == [later["id"]]


def test_an_expired_own_lease_says_renew_not_already_active(server, project,
                                                            agent, client):
    """The store's refusal is right; its sentence is not.

    `run_already_active` reads "A runner is already active for that agent",
    which is the wrong thing to tell a supervisor whose own lease simply ran
    out and whom nobody has replaced. The recovery is the same as never having
    registered, so the route says that instead.
    """
    client.register("rnr_aaaa1111", agent["agent_id"], "/tmp/wt")
    server.store.conn.execute(
        "UPDATE runner_leases SET expires_at = '2000-01-01T00:00:00Z'"
        " WHERE agent_id = ?", (agent["agent_id"],))

    with pytest.raises(ApiError) as caught:
        client.jobs("rnr_aaaa1111", wait_seconds=0)
    assert caught.value.status == 403
    assert "expired" in caught.value.message
    assert "register" in caught.value.message

    # And re-registering at the epoch it holds gets it working again.
    lease = client.register("rnr_aaaa1111", agent["agent_id"], "/tmp/wt",
                            expected_epoch=1)
    assert lease["runner_id"] == "rnr_aaaa1111"
    assert client.jobs("rnr_aaaa1111", wait_seconds=0)["items"] == []
