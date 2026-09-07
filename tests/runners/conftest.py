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
"""

from __future__ import annotations

import io
import json
import sys
import urllib.error
import uuid
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
SRC = REPO / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from ticket_board.runners import RunnerClient  # noqa: E402
from ticket_board.runners.launcher import LaunchFailed, LaunchResult  # noqa: E402
from ticket_board.server import BoardServer  # noqa: E402
from ticket_board.server.wire import Request  # noqa: E402


def rid():
    return str(uuid.uuid4())


class _Response:
    def __init__(self, status, body):
        self.status = status
        self._body = body

    def read(self):
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def in_process_opener(server):
    """A urlopen replacement that dispatches into the router directly."""

    def opener(req, timeout=None):
        url = req.full_url
        path = url.split("://", 1)[-1].split("/", 1)[-1]
        path, _, query = path.partition("?")
        response = server.handle(Request(
            req.get_method(), "/" + path.lstrip("/"), query=query,
            headers={k.lower(): v for k, v in req.headers.items()},
            body=req.data or b"",
        ))
        body = json.dumps(response.body).encode() if response.body is not None else b""
        if response.status >= 400:
            raise urllib.error.HTTPError(url, response.status, "error", {},
                                         io.BytesIO(body))
        return _Response(response.status, body)

    return opener


class FakeProcess:
    """Stands in for a `claude` child. Never runs anything."""

    def __init__(self, pid=4242, returncode=0, stdout="done", stderr="",
                 hang=False):
        self.pid = pid
        self.returncode = returncode
        self._stdout = stdout
        self._stderr = stderr
        self._hang = hang
        self.terminated = False
        self.stdin = io.StringIO()

    def communicate(self, timeout=None):
        if self._hang:
            import subprocess
            raise subprocess.TimeoutExpired("claude", timeout or 0)
        return self._stdout, self._stderr

    def poll(self):
        return None if self._hang else self.returncode

    def terminate(self):
        self.terminated = True
        self._hang = False
        self.returncode = -15

    def wait(self, timeout=None):
        return self.returncode

    def kill(self):                                        # pragma: no cover
        self.terminated = True


class FakeLauncher:
    """Records what it was asked to launch, and launches nothing."""

    def __init__(self, process=None, fail=None):
        self.process = process or FakeProcess()
        self.fail = fail
        self.specs = []

    def start(self, spec):
        self.specs.append(spec)
        if self.fail is not None:
            raise LaunchFailed(self.fail)
        from ticket_board.runners.launcher import LaunchHandle
        return LaunchHandle(self.process, spec)

    def launch(self, spec):                                # pragma: no cover
        return self.start(spec).wait(timeout=spec.timeout_seconds)


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


def enroll(server, project, operator, name="runner-1"):
    """Enrollment -> exchange, the same path a real connected agent takes."""
    created = server.handle(Request("POST", "/enrollments", headers={
        "x-project-id": project["id"],
        "cookie": "tb_session=" + operator["session_token"],
        "x-csrf-token": operator["csrf_token"],
        "origin": "http://127.0.0.1:4319",
        "content-type": "application/json",
    }, body=json.dumps({
        "request_id": rid(), "agent_name": name, "role": "backend",
        "capabilities": ["python"], "connection_mode": "managed",
        "max_active_tickets": 1,
    }).encode()))
    assert created.status == 201, created.body
    session_id = "ses_" + uuid.uuid4().hex[:8]
    exchanged = server.handle(Request("POST", "/sessions", headers={
        "x-project-id": project["id"], "content-type": "application/json",
    }, body=json.dumps({
        "request_id": rid(), "code": created.body["code"],
        "session_id": session_id,
        "runtime": {"adapter": "claude_code", "version": "2.1.0"},
    }).encode()))
    assert exchanged.status == 201, exchanged.body
    return {"agent_id": exchanged.body["agent"]["id"],
            "session_id": session_id,
            "token": exchanged.body["token"]}


@pytest.fixture()
def agent(server, project, operator):
    return enroll(server, project, operator)


@pytest.fixture()
def client(server, project, agent):
    return RunnerClient("http://board.test", project["id"], agent["token"],
                        opener=in_process_opener(server), retries=0,
                        sleep=lambda _s: None)


def make_wake_job(server, project, agent_id, *, body="Do the thing.",
                  ticket_id=None, wake_job_id=None):
    """A committed task message with a delivery and a wake job.

    Built through the store rather than `POST /messages/{id}/task`, because
    that route is T-187's and is not merged yet. The records are the same ones
    it writes -- this is the storage the runner consumes, not a stand-in.
    """
    store = server.store
    cached = getattr(server, "_test_channel", None)
    if cached is None:
        author = store.create_member(project["id"], "human", "Operator", "owner")
        channel = store.create_channel(project["id"], "work", "public")
        cached = (author, channel)
        server._test_channel = cached
    author, channel = cached
    sent = store.send_message(
        project["id"], channel["id"],
        {"type": "member", "id": author["id"], "display_name": "Operator"},
        body, intent="task", ticket_id=ticket_id,
        recipient_agent_ids=[agent_id])
    delivery = sent["deliveries"][0]
    return store.create_wake_job(project["id"], sent["message"]["id"], agent_id,
                                 delivery["id"], ticket_id=ticket_id,
                                 wake_job_id=wake_job_id)
