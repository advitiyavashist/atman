"""T-1103: the local JSON API (/api/v1) the TypeScript app builds against.

Fixture boards only: ATMAN_BOARD_CONFIG, TICKETS_DIR, HOME and
TICKETS_CACHE_DIR all live under tmp_path, and TICKET_SEAT, TICKET_AGENT and
TICKET_SESSION_ID are unset, so nothing here can reach the real board or a
live session. The real `atm ui` server (cmd_ui) runs in-process on a thread,
so read routes are audited with an audit hook in the same process. Every
route's real output is validated against its JSON Schema in docs/api/schemas.

The launch token, loopback Host gate and --host refusal are T-1105's (#266);
posting as the operator is T-1104's (#265). This file checks that the API
sits behind that gate and adds only its own origin allowlist.
"""
from __future__ import annotations

import argparse
import hashlib
import http.client
import importlib.util
import json
import os
import re
import socket
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
TOOL = ROOT / "tickets.py"
SCHEMAS = ROOT / "docs" / "api" / "schemas"
DOC = ROOT / "docs" / "api" / "app-v2.md"
OP = "ada"
HEAD = "9c1e5a0b7d3f2e1a4b6c8d0e2f4a6b8c0d1e3f50"
ACC = "24fb8c0aa1b2c3d4e5f60718293a4b5c6d7e8f90"
DEV = "http://localhost:5173"
SESSION_PREFIXES = ("CLAUDE", "CODEX_", "CURSOR_", "TICKET_", "TERM_SESSION", "ATMAN_")

READ_ROUTES = {
    # path -> schema
    "/api/v1/projects": "projects.json",
    "/api/v1/board": "board.json",
    "/api/v1/board?project=demo": "board.json",
    "/api/v1/board?seat=planner": "board.json",
    "/api/v1/plan": "plan.json",
    "/api/v1/plan?project=demo": "plan.json",
    "/api/v1/lead": "lead.json",
    "/api/v1/lead?project=demo": "lead.json",
    "/api/v1/thread": "thread.json",
    "/api/v1/thread?project=demo": "thread.json",
    "/api/v1/thread?with=coder&all=1": "thread.json",
    "/api/v1/needs-you": "needs-you.json",
    "/api/v1/needs-you?project=demo": "needs-you.json",
    "/api/v1/ticket/T-1": "ticket.json",
    "/api/v1/ticket/T-2": "ticket.json",
    "/api/v1/ticket/T-3?all=1": "ticket.json",
    "/api/v1/ticket/T-100?project=demo": "ticket.json",
}


def stamp(mins_ago):
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(time.time() - mins_ago * 60))


def write(path, obj):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj))


def seat(board, name, **extra):
    write(board / "agents" / (name + ".json"), dict({"owner": name, "seen": stamp(1)}, **extra))


def ticket(board, tid, title, **kw):
    write(board / (tid + ".json"), dict(dict(id=tid, title=title, status="open", role="backend",
                                             deps=[], notes=[], priority=2), **kw))


def msg(board, **m):
    with open(board / "messages.jsonl", "a") as f:
        f.write(json.dumps(dict({"at": stamp(5), "to": "", "re": "", "text": ""}, **m)) + "\n")


def lines(board):
    p = board / "messages.jsonl"
    return [json.loads(x) for x in p.read_text().splitlines() if x.strip()] if p.exists() else []


def tree_hash(*roots):
    h = {}
    for root in roots:
        if not Path(root).exists():
            continue
        for p in sorted(Path(root).rglob("*")):
            if p.is_file():
                h[str(p)] = hashlib.sha256(p.read_bytes()).hexdigest()
    return h


def seed_alpha(board):
    """Stub harness seats, tickets in every accept state, runs and messages."""
    for s in (OP, "planner", "coder", "rev", "scout", "dave"):
        seat(board, s)
    write(board / "workforce.json", {"planner": {"harness": "codex"}, "coder": {"harness": "claude"},
                                     "rev": {"harness": "codex"}, "dave": {"harness": "codex"}})
    write(board / "master.json", {"owner": "planner", "cos": "", "since": stamp(600), "lead": "planner"})
    write(board / "objective.json", {"text": "Alpha objective", "exit_criterion": "alpha ships", "state": "active"})
    ticket(board, "T-1", "Accepted base", status="done", owner="coder", review_head=ACC,
           review_events=[{"kind": "accept", "by": "rev", "at": stamp(300), "sha": ACC, "notes": "ok"}],
           notes=[{"by": "coder", "at": stamp(301), "text": "handoff: schema frozen"}])
    ticket(board, "T-2", "Done but never accepted", status="done", owner="scout",
           notes=[{"by": "scout", "at": stamp(200), "text": "done"}])
    ticket(board, "T-3", "In flight", status="claimed", owner="coder", deps=["T-1"], claimed_at=stamp(14),
           commit="t3-branch@" + HEAD, branch="t3-branch", pr="https://example.invalid/pr/7", review_head=HEAD,
           review_events=[{"kind": "reject", "by": "rev", "at": stamp(6), "sha": HEAD, "reason": "edges overlap"}],
           steers=[{"id": "ste-1", "kind": "ask", "from": OP, "seat": "coder", "text": "390 covered?",
                    "at": stamp(9), "receipt": "delivered-unconfirmed"}])
    ticket(board, "T-4", "Waits on open dep", deps=["T-3"], reserved_for="rev")
    ticket(board, "T-5", "Waits on unaccepted dep", deps=["T-2"])
    ticket(board, "T-6", "Posted to an offline seat", reserved_for="dave")
    ticket(board, "T-8", "Released by override, never accepted", status="done", owner="scout",
           release_override={"kind": "owner", "by": "planner", "at": stamp(50), "reason": "docs only"})
    events = [
        {"v": 1, "at": stamp(14), "kind": "run_start", "agent": "coder", "ticket": "T-3", "run_no": 1,
         "run_id": "r-coder-1", "harness": "claude", "trigger": ["holding"]},
        {"v": 1, "at": stamp(9), "kind": "run_start", "agent": "rev", "ticket": "T-3", "run_no": 1,
         "run_id": "r-rev-1", "harness": "codex", "trigger": ["review_queue"]},
        {"v": 1, "at": stamp(6), "kind": "run_end", "agent": "rev", "ticket": "T-3", "run_no": 1,
         "run_id": "r-rev-1", "exit": 0, "started_at": stamp(9), "ended_at": stamp(6),
         "tokens_in": 30000, "tokens_out": 11200},
    ]
    (board / "trajectories.jsonl").write_text("".join(json.dumps(e) + "\n" for e in events))
    rev = json.loads((board / "agents" / "rev.json").read_text())
    rev["limit"] = {"at": stamp(3), "until": "17:40", "note": "weekly cap"}
    write(board / "agents" / "rev.json", rev)
    # an EXPIRED limit: _active_seat_limit would clear it on disk outside read-only mode
    dave = json.loads((board / "agents" / "dave.json").read_text())
    dave["limit"] = {"at": stamp(120), "reset_at": stamp(60)}
    write(board / "agents" / "dave.json", dave)
    msg(board, id="m1", at=stamp(41), **{"from": OP}, to="planner", text="What blocks the cut?")
    msg(board, id="m2", at=stamp(35), **{"from": "planner"}, to=OP, text="T-4 waits on T-3; see T-99.")
    msg(board, id="m3", at=stamp(30), **{"from": "coder"}, to="planner", re="T-3", text="T-3 submitted")
    msg(board, id="m4", at=stamp(20), **{"from": "planner"}, text="DECIDE: rule A or B? ruling: A")
    msg(board, id="m5", at=stamp(90), **{"from": "scout"}, text="stuck: staging host unreachable")
    msg(board, id="m6", at=stamp(10), **{"from": "scout"}, text="stuck: just now")


def seed_demo(board):
    for s in (OP, "planner", "coder"):
        seat(board, s)
    write(board / "workforce.json", {"planner": {"harness": "codex"}, "coder": {"harness": "claude"}})
    write(board / "master.json", {"owner": "planner", "cos": "coder", "since": stamp(600)})
    write(board / "objective.json", {"text": "Demo objective", "exit_criterion": "demo ships", "state": "active"})
    ticket(board, "T-100", "Demo only ticket")


def _free_port():
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class Srv:
    """The real cmd_ui, in-process on a thread; stopped by killing its --parent-pid."""

    def __init__(self, tk, board, operator=OP, dev_origins=(), app_dir=""):
        self.port = _free_port()
        self.parent = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(600)"])
        a = argparse.Namespace(json=False, host="127.0.0.1", port=self.port, open=False,
                               parent_pid=self.parent.pid, operator=operator,
                               dev_origin=list(dev_origins), app_dir=app_dir)
        self.thread = threading.Thread(target=tk.cmd_ui, args=(a, str(board)), daemon=True)
        self.thread.start()
        deadline = time.time() + 20
        while time.time() < deadline:
            try:
                if self.req("GET", "/api/v1/projects")[0] == 200:
                    break
            except OSError:
                time.sleep(0.05)
        else:
            raise RuntimeError("atm ui never came up")
        page = self.req("GET", "/", raw=True)[1]
        self.token = re.search(r'const UI_TOKEN="([^"]*)"', page).group(1)

    def req(self, method, path, body=None, host=None, token=True, headers=None, raw=False):
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=60)
        head = {"Host": host if host is not None else "127.0.0.1:%d" % self.port}
        data = None
        if body is not None:
            data = json.dumps(body).encode()
            head["Content-Type"] = "application/json"
        if method == "POST" and token:
            head["X-Atman-Token"] = self.token if token is True else token
        head.update(headers or {})
        conn.request(method, path, body=data, headers=head)
        r = conn.getresponse()
        payload = r.read()
        self.last_headers = dict(r.getheaders())
        conn.close()
        if raw:
            return r.status, payload.decode()
        try:
            return r.status, json.loads(payload)
        except ValueError:
            return r.status, payload.decode()

    def get(self, path, **kw):
        return self.req("GET", path, **kw)

    def post(self, path, body, **kw):
        return self.req("POST", path, body=body, **kw)

    def stop(self):
        self.parent.kill()
        self.parent.wait()
        self.thread.join(5)


@pytest.fixture
def env(tmp_path, monkeypatch):
    for k in list(os.environ):
        if k.startswith(SESSION_PREFIXES):
            monkeypatch.delenv(k, raising=False)
    home = tmp_path / "home"
    home.mkdir()
    alpha = tmp_path / "alpha" / ".tickets"
    demo = tmp_path / "demo" / ".tickets"
    seed_alpha(alpha)
    seed_demo(demo)
    cfg = tmp_path / "board-config.json"
    write(cfg, {"projects": {"alpha": {"board": str(alpha), "repos": [str(alpha.parent)]},
                             "demo": {"board": str(demo), "repos": [str(demo.parent)]}}})
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("TICKETS_CACHE_DIR", str(tmp_path / "cache"))
    monkeypatch.setenv("ATMAN_BOARD_CONFIG", str(cfg))
    monkeypatch.setenv("TICKETS_DIR", str(alpha))
    monkeypatch.chdir(tmp_path)
    for k in ("TICKET_SEAT", "TICKET_AGENT", "TICKET_SESSION_ID"):
        assert k not in os.environ
    spec = importlib.util.spec_from_file_location("tickets_t1103_api", TOOL)
    tk = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(tk)
    servers = []

    def serve(board=alpha, **kw):
        s = Srv(tk, board, **kw)
        servers.append(s)
        return s

    box = type("Env", (), dict(tk=tk, alpha=alpha, demo=demo, cfg=cfg, home=home, tmp=tmp_path, serve=serve))
    yield box
    for s in servers:
        s.stop()


def cli(e, board, *args):
    env = {k: v for k, v in os.environ.items() if not k.startswith(SESSION_PREFIXES)}
    env.update(TICKETS_DIR=str(board), HOME=str(e.home), TICKETS_CACHE_DIR=str(e.tmp / "cache"),
               ATMAN_BOARD_CONFIG=str(e.cfg))
    return subprocess.run([sys.executable, str(TOOL), *args], capture_output=True, text=True,
                          env=env, cwd=str(board.parent))


class Audit:
    """No subprocess, no write-mode open, no remove/rename while armed (T-1072 shape)."""

    def __init__(self):
        self.seen, self.armed = [], False
        sys.addaudithook(self._hook)

    def _hook(self, event, args):
        if not self.armed:
            return
        if event in ("subprocess.Popen", "os.system", "os.posix_spawn", "os.exec", "os.fork",
                     "os.spawn", "pty.spawn"):
            self.seen.append((event, str(args)[:160]))
        elif event == "open" and len(args) > 2:
            mode, flags = args[1], args[2] or 0
            if (isinstance(mode, str) and any(c in mode for c in "wax+")) or \
                    flags & (os.O_WRONLY | os.O_RDWR | os.O_CREAT):
                self.seen.append((event, args[0]))
        elif event in ("os.remove", "os.rename", "os.unlink", "shutil.rmtree"):
            self.seen.append((event, str(args)[:160]))


_AUDIT = None


def audit():
    global _AUDIT
    if _AUDIT is None:
        _AUDIT = Audit()
    _AUDIT.seen = []
    return _AUDIT


def validator(name):
    js = pytest.importorskip("jsonschema")
    referencing = pytest.importorskip("referencing")
    resources = []
    for p in SCHEMAS.glob("*.json"):
        doc = json.loads(p.read_text())
        resources.append((doc["$id"], referencing.Resource.from_contents(doc)))
    registry = referencing.Registry().with_resources(resources)
    schema = json.loads((SCHEMAS / name).read_text())
    return js.Draft202012Validator(schema, registry=registry,
                                   format_checker=js.Draft202012Validator.FORMAT_CHECKER)


def valid(name, obj):
    errs = sorted(validator(name).iter_errors(obj), key=lambda e: list(e.path))
    assert not errs, "%s: %s" % (name, "; ".join("%s: %s" % (list(e.path), e.message) for e in errs[:5]))


# --- read routes: no subprocess, no writes -------------------------------------

def test_api_read_routes_no_subprocess_no_writes(env):
    srv = env.serve()
    routes = list(READ_ROUTES)
    first = srv.get("/api/v1/thread")[1]
    routes.append("/api/v1/thread?before=" + first["oldest_id"])
    before = tree_hash(env.alpha, env.demo, env.tmp / "cache", env.home)
    a = audit()
    a.armed = True
    try:
        statuses = [(r, srv.get(r)[0]) for r in routes]
        statuses.append(("session", srv.get("/api/v1/session", headers={"X-Atman-Client": "app"})[0]))
    finally:
        a.armed = False
    assert all(s == 200 for _r, s in statuses), statuses
    assert a.seen == []
    assert tree_hash(env.alpha, env.demo, env.tmp / "cache", env.home) == before
    # the expired limit was not cleared by a read
    assert "limit" in json.loads((env.alpha / "agents" / "dave.json").read_text())


# --- the contract ---------------------------------------------------------------

def test_schemas_are_valid_and_documented():
    js = pytest.importorskip("jsonschema")
    doc = DOC.read_text()
    names = sorted(p.name for p in SCHEMAS.glob("*.json"))
    assert names == sorted(["common.json", "error.json", "session.json", "projects.json", "board.json",
                            "plan.json", "thread.json", "lead.json", "needs-you.json", "ticket.json",
                            "lead-request.json", "lead-response.json"])
    for n in names:
        schema = json.loads((SCHEMAS / n).read_text())
        js.Draft202012Validator.check_schema(schema)
        assert schema["$id"].endswith("/" + n)
        assert n in doc, n
    for route in ("GET /api/v1/session", "GET /api/v1/projects", "GET /api/v1/board", "GET /api/v1/plan",
                  "GET /api/v1/thread", "GET /api/v1/ticket/{id}", "GET /api/v1/lead",
                  "GET /api/v1/needs-you", "POST /api/v1/lead", "OPTIONS /api/v1/*", "GET /app/"):
        assert route in doc, route


def test_every_route_validates_against_its_schema(env):
    srv = env.serve()
    for path, schema in READ_ROUTES.items():
        st, out = srv.get(path)
        assert st == 200, (path, out)
        valid(schema, out)
    valid("session.json", srv.get("/api/v1/session", headers={"X-Atman-Client": "app"})[1])
    body = {"seat": "coder", "project": "demo"}
    valid("lead-request.json", body)
    st, out = srv.post("/api/v1/lead", body)
    assert st == 200, out
    valid("lead-response.json", out)
    valid("lead.json", srv.get("/api/v1/lead?project=demo")[1])  # now with a status, not a picker
    for path in ("/api/v1/ticket/T-404", "/api/v1/ticket/nope", "/api/v1/board?project=nope",
                 "/api/v1/thread?with=ghost", "/api/v1/thread?before=nope", "/api/v1/nope",
                 "/api/v1/session"):
        st, out = srv.get(path)
        assert st >= 400, path
        valid("error.json", out)
    st, out = srv.post("/api/v1/lead", {"seat": "nobody"})
    assert st == 400
    valid("error.json", out)


def test_empty_board_validates_too(env, tmp_path):
    bare = tmp_path / "bare" / ".tickets"
    (bare / "agents").mkdir(parents=True)
    env.cfg.write_text(json.dumps({"projects": {}}))
    srv = env.serve(board=bare, operator="")
    for path, schema in (("/api/v1/projects", "projects.json"), ("/api/v1/board", "board.json"),
                         ("/api/v1/plan", "plan.json"), ("/api/v1/lead", "lead.json"),
                         ("/api/v1/thread", "thread.json"), ("/api/v1/needs-you", "needs-you.json")):
        st, out = srv.get(path)
        assert st == 200, (path, out)
        valid(schema, out)


# --- projects -------------------------------------------------------------------

def test_project_switch_reads_the_registered_board(env):
    srv = env.serve()
    projects = srv.get("/api/v1/projects")[1]["projects"]
    assert [p["slug"] for p in projects][:2] == ["alpha", "demo"]
    assert projects[0]["current"] is True and projects[0]["lead"] == "planner"
    assert projects[1]["lead"] == ""
    a = srv.get("/api/v1/board?project=alpha")[1]
    d = srv.get("/api/v1/board?project=demo")[1]
    assert a["objective"]["text"] == "Alpha objective" and d["objective"]["text"] == "Demo objective"
    assert {n["id"] for n in d["work"]["nodes"]} == {"T-100"}
    assert a["app"]["lead"] == "planner" and d["app"]["lead"] == "" and d["app"]["project"] == "demo"
    assert {n["id"] for n in srv.get("/api/v1/plan?project=demo")[1]["nodes"]} == {"T-100"}
    assert srv.get("/api/v1/ticket/T-100")[0] == 404  # T-100 is demo's, not alpha's
    alpha_master = (env.alpha / "master.json").read_text()
    assert srv.post("/api/v1/lead", {"seat": "coder", "project": "demo"})[0] == 200
    assert (env.alpha / "master.json").read_text() == alpha_master
    assert json.loads((env.demo / "master.json").read_text())["lead"] == "coder"
    assert srv.get("/api/v1/board?project=nope")[0] == 404


def test_registry_missing_or_bad_lists_only_the_started_board(env):
    for content in ("{not json", "[]", json.dumps({"projects": {"ghost": {"board": "/nonexistent/.tickets"}},
                                                     "boards": {"/x": 7}})):
        env.cfg.write_text(content)
        srv = env.serve()
        st, out = srv.get("/api/v1/projects")
        assert st == 200
        assert [p["board"] for p in out["projects"]] == [os.path.realpath(env.alpha)]
        assert out["projects"][0]["slug"] == "alpha"  # basename(dirname(board))
        assert srv.get("/api/v1/board")[0] == 200
    env.cfg.unlink()
    srv = env.serve()
    assert len(srv.get("/api/v1/projects")[1]["projects"]) == 1


# --- the lead -------------------------------------------------------------------

def test_no_lead_means_picker_not_guess(env):
    srv = env.serve()
    out = srv.get("/api/v1/lead?project=demo")[1]
    assert out["needs_lead"] is True and out["lead"] == "" and out["status"] is None
    picks = {p["seat"]: p for p in out["picker"]}
    assert set(picks) == {"planner", "coder"}  # master and CoS are offered, never chosen; never the operator
    thread = srv.get("/api/v1/thread?project=demo")[1]
    assert thread["needs_lead"] is True and thread["messages"] == []
    m = json.loads((env.demo / "master.json").read_text())
    m["lead"] = "gone"
    write(env.demo / "master.json", m)
    out = srv.get("/api/v1/lead?project=demo")[1]
    assert out["needs_lead"] is True and "gone" in out["lead_note"]


def test_lead_capability_line_matches_steer_table(env):
    srv = env.serve()
    tk = env.tk
    sock_dir = Path(env.tmp) / "s"
    sock_dir.mkdir()
    sock = sock_dir / "s"
    sock.touch()
    write(Path(tk._session_adapters().endpoint_path(str(env.demo), "coder")),
          {"seat": "coder", "provider": "claude", "mode": "native", "socket": str(sock), "pid": os.getpid()})
    caps = {p["seat"]: p["capability"] for p in srv.get("/api/v1/lead?project=demo")[1]["picker"]}
    assert caps == {"coder": "takes mid-run messages", "planner": "answers on its next turn"}
    steer = tk._steer()
    assert steer.harness_refuse_reason("codex") and not steer.harness_refuse_reason("claude")
    sock.unlink()
    assert tk.ui_capability(str(env.demo), "coder", "claude")["line"] == "answers on its next turn"
    status = srv.get("/api/v1/lead")[1]["status"]
    assert status["capability"]["line"] == "answers on its next turn"  # planner is codex


def test_post_lead_is_operator_only_and_recorded(env):
    srv = env.serve()
    assert srv.post("/api/v1/lead", {"seat": "nobody", "project": "demo"})[0] == 400
    assert srv.post("/api/v1/lead", {"seat": OP, "project": "demo"})[0] == 400
    assert srv.post("/api/v1/lead", {"seat": "coder", "extra": 1, "project": "demo"})[0] == 400
    st, out = srv.post("/api/v1/lead", {"seat": "coder", "project": "demo"})
    assert st == 200 and out["lead"] == "coder" and out["harness"] == "claude", out
    m = json.loads((env.demo / "master.json").read_text())
    assert m == dict(m, owner="planner", cos="coder", lead="coder")
    assert "lead set to coder" in (env.demo / "MASTER.md").read_text()
    assert srv.get("/api/v1/lead?project=demo")[1]["lead"] == "coder"
    # a master change keeps the user's lead choice
    r = cli(env, env.demo, "master", "take", "--owner", "planner")
    assert r.returncode == 0, r.stderr
    assert json.loads((env.demo / "master.json").read_text())["lead"] == "coder"
    # the lead is persistent + continuous like master/CoS unless workforce says otherwise
    assert env.tk.wake_mode_of(str(env.demo), "coder") == "continuous"
    assert env.tk.lifecycle_of(str(env.demo), "coder") == "persistent"
    # no operator -> nobody may pick, even with the token
    srv2 = env.serve(operator="")
    st, out = srv2.post("/api/v1/lead", {"seat": "planner"})
    assert st == 400 and "operator" in out["error"]
    # a harness-run seat is never the operator
    srv3 = env.serve(operator="coder")
    st, out = srv3.post("/api/v1/lead", {"seat": "planner"})
    assert st == 400 and "harness-run seat" in out["error"]


def test_lead_cli_set_show_clear(env):
    r = cli(env, env.demo, "lead")
    assert "(none)" in r.stdout and "atm lead set" in r.stdout
    r = cli(env, env.demo, "lead", "set", "planner", "--owner", OP)
    assert r.returncode == 0 and "answers on its next turn" in r.stdout, r.stdout + r.stderr
    assert json.loads((env.demo / "master.json").read_text())["lead"] == "planner"
    assert cli(env, env.demo, "lead", "set", "nobody", "--owner", OP).returncode != 0
    r = cli(env, env.demo, "lead", "clear", "--owner", OP)
    assert r.returncode == 0 and "lead" not in json.loads((env.demo / "master.json").read_text())


def test_lead_status_names_limited_logged_out_offline_and_quota(env):
    tk = env.tk
    board = str(env.alpha)
    st = tk.ui_lead_status(board, "planner")
    assert st["cannot_answer"]["kind"] == "offline"
    assert "atm spawn planner --persist" in st["cannot_answer"]["text"]
    assert st["usage"]["age"] == "age unknown" and st["usage"]["remaining_pct"] is None
    write(env.alpha / "provider_usage.json", {"providers": {"codex": {
        "provider": "codex", "status": "limited", "reset_at": "", "checked_at": stamp(12), "source": "cli"}}})
    st = tk.ui_lead_status(board, "planner")
    assert st["cannot_answer"]["kind"] == "quota" and "12m ago" in st["cannot_answer"]["text"]
    rec = json.loads((env.alpha / "agents" / "planner.json").read_text())
    rec["limit"] = {"at": stamp(2), "until": "17:40", "note": "cap"}
    write(env.alpha / "agents" / "planner.json", rec)
    st = tk.ui_lead_status(board, "planner")
    assert st["cannot_answer"]["kind"] == "limited" and "limited until 17:40" in st["cannot_answer"]["text"]
    rec["auth_check"] = {"state": "login_required", "login_cmd": "codex login", "harness": "codex"}
    write(env.alpha / "agents" / "planner.json", rec)
    st = tk.ui_lead_status(board, "planner")
    assert st["cannot_answer"]["kind"] == "logged_out" and "logged out" in st["cannot_answer"]["text"]
    srv = env.serve()
    out = srv.get("/api/v1/lead")[1]
    valid("lead.json", out)
    assert out["status"]["cannot_answer"]["kind"] == "logged_out"


# --- security: the API sits behind T-1105's gate, plus an origin allowlist -------

def test_api_host_header_must_be_loopback(env):
    srv = env.serve()
    evil = "evil.test:%d" % srv.port
    for path in ("/api/v1/projects", "/api/v1/session", "/api/v1/ticket/T-3", "/app/"):
        assert srv.get(path, host=evil, headers={"X-Atman-Client": "app"})[0] in (400, 403), path
    assert srv.get("/api/v1/plan", host="localhost.evil.test:%d" % srv.port)[0] in (400, 403)
    assert srv.get("/api/v1/plan", host="")[0] in (400, 403)
    assert srv.get("/api/v1/plan", host="localhost:%d" % srv.port)[0] == 200
    before = (env.demo / "master.json").read_text()
    st, out = srv.post("/api/v1/lead", {"seat": "coder", "project": "demo"}, host=evil,
                       headers={"Origin": "http://" + evil})
    assert st in (400, 403) and (env.demo / "master.json").read_text() == before
    st, out = srv.req("OPTIONS", "/api/v1/lead", host=evil, headers={"Origin": "http://" + evil})
    assert st in (400, 403)
    r = cli(env, env.alpha, "ui", "--host", "0.0.0.0", "--port", "0")
    assert r.returncode != 0 and "loopback" in r.stderr


def test_api_write_requires_the_launch_token(env):
    srv = env.serve()
    before = tree_hash(env.alpha, env.demo)
    for token in (False, "wrong-token", srv.token + "x", "x" * len(srv.token)):
        st, out = srv.post("/api/v1/lead", {"seat": "coder", "project": "demo"}, token=token)
        assert st in (400, 403) and "token" in out["error"], (token, out)
    other = env.serve()
    assert other.token != srv.token
    assert srv.post("/api/v1/lead", {"seat": "coder", "project": "demo"}, token=other.token)[0] in (400, 403)
    assert tree_hash(env.alpha, env.demo) == before
    # GET needs no token; the only API write is the lead; the app never posts as a seat here
    assert srv.post("/api/v1/msg", {"text": "hi", "from": "coder"})[0] == 404
    assert srv.post("/api/v1/ticket/T-3", {"status": "done"})[0] == 404
    assert tree_hash(env.alpha, env.demo) == before


def test_cors_allows_only_the_apps_own_origins(env):
    srv = env.serve(dev_origins=[DEV])
    own = "http://127.0.0.1:%d" % srv.port
    # preflight: allowed for the dev origin, refused (no CORS headers) otherwise
    st, _ = srv.req("OPTIONS", "/api/v1/lead", headers={
        "Origin": DEV, "Access-Control-Request-Method": "POST",
        "Access-Control-Request-Headers": "content-type,x-atman-token"})
    assert st == 204
    h = srv.last_headers
    assert h["Access-Control-Allow-Origin"] == DEV and "X-Atman-Token" in h["Access-Control-Allow-Headers"]
    for bad in ("http://evil.test", "http://localhost:9999", "https://localhost:5173", "null",
                "http://127.0.0.1.evil.test:5173"):
        st, _ = srv.req("OPTIONS", "/api/v1/lead", headers={"Origin": bad, "Access-Control-Request-Method": "POST"})
        assert st == 403 and "Access-Control-Allow-Origin" not in srv.last_headers, bad
        st, _ = srv.get("/api/v1/projects", headers={"Origin": bad})
        assert st == 403 and "Access-Control-Allow-Origin" not in srv.last_headers, bad
    # reads: the allowed origins get ACAO; same-origin (no Origin) works without it
    for good in (DEV, own):
        assert srv.get("/api/v1/projects", headers={"Origin": good})[0] == 200
        assert srv.last_headers["Access-Control-Allow-Origin"] == good
    assert srv.get("/api/v1/projects")[0] == 200
    assert "Access-Control-Allow-Origin" not in srv.last_headers
    # a no-cors cross-site embed (no Origin) is refused by Sec-Fetch-Site
    assert srv.get("/api/v1/projects", headers={"Sec-Fetch-Site": "cross-site"})[0] == 403
    assert srv.get("/api/v1/projects", headers={"Sec-Fetch-Site": "same-origin"})[0] == 200
    # a cross-origin write with a valid token is still refused, and writes nothing
    before = (env.demo / "master.json").read_text()
    st, out = srv.post("/api/v1/lead", {"seat": "coder", "project": "demo"},
                       headers={"Origin": "http://evil.test"})
    assert st == 403 and (env.demo / "master.json").read_text() == before
    st, out = srv.post("/api/v1/lead", {"seat": "coder", "project": "demo"}, headers={"Origin": DEV})
    assert st == 200 and srv.last_headers["Access-Control-Allow-Origin"] == DEV
    # CORS is for /api/v1 only: never the embedded page, /app/, or a refused preflight
    for method, path in (("GET", "/board.json"), ("GET", "/app/"), ("GET", "/app/x.js"),
                         ("OPTIONS", "/board.json"), ("OPTIONS", "/app/"), ("OPTIONS", "/msg")):
        srv.req(method, path, headers={"Origin": DEV, "Access-Control-Request-Method": "POST"})
        assert "Access-Control-Allow-Origin" not in srv.last_headers, (method, path)
    # without --dev-origin the dev server is just another origin
    plain = env.serve()
    assert plain.get("/api/v1/projects", headers={"Origin": DEV})[0] == 403
    # a --dev-origin must itself be loopback
    for bad in ("http://example.com:5173", "http://192.168.1.4:5173", "file:///x", "http://localhost:5173/app"):
        with pytest.raises(ValueError):
            env.tk.UiApi(str(env.alpha), "t", dev_origins=[bad])
    r = cli(env, env.alpha, "ui", "--port", "0", "--dev-origin", "http://example.com:5173")
    assert r.returncode != 0 and "loopback" in r.stderr


def test_session_token_needs_the_client_header_and_never_rides_a_url(env):
    srv = env.serve(dev_origins=[DEV])
    st, out = srv.get("/api/v1/session")
    assert st == 400 and "token" not in out
    st, out = srv.get("/api/v1/session", headers={"X-Atman-Client": "app", "Origin": DEV})
    assert st == 200 and out["token"] == srv.token and out["token_header"] == "X-Atman-Token"
    assert srv.last_headers["Cache-Control"] == "no-store"
    st, out = srv.get("/api/v1/session", headers={"X-Atman-Client": "app", "Origin": "http://evil.test"})
    assert st == 403 and "token" not in out
    # the token from the session route works for the one write
    assert srv.post("/api/v1/lead", {"seat": "coder", "project": "demo"}, token=out.get("token") or srv.token)[0] == 200
    # no route takes the token from the query string
    st, _ = srv.req("POST", "/api/v1/lead?token=" + srv.token, body={"seat": "planner"}, token=False)
    assert st in (400, 403)


def test_served_bundle_carries_the_token_in_a_meta_tag(env, tmp_path):
    dist = tmp_path / "dist"
    (dist / "assets").mkdir(parents=True)
    (dist / "index.html").write_text("<!doctype html><html><head><title>app</title></head><body></body></html>")
    (dist / "assets" / "app.js").write_text("console.log(1)")
    (tmp_path / "secret.txt").write_text("outside the bundle")
    srv = env.serve(app_dir=str(dist))
    st, page = srv.get("/app/", raw=True)
    assert st == 200 and '<meta name="atman-token" content="%s">' % srv.token in page
    assert '<meta name="atman-api" content="/api/v1">' in page
    assert srv.last_headers["X-Frame-Options"] == "DENY"
    assert srv.get("/app/plan/T-3", raw=True)[1] == page  # client-side route -> the shell
    st, js = srv.get("/app/assets/app.js", raw=True)
    assert st == 200 and js == "console.log(1)" and srv.last_headers["Content-Type"].startswith("text/javascript")
    for path in ("/app/../secret.txt", "/app/%2e%2e/secret.txt", "/app/assets/../../secret.txt", "/app/nope.js"):
        st, body = srv.get(path, raw=True)
        assert st == 404 and "outside the bundle" not in body, path
    none = env.serve(app_dir=str(tmp_path / "missing"))
    st, out = none.get("/app/")
    assert st == 404 and "npm run build" in out["error"]


# --- honesty --------------------------------------------------------------------

def test_harness_is_never_defaulted_to_claude_in_api_fields(env):
    msg(env.alpha, id="m7", at=stamp(2), **{"from": "scout"}, to=OP, text="no harness on file")
    msg(env.alpha, id="m8", at=stamp(1), **{"from": "planner"}, to=OP, text="stamped", harness="codex")
    ticket(env.alpha, "T-9", "Scout run", status="claimed", owner="scout")
    with open(env.alpha / "trajectories.jsonl", "a") as f:
        f.write(json.dumps({"v": 1, "at": stamp(3), "kind": "run_start", "agent": "scout", "ticket": "T-9",
                            "run_no": 1, "run_id": "r-scout-1", "trigger": ["holding"]}) + "\n")
    srv = env.serve()
    posts = {m["id"]: m for m in srv.get("/api/v1/thread?with=scout")[1]["messages"]}
    assert posts["m7"]["harness"] == {"value": "unknown", "recorded": False, "note": "harness not recorded"}
    posts = {m["id"]: m for m in srv.get("/api/v1/thread")[1]["messages"]}
    assert posts["m2"]["harness"]["value"] == "codex" and posts["m2"]["harness"]["recorded"] is False
    assert posts["m8"]["harness"]["recorded"] is True
    assert posts["m1"]["harness"]["value"] == "operator"
    runs = srv.get("/api/v1/ticket/T-9")[1]["runs"]
    assert runs and all(r["harness"] == "unknown" for r in runs), runs
    picks = {p["seat"]: p["harness"] for p in srv.get("/api/v1/lead?project=demo")[1]["picker"]}
    assert picks == {"planner": "codex", "coder": "claude"}
    m = json.loads((env.alpha / "master.json").read_text())
    m["lead"] = "scout"
    write(env.alpha / "master.json", m)
    assert srv.get("/api/v1/lead")[1]["status"]["harness"] == "unknown"


def test_thread_pages_past_the_snapshot_window(env):
    board = env.alpha
    (board / "messages.jsonl").unlink()
    thread = 0
    for i in range(500):
        if i % 2 == 0:
            msg(board, id="p%03d" % i, at=stamp(600 - i), **{"from": OP if i % 4 == 0 else "planner"},
                to="planner" if i % 4 == 0 else OP, text="thread %d" % i)
            thread += 1
        else:
            msg(board, id="p%03d" % i, at=stamp(600 - i), **{"from": "coder"}, to="rev", text="noise %d" % i)
    srv = env.serve()
    assert len(srv.get("/api/v1/board")[1]["messages"]) == 40
    page = srv.get("/api/v1/thread")[1]
    assert len(page["messages"]) == 100 and page["has_more"] is True and page["with"] == "planner"
    got = [m["id"] for m in page["messages"]]
    while page["has_more"]:
        page = srv.get("/api/v1/thread?before=" + page["oldest_id"])[1]
        valid("thread.json", page)
        got = [m["id"] for m in page["messages"]] + got
    assert len(got) == thread == len(set(got))
    assert got[0] == "p000" and all(m.startswith("p") for m in got)
    st, out = srv.get("/api/v1/thread?before=nope")
    assert st == 400 and out["error"].startswith("unknown cursor")
    assert len(srv.get("/api/v1/thread?limit=5000")[1]["messages"]) == 200
    assert len(srv.get("/api/v1/thread?limit=junk")[1]["messages"]) == 100


def test_plan_blockers_distinguish_unaccepted_dep(env):
    srv = env.serve()
    nodes = {n["id"]: n for n in srv.get("/api/v1/plan")[1]["nodes"]}
    kinds = lambda tid: {b["kind"]: b for b in nodes[tid]["blockers"]}
    assert "dep_unaccepted" in kinds("T-5") and "dep_open" not in kinds("T-5")
    assert kinds("T-5")["dep_unaccepted"]["text"] == "dep T-2 done, not accepted"
    assert "dep_open" in kinds("T-4") and kinds("T-4")["dep_open"]["text"] == "dep T-3 still in flight"
    assert kinds("T-4")["seat_limited"]["text"] == "seat rev limited until 17:40"
    assert not [k for k in kinds("T-3") if k.startswith("dep_")]  # accepted dep T-1: no dep chip
    assert "seat_offline" in kinds("T-6")
    rec = json.loads((env.alpha / "agents" / "rev.json").read_text())
    rec["auth_check"] = {"state": "login_required", "harness": "codex"}
    write(env.alpha / "agents" / "rev.json", rec)
    nodes = {n["id"]: n for n in srv.get("/api/v1/plan")[1]["nodes"]}
    assert any(b["kind"] == "auth" for b in nodes["T-4"]["blockers"])
    # the chip agrees with the gate the CLI enforces
    wv = env.tk._work_view()
    tickets = env.tk.load_all(str(env.alpha))
    by = {t["id"]: t for t in tickets}
    assert wv.unreleased_dep_id(by["T-5"], tickets) == "T-2"
    assert wv.unreleased_dep_id(by["T-3"], tickets) == ""


def test_ticket_tokens_unknown_not_zero_and_usage_has_age(env):
    srv = env.serve()
    st, t = srv.get("/api/v1/ticket/T-3")
    assert st == 200
    runs = {r["seat"]: r for r in t["runs"]}
    assert runs["coder"]["tokens"] is None and runs["coder"]["tokens_label"] == "unknown"
    assert runs["rev"]["tokens"] == 41200 and runs["rev"]["tokens_label"] == "41,200"
    assert runs["rev"]["verdict"].startswith("REJECT") and runs["coder"]["author"] == "coder@alpha"
    usage = {u["provider"]: u for u in t["usage"]}
    assert usage["codex"]["age"] == "age unknown" and usage["codex"]["remaining_pct"] is None
    assert t["review"]["head"] == HEAD and t["review"]["head_len"] == 40
    assert t["review"]["verdicts"][0]["kind"] == "reject" and t["review"]["verdicts"][0]["applies"] is True
    assert t["diff_cmd"] == "git diff main...%s" % HEAD and "never runs git" in t["diff_note"]
    assert t["artifact"]["branch"] == "t3-branch" and t["artifact"]["pr"].endswith("/pr/7")
    assert [m["id"] for m in t["messages"]] == ["m3"]
    assert t["steers"][0]["text"] == "390 covered?"
    assert t["handoff"][0]["from"] == "T-1"
    assert srv.get("/api/v1/ticket/T-404")[0] == 404
    assert srv.get("/api/v1/ticket/..%2Fmaster.json")[0] in (400, 404)
    assert srv.get("/api/v1/ticket/master")[0] == 400
    write(env.alpha / "provider_usage.json", {"providers": {"codex": {
        "provider": "codex", "status": "ok", "remaining": 62, "windows": [{"name": "5h", "used_percent": 38}],
        "checked_at": stamp(3), "source": "cli"}}})
    usage = {u["provider"]: u for u in srv.get("/api/v1/ticket/T-3")[1]["usage"]}
    assert "3m ago" in usage["codex"]["age"] and usage["codex"]["checked_at"]


def test_done_without_accept_is_never_accepted(env):
    srv = env.serve()
    plan = srv.get("/api/v1/plan")[1]
    nodes = {n["id"]: n for n in plan["nodes"]}
    assert nodes["T-2"]["unverified"] is True and nodes["T-2"]["accepted"] is False
    assert nodes["T-2"]["status_label"] == "done, not accepted"
    assert [b["kind"] for b in nodes["T-2"]["blockers"]] == ["unaccepted"]
    assert nodes["T-8"]["accepted"] is False and nodes["T-8"]["released"] is True
    assert "not accepted" in nodes["T-8"]["status_label"]
    if "T-1" in nodes:
        assert nodes["T-1"]["accepted"] is True and nodes["T-1"]["status_label"] == "done, accepted"
    t2 = srv.get("/api/v1/ticket/T-2")[1]
    assert t2["status_label"] == "done, not accepted" and t2["accepted"] is False
    t8 = srv.get("/api/v1/ticket/T-8")[1]
    assert t8["accepted"] is False and t8["released"] is True and "not accepted" in t8["status_label"]
    t1 = srv.get("/api/v1/ticket/T-1")[1]
    assert t1["status_label"] == "done, accepted" and t1["accepted"] is True
    assert {d["id"]: d["state"] for d in srv.get("/api/v1/ticket/T-5")[1]["deps"]} == {"T-2": "done, not accepted"}
    # prose is not an accept
    msg(env.alpha, id="m9", **{"from": "rev"}, re="T-2", text="ACCEPT, looks good")
    assert srv.get("/api/v1/ticket/T-2")[1]["accepted"] is False
    assert {n["id"]: n for n in srv.get("/api/v1/plan")[1]["nodes"]}["T-2"]["accepted"] is False


def test_receipts_never_say_ack(env):
    tk = env.tk
    rec = json.loads((env.alpha / "agents" / "planner.json").read_text())
    rec["wake_delivery"] = {"label": "woken", "msg_id": "m1", "at": stamp(1)}
    rec["inbox_seen"] = stamp(0)
    write(env.alpha / "agents" / "planner.json", rec)
    srv = env.serve()
    words = []
    for m in srv.get("/api/v1/thread")[1]["messages"] + srv.get("/api/v1/ticket/T-3")[1]["messages"]:
        for r in m["receipts"]:
            words += r["words"]
    assert words and "posted" in words and "inbox read" in words
    assert not [w for w in words if re.search(r"ack|acknowledg|understood|on it", w, re.I)]
    assert tk._ui_receipt_words(True, {"label": "acked-by-agent"}) == ["posted", "inbox read", "wake: receipt recorded"]


def test_needs_you_is_asked_only(env):
    srv = env.serve()
    out = srv.get("/api/v1/needs-you")[1]
    items = {i["id"]: i for i in out["items"]}
    assert items["m4"]["state"] == "asked" and items["m4"]["label"] == "asked (unstructured)"
    assert "m5" in items and "m6" not in items  # stuck > 1h only
    assert "m2" in items  # addressed to the operator, no reply since
    assert "ruled" not in json.dumps(out).lower().replace("ruling", "")
    # the operator answers the lead (atm msg, not the API): that ask leaves the queue
    msg(env.alpha, id="m10", at=stamp(0), **{"from": OP}, to="planner", text="thanks, noted")
    assert "m2" not in {i["id"] for i in srv.get("/api/v1/needs-you")[1]["items"]}
    ticket(env.alpha, "T-7", "Auto step", kind="automated",
           automated={"escalated": True, "escalate_reason": "judgment needed", "escalated_at": stamp(4)})
    out = srv.get("/api/v1/needs-you")[1]
    assert "T-7" in {i["id"] for i in out["items"]}
    valid("needs-you.json", out)


# --- the embedded page is unchanged ---------------------------------------------

def test_embedded_page_and_board_json_still_serve(env):
    srv = env.serve()
    st, page = srv.get("/", raw=True)
    assert st == 200 and 'id="composer"' in page and "const UI_TOKEN=" in page
    st, snap = srv.get("/board.json")
    assert st == 200 and "app" not in snap
    for key in ("project", "generated", "counts", "agents", "messages", "work", "agent_map"):
        assert key in snap


def test_docs_and_schemas_name_no_external_products():
    text = DOC.read_text() + "".join(p.read_text() for p in SCHEMAS.glob("*.json"))
    for name in ("Slack", "Discord", "Microsoft Teams", "Jira", "Notion", "Asana", "Trello", "Linear",
                 "ChatGPT", "Copilot", "Vite", "Webpack", "retc"):
        assert name not in text, name
