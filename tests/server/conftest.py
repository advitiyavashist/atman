"""Fixtures for the API tests.

The client drives `BoardServer.handle` directly -- see `api_client.py`. There is
no socket here on purpose: the router is a pure function, so these tests are fast
and deterministic, and the two behaviours that genuinely need a network (SSE
reconnect and the cross-process claim race) get a real server in their own
modules.
"""

import sys
import uuid
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
SRC = REPO / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from api_client import Client, rid  # noqa: E402,F401  (re-exported for tests)
from ticket_board.server import BoardServer  # noqa: E402


@pytest.fixture()
def db_path(tmp_path):
    return tmp_path / "board.sqlite3"


@pytest.fixture()
def server(db_path):
    s = BoardServer(db_path)
    try:
        yield s
    finally:
        s.close()


@pytest.fixture()
def project(server):
    return server.store.create_project("Demo")


@pytest.fixture()
def operator_session(server, project):
    return server.bootstrap_operator(project["id"])


@pytest.fixture()
def operator(server, project, operator_session):
    return Client(server, project_id=project["id"],
                  cookie=operator_session["session_token"],
                  csrf=operator_session["csrf_token"])


@pytest.fixture()
def anonymous(server, project):
    return Client(server, project_id=project["id"])


@pytest.fixture()
def enrolled(server, project, operator):
    """An enrolled, connected agent: enrollment -> exchange -> live credentials."""
    created = operator.post("/enrollments", {
        "request_id": rid(), "agent_name": "backend-1", "role": "backend",
        "capabilities": ["python"], "connection_mode": "managed",
        "max_active_tickets": 1,
    })
    assert created.status == 201, created.json()
    code = created.json()["code"]
    session_id = "ses_" + uuid.uuid4().hex[:8]
    exchanged = Client(server, project_id=project["id"]).post("/sessions", {
        "request_id": rid(), "code": code, "session_id": session_id,
        "runtime": {"adapter": "claude_code", "version": "2.1.0"},
    })
    assert exchanged.status == 201, exchanged.json()
    payload = exchanged.json()
    return {
        "agent": payload["agent"],
        "agent_id": payload["agent"]["id"],
        "session_id": session_id,
        "token": payload["token"],
        "client": Client(server, project_id=project["id"], token=payload["token"],
                         origin=None),
    }


@pytest.fixture()
def ticket(operator):
    """A created ticket with acceptance criteria, ready to be claimed."""
    created = operator.post("/tickets", {
        "request_id": rid(),
        "title": "Add the widget endpoint",
        "outcome": "GET /widgets returns a cursor page and is covered by tests.",
        "acceptance": [{"text": "Cursor paging works"}],
        "role": "backend",
        "files": ["src/widgets/"],
    })
    assert created.status == 201, created.json()
    return created.json()
