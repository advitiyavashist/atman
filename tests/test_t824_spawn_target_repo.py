"""T-824: explicit --repo/--base for cross-repo spawn; unique hook identity."""

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from watch_reaper import collect_watch_pids_from_board

ROOT = Path(__file__).resolve().parents[1]
TOOL = ROOT / "tickets.py"
GIT_ENV = dict(os.environ, GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t@t",
               GIT_COMMITTER_NAME="t", GIT_COMMITTER_EMAIL="t@t")

STEER_ORIGIN = "https://github.com/advitiyavashist/steer.git"
ATMAN_ORIGIN = "https://github.com/advitiyavashist/atman.git"


def run(board, *args, agent="operator", cwd=None, env=None):
    e = dict(os.environ, TICKETS_DIR=str(board), TICKET_AGENT=agent,
             HOME=str(Path(board).resolve().parent.parent / "home"))
    if env:
        e.update(env)
    where = cwd or Path(board).parent
    r = subprocess.run([sys.executable, str(TOOL), *args], capture_output=True,
                       text=True, env=e, cwd=str(where))
    if args and args[0] == "spawn" and "--stop" not in args and "--list" not in args:
        collect_watch_pids_from_board(board)
    return r


def init_repo(path, origin, files=None):
    path.mkdir(parents=True)
    subprocess.run(["git", "init", "-q", "-b", "main", str(path)], check=True)
    if files:
        for rel, text in files.items():
            dest = path / rel
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_text(text)
            subprocess.run(["git", "-C", str(path), "add", "--", rel], check=True)
    subprocess.run(["git", "-C", str(path), "commit", "-q", "--allow-empty", "-m", "init"],
                   check=True, env=GIT_ENV)
    subprocess.run(["git", "-C", str(path), "remote", "add", "origin", origin], check=True)
    subprocess.run(["git", "-C", str(path), "update-ref", "refs/remotes/origin/main", "HEAD"],
                   check=True)
    return path


def agent_record(board, name):
    return json.loads((board / "agents" / (name + ".json")).read_text())


def common_dir(path):
    raw = subprocess.check_output(
        ["git", "-C", str(path), "rev-parse", "--git-common-dir"], text=True).strip()
    if not os.path.isabs(raw):
        raw = os.path.normpath(os.path.join(path, raw))
    return os.path.realpath(raw)


@pytest.fixture
def pair(tmp_path):
    (tmp_path / "home").mkdir()
    steer = init_repo(tmp_path / "steer", STEER_ORIGIN)
    atman = init_repo(tmp_path / "atman", ATMAN_ORIGIN, {
        ".cursor/hooks/tickets-board.py": (
            "#!/usr/bin/env python3\n"
            "AGENT = 'cursor'\n"
            "# Cursor hook installed by `tickets hooks cursor`\n"
        ),
        ".cursor/hooks.json": json.dumps({
            "version": 1,
            "hooks": {"sessionStart": [{"command": "tickets-board.py --agent cursor"}]},
        }) + "\n",
        ".claude/settings.json": json.dumps({"from": "atman"}) + "\n",
    })
    (steer / ".claude").mkdir()
    (steer / ".claude" / "settings.json").write_text(json.dumps({"from": "steer"}))
    board = steer / ".tickets"
    assert run(board, "create", "Work", "--role", "docs").returncode == 0
    return board, steer, atman


def test_same_repo_spawn_still_defaults_under_board_parent(tmp_path):
    (tmp_path / "home").mkdir()
    repo = init_repo(tmp_path / "repo", ATMAN_ORIGIN)
    board = repo / ".tickets"
    assert run(board, "create", "Work", "--role", "docs").returncode == 0
    r = run(board, "spawn", "local-seat", "--exec", "true", "--every", "3600",
            "--max-runs", "1")
    assert r.returncode == 0, r.stderr + r.stdout
    wt = repo / ".worktrees" / "local-seat"
    assert wt.is_dir()
    assert os.path.realpath(common_dir(wt)).startswith(os.path.realpath(repo))
    origin = subprocess.check_output(
        ["git", "-C", str(wt), "remote", "get-url", "origin"], text=True).strip()
    assert origin == ATMAN_ORIGIN
    run(board, "spawn", "local-seat", "--stop")


def test_cross_repo_worktree_without_repo_fails_closed(pair):
    board, steer, atman = pair
    guessed = atman / ".worktrees" / "oops"
    r = run(board, "spawn", "oops", "--worktree", str(guessed),
            "--exec", "true", "--every", "3600", "--max-runs", "1")
    assert r.returncode != 0
    text = r.stderr + r.stdout
    assert "cross-repo spawn is ambiguous" in text
    assert "advitiyavashist/atman" in text
    assert "advitiyavashist/steer" in text
    assert not guessed.exists()


def test_cross_repo_spawn_uses_target_repo_and_pins_unique_hooks(pair):
    board, steer, atman = pair
    r = run(board, "spawn", "atman-worker",
            "--repo", str(atman), "--base", "origin/main",
            "--harness", "cursor", "--exec", "true",
            "--every", "3600", "--max-runs", "1")
    assert r.returncode == 0, r.stderr + r.stdout
    wt = atman / ".worktrees" / "atman-worker"
    assert wt.is_dir()
    assert (steer / ".worktrees" / "atman-worker").exists() is False
    origin = subprocess.check_output(
        ["git", "-C", str(wt), "remote", "get-url", "origin"], text=True).strip()
    assert origin == ATMAN_ORIGIN
    common = common_dir(wt)
    assert common.startswith(os.path.realpath(atman) + os.sep) or common == os.path.realpath(atman / ".git")
    assert os.path.realpath(steer) not in common
    hook = (wt / ".cursor" / "hooks" / "tickets-board.py").read_text()
    assert "AGENT = 'atman-worker'" in hook
    assert "AGENT = 'cursor'" not in hook
    rec = agent_record(board, "atman-worker")
    assert os.path.realpath(rec["worktree"]) == os.path.realpath(wt)
    wf = json.loads((board / "workforce.json").read_text())
    assert wf["atman-worker"]["expected_origin"] == "advitiyavashist/atman"
    assert os.path.realpath(wf["atman-worker"]["spawn_repo"]) == os.path.realpath(atman)
    inherited = json.loads((wt / ".claude" / "settings.json").read_text())
    assert inherited["from"] == "atman"
    assert "target repo" in r.stdout and "origin/main" in r.stdout
    assert "hooks pinned to atman-worker" in r.stdout
    run(board, "spawn", "atman-worker", "--stop")


def test_repo_slug_finds_sibling_checkout(pair):
    board, steer, atman = pair
    r = run(board, "spawn", "slug-worker",
            "--repo", "advitiyavashist/atman",
            "--harness", "cursor", "--exec", "true",
            "--every", "3600", "--max-runs", "1")
    assert r.returncode == 0, r.stderr + r.stdout
    wt = atman / ".worktrees" / "slug-worker"
    assert wt.is_dir()
    hook = (wt / ".cursor" / "hooks" / "tickets-board.py").read_text()
    assert "AGENT = 'slug-worker'" in hook
    run(board, "spawn", "slug-worker", "--stop")


def test_shared_checkout_with_foreign_hooks_is_refused(pair):
    board, steer, atman = pair
    planted = run(board, "hooks", "cursor", "--agent", "cursor", "--worktree", str(atman),
                  "--force")
    assert planted.returncode == 0, planted.stderr
    r = run(board, "spawn", "intruder",
            "--repo", str(atman), "--worktree", str(atman),
            "--harness", "cursor", "--exec", "true",
            "--every", "3600", "--max-runs", "1")
    assert r.returncode != 0
    assert "hooks already pin cursor" in (r.stderr + r.stdout)


def test_worktree_under_wrong_repo_flag_is_refused(pair):
    board, steer, atman = pair
    r = run(board, "spawn", "mismatch",
            "--repo", str(atman),
            "--worktree", str(steer / ".worktrees" / "mismatch"),
            "--exec", "true", "--every", "3600", "--max-runs", "1")
    assert r.returncode != 0
    assert "repo_mismatch" in (r.stderr + r.stdout)


def test_docs_teach_repo_contract_and_misbound_repair():
    howto = (ROOT / "docs" / "onboarding" / "master-howto.md").read_text()
    guide = (ROOT / "docs" / "t824-spawn-target-repo.md").read_text()
    byoa = (ROOT / "docs" / "byoa.md").read_text()
    assert "spawn --repo" in howto or "--repo /path/to/atman" in howto
    assert "identity-pinned hooks" in howto
    assert "worktree remove" in guide
    assert "advitiyavashist/atman" in guide
    assert "t824-spawn-target-repo.md" in byoa
