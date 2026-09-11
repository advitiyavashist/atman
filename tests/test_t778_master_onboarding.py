"""T-778: productize master onboarding (throwaway board only).

Startup must be the first thing `tickets connect`, `tickets boot`, and
`tickets master` print — not after MASTER.md history. `tickets harness
available` prints every catalog row, including missing binaries, and never
spawns. Stale ~/.local/bin/codex is retargeted to the newest openai.chatgpt-*
extension binary under the test HOME, never the operator's.
"""
import os
import stat
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TOOL = ROOT / "tickets.py"
HOWTO = ROOT / "docs/onboarding/master-howto.md"

CATALOG_IDS = ("cursor", "agy", "claude", "codex", "devin", "gemini")


def clean_env(tmp_path, **overrides):
    e = {
        "PATH": str(tmp_path / "bin") + os.pathsep + "/usr/bin" + os.pathsep + "/bin",
        "HOME": str(tmp_path / "fake-home"),
        "PYTEST_CURRENT_TEST": "tests/test_t778_master_onboarding.py::simulated (call)",
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
        ["git", "-c", "user.email=t778@test", "-c", "user.name=t778", *args],
        cwd=str(cwd), capture_output=True, text=True, check=True)


def make_repo(path):
    path.mkdir(parents=True, exist_ok=True)
    git(path, "init", "-q", "-b", "main")
    (path / "README").write_text("t778\n")
    git(path, "add", "README")
    git(path, "commit", "-qm", "init")
    return path


def first_nonempty(text):
    for ln in (text or "").splitlines():
        if ln.strip():
            return ln.strip()
    return ""


def test_master_template_and_howto_start_with_onboarding():
    src = (ROOT / "tickets.py").read_text()
    marker = "MASTER_TEMPLATE = "
    i = src.find(marker)
    assert i != -1
    # The assigned template begins with the onboarding sentence (possibly via
    # concatenation after a short prefix string).
    chunk = src[i:i + 400]
    assert "You are onboarding" in chunk
    howto = HOWTO.read_text()
    # First markdown body line after the title.
    lines = [ln.strip() for ln in howto.splitlines() if ln.strip()]
    assert lines[0] == "# Master how-to"
    assert "You are onboarding" in lines[1]


def test_connect_boot_master_print_startup_first(tmp_path):
    repo = make_repo(tmp_path / "repo")
    env = clean_env(tmp_path)
    r = run(repo, "init", env=env, tmp_path=tmp_path)
    assert r.returncode == 0, r.stderr
    # History after the startup message must not bury it.
    master = repo / ".tickets" / "MASTER.md"
    master.write_text(master.read_text() + ("\n- fake history line\n" * 80))

    c = run(repo, "connect", env=env, tmp_path=tmp_path)
    assert c.returncode == 0, c.stderr
    assert first_nonempty(c.stdout).startswith("**You are onboarding.**")
    assert "harness available" in c.stdout
    assert c.stdout.find("**You are onboarding.**") < c.stdout.find("Connecting an agent")

    b = run(repo, "boot", "--agent", "boss", env=env, tmp_path=tmp_path)
    assert b.returncode == 0, b.stderr
    assert first_nonempty(b.stdout).startswith("**You are onboarding.**")
    assert b.stdout.find("**You are onboarding.**") < b.stdout.find("BOOT boss")

    m = run(repo, "master", env=env, tmp_path=tmp_path)
    assert m.returncode == 0, m.stderr
    assert first_nonempty(m.stdout).startswith("**You are onboarding.**")
    assert m.stdout.find("**You are onboarding.**") < m.stdout.find("MASTER BRIEFING")
    assert m.stdout.find("**You are onboarding.**") < m.stdout.find("fake history line")


def test_master_init_writes_onboarding_template(tmp_path):
    repo = make_repo(tmp_path / "repo")
    env = clean_env(tmp_path)
    r = run(repo, "init", env=env, tmp_path=tmp_path)
    assert r.returncode == 0, r.stderr
    body = (repo / ".tickets" / "MASTER.md").read_text()
    assert body.lstrip().startswith("**You are onboarding.**")
    assert "tickets harness available" in body
    assert "Onboarding name:" in body


def test_harness_available_prints_every_catalog_row_including_missing(tmp_path):
    repo = make_repo(tmp_path / "repo")
    env = clean_env(tmp_path)
    r = run(repo, "init", env=env, tmp_path=tmp_path)
    assert r.returncode == 0, r.stderr
    # Empty PATH bin dir: none of the catalog CLIs exist.
    env["PATH"] = str(tmp_path / "empty-bin")
    (tmp_path / "empty-bin").mkdir()
    r = run(repo, "harness", "available", env=env, tmp_path=tmp_path)
    assert r.returncode == 0, r.stderr
    for cid in CATALOG_IDS:
        assert cid in r.stdout, cid
    assert r.stdout.count("\n         ") >= 12  # policy + if-yes per row
    assert "do not spawn" in r.stdout.lower()
    assert "Which of these do you want to use?" in r.stdout
    assert "list only" in r.stdout
    # Documentation may mention spawn; the command itself must not create seats.
    agents = list((repo / ".tickets" / "agents").glob("*")) if (repo / ".tickets" / "agents").exists() else []
    assert agents == []


def test_harness_available_retargets_stale_local_codex(tmp_path):
    repo = make_repo(tmp_path / "repo")
    home = tmp_path / "fake-home"
    ext = home / ".vscode/extensions/openai.chatgpt-99.0.0/bin/darwin-arm64"
    ext.mkdir(parents=True)
    newest = ext / "codex"
    newest.write_text("#!/bin/sh\necho newest-codex\n")
    newest.chmod(newest.stat().st_mode | stat.S_IEXEC)
    local_bin = home / ".local/bin"
    local_bin.mkdir(parents=True)
    stale = local_bin / "codex"
    stale.symlink_to("/definitely/missing/codex-old")
    assert not stale.exists()  # dangling

    env = clean_env(tmp_path)
    env["PATH"] = str(local_bin) + os.pathsep + str(tmp_path / "bin")
    r = run(repo, "init", env=env, tmp_path=tmp_path)
    assert r.returncode == 0, r.stderr
    r = run(repo, "harness", "available", env=env, tmp_path=tmp_path)
    assert r.returncode == 0, r.stderr
    assert "retargeted" in r.stdout
    assert stale.is_symlink()
    assert os.path.realpath(stale) == os.path.realpath(newest)
    assert "codex" in r.stdout
    assert "yes" in r.stdout


def test_harness_available_does_not_overwrite_nonsymlink_codex(tmp_path):
    repo = make_repo(tmp_path / "repo")
    home = tmp_path / "fake-home"
    ext = home / ".vscode/extensions/openai.chatgpt-1.0.0/bin/x"
    ext.mkdir(parents=True)
    newest = ext / "codex"
    newest.write_text("#!/bin/sh\necho ext\n")
    newest.chmod(newest.stat().st_mode | stat.S_IEXEC)
    local_bin = home / ".local/bin"
    local_bin.mkdir(parents=True)
    regular = local_bin / "codex"
    regular.write_text("#!/bin/sh\necho regular\n")
    regular.chmod(regular.stat().st_mode | stat.S_IEXEC)

    env = clean_env(tmp_path)
    r = run(repo, "init", env=env, tmp_path=tmp_path)
    assert r.returncode == 0
    r = run(repo, "harness", "available", env=env, tmp_path=tmp_path)
    assert r.returncode == 0, r.stderr
    assert "retargeted" not in r.stdout
    assert regular.read_text().startswith("#!/bin/sh\necho regular")
