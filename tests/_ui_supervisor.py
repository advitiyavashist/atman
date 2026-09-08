"""Test-only parent watchdog: kill the ui child when the pytest test process dies.

When pytest is SIGTERM'd mid-test, atexit, fixture finalizers, and
pytest_sessionfinish never run. This supervisor is the test harness's parent;
it polls the test pid and tears down the ui process group on parent death.
"""
from __future__ import annotations

import atexit
import os
import signal
import subprocess
import sys
import time

_UI_PROC: subprocess.Popen | None = None


def _kill_ui(proc: subprocess.Popen | None) -> None:
    if proc is None or proc.poll() is not None:
        return
    try:
        os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
    except (ProcessLookupError, PermissionError, OSError):
        try:
            proc.terminate()
        except OSError:
            pass
    try:
        proc.wait(timeout=5)
    except subprocess.TimeoutExpired:
        try:
            os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
        except (ProcessLookupError, PermissionError, OSError):
            proc.kill()
        proc.wait(timeout=2)


def _shutdown(code: int = 0) -> None:
    _kill_ui(_UI_PROC)
    raise SystemExit(code)


def _on_signal(signum, _frame) -> None:
    _shutdown(128 + signum)


def main() -> int:
    global _UI_PROC
    parent_pid = int(sys.argv[1])
    ui_cmd = sys.argv[2:]
    _UI_PROC = subprocess.Popen(
        ui_cmd,
        start_new_session=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    atexit.register(lambda: _kill_ui(_UI_PROC))
    signal.signal(signal.SIGTERM, _on_signal)
    signal.signal(signal.SIGINT, _on_signal)
    try:
        while True:
            try:
                os.kill(parent_pid, 0)
            except ProcessLookupError:
                _shutdown(0)
            if _UI_PROC.poll() is not None:
                return _UI_PROC.returncode or 0
            time.sleep(0.15)
    except KeyboardInterrupt:
        _shutdown(130)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
