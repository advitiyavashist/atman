"""T-790: any-CEO executable onboarding path (throwaway board only).

python3 tickets.py — never PATH tickets. Cursor harness only. Real
tickets plan deps. No living Steer TICKETS_DIR. No tickets clear.
"""
import json
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TOOL = ROOT / "tickets.py"
RUNBOOK = ROOT / "docs/onboarding/ceo-mac-runbook.md"
HOWTO = ROOT / "docs/onboarding/master-howto.md"
ONBOARD = ROOT / "docs/onboarding/README.md"
README = ROOT / "README.md"

PLAN = json.dumps([
    {"key": "api", "title": "Build REST API", "role": "backend", "deps": []},
    {"key": "ui", "title": "Build login UI", "role": "frontend", "deps": ["api"]},
])


def clean_env(tmp_path, **overrides):
    e = {
        "PATH": str(tmp_path / "bin") + os.pathsep + "/usr/bin" + os.pathsep + "/bin",
        "HOME": str(tmp_path / "fake-home"),
        "TICKET_AGENT": "atman-ceo",
        "PYTEST_CURRENT_TEST": "tests/test_t790_ceo_onboarding.py::simulated (call)",
    }
    e.pop("TICKETS_DIR", None)
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
        ["git", "-c", "user.email=t790@test", "-c", "user.name=t790", *args],
        cwd=str(cwd), capture_output=True, text=True, check=True)


def make_repo(path):
    path.mkdir(parents=True, exist_ok=True)
    git(path, "init", "-q", "-b", "main")
    (path / "README").write_text("t790\n")
    git(path, "add", "README")
    git(path, "commit", "-qm", "init")
    return path


def test_runbook_pins_this_mac_and_forbids_shim():
    body = RUNBOOK.read_text(encoding="utf-8")
    assert "/Users/kavana/Downloads/atman/.worktrees/cursor-community-t790/tickets.py" in body
    assert "/Users/kavana/Downloads/steer/.tickets" in body
    assert "python3" in body
    assert "stale shim" in body
    assert "~/.local/bin/tickets" in body
    assert "tickets plan" in body
    assert "one `tickets create` per title" in body
    assert "--roles master" in body
    assert "--wake-mode continuous" in body
    assert "--harness cursor" in body
    assert "hooks cursor" in body
    assert "master take" in body
    assert "objective --set" in body
    assert "master cos" in body
    assert "msg --to" in body
    assert "tickets init" in body and "tickets clear" in body
    assert "HOLD T-773" in body
    assert "steer main" in body
    assert "Minions" not in body and "Inspect" not in body
    assert ONBOARD.read_text(encoding="utf-8").count("ceo-mac-runbook.md") >= 1
    assert "docs/onboarding/ceo-mac-runbook.md" in README.read_text(encoding="utf-8")
    howto = HOWTO.read_text(encoding="utf-8")
    assert "ceo-mac-runbook.md" in howto


def test_objective_set_flag(tmp_path):
    repo = make_repo(tmp_path / "repo")
    env = clean_env(tmp_path)
    assert run(repo, "init", env=env, tmp_path=tmp_path).returncode == 0
    r = run(repo, "join", "atman-ceo", "--roles", "master", env=env, tmp_path=tmp_path)
    assert r.returncode == 0, r.stderr
    r = run(repo, "objective", "--set", "Dry CEO onboarding path",
            "--exit", "graph has real deps", env=env, tmp_path=tmp_path)
    assert r.returncode == 0, r.stderr + r.stdout
    assert "objective set" in r.stdout
    obj = json.loads((repo / ".tickets" / "objective.json").read_text())
    assert obj["text"] == "Dry CEO onboarding path"


def test_throwaway_ceo_path_python3_not_path_tickets(tmp_path):
    repo = make_repo(tmp_path / "repo")
    env = clean_env(tmp_path)
    # Poison PATH with a fake `tickets` so using it would fail the proof.
    shim = tmp_path / "bin" / "tickets"
    shim.write_text("#!/bin/sh\necho STALE_SHIM >&2\nexit 99\n")
    shim.chmod(0o755)

    def t(*args, stdin=None):
        return run(repo, *args, env=env, tmp_path=tmp_path, stdin=stdin)

    assert t("init").returncode == 0
    r = t("join", "atman-ceo", "--roles", "master", "--persistent",
          "--wake-mode", "continuous", "--harness", "cursor")
    assert r.returncode == 0, r.stderr + r.stdout
    assert "harness=cursor" in r.stdout
    assert "wake=continuous" in r.stdout
    wf = json.loads((repo / ".tickets" / "workforce.json").read_text())
    assert wf["atman-ceo"]["harness"] == "cursor"

    r = t("hooks", "cursor", "--agent", "atman-ceo", "--worktree", str(repo))
    assert r.returncode == 0, r.stderr + r.stdout
    assert (repo / ".cursor" / "hooks.json").is_file()

    r = t("master", "take")
    assert r.returncode == 0, r.stderr + r.stdout
    assert "atman-ceo" in r.stdout.lower() or "master" in r.stdout.lower()

    r = t("msg", "--to", "everyone",
          "atman-ceo is onboarding. Integrating: cursor. Objective and tasks next. @everyone")
    assert r.returncode == 0, r.stderr + r.stdout
    assert "STALE_SHIM" not in r.stderr

    r = t("objective", "--set", "Dry CEO onboarding path",
          "--exit", "graph has real deps; CoS messaged")
    assert r.returncode == 0, r.stderr + r.stdout

    r = t("plan", stdin=PLAN)
    assert r.returncode == 0, r.stderr + r.stdout
    t2 = json.loads((repo / ".tickets" / "T-002.json").read_text())
    assert t2["deps"] == ["T-001"]

    g = t("graph")
    assert g.returncode == 0, g.stderr
    assert "T-001" in g.stdout and "T-002" in g.stdout

    r = t("join", "cursor-worker", "--roles", "backend", "--harness", "cursor",
          "--wake-mode", "task-only")
    assert r.returncode == 0, r.stderr + r.stdout
    try:
        r = t("spawn", "cursor-worker", "--harness", "cursor", "--max-runs", "1",
              "--exec", "/usr/bin/true {prompt_file} {cwd} {agent}")
        assert r.returncode == 0, r.stderr + r.stdout
        assert "STALE_SHIM" not in r.stderr
    finally:
        t("spawn", "cursor-worker", "--stop")

    r = t("join", "cos-cursor", "--roles", "docs", "--harness", "cursor",
          "--wake-mode", "continuous")
    assert r.returncode == 0, r.stderr
    r = t("master", "cos", "cos-cursor")
    assert r.returncode == 0, r.stderr + r.stdout
    r = t("msg", "--to", "cos-cursor", "CoS: staff cursor only.")
    assert r.returncode == 0, r.stderr + r.stdout
    mail = (repo / ".tickets" / "messages.jsonl").read_text()
    assert "CoS: staff cursor only." in mail
    assert "STALE_SHIM" not in mail
    # Proof never touched the living board.
    assert env.get("TICKETS_DIR") in (None, "")
    assert str(repo / ".tickets") != "/Users/kavana/Downloads/steer/.tickets"
