"""Reap passing tmp_path git-fixture dirs after a green session (T-471).

pytest's tmp_path_retention_policy=failed uses shutil.rmtree(..., ignore_errors=True).
That leaves trees whose files are not writable (git objects after chmod/gc, and
the T-417 leftover class). It also skips the session basetemp entirely when
--basetemp is set. This module chmod+rmtree's the session basetemp on a green
exit so those dirs cannot recur.
"""
from __future__ import annotations

import os
import shutil
from pathlib import Path

import pytest
from _pytest.config import ExitCode


def rm_rf_readonly(path: Path) -> None:
    """rmtree that chmods read-only git objects first (ignore_errors cannot)."""
    path = Path(path)
    if path.is_symlink() or path.is_file():
        try:
            path.unlink()
        except OSError:
            pass
        return
    if not path.is_dir():
        return
    for root, dirs, files in os.walk(path, followlinks=False):
        for name in files + dirs:
            p = os.path.join(root, name)
            try:
                if os.path.islink(p):
                    continue
                os.chmod(p, 0o700)
            except OSError:
                pass
        try:
            os.chmod(root, 0o700)
        except OSError:
            pass
    shutil.rmtree(path, ignore_errors=True)


def reap_green_basetemp(session, exitstatus) -> None:
    if exitstatus not in (0, ExitCode.OK):
        return
    factory = getattr(session.config, "_tmp_path_factory", None)
    if factory is None:
        return
    basetemp = factory._basetemp
    if basetemp is None:
        return
    rm_rf_readonly(Path(basetemp))


@pytest.hookimpl(trylast=True)
def pytest_sessionfinish(session, exitstatus):
    reap_green_basetemp(session, exitstatus)
