"""T-471: a passing test's tmp_path git dir must be gone after the session.

Nested pytest --basetemp plus a read-only .git tree is the T-417 leftover
class: pytest's rmtree(ignore_errors=True) cannot remove it, and
sessionfinish skips when --basetemp is set. The tmp_path_reaper plugin
must still delete it. No full suite.
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

TESTS = Path(__file__).resolve().parent

NESTED_TEST = r'''
import os
import subprocess
from pathlib import Path

def test_makes_readonly_git(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.check_call(["git", "init", "-q", str(repo)])
    (repo / "f").write_text("x")
    env = dict(os.environ, GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t@t",
               GIT_COMMITTER_NAME="t", GIT_COMMITTER_EMAIL="t@t")
    subprocess.check_call(["git", "-C", str(repo), "add", "f"])
    subprocess.check_call(["git", "-C", str(repo), "commit", "-q", "-m", "x"], env=env)
    for p in repo.rglob("*"):
        mode = 0o444 if p.is_file() else 0o555
        try:
            os.chmod(p, mode)
        except OSError:
            pass
    os.chmod(repo, 0o555)
'''


def _nested_env():
    e = dict(os.environ)
    e.pop("TICKETS_DIR", None)
    e.pop("PYTEST_ADDOPTS", None)
    e["PYTHONPATH"] = str(TESTS) + os.pathsep + e.get("PYTHONPATH", "")
    return e


def _run_nested(root: Path, *, plugin: bool) -> subprocess.CompletedProcess:
    probe = root / "probe"
    probe.mkdir(parents=True)
    (probe / "test_git.py").write_text(NESTED_TEST)
    basetemp = root / "bt"
    cmd = [
        sys.executable, "-m", "pytest", "-q",
        "-p", "no:cacheprovider",
        "-o", "tmp_path_retention_policy=failed",
        "-o", "tmp_path_retention_count=1",
        "--basetemp", str(basetemp),
        str(probe / "test_git.py"),
    ]
    if plugin:
        cmd[4:4] = ["-p", "tmp_path_reaper"]
    return subprocess.run(cmd, cwd=str(probe), env=_nested_env(),
                          capture_output=True, text=True)


def _leftover_gits(basetemp: Path):
    if not basetemp.exists():
        return []
    return [p for p in basetemp.rglob(".git") if p.is_dir()]


def test_passing_tmp_path_git_dir_gone_after_session(tmp_path):
    control = tmp_path / "noplugin"
    r = _run_nested(control, plugin=False)
    assert r.returncode == 0, r.stdout + r.stderr
    assert _leftover_gits(control / "bt"), (
        "control: readonly git tmp_path must survive pytest ignore_errors; "
        + r.stdout + r.stderr
    )

    fixed = tmp_path / "plugin"
    r = _run_nested(fixed, plugin=True)
    assert r.returncode == 0, r.stdout + r.stderr
    leftover = _leftover_gits(fixed / "bt")
    assert leftover == [], leftover
    assert not (fixed / "bt").exists() or not any((fixed / "bt").iterdir())
