#!/usr/bin/env python3
"""Real provider entry points. Never used by the dry rehearsal."""
import argparse
import fcntl
import json
import os
import shlex
import shutil
import subprocess
import sys
import time

from rehearsal import HERE, env_for, load_run


def log(run, event):
    with (run / "logs/events.jsonl").open("a") as stream:
        fcntl.flock(stream, fcntl.LOCK_EX)
        stream.write(json.dumps(event) + "\n")


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("run")
    p.add_argument("seat", choices=("ceo", "codex-worker", "cursor-worker"))
    p.add_argument("--watch", action="store_true")
    p.add_argument("--release-approved", action="store_true", help="recorder confirms T-1014 landed and final runtime was checked")
    a = p.parse_args()
    run, manifest = load_run(a.run)
    if manifest["mode"] != "real" or not a.release_approved:
        p.error("real providers require a real setup and --release-approved after T-1014")
    env = env_for(run, a.seat)
    cwd = run / "repo/.worktrees" / a.seat
    if a.watch:
        if a.seat != "cursor-worker":
            p.error("only the Cursor handoff uses this watcher")
        # Launch only after Claude has reserved both tasks. Otherwise a generic
        # backend watcher could see A before the reservation is recorded.
        for tid in ("T-001", "T-002"):
            path = run / "repo/.tickets" / (tid + ".json")
            if not path.exists():
                p.error("start watcher after Claude creates and reserves both tickets")
        b = json.loads((run / "repo/.tickets/T-002.json").read_text())
        parent = json.loads((run / "repo/.tickets/T-001.json").read_text())
        if (b.get("deps") != ["T-001"] or b.get("reserved_for") != "cursor-worker"
                or parent.get("reserved_for") != "codex-worker"):
            p.error("Claude must reserve A for Codex and dependent B for Cursor before watcher startup")
        command = shlex.join([sys.executable, str(HERE / "seat.py"), str(run), a.seat, "--release-approved"])
        # This launches a real Cursor turn; no shell-side inbox, next or edits.
        argv = [sys.executable, str(manifest["runtime"] + "/tickets.py"), "watch",
                "--agent", a.seat, "--every", "5", "--cwd", str(cwd), "--exec", command, "--max-runs", "1"]
        print("Supervised watcher — task posting is not a claim", flush=True)
        return subprocess.call(argv, cwd=cwd, env=env)
    prompt = (run / (a.seat + ".prompt")).read_text()
    commands = {
        "ceo": ["claude", "--print", "--verbose", "--output-format", "stream-json", prompt],
        "codex-worker": ["codex", "exec", "--json", "--cd", str(cwd), prompt],
        "cursor-worker": ["cursor-agent", "--print", "--output-format", "stream-json", prompt],
    }
    argv = commands[a.seat]
    executable = shutil.which(argv[0], path=env["PATH"])
    if not executable:
        raise SystemExit("provider executable unavailable: " + argv[0])
    argv[0] = executable
    child = subprocess.Popen(argv, cwd=cwd, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    log(run, {"kind": "provider_start", "seat": a.seat, "pid": child.pid,
              "executable": executable, "start": time.time(), "cwd": str(cwd)})
    with (run / "logs" / (a.seat + ".jsonl")).open("ab") as stream:
        # Preserve and display the same bytes. Do not buffer until process exit.
        while True:
            block = os.read(child.stdout.fileno(), 4096)
            if not block:
                break
            stream.write(block)
            stream.flush()
            sys.stdout.buffer.write(block)
            sys.stdout.buffer.flush()
    rc = child.wait()
    log(run, {"kind": "provider_exit", "seat": a.seat, "pid": child.pid,
              "exit": rc, "end": time.time()})
    return rc


if __name__ == "__main__":
    raise SystemExit(main())
