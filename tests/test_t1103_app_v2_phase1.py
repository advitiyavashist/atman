"""T-1103 app v2 phase 1: lead chat beside a live plan.

Fixture boards only (ATMAN_BOARD_CONFIG, TICKETS_DIR, HOME and
TICKETS_CACHE_DIR all under tmp_path); never the real board. The server runs
in-process (make_ui_handler on port 0) so read routes can be audited with an
audit hook, and no outer harness session variable is visible: a wake from a
test board can only reach the fake session socket this file creates.
"""
from __future__ import annotations

import hashlib
import http.client
import importlib.util
import json
import os
import re
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import time
from http.server import ThreadingHTTPServer
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
TOOL = ROOT / "tickets.py"
OP = "ada"
HEAD = "9c1e5a0b7d3f2e1a4b6c8d0e2f4a6b8c0d1e3f50"
ACC = "24fb8c0aa1b2c3d4e5f60718293a4b5c6d7e8f90"
SESSION_PREFIXES = ("CLAUDE", "CODEX_", "CURSOR_", "TICKET_", "TERM_SESSION", "ATMAN_")
# Import machinery bytecode is not a board write. First GET can otherwise
# fail this file's audit on a cold checkout (ticket_board.turns pyc).
sys.dont_write_bytecode = True


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


def tree_hash(*roots):
    h = {}
    for root in roots:
        for p in sorted(Path(root).rglob("*")):
            if p.is_file():
                h[str(p)] = hashlib.sha256(p.read_bytes()).hexdigest()
    return h


def seed_alpha(board):
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
                             "demo": {"board": str(demo), "repos": [str(demo.parent)]}},
                "operator": OP})
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("TICKETS_CACHE_DIR", str(tmp_path / "cache"))
    monkeypatch.setenv("ATMAN_BOARD_CONFIG", str(cfg))
    monkeypatch.setenv("TICKETS_DIR", str(alpha))
    monkeypatch.chdir(tmp_path)
    spec = importlib.util.spec_from_file_location("tickets_t1103", TOOL)
    tk = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(tk)
    servers = []

    def serve(board=alpha, operator=OP):
        s = Srv(tk, board, operator)
        servers.append(s)
        return s

    box = type("Env", (), dict(tk=tk, alpha=alpha, demo=demo, cfg=cfg, home=home, tmp=tmp_path, serve=serve))
    yield box
    for s in servers:
        s.stop()


class Srv:
    def __init__(self, tk, board, operator):
        self.ctx = tk.UiContext(str(board), operator=operator)
        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), tk.make_ui_handler(self.ctx))
        self.port = self.ctx.port = self.httpd.server_address[1]
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        self.thread.start()

    def req(self, method, path, body=None, host=None, token=True, headers=None, raw=False):
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=60)
        head = {"Host": host if host is not None else "127.0.0.1:%d" % self.port}
        data = None
        if body is not None:
            data = json.dumps(body).encode()
            head["Content-Type"] = "application/json"
        if method == "POST" and token:
            head["X-Atman-Token"] = self.ctx.token if token is True else token
        head.update(headers or {})
        conn.request(method, path, body=data, headers=head)
        r = conn.getresponse()
        payload = r.read()
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
        self.httpd.shutdown()
        self.httpd.server_close()


def cli(e, board, *args, agent=""):
    env = {k: v for k, v in os.environ.items() if not k.startswith(SESSION_PREFIXES)}
    env.update(TICKETS_DIR=str(board), HOME=str(e.home), TICKETS_CACHE_DIR=str(e.tmp / "cache"),
               ATMAN_BOARD_CONFIG=str(e.cfg), TICKET_AGENT=agent,
               TICKET_SESSION_ID="t1103-" + (agent or "anon"))
    return subprocess.run([sys.executable, str(TOOL), *args], capture_output=True, text=True,
                          env=env, cwd=str(board.parent))


def lines(board):
    p = board / "messages.jsonl"
    return [json.loads(x) for x in p.read_text().splitlines() if x.strip()] if p.exists() else []


class Audit:
    """No subprocess, no write-mode open while armed (same shape as T-1072)."""

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
            path = str(args[0])
            if "__pycache__" in path.replace("\\", "/") or path.endswith((".pyc", ".pyo")):
                return
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


# --- read routes --------------------------------------------------------------

def test_app_v2_read_routes_no_subprocess_no_writes(env):
    srv = env.serve()
    routes = ["/", "/?project=demo", "/board.json", "/board.json?project=alpha", "/board.json?project=demo",
              "/board.json?seat=planner", "/projects.json", "/thread.json", "/thread.json?project=alpha&with=coder",
              "/thread.json?project=demo", "/needs-you.json", "/needs-you.json?project=demo",
              "/ticket/T-3.json", "/ticket/T-2.json?all=1", "/ticket/T-100.json?project=demo"]
    first = srv.get("/thread.json")[1]
    routes.append("/thread.json?before=" + first["oldest_id"])
    before = tree_hash(env.alpha, env.demo, env.tmp / "cache") if (env.tmp / "cache").exists() \
        else tree_hash(env.alpha, env.demo)
    a = audit()
    a.armed = True
    try:
        statuses = [(r, srv.get(r, raw=True)[0]) for r in routes]
    finally:
        a.armed = False
    assert all(s == 200 for _r, s in statuses), statuses
    assert a.seen == []
    after = tree_hash(env.alpha, env.demo, env.tmp / "cache") if (env.tmp / "cache").exists() \
        else tree_hash(env.alpha, env.demo)
    assert after == before
    # the expired limit was not cleared by a read
    assert "limit" in json.loads((env.alpha / "agents" / "dave.json").read_text())


def test_project_switch_reads_the_registered_board(env):
    srv = env.serve()
    projects = srv.get("/projects.json")[1]["projects"]
    slugs = [p["slug"] for p in projects]
    assert slugs[:2] == ["alpha", "demo"]
    assert projects[0]["current"] is True and projects[0]["lead"] == "planner"
    assert projects[1]["lead"] == ""
    a = srv.get("/board.json?project=alpha")[1]
    d = srv.get("/board.json?project=demo")[1]
    assert a["objective"]["text"] == "Alpha objective" and d["objective"]["text"] == "Demo objective"
    assert {n["id"] for n in d["work"]["nodes"]} == {"T-100"}
    assert "T-100" not in {n["id"] for n in a["work"]["nodes"]}
    assert {x["name"] for x in d["agents"]} != {x["name"] for x in a["agents"]}
    assert a["app"]["lead"] == "planner" and d["app"]["lead"] == ""
    assert d["app"]["project"] == "demo"
    alpha_before = (env.alpha / "messages.jsonl").read_text()
    st, out = srv.post("/msg", {"text": "hello demo", "to": "coder", "project": "demo"})
    assert st == 200 and out["ok"], out
    assert (env.alpha / "messages.jsonl").read_text() == alpha_before
    assert lines(env.demo)[-1]["text"] == "hello demo"
    assert srv.get("/board.json?project=nope")[0] == 404


def test_registry_missing_or_bad_lists_only_the_started_board(env):
    for content in ("{not json", "[]", json.dumps({"projects": {"ghost": {"board": "/nonexistent/.tickets"}},
                                                     "boards": {"/x": 7}})):
        env.cfg.write_text(content)
        srv = env.serve()
        st, out = srv.get("/projects.json")
        assert st == 200
        assert [p["board"] for p in out["projects"]] == [os.path.realpath(env.alpha)]
        assert out["projects"][0]["slug"] == "alpha"  # basename(dirname(board))
        assert srv.get("/board.json")[0] == 200
    env.cfg.unlink()
    srv = env.serve()
    assert len(srv.get("/projects.json")[1]["projects"]) == 1


def test_no_lead_means_picker_not_guess(env):
    srv = env.serve()
    st, out = srv.get("/thread.json?project=demo")
    assert st == 200
    assert out["needs_lead"] is True and out["lead"] == "" and out["messages"] == []
    assert out.get("status") is None
    # master (planner) and CoS (coder) exist, and neither is used as a fallback
    picks = {p["seat"]: p for p in out["picker"]}
    assert set(picks) == {"planner", "coder"}  # never the operator
    # a lead that is no longer registered is reported, not replaced
    m = json.loads((env.demo / "master.json").read_text())
    m["lead"] = "gone"
    write(env.demo / "master.json", m)
    out = srv.get("/thread.json?project=demo")[1]
    assert out["needs_lead"] is True and "gone" in out["lead_note"]
    page = srv.get("/?project=demo", raw=True)[1]
    assert "Pick who you talk to on this project" in page
    assert "The app never picks one for you." in page


def test_lead_capability_line_matches_steer_table(env):
    srv = env.serve()
    tk = env.tk
    sock_dir = tempfile.mkdtemp(prefix="t1103c", dir="/tmp")
    try:
        sock = os.path.join(sock_dir, "s")
        Path(sock).touch()
        write(Path(tk._session_adapters().endpoint_path(str(env.demo), "coder")),
              {"seat": "coder", "provider": "claude", "mode": "native", "socket": sock, "pid": os.getpid()})
        out = srv.get("/thread.json?project=demo")[1]
        caps = {p["seat"]: p["capability"] for p in out["picker"]}
        assert caps["coder"] == "takes mid-run messages"
        assert caps["planner"] == "answers on its next turn"
        steer = tk._steer()
        assert steer.harness_refuse_reason("codex") and not steer.harness_refuse_reason("claude")
        # claude without a live socket: next turn only
        os.unlink(sock)
        assert tk.ui_capability(str(env.demo), "coder", "claude")["line"] == "answers on its next turn"
    finally:
        shutil.rmtree(sock_dir, ignore_errors=True)
    status = srv.get("/thread.json")[1]["status"]
    assert status["capability"]["line"] == "answers on its next turn"  # planner is codex
    assert "answers on its next turn; it cannot be interrupted mid-run." in srv.get("/", raw=True)[1]


def test_post_lead_is_operator_only_and_recorded(env):
    srv = env.serve()
    assert srv.post("/lead", {"seat": "coder", "project": "demo"}, token=False)[0] == 403
    assert srv.post("/lead", {"seat": "nobody", "project": "demo"})[0] == 400
    assert srv.post("/lead", {"seat": OP, "project": "demo"})[0] == 400
    st, out = srv.post("/lead", {"seat": "coder", "project": "demo"})
    assert st == 200 and out["lead"] == "coder", out
    m = json.loads((env.demo / "master.json").read_text())
    assert m == dict(m, owner="planner", cos="coder", lead="coder")
    assert "lead set to coder" in (env.demo / "MASTER.md").read_text()
    assert srv.get("/thread.json?project=demo")[1]["lead"] == "coder"
    # a master change keeps the user's lead choice
    r = cli(env, env.demo, "master", "take", agent="planner")
    assert r.returncode == 0, r.stderr
    assert json.loads((env.demo / "master.json").read_text())["lead"] == "coder"
    # the lead is persistent + continuous like master/CoS unless workforce says otherwise
    assert env.tk.wake_mode_of(str(env.demo), "coder") == "continuous"
    assert env.tk.lifecycle_of(str(env.demo), "coder") == "persistent"
    # no operator -> nobody may pick
    srv2 = env.serve(operator="")
    env.cfg.write_text(json.dumps({"projects": {}}))
    assert srv2.post("/lead", {"seat": "planner"})[0] == 400


def test_lead_cli_set_show_clear(env):
    r = cli(env, env.demo, "lead")
    assert "(none)" in r.stdout and "atm lead set" in r.stdout
    r = cli(env, env.demo, "lead", "set", "planner", agent=OP)
    assert r.returncode == 0 and "answers on its next turn" in r.stdout, r.stdout + r.stderr
    assert json.loads((env.demo / "master.json").read_text())["lead"] == "planner"
    assert cli(env, env.demo, "lead", "set", "nobody", agent=OP).returncode != 0
    r = cli(env, env.demo, "lead", "clear", agent=OP)
    assert r.returncode == 0 and "lead" not in json.loads((env.demo / "master.json").read_text())


# --- write routes: security --------------------------------------------------

def test_composer_posts_as_operator_only(env):
    srv = env.serve()
    before = (env.alpha / "messages.jsonl").read_text()
    for spoof in ("planner", "coder", "rev"):
        st, out = srv.post("/msg", {"from": spoof, "text": "impersonation attempt", "to": "coder"})
        assert st == 400 and "operator" in out["error"], out
    st, out = srv.post("/msg", {"text": "x", "to": "coder", "sender": "planner"})
    assert st == 400
    assert (env.alpha / "messages.jsonl").read_text() == before
    st, out = srv.post("/msg", {"text": "from the app", "to": "coder", "re": "T-3"})
    assert st == 200 and out["ok"] and out["from"] == OP, out
    rec = lines(env.alpha)[-1]
    assert rec["from"] == OP and rec["via"] == "ui-operator" and rec["sender_kind"] == "operator"
    assert rec["session"] == srv.ctx.session and rec["session"].startswith("ui:")
    assert srv.ctx.token not in json.dumps(rec)
    # explicit from equal to the operator is fine
    assert srv.post("/msg", {"from": OP, "text": "same person", "to": "coder"})[0] == 200
    assert srv.post("/msg", {"text": "no such ticket", "to": "coder", "re": "T-999"})[0] == 400
    assert srv.post("/msg", {"text": "path", "to": "../../etc/x"})[0] == 400
    assert srv.post("/msg", {"text": "ghost", "to": "coder,ghost"})[0] == 400
    assert srv.post("/msg", {"text": "self", "to": OP})[0] == 400
    # the page carries only the operator as a sender
    page = srv.get("/", raw=True)[1]
    assert '<meta name="atman-operator" content="%s">' % OP in page
    assert "(pick agent)" not in page and "from.disabled=true" in page


def test_no_operator_means_read_only_composer(env):
    env.cfg.write_text(json.dumps({"projects": {}}))
    srv = env.serve(operator="")
    before = (env.alpha / "messages.jsonl").read_text()
    st, out = srv.post("/msg", {"text": "hi", "to": "coder"})
    assert st == 400 and "atm ui --operator" in out["error"]
    page = srv.get("/", raw=True)[1]
    assert '<meta name="atman-operator" content="">' in page
    assert "set an operator: atm ui --operator &lt;name&gt;" in page
    # a harness-run seat is never accepted as the operator
    srv2 = env.serve(operator="coder")
    st, out = srv2.post("/msg", {"text": "hi", "to": "planner"})
    assert st == 400 and "never as a harness-run seat" in out["error"]
    assert (env.alpha / "messages.jsonl").read_text() == before


def test_host_header_must_be_loopback(env):
    srv = env.serve()
    evil = "evil.test:%d" % srv.port
    assert srv.get("/", host=evil)[0] == 403
    assert srv.get("/board.json", host=evil)[0] == 403
    assert srv.get("/thread.json", host="localhost.evil.test:%d" % srv.port)[0] == 403
    assert srv.get("/", host="127.0.0.1:%d" % (srv.port + 1))[0] == 403
    assert srv.get("/", host="")[0] == 403
    assert srv.get("/", host="localhost:%d" % srv.port)[0] == 200
    assert srv.get("/", host="[::1]:%d" % srv.port)[0] == 200
    before = (env.alpha / "messages.jsonl").read_text()
    st, out = srv.post("/msg", {"text": "rebind", "to": "coder"}, host=evil,
                       headers={"Origin": "http://" + evil})
    assert st == 403 and "loopback" in out["error"]
    assert (env.alpha / "messages.jsonl").read_text() == before
    tk = env.tk
    assert tk._ui_msg_origin_ok({"Origin": "http://" + evil, "Host": evil}) is False
    assert tk._ui_msg_origin_ok({"Origin": "http://127.0.0.1:1", "Host": "127.0.0.1:1"}) is True
    for bad in ("0.0.0.0", "192.168.1.5", "example.com", "::"):
        r = cli(env, env.alpha, "ui", "--host", bad, "--port", "0")
        assert r.returncode != 0 and "loopback" in (r.stderr + r.stdout), (bad, r.stderr)


def test_write_requires_launch_token(env):
    srv = env.serve()
    before = tree_hash(env.alpha)
    for path, body in (("/msg", {"text": "no token", "to": "coder"}), ("/lead", {"seat": "coder"}),
                       ("/auth-reconnect", {"agent": "coder"})):
        assert srv.post(path, body, token=False)[0] == 403
        assert srv.post(path, body, token="wrong-token")[0] == 403
        assert srv.post(path, body, token=srv.ctx.token + "x")[0] == 403
    assert tree_hash(env.alpha) == before
    conn = http.client.HTTPConnection("127.0.0.1", srv.port, timeout=30)
    conn.request("GET", "/")
    resp = conn.getresponse()
    resp.read()
    assert resp.getheader("X-Frame-Options") == "DENY"
    assert "frame-ancestors 'none'" in resp.getheader("Content-Security-Policy")
    assert resp.getheader("Access-Control-Allow-Origin") is None
    conn.close()
    page = srv.get("/", raw=True)[1]
    assert '<meta name="atman-token" content="%s">' % srv.ctx.token in page
    assert "'X-Atman-Token':TOKEN" in page
    other = env.serve()
    assert other.ctx.token != srv.ctx.token
    assert srv.post("/msg", {"text": "t", "to": "coder"}, token=other.ctx.token)[0] == 403
    # GET never needs the token, POST to an unknown path is 404
    assert srv.post("/nope", {"x": 1})[0] == 404


# --- the wake path -------------------------------------------------------------

class FakeSession:
    """A stand-in Claude messaging socket. The only session a test wake may reach."""

    def __init__(self):
        self.dir = tempfile.mkdtemp(prefix="t1103w", dir="/tmp")
        self.path = os.path.join(self.dir, "s")
        self.frames = []
        self.srv = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.srv.bind(self.path)
        self.srv.listen(8)
        self.srv.settimeout(0.2)
        self.stop_flag = False
        self.t = threading.Thread(target=self._loop, daemon=True)
        self.t.start()

    def _loop(self):
        while not self.stop_flag:
            try:
                c, _ = self.srv.accept()
            except (socket.timeout, OSError):
                continue
            buf = b""
            c.settimeout(2)
            try:
                while True:
                    chunk = c.recv(4096)
                    if not chunk:
                        break
                    buf += chunk
            except OSError:
                pass
            c.close()
            self.frames.extend(json.loads(x) for x in buf.decode().splitlines() if x.strip())

    def close(self):
        self.stop_flag = True
        self.t.join(2)
        self.srv.close()
        shutil.rmtree(self.dir, ignore_errors=True)


def test_ui_post_wakes_like_cli_and_only_the_fake_session(env):
    assert not any(k.startswith(("CLAUDE", "CODEX_", "CURSOR_")) for k in os.environ)
    fake = FakeSession()
    try:
        r = subprocess.run(
            [sys.executable, str(TOOL), "join", "pm", "--harness", "claude", "--persistent"],
            capture_output=True, text=True, cwd=str(env.demo.parent),
            env=dict({k: v for k, v in os.environ.items() if not k.startswith(SESSION_PREFIXES)},
                     TICKETS_DIR=str(env.demo), HOME=str(env.home), TICKETS_CACHE_DIR=str(env.tmp / "cache"),
                     ATMAN_BOARD_CONFIG=str(env.cfg), CLAUDE_CODE_MESSAGING_SOCKET=fake.path,
                     TICKET_SESSION_PID=str(os.getpid()), TICKET_SESSION_ID="t1103-pm-session"))
        assert r.returncode == 0, r.stderr
        m = json.loads((env.demo / "master.json").read_text())
        m["lead"] = "pm"
        write(env.demo / "master.json", m)
        tk = env.tk
        ep = tk._session_adapters().read_endpoint(str(env.demo), "pm")
        assert ep["socket"] == fake.path  # the endpoint names only the fake session
        srv = env.serve(board=env.demo)
        st, out = srv.post("/msg", {"text": "app wake probe", "to": "pm"})
        assert st == 200 and out["ok"], out
        ui_label = out["wakes"][0]["label"]
        ui_rec = json.loads((env.demo / "agents" / "pm.json").read_text())["wake_delivery"]
        assert ui_rec["label"] == ui_label
        r = cli(env, env.demo, "msg", "--to", "pm", "cli wake probe", "--owner", OP, agent=OP)
        assert r.returncode == 0, r.stderr
        assert ("wake: pm -> %s" % ui_label) in r.stdout, r.stdout
        cli_rec = json.loads((env.demo / "agents" / "pm.json").read_text())["wake_delivery"]
        assert cli_rec["label"] == ui_rec["label"]
        deadline = time.time() + 5
        while len(fake.frames) < 2 and time.time() < deadline:
            time.sleep(0.05)
        texts = [f["message"]["content"] for f in fake.frames if f.get("type") == "user"]
        assert any("app wake probe" in t for t in texts) and any("cli wake probe" in t for t in texts)
        # the post is the operator's, and the wake never went anywhere but the fake socket
        assert lines(env.demo)[-2]["from"] == OP and lines(env.demo)[-2]["via"] == "ui-operator"
    finally:
        fake.close()


def test_limited_lead_post_is_queued_and_says_so(env):
    lim = json.loads((env.alpha / "agents" / "planner.json").read_text())
    lim["limit"] = {"at": stamp(2), "until": "17:40", "note": "cap"}
    write(env.alpha / "agents" / "planner.json", lim)
    srv = env.serve()
    t0 = time.time()
    st, out = srv.post("/msg", {"text": "are you there?", "to": "planner"})
    assert st == 200 and out["wakes"] == [{"to": "planner", "label": "limited"}]
    assert time.time() - t0 < 10
    status = srv.get("/thread.json")[1]["status"]
    assert status["cannot_answer"]["kind"] == "limited"
    assert "limited until 17:40" in status["cannot_answer"]["text"]
    assert "queued" in status["cannot_answer"]["text"]


def test_lead_status_names_logged_out_offline_and_quota(env):
    tk = env.tk
    board = str(env.alpha)
    st = tk.ui_lead_status(board, "planner")
    assert st["cannot_answer"]["kind"] == "offline"
    assert "atm spawn planner --persist" in st["cannot_answer"]["text"]
    assert st["usage"]["age"] == "age unknown" and st["usage"]["remaining_pct"] is None
    write(env.alpha / "provider_usage.json", {"providers": {"codex": {
        "provider": "codex", "status": "limited", "reset_at": "", "checked_at": stamp(12), "source": "cli"}}})
    st = tk.ui_lead_status(board, "planner")
    assert st["cannot_answer"]["kind"] == "quota" and "last read 12m ago" in st["cannot_answer"]["text"]
    rec = json.loads((env.alpha / "agents" / "planner.json").read_text())
    rec["auth_check"] = {"state": "login_required", "login_cmd": "codex login", "harness": "codex"}
    write(env.alpha / "agents" / "planner.json", rec)
    st = tk.ui_lead_status(board, "planner")
    assert st["cannot_answer"]["kind"] == "logged_out" and "logged out" in st["cannot_answer"]["text"]
    assert st["state"] in tk.STATE_WORDS


# --- honesty -----------------------------------------------------------------

def test_harness_badge_never_defaults_to_claude(env):
    srv = env.serve()
    msg(env.alpha, id="m7", at=stamp(2), **{"from": "scout"}, to=OP, text="no harness on file")
    msg(env.alpha, id="m8", at=stamp(1), **{"from": "planner"}, to=OP, text="stamped", harness="codex")
    snap = srv.get("/board.json")[1]
    by = {a["name"]: a for a in snap["agents"]}
    assert by["scout"]["harness"] == "unknown"
    assert by["coder"]["harness"] == "claude"
    tk = env.tk
    wf = tk.load_workforce(str(env.alpha))
    rows = {m["id"]: tk._ui_post_row(str(env.alpha), m, "alpha", OP, wf, {}) for m in lines(env.alpha)}
    assert rows["m7"]["harness"] == {"value": "unknown", "recorded": False, "note": "harness not recorded"}
    assert rows["m2"]["harness"]["value"] == "codex" and rows["m2"]["harness"]["recorded"] is False
    assert rows["m8"]["harness"]["recorded"] is True
    assert rows["m1"]["harness"]["value"] == "operator"
    assert all(r["harness"]["value"] != "claude" for k, r in rows.items() if k in ("m7", "m5", "m6"))
    assert rows["m2"]["author"] == "planner@alpha"


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
    snap = srv.get("/board.json")[1]
    assert len(snap["messages"]) == 40
    page = srv.get("/thread.json")[1]
    assert len(page["messages"]) == 100 and page["has_more"] is True
    got = [m["id"] for m in page["messages"]]
    while page["has_more"]:
        page = srv.get("/thread.json?before=" + page["oldest_id"])[1]
        got = [m["id"] for m in page["messages"]] + got
    assert len(got) == thread == len(set(got))
    assert got[0] == "p000" and all(m.startswith("p") for m in got)
    assert srv.get("/thread.json?before=nope")[1]["error"].startswith("unknown cursor")
    assert len(srv.get("/thread.json?limit=5000")[1]["messages"]) == 200


def test_blocker_chip_distinguishes_unaccepted_dep(env):
    srv = env.serve()
    nodes = {n["id"]: n for n in srv.get("/board.json")[1]["work"]["nodes"]}
    kinds = lambda tid: {b["kind"]: b for b in nodes[tid]["blockers"]}
    assert "dep_unaccepted" in kinds("T-5") and "dep_open" not in kinds("T-5")
    assert kinds("T-5")["dep_unaccepted"]["text"] == "dep T-2 done, not accepted"
    assert "dep_open" in kinds("T-4") and kinds("T-4")["dep_open"]["text"] == "dep T-3 still in flight"
    assert kinds("T-4")["seat_limited"]["text"] == "seat rev limited until 17:40"
    assert not [k for k in kinds("T-3") if k.startswith("dep_")]  # accepted dep T-1: no dep chip
    assert "seat_offline" in kinds("T-6")
    assert "T-1" not in nodes or (nodes["T-1"]["unverified"] is False and nodes["T-1"]["blockers"] == [])
    rec = json.loads((env.alpha / "agents" / "rev.json").read_text())
    rec["auth_check"] = {"state": "login_required", "harness": "codex"}
    write(env.alpha / "agents" / "rev.json", rec)
    time.sleep(0.01)
    nodes = {n["id"]: n for n in srv.get("/board.json")[1]["work"]["nodes"]}
    assert any(b["kind"] == "auth" for b in nodes["T-4"]["blockers"])
    # the chip agrees with the gate the CLI enforces
    wv = env.tk._work_view()
    tickets = env.tk.load_all(str(env.alpha))
    by = {t["id"]: t for t in tickets}
    assert wv.unreleased_dep_id(by["T-5"], tickets) == "T-2"
    assert wv.unreleased_dep_id(by["T-3"], tickets) == ""


def test_drilldown_tokens_unknown_not_zero(env):
    srv = env.serve()
    st, t = srv.get("/ticket/T-3.json")
    assert st == 200
    runs = {r["seat"]: r for r in t["runs"]}
    assert runs["coder"]["tokens"] is None and runs["coder"]["tokens_label"] == "unknown"
    assert runs["rev"]["tokens"] == 41200 and runs["rev"]["tokens_label"] == "41,200"
    assert runs["rev"]["verdict"].startswith("REJECT")
    assert runs["coder"]["author"] == "coder@alpha"
    usage = {u["provider"]: u for u in t["usage"]}
    assert usage["codex"]["age"] == "age unknown" and usage["codex"]["remaining_pct"] is None
    assert usage["claude"]["age"] == "age unknown"
    assert t["review"]["head"] == HEAD and t["review"]["head_len"] == 40
    assert t["review"]["verdicts"][0]["kind"] == "reject" and t["review"]["verdicts"][0]["sha"] == HEAD
    assert t["diff_cmd"] == "git diff main...%s" % HEAD
    assert "never runs git" in t["diff_note"]
    assert t["artifact"]["branch"] == "t3-branch" and t["artifact"]["pr"].endswith("/pr/7")
    assert [m["id"] for m in t["messages"]] == ["m3"]
    assert t["steers"][0]["text"] == "390 covered?"
    assert t["handoff"][0]["from"] == "T-1"
    assert srv.get("/ticket/T-404.json")[0] == 404
    assert srv.get("/ticket/..%2Fmaster.json")[0] in (400, 404)
    assert srv.get("/ticket/master.json")[0] == 400
    write(env.alpha / "provider_usage.json", {"providers": {"codex": {
        "provider": "codex", "status": "ok", "remaining": 62, "windows": [{"name": "5h", "used_percent": 38}],
        "checked_at": stamp(3), "source": "cli"}}})
    usage = {u["provider"]: u for u in srv.get("/ticket/T-3.json")[1]["usage"]}
    assert usage["codex"]["age"] == "last read 3m ago"


def test_done_without_accept_is_never_accepted_in_v2_shell(env):
    srv = env.serve()
    snap = srv.get("/board.json")[1]
    nodes = {n["id"]: n for n in snap["work"]["nodes"]}
    assert nodes["T-2"]["unverified"] is True
    assert [b["text"] for b in nodes["T-2"]["blockers"]] == ["done, not accepted"]
    assert snap["counts"]["accepted"] == 1 and snap["counts"]["done_unverified"] == 1
    t2 = srv.get("/ticket/T-2.json")[1]
    assert t2["status_label"] == "done, not accepted" and t2["accepted"] is False
    t1 = srv.get("/ticket/T-1.json")[1]
    assert t1["status_label"] == "done, accepted" and t1["accepted"] is True
    # prose is not an accept
    msg(env.alpha, id="m9", **{"from": "rev"}, re="T-2", text="ACCEPT, looks good")
    assert srv.get("/ticket/T-2.json")[1]["accepted"] is False
    snap = srv.get("/board.json")[1]
    nodes = {n["id"]: n for n in snap["work"]["nodes"]}
    assert nodes["T-2"]["unverified"] is True
    assert snap["counts"]["accepted"] == 1 and snap["counts"]["done_unverified"] == 1
    page = srv.get("/", raw=True)[1]
    assert "done, not accepted" in page


def test_receipts_never_say_ack(env):
    srv = env.serve()
    tk = env.tk
    rec = json.loads((env.alpha / "agents" / "planner.json").read_text())
    rec["wake_delivery"] = {"label": "woken", "msg_id": "m1", "at": stamp(1)}
    rec["inbox_seen"] = stamp(0)
    write(env.alpha / "agents" / "planner.json", rec)
    words = []
    for m in srv.get("/thread.json")[1]["messages"] + srv.get("/ticket/T-3.json")[1]["messages"]:
        for r in m["receipts"]:
            words += r["words"]
    assert words and "posted" in words and "inbox read" in words
    assert not [w for w in words if re.search(r"ack|acknowledg|understood|on it", w, re.I)]
    assert tk._ui_receipt_words(True, {"label": "acked-by-agent"}) == ["posted", "inbox read", "wake: receipt recorded"]
    page = srv.get("/", raw=True)[1]
    assert "function receiptsHtml(rs){return (rs||[]).map(r=>" in page and "r.words" in page


def test_needs_you_is_asked_only(env):
    srv = env.serve()
    out = srv.get("/needs-you.json")[1]
    items = {i["id"]: i for i in out["items"]}
    assert items["m4"]["state"] == "asked" and items["m4"]["label"] == "asked (unstructured)"
    assert "m5" in items and "m6" not in items  # stuck > 1h only
    assert "m2" in items  # addressed to the operator, no reply since
    assert all(i["state"] == "asked" for i in out["items"])
    assert "ruled" not in json.dumps(out).lower().replace("ruling", "")
    # the operator answers the lead: that ask leaves the queue
    srv.post("/msg", {"text": "thanks, noted", "to": "planner"})
    assert "m2" not in {i["id"] for i in srv.get("/needs-you.json")[1]["items"]}
    # escalated automated nodes are listed too
    ticket(env.alpha, "T-7", "Auto step", kind="automated",
           automated={"escalated": True, "escalate_reason": "judgment needed", "escalated_at": stamp(4)})
    assert "T-7" in {i["id"] for i in srv.get("/needs-you.json")[1]["items"]}


def test_seat_at_project_is_not_a_mention(env):
    srv = env.serve()
    st, out = srv.post("/msg", {"text": "coder@demo and planner@alpha please look", "to": "planner"})
    assert st == 200, out
    assert "mentions" not in lines(env.alpha)[-1]


def test_page_has_no_voice_and_no_external_product_names(env):
    srv = env.serve()
    page = srv.get("/", raw=True)[1]
    for word in ("SpeechRecognition", "speechSynthesis", "webkitSpeech", "getUserMedia", ">Mic<", "Voice"):
        assert word not in page, word
    for name in ("Slack", "Discord", "Microsoft Teams", "Jira", "Notion", "Asana", "Trello", "Linear",
                 "ChatGPT", "Copilot", "Minions", "Inspect"):
        assert name not in page, name
    # shell regions exist, and every surface of the Team IA lock is still there
    for marker in ('id="appSide"', 'id="projectSel"', 'id="leadPane"', 'id="leadThread"', 'id="leadCompose"',
                   'id="drill"', 'id="needsBox"', 'data-side="fleet"', 'data-side="runs"', 'data-side="hub"',
                   'id="sideClock"', 'data-tab-btn="objective"', 'data-tab-btn="agents"', 'data-tab-btn="board"',
                   'data-tab-btn="messages"', 'data-work-view="columns"'):
        assert marker in page, marker


# --- browser check (skipped when no local Chrome is available) ----------------

def _chrome():
    pw = pytest.importorskip("playwright.sync_api")
    return pw


def test_browser_1440_and_390_plan_chat_and_drill(env):
    pw = _chrome()
    srv = env.serve()
    base = "http://127.0.0.1:%d" % srv.port
    try:
        ctx = pw.sync_playwright().start()
    except Exception as e:  # pragma: no cover
        pytest.skip("playwright unavailable: %s" % e)
    try:
        try:
            browser = ctx.chromium.launch(channel="chrome", headless=True)
        except Exception:
            try:
                browser = ctx.chromium.launch(headless=True)
            except Exception as e:
                pytest.skip("no headless browser: %s" % e)
        for width, height in ((1440, 900), (390, 844)):
            pg = browser.new_page(viewport={"width": width, "height": height})
            errors = []
            pg.on("pageerror", lambda e: errors.append(str(e)))
            pg.goto(base + "/")
            pg.wait_for_selector("#leadPosts .post", timeout=20000)
            pg.wait_for_selector("#workflowGraph button[data-id]", timeout=20000, state="attached")
            assert pg.evaluate("document.documentElement.scrollWidth<=document.documentElement.clientWidth")
            posts = pg.inner_text("#leadPosts")
            assert "planner@alpha" in posts and "codex" in posts
            assert not re.search(r"acknowledg|\bACK\b", posts)
            if width < 900:
                pg.click("#sideNav [data-side=plan]")
            chips = pg.eval_on_selector_all(
                "#workflowGraph button[data-id]",
                "els=>Object.fromEntries(els.map(e=>[e.dataset.id,[...e.querySelectorAll('.wv-chip')].map(c=>c.textContent)]))")
            assert chips["T-5"] == ["dep T-2 done, not accepted"]
            assert "seat rev limited until 17:40" in chips["T-4"]
            pg.click('#workflowGraph button[data-id="T-2"]')
            pg.wait_for_selector("#drillBody .dh", timeout=20000)
            assert "done, not accepted" in pg.inner_text("#drillBody").lower()
            assert pg.evaluate("document.documentElement.scrollWidth<=document.documentElement.clientWidth")
            pg.click("[data-drill-back]")
            assert pg.evaluate("document.getElementById('drill').hidden") is True
            assert pg.evaluate("window.AtmanWork.selected()") == "T-2"
            assert errors == []
            pg.close()
        browser.close()
    finally:
        ctx.stop()


def test_shared_board_repo_lens_is_read_only_node_data(env):
    t = json.loads((env.alpha / "T-4.json").read_text())
    t["repo"] = "example.invalid/org/steer.git"
    write(env.alpha / "T-4.json", t)
    srv = env.serve()
    before = tree_hash(env.alpha)
    nodes = {n["id"]: n for n in srv.get("/board.json")[1]["work"]["nodes"]}
    assert nodes["T-4"]["repo"] == "example.invalid/org/steer.git" and nodes["T-5"]["repo"] == ""
    assert tree_hash(env.alpha) == before
    page = srv.get("/", raw=True)[1]
    assert 'id="repoLens"' in page and "function lensWork(w)" in page
