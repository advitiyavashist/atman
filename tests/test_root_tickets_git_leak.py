"""T-243: git resolution must not leak to another repository from inside a worktree.

Covers the root-level tickets.py that is actually deployed (T-223's release
mechanism ships *this* file to ~/.claude/tools/tickets.py, not the newer
src/ticket_board package that T-247 isolated separately).

Three distinct failure modes are pinned here, and they fail on different
revisions -- which is the point, because the first fix for this ticket traded
one for the others:

  * THE ORIGINAL DEFECT (fails on main): an ambient GIT_DIR/GIT_COMMON_DIR
    outranks cwd during git's repo discovery, so board discovery and
    branch/sha reporting silently answer for a different repository.
  * REGRESSION 1 (fails on the first fix, passes on main): a linked worktree
    living OUTSIDE the main repo tree -- plain `git worktree add ../wt`, a
    supported layout -- must still share the main repo's board, not silently
    get a brand-new empty one.
  * REGRESSION 2 (fails on the first fix, passes on main): when resolution
    disagrees with cwd, git_state() must return None. A truthy placeholder
    fails OPEN rather than safe: it satisfies every `if not g` guard, passes
    the never-work-on-main check and passes the clean-tree check.

Plus the deliberate narrowing of the env scrub (T-259 defect 3): only Git's
*location* variables may be stripped, because the same helper builds the
fleet-launch environment for cmd_watch/cmd_spawn and the commit environment
for cmd_merge, where GIT_AUTHOR_*/GIT_COMMITTER_* must survive.

Original repro rig and the first implementation: infra-2.
"""

from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
TICKETS_PY = ROOT / "tickets.py"

LOCATION_VARS = ("GIT_DIR", "GIT_COMMON_DIR", "GIT_WORK_TREE")


def _load_tickets_module():
    spec = importlib.util.spec_from_file_location("tickets_under_test", TICKETS_PY)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture
def tickets_mod():
    return _load_tickets_module()


def _git(*args, **kw):
    return subprocess.run(["git", *args], capture_output=True, text=True, **kw)


def _init_repo(path, name, email):
    path.mkdir(parents=True, exist_ok=True)
    _git("init", "-q", "-b", "main", str(path))
    _git("-C", str(path), "config", "user.email", email)
    _git("-C", str(path), "config", "user.name", name)


@pytest.fixture
def rig(tmp_path, monkeypatch):
    """A main repo carrying the shared board, an OUT-OF-TREE linked worktree,
    and an unrelated 'decoy' repo that ambient GIT_* will be aimed at.

    The worktree is a sibling of the repo, not a child: that is the supported
    layout a naive "the resolved root must contain cwd" check wrongly rejects.
    Nothing here touches the real board -- every path is under tmp_path, and
    TICKETS_DIR is cleared so board_dir()'s own discovery is what is measured.
    """
    repo = tmp_path / "repo"
    _init_repo(repo, "t", "t@example.com")
    board = repo / ".tickets"
    board.mkdir()
    (board / "tickets.json").write_text("[]")
    (repo / "f.txt").write_text("x")
    _git("-C", str(repo), "add", "-A")
    _git("-C", str(repo), "commit", "-qm", "init")

    worktree = tmp_path / "wt"                      # OUTSIDE repo, deliberately
    _git("-C", str(repo), "worktree", "add", "-q", "-b", "feat", str(worktree))

    decoy = tmp_path / "decoy"
    _init_repo(decoy, "d", "d@example.com")
    (decoy / "d.txt").write_text("d")
    _git("-C", str(decoy), "add", "-A")
    _git("-C", str(decoy), "commit", "-qm", "decoy")
    (decoy / ".tickets").mkdir()
    (decoy / ".tickets" / "tickets.json").write_text("[]")

    for var in (*LOCATION_VARS, "TICKETS_DIR"):
        monkeypatch.delenv(var, raising=False)

    return {
        "repo": repo.resolve(),
        "worktree": worktree.resolve(),
        "decoy": decoy.resolve(),
        "board": board.resolve(),
    }


def _pollute(monkeypatch, decoy):
    """Aim git's location environment at an unrelated repository."""
    monkeypatch.setenv("GIT_DIR", str(decoy / ".git"))
    monkeypatch.setenv("GIT_COMMON_DIR", str(decoy / ".git"))


# ---------------------------------------------------------------------------
# Sanity: the rig must still reproduce the symptom at the raw-git level, or
# these tests could silently stop testing anything if git's behaviour changed.
# ---------------------------------------------------------------------------

def test_rig_reproduces_the_leak_with_raw_git(rig, monkeypatch):
    _pollute(monkeypatch, rig["decoy"])
    out = _git("rev-parse", "--abbrev-ref", "HEAD", cwd=str(rig["worktree"]))
    assert out.stdout.strip() == "main", (
        "rig no longer reproduces the leak: raw git inside the worktree should "
        "answer for the decoy (branch 'main'), got %r" % out.stdout.strip()
    )


# ---------------------------------------------------------------------------
# The original defect: ambient GIT_* must not redirect resolution.
# ---------------------------------------------------------------------------

def test_repo_root_ignores_ambient_git_dir(rig, tickets_mod, monkeypatch):
    monkeypatch.chdir(rig["worktree"])
    _pollute(monkeypatch, rig["decoy"])
    assert Path(tickets_mod._repo_root()).resolve() == rig["repo"]


def test_board_dir_does_not_leak_to_another_repos_board(rig, tickets_mod, monkeypatch):
    """The worst form of this bug: reading and WRITING another project's board."""
    monkeypatch.chdir(rig["worktree"])
    _pollute(monkeypatch, rig["decoy"])
    resolved = Path(tickets_mod.board_dir()).resolve()
    assert resolved != (rig["decoy"] / ".tickets").resolve()
    assert resolved == rig["board"]


def test_git_state_reports_the_worktrees_own_head_under_pollution(rig, tickets_mod, monkeypatch):
    monkeypatch.chdir(rig["worktree"])
    _pollute(monkeypatch, rig["decoy"])
    state = tickets_mod.git_state()
    assert state is not None
    assert state["branch"] == "feat"
    assert Path(state["top"]).resolve() == rig["worktree"]
    assert state["main_tree"] is False


def test_repo_identity_is_not_fooled_by_ambient_git_dir(rig, tickets_mod, monkeypatch):
    """T-215 records this at review time; under pollution it named the wrong repo."""
    monkeypatch.chdir(rig["worktree"])
    _pollute(monkeypatch, rig["decoy"])
    identity = tickets_mod.repo_identity(str(rig["worktree"]))
    assert identity is not None
    assert str(rig["decoy"]) not in identity
    assert Path(identity).resolve() == (rig["repo"] / ".git").resolve()


# ---------------------------------------------------------------------------
# Regression 1: an out-of-tree linked worktree must share the main board.
# ---------------------------------------------------------------------------

def test_out_of_tree_worktree_shares_the_main_board(rig, tickets_mod, monkeypatch):
    """No pollution at all -- this is the plain supported layout.

    The first fix refused any resolved root that did not contain cwd, which is
    true of every worktree created outside the repo tree. _repo_root() then
    returned None, board_dir() fell through to its cwd-walk, found the
    worktree's own .git file and returned <worktree>/.tickets: a brand-new,
    empty, silently-isolated board (the T-263 failure class).
    """
    monkeypatch.chdir(rig["worktree"])
    assert tickets_mod._repo_root() is not None, (
        "_repo_root refused a legitimate out-of-tree worktree"
    )
    assert Path(tickets_mod._repo_root()).resolve() == rig["repo"]
    assert Path(tickets_mod.board_dir()).resolve() == rig["board"]


def test_out_of_tree_worktree_shares_the_main_board_under_pollution(rig, tickets_mod, monkeypatch):
    monkeypatch.chdir(rig["worktree"])
    _pollute(monkeypatch, rig["decoy"])
    assert Path(tickets_mod.board_dir()).resolve() == rig["board"]


def test_fs_repo_link_resolves_a_linked_worktree_from_disk_alone(rig, tickets_mod, monkeypatch):
    """The filesystem cross-check must work with the environment actively lying."""
    _pollute(monkeypatch, rig["decoy"])
    root, common = tickets_mod._fs_repo_link(str(rig["worktree"]))
    assert Path(root).resolve() == rig["worktree"]
    assert Path(common).resolve() == (rig["repo"] / ".git").resolve()


# ---------------------------------------------------------------------------
# Regression 2: unresolved must mean None, so callers' guards fail safe.
# ---------------------------------------------------------------------------

@pytest.fixture
def mismatched_worktree(tmp_path):
    """core.worktree pointing somewhere that does not contain cwd.

    This is the vector that survives the env scrub, so it is how the mismatch
    branch is still reachable at all.
    """
    repo = tmp_path / "m_repo"
    _init_repo(repo, "t", "t@example.com")
    sub = repo / "sub"
    sub.mkdir()
    (sub / "f.txt").write_text("x")
    _git("-C", str(repo), "add", "-A")
    _git("-C", str(repo), "commit", "-qm", "init")
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    _git("-C", str(repo), "config", "core.worktree", str(elsewhere))
    return sub


def test_git_state_returns_none_when_resolution_disagrees_with_cwd(
    mismatched_worktree, tickets_mod, monkeypatch
):
    monkeypatch.chdir(mismatched_worktree)
    for var in LOCATION_VARS:
        monkeypatch.delenv(var, raising=False)
    assert tickets_mod.git_state() is None


def test_unresolved_state_does_not_disarm_the_sync_guards(
    mismatched_worktree, tickets_mod, monkeypatch
):
    """The consequence, spelled out as the guard expressions cmd_sync uses.

    A placeholder dict makes all three of these pass, and `tickets sync` then
    runs a real `git merge` with the never-work-on-main and clean-tree rules
    both disabled.
    """
    monkeypatch.chdir(mismatched_worktree)
    for var in LOCATION_VARS:
        monkeypatch.delenv(var, raising=False)
    g = tickets_mod.git_state()
    assert not g, "the `if not g: exit` guard in cmd_sync/cmd_review must fire"


def test_mismatch_is_still_recorded_for_the_agent_record(
    mismatched_worktree, tickets_mod, monkeypatch, tmp_path
):
    """git_state() drops the signal; checkin() must not lose it."""
    monkeypatch.chdir(mismatched_worktree)
    for var in LOCATION_VARS:
        monkeypatch.delenv(var, raising=False)
    board = tmp_path / "isolated_board"
    board.mkdir()
    tickets_mod.checkin(str(board), "test-agent")
    rec = json.loads((board / "agents" / "test-agent.json").read_text())
    assert rec["git_mismatch"] is True
    # cwd is recorded with no git resolution in its path, so it stays honest.
    assert Path(rec["cwd"]).resolve() == mismatched_worktree.resolve()


def test_checkin_records_the_worktrees_own_branch_under_pollution(
    rig, tickets_mod, monkeypatch, tmp_path
):
    monkeypatch.chdir(rig["worktree"])
    _pollute(monkeypatch, rig["decoy"])
    board = tmp_path / "isolated_board2"
    board.mkdir()
    tickets_mod.checkin(str(board), "test-agent")
    rec = json.loads((board / "agents" / "test-agent.json").read_text())
    assert rec["branch"] == "feat"
    assert rec["git_mismatch"] is False
    assert Path(rec["cwd"]).resolve() == rig["worktree"]


# ---------------------------------------------------------------------------
# The scrub must be narrow: location only, never identity or credentials.
# ---------------------------------------------------------------------------

def test_clean_git_env_strips_every_location_variable(tickets_mod):
    polluted = {var: "/somewhere" for var in tickets_mod.GIT_LOCATION_VARS}
    polluted["PATH"] = "/usr/bin"
    cleaned = tickets_mod._clean_git_env(polluted)
    for var in tickets_mod.GIT_LOCATION_VARS:
        assert var not in cleaned, "%s must not survive the scrub" % var
    assert cleaned["PATH"] == "/usr/bin"


@pytest.mark.parametrize(
    "var",
    [
        "GIT_AUTHOR_NAME",
        "GIT_AUTHOR_EMAIL",
        "GIT_COMMITTER_NAME",
        "GIT_COMMITTER_EMAIL",
        "GIT_SSH_COMMAND",
        "GIT_ASKPASS",
        "GIT_TERMINAL_PROMPT",
        "GIT_EDITOR",
    ],
)
def test_clean_git_env_preserves_identity_and_credential_variables(tickets_mod, var):
    """Not cosmetic: this same helper builds the cmd_watch/cmd_spawn
    fleet-launch environment and cmd_merge's commit environment. Stripping
    every GIT_* key there loses commit authorship for the whole fleet -- the
    same class of attribution loss as T-238 (T-259 defect 3).
    """
    cleaned = tickets_mod._clean_git_env({var: "value", "GIT_DIR": "/leak"})
    assert cleaned.get(var) == "value"
    assert "GIT_DIR" not in cleaned


def test_clean_git_env_does_not_mutate_the_source(tickets_mod):
    source = {"GIT_DIR": "/leak", "PATH": "/usr/bin"}
    tickets_mod._clean_git_env(source)
    assert source["GIT_DIR"] == "/leak"
