"""A real board on a real socket, and the callers that talk to it.

Named module rather than `conftest.py` helpers so the test files can import
these by name (this repo already has two `conftest.py` files that shadow each
other under deferred imports -- see `tests/server/api_client.py`).

Nothing here is a mock. `LiveBoard.start` calls the same `serve()` an operator
runs; `Http` is `urllib` over TCP; `FakeLauncher` is the ONE stand-in, for the
`claude` binary, and `test_live_claude.py` does without it.
"""

from __future__ import annotations

import io
import json
import socket
import subprocess
import sys
import urllib.error
import urllib.request
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

REPO = Path(__file__).resolve().parents[3]
SRC = REPO / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from ticket_board.runners import RunnerClient  # noqa: E402
from ticket_board.runners.launcher import LaunchFailed, LaunchHandle  # noqa: E402
from ticket_board.runners.supervisor import Supervisor  # noqa: E402
from ticket_board.server.httpd import serve  # noqa: E402


def rid() -> str:
    return str(uuid.uuid4())


def free_port() -> int:
    """Reserve a concrete port. `serve(port=0)` builds its base_url before it
    binds (T-280), so the OS-chosen port would never match the CSRF allowlist."""
    probe = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    probe.bind(("127.0.0.1", 0))
    port = probe.getsockname()[1]
    probe.close()
    return port


# ------------------------------------------------------------------ transport

@dataclass
class Resp:
    status: int
    body: Any
    headers: Dict[str, str]

    def json(self):
        return self.body

    def code(self) -> Optional[str]:
        if isinstance(self.body, dict) and "error" in self.body:
            return self.body["error"].get("code")
        return None


class Http:
    """One credentialled caller over TCP."""

    def __init__(self, base_url: str, project_id: str, *, cookie=None,
                 csrf=None, token=None, origin=None):
        self.base_url = base_url.rstrip("/")
        self.project_id = project_id
        self.cookie = cookie
        self.csrf = csrf
        self.token = token
        # Operator writes need an Origin the board allows; its own base_url is.
        self.origin = origin if origin is not None else (
            base_url if cookie else None)

    def _headers(self):
        h = {"X-Project-Id": self.project_id}
        if self.token:
            h["Authorization"] = "Bearer " + self.token
        if self.cookie:
            h["Cookie"] = "tb_session=" + self.cookie
        if self.csrf:
            h["X-CSRF-Token"] = self.csrf
        if self.origin:
            h["Origin"] = self.origin
        return h

    def call(self, method: str, path: str, body=None, query: str = "") -> Resp:
        url = self.base_url + path + ("?" + query if query else "")
        data = json.dumps(body).encode() if body is not None else None
        headers = self._headers()
        if data is not None:
            headers["Content-Type"] = "application/json"
        req = urllib.request.Request(url, data=data, method=method,
                                     headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                raw = r.read()
                return Resp(r.status, _decode(raw), dict(r.headers))
        except urllib.error.HTTPError as exc:
            raw = exc.read()
            return Resp(exc.code, _decode(raw), dict(exc.headers))

    def get(self, path, query=""):
        return self.call("GET", path, query=query)

    def post(self, path, body=None):
        return self.call("POST", path, body=body if body is not None else {})

    def delete(self, path, body=None):
        return self.call("DELETE", path, body=body if body is not None else {})


def _decode(raw: bytes):
    if not raw:
        return None
    try:
        return json.loads(raw.decode())
    except ValueError:
        return raw.decode(errors="replace")


# --------------------------------------------------------------------- board

@dataclass
class Agent:
    agent_id: str
    session_id: str
    token: str
    http: Http
    name: str
    member_id: Optional[str] = None


class LiveBoard:
    def __init__(self, tmp_path: Path):
        self.tmp_path = Path(tmp_path)
        self.port = free_port()
        self.base_url = "http://127.0.0.1:{}".format(self.port)
        self.httpd, self.thread, creds = serve(
            self.tmp_path / "board.sqlite3", host="127.0.0.1", port=self.port,
            project_name="T-190", state_dir=str(self.tmp_path), block=False)
        self.board = self.httpd.board
        # A long-poll that re-checks every 10 ms keeps the suite quick; the
        # value is a server tuning knob, not behaviour under test.
        self.board.runner_poll_interval = 0.01
        self.project_id = creds["project_id"]
        self.operator = Http(self.base_url, self.project_id,
                             cookie=creds["session_token"],
                             csrf=creds["csrf_token"])

    @property
    def store(self):
        return self.board.store

    def close(self):
        self.httpd.shutdown()
        self.httpd.server_close()
        self.board.close()

    # ----------------------------------------------------------- identities

    def operator_member(self) -> Dict[str, Any]:
        items = self.operator.get("/members").json()["items"]
        return next(m for m in items if m["kind"] == "human")

    def second_operator(self, name="Second", role="member") -> Http:
        session = self.board.bootstrap_operator(self.project_id, name, role=role)
        return Http(self.base_url, self.project_id,
                    cookie=session["session_token"], csrf=session["csrf_token"])

    def enroll(self, name: str, *, connection_mode="managed",
               role="backend", max_active_tickets=1) -> Agent:
        """Enrollment -> exchange over the socket, as a connected agent does.

        `max_active_tickets=1` is the design's concurrency=1 default; tests
        that need an agent to hold two claims say so explicitly.
        """
        created = self.operator.post("/enrollments", {
            "request_id": rid(), "agent_name": name, "role": role,
            "capabilities": ["python"], "connection_mode": connection_mode,
            "max_active_tickets": max_active_tickets,
        })
        assert created.status == 201, created.body
        session_id = "ses_" + uuid.uuid4().hex[:12]
        anonymous = Http(self.base_url, self.project_id)
        exchanged = anonymous.post("/sessions", {
            "request_id": rid(), "code": created.body["code"],
            "session_id": session_id,
            "runtime": {"adapter": "claude_code", "version": "2.1.263"},
        })
        assert exchanged.status == 201, exchanged.body
        token = exchanged.body["token"]
        agent = Agent(exchanged.body["agent"]["id"], session_id, token,
                      Http(self.base_url, self.project_id, token=token), name)
        for m in self.operator.get("/members").json()["items"]:
            if m["kind"] == "agent" and m["display_name"] == name:
                agent.member_id = m["id"]
        return agent

    # -------------------------------------------------------------- channels

    def channel(self, name: str, visibility="public", *, http: Http = None,
                designated_master: Optional[str] = None) -> Dict[str, Any]:
        created = (http or self.operator).post("/channels", {
            "request_id": rid(), "name": name, "visibility": visibility})
        assert created.status == 201, created.body
        channel = created.body
        if designated_master is not None:
            # The frozen CreateChannelRequest has no designated_master field;
            # a channel's master is configured out of band, so it is set here
            # the way the messaging unit tests set it.
            self.store.conn.execute(
                "UPDATE channels SET designated_master = ? WHERE id = ?",
                (designated_master, channel["id"]))
            self.store.conn.commit()
            channel["designated_master"] = designated_master
        return channel

    def dm(self, *member_ids: str) -> Dict[str, Any]:
        """A DM is a private `kind=dm` channel with exactly its participants.

        The frozen contract has no DM-creation route (recorded by T-187 for
        T-224), so a DM is minted through the store, exactly as a seed would.
        """
        channel = self.store.create_channel(
            self.project_id, "dm-" + uuid.uuid4().hex[:6], "private", kind="dm")
        for member_id in member_ids:
            self.store.add_channel_member(self.project_id, channel["id"],
                                          member_id)
        return channel

    # -------------------------------------------------------------- messages

    def say(self, http: Http, channel_id: str, text="hello", **extra) -> Resp:
        body = {"request_id": rid(), "channel_id": channel_id, "body": text,
                "intent": "message"}
        body.update(extra)
        return http.post("/messages", body)

    def task(self, http: Http, message_id: str, agent_id: Optional[str] = None,
             *, mode="direct", ticket=None,
             outcome="Do the thing and say so.", request_id=None) -> Resp:
        routing = {"mode": mode}
        if agent_id is not None:
            routing["agent_id"] = agent_id
        body = {"request_id": request_id or rid(), "outcome": outcome,
                "routing": routing,
                "ticket": ticket if ticket is not None else
                {"new_ticket": {"title": "T-190 task", "role": "backend"}}}
        return http.post("/messages/{}/task".format(message_id), body)

    def deliveries(self, message_id: str, http: Http = None) -> List[Dict]:
        r = (http or self.operator).get("/messages/{}/deliveries".format(message_id))
        assert r.status == 200, r.body
        return r.body["items"]

    def messages(self, channel_id: str, http: Http = None, thread_id=None) -> Dict:
        query = "channel_id=" + channel_id
        if thread_id:
            query += "&thread_id=" + thread_id
        r = (http or self.operator).get("/messages", query)
        assert r.status == 200, r.body
        return r.body

    def ticket(self, title="T-190 ticket", *, dependencies=None,
               http: Http = None) -> Dict[str, Any]:
        body = {"request_id": rid(), "title": title,
                "outcome": "verified", "acceptance": ["it is verified"]}
        if dependencies:
            body["dependencies"] = list(dependencies)
        r = (http or self.operator).post("/tickets", body)
        assert r.status == 201, r.body
        return r.body["ticket"] if "ticket" in r.body else r.body

    # ---------------------------------------------------------------- runs

    def run(self, run_id: str) -> Dict[str, Any]:
        return self.store.get_run(self.project_id, run_id)

    def wake_job(self, wake_job_id: str) -> Dict[str, Any]:
        row = self.store.conn.execute(
            "SELECT * FROM wake_jobs WHERE id = ?", (wake_job_id,)).fetchone()
        return dict(row) if row is not None else None

    def audit(self, action_prefix: str, subject_id: Optional[str] = None) -> List[str]:
        rows = self.store.conn.execute(
            "SELECT action FROM audit_events WHERE project_id = ?"
            " AND action LIKE ? ORDER BY rowid",
            (self.project_id, action_prefix + "%")).fetchall()
        if subject_id is None:
            return [r["action"] for r in rows]
        rows = self.store.conn.execute(
            "SELECT action FROM audit_events WHERE project_id = ?"
            " AND action LIKE ? AND subject_id = ? ORDER BY rowid",
            (self.project_id, action_prefix + "%", subject_id)).fetchall()
        return [r["action"] for r in rows]

    # ------------------------------------------------------------ supervisor

    def runner_client(self, agent: Agent) -> RunnerClient:
        return RunnerClient(self.base_url, self.project_id, agent.token,
                            retries=0, sleep=lambda _s: None)

    def supervisor(self, agent: Agent, *, launcher=None, state_dir=None,
                   worktree=None, budget=None, prompt_builder=None,
                   permission_policy="prompt") -> Supervisor:
        state_dir = Path(state_dir or self.tmp_path / ("state-" + agent.name))
        worktree = Path(worktree or self.tmp_path / ("wt-" + agent.name))
        worktree.mkdir(parents=True, exist_ok=True)
        return Supervisor(self.runner_client(agent), agent_id=agent.agent_id,
                          session_id=agent.session_id, worktree=worktree,
                          state_dir=state_dir,
                          launcher=launcher or FakeLauncher(),
                          budget=budget, prompt_builder=prompt_builder,
                          permission_policy=permission_policy)


# --------------------------------------------------------------- the fake

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


@dataclass
class FakeLauncher:
    """Records every LaunchSpec and launches nothing."""

    process: FakeProcess = field(default_factory=FakeProcess)
    fail: Optional[str] = None
    specs: List[Any] = field(default_factory=list)
    on_start: Optional[Any] = None

    def start(self, spec):
        self.specs.append(spec)
        if self.fail is not None:
            raise LaunchFailed(self.fail)
        if self.on_start is not None:
            self.on_start(spec)
        return LaunchHandle(self.process, spec)
