"""T-427: watch loop hops onto the live shim at the idle boundary.

Fake tickets-releases dirs under tmp; never touch ~/.claude/tools.
"""
from __future__ import annotations

import hashlib
import importlib.util
import os
import shutil
import signal
import subprocess
import sys
import time
from pathlib import Path

import pytest

from test_wakeup import board  # noqa: F401

ROOT = Path(__file__).resolve().parents[1]
FILES = ("tickets.py", "ticket_coordination.py", "board_backup.py")
START = "# --- T-427: watch idle-boundary self-execv"
END = "# --- end T-427 ---"


def _extract_helper(path):
    text = Path(path).read_text()
    a = text.index(START)
    b = text.index(END, a) + len(END)
    return text[a:b]


def load_tickets():
    spec = importlib.util.spec_from_file_location("tickets_t427", ROOT / "tickets.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def write_shim(path, tickets_py):
    path.write_text(
        "#!/usr/bin/env python3\nimport os, sys\n"
        "os.execv(sys.executable, [sys.executable, %r] + sys.argv[1:])\n"
        % str(tickets_py)
    )
    path.chmod(0o755)


def stamp_release(release, sha):
    files = {}
    for name in FILES:
        data = (release / name).read_bytes()
        files[name] = {"sha256": hashlib.sha256(data).hexdigest(), "size": len(data)}
    (release / "release.json").write_text(
        __import__("json").dumps({"commit": sha, "files": files}, sort_keys=True) + "\n"
    )


def make_releases(tmp_path, sha_a="a" * 40, sha_b="b" * 40):
    root = tmp_path / "tools"
    releases = root / "tickets-releases"
    shim = root / "tickets.py"
    copies = {}
    for sha in (sha_a, sha_b):
        dest = releases / sha
        dest.mkdir(parents=True)
        for name in FILES:
            shutil.copy2(ROOT / name, dest / name)
        stamp_release(dest, sha)
        copies[sha] = dest
    write_shim(shim, copies[sha_a] / "tickets.py")
    return shim, copies[sha_a], copies[sha_b], sha_a, sha_b


def cmdline_of(pid):
    try:
        out = subprocess.check_output(["ps", "-p", str(pid), "-o", "command="], text=True)
    except subprocess.CalledProcessError:
        return ""
    return out.strip()


def pid_alive(pid):
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def stop_watch(board, owner, proc):
    stop = board / "agents" / ("%s.watch.stop" % owner)
    stop.parent.mkdir(parents=True, exist_ok=True)
    stop.write_text("1")
    if proc.poll() is None:
        try:
            proc.send_signal(signal.SIGTERM)
        except OSError:
            pass
        try:
            proc.wait(timeout=8)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait(timeout=5)


def watch_env(board, shim, extra=None):
    e = dict(os.environ, TICKETS_DIR=str(board), TICKET_AGENT="doc",
             TICKETS_LIVE_SHIM=str(shim), HOME=str(board.parent.parent / "home"))
    e.pop("TICKETS_STOP_HOOK", None)
    if extra:
        e.update(extra)
    return e


def start_watch(tool, board, shim, *args, cwd=None):
    repo = cwd or board.parent
    return subprocess.Popen(
        [sys.executable, str(tool), "watch", "--agent", "doc", *args],
        cwd=str(repo), env=watch_env(board, shim),
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
    )


def wait_pid_file(board, owner="doc", timeout=8):
    path = board / "agents" / ("%s.watch.pid" % owner)
    deadline = time.time() + timeout
    while time.time() < deadline:
        if path.exists():
            try:
                pid = int(path.read_text().strip() or "0")
            except ValueError:
                pid = 0
            if pid and pid_alive(pid):
                return pid, path
        time.sleep(0.05)
    return 0, path


def wait_log(log_path, needle, timeout=20):
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            text = log_path.read_text()
        except OSError:
            text = ""
        if needle in text:
            return text
        time.sleep(0.1)
    try:
        return log_path.read_text()
    except OSError:
        return ""


def test_helper_parity_byte_identical():
    assert _extract_helper(ROOT / "tickets.py") == _extract_helper(
        ROOT / "src" / "ticket_board" / "cli.py"
    )


def test_helper_hops_when_shim_flipped(tmp_path):
    tk = load_tickets()
    tk.watch_idle_reexec._warned = set()
    shim, rel_a, rel_b, sha_a, sha_b = make_releases(tmp_path)
    hopped = []

    def fake_execv(exe, argv):
        hopped.append((exe, list(argv)))
        raise SystemExit(0)

    logs = []
    pid_path = tmp_path / "doc.watch.pid"
    assert tk.watch_idle_reexec(
        executing_file=str(rel_a / "tickets.py"),
        shim_path=str(shim),
        argv=["watch", "--agent", "doc", "--every", "5"],
        log=logs.append,
        pid_path=str(pid_path),
        execv=fake_execv,
    ) is False
    assert hopped == []
    write_shim(shim, rel_b / "tickets.py")
    tk.watch_idle_reexec._warned = set()
    logs.clear()
    with pytest.raises(SystemExit):
        tk.watch_idle_reexec(
            executing_file=str(rel_a / "tickets.py"),
            shim_path=str(shim),
            argv=["watch", "--agent", "doc", "--every", "5", "--heartbeat", "30"],
            log=logs.append,
            pid_path=str(pid_path),
            execv=fake_execv,
        )
    assert logs == ["release %s -> %s, re-exec" % (sha_a, sha_b)]
    assert hopped[0][1][1] == str(shim.resolve())
    assert hopped[0][1][2:] == ["watch", "--agent", "doc", "--every", "5", "--heartbeat", "30"]
    assert pid_path.read_text().strip() == str(os.getpid())


def test_helper_unreadable_shim_stays_and_logs_once(tmp_path):
    tk = load_tickets()
    tk.watch_idle_reexec._warned = set()
    shim, rel_a, rel_b, sha_a, sha_b = make_releases(tmp_path)
    missing = tmp_path / "no-such-shim.py"
    logs = []
    hopped = []
    tk.watch_idle_reexec(
        executing_file=str(rel_a / "tickets.py"),
        shim_path=str(missing),
        argv=["watch"],
        log=logs.append,
        execv=lambda *a: hopped.append(a),
    )
    tk.watch_idle_reexec(
        executing_file=str(rel_a / "tickets.py"),
        shim_path=str(missing),
        argv=["watch"],
        log=logs.append,
        execv=lambda *a: hopped.append(a),
    )
    assert hopped == []
    assert len(logs) == 1
    assert "unreadable" in logs[0]


def test_helper_same_size_tamper_does_not_hop(tmp_path):
    tk = load_tickets()
    tk.watch_idle_reexec._warned = set()
    shim, rel_a, rel_b, sha_a, sha_b = make_releases(tmp_path)
    path = rel_b / "tickets.py"
    data = bytearray(path.read_bytes())
    data[0] ^= 0x01
    path.write_bytes(bytes(data))
    recorded = __import__("json").loads((rel_b / "release.json").read_text())
    assert path.stat().st_size == recorded["files"]["tickets.py"]["size"]
    write_shim(shim, path)
    hopped = []
    logs = []
    tk.watch_idle_reexec(
        executing_file=str(rel_a / "tickets.py"),
        shim_path=str(shim),
        argv=["watch"],
        log=logs.append,
        execv=lambda *a: hopped.append(a),
    )
    assert hopped == []
    assert any("not a verified release" in line for line in logs)


def test_helper_size_mismatch_does_not_hop(tmp_path):
    tk = load_tickets()
    tk.watch_idle_reexec._warned = set()
    shim, rel_a, rel_b, sha_a, sha_b = make_releases(tmp_path)
    with open(rel_b / "tickets.py", "ab") as source:
        source.write(b"#")
    write_shim(shim, rel_b / "tickets.py")
    hopped = []
    logs = []
    tk.watch_idle_reexec(
        executing_file=str(rel_a / "tickets.py"),
        shim_path=str(shim),
        argv=["watch"],
        log=logs.append,
        execv=lambda *a: hopped.append(a),
    )
    assert hopped == []
    assert any("not a verified release" in line for line in logs)


def test_helper_unverified_target_does_not_hop(tmp_path):
    tk = load_tickets()
    tk.watch_idle_reexec._warned = set()
    shim, rel_a, rel_b, sha_a, sha_b = make_releases(tmp_path)
    (rel_b / "release.json").unlink()
    write_shim(shim, rel_b / "tickets.py")
    hopped = []
    logs = []
    tk.watch_idle_reexec(
        executing_file=str(rel_a / "tickets.py"),
        shim_path=str(shim),
        argv=["watch"],
        log=logs.append,
        execv=lambda *a: hopped.append(a),
    )
    assert hopped == []
    assert any("not a verified release" in line for line in logs)


def test_loop_reexecs_onto_flipped_shim(board, tmp_path):
    from test_wakeup import run
    run(board, "join", "doc", "--roles", "docs")
    run(board, "next", agent="doc")  # claim so the loop idles
    shim, rel_a, rel_b, sha_a, sha_b = make_releases(tmp_path)
    proc = start_watch(rel_a / "tickets.py", board, shim, "--every", "5", "--exec", "true")
    try:
        pid, pid_file = wait_pid_file(board)
        assert pid, "watch did not take the pid lock"
        assert sha_a in cmdline_of(pid)
        write_shim(shim, rel_b / "tickets.py")
        log = board / "agents" / "doc.watch.log"
        text = wait_log(log, "release %s -> %s, re-exec" % (sha_a, sha_b), timeout=16)
        assert "release %s -> %s, re-exec" % (sha_a, sha_b) in text, text
        deadline = time.time() + 8
        new_pid = pid
        while time.time() < deadline:
            try:
                new_pid = int(pid_file.read_text().strip() or "0")
            except (OSError, ValueError):
                new_pid = 0
            if new_pid and pid_alive(new_pid) and sha_b in cmdline_of(new_pid):
                break
            time.sleep(0.1)
        assert sha_b in cmdline_of(new_pid), cmdline_of(new_pid)
        rec = __import__("json").loads((board / "agents" / "doc.json").read_text())
        assert rec.get("name") == "doc" or rec
        argv = cmdline_of(new_pid)
        assert "--agent" in argv and "doc" in argv
        assert "--every" in argv
        assert "--exec" in argv
    finally:
        stop_watch(board, "doc", proc)


def test_in_flight_run_is_not_interrupted(board, tmp_path):
    from test_wakeup import run
    run(board, "join", "doc", "--roles", "docs")
    marker = board.parent / "ran.txt"
    shim, rel_a, rel_b, sha_a, sha_b = make_releases(tmp_path)
    cmd = "sleep 6; echo done > %s" % marker
    proc = start_watch(rel_a / "tickets.py", board, shim, "--every", "5", "--exec", cmd)
    try:
        pid, pid_file = wait_pid_file(board)
        assert pid
        log = board / "agents" / "doc.watch.log"
        wait_log(log, "run 1", timeout=8)
        write_shim(shim, rel_b / "tickets.py")
        time.sleep(1.0)
        assert pid_alive(pid)
        assert sha_a in cmdline_of(pid), "parent hopped while the child was running"
        assert not marker.exists()
        deadline = time.time() + 20
        hopped = False
        while time.time() < deadline:
            try:
                cur = int(pid_file.read_text().strip() or "0")
            except (OSError, ValueError):
                cur = 0
            if cur and sha_b in cmdline_of(cur):
                hopped = True
                break
            time.sleep(0.2)
        assert marker.read_text().strip() == "done"
        assert hopped, cmdline_of(int(pid_file.read_text().strip() or "0") or pid)
    finally:
        stop_watch(board, "doc", proc)


def test_unreadable_shim_does_not_crash_loop(board, tmp_path):
    from test_wakeup import run
    run(board, "join", "doc", "--roles", "docs")
    run(board, "next", agent="doc")
    shim, rel_a, rel_b, sha_a, sha_b = make_releases(tmp_path)
    missing = tmp_path / "gone-shim.py"
    proc = start_watch(rel_a / "tickets.py", board, missing, "--every", "5", "--exec", "true")
    try:
        pid, _pid_file = wait_pid_file(board)
        assert pid and pid_alive(pid)
        time.sleep(1.2)
        assert pid_alive(pid)
        log = (board / "agents" / "doc.watch.log").read_text()
        assert "unreadable" in log
        assert log.count("unreadable") == 1
        assert proc.poll() is None
    finally:
        stop_watch(board, "doc", proc)
