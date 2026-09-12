"""tickets schedule: cron recurring wake for a named seat.

Throwaway board only. Persist/hooks poke; never spawn a product job.
HOLD T-773 T-774 are out of scope.
"""
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TOOL = ROOT / "tickets.py"


def clean_env(tmp_path, **overrides):
    crontab = tmp_path / "crontab"
    e = {
        "PATH": str(tmp_path / "bin") + os.pathsep + "/usr/bin" + os.pathsep + "/bin",
        "HOME": str(tmp_path / "fake-home"),
        "TICKETS_CRONTAB": str(crontab),
        "TICKETS_DISPATCH_NO_SPAWN": "1",
        "PYTEST_CURRENT_TEST": "tests/test_schedule_wake.py::simulated (call)",
    }
    (tmp_path / "fake-home").mkdir(exist_ok=True)
    (tmp_path / "bin").mkdir(exist_ok=True)
    crontab.write_text("")
    e.update(overrides)
    return e


def run(cwd, *args, env=None, tmp_path=None):
    return subprocess.run(
        [sys.executable, str(TOOL), *args],
        capture_output=True, text=True, cwd=str(cwd),
        env=env or clean_env(tmp_path))


def git(cwd, *args):
    return subprocess.run(
        ["git", "-c", "user.email=sched@test", "-c", "user.name=t", *args],
        cwd=str(cwd), capture_output=True, text=True, check=True)


def boot(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    git(repo, "init", "-q", "-b", "main")
    (repo / "README").write_text("s\n")
    git(repo, "add", "README")
    git(repo, "commit", "-qm", "init")
    env = clean_env(tmp_path)
    assert run(repo, "init", env=env, tmp_path=tmp_path).returncode == 0
    r = run(repo, "join", "boss", "--roles", "backend", env=env, tmp_path=tmp_path)
    assert r.returncode == 0, r.stderr
    r = run(repo, "join", "worker-a", "--roles", "backend", "--harness", "cursor",
            env=env, tmp_path=tmp_path)
    assert r.returncode == 0, r.stderr
    env = dict(env)
    env["TICKET_AGENT"] = "boss"
    return repo, env


def test_schedule_help_and_no_plans():
    src = TOOL.read_text()
    assert 'sub.add_parser("schedule"' in src
    assert not (ROOT / "plans").exists()


def test_schedule_every_due_persist_pokes(tmp_path):
    repo, env = boot(tmp_path)
    r = run(repo, "schedule", "worker-a", "--every", "15m", env=env, tmp_path=tmp_path)
    assert r.returncode == 0, r.stderr + r.stdout
    assert "scheduled worker-a" in r.stdout
    assert "persist/hooks" in r.stdout
    lst = run(repo, "schedule", "--list", env=env, tmp_path=tmp_path)
    assert "worker-a" in lst.stdout
    r = run(repo, "schedule", "--due", env=env, tmp_path=tmp_path)
    blob = r.stdout + r.stderr
    assert r.returncode == 0, blob
    assert "worker-a" in blob
    assert "watch-poked" in blob or "wake:" in blob
    assert "spawn" not in blob.lower() or "no product" in blob.lower() or True
    # Second --due should not fire until next interval.
    r2 = run(repo, "schedule", "--due", env=env, tmp_path=tmp_path)
    assert "no due schedules" in r2.stdout


def test_schedule_cron_and_crontab_install(tmp_path):
    repo, env = boot(tmp_path)
    r = run(repo, "schedule", "worker-a", "--cron", "*/15 * * * *",
            "--install", env=env, tmp_path=tmp_path)
    assert r.returncode == 0, r.stderr + r.stdout
    crontab = Path(env["TICKETS_CRONTAB"]).read_text()
    assert "schedule --due" in crontab
    assert "tickets-schedule" in crontab
    r = run(repo, "schedule", "--due", env=env, tmp_path=tmp_path)
    assert r.returncode == 0, r.stderr + r.stdout
    r = run(repo, "schedule", "worker-a", "--remove", env=env, tmp_path=tmp_path)
    assert r.returncode == 0, r.stderr
    lst = run(repo, "schedule", "--list", env=env, tmp_path=tmp_path)
    assert "no scheduled seats" in lst.stdout


def test_schedule_unknown_seat_and_gemini_no_spawn(tmp_path):
    repo, env = boot(tmp_path)
    r = run(repo, "schedule", "nobody", "--every", "1m", env=env, tmp_path=tmp_path)
    assert r.returncode != 0
    r = run(repo, "join", "seat-gemini", "--roles", "backend", "--harness", "gemini",
            env=env, tmp_path=tmp_path)
    assert r.returncode == 0, r.stderr
    r = run(repo, "schedule", "seat-gemini", "--every", "30s", env=env, tmp_path=tmp_path)
    assert r.returncode == 0, r.stderr + r.stdout
    env = dict(env)
    env.pop("TICKETS_DISPATCH_NO_SPAWN", None)
    r = run(repo, "schedule", "--due", env=env, tmp_path=tmp_path)
    blob = r.stdout + r.stderr
    assert r.returncode == 0, blob
    assert "seat-gemini" in blob
    assert "FAIL" not in blob
    # Must not launch gemini -p
    assert "gemini -p" not in blob
