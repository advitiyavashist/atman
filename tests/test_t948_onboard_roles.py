"""T-948: no hardcoded Cursor CoS; discover then ask/suggest (throwaway board)."""
import json
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TOOL = ROOT / "tickets.py"
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from ticket_board import onboard_roles as roles


def clean_env(tmp_path, **overrides):
    e = {
        "PATH": str(tmp_path / "bin") + os.pathsep + "/usr/bin" + os.pathsep + "/bin",
        "HOME": str(tmp_path / "fake-home"),
        "TICKET_AGENT": "boss",
        "PYTEST_CURRENT_TEST": "tests/test_t948_onboard_roles.py::simulated (call)",
    }
    (tmp_path / "fake-home").mkdir(exist_ok=True)
    (tmp_path / "bin").mkdir(exist_ok=True)
    e.update(overrides)
    return e


def run(cwd, *args, env=None, tmp_path=None):
    return subprocess.run(
        [sys.executable, str(TOOL), *args],
        capture_output=True, text=True, cwd=str(cwd),
        env=env or clean_env(tmp_path))


def git(cwd, *args):
    return subprocess.run(
        ["git", "-c", "user.email=t948@test", "-c", "user.name=t948", *args],
        cwd=str(cwd), capture_output=True, text=True, check=True)


def make_repo(path):
    path.mkdir(parents=True, exist_ok=True)
    git(path, "init", "-q", "-b", "main")
    (path / "README").write_text("t948\n")
    git(path, "add", "README")
    git(path, "commit", "-qm", "init")
    return path


BANNED = (
    "CoS is cursor",
    "CoS (`cursor`)",
    "CoS (cursor)",
    "msg --to cursor",
    "Cursor-only spawns",
)


def test_usage_unknown_never_zero_or_fail():
    rem, reset = roles.usage_cell({"remaining": None, "reset": ""})
    assert rem == "unknown" and reset == "unknown"
    rem, reset = roles.usage_cell({"remaining": "0", "reset": "0"})
    assert rem == "unknown"


def test_suggestion_not_applied_without_flags(tmp_path):
    repo = make_repo(tmp_path / "repo")
    env = clean_env(tmp_path)
    assert run(repo, "init", env=env, tmp_path=tmp_path).returncode == 0
    r = run(repo, "connect", env=env, tmp_path=tmp_path)
    assert r.returncode == 0, r.stderr + r.stdout
    out = r.stdout
    for banned in BANNED:
        assert banned not in out, banned
    assert "SUGGESTED ASSIGNMENT" in out
    assert "unknown" in out
    assert "CoS unset until chosen" in out
    assert "CLI: atm" in out or "tickets is an alias" in out
    master = repo / ".tickets" / "master.json"
    if master.is_file():
        rec = json.loads(master.read_text())
        assert not rec.get("cos")


def test_connect_requires_explicit_flags_to_set_roles(tmp_path):
    repo = make_repo(tmp_path / "repo")
    env = clean_env(tmp_path)
    assert run(repo, "init", env=env, tmp_path=tmp_path).returncode == 0
    r = run(repo, "connect", "--master", "ada", "--cos", "bev", env=env, tmp_path=tmp_path)
    assert r.returncode == 0, r.stderr + r.stdout
    rec = json.loads((repo / ".tickets" / "master.json").read_text())
    assert rec["owner"] == "ada"
    assert rec["cos"] == "bev"
    r2 = run(repo, "connect", "--ceo", env=env, tmp_path=tmp_path)
    assert "CoS is bev" in r2.stdout
    assert "atm msg --to bev" in r2.stdout
    assert "tickets msg --to cursor" not in r2.stdout
    assert "atm msg --to cursor" not in r2.stdout


def test_join_ceo_mail_uses_resolved_holder(tmp_path):
    repo = make_repo(tmp_path / "repo")
    env = clean_env(tmp_path)
    assert run(repo, "init", env=env, tmp_path=tmp_path).returncode == 0
    run(repo, "connect", "--master", "ada", "--cos", "bev", env=env, tmp_path=tmp_path)
    r = run(repo, "join", "atman-ceo", "--roles", "master", env=env, tmp_path=tmp_path)
    assert r.returncode == 0, r.stderr + r.stdout
    assert "atm msg --to bev" in r.stdout
    assert "tickets msg --to cursor" not in r.stdout
    assert "atm msg --to cursor" not in r.stdout


def test_login_probe_yes_no_unknown():
    assert roles.classify_login_output(0, "Logged in as fixture@example.test") == "yes"
    assert roles.classify_login_output(1, "Error: login required") == "no"
    assert roles.classify_login_output(1, "connection refused") == "unknown"
    assert roles.classify_login_output(124, "") == "unknown"

    def runner_ok(argv, timeout):
        return 0, "Logged in as fixture@example.test"

    yes = roles.probe_login(
        {"id": "cursor", "on_disk": True, "path": "/bin/agent"},
        runner=runner_ok)
    assert yes == "yes"
    assert roles.probe_login({"id": "cursor", "on_disk": False, "path": ""}) == "no"
    assert roles.probe_login(
        {"id": "cursor", "on_disk": True, "path": "/bin/agent"},
        runner=lambda argv, timeout: (1, "status failed")) == "unknown"


def test_suggestion_skips_codex_for_leadership_and_gives_reasons():
    rows = [
        {"id": "codex", "on_disk": True, "logged_in": "yes", "remaining": None},
        {"id": "cursor", "on_disk": True, "logged_in": "unknown", "remaining": None},
        {"id": "claude", "on_disk": True, "logged_in": "yes", "remaining": None},
    ]
    sug = roles.suggest_assignment(rows)
    assert sug["master"] == "claude"
    assert sug["cos"] == "cursor"
    assert "codex" in sug["workers"]
    assert "T-875" in sug["reasons"]["workers"]
    assert "reliable interactive" in sug["reasons"]["master"]
    text = roles.format_suggestion(sug)
    assert "atm master take" in text
    assert "CLI: atm" in text
    assert "tickets master take" not in text


def test_master_template_has_no_cursor_cos_role():
    text = (ROOT / "tickets.py").read_text()
    start = text.index("MASTER_TEMPLATE = ")
    chunk = text[start:start + 8000]
    for banned in BANNED:
        assert banned not in chunk, banned
    assert "never auto-assign" in chunk.lower() or "Never auto-assign" in chunk
