"""T-788: any-CEO connect path is the product flow (throwaway board).

Living board `tickets connect` must run catalog+usage → atman-<seat> →
announce → feedback → graph/map. It must not print tickets next. Fresh
boards keep the T-778/T-780 new-board script.
"""
import json
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TOOL = ROOT / "tickets.py"
HOWTO = ROOT / "docs/onboarding/master-howto.md"
CEO_DOC = ROOT / "docs/onboarding/ceo-connect.md"
CLI = ROOT / "src/ticket_board/cli.py"

PLAN = json.dumps([
    {"key": "api", "title": "Build REST API", "role": "backend", "deps": []},
    {"key": "ui", "title": "Build login UI", "role": "frontend", "deps": ["api"]},
])


def clean_env(tmp_path, **overrides):
    e = {
        "PATH": str(tmp_path / "bin") + os.pathsep + "/usr/bin" + os.pathsep + "/bin",
        "HOME": str(tmp_path / "fake-home"),
        "TICKET_AGENT": "atman-ceo",
        "PYTEST_CURRENT_TEST": "tests/test_t788_ceo_connect.py::simulated (call)",
    }
    (tmp_path / "fake-home").mkdir(exist_ok=True)
    (tmp_path / "bin").mkdir(exist_ok=True)
    e.update(overrides)
    e.pop("TICKETS_DIR", None)
    return e


def run(cwd, *args, env=None, tmp_path=None, stdin=None):
    return subprocess.run(
        [sys.executable, str(TOOL), *args],
        capture_output=True, text=True, cwd=str(cwd),
        env=env or clean_env(tmp_path),
        input=stdin)


def git(cwd, *args):
    return subprocess.run(
        ["git", "-c", "user.email=t788@test", "-c", "user.name=t788", *args],
        cwd=str(cwd), capture_output=True, text=True, check=True)


def make_repo(path):
    path.mkdir(parents=True)
    git(path, "init", "-q", "-b", "main")
    (path / "README").write_text("t788\n")
    git(path, "add", "README")
    git(path, "commit", "-qm", "init")
    return path


def first_nonempty(text):
    for ln in (text or "").splitlines():
        if ln.strip():
            return ln.strip()
    return ""


def living_board(tmp_path):
    repo = make_repo(tmp_path / "repo")
    env = clean_env(tmp_path)
    assert run(repo, "init", env=env, tmp_path=tmp_path).returncode == 0
    r = run(repo, "objective", "--set", "Ship the living objective",
            env=env, tmp_path=tmp_path)
    assert r.returncode == 0, r.stderr
    r = run(repo, "plan", env=env, tmp_path=tmp_path, stdin=PLAN)
    assert r.returncode == 0, r.stderr + r.stdout
    return repo, env


def test_fresh_connect_keeps_new_board_script(tmp_path):
    repo = make_repo(tmp_path / "repo")
    env = clean_env(tmp_path)
    assert run(repo, "init", env=env, tmp_path=tmp_path).returncode == 0
    c = run(repo, "connect", env=env, tmp_path=tmp_path)
    assert c.returncode == 0, c.stderr
    assert first_nonempty(c.stdout).startswith("**You are onboarding.**")
    assert "tickets plan" in c.stdout
    assert "Connecting an agent" in c.stdout


def test_living_connect_is_atman_ceo_product_flow(tmp_path):
    repo, env = living_board(tmp_path)
    c = run(repo, "connect", env=env, tmp_path=tmp_path)
    assert c.returncode == 0, c.stderr
    out = c.stdout
    assert first_nonempty(out).startswith("**You are onboarding as Atman CEO.**")
    assert "not Claude" in out or "not a provider" in out.lower() or "not Claude, Cursor, Codex" in out
    assert "1. CATALOG + SUBSCRIPTIONS" in out
    assert "cursor" in out and "gemini" in out      # catalog rows, not a staffing claim
    assert "USAGE" in out
    assert "2. LIVING BOARD" in out
    assert "do not invent a new team" in out.lower() or "Do not invent a new team" in out
    assert "Ship the living objective" in out
    assert "atman-ceo" in out
    assert "ANNOUNCE THE ATMAN ROLE" in out
    assert "ASK FOR FEEDBACK" in out
    assert "tickets graph" in out and "tickets map" in out
    assert "CoS" in out
    # This board records no CoS, so connect must SAY so rather than name a
    # provider. Asserting the literal "cursor" here is what let the hardcode
    # survive: the test passed on the catalog row while the staffing line was
    # telling every CEO to mail a seat that had never joined.
    assert "(unset)" in out or "NO CoS RECORDED" in out
    assert "tickets hooks <your-harness>" in out
    assert "tickets hooks codex" in out
    assert "tickets hooks remote" in out
    assert "tickets next" not in out
    assert "Paste this at the start of ANY agent session" not in out


def test_connect_never_names_a_provider_as_the_cos(tmp_path):
    """A harness is not a seat. No provider may be asserted as the CoS."""
    repo, env = living_board(tmp_path)
    out = run(repo, "connect", env=env, tmp_path=tmp_path).stdout
    for provider in ("cursor", "claude", "codex", "gemini", "grok", "agy", "devin"):
        assert "CoS is %s" % provider not in out
        assert "CoS (`%s`)" % provider not in out
        assert "CoS (%s) staffs" % provider not in out
        assert "--to %s " % provider not in out


def test_connect_resolves_the_recorded_cos_from_the_board(tmp_path):
    """Once a CoS is recorded, connect must address THAT seat."""
    repo, env = living_board(tmp_path)
    # Deliberately NOT "atman-ceo": run()'s session id is derived from the actor
    # name and is not board-scoped, so reusing a seat name another test in this
    # file also joins leaks that session's binding across boards. Own seat name,
    # own session. (The underlying scoping gap is T-020's, not this test's.)
    env = dict(env, TICKET_AGENT="costest-lead")
    assert run(repo, "join", "costest-lead", "--roles", "master",
               env=env, tmp_path=tmp_path).returncode == 0
    assert run(repo, "master", "take", env=env, tmp_path=tmp_path).returncode == 0
    assert run(repo, "master", "cos", "real-cos-seat",
               env=env, tmp_path=tmp_path).returncode == 0
    out = run(repo, "connect", env=env, tmp_path=tmp_path).stdout
    assert "CoS is real-cos-seat" in out
    assert "--to real-cos-seat" in out
    assert "CoS is cursor" not in out


def test_living_board_is_still_asked_the_subscription_question(tmp_path):
    """The ask was gated to blank boards only; that gate is the root cause.

    A living board used to get a hardcoded staffing claim INSTEAD of the
    question, which is how operators without Cursor installed were told Cursor
    staffs their board. Which providers are subscribed is operator knowledge on
    every board.
    """
    repo, env = living_board(tmp_path)
    for argv in (("connect",), ("harness", "available")):
        out = run(repo, *argv, env=env, tmp_path=tmp_path).stdout
        assert "SUBSCRIPTIONS -- ASK THE OPERATOR" in out, argv
        assert "Installed is NOT subscribed" in out, argv
        assert "on this machine:" in out, argv


def test_connect_ceo_flag_on_blank_board(tmp_path):
    repo = make_repo(tmp_path / "repo")
    env = clean_env(tmp_path)
    assert run(repo, "init", env=env, tmp_path=tmp_path).returncode == 0
    c = run(repo, "connect", "--ceo", "--seat", "cto", env=env, tmp_path=tmp_path)
    assert c.returncode == 0, c.stderr
    assert "atman-cto" in c.stdout
    assert "tickets next" not in c.stdout


def test_worker_connect_on_living_board_still_has_next(tmp_path):
    repo, env = living_board(tmp_path)
    c = run(repo, "connect", "--worker", env=env, tmp_path=tmp_path)
    assert c.returncode == 0, c.stderr
    assert "tickets next" in c.stdout


def test_ceo_join_does_not_print_tickets_next(tmp_path):
    repo, env = living_board(tmp_path)
    env = dict(env)
    env["TICKET_AGENT"] = "atman-ceo"
    j = run(repo, "join", "atman-ceo", "--roles", "master", env=env, tmp_path=tmp_path)
    assert j.returncode == 0, j.stderr + j.stdout
    assert "joined as atman-ceo" in j.stdout
    assert "CEO does not claim worker tickets" in j.stdout
    assert "tickets graph / tickets map" in j.stdout
    assert "tickets next" not in j.stdout


def test_ceo_can_run_graph_and_map(tmp_path):
    repo, env = living_board(tmp_path)
    g = run(repo, "graph", env=env, tmp_path=tmp_path)
    assert g.returncode == 0, g.stderr
    assert "T-001" in g.stdout and "T-002" in g.stdout
    assert "waiting on T-001" in g.stdout
    m = run(repo, "map", env=env, tmp_path=tmp_path)
    assert m.returncode == 0, m.stderr
    assert "T-001" in m.stdout and "T-002" in m.stdout


def test_announce_and_feedback_mail(tmp_path):
    repo, env = living_board(tmp_path)
    env = dict(env)
    env["TICKET_AGENT"] = "atman-ceo"
    assert run(repo, "join", "atman-ceo", "--roles", "master",
               env=env, tmp_path=tmp_path).returncode == 0
    a = run(repo, "msg", "--to", "everyone",
             "atman-ceo is Atman CEO on this living board. CoS is cursor. Integrating: cursor. @everyone",
             env=env, tmp_path=tmp_path)
    assert a.returncode == 0, a.stderr
    assert "posted:" in a.stdout
    env["TICKET_AGENT"] = "cursor"
    assert run(repo, "join", "cursor", "--roles", "backend",
               env=env, tmp_path=tmp_path).returncode == 0
    env["TICKET_AGENT"] = "atman-ceo"
    f = run(repo, "msg", "--to", "cursor",
             "CEO atman-ceo onboarded. Operator feedback: keep catalog first.",
             env=env, tmp_path=tmp_path)
    assert f.returncode == 0, f.stderr
    env["TICKET_AGENT"] = "cursor"
    inbox = run(repo, "inbox", "--keep", env=env, tmp_path=tmp_path)
    assert inbox.returncode == 0, inbox.stderr
    assert "atman-ceo" in inbox.stdout
    assert "feedback" in inbox.stdout.lower() or "catalog first" in inbox.stdout


def test_docs_and_template_teach_ceo_path():
    src = TOOL.read_text()
    cli = CLI.read_text()
    howto = HOWTO.read_text()
    assert "atman-<seat>" in src and "atman-<seat>" in cli
    assert "CEO ONBOARDING" in src and "CEO ONBOARDING" in cli
    assert "Ask the operator for feedback" in src or "ASK FOR FEEDBACK" in src
    assert CEO_DOC.exists()
    body = CEO_DOC.read_text()
    assert "tickets connect" in body
    assert "atman-ceo" in body
    assert "tickets graph" in body and "tickets map" in body
    assert "stale shim" in body.lower() or "sol-agy-harness" in body


def test_master_on_living_board_does_not_lead_with_new_board_script(tmp_path):
    repo, env = living_board(tmp_path)
    m = run(repo, "master", env=env, tmp_path=tmp_path)
    assert m.returncode == 0, m.stderr
    assert first_nonempty(m.stdout).startswith("====") or first_nonempty(m.stdout).startswith("MASTER")
    assert not first_nonempty(m.stdout).startswith("**You are onboarding.**")
