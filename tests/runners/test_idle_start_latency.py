"""The <=5s acceptance target, measured rather than inherited.

The design doc is explicit that 5 seconds is *a target, not an observed
result*, so this module produces an observation and is equally explicit about
what the number does and does not include.

**Measured here:** the interval from a task message being committed to the
board, to the run being reported `started` -- that is, the wake job appearing,
a long-polling supervisor noticing it, the ticket claim, the process spawn and
the `started` report. This is the part this ticket built and the part a change
in this repo can regress.

**Not measured here, and not claimed:** the time `claude` itself takes to come
up and produce a first token. The child is a fake process. Including a real
model launch would make this a measurement of Anthropic's availability and the
machine's load, which is not a thing a test suite can assert a bound on -- and
this machine currently runs several live Claude agents, so its load is not
representative of anything.

So the honest reading is: **the board-and-supervisor overhead has this much of
the 5s budget left over for Claude's own startup.** The threshold below is
deliberately far under 5s for that reason; if the overhead alone approached the
target there would be no budget left for the actual work.
"""

from __future__ import annotations

import threading
import time

import pytest

from tests.runner_helpers import FakeLauncher, make_wake_job
from ticket_board.runners import Supervisor
from ticket_board.storage import BoardStore

# The overhead budget. The acceptance target is 5s end to end; this asserts the
# machinery leaves nearly all of it for the model. Loose enough not to be flaky
# on a loaded machine, tight enough that a regression to per-second polling
# (a 20x jump) fails it outright.
OVERHEAD_BUDGET_SECONDS = 1.5


@pytest.fixture()
def supervisor(server, project, agent, client, tmp_path):
    return Supervisor(client, agent_id=agent["agent_id"],
                      session_id=agent["session_id"], worktree=tmp_path,
                      state_dir=tmp_path / "state", launcher=FakeLauncher())


def test_an_idle_runner_starts_well_inside_the_five_second_target(
        server, project, agent, supervisor, tmp_path, capsys):
    """A genuinely idle supervisor: registered, long-polling, nothing queued.

    The wake job is committed from a second thread on its own connection --
    the server's store belongs to the main thread, and sqlite connections are
    not shared across threads. That is also the realistic shape: whoever sends
    a task message is not the process that is waiting for it.
    """
    supervisor.register()
    db_path = server.store.conn.execute("PRAGMA database_list").fetchone()[2]

    committed = {}

    def commit_after_a_moment():
        time.sleep(0.25)          # the supervisor is already waiting by now
        writer = BoardStore(db_path)
        try:
            author = server._test_channel[0]
            channel = server._test_channel[1]
            sent = writer.send_message(
                project["id"], channel["id"],
                {"type": "member", "id": author["id"],
                 "display_name": "Operator"},
                "Start the thing.", intent="task",
                recipient_agent_ids=[agent["agent_id"]])
            committed["at"] = time.monotonic()
            writer.create_wake_job(project["id"], sent["message"]["id"],
                                   agent["agent_id"],
                                   sent["deliveries"][0]["id"])
        finally:
            writer.close()

    # The channel and author have to exist before the writer thread starts.
    make_wake_job(server, project, agent["agent_id"], body="seed")
    server.store.conn.execute("DELETE FROM wake_jobs")
    server.store.conn.execute("DELETE FROM runs")

    thread = threading.Thread(target=commit_after_a_moment)
    thread.start()
    outcomes = supervisor.poll_once(wait_seconds=5)
    thread.join()

    assert [o.state for o in outcomes] == ["responded"]
    elapsed = time.monotonic() - committed["at"]
    print("\nidle start overhead: {:.3f}s "
          "(board + supervisor only; claude startup excluded)".format(elapsed))
    assert elapsed < OVERHEAD_BUDGET_SECONDS, (
        "commit-to-started took {:.3f}s, which leaves too little of the 5s "
        "acceptance target for Claude's own startup".format(elapsed))


def test_an_empty_long_poll_returns_when_asked_and_not_later(supervisor):
    """The wait is honoured, not overshot.

    A poll loop that sleeps past its deadline turns a 1s wait into a 2s one,
    and that error compounds straight into the acceptance target.
    """
    supervisor.register()
    began = time.monotonic()
    assert supervisor.poll_once(wait_seconds=1) == []
    elapsed = time.monotonic() - began
    assert 0.9 <= elapsed < 1.6, elapsed


def test_wait_zero_does_not_wait(supervisor):
    supervisor.register()
    began = time.monotonic()
    assert supervisor.poll_once(wait_seconds=0) == []
    assert time.monotonic() - began < 0.3
