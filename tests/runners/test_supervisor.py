"""The supervisor, driven end to end against a real board and a fake Claude.

Every test here goes through the real router, the real client and the real
state file. The only substitution is the child process, because spawning a
model in a unit test would measure Anthropic's availability rather than this
code -- and because five live Claude agents run on this machine and none of
them should meet a test's process.
"""

from __future__ import annotations

import json

import pytest

from conftest import FakeLauncher, FakeProcess, enroll, in_process_opener, make_wake_job
from ticket_board.runners import (
    ApiError,
    RunnerClient,
    RunOutcome,
    Supervisor,
    load_state,
    state_path,
)


@pytest.fixture()
def supervisor(server, project, agent, client, tmp_path):
    return Supervisor(client, agent_id=agent["agent_id"],
                      session_id=agent["session_id"],
                      worktree=tmp_path, state_dir=tmp_path / "state",
                      launcher=FakeLauncher(),
                      clock=iter_clock())


def iter_clock():
    """A monotonic clock that advances 0.4s per read, so latency is asserted
    on rather than raced against."""
    state = {"t": 0.0}

    def clock():
        state["t"] += 0.4
        return state["t"]
    return clock


def test_a_task_message_wakes_the_runner_and_the_run_reaches_responded(
        server, project, agent, supervisor):
    job = make_wake_job(server, project, agent["agent_id"], body="Fix the thing.")
    supervisor.register()

    outcomes = supervisor.poll_once(wait_seconds=0)

    assert [o.state for o in outcomes] == ["responded"]
    run = server.store.get_run(project["id"], "run_" + job["id"][4:])
    assert run["state"] == "responded"
    assert run["started_at"] is not None
    assert run["session_id"] == supervisor.launcher.specs[0].session_id
    # The receipt chain closed the job too, so it is not redelivered.
    states = {j["id"]: j["state"]
              for j in server.store.list_wake_jobs(project["id"])["items"]}
    assert states[job["id"]] == "completed"


def test_the_message_body_never_reaches_a_command_line(server, project, agent,
                                                       supervisor):
    """A task message is arbitrary text. It goes on stdin, and only on stdin."""
    make_wake_job(server, project, agent["agent_id"],
                  body="; rm -rf / #$(whoami)`id`")
    supervisor.register()
    supervisor.poll_once(wait_seconds=0)

    spec = supervisor.launcher.specs[0]
    from ticket_board.runners import build_argv
    argv = build_argv("/usr/local/bin/claude", spec)
    assert all("rm -rf" not in part for part in argv)
    assert "-p" in argv and "--session-id" in argv


def test_the_child_never_inherits_this_agents_board_identity(monkeypatch):
    """A spawned claude inherits the parent environment, and a TICKET_AGENT in
    it makes the child's own Stop hook post to this board under the
    supervisor's name. That is not hypothetical -- T-214 hit it."""
    from ticket_board.runners import child_env
    monkeypatch.setenv("TICKET_AGENT", "opus-infra")
    monkeypatch.setenv("TICKETS_DIR", "/Users/kavana/Downloads/steer/.tickets")
    monkeypatch.setenv("PATH", "/usr/bin")

    env = child_env()
    assert "TICKET_AGENT" not in env
    assert "TICKETS_DIR" not in env
    assert env["PATH"] == "/usr/bin"        # everything else is preserved


def test_a_duplicate_delivery_produces_one_execution(server, project, agent,
                                                     supervisor):
    """At-least-once delivery, exactly-once execution -- as far as local state
    can promise it. The second copy is recognised, not run."""
    job = make_wake_job(server, project, agent["agent_id"])
    supervisor.register()
    assert [o.state for o in supervisor.poll_once(wait_seconds=0)] == ["responded"]
    launched = len(supervisor.launcher.specs)

    # The same job, redelivered: same (message_id, recipient_agent_id).
    duplicate = dict(job, state="pending")
    outcome = supervisor.execute(duplicate)

    assert outcome.state == "skipped"
    assert len(supervisor.launcher.specs) == launched     # nothing spawned


def test_a_busy_supervisor_starts_no_parallel_run(server, project, agent,
                                                  supervisor):
    make_wake_job(server, project, agent["agent_id"], body="one")
    make_wake_job(server, project, agent["agent_id"], body="two")
    supervisor.register()

    first = supervisor.client.jobs(supervisor.state.runner_id, wait_seconds=0)
    assert len(first["items"]) == 1
    # While that job is leased the board hands out nothing else, whoever asks.
    assert supervisor.client.jobs(supervisor.state.runner_id,
                                  wait_seconds=0)["items"] == []


def test_the_run_is_retained_on_the_board_before_the_process_exists(
        server, project, agent, supervisor, tmp_path):
    """The contract retains Run.session_id before launching so a crash between
    claim and spawn is reconcilable. The local half of that promise is the
    state file, and it has to be on disk before `start()` is called."""
    seen = {}

    class WatchingLauncher(FakeLauncher):
        def start(self, spec):
            seen["state_on_disk"] = json.loads(
                state_path(tmp_path / "state").read_text())
            return super().start(spec)

    supervisor.launcher = WatchingLauncher()
    make_wake_job(server, project, agent["agent_id"])
    supervisor.register()
    supervisor.poll_once(wait_seconds=0)

    flight = seen["state_on_disk"]["in_flight"]
    assert flight is not None
    assert flight["run_id"].startswith("run_")
    assert flight["session_id"].startswith("ses_")
    assert flight["spawned"] is False       # written before, not after


def test_a_crash_between_claim_and_spawn_is_reviewed_not_rerun(
        server, project, agent, client, tmp_path):
    """The restart case. A second execution of a half-finished task is worse
    than telling a human the outcome is unclear."""
    job = make_wake_job(server, project, agent["agent_id"])
    first = Supervisor(client, agent_id=agent["agent_id"],
                       session_id=agent["session_id"], worktree=tmp_path,
                       state_dir=tmp_path / "state", launcher=FakeLauncher())
    first.register()
    first.client.jobs(first.state.runner_id, wait_seconds=0)   # mints the run
    first.client.run_event("run_" + job["id"][4:], "starting",
                           expected_version=1, session_id="ses_child001")
    # Simulate the crash: in-flight state on disk, no process, no report.
    from ticket_board.runners import InFlight, save_state
    first.state.in_flight = InFlight(
        run_id="run_" + job["id"][4:], wake_job_id=job["id"],
        dedupe_key=job["dedupe_key"], session_id="ses_child001",
        pid=None, spawned=True)
    save_state(tmp_path / "state", first.state)

    revived = Supervisor(client, agent_id=agent["agent_id"],
                         session_id=agent["session_id"], worktree=tmp_path,
                         state_dir=tmp_path / "state", launcher=FakeLauncher())
    # Same identity, not a new one: a fresh runner_id would fence out a
    # supervisor that might still be alive.
    assert revived.state.runner_id == first.state.runner_id
    revived.register()
    outcome = revived.reconcile()

    assert outcome.state == "failed"
    assert "uncertain" in outcome.reason.lower()
    assert revived.state.in_flight is None
    run = server.store.get_run(project["id"], "run_" + job["id"][4:])
    assert run["state"] == "failed"
    # ...and it is in the inspectable failed queue, not silently dropped.
    assert [f["dedupe_key"] for f in revived.state.failed] == [job["dedupe_key"]]
    assert revived.state.seen(job["dedupe_key"]) is True


def test_a_second_supervisor_is_fenced_out(server, project, agent, client,
                                           tmp_path):
    first = Supervisor(client, agent_id=agent["agent_id"],
                       session_id=agent["session_id"], worktree=tmp_path,
                       state_dir=tmp_path / "a", launcher=FakeLauncher())
    first.register()

    second = Supervisor(client, agent_id=agent["agent_id"],
                        session_id=agent["session_id"], worktree=tmp_path,
                        state_dir=tmp_path / "b", launcher=FakeLauncher())
    with pytest.raises(ApiError) as caught:
        second.register()
    assert caught.value.code == "run_already_active"


def test_a_missing_claude_binary_is_a_named_failure_not_a_green_run(
        server, project, agent, supervisor):
    """T-181's D1, one layer up: installed and runnable are different facts,
    and only execution separates them."""
    supervisor.launcher = FakeLauncher(
        fail="No executable `claude` on this machine (looked for 'claude').")
    job = make_wake_job(server, project, agent["agent_id"])
    supervisor.register()

    outcome = supervisor.poll_once(wait_seconds=0)[0]

    assert outcome.state == "failed"
    assert "claude" in outcome.reason
    run = server.store.get_run(project["id"], "run_" + job["id"][4:])
    assert run["state"] == "failed"
    assert run["started_at"] is None       # never reported as started


def test_a_run_over_its_time_budget_pauses_with_a_visible_reason(
        server, project, agent, supervisor):
    supervisor.launcher = FakeLauncher(FakeProcess(hang=True))
    supervisor.budget["max_seconds"] = 1
    job = make_wake_job(server, project, agent["agent_id"])
    supervisor.register()

    outcome = supervisor.poll_once(wait_seconds=0)[0]

    assert outcome.state == "budget_reached"
    run = server.store.get_run(project["id"], "run_" + job["id"][4:])
    assert run["state"] == "paused"
    assert "budget" in run["terminal_reason"].lower()
    assert supervisor.launcher.process.terminated is True


def test_the_permission_policy_comes_from_the_lease_not_the_supervisor(
        server, project, agent, client, tmp_path):
    """Nothing here widens what the operator configured."""
    from ticket_board.runners import build_argv
    from ticket_board.runners.launcher import LaunchSpec

    supervisor = Supervisor(client, agent_id=agent["agent_id"],
                            session_id=agent["session_id"], worktree=tmp_path,
                            state_dir=tmp_path / "state",
                            launcher=FakeLauncher(),
                            permission_policy="deny_all")
    supervisor.register()
    assert supervisor.permission_policy == "deny_all"

    make_wake_job(server, project, agent["agent_id"])
    supervisor.poll_once(wait_seconds=0)
    spec = supervisor.launcher.specs[0]
    assert spec.permission_policy == "deny_all"
    # `prompt` has no flag at all: a permission request surfaces and pauses,
    # rather than being auto-answered on the operator's behalf.
    assert build_argv("/bin/claude", LaunchSpec(
        session_id="ses_x", worktree=tmp_path, prompt="",
        permission_policy="prompt")).count("--permission-mode") == 0


def test_two_agents_on_one_board_do_not_see_each_others_work(
        server, project, operator, agent, client, tmp_path):
    """Two sessions, two supervisors, one board -- and no crosstalk."""
    other = enroll(server, project, operator, name="runner-2")
    other_client = RunnerClient("http://board.test", project["id"],
                                other["token"],
                                opener=in_process_opener(server), retries=0)
    mine = make_wake_job(server, project, agent["agent_id"], body="mine")
    theirs = make_wake_job(server, project, other["agent_id"], body="theirs")

    a = Supervisor(client, agent_id=agent["agent_id"],
                   session_id=agent["session_id"], worktree=tmp_path,
                   state_dir=tmp_path / "a", launcher=FakeLauncher())
    b = Supervisor(other_client, agent_id=other["agent_id"],
                   session_id=other["session_id"], worktree=tmp_path,
                   state_dir=tmp_path / "b", launcher=FakeLauncher())
    a.register()
    b.register()

    assert [o.wake_job_id for o in a.poll_once(wait_seconds=0)] == [mine["id"]]
    assert [o.wake_job_id for o in b.poll_once(wait_seconds=0)] == [theirs["id"]]
    assert a.state.seen(theirs["dedupe_key"]) is False


def test_state_survives_a_restart_and_a_corrupt_file_is_refused(tmp_path,
                                                                client, agent):
    supervisor = Supervisor(client, agent_id=agent["agent_id"],
                            session_id=agent["session_id"], worktree=tmp_path,
                            state_dir=tmp_path / "state",
                            launcher=FakeLauncher())
    supervisor.register()
    runner_id = supervisor.state.runner_id
    assert load_state(tmp_path / "state").runner_id == runner_id
    assert oct(state_path(tmp_path / "state").stat().st_mode)[-3:] == "600"

    state_path(tmp_path / "state").write_text("{not json")
    from ticket_board.runners import RunnerStateCorrupt
    with pytest.raises(RunnerStateCorrupt):
        load_state(tmp_path / "state")
