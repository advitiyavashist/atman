#!/usr/bin/env python3
"""Record executed CLI commands and their resulting CLI snapshot."""
import fcntl
import json
import os
from pathlib import Path
import subprocess
import sys
import time

from rehearsal import load_run

run, manifest = load_run(os.environ["DEMO_RUN"])
board = run / "repo/.tickets"
if Path(os.environ.get("TICKETS_DIR", "")).resolve() != board:
    raise SystemExit("demo shim refuses a different board")
if manifest["mode"] != "dry" and os.environ.get("TICKETS_PR_VIEW"):
    raise SystemExit("fixture PR injection is forbidden in a real run")
runtime = Path(manifest["runtime"])
actual = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=runtime, text=True).strip()
if actual != manifest["runtime_sha"]:
    raise SystemExit("runtime SHA changed after setup; make a fresh run")
argv = [sys.executable, str(runtime / "tickets.py"), *sys.argv[1:]]
start = time.time()
r = subprocess.run(argv, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
sys.stdout.write(r.stdout)
sys.stderr.write(r.stderr)
snap = subprocess.run([sys.executable, str(runtime / "tickets.py"), "ui", "--json"],
                      text=True, capture_output=True)
try:
    state = json.loads(snap.stdout) if snap.returncode == 0 else None
except ValueError:
    state = None
event = {"kind": "cli", "mode": manifest["mode"], "seat": os.environ.get("TICKET_AGENT"),
         "argv": sys.argv[1:], "pid": os.getpid(), "parent_pid": os.getppid(),
         "cwd": os.getcwd(), "start": start, "end": time.time(),
         "exit": r.returncode, "stdout": r.stdout, "stderr": r.stderr, "state": state}
event["tickets"] = {p.stem: json.loads(p.read_text()) for p in board.glob("T-*.json")}
with (run / "logs/events.jsonl").open("a") as f:
    fcntl.flock(f, fcntl.LOCK_EX)
    f.write(json.dumps(event) + "\n")
raise SystemExit(r.returncode)
