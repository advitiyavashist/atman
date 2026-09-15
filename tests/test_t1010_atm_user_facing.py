"""T-1010: users read atm; tickets remains a working alias.

User-facing command strings (prompts, join/next/quickstart, wake lines,
onboarding docs, README) teach `atm <subcommand>`. The `tickets` PATH name
must still execute the same implementation. Historical docs/internal and
verbatim past board quotes are out of scope.
"""
from __future__ import annotations

import os
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TOOL = ROOT / "tickets.py"

SUBCOMMANDS = (
    "create", "assign", "reserve", "hold", "epic", "sprint", "master", "join",
    "retire", "harness", "route", "connect", "self", "pending", "remote",
    "prompt", "stop-hook", "objective", "drive", "watch", "hook-run",
    "codex-hook", "boot", "spawn", "ui", "quickstart", "guide", "brief",
    "util", "dash", "hooks", "here", "who", "msg", "inbox", "trajectories",
    "traj", "turns", "review", "accept", "reject", "sync", "merge", "limit",
    "limits", "status", "update", "dep", "graph", "map", "plan", "capture",
    "sound", "dispatch", "pr-sync", "schedule", "plan-status", "discard",
    "retro", "list", "board", "show", "next", "claim", "done", "repin",
    "block", "note", "reopen", "where", "context", "knowledge", "kb",
    "mine", "init", "identity", "role", "handover", "pulse", "clear",
)
CMD = re.compile(r"(?<![\w./-])tickets (" + "|".join(SUBCOMMANDS) + r")\b")

USER_FACING_BLOBS = (
    "WORKER_PROMPT",
    "MASTER_PROMPT",
    "COS_PROMPT",
    "PLANNER_PROMPT",
    "MASTER_TEMPLATE",
    "CONNECT",
    "PROTOCOL",
    "ONBOARDING_STARTUP",
)


def _load():
    import importlib.util
    spec = importlib.util.spec_from_file_location("t1010_tickets", TOOL)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _hits(text):
    return [m.group(0) for m in CMD.finditer(text or "")]


def test_user_facing_templates_teach_atm_not_tickets_subcommand():
    mod = _load()
    for name in USER_FACING_BLOBS:
        blob = getattr(mod, name)
        found = _hits(blob)
        assert found == [], "%s still teaches %s" % (name, found)
    assert "compatibility alias" in mod.WORKER_PROMPT
    assert "atm next" in mod.WORKER_PROMPT
    src = TOOL.read_text(encoding="utf-8")
    assert re.search(r'prompt = \{.*"atm prompt --master"', src, re.S)
    assert '"$(%s)"' in src
    assert "tickets prompt" not in src[src.index("def _worker_cmd"): src.index("def _worker_cmd") + 2500]


def test_join_next_quickstart_and_wake_say_atm():
    src = TOOL.read_text(encoding="utf-8")
    assert '%satm next' in src
    assert '%satm update' in src
    assert '%satm review' in src
    assert '%stickets next' not in src
    adapters = (ROOT / "session_adapters.py").read_text(encoding="utf-8")
    assert "see `atm inbox` for the rest" in adapters
    assert "see `tickets inbox` for the rest" not in adapters
    join_loop = [ln for ln in src.splitlines() if ln.strip().startswith('print("Loop:')]
    assert join_loop, src
    assert all("tickets " not in ln for ln in join_loop)


def test_onboarding_docs_and_readme_teach_atm():
    paths = [
        ROOT / "README.md",
        ROOT / "docs" / "first-session.md",
        *sorted((ROOT / "docs" / "onboarding").glob("*.md")),
    ]
    for path in paths:
        found = _hits(path.read_text(encoding="utf-8"))
        assert found == [], "%s still teaches %s" % (path.relative_to(ROOT), found)


def test_tickets_alias_still_runs_the_same_commands(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q", "-b", "main"], cwd=repo, check=True)
    board = repo / ".tickets"
    bindir = tmp_path / "bin"
    bindir.mkdir()
    (bindir / "atm").symlink_to(TOOL)
    (bindir / "tickets").symlink_to(TOOL)
    env = dict(os.environ, TICKETS_DIR=str(board), TICKET_AGENT="t1010",
               HOME=str(tmp_path / "home"), PATH=str(bindir) + os.pathsep + os.environ.get("PATH", ""))
    env.pop("TICKETS_STOP_HOOK", None)
    (tmp_path / "home").mkdir()
    created = subprocess.run(
        [str(bindir / "atm"), "create", "alias-proof", "--role", "backend"],
        capture_output=True, text=True, env=env, cwd=str(repo))
    assert created.returncode == 0, created.stderr
    shown = subprocess.run(
        [str(bindir / "tickets"), "show", "T-001"],
        capture_output=True, text=True, env=env, cwd=str(repo))
    assert shown.returncode == 0, shown.stderr
    assert "alias-proof" in shown.stdout
    listed = subprocess.run(
        [str(bindir / "tickets"), "list"],
        capture_output=True, text=True, env=env, cwd=str(repo))
    assert listed.returncode == 0, listed.stderr
    assert "T-001" in listed.stdout
