"""T-793: harness available auto-checks usage remaining/reset (throwaway board).

Every catalog row (cursor, agy, claude, codex, devin, gemini) must be
usage-checked. Missing remaining or reset is FAIL. Probes are status/about
only — never -p/--print/exec or a model prompt.
"""
import importlib.util
import os
import stat
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TOOL = ROOT / "tickets.py"

CATALOG_IDS = ("cursor", "agy", "claude", "codex", "devin", "gemini")
SPAWN_TOKENS = ("-p", "--print", "--prompt", "--prompt-interactive", "-i", "exec", "--yolo")


def load_tickets():
    spec = importlib.util.spec_from_file_location("tickets_t793", TOOL)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def clean_env(tmp_path, **overrides):
    e = {
        "PATH": str(tmp_path / "bin") + os.pathsep + "/usr/bin" + os.pathsep + "/bin",
        "HOME": str(tmp_path / "fake-home"),
        "PYTEST_CURRENT_TEST": "tests/test_t793_harness_usage.py::simulated (call)",
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
        ["git", "-c", "user.email=t793@test", "-c", "user.name=t793", *args],
        cwd=str(cwd), capture_output=True, text=True, check=True)


def make_repo(path):
    path.mkdir(parents=True, exist_ok=True)
    git(path, "init", "-q", "-b", "main")
    (path / "README").write_text("t793\n")
    git(path, "add", "README")
    git(path, "commit", "-qm", "init")
    return path


def write_exec(path, body):
    path.write_text(body)
    path.chmod(path.stat().st_mode | stat.S_IEXEC)
    return path


USAGE_OK_SH = """#!/bin/sh
echo '{"remaining": "80%", "reset": "2026-09-13T08:00"}'
"""

USAGE_REMAINING_ONLY_SH = """#!/bin/sh
echo '{"remaining": "80%"}'
"""

USAGE_RESET_ONLY_SH = """#!/bin/sh
echo '{"reset": "2026-09-13T08:00"}'
"""


def install_catalog_bins(bin_dir, body):
    names = ("agent", "agy", "claude", "codex", "devin", "gemini")
    for name in names:
        write_exec(bin_dir / name, body)


def test_usage_probes_are_not_spawn():
    mod = load_tickets()
    assert not any(mod.usage_probe_is_spawn(s.get("usage_args") or ()) for s in mod.INTEGRATION_CATALOG)
    for spec in mod.INTEGRATION_CATALOG:
        args = [str(x).lower() for x in (spec.get("usage_args") or ())]
        for tok in SPAWN_TOKENS:
            assert tok not in args, (spec["id"], args)
    for tok in SPAWN_TOKENS:
        assert mod.usage_probe_is_spawn([tok])


def test_parse_usage_remaining_reset_does_not_invent():
    mod = load_tickets()
    assert mod.parse_usage_remaining_reset("") == (None, None)
    assert mod.parse_usage_remaining_reset('{"loggedIn": true}') == (None, None)
    assert mod.parse_usage_remaining_reset(
        '{"remaining": "80%", "reset": "2026-09-13T08:00"}') == ("80%", "2026-09-13T08:00")
    assert mod.parse_usage_remaining_reset('{"remaining": "80%"}') == ("80%", None)
    remaining, reset = mod.parse_usage_remaining_reset('{"reset": "tomorrow"}')
    assert remaining is None and reset == "tomorrow"
    remaining, reset = mod.parse_usage_remaining_reset("remaining: 12%\nresets at Sep 13 08:00")
    assert remaining == "12%" and "Sep 13" in reset
    # 0 is reported, not missing.
    remaining, reset = mod.parse_usage_remaining_reset('{"remaining": 0, "reset_at": "never"}')
    assert remaining == "0" and reset == "never"


def test_harness_available_fail_when_remaining_reset_missing(tmp_path):
    repo = make_repo(tmp_path / "repo")
    env = clean_env(tmp_path)
    r = run(repo, "init", env=env, tmp_path=tmp_path)
    assert r.returncode == 0, r.stderr
    env["PATH"] = str(tmp_path / "empty-bin")
    (tmp_path / "empty-bin").mkdir()
    r = run(repo, "harness", "available", env=env, tmp_path=tmp_path)
    assert r.returncode == 0, r.stderr
    for cid in CATALOG_IDS:
        assert cid in r.stdout, cid
        assert "usage FAIL" in r.stdout
    assert "remaining=(missing)" in r.stdout
    assert "reset=(missing)" in r.stdout
    assert "missing remaining/reset is a fail row" in r.stdout.lower()
    assert "do not spawn" in r.stdout.lower()
    agents = list((repo / ".tickets" / "agents").glob("*")) if (repo / ".tickets" / "agents").exists() else []
    assert agents == []


def test_harness_usage_fail_table_for_every_catalog_row(tmp_path):
    repo = make_repo(tmp_path / "repo")
    env = clean_env(tmp_path)
    assert run(repo, "init", env=env, tmp_path=tmp_path).returncode == 0
    env["PATH"] = str(tmp_path / "empty-bin")
    (tmp_path / "empty-bin").mkdir()
    r = run(repo, "harness", "usage", env=env, tmp_path=tmp_path)
    assert r.returncode == 0, r.stderr
    for cid in CATALOG_IDS:
        assert cid in r.stdout, cid
    assert r.stdout.count("FAIL") >= 6
    assert "(missing)" in r.stdout
    assert "do not spawn" in r.stdout.lower()


def test_harness_available_ok_when_remaining_and_reset_present(tmp_path):
    repo = make_repo(tmp_path / "repo")
    env = clean_env(tmp_path)
    bin_dir = tmp_path / "bin"
    install_catalog_bins(bin_dir, USAGE_OK_SH)
    assert run(repo, "init", env=env, tmp_path=tmp_path).returncode == 0
    r = run(repo, "harness", "available", env=env, tmp_path=tmp_path)
    assert r.returncode == 0, r.stderr + r.stdout
    for cid in CATALOG_IDS:
        assert cid in r.stdout, cid
    assert "usage ok" in r.stdout
    assert "remaining=80%" in r.stdout
    assert "reset=2026-09-13T08:00" in r.stdout
    assert "usage FAIL" not in r.stdout
    agents = list((repo / ".tickets" / "agents").glob("*")) if (repo / ".tickets" / "agents").exists() else []
    assert agents == []


def test_harness_available_fail_when_only_remaining(tmp_path):
    repo = make_repo(tmp_path / "repo")
    env = clean_env(tmp_path)
    install_catalog_bins(tmp_path / "bin", USAGE_REMAINING_ONLY_SH)
    assert run(repo, "init", env=env, tmp_path=tmp_path).returncode == 0
    r = run(repo, "harness", "available", env=env, tmp_path=tmp_path)
    assert r.returncode == 0, r.stderr
    assert "usage FAIL" in r.stdout
    assert "remaining=80%" in r.stdout
    assert "reset=(missing)" in r.stdout
    assert "usage ok" not in r.stdout


def test_harness_available_fail_when_only_reset(tmp_path):
    repo = make_repo(tmp_path / "repo")
    env = clean_env(tmp_path)
    install_catalog_bins(tmp_path / "bin", USAGE_RESET_ONLY_SH)
    assert run(repo, "init", env=env, tmp_path=tmp_path).returncode == 0
    r = run(repo, "harness", "available", env=env, tmp_path=tmp_path)
    assert r.returncode == 0, r.stderr
    assert "usage FAIL" in r.stdout
    assert "remaining=(missing)" in r.stdout
    assert "reset=2026-09-13T08:00" in r.stdout
    assert "usage ok" not in r.stdout


def test_harness_usage_ok_table(tmp_path):
    repo = make_repo(tmp_path / "repo")
    env = clean_env(tmp_path)
    install_catalog_bins(tmp_path / "bin", USAGE_OK_SH)
    assert run(repo, "init", env=env, tmp_path=tmp_path).returncode == 0
    r = run(repo, "harness", "usage", env=env, tmp_path=tmp_path)
    assert r.returncode == 0, r.stderr
    for cid in CATALOG_IDS:
        assert cid in r.stdout, cid
    assert r.stdout.count("ok") >= 6
    assert "FAIL" not in r.stdout.split("USAGE", 1)[-1].split("Missing", 1)[0]
