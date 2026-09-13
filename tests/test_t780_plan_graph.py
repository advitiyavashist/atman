"""T-780: plan + graph + follow-up as the onboarding loop (throwaway board).

tickets plan must create real --after edges. tickets graph must show statuses.
MASTER_TEMPLATE / master-howto / CoS onboarding teach that loop — not one
create per title, and not a second planner product.
"""
import json
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TOOL = ROOT / "tickets.py"
HOWTO = ROOT / "docs/onboarding/master-howto.md"
CLI = ROOT / "src/ticket_board/cli.py"


def clean_env(tmp_path, **overrides):
    e = {
        "PATH": str(tmp_path / "bin") + os.pathsep + "/usr/bin" + os.pathsep + "/bin",
        "HOME": str(tmp_path / "fake-home"),
        "TICKET_AGENT": "boss",
        "PYTEST_CURRENT_TEST": "tests/test_t780_plan_graph.py::simulated (call)",
    }
    (tmp_path / "fake-home").mkdir(exist_ok=True)
    (tmp_path / "bin").mkdir(exist_ok=True)
    e.update(overrides)
    return e


def run(cwd, *args, env=None, tmp_path=None, stdin=None):
    return subprocess.run(
        [sys.executable, str(TOOL), *args],
        capture_output=True, text=True, cwd=str(cwd),
        env=env or clean_env(tmp_path),
        input=stdin)


def git(cwd, *args):
    return subprocess.run(
        ["git", "-c", "user.email=t780@test", "-c", "user.name=t780", *args],
        cwd=str(cwd), capture_output=True, text=True, check=True)


def make_repo(path):
    path.mkdir(parents=True, exist_ok=True)
    git(path, "init", "-q", "-b", "main")
    (path / "README").write_text("t780\n")
    git(path, "add", "README")
    git(path, "commit", "-qm", "init")
    return path


PLAN = json.dumps([
    {"key": "api", "title": "Build REST API", "role": "backend", "deps": []},
    {"key": "ui", "title": "Build login UI", "role": "frontend", "deps": ["api"]},
])


def test_plan_creates_graph_with_real_deps_and_graph_shows_statuses(tmp_path):
    repo = make_repo(tmp_path / "repo")
    env = clean_env(tmp_path)
    r = run(repo, "init", env=env, tmp_path=tmp_path)
    assert r.returncode == 0, r.stderr
    r = run(repo, "plan", env=env, tmp_path=tmp_path, stdin=PLAN)
    assert r.returncode == 0, r.stderr + r.stdout
    assert "created T-001" in r.stdout
    assert "created T-002" in r.stdout

    t1 = json.loads((repo / ".tickets" / "T-001.json").read_text())
    t2 = json.loads((repo / ".tickets" / "T-002.json").read_text())
    assert t1["title"] == "Build REST API"
    assert t1.get("deps") in ([], None) or t1.get("deps") == []
    assert t2["deps"] == ["T-001"]

    g = run(repo, "graph", env=env, tmp_path=tmp_path)
    assert g.returncode == 0, g.stderr
    assert "TO DO" in g.stdout or "[ ]" in g.stdout
    assert "T-001" in g.stdout and "T-002" in g.stdout
    assert "Build REST API" in g.stdout
    assert "Build login UI" in g.stdout
    assert "waiting on T-001" in g.stdout
    assert g.stdout.find("T-001") < g.stdout.find("T-002")

    m = run(repo, "map", env=env, tmp_path=tmp_path)
    assert m.returncode == 0, m.stderr
    assert "T-001" in m.stdout and "T-002" in m.stdout
    assert "after T-001" in m.stdout or "waits T-001" in m.stdout


def test_mid_run_dep_rewires_graph(tmp_path):
    repo = make_repo(tmp_path / "repo")
    env = clean_env(tmp_path)
    assert run(repo, "init", env=env, tmp_path=tmp_path).returncode == 0
    r = run(repo, "plan", env=env, tmp_path=tmp_path, stdin=PLAN)
    assert r.returncode == 0, r.stderr
    r = run(repo, "create", "Add rate limiting", "--deps", "T-002", env=env, tmp_path=tmp_path)
    assert r.returncode == 0, r.stderr
    t3 = json.loads((repo / ".tickets" / "T-003.json").read_text())
    assert t3["deps"] == ["T-002"]
    r = run(repo, "create", "DB migration", "--blocks", "T-001", env=env, tmp_path=tmp_path)
    assert r.returncode == 0, r.stderr + r.stdout
    t1 = json.loads((repo / ".tickets" / "T-001.json").read_text())
    t4 = json.loads((repo / ".tickets" / "T-004.json").read_text())
    assert t4["title"] == "DB migration"
    assert t1["deps"] == ["T-004"]
    r = run(repo, "dep", "T-003", "--after", "T-001", env=env, tmp_path=tmp_path)
    assert r.returncode == 0, r.stderr
    t3 = json.loads((repo / ".tickets" / "T-003.json").read_text())
    assert t3["deps"] == ["T-002", "T-001"]
    g = run(repo, "graph", env=env, tmp_path=tmp_path)
    assert "T-003" in g.stdout and "T-004" in g.stdout
    assert "waiting on T-002" in g.stdout


def test_docs_teach_plan_graph_followup_not_create_per_title():
    src = TOOL.read_text()
    howto = HOWTO.read_text()
    cli = CLI.read_text()
    marker = "MASTER_TEMPLATE = "
    i = src.find(marker)
    assert i != -1
    template = src[i:i + 8000]
    assert "tickets plan" in template
    assert "tickets graph" in template
    assert "one tickets create per task" not in template
    assert "COS ONBOARDING" in template
    assert "You are onboarding as chief of staff" in template
    assert "tickets reopen" in template
    assert "tickets drive" in template
    assert "tickets update" in template
    assert "tickets here" in template
    assert "create --blocks" in template or "tickets create --blocks" in template

    assert "tickets plan" in howto
    assert "one tickets create per task" not in howto
    assert "tickets graph" in howto
    assert "90m" in howto or "90 m" in howto
    assert "tickets drive" in howto
    assert "chief of staff" in howto.lower() or "CoS" in howto

    assert "tickets plan" in cli
    assert "COS ONBOARDING" in cli
    assert "one tickets create per task" not in cli


def test_init_master_template_has_plan_loop(tmp_path):
    repo = make_repo(tmp_path / "repo")
    env = clean_env(tmp_path)
    r = run(repo, "init", env=env, tmp_path=tmp_path)
    assert r.returncode == 0, r.stderr
    body = (repo / ".tickets" / "MASTER.md").read_text()
    assert "tickets plan" in body
    assert "tickets graph" in body
    assert "COS ONBOARDING" in body
    assert "one tickets create per task" not in body


def test_connect_mentions_plan_graph(tmp_path):
    repo = make_repo(tmp_path / "repo")
    env = clean_env(tmp_path)
    r = run(repo, "init", env=env, tmp_path=tmp_path)
    assert r.returncode == 0, r.stderr
    c = run(repo, "connect", env=env, tmp_path=tmp_path)
    assert c.returncode == 0, c.stderr
    assert "tickets plan" in c.stdout
    assert "tickets graph" in c.stdout
    assert c.stdout.find("**You are onboarding.**") < c.stdout.find("tickets plan")
