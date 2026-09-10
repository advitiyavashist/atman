"""T-404 F-12 teeth: the wait() closed-stdin fix must be visible on Python 3.14.

T-190's real-child round-trip only failed on CPython 3.9: ``communicate()``
after ``stdin.close()`` raises ``ValueError`` there and does not on 3.14.2.
Reverting ``process.stdin = None`` therefore reds nothing on the fleet
interpreter. This module asserts the structural property the fix
establishes -- the Popen no longer holds a closed stdin for communicate()
to flush -- and that start()+wait() still echoes the prompt.
"""

from __future__ import annotations

import os
import signal
import subprocess
import sys
import time
import uuid
from pathlib import Path

import pytest

from ticket_board.runners.launcher import ClaudeLauncher, LaunchSpec


def _script(tmp_path: Path) -> str:
    script = tmp_path / "fake-claude"
    script.write_text("#!/bin/sh\ncat\n")
    script.chmod(0o755)
    return str(script)


def test_start_drops_stdin_so_wait_cannot_flush_a_closed_pipe(tmp_path):
    launcher = ClaudeLauncher(binary=_script(tmp_path))
    spec = LaunchSpec(
        session_id=str(uuid.uuid4()),
        worktree=tmp_path,
        prompt="ECHO-F12\n",
    )
    handle = launcher.start(spec)
    try:
        assert handle._process.stdin is None, (
            "ClaudeLauncher.start must drop Popen.stdin after close(); "
            "otherwise communicate() raises on Python 3.9 and is silent on 3.14"
        )
        result = handle.wait(timeout=5)
        assert result.returncode == 0, (result.returncode, result.stderr)
        assert "ECHO-F12" in result.stdout
    finally:
        if handle._process.poll() is None:
            handle.terminate()


@pytest.mark.xfail(strict=True, reason=(
    "F-12b (T-493): F-12 closed the wait() crash, not the orphan. SIGKILL of "
    "the parent still leaves the child alive (reparented to pid 1). "
    "launcher.py is a bare Popen with no start_new_session/preexec_fn and "
    "no death signal. Distinct from F-11 (cancel never reaches the child)."))
def test_killing_the_launcher_parent_does_not_leave_the_child_alive(tmp_path):
    child_script = tmp_path / "hold"
    script = tmp_path / "parent.py"
    child_script.write_text("#!/usr/bin/env python3\nimport time\ntime.sleep(60)\n")
    child_script.chmod(0o755)
    script.write_text(
        "import os, signal, sys, uuid\n"
        "from pathlib import Path\n"
        "sys.path.insert(0, {src!r})\n"
        "from ticket_board.runners.launcher import ClaudeLauncher, LaunchSpec\n"
        "launcher = ClaudeLauncher(binary={binary!r})\n"
        "handle = launcher.start(LaunchSpec(\n"
        "    session_id=str(uuid.uuid4()), worktree=Path({wt!r}), prompt='x'))\n"
        "sys.stdout.write(str(handle.pid) + '\\n')\n"
        "sys.stdout.flush()\n"
        "os.kill(os.getpid(), signal.SIGKILL)\n".format(
            src=str(Path(__file__).resolve().parents[2] / "src"),
            binary=str(child_script),
            wt=str(tmp_path),
        )
    )
    proc = subprocess.Popen(
        [sys.executable, str(script)],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    child_pid = None
    try:
        stdout, stderr = proc.communicate(timeout=5)
        assert proc.returncode != 0
        child_pid = int(stdout.strip().splitlines()[0])
        time.sleep(0.4)
        os.kill(child_pid, 0)
        raise AssertionError(
            "child pid {} still alive after parent SIGKILL (stderr={!r})".format(
                child_pid, stderr)
        )
    except ProcessLookupError:
        return
    except subprocess.TimeoutExpired:
        proc.kill()
        raise
    finally:
        if child_pid:
            try:
                os.kill(child_pid, signal.SIGKILL)
            except OSError:
                pass
