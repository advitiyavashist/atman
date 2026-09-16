#!/usr/bin/env python3
"""T-924 isolated rehearsal. Dry output is NEVER a provider demonstration."""
import argparse
import json
import os
from pathlib import Path
import shlex
import subprocess
import sys
import tempfile

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
SEATS = ("ceo", "codex-worker", "cursor-worker")
PLAN = {"tickets": [
    {"key": "A", "title": "Summarize regional sales", "role": "backend",
     "cause": "The CSV has no reusable regional-summary function.",
     "change": "Add saleskit/summary.py: summarize(path) returns region -> {units, revenue}.",
     "proof": "python3 -m unittest discover -s tests passes; north totals 15 units and 600.00 revenue."},
    {"key": "B", "title": "Print a regional sales report", "role": "backend", "deps": ["A"],
     "cause": "There is no report command for the CSV.",
     "change": "Integrate the accepted A commit; add saleskit/cli.py using summarize().",
     "proof": "python3 -m saleskit.cli report data/sales.csv prints north/south/east totals; CLI test passes."},
]}


def clean_env():
    # Do not inherit live-board or stale session identity into a throwaway run.
    return {k: v for k, v in os.environ.items() if not k.startswith(("TICKET", "TICKETS_"))}


def call(argv, cwd, env=None, capture=False):
    r = subprocess.run([str(x) for x in argv], cwd=cwd, env=env,
                       text=True, capture_output=capture, check=True)
    return r.stdout.strip() if capture else None


def env_for(run, seat):
    e = clean_env()
    e.update(TICKETS_DIR=str(run / "repo/.tickets"), TICKET_AGENT=seat,
             TICKET_SEAT=seat, DEMO_RUN=str(run), PATH=str(run / "bin") + os.pathsep + e["PATH"])
    return e


def load_run(run):
    run = Path(run).resolve()
    manifest = json.loads((run / "manifest.json").read_text())
    if manifest["kind"] != "atman-isolated-demo" or Path(manifest["run"]) != run:
        raise ValueError("not an isolated demo run")
    return run, manifest


def setup(runtime, mode, origin=None):
    runtime = Path(runtime).resolve()
    sha = call(["git", "rev-parse", "HEAD"], runtime, capture=True)
    if mode == "real" and not origin:
        raise ValueError("real setup requires a dedicated throwaway GitHub --origin")
    run = Path(tempfile.mkdtemp(prefix="atman-demo-")).resolve()
    repo = run / "repo"
    repo.mkdir()
    (run / "bin").mkdir()
    (run / "logs").mkdir()
    manifest = {"kind": "atman-isolated-demo", "run": str(run), "mode": mode,
                "runtime": str(runtime), "runtime_sha": sha}
    (run / "manifest.json").write_text(json.dumps(manifest, indent=2))
    # The shim records command exits + CLI-derived state, not just echoed text.
    shim = "#!/bin/sh\nexec " + shlex.quote(sys.executable) + " " + shlex.quote(str(HERE / "receipt.py")) + ' "$@"\n'
    (run / "bin/atm").write_text(shim)
    (run / "bin/atm").chmod(0o755)
    call(["git", "init", "-q", "-b", "main"], repo)
    call(["git", "config", "user.name", "Atman demo"], repo)
    call(["git", "config", "user.email", "demo@atman.local"], repo)
    (repo / "data").mkdir()
    (repo / "data/sales.csv").write_text("region,units,revenue\nnorth,12,480.00\nsouth,7,315.50\nnorth,3,120.00\neast,21,987.25\n")
    (repo / "README.md").write_text("# Disposable sales demo\n")
    (repo / ".gitignore").write_text(".tickets/\n.worktrees/\n__pycache__/\n")
    call(["git", "add", "."], repo)
    call(["git", "commit", "-qm", "Seed sales CSV"], repo)
    if not origin:
        origin = str(run / "origin.git")
        call(["git", "init", "-q", "--bare", origin], run)
    call(["git", "remote", "add", "origin", origin], repo)
    # Setup never pushes to a supplied remote; the recorder checks it is empty.
    if mode == "dry":
        call(["git", "push", "-q", "origin", "main"], repo)
    ce = env_for(run, "ceo")
    call(["atm", "init"], repo, ce)
    call(["git", "add", ".gitignore"], repo)
    if call(["git", "diff", "--cached", "--name-only"], repo, capture=True):
        call(["git", "commit", "-qm", "Ignore demo board"], repo)
    for seat in SEATS:
        work = repo / ".worktrees" / seat
        call(["git", "worktree", "add", "-q", str(work), "-b", seat + "-work", "main"], repo)
        harness = {"ceo": "claude", "codex-worker": "codex", "cursor-worker": "cursor"}[seat]
        call(["atm", "join", seat, "--roles", "master,backend" if seat == "ceo" else "backend",
              "--harness", harness], work, env_for(run, seat))
    call(["atm", "master", "take"], repo / ".worktrees/ceo", ce)
    (run / "plan.json").write_text(json.dumps(PLAN, indent=2))
    write_prompts(run)
    print("DRY REHEARSAL — scripted actors, no provider evidence" if mode == "dry" else "REAL SETUP ONLY — provider launch is separate")
    print("RUN=" + str(run))
    return run


def write_prompts(run):
    common = "This is a throwaway demonstration. Perform real work; do not simulate output or manufacture evidence. Use atm commands yourself. Stay on this board and your existing worktree. Do not edit .tickets files. "
    (run / "ceo.prompt").write_text(common + f"""You are the reviewer/planner. Run:
atm objective --set 'Ship a regional sales summary' --exit 'python3 -m saleskit.cli report data/sales.csv prints regional totals'
atm plan < {shlex.quote(str(run / 'plan.json'))}
atm reserve T-001 --for codex-worker
atm reserve T-002 --for cursor-worker
Wait for T-001 to be IN REVIEW using atm show T-001. Do not implement A yourself.
Then inspect the submitted PR, diff and full commit SHA, and independently run its tests in {run}/repo/.worktrees/codex-worker.
Only if correct run atm accept T-001 --sha FULL40 --notes 'actual checks performed'.
Run atm done T-001 --artifact {run}/repo/.worktrees/codex-worker --notes 'Accepted FULL40; integrate that exact commit before B. saleskit/summary.py: summarize(path) -> region -> units/revenue; north=15/600.00.'
Use the actual SHA instead of FULL40. Do not run any command in Cursor's pane. Stop after done.
""")
    (run / "codex-worker.prompt").write_text(common + """Wait for T-001 to exist, then run atm next and pwd yourself. Implement A; use Python unittest (no dependencies). Run its tests, commit and push your branch. Open a real PR using gh pr create. Run atm review T-001 --pr THE_ACTUAL_PR --notes 'actual paths, interface and test result'. Do not accept or mark your own work done. Stop after review.
""")
    (run / "cursor-worker.prompt").write_text(common + """You were launched by a supervised watcher. Run atm inbox and atm next yourself. Read the Handoff from dependencies output. If no task was claimed, stop without editing. Extract A's accepted full SHA from the handoff, verify it against atm show T-001, and run git merge --ff-only THE_ACTUAL_SHA in your own worktree. Show python3 -c 'from saleskit.summary import summarize; print(summarize("data/sales.csv"))'. Then implement B using that function, add a CLI test, run python3 -m unittest discover -s tests, and run python3 -m saleskit.cli report data/sales.csv. Commit and stop; do not claim B complete before tests pass.
""")


def dry(run):
    run, manifest = load_run(run)
    if manifest["mode"] != "dry":
        raise ValueError("scripted work is allowed only on a dry run")
    work = {s: run / "repo/.worktrees" / s for s in SEATS}
    def atm(seat, *args, extra=None):
        e = env_for(run, seat)
        e.update(extra or {})
        return call(["atm", *args], work[seat], e)
    # Deliberately scripted. Real mode never runs this path.
    atm("ceo", "objective", "--set", "Ship a regional sales summary", "--exit", "Regional report passes")
    with (run / "plan.json").open() as inp:
        subprocess.run([str(run / "bin/atm"), "plan"], cwd=work["ceo"], env=env_for(run, "ceo"), stdin=inp, check=True)
    atm("ceo", "reserve", "T-001", "--for", "codex-worker")
    atm("ceo", "reserve", "T-002", "--for", "cursor-worker")
    atm("codex-worker", "next")
    a = work["codex-worker"]
    (a / "saleskit").mkdir()
    (a / "saleskit/__init__.py").touch()
    (a / "saleskit/summary.py").write_text('import csv\n\ndef summarize(path):\n    out = {}\n    with open(path, newline="") as stream:\n        for row in csv.DictReader(stream):\n            value = out.setdefault(row["region"], {"units": 0, "revenue": 0.0})\n            value["units"] += int(row["units"])\n            value["revenue"] = round(value["revenue"] + float(row["revenue"]), 2)\n    return out\n')
    (a / "tests").mkdir()
    (a / "tests/test_summary.py").write_text('import unittest\nfrom saleskit.summary import summarize\n\nclass SummaryTest(unittest.TestCase):\n    def test_north(self):\n        self.assertEqual(summarize("data/sales.csv")["north"], {"units": 15, "revenue": 600.0})\n')
    call([sys.executable, "-m", "unittest", "discover", "-s", "tests"], a)
    call(["git", "add", "."], a)
    call(["git", "commit", "-qm", "A: regional summary"], a)
    call(["git", "push", "-q", "-u", "origin", "HEAD"], a)
    sha = call(["git", "rev-parse", "HEAD"], a, capture=True)
    origin = call(["git", "config", "--get", "remote.origin.url"], a, capture=True)
    # Explicit fixture injection is confined to the dry review command.
    fixture = json.dumps({"1": {"headRefOid": sha, "headRefName": "codex-worker-work", "headRepository": {"nameWithOwner": origin}}})
    atm("codex-worker", "review", "T-001", "--pr", "1", "--notes", "DRY fixture PR; summarize(path); unittest passed", extra={"TICKETS_PR_VIEW": fixture})
    call([sys.executable, "-m", "unittest", "discover", "-s", "tests"], a, env_for(run, "ceo"))
    atm("ceo", "accept", "T-001", "--sha", sha, "--notes", "DRY rehearsal: independently rerun summary unittest")
    atm("ceo", "done", "T-001", "--artifact", str(a), "--notes", "Accepted " + sha + "; integrate this exact commit. saleskit/summary.py: summarize(path) -> region -> units/revenue.")
    atm("cursor-worker", "inbox")
    atm("cursor-worker", "next")
    b = work["cursor-worker"]
    call(["git", "merge", "--ff-only", sha], b)
    call([sys.executable, "-c", "from saleskit.summary import summarize; assert summarize('data/sales.csv')['north']['units'] == 15"], b)
    (b / "saleskit/cli.py").write_text('import sys\nfrom saleskit.summary import summarize\n\nif __name__ == "__main__":\n    if len(sys.argv) != 3 or sys.argv[1] != "report":\n        raise SystemExit("usage: python3 -m saleskit.cli report CSV")\n    for region, values in summarize(sys.argv[2]).items():\n        print("%s %d %.2f" % (region, values["units"], values["revenue"]))\n')
    (b / "tests/test_cli.py").write_text('import subprocess, sys, unittest\n\nclass CLITest(unittest.TestCase):\n    def test_report(self):\n        text = subprocess.check_output([sys.executable, "-m", "saleskit.cli", "report", "data/sales.csv"], text=True)\n        self.assertEqual(text.splitlines(), ["north 15 600.00", "south 7 315.50", "east 21 987.25"])\n')
    call([sys.executable, "-m", "unittest", "discover", "-s", "tests"], b)
    call([sys.executable, "-m", "saleskit.cli", "report", "data/sales.csv"], b)
    call(["git", "add", "."], b)
    call(["git", "commit", "-qm", "B: report using accepted parser"], b)
    call([sys.executable, str(HERE / "verify.py"), str(run)], HERE)


def main():
    p = argparse.ArgumentParser(description=__doc__)
    sub = p.add_subparsers(dest="action", required=True)
    s = sub.add_parser("setup")
    s.add_argument("--runtime", default=str(ROOT))
    s.add_argument("--mode", choices=("dry", "real"), default="dry")
    s.add_argument("--origin")
    d = sub.add_parser("dry")
    d.add_argument("run")
    a = p.parse_args()
    if a.action == "setup":
        setup(a.runtime, a.mode, a.origin)
    else:
        dry(a.run)


if __name__ == "__main__":
    main()
