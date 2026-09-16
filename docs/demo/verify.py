#!/usr/bin/env python3
"""Validate transitions and git provenance; never certifies model inference."""
import argparse
import json
from pathlib import Path
import subprocess

from rehearsal import load_run


def verify_events(events):
    def event(command, seat):
        found = [e for e in events if e.get("kind") == "cli" and e.get("argv", [None])[0] == command
                 and e.get("seat") == seat and e.get("exit") == 0]
        assert found, "missing successful executed %s by %s" % (command, seat)
        return found[-1]
    plan = event("plan", "ceo")
    assert plan["tickets"]["T-002"]["deps"] == ["T-001"], "missing dependency"
    assert plan["tickets"]["T-002"]["status"] == "open", "B must initially wait"
    a = event("next", "codex-worker")
    assert a["tickets"]["T-001"]["owner"] == "codex-worker"
    assert a["tickets"]["T-001"]["status"] == "claimed"
    review = event("review", "codex-worker")
    pin = review["tickets"]["T-001"]
    sha = pin["review_head"]
    assert len(sha) == 40 and pin["status"] == "review", "review must pin full SHA"
    accepted = event("accept", "ceo")
    verdicts = accepted["tickets"]["T-001"]["review_events"]
    assert any(v["kind"] == "accept" and v["sha"] == sha and v["by"] == "ceo" for v in verdicts), "no structured exact acceptance"
    done = event("done", "ceo")
    assert done["tickets"]["T-001"]["status"] == "done"
    assert done["tickets"]["T-002"]["status"] == "open", "posting must not pretend B claimed"
    b = event("next", "cursor-worker")
    assert b["tickets"]["T-002"]["owner"] == "cursor-worker"
    assert b["tickets"]["T-002"]["status"] == "claimed"
    handoff = done["tickets"]["T-001"]["notes"][-1]["text"]
    assert sha in handoff and "summarize(path)" in handoff, "handoff must name code and interface"
    assert handoff in b["stdout"], "Cursor claim did not receive actual dependency note"
    assert plan["end"] <= a["start"] < review["start"] < accepted["start"] < done["start"] < b["start"], "invalid transition order"
    for e in events:
        if e.get("kind") == "cli" and e.get("exit"):
            raise AssertionError("CLI failure retained in receipt: %s" % e["argv"])
    return sha


def verify(run):
    run, manifest = load_run(run)
    events = [json.loads(x) for x in (run / "logs/events.jsonl").read_text().splitlines()]
    sha = verify_events(events)
    board = run / "repo/.tickets"
    current = json.loads((board / "T-001.json").read_text())
    assert current["review_head"] == sha
    a = run / "repo/.worktrees/codex-worker"
    b = run / "repo/.worktrees/cursor-worker"
    actual = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=a, text=True).strip()
    assert actual == sha, "A changed after review"
    subprocess.run(["git", "merge-base", "--is-ancestor", sha, "HEAD"], cwd=b, check=True)
    assert (b / "saleskit/summary.py").is_file(), "B has no accepted code"
    if manifest["mode"] == "real":
        # A receipt cannot establish that a model did the work. Require preserved
        # provider streams and fail closed as to final publication approval.
        for seat in ("ceo", "codex-worker", "cursor-worker"):
            launches = [e for e in events if e.get("kind") == "provider_exit" and e.get("seat") == seat and e.get("exit") == 0]
            assert launches, "missing successful real provider process: " + seat
            assert (run / "logs" / (seat + ".jsonl")).stat().st_size > 0
        print("REAL mechanical checks passed; HUMAN transcript/cast review still REQUIRED")
    else:
        print("DRY checks passed: dependency, review/accept SHA, ordered state transitions, handoff receipt, git ancestry. NO provider or watcher evidence.")
    return sha


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("run")
    verify(parser.parse_args().run)
