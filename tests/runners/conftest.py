"""Fixtures for the managed runner.

The runner talks HTTP, so its tests could have used a socket -- and then every
one of them would be measuring the loopback stack as well as the behaviour
under test. Instead `in_process_opener` swaps `urllib.request.urlopen` for a
function that hands the request straight to `BoardServer.handle`. The client's
own code path is unchanged: it still builds a real `urllib.request.Request`,
still reads a real `HTTPError` body, still applies its own retry policy. What
disappears is the socket, not the transport logic.

The two behaviours that genuinely need a process -- a real `claude` binary, and
a real clock -- get a fake launcher and an injected clock respectively, so
nothing here spawns a model or sleeps for a budget.

Importable helpers live in `tests/runner_helpers.py` so a combined
`pytest tests/runners tests/server` cannot shadow them via the bare `conftest`
module name (T-544).
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
SRC = REPO / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from tests.runner_helpers import (  # noqa: E402,F401
    FakeLauncher, FakeProcess, enroll, in_process_opener, make_wake_job, rid,
)
from ticket_board.runners import RunnerClient  # noqa: E402
from ticket_board.server import BoardServer  # noqa: E402


@pytest.fixture()
def server(tmp_path):
    s = BoardServer(tmp_path / "board.sqlite3", runner_lease_seconds=60,
                    runner_poll_interval=0.01)
    try:
        yield s
    finally:
        s.close()


@pytest.fixture()
def project(server):
    return server.store.create_project("Demo")


@pytest.fixture()
def operator(server, project):
    return server.bootstrap_operator(project["id"])


@pytest.fixture()
def agent(server, project, operator):
    return enroll(server, project, operator)


@pytest.fixture()
def client(server, project, agent):
    return RunnerClient("http://board.test", project["id"], agent["token"],
                        opener=in_process_opener(server), retries=0,
                        sleep=lambda _s: None)
