"""T-353 case-3 adversarial reproducers: harness check placeholder validation.

Ported from composer/t353@b40a03e for T-435. Cases 1/2/4/5 belong elsewhere.
"""

import json
import os
import stat
import subprocess
import sys
from pathlib import Path

import pytest

TOOL = Path(__file__).resolve().parents[1] / "tickets.py"


def run(board, *args, agent="", env=None, cwd=None):
    e = dict(
        os.environ,
        TICKETS_DIR=str(board),
        TICKET_AGENT=agent or "",
        HOME=str(board.parent.parent / "home"),
    )
    e.pop("TICKETS_STOP_HOOK", None)
    if env:
        e.update(env)
    return subprocess.run(
        [sys.executable, str(TOOL), *args],
        capture_output=True,
        text=True,
        env=e,
        cwd=str(cwd or board.parent),
    )


@pytest.fixture
def board(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    (tmp_path / "home").mkdir()
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    subprocess.run(
        ["git", "-C", str(repo), "commit", "-q", "--allow-empty", "-m", "init"],
        check=True,
        env=dict(
            os.environ,
            GIT_AUTHOR_NAME="t",
            GIT_AUTHOR_EMAIL="t@t",
            GIT_COMMITTER_NAME="t",
            GIT_COMMITTER_EMAIL="t@t",
        ),
    )
    b = repo / ".tickets"
    r = run(b, "create", "Write docs", "--role", "docs", cwd=repo)
    assert r.returncode == 0, r.stderr
    return b


def test_case3_harness_check_absent_binary(board):
    assert run(
        board, "join", "a", "--roles", "docs",
        "--harness", "custom:definitely-not-a-real-binary-t353 {prompt_file}",
    ).returncode == 0
    r = run(board, "harness", "check", "a")
    assert r.returncode != 0
    assert "HARNESS FAILED" in r.stdout


def test_case3_harness_check_non_executable(board, tmp_path):
    path = tmp_path / "notexec.sh"
    path.write_text("#!/bin/sh\necho OK\n")
    path.chmod(0o644)
    assert run(board, "join", "a", "--roles", "docs", "--harness", "custom", "--cmd", "%s {prompt_file}" % path).returncode == 0
    r = run(board, "harness", "check", "a")
    assert r.returncode != 0
    assert "HARNESS FAILED" in r.stdout


def test_case3_harness_check_exit_nonzero(board, tmp_path):
    script = tmp_path / "bad.sh"
    script.write_text("#!/bin/sh\necho broken\nexit 3\n")
    script.chmod(0o755)
    assert run(board, "join", "a", "--roles", "docs", "--harness", "custom", "--cmd", "%s {prompt_file}" % script).returncode == 0
    r = run(board, "harness", "check", "a")
    assert r.returncode != 0
    assert "HARNESS FAILED" in r.stdout


def test_case3_harness_check_unknown_placeholder(board, tmp_path):
    script = tmp_path / "ok.sh"
    script.write_text("#!/bin/sh\necho OK\n")
    script.chmod(0o755)
    assert run(
        board, "join", "a", "--roles", "docs",
        "--harness", "custom", "--cmd", "%s {prompt_file} {foo}" % script,
    ).returncode == 0
    r = run(board, "harness", "check", "a")
    assert r.returncode != 0, "unknown {foo} placeholder must fail check, not pass silently"
    out = r.stdout + r.stderr
    assert "HARNESS FAILED" in out or "foo" in out.lower() or "{foo}" in out


def test_case3_harness_check_missing_prompt_file_placeholder(board, tmp_path):
    script = tmp_path / "ok.sh"
    script.write_text("#!/bin/sh\necho OK\n")
    script.chmod(0o755)
    assert run(
        board, "join", "a", "--roles", "docs",
        "--harness", "custom", "--cmd", str(script),
    ).returncode == 0
    r = run(board, "harness", "check", "a")
    assert r.returncode != 0, "template without {prompt_file} must fail harness check"
    out = r.stdout + r.stderr
    assert "HARNESS FAILED" in out or "prompt_file" in out
