"""Importable runner-test helpers.

Lives outside any `conftest.py` on purpose (same reason as
`tests/server/api_client.py`). Pytest loads each directory's conftest under the
bare name `conftest`, so `from conftest import in_process_opener` collected
together with `tests/server` resolves to the server module and raises
ImportError. A uniquely named module cannot.
"""

from __future__ import annotations

import io
import json
import urllib.error
import uuid

from ticket_board.runners.launcher import LaunchFailed
from ticket_board.server.wire import Request


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
