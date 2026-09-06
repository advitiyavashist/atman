"""Fixtures for the routing loop.

The sweep is a store-level object, so these tests drive it directly rather than
over HTTP -- the API surface it uses (`server/master.py`) already has its own
conformance tests in tests/server/. What matters here is the decision, and the
fence.

Agents are made live by giving them a real unexpired session lease and a fresh
heartbeat, because `derive_agent_state` is what the policy consults and a
default agent row reads as offline. Anything that wants an offline agent says so
explicitly.
"""

import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
SRC = REPO / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from ticket_board.server import BoardServer            # noqa: E402
from ticket_board.server import master as master_ops   # noqa: E402
from ticket_board.server.auth import in_seconds        # noqa: E402
from ticket_board.storage import ids                   # noqa: E402
from ticket_board.orchestrator import Sweeper          # noqa: E402


@pytest.fixture()
def server(tmp_path):
    s = BoardServer(tmp_path / "board.sqlite3")
    try:
        yield s
    finally:
        s.close()


@pytest.fixture()
def store(server):
    return server.store


@pytest.fixture()
def project(store):
    return store.create_project("Demo")["id"]


@pytest.fixture()
def operator():
    return {"type": "master", "id": "mem_operator1",
            "display_name": "Operator", "session_id": "ses_operator"}


@pytest.fixture()
def make_agent(store):
    def _make(name, *, role=None, capabilities=None, max_active=1,
              live=True, worktree=None, heartbeat_age=0):
        agent = store.create_agent(project_id=_project_of(store), name=name,
                                   role=role, capabilities=capabilities or [],
                                   max_active_tickets=max_active)
        aid = agent["id"] if isinstance(agent, dict) else agent
        if live:
            store.open_session(aid, in_seconds(3600))
            store.conn.execute(
                "UPDATE agents SET last_heartbeat_at = ? WHERE id = ?",
                (in_seconds(-heartbeat_age), aid))
            store.conn.commit()
        if worktree is not None:
            store.conn.execute("UPDATE agents SET worktree = ? WHERE id = ?",
                               (worktree, aid))
            store.conn.commit()
        return aid
    return _make


def _project_of(store):
    return store.conn.execute("SELECT id FROM projects LIMIT 1").fetchone()["id"]


@pytest.fixture()
def make_ticket(store, project):
    counter = {"n": 0}

    def _make(title="A ticket", *, role=None, dependencies=None, files=None,
              state="open", owner=None, worktree=None):
        counter["n"] += 1
        tid = "DEMO-%d" % counter["n"]
        store.create_ticket(project, tid, title, role=role,
                            dependencies=dependencies, files=files)
        if state != "open" or owner or worktree:
            store.conn.execute(
                "UPDATE tickets SET state = ?, owner = ?, worktree = ?"
                " WHERE project_id = ? AND id = ?",
                (state, owner, worktree, project, tid))
            store.conn.commit()
        return tid
    return _make


@pytest.fixture()
def lease(store, project, operator):
    """A held lease at epoch 1, sweeps due immediately."""
    master_ops.take_lease(store, project, operator, expected_epoch=0)
    store.conn.execute(
        "UPDATE master_lease SET next_sweep_at = ? WHERE project_id = ?",
        (in_seconds(-1), project))
    store.conn.commit()
    return master_ops.current_epoch(store, project)


@pytest.fixture()
def sweeper(store, project, operator, lease):
    return Sweeper(store, project, operator, lease)
