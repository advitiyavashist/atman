"""T-263 / T-282: `tickets init` must create AND BIND a board, or fail loudly.

The original defect: cmd_init took its write location from board_dir() -- the
AMBIENT resolution -- and rooted the protocol files at its dirname. So in a
fresh repo with TICKETS_DIR exported (every agent session has one), or nested
under a project that already has a board, init reported success while writing
four files into a DIFFERENT project and leaving every later command on the
other board. That is how T-256 minted a ticket on the live steer board.

The first fix was itself defective, and this file exists mostly because of
that: it compared init's target against the ambient board, but computed BOTH
through _repo_root(), whose docstring reads "Root of the MAIN worktree, so
every linked worktree shares one board". Inside a linked worktree the two
operands were therefore equal by construction and the refusal branch was dead
code -- in the only configuration this fleet actually runs in. A guard whose
operands come from one resolver is not a guard, so test_the_two_resolvers_
disagree_inside_a_linked_worktree below pins the independence directly, not
just its consequences.

Everything here runs the real CLI as a subprocess (test_wakeup.py /
test_t257_board_guard.py pattern) under a scrubbed env with a throwaway HOME
and no ambient TICKETS_DIR, so no live board is reachable from any of it.
Both copies of the tool are exercised: T-243's two-copy pattern means a fix
that reaches only tickets.py silently leaves src/ticket_board/cli.py broken.
"""
import importlib.util
import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
TOOLS = [ROOT / "tickets.py", ROOT / "src" / "ticket_board" / "cli.py"]
TOOL_IDS = ["tickets.py", "cli.py"]

PROTOCOL_ARTIFACTS = (".tickets", ".cursor", "AGENTS.md", ".gitignore")


def clean_env(tmp_path, **overrides):
    """No TICKETS_DIR, no TICKET_AGENT, a throwaway HOME, and no GIT_* leaks.

    PYTEST_CURRENT_TEST is kept ON PURPOSE: it arms T-257's guard, so if any
    of these cases ever resolved a board outside the temp dir the run would
    refuse loudly instead of touching something real.
    """
    e = {
        "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
        "HOME": str(tmp_path / "fake-home"),
        "PYTEST_CURRENT_TEST": "tests/test_t263_init_isolation.py::simulated (call)",
    }
    # TMPDIR must be forwarded, and the reason is worth knowing: on macOS
    # pytest's tmp_path lives under $TMPDIR (/var/folders/.../T), while
    # tempfile.gettempdir() in a subprocess WITHOUT TMPDIR returns /private/tmp.
    # Drop it and T-257's guard correctly refuses every fixture in this file as
    # "outside the system temp dir". It is not a board-resolution variable, so
    # forwarding it does not weaken the isolation these tests depend on.
    for passthrough in ("TMPDIR", "TMP", "TEMP"):
        if os.environ.get(passthrough):
            e[passthrough] = os.environ[passthrough]
    (tmp_path / "fake-home").mkdir(exist_ok=True)
    e.update(overrides)
    return e


def run(tool, cwd, *args, env=None, tmp_path=None):
    return subprocess.run([sys.executable, str(tool), *args], capture_output=True,
                          text=True, cwd=str(cwd), env=env or clean_env(tmp_path))


def git(cwd, *args):
    return subprocess.run(
        ["git", "-c", "user.email=t263@test", "-c", "user.name=t263", *args],
        cwd=str(cwd), capture_output=True, text=True, check=True)


def make_repo(path):
    path.mkdir(parents=True, exist_ok=True)
    git(path, "init", "-q", "-b", "main")
    (path / "README").write_text("t263\n")
    git(path, "add", "README")
    git(path, "commit", "-qm", "init")
    return path


def make_linked_worktree(main_repo, wt_path):
    git(main_repo, "worktree", "add", "-q", "-b", "wt", str(wt_path))
    assert (wt_path / ".git").is_file(), "a linked worktree's .git must be a FILE"
    return wt_path


def artifacts_in(path):
    return sorted(n for n in PROTOCOL_ARTIFACTS if (path / n).exists())


# --------------------------------------------------------------------------
# The structural property. This is the test that would have caught the first
# fix: it does not ask what init DID, it asks whether the guard's two operands
# can ever differ. If they cannot, every behavioural test below passes
# vacuously.
# --------------------------------------------------------------------------

@pytest.mark.parametrize("tool", TOOLS, ids=TOOL_IDS)
def test_the_two_resolvers_disagree_inside_a_linked_worktree(tool, tmp_path, monkeypatch):
    main_repo = make_repo(tmp_path / "proj")
    wt = make_linked_worktree(main_repo, tmp_path / "proj-wt")

    spec = importlib.util.spec_from_file_location("t263_tool_%s" % tool.stem, tool)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)

    monkeypatch.chdir(wt)
    init_root = mod._init_cwd_worktree_root()
    ambient_root = mod._repo_root()

    # _repo_root() is documented to return the MAIN worktree; init's resolver
    # must return the worktree cwd is literally in. Inside a linked worktree
    # those are different directories, and a guard comparing them can fire.
    assert os.path.realpath(init_root) == os.path.realpath(wt)
    assert os.path.realpath(ambient_root) == os.path.realpath(main_repo)
    assert os.path.realpath(init_root) != os.path.realpath(ambient_root)


# --------------------------------------------------------------------------
# Acceptance 1: create AND bind, or fail loudly. Never success-then-resolve-
# elsewhere.
# --------------------------------------------------------------------------

@pytest.mark.parametrize("tool", TOOLS, ids=TOOL_IDS)
def test_linked_worktree_init_refuses_and_writes_nothing(tool, tmp_path):
    """The T-282 case, and the live one: every agent here works in a linked
    worktree. Before the fix this printed success and installed the protocol
    into the sibling main checkout."""
    main_repo = make_repo(tmp_path / "proj")
    wt = make_linked_worktree(main_repo, tmp_path / "proj-wt")
    before_main, before_wt = artifacts_in(main_repo), artifacts_in(wt)

    r = run(tool, wt, "init", tmp_path=tmp_path)

    assert r.returncode != 0, "init must FAIL, not report success:\n%s" % r.stdout
    assert "REFUSING TO INIT" in (r.stdout + r.stderr)
    assert "LINKED WORKTREE" in (r.stdout + r.stderr)
    # Nothing was written -- into cwd or, crucially, into the sibling repo.
    assert artifacts_in(main_repo) == before_main
    assert artifacts_in(wt) == before_wt


@pytest.mark.parametrize("tool", TOOLS, ids=TOOL_IDS)
def test_tickets_dir_pointing_elsewhere_refuses_and_leaves_that_project_alone(tool, tmp_path):
    """The originally-filed path: TICKETS_DIR is exported in every agent
    session, so init in a fresh repo appended to the OTHER project's AGENTS.md
    and .gitignore and wrote its .cursor rule."""
    other = make_repo(tmp_path / "other")
    (other / ".tickets").mkdir()
    (other / ".tickets" / "T-042.json").write_text("{}")
    (other / "AGENTS.md").write_text("other project\n")
    fresh = make_repo(tmp_path / "fresh")
    before_other = (other / "AGENTS.md").read_text()

    r = run(tool, fresh, "init",
            env=clean_env(tmp_path, TICKETS_DIR=str(other / ".tickets")))

    assert r.returncode != 0, r.stdout
    assert "REFUSING TO INIT" in (r.stdout + r.stderr)
    assert "TICKETS_DIR" in (r.stdout + r.stderr)
    assert (other / "AGENTS.md").read_text() == before_other
    assert not (other / ".cursor").exists()
    assert artifacts_in(fresh) == []


@pytest.mark.parametrize("tool", TOOLS, ids=TOOL_IDS)
def test_nested_repo_under_an_ancestor_board_refuses(tool, tmp_path):
    """No env var involved at all: a fresh `git init` under any project that
    already has a board resolves to the ancestor via the cwd-upward walk."""
    parent = make_repo(tmp_path / "parent")
    (parent / ".tickets").mkdir()
    (parent / ".tickets" / "T-001.json").write_text("{}")
    nested = make_repo(parent / "nested")

    r = run(tool, nested, "init", tmp_path=tmp_path)

    assert r.returncode != 0, r.stdout
    assert "REFUSING TO INIT" in (r.stdout + r.stderr)
    assert artifacts_in(nested) == []


# --------------------------------------------------------------------------
# Acceptance 2: after a successful init the next create mints T-001 -- ASSERT
# THE NUMBER. A board that continues someone else's sequence is the tell that
# init bound nothing.
# --------------------------------------------------------------------------

@pytest.mark.parametrize("tool", TOOLS, ids=TOOL_IDS)
def test_plain_repo_init_binds_and_next_create_mints_T001(tool, tmp_path):
    fresh = make_repo(tmp_path / "fresh")

    r = run(tool, fresh, "init", tmp_path=tmp_path)
    assert r.returncode == 0, r.stdout + r.stderr
    assert "bound:" in r.stdout, r.stdout
    assert (fresh / ".tickets").is_dir()

    c = run(tool, fresh, "create", "first ticket", "--role", "backend",
            env=clean_env(tmp_path, TICKET_AGENT="t263-probe"))
    assert c.returncode == 0, c.stdout + c.stderr
    assert "T-001" in c.stdout, c.stdout
    assert (fresh / ".tickets" / "T-001.json").is_file()


# --------------------------------------------------------------------------
# Acceptance 3: "which board am I about to write to" is answerable BEFORE the
# write.
# --------------------------------------------------------------------------

@pytest.mark.parametrize("tool", TOOLS, ids=TOOL_IDS)
def test_target_board_is_announced_before_the_first_write(tool, tmp_path):
    main_repo = make_repo(tmp_path / "proj")
    wt = make_linked_worktree(main_repo, tmp_path / "proj-wt")

    r = run(tool, wt, "init", tmp_path=tmp_path)

    # The refusal proves the announcement came first: the target is named in
    # stdout, and the run that named it wrote nothing anywhere.
    assert "board: %s" % (wt / ".tickets") in r.stdout, r.stdout
    assert not (wt / ".tickets").exists()
    assert not (main_repo / ".tickets").exists()


@pytest.mark.parametrize("tool", TOOLS, ids=TOOL_IDS)
def test_where_answers_read_only_without_writing(tool, tmp_path):
    fresh = make_repo(tmp_path / "fresh")
    r = run(tool, fresh, "where", tmp_path=tmp_path)
    assert r.returncode == 0, r.stdout + r.stderr
    assert artifacts_in(fresh) == [], "`where` must not create anything"


# --------------------------------------------------------------------------
# The explicit escape hatch, and its honesty.
# --------------------------------------------------------------------------

@pytest.mark.parametrize("tool", TOOLS, ids=TOOL_IDS)
def test_explicit_board_writes_there_and_says_plainly_it_is_not_bound(tool, tmp_path):
    main_repo = make_repo(tmp_path / "proj")
    wt = make_linked_worktree(main_repo, tmp_path / "proj-wt")
    elsewhere = tmp_path / "chosen"

    r = run(tool, wt, "init", "--board", str(elsewhere / ".tickets"), tmp_path=tmp_path)

    assert r.returncode == 0, r.stdout + r.stderr
    assert (elsewhere / ".tickets").is_dir()
    # It wrote where it was told -- and it does NOT claim to be bound, because
    # it is not. Reporting a bind it cannot deliver is the whole defect.
    assert "NOT BOUND" in r.stdout, r.stdout
    assert "TICKETS_DIR=%s" % (elsewhere / ".tickets") in r.stdout


@pytest.mark.parametrize("tool", TOOLS, ids=TOOL_IDS)
def test_explicit_board_that_does_bind_reports_bound(tool, tmp_path):
    fresh = make_repo(tmp_path / "fresh")
    r = run(tool, fresh, "init", "--board", str(fresh / ".tickets"), tmp_path=tmp_path)
    assert r.returncode == 0, r.stdout + r.stderr
    assert "bound:" in r.stdout and "NOT BOUND" not in r.stdout, r.stdout
