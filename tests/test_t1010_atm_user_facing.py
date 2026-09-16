"""T-1010: users read atm; tickets remains a working alias.

User-facing command strings (prompts, join/next/quickstart, wake lines,
onboarding docs, README, generated init files) teach `atm <subcommand>`.
The `tickets` PATH name must still execute the same implementation.
Historical docs/internal and verbatim past board quotes are out of scope.
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
    "--version",
)
# Split-line ``tickets\nsprint show`` is a generated-file defect; match across
# whitespace including newlines.
CMD = re.compile(
    r"(?<![\w./-])tickets\s+(" + "|".join(re.escape(s) for s in SUBCOMMANDS) + r")\b",
    re.S,
)

USER_FACING_BLOBS = (
    "WORKER_PROMPT",
    "MASTER_PROMPT",
    "COS_PROMPT",
    "PLANNER_PROMPT",
    "MASTER_TEMPLATE",
    "CONNECT",
    "PROTOCOL",
    "ONBOARDING_STARTUP",
    "CURSOR_RULE",
)

PUBLIC_PATHS = [
    ROOT / "README.md",
    ROOT / "docs" / "first-session.md",
    ROOT / "docs" / "connect-agy.md",
    ROOT / "docs" / "wake-recipients.md",
    ROOT / "session_adapters.py",
    ROOT / "src" / "ticket_board" / "cli.py",
    *sorted((ROOT / "docs" / "onboarding").glob("*.md")),
]

# Live-board / author-machine tokens must not appear on the public first-read
# path. Historical receipts live under docs/internal.
# Derive the operator home at runtime so the guard still fails a real home
# without publishing that home as a string in the repo (T-1045).
PLACEHOLDER_USER_HOMES = (
    "/Users/someone",
)


def _runtime_operator_home() -> str:
    return os.path.expanduser("~")


def _forbidden_operator_home() -> tuple[str, ...]:
    home = _runtime_operator_home()
    if home in PLACEHOLDER_USER_HOMES:
        return ()
    return (home,)


FORBIDDEN_PUBLIC = (
    *_forbidden_operator_home(),
    "Living Steer",
    "HOLD T-773",
    "HOLD T-774",
    "Never Grok DMs",
    "sol-agy-harness",
    "No NER",
    "Cursor-only",
    "this Mac",
    "cd ~/tickets",
    "tickets --version",
)


def _load():
    import importlib.util
    spec = importlib.util.spec_from_file_location("t1010_tickets", TOOL)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _hits(text):
    return [re.sub(r"\s+", " ", m.group(0)) for m in CMD.finditer(text or "")]


def test_user_facing_templates_teach_atm_not_tickets_subcommand():
    mod = _load()
    for name in USER_FACING_BLOBS:
        blob = getattr(mod, name)
        found = _hits(blob)
        assert found == [], "%s still teaches %s" % (name, found)
    assert "compatibility alias" in mod.WORKER_PROMPT
    assert "atm next" in mod.WORKER_PROMPT
    assert "atm sprint show" in mod.PROTOCOL
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
    assert "`atm hooks agy`" in adapters
    assert "`tickets hooks agy`" not in adapters
    join_loop = [ln for ln in src.splitlines() if ln.strip().startswith('print("Loop:')]
    assert join_loop, src
    assert all("tickets " not in ln for ln in join_loop)


def test_onboarding_docs_and_readme_teach_atm():
    for path in PUBLIC_PATHS:
        text = path.read_text(encoding="utf-8")
        found = _hits(text)
        assert found == [], "%s still teaches %s" % (path.relative_to(ROOT), found)
        for token in FORBIDDEN_PUBLIC:
            assert token not in text, "%s still contains %r" % (
                path.relative_to(ROOT), token)


def test_generated_init_files_teach_atm(tmp_path):
    repo = tmp_path / "fresh"
    repo.mkdir()
    subprocess.run(["git", "init", "-q", "-b", "main"], cwd=repo, check=True)
    env = dict(os.environ, TICKETS_DIR=str(repo / ".tickets"),
               TICKET_AGENT="t1010-init", HOME=str(tmp_path / "home"))
    env.pop("TICKETS_STOP_HOOK", None)
    (tmp_path / "home").mkdir()
    r = subprocess.run(
        [sys.executable, str(TOOL), "init"],
        capture_output=True, text=True, env=env, cwd=str(repo))
    assert r.returncode == 0, r.stderr + r.stdout
    agents = (repo / "AGENTS.md").read_text(encoding="utf-8")
    rule = (repo / ".cursor" / "rules" / "tickets.mdc").read_text(encoding="utf-8")
    for label, text in (("AGENTS.md", agents), ("tickets.mdc", rule)):
        found = _hits(text)
        assert found == [], "generated %s still teaches %s" % (label, found)
        assert "atm sprint show" in text
        assert "tickets sprint show" not in text


# Runtime connect may print discovered on-disk harness paths under the
# operator HOME. Those are not hardcoded policy; the leak is board-specific
# instruction baked into print_ceo_connect().
FORBIDDEN_CONNECT_OUTPUT = tuple(
    t for t in FORBIDDEN_PUBLIC if t not in _forbidden_operator_home()
)


def test_connect_ceo_output_has_no_forbidden_public_tokens(tmp_path):
    """Isolated PATH `atm connect --ceo` must not emit live-board operator policy."""
    repo = tmp_path / "fresh"
    repo.mkdir()
    subprocess.run(["git", "init", "-q", "-b", "main"], cwd=repo, check=True)
    bindir = tmp_path / "prefix" / "bin"
    bindir.mkdir(parents=True)
    (bindir / "atm").symlink_to(TOOL)
    (bindir / "tickets").symlink_to(TOOL)
    home = tmp_path / "home"
    home.mkdir()
    empty = tmp_path / "empty-bin"
    empty.mkdir()
    env = {
        "PATH": str(bindir) + os.pathsep + str(empty) + os.pathsep + "/usr/bin" + os.pathsep + "/bin",
        "HOME": str(home),
        "TICKETS_DIR": str(repo / ".tickets"),
        "TICKET_AGENT": "t1010-ceo",
        "PYTEST_CURRENT_TEST": "tests/test_t1010_atm_user_facing.py::test_connect_ceo_output_has_no_forbidden_public_tokens (call)",
    }
    initialized = subprocess.run(
        [str(bindir / "atm"), "init"],
        capture_output=True, text=True, env=env, cwd=str(repo))
    assert initialized.returncode == 0, initialized.stderr + initialized.stdout
    r = subprocess.run(
        [str(bindir / "atm"), "connect", "--ceo"],
        capture_output=True, text=True, env=env, cwd=str(repo))
    assert r.returncode == 0, r.stderr + r.stdout
    out = r.stdout + r.stderr
    for token in FORBIDDEN_CONNECT_OUTPUT:
        assert token not in out, "atm connect --ceo still emits %r" % token
    assert "Use board mail only." in r.stdout
    assert "stale shim" in r.stdout
    assert "atm next" not in r.stdout


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
