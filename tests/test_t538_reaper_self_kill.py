"""T-538: the tmp_path watch-reaper must never SIGTERM its own runner.

The autouse fixture in conftest.py reaps every tmp_path after every test, and
reap_watchers_under() kills whatever integer it finds in a *.watch.pid file
there. The pid in that file is written by the code under test -- and
tickets.py's watch_idle_reexec writes str(os.getpid()), so a test that calls
that helper in-process plants the PYTEST PROCESS'S OWN PID where the reaper
looks. Before the fix, tests/test_t427_watch_reexec.py died exit 143 after two
dots with no summary line, at the teardown of test_helper_hops_when_shim_flipped
-- indistinguishable, to a merge, from a code failure.

Each scenario runs in a SUBPROCESS: pre-fix, the victim is killed by SIGTERM,
so an in-process assertion would take the test runner down with it instead of
failing cleanly.
"""
from __future__ import annotations

import os
import subprocess
import sys
import textwrap
from pathlib import Path

TESTS = Path(__file__).resolve().parent


def run_scenario(body, tmp_path, timeout=60):
    """Execute `body` in a fresh interpreter that can import watch_reaper."""
    script = tmp_path / "scenario.py"
    script.write_text(
        "import os, sys, time, subprocess\n"
        "sys.path.insert(0, %r)\n" % str(TESTS)
        # Import ONLY what existed before the fix: if the scenario preamble
        # referenced the new guard, every pre-fix teeth run would die on an
        # ImportError and never exercise the kill these tests exist to catch.
        + "from watch_reaper import reap_watchers_under, kill_pid_tree\n"
        + textwrap.dedent(body)
    )
    return subprocess.run([sys.executable, str(script)], capture_output=True,
                          text=True, timeout=timeout, cwd=str(tmp_path))


def test_reaper_does_not_kill_its_own_runner(tmp_path):
    """A *.watch.pid holding the reaping process's own pid must be skipped."""
    root = tmp_path / "root"
    (root / "agents").mkdir(parents=True)
    out = run_scenario(
        """
        root = %r
        open(os.path.join(root, "agents", "doc.watch.pid"), "w").write(str(os.getpid()))
        reap_watchers_under(root)
        time.sleep(1.5)          # outlive kill_pid_tree's SIGTERM -> SIGKILL escalation
        print("SURVIVED")
        """ % str(root),
        tmp_path,
    )
    assert out.returncode == 0, (
        "reaper killed its own runner: rc=%s (negative rc = died by that signal; "
        "-15 is the exact T-538 defect)\nstderr=%s" % (out.returncode, out.stderr)
    )
    assert "SURVIVED" in out.stdout, out.stdout


def test_reaper_does_not_kill_an_ancestor(tmp_path):
    """A pid file naming the reaper's PARENT must be skipped too."""
    root = tmp_path / "root"
    (root / "agents").mkdir(parents=True)
    out = run_scenario(
        """
        root = %r
        # This process is the ANCESTOR. Its child does the reaping and finds
        # this pid in the file -- the shape a nested harness produces.
        child = subprocess.run([sys.executable, "-c",
            "import os, sys, time\\n"
            "sys.path.insert(0, %%r)\\n"
            "from watch_reaper import reap_watchers_under\\n"
            "open(os.path.join(%%r, 'agents', 'doc.watch.pid'), 'w').write(str(os.getppid()))\\n"
            "reap_watchers_under(%%r)\\n"
            "print('CHILD OK')\\n" %% (%r, root, root)],
            capture_output=True, text=True, timeout=30)
        time.sleep(1.5)
        print("PARENT SURVIVED", child.returncode, child.stdout.strip())
        """ % (str(root), str(TESTS)),
        tmp_path,
    )
    assert out.returncode == 0, (
        "reaper killed an ancestor of itself: rc=%s\nstderr=%s" % (out.returncode, out.stderr))
    assert "PARENT SURVIVED 0" in out.stdout, out.stdout


def test_guard_still_reaps_a_real_leaked_process(tmp_path):
    """Positive control: the guard must not turn the reaper into a no-op."""
    root = tmp_path / "root"
    (root / "agents").mkdir(parents=True)
    out = run_scenario(
        """
        root = %r
        leaked = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(120)"],
                                  start_new_session=True)
        open(os.path.join(root, "agents", "doc.watch.pid"), "w").write(str(leaked.pid))
        reap_watchers_under(root)
        for _ in range(60):
            if leaked.poll() is not None:
                break
            time.sleep(0.1)
        print("LEAKED RC", leaked.poll())
        """ % str(root),
        tmp_path,
    )
    assert out.returncode == 0, out.stderr
    assert "LEAKED RC None" not in out.stdout, (
        "guard disabled the reaper -- a genuinely leaked process survived: %s" % out.stdout)
    assert "LEAKED RC" in out.stdout, out.stdout


def test_would_kill_self_identifies_self_and_parent():
    from watch_reaper import would_kill_self
    assert would_kill_self(os.getpid()) is True
    assert would_kill_self(os.getppid()) is True
    assert would_kill_self(-1) is False
    assert would_kill_self("not-a-pid") is False
