"""T-1104: atm ui composer posts as a person operator, never a harness seat."""
import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path

from test_wakeup import board, run  # noqa: F401
from ui_server_harness import TOOL, UiServer


def _live(board_dir):
    path = board_dir / "messages.jsonl"
    return path.read_text() if path.exists() else ""


def _last_rec(board_dir):
    lines = [ln for ln in _live(board_dir).splitlines() if ln.strip()]
    assert lines, "expected a message on the board"
    return json.loads(lines[-1])


def _join_person(board_dir, name):
    """A person: atm join with no --harness. Not a workforce/harness seat."""
    r = run(board_dir, "join", name, agent=name)
    assert r.returncode == 0, r.stderr


def test_composer_posts_as_operator_only(board):
    """Payload from:<seat> is refused. A real post is the operator + ui-operator marker."""
    _join_person(board, "pat")
    _join_person(board, "worker")
    srv = UiServer(board, probe_prefix="t1104-op", operator="pat")
    try:
        status, out = srv.post("/msg", {
            "from": "worker", "text": "impersonating a seat", "to": "",
        })
        assert status == 400 and not out["ok"], out
        err = (out.get("error") or "").lower()
        assert "operator" in err or "from" in err
        assert "impersonating a seat" not in _live(board)

        status, out = srv.post("/msg", {"text": "hello from the operator", "to": "worker"})
        assert status == 200 and out["ok"], out
        rec = _last_rec(board)
        assert rec["from"] == "pat"
        assert rec["via"] == "ui-operator"
        assert rec.get("sender_kind") == "operator"
        assert rec["session"] == "ui-operator"
        assert rec["endpoint_pid"] == "ui-operator"
        assert "leadership_flag" not in rec
        assert rec["text"] == "hello from the operator"
        assert rec["to"] == "worker"
        assert rec.get("unverified") is False
        snap = srv.get("/board.json")
        assert snap.get("operator") == "pat"
    finally:
        srv.stop()


def test_composer_disabled_without_operator(board):
    """No --operator: composer copy names the flag; POST /msg is 400."""
    _join_person(board, "worker")
    srv = UiServer(board, probe_prefix="t1104-none")
    try:
        page = srv.get("/", raw=True).decode()
        assert "set an operator: atm ui --operator" in page
        assert 'id="cFrom"' in page
        assert "(pick agent)" not in page
        status, out = srv.post("/msg", {
            "from": "worker", "text": "no-operator-should-not-land", "to": "",
        })
        assert status == 400 and not out["ok"], out
        assert "operator" in (out.get("error") or "").lower()
        assert "no-operator-should-not-land" not in _live(board)
        snap = srv.get("/board.json")
        assert snap.get("operator") == ""
    finally:
        srv.stop()


def test_matching_operator_from_is_accepted(board):
    """from matching the operator is allowed; still recorded as ui-operator."""
    _join_person(board, "pat")
    srv = UiServer(board, probe_prefix="t1104-match", operator="pat")
    try:
        status, out = srv.post("/msg", {
            "from": "pat", "text": "operator named themselves", "to": "",
        })
        assert status == 200 and out["ok"], out
        rec = _last_rec(board)
        assert rec["from"] == "pat"
        assert rec["via"] == "ui-operator"
        assert rec["session"] == "ui-operator"
    finally:
        srv.stop()


def test_harness_seat_refused_as_operator(board):
    """--operator alice (a claude seat) must not start or post as a verified operator."""
    r = run(board, "join", "alice", "--harness", "claude", agent="alice")
    assert r.returncode == 0, r.stderr
    home = board.parent.parent / "home"
    cache = board.parent / "cache"
    cache.mkdir(exist_ok=True)
    home.mkdir(exist_ok=True)
    env = dict(os.environ, TICKETS_DIR=str(board), HOME=str(home),
               TICKETS_CACHE_DIR=str(cache))
    for var in ("TICKET_SEAT", "TICKET_AGENT", "TICKET_SESSION_ID",
                "CLAUDE_CODE_SESSION_ID", "CODEX_SESSION_ID", "CURSOR_SESSION_ID",
                "TERM_SESSION_ID"):
        env.pop(var, None)
    proc = subprocess.run(
        [sys.executable, str(TOOL), "ui", "--json", "--operator", "alice"],
        env=env, capture_output=True, text=True, cwd=board.parent,
    )
    assert proc.returncode != 0, proc.stdout
    err = (proc.stderr or proc.stdout or "").lower()
    assert "harness-run seat" in err
    assert "alice" in err


def test_session_endpoint_seat_refused_as_operator(board):
    """A person join plus a registered harness session is still a seat."""
    _join_person(board, "sam")
    here = Path(__file__).resolve().parents[1]
    sys.path.insert(0, str(here))
    import session_adapters as sa
    home = board.parent.parent / "home"
    cache = board.parent / "cache"
    cache.mkdir(exist_ok=True)
    home.mkdir(exist_ok=True)
    prev_home = os.environ.get("HOME")
    prev_cache = os.environ.get("TICKETS_CACHE_DIR")
    os.environ["HOME"] = str(home)
    os.environ["TICKETS_CACHE_DIR"] = str(cache)
    try:
        sa.write_endpoint(str(board), "sam", {"provider": "claude", "socket": "/tmp/t1104.sock"})
    finally:
        if prev_home is None:
            os.environ.pop("HOME", None)
        else:
            os.environ["HOME"] = prev_home
        if prev_cache is None:
            os.environ.pop("TICKETS_CACHE_DIR", None)
        else:
            os.environ["TICKETS_CACHE_DIR"] = prev_cache
    env = dict(os.environ, TICKETS_DIR=str(board), HOME=str(home),
               TICKETS_CACHE_DIR=str(cache))
    for var in ("TICKET_SEAT", "TICKET_AGENT", "TICKET_SESSION_ID",
                "CLAUDE_CODE_SESSION_ID"):
        env.pop(var, None)
    proc = subprocess.run(
        [sys.executable, str(TOOL), "ui", "--json", "--operator", "sam"],
        env=env, capture_output=True, text=True, cwd=board.parent,
    )
    assert proc.returncode != 0, proc.stdout
    assert "harness-run seat" in (proc.stderr or proc.stdout or "").lower()


def _ui_json_operator(board, operator):
    home = board.parent.parent / "home"
    cache = board.parent / "cache"
    cache.mkdir(exist_ok=True)
    home.mkdir(exist_ok=True)
    env = dict(os.environ, TICKETS_DIR=str(board), HOME=str(home),
               TICKETS_CACHE_DIR=str(cache))
    for var in ("TICKET_SEAT", "TICKET_AGENT", "TICKET_SESSION_ID",
                "CLAUDE_CODE_SESSION_ID", "CODEX_SESSION_ID", "CURSOR_SESSION_ID",
                "TERM_SESSION_ID"):
        env.pop(var, None)
    return subprocess.run(
        [sys.executable, str(TOOL), "ui", "--json", "--operator", operator],
        env=env, capture_output=True, text=True, cwd=board.parent,
    )


def test_operator_case_variant_of_harness_seat_refused(board):
    """--operator HJOIN / Hjoin / WFH must not slip past a lowercase harness seat."""
    r = run(board, "join", "hjoin", "--harness", "claude", agent="hjoin")
    assert r.returncode == 0, r.stderr
    r = run(board, "join", "wfh", "--harness", "cursor", agent="wfh")
    assert r.returncode == 0, r.stderr
    for variant in ("HJOIN", "Hjoin", "WFH"):
        proc = _ui_json_operator(board, variant)
        assert proc.returncode != 0, (variant, proc.stdout, proc.stderr)
        err = (proc.stderr or proc.stdout or "").lower()
        assert "harness-run seat" in err, (variant, err)


def test_agent_record_harness_refused_as_operator(board):
    """harness only on agents/<name>.json is still a harness-run seat."""
    _join_person(board, "onlyrec")
    rec_path = board / "agents" / "onlyrec.json"
    rec = json.loads(rec_path.read_text())
    rec["harness"] = "claude"
    rec_path.write_text(json.dumps(rec))
    proc = _ui_json_operator(board, "onlyrec")
    assert proc.returncode != 0, proc.stdout
    assert "harness-run seat" in (proc.stderr or proc.stdout or "").lower()


def test_workforce_provider_only_refused_as_operator(board):
    """A workforce entry with only provider is harness-run."""
    _join_person(board, "provonly")
    wf_path = board / "workforce.json"
    wf = json.loads(wf_path.read_text()) if wf_path.exists() else {}
    wf["provonly"] = {"provider": "claude"}
    wf_path.write_text(json.dumps(wf))
    proc = _ui_json_operator(board, "provonly")
    assert proc.returncode != 0, proc.stdout
    assert "harness-run seat" in (proc.stderr or proc.stdout or "").lower()


def test_person_operator_case_rewritten_to_canonical(board):
    """--operator Pat posts as the registered owner pat, not the typed case."""
    _join_person(board, "pat")
    srv = UiServer(board, probe_prefix="t1104-case-ok", operator="Pat")
    try:
        status, out = srv.post("/msg", {"text": "typed as Pat", "to": ""})
        assert status == 200 and out["ok"], out
        rec = _last_rec(board)
        assert rec["from"] == "pat"
        assert rec["via"] == "ui-operator"
        assert rec.get("unverified") is False
        snap = srv.get("/board.json")
        assert snap.get("operator") == "pat"
    finally:
        srv.stop()


def test_ui_process_session_id_does_not_reach_record(board):
    """CLAUDE_CODE_SESSION_ID in the atm ui process must not stamp the message."""
    _join_person(board, "pat")
    leaked = "leaked-claude-session-for-t1104"
    leaked_hash = hashlib.sha256(leaked.encode()).hexdigest()[:16]
    srv = UiServer(
        board, probe_prefix="t1104-sess", operator="pat",
        env={"CLAUDE_CODE_SESSION_ID": leaked},
    )
    try:
        status, out = srv.post("/msg", {"text": "from the page, not the ui shell", "to": ""})
        assert status == 200 and out["ok"], out
        rec = _last_rec(board)
        assert rec["from"] == "pat"
        assert rec["via"] == "ui-operator"
        assert rec["session"] == "ui-operator"
        assert rec["session"] != leaked_hash
        assert rec["endpoint_pid"] == "ui-operator"
        assert leaked not in json.dumps(rec)
        assert leaked_hash not in json.dumps(rec)
        assert "leadership_flag" not in rec
    finally:
        srv.stop()
