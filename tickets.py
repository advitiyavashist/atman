#!/usr/bin/env python3
"""A tiny file-based ticket board for coordinating AI coding agents.

Tool-neutral on purpose: Claude Code, Codex and Cursor all just shell out to
this script, so they share one board.

One JSON file per ticket, so parallel agents never fight over a single file.
Claiming is atomic (O_EXCL lock file), so two agents can never take the same
ticket even if they call `next` at the same instant.

Board location, in order of preference:
  $TICKETS_DIR
  nearest ancestor with a live .tickets/ board
  git worktree/root .tickets
  the only live .tickets/ in a child directory (so `tickets next` from a
  parent folder like Downloads still finds the project)
  cwd/.tickets

`tickets board` does not scan children — SessionStart hooks stay silent in
folders that are not the project. `tickets next` / `show` / `done` do.

Agent identity comes from $TICKET_AGENT (set it per tool: claude, codex, cursor).
Default roles for those names can be overridden by .tickets/roles.json.
"""

import argparse
import errno
import glob
import hashlib
import json
import os
import re
import shlex
import sys
import threading
import uuid
from datetime import datetime, timezone

# Immutable releases verify every shipped byte before dispatch.  Do not add
# interpreter-generated files under the verified package tree after that
# check, or the next invocation would correctly report an unexpected file.
if os.path.isfile(os.path.join(os.path.dirname(os.path.realpath(__file__)),
                               "release.json")):
    sys.dont_write_bytecode = True

STATUSES = ("open", "claimed", "review", "blocked", "done")
LABEL = {"open": "TO DO", "claimed": "IN PROGRESS", "review": "IN REVIEW",
         "blocked": "BLOCKED", "done": "DONE"}
# words agents may type for `tickets status <id> <word>`
STATUS_WORDS = {
    "todo": "open", "to-do": "open", "open": "open", "unassigned": "open", "backlog": "open",
    "in-progress": "claimed", "inprogress": "claimed", "progress": "claimed", "wip": "claimed",
    "doing": "claimed", "claimed": "claimed", "started": "claimed",
    "review": "review", "in-review": "review", "pr": "review", "ready": "review", "submitted": "review",
    "blocked": "blocked", "stuck": "blocked",
    "done": "done", "merged": "done", "closed": "done", "complete": "done",
}
UPDATE_EVERY_MIN = 45  # agents must post `tickets update` at least this often

DEFAULT_ROLES = {
    "codex": ["console"],
    "claude": ["backend"],
    "cursor": ["verification", "acceptance"],
    "grok": [],
}

# T-809: one implementation, two PATH names. atm is the public CLI; tickets
# is the compatibility alias. Behavior, board, and exit codes must not fork.
PRIMARY_CLI_NAME = "atm"
COMPAT_CLI_NAME = "tickets"


def cli_prog(argv=None):
    """argparse/help/error name from how this process was invoked."""
    raw = (argv if argv is not None else sys.argv) or [""]
    base = os.path.basename(str(raw[0]).replace("\\", "/"))
    stem = os.path.splitext(base)[0].lower()
    if stem == COMPAT_CLI_NAME:
        return COMPAT_CLI_NAME
    if stem == PRIMARY_CLI_NAME:
        return PRIMARY_CLI_NAME
    return PRIMARY_CLI_NAME


# --------------------------------------------------------------------------
# board location + io
# --------------------------------------------------------------------------

def _live_board(path):
    return os.path.isdir(path) and bool(glob.glob(os.path.join(path, "T-*.json")))


def child_boards(cwd):
    found = []
    try:
        for name in os.listdir(cwd):
            path = os.path.join(cwd, name, ".tickets")
            if _live_board(path):
                found.append(os.path.abspath(path))
    except OSError:
        pass
    return found


# Git's *location* environment: the variables that override repo discovery and
# make git answer for a repository other than the one cwd is standing in. This
# is deliberately NOT "every GIT_* key" -- see _clean_git_env below.
GIT_LOCATION_VARS = (
    "GIT_DIR",
    "GIT_COMMON_DIR",
    "GIT_WORK_TREE",
    "GIT_INDEX_FILE",
    "GIT_OBJECT_DIRECTORY",
    "GIT_ALTERNATE_OBJECT_DIRECTORIES",
    "GIT_CEILING_DIRECTORIES",
    "GIT_DISCOVERY_ACROSS_FILESYSTEM",
    "GIT_NAMESPACE",
    "GIT_PREFIX",
)


def _clean_git_env(environ=None):
    """Copy an environment without inherited Git *location* overrides (T-243).

    GIT_DIR/GIT_COMMON_DIR/GIT_WORK_TREE and friends take priority over cwd
    during git's repo discovery, so a subprocess that inherits them silently
    answers for whatever repo they name instead of the caller's own cwd. Once
    such a value is forwarded into a spawned/exec'd child it cascades to every
    agent the watcher launches, which is why the wrong repo tracked "whichever
    worktree was most recently active globally" rather than any one agent.

    Scope, deliberately narrow: only the
    location family is removed. GIT_AUTHOR_*/GIT_COMMITTER_* must survive --
    this env is also handed to `cmd_watch`/`cmd_spawn` as the launch
    environment, and stripping identity there is the same class of attribution
    loss. GIT_SSH_COMMAND/GIT_ASKPASS/GIT_TERMINAL_PROMPT likewise
    survive so credential helpers keep working. None of those can redirect
    which repository git resolves, so none of them is this bug's mechanism.

    Always pair with an explicit cwd.
    """
    source = os.environ if environ is None else environ
    return {k: v for k, v in source.items() if k not in GIT_LOCATION_VARS}


def _fs_repo_link(start):
    """Resolve (worktree_root, shared .git dir) for `start` from the FILESYSTEM.

    No environment variable can redirect this, which is exactly the point: it
    is ground truth to cross-check git's own answer against. Handles both a
    normal checkout (.git is a directory) and a linked worktree (.git is a
    file containing "gitdir: <common>/worktrees/<name>"), including a worktree
    that lives OUTSIDE the main repo tree -- a supported layout that a naive
    "the root must contain cwd" test wrongly rejects (T-259 defect 1).
    Returns (None, None) when cwd is not inside a work tree at all.
    """
    d = os.path.realpath(start)
    while True:
        cand = os.path.join(d, ".git")
        if os.path.isdir(cand):
            return d, os.path.realpath(cand)
        if os.path.isfile(cand):
            try:
                with open(cand, encoding="utf-8", errors="replace") as fh:
                    line = fh.read().strip()
            except OSError:
                return None, None
            if not line.startswith("gitdir:"):
                return None, None
            gitdir = os.path.realpath(os.path.join(d, line.split(":", 1)[1].strip()))
            parent = os.path.dirname(gitdir)
            if os.path.basename(parent) == "worktrees":
                return d, os.path.realpath(os.path.dirname(parent))
            return d, gitdir
        parent = os.path.dirname(d)
        if parent == d:
            return None, None
        d = parent


def _repo_root():
    """Root of the MAIN worktree, so every linked worktree shares one board.

    Explicit cwd + a scrubbed env (T-243): without them an ambient GIT_DIR or
    GIT_COMMON_DIR silently reroutes board discovery to a DIFFERENT project's
    board -- strictly worse than the misreported location this ticket was
    filed for, because it means reading and writing another repo's tickets.

    The result is then cross-checked against the filesystem. On disagreement
    the FILESYSTEM WINS: it cannot be redirected by the environment, so it
    recovers the correct shared board instead of refusing and letting
    board_dir() fall through to a brand-new empty one (T-259 defect 1).
    """
    import subprocess
    here = os.getcwd()
    fs_root, fs_common = _fs_repo_link(here)
    try:
        out = subprocess.run(["git", "rev-parse", "--git-common-dir"],
                             capture_output=True, text=True, timeout=5,
                             cwd=here, env=_clean_git_env())
    except (OSError, subprocess.TimeoutExpired):
        out = None
    common = None
    if out is not None and out.returncode == 0:
        common = os.path.realpath(os.path.join(here, out.stdout.strip()))

    if fs_common is not None and common is not None and fs_common != common:
        sys.stderr.write(
            "tickets: git resolved its common dir to %s but the filesystem says cwd %s "
            "belongs to %s -- trusting the filesystem (T-243)\n" % (common, here, fs_common))
        common = fs_common
    elif common is None:
        common = fs_common
    if common is None:
        return None
    if os.path.basename(common) != ".git":
        return None  # bare repo or unusual layout
    return os.path.dirname(common)


def _init_cwd_worktree_root(start=None):
    """Root of the worktree cwd is LITERALLY in -- deliberately NOT _repo_root().

    _repo_root() resolves `--git-common-dir` and therefore returns the MAIN
    worktree: correct for board_dir(), because every linked worktree of a
    project is meant to share one board.  For `init` that is the defect
    (T-263/T-282).  It made init's "where am I about to write" answer
    identical BY CONSTRUCTION to the ambient "where will this resolve later"
    answer, so the refuse-on-disagreement check could never fire inside a
    linked worktree -- a common configuration for parallel agents.
    A guard whose two operands come out of the same resolver is not a guard.

    So this function shares no code path with _repo_root().  The filesystem
    walk is the answer: the first ancestor holding a `.git` entry, which no
    environment variable can redirect.  git's own `--show-toplevel` is asked
    only as a cross-check, and on disagreement the filesystem wins (T-243).
    Returns None when cwd is not inside a git worktree at all.
    """
    import subprocess
    here = os.path.realpath(start if start is not None else os.getcwd())
    fs_root = None
    d = here
    while True:
        if os.path.exists(os.path.join(d, ".git")):
            fs_root = d
            break
        parent = os.path.dirname(d)
        if parent == d:
            break
        d = parent
    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    git_root = None
    try:
        out = subprocess.run(["git", "rev-parse", "--show-toplevel"],
                             capture_output=True, text=True, timeout=5,
                             cwd=here, env=env)
        if out.returncode == 0 and out.stdout.strip():
            git_root = os.path.realpath(out.stdout.strip())
    except (OSError, subprocess.TimeoutExpired):
        pass
    if fs_root is not None and git_root is not None and fs_root != git_root:
        sys.stderr.write(
            "tickets: git says cwd %s is in worktree %s but the filesystem says %s "
            "-- trusting the filesystem (T-243)\n" % (here, git_root, fs_root))
    if fs_root is not None:
        return fs_root
    return git_root


def _same_board(a, b):
    if a is None or b is None:
        return False
    return os.path.realpath(a) == os.path.realpath(b)


def _init_resolve_board(a):
    """(target, why): where `init` will write, decided from cwd alone."""
    explicit = getattr(a, "board", None)
    if explicit:
        return os.path.abspath(os.path.expanduser(explicit)), "an explicit --board"
    root = _init_cwd_worktree_root()
    if root is None:
        return (os.path.join(os.path.realpath(os.getcwd()), ".tickets"),
                "the current directory (not inside a git worktree)")
    return os.path.join(root, ".tickets"), "the git worktree cwd is in (%s)" % root


def _init_disagreement_cause(ambient):
    """Plain-language reason the ambient board is not the cwd board."""
    env = os.environ.get("TICKETS_DIR")
    if env:
        return ("TICKETS_DIR is set to %r, and board resolution honours it "
                "before anything on disk." % env)
    here = _init_cwd_worktree_root()
    main = _repo_root()
    if here and main and os.path.realpath(here) != os.path.realpath(main):
        return ("%s is a LINKED WORKTREE whose main worktree is %s, and every "
                "linked worktree deliberately shares the main worktree's "
                "board -- that sharing is the design, not a bug, so init "
                "must not quietly fork a second board here." % (here, main))
    return ("an ancestor directory already holds a board (%s) and the "
            "upward search finds it before this one." % os.path.dirname(ambient))


def _init_refusal(target, ambient):
    return (
        "REFUSING TO INIT: nothing was written.\n"
        "  would write to: %s\n"
        "  but `tickets` run from %s resolves to: %s\n"
        "  because %s\n"
        "\n"
        "Writing anyway is the T-263 defect: init would report success, install "
        "the protocol files into a DIFFERENT project, and leave every later "
        "command on the other board (that is how T-256 minted a ticket on a "
        "live board). Pick one:\n"
        "  * bind this directory:      export TICKETS_DIR=%s\n"
        "  * install into the board\n"
        "    that is actually in effect: tickets init --board %s\n"
        "  * run init in %s instead\n"
        % (target, os.getcwd(), ambient, _init_disagreement_cause(ambient),
           target, ambient, os.path.dirname(ambient))
    )


def _join_cwd_board():
    """Board sitting in cwd's worktree root (for the isdir probe only).

    Not the ambient join target -- that comes from _join_ambient_board(), which
    uses the same board_dir() resolver with TICKETS_DIR stripped (T-494)."""
    root = _init_cwd_worktree_root()
    base = root if root is not None else os.path.realpath(os.getcwd())
    return os.path.join(base, ".tickets")


def _join_ambient_board():
    """Board join would resolve to with the same resolver but TICKETS_DIR unset."""
    saved = os.environ.pop("TICKETS_DIR", None)
    try:
        return _board_dir_uncached()
    finally:
        if saved is not None:
            os.environ["TICKETS_DIR"] = saved


def _refuse_join_tickets_dir_shadow(board):
    """Refuse a join whose cwd has its own .tickets that $TICKETS_DIR is shadowing.

    board_dir() honours $TICKETS_DIR unconditionally (T-424): an agent that
    mkdir's an isolated .tickets/ to probe the tool and then runs `join` from
    there registers a real seat on whatever board TICKETS_DIR names instead,
    with nothing in the join output to say so -- that is how opus-authz's
    probe seat 'zed' ended up on the live production board. `init` already
    refuses this shape (T-263); join gets the same treatment rather than a
    second, different resolution rule for the same tool.

    Compare the TICKETS_DIR-resolved board (``board``) against the same
    resolver with TICKETS_DIR stripped (T-494 C1). Do not compare against
    _join_cwd_board(): inside a linked worktree that holds its own real
    .tickets, that path disagrees with board_dir() even when TICKETS_DIR
    names the board join would have used anyway.
    """
    env = os.environ.get("TICKETS_DIR")
    if not env:
        return
    cwd_board = _join_cwd_board()
    if not os.path.isdir(cwd_board):
        return
    ambient = _join_ambient_board()
    if _same_board(board, ambient):
        return
    sys.exit(
        "REFUSING TO JOIN: nothing was written.\n"
        "  a .tickets directory exists here: %s\n"
        "  TICKETS_DIR=%r makes `tickets` resolve to:  %s\n"
        "  unsetting TICKETS_DIR would resolve to:  %s\n"
        "\n"
        "join with TICKETS_DIR set would register a real seat on the second "
        "path, not the board you get by unsetting TICKETS_DIR -- that is the "
        "T-424 defect (two agents hit it inside half an hour). Pick one:\n"
        "  * join the board TICKETS_DIR names (most agents want this): "
        "remove or ignore the stray %s\n"
        "  * join without TICKETS_DIR instead: unset TICKETS_DIR, then re-run "
        "join (resolves to %s)\n"
        % (cwd_board, env, board, ambient, cwd_board, ambient)
    )
# <unistd.h>. os.confstr_names carries no name for it on CPython, so the
# integer is the only way to ask; guarded by try/except for every non-Darwin
# platform, where it simply does not exist.
_CS_DARWIN_USER_TEMP_DIR = 65537


def _trusted_tmp_roots():
    """Temp roots resolved from the PLATFORM, never from the environment.

    T-273: this guard used to compare against tempfile.gettempdir(), which
    honours $TMPDIR -- so the guard's own safety boundary was settable by the
    very caller it exists to police. Reproduced read-only against the real
    board before any of this was written:

        cd <steer> && env -i PATH=... HOME=... \
            PYTEST_CURRENT_TEST="fake::test (call)" TMPDIR=/Users/<operator>/Downloads \
            python3 tickets.py board --quiet

    printed the full live 200+-ticket board, exit 0, no refusal; the identical
    call without the TMPDIR override refused correctly. Had the subcommand
    been `create`, that is T-256 happening again.

    The non-obvious part, and the reason the ticket's own first suggested fix
    (strip TMPDIR, call gettempdir()) is WRONG: on macOS pytest's tmp_path
    lives under the per-user /var/folders/<...>/T that TMPDIR normally points
    at, while gettempdir() with TMPDIR stripped returns /private/tmp.
    Requiring /private/tmp would refuse every legitimate run in this suite.
    confstr(_CS_DARWIN_USER_TEMP_DIR) yields that same /var/folders root and
    reads it from the kernel, not from the environment.

    There is deliberately NO name-based escape valve here. An earlier draft
    trusted any path containing a `pytest-of-*` component on the reasoning
    that pytest creates that name rather than reading it from the env. True of
    pytest, and irrelevant: any process can mkdir that name anywhere, so
    `mkdir pytest-of-evil` silently disabled the guard for everything beneath
    it. That is the same failure this ticket is about -- a guard certifying an
    untrusted path as safe -- merely relocated from an env var to a directory
    name. An opt-out spelled as a filename is still an opt-out (T-261).
    """
    roots = []
    try:
        darwin_tmp = os.confstr(_CS_DARWIN_USER_TEMP_DIR)
    except (ValueError, OSError, AttributeError):
        darwin_tmp = None
    if darwin_tmp:
        roots.append(darwin_tmp)
    roots.extend(("/tmp", "/private/tmp", "/var/tmp", "/private/var/tmp", "/usr/tmp"))
    out = []
    for r in roots:
        try:
            real = os.path.realpath(r)
        except OSError:
            continue
        if os.path.isdir(real) and real not in out:
            out.append(real)
    if not out:
        # Non-POSIX (Windows): none of the fixed roots exist and there is no
        # confstr. Fall back to gettempdir() rather than refusing every board
        # and breaking the suite outright -- a weaker boundary there is better
        # than a guard that makes the tool unusable.
        import tempfile
        out.append(os.path.realpath(tempfile.gettempdir()))
    return out


def _refuse_board_outside_pytest_tmp(path):
    """Refuse a board path that escapes the pytest temp dir.

    Root cause -- board_dir() prefers $TICKETS_DIR unconditionally, and a
    subprocess a test forgets to sandbox inherits that ambient value. pytest
    sets PYTEST_CURRENT_TEST for the life of every test, and pytest's own
    tmp_path/tmpdir fixtures always live under the system temp dir, so that
    combination is a reliable signal a board resolution is about to escape
    its sandbox. Fail loud instead of writing -- a silently-wrong resolution
    here is indistinguishable from a real board write after the fact."""
    if not os.environ.get("PYTEST_CURRENT_TEST"):
        return
    real = os.path.realpath(path)
    roots = _trusted_tmp_roots()
    for root in roots:
        if real == root or real.startswith(root + os.sep):
            return
    sys.exit(
        "REFUSING TO USE BOARD %r: running under pytest (PYTEST_CURRENT_TEST "
        "is set) but this board resolved outside every trusted temp root "
        "(%s). This looks like a test about to read or write a real board "
        "instead of an isolated tmp_path fixture -- see T-257. Make sure "
        "TICKETS_DIR points at a tmp_path (and that any subprocess.run() call "
        "passes env= explicitly rather than inheriting the ambient "
        "environment). Those roots come from the platform, not from $TMPDIR: "
        "exporting TMPDIR cannot widen them, and a directory merely NAMED "
        "'pytest-of-*' outside them is not trusted either (T-273)."
        % (real, ", ".join(roots))
    )


def _cwd_belongs_to_board(board_path):
    """True when cwd is this board's checkout (in-tree dir or linked worktree).

    T-409 / zed class: a cwd with no board of its own must not silently write
    a live board discovered as a unique child (Downloads -> project) or via
    any other unbound resolution. Out-of-tree `git worktree add` still belongs
    because `_repo_root()` is the main checkout that owns the board (T-243).
    """
    board = os.path.realpath(board_path)
    board_root = os.path.dirname(board)
    cwd = os.path.realpath(os.getcwd())
    if cwd == board_root or cwd.startswith(board_root + os.sep):
        return True
    root = _repo_root()
    if root and os.path.realpath(root) == board_root:
        return True
    here = _init_cwd_worktree_root()
    if here and os.path.realpath(os.path.join(here, ".tickets")) == board:
        return True
    return False


def _refuse_unbound_live_board(path):
    """Refuse a live board cwd does not belong to, unless TICKETS_DIR binds it.

    T-409: a mktemp / parent-folder cwd with no explicit TICKETS_DIR resolved
    the shared live board (zed / T-331). Fail closed. An empty local
    cwd/.tickets is not live, so init/join in a fresh dir still works.
    """
    if os.environ.get("TICKETS_DIR"):
        return
    if not _live_board(path):
        return
    if _cwd_belongs_to_board(path):
        return
    real = os.path.realpath(path)
    sys.exit(
        "REFUSING TO USE BOARD %r: cwd %r is not inside a board checkout "
        "and TICKETS_DIR is unset. Binding a live board from an unbound "
        "directory is the zed/T-331 class (a sandbox writing the shared "
        "board). Export TICKETS_DIR to the board you mean, or run from "
        "that project's checkout."
        % (real, os.path.realpath(os.getcwd()))
    )


def board_dir(discover_children=True):
    result = _board_dir_uncached(discover_children)
    _refuse_board_outside_pytest_tmp(result)
    _refuse_unbound_live_board(result)
    return result


def _board_dir_uncached(discover_children=True):
    env = os.environ.get("TICKETS_DIR")
    if env:
        return os.path.abspath(os.path.expanduser(env))
    root = _repo_root()
    if root:
        shared = os.path.join(root, ".tickets")
        if _live_board(shared) or os.path.isdir(shared) or root != os.getcwd():
            return shared
    d = os.getcwd()
    git_root = None
    while True:
        tickets = os.path.join(d, ".tickets")
        if _live_board(tickets) or os.path.isdir(tickets):
            if _live_board(tickets) or os.path.isdir(os.path.join(d, ".git")) or os.path.isfile(
                os.path.join(d, ".git")
            ):
                return os.path.abspath(tickets)
        git = os.path.join(d, ".git")
        if git_root is None and (os.path.isdir(git) or os.path.isfile(git)):
            git_root = d
        parent = os.path.dirname(d)
        if parent == d:
            break
        d = parent
    if git_root:
        return os.path.join(git_root, ".tickets")
    if discover_children:
        kids = child_boards(os.getcwd())
        if len(kids) == 1:
            return kids[0]
    return os.path.join(os.getcwd(), ".tickets")


def whoami(explicit=None):
    return explicit or os.environ.get("TICKET_AGENT") or "agent-%d" % os.getpid()


def now():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def ticket_path(board, tid):
    return os.path.join(board, tid + ".json")


def load(board, tid):
    try:
        with open(ticket_path(board, tid)) as f:
            return json.load(f)
    except FileNotFoundError:
        sys.exit("no such ticket: %s" % tid)


def save(board, t):
    t["updated"] = now()
    path = ticket_path(board, t["id"])
    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        json.dump(t, f, indent=2)
    os.replace(tmp, path)  # atomic
    return t


def load_all(board):
    out = []
    for path in sorted(glob.glob(os.path.join(board, "T-*.json"))):
        try:
            with open(path) as f:
                out.append(json.load(f))
        except (ValueError, IOError):
            continue
    return out


def load_roles(board):
    path = os.path.join(board, "roles.json")
    roles = dict((k, list(v)) for k, v in DEFAULT_ROLES.items())
    if os.path.isfile(path):
        try:
            with open(path) as f:
                extra = json.load(f)
            if isinstance(extra, dict):
                for k, v in extra.items():
                    if isinstance(v, list):
                        roles[k] = [str(x) for x in v]
        except (ValueError, IOError):
            pass
    return roles


def roles_for(board, owner, explicit=None):
    if explicit:
        return [r.strip() for r in explicit.split(",") if r.strip()]
    mapped = load_roles(board).get(owner)
    if mapped is not None:
        return mapped
    return None  # None = any role


def workforce_path(board):
    return os.path.join(board, "workforce.json")


def load_workforce(board):
    """{agent: {tool, can:[capabilities], cost: low|medium|high, best_for}}"""
    try:
        with open(workforce_path(board)) as f:
            w = json.load(f)
        return w if isinstance(w, dict) else {}
    except (IOError, ValueError):
        return {}


def save_workforce(board, w):
    os.makedirs(board, exist_ok=True)
    tmp = workforce_path(board) + ".tmp"
    with open(tmp, "w") as f:
        json.dump(w, f, indent=2)
    os.replace(tmp, workforce_path(board))


def can_do(board, owner, ticket):
    """False if the ticket declares needs this agent has not registered."""
    needs = ticket.get("needs") or []
    if not needs:
        return True
    have = set(load_workforce(board).get(owner, {}).get("can", []))
    return all(n in have for n in needs)


def cost_rank(board, owner):
    return {"low": 0, "medium": 1, "high": 2}.get(
        load_workforce(board).get(owner, {}).get("cost", "medium"), 1)


def context_paths(board, owner=None):
    """Briefing files an agent should read after a claim. Per-agent briefs live
    in .tickets/briefs/<agent>.md and come first when present."""
    paths = []
    cands = []
    if owner:
        cands.append(os.path.join(board, "briefs", owner + ".md"))
    cands += [
        os.path.join(board, "MASTER.md"),
        os.path.join(board, "CONTEXT.md"),
        os.path.join(os.path.dirname(board), "docs", "handoffs", "AGENT_CONTEXT.md"),
    ]
    for p in cands:
        if os.path.isfile(p) and p not in paths:
            paths.append(p)
    return paths


def _alloc(directory, prefix, width, record):
    """Write `record` under the next free `<prefix>-NNN` id. Race-safe via O_EXCL."""
    os.makedirs(directory, exist_ok=True)
    used = []
    for path in glob.glob(os.path.join(directory, prefix + "-*.json")):
        stem = os.path.basename(path)[len(prefix) + 1:-5]
        if stem.isdigit():
            used.append(int(stem))
    n = max(used) + 1 if used else 1
    while True:
        rid = "%s-%0*d" % (prefix, width, n)
        path = os.path.join(directory, rid + ".json")
        try:
            fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except OSError as e:
            if e.errno != errno.EEXIST:
                raise
            n += 1
            continue
        record["id"] = rid
        with os.fdopen(fd, "w") as f:
            json.dump(record, f, indent=2)
        return record


def create(board, title, body="", role="", deps=None, priority=2, epic="", sprint="", needs=None):
    return _alloc(board, "T", 3, {
        "title": title,
        "body": body,
        "role": role,
        "status": "open",
        "deps": deps or [],
        "priority": priority,
        "epic": epic,
        "sprint": sprint,
        "needs": needs or [],
        "owner": "",
        "created": now(),
        "updated": now(),
        "notes": [],
    })


# --------------------------------------------------------------------------
# epics, sprints, master
# --------------------------------------------------------------------------

def epics_dir(board):
    return os.path.join(board, "epics")


def sprints_dir(board):
    return os.path.join(board, "sprints")


def _load_dir(directory, prefix):
    out = []
    pattern = (prefix + "-*.json") if prefix else "*.json"
    for path in sorted(glob.glob(os.path.join(directory, pattern))):
        try:
            with open(path) as f:
                out.append(json.load(f))
        except (ValueError, IOError):
            continue
    return out


def _save_in(directory, rec):
    rec["updated"] = now()
    path = os.path.join(directory, rec["id"] + ".json")
    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        json.dump(rec, f, indent=2)
    os.replace(tmp, path)
    return rec


def load_epics(board):
    return _load_dir(epics_dir(board), "E")


def load_sprints(board):
    return _load_dir(sprints_dir(board), "S")


def get_epic(board, eid):
    for e in load_epics(board):
        if e["id"] == eid:
            return e
    sys.exit("no such epic: %s" % eid)


def get_sprint(board, sid):
    for s in load_sprints(board):
        if s["id"] == sid:
            return s
    sys.exit("no such sprint: %s" % sid)


def active_sprint(board):
    for s in load_sprints(board):
        if s.get("status") == "active":
            return s
    return None


def progress(tickets):
    """(done, total, claimed, blocked) over a set of tickets."""
    done = sum(1 for t in tickets if t["status"] == "done")
    claimed = sum(1 for t in tickets if t["status"] == "claimed")
    blocked = sum(1 for t in tickets if t["status"] == "blocked")
    return done, len(tickets), claimed, blocked


def bar(done, total, width=20):
    if not total:
        return "[%s] 0/0" % (" " * width)
    n = int(round(width * done / float(total)))
    return "[%s%s] %d/%d" % ("#" * n, " " * (width - n), done, total)


def master_path(board):
    return os.path.join(board, "MASTER.md")


def objective_path(board):
    return os.path.join(board, "objective.json")


def load_objective(board):
    """The standing objective the master drives toward (`tickets objective`).
    {} when none is set."""
    try:
        with open(objective_path(board)) as f:
            return json.load(f)
    except (IOError, ValueError):
        return {}


OBJECTIVE_STATES = ("active", "achieved", "blocked", "replaced")
STOP_CONDITION = (
    "unattended task-only watchers run one model run then stop; continuous/scheduled adapters "
    "stay online until spawn --stop. Interactive Codex/Claude sessions are separate from their persistent adapter."
)
WAKE_MODES = ("task-only", "continuous", "scheduled")
LIFECYCLES = ("persistent", "ephemeral")
WAKE_KEYS = frozenset({
    "holding", "suggested_for_me", "ready_in_my_lane",
    "task_messages", "stuck_messages", "drive", "forced",
})
WAKE_MESSAGE_LIMIT = 5
WAKE_MESSAGE_MAX_CHARS = 320
REMOTE_LEASE_TTL = 30
REMOTE_MAX_ATTEMPTS = 3
REMOTE_LONG_POLL_MAX = 30
LOCAL_DISPATCH_MAX_ATTEMPTS = 3


def objective_state(obj):
    """Terminal state achieved|blocked|replaced, else active. Legacy done => achieved."""
    if not obj:
        return ""
    st = obj.get("state")
    if st in OBJECTIVE_STATES:
        return st
    return "achieved" if obj.get("done") else "active"


def objective_exit_ok(obj):
    return bool(str((obj or {}).get("exit_criterion") or "").strip())


def objective_exit_missing(obj):
    """True when the standing objective has no measurable exit criterion."""
    if not obj:
        return False
    if "exit_missing" in obj:
        return bool(obj.get("exit_missing"))
    return not objective_exit_ok(obj) and objective_state(obj) == "active"


def drive_status(board, tickets=None):
    """One-paragraph progress picture for the master's heartbeat: sprint burn,
    review queue, unowned ready work, live workers whose lane is empty."""
    tickets = tickets if tickets is not None else load_all(board)
    cur = active_sprint(board)
    lines = []
    if cur:
        mine = [t for t in tickets if t.get("sprint") == cur["id"]]
        d = sum(1 for t in mine if t.get("status") == "done")
        lines.append("sprint %s: %d/%d done, %d in flight, %d blocked -- %s" % (
            cur["id"], d, len(mine), sum(1 for t in mine if t.get("status") in ("claimed", "review")),
            sum(1 for t in mine if t.get("status") == "blocked"), cur.get("goal", "")[:100]))
    else:
        lines.append("no active sprint")
    rq = [t["id"] for t in tickets if t.get("status") == "review"]
    lines.append("review queue: %s" % (", ".join(rq) if rq else "empty"))
    ready = [t for t in unblocked(board, tickets) if not t.get("owner")]
    lines.append("ready and unowned: %s" % (", ".join(t["id"] for t in ready[:8]) if ready else "none"))
    idle = []
    for r in load_agents(board):
        who = r.get("owner", "")
        if not who or r.get("limit") or hours_since(r.get("seen", "")) > 2:
            continue
        holds = any(t.get("owner") == who and t.get("status") in ("claimed", "review") for t in tickets)
        if holds:
            continue
        roles = roles_for(board, who, None)
        lane = [t for t in _filter_ready(unblocked(board, tickets), roles) if can_do(board, who, t)]
        if not lane:
            idle.append(who)
    lines.append("live workers with an empty lane: %s" % (", ".join(idle) if idle else "none"))
    return "\n".join(lines)


def master_state_path(board):
    return os.path.join(board, "master.json")


def current_master(board):
    try:
        with open(master_state_path(board)) as f:
            return json.load(f)
    except (IOError, ValueError):
        return None


def _notify_review_submitted(board, author, tid, text, master_state=None):
    """Wake the reviewer once via kind=task; static review_queue is not a wake key.

    With a CoS: task the CoS, notification-only copy to master.
    With no CoS: task the master so an unattended master does not sleep through
    the review. After inbox read, review_queue alone must not re-wake.
    """
    m = master_state if master_state is not None else (current_master(board) or {})
    master_name = (m or {}).get("owner") or ""
    cos_name = (m or {}).get("cos") or ""
    body = "%s ready for review: %s" % (tid, text)
    obj_state = objective_state(_safe(lambda: load_objective(board), {}))
    # Reviews advance an active objective, but a terminal objective is an
    # explicit stop boundary. Keep the durable notification while withholding
    # its automatic task wake. `source=review` also lets the pending gate catch
    # a review queued while active if the objective becomes terminal before a
    # persistent seat polls it.
    review_kind = "" if obj_state in ("blocked", "achieved", "replaced") else "task"
    if cos_name:
        post_message(board, author, body, to=cos_name, re=tid,
                     kind=review_kind, source="review")
        if master_name and master_name != cos_name:
            post_message(board, author, body, to=master_name, re=tid, source="review")
    else:
        post_message(board, author, body, to=master_name, re=tid,
                     kind=review_kind, source="review")
    return master_name, cos_name


def spawn_watch_max_runs(wake_mode="task-only", persist=False, max_runs=None):
    """Watch --max-runs for `tickets spawn`. 0 loops until spawn --stop.

    A seat's durable wake policy, rather than its harness brand, decides
    whether the adapter remains online. An explicit --max-runs (including 1
    for a diagnostic one-shot) overrides that. --persist always loops.
    """
    if persist:
        return 0
    if max_runs is not None:
        return int(max_runs)
    if wake_mode in ("continuous", "scheduled"):
        return 0
    return 1


def wake_mode_of(board, owner, master_state=None, workforce=None):
    """Effective durable wake policy for one seat.

    Existing boards need no migration: an explicit workforce value wins;
    otherwise the current master and CoS inherit the continuous default and
    every other seat stays task-only. The setting is harness-neutral, so the
    same policy applies to Claude, Codex, Cursor, or a remote/custom bridge.
    """
    wf = workforce if workforce is not None else _safe(lambda: load_workforce(board), {})
    configured = ((wf or {}).get(owner, {}) or {}).get("wake_mode")
    if configured in WAKE_MODES:
        return configured
    state = master_state if master_state is not None else _safe(lambda: current_master(board), {})
    if owner and owner in ((state or {}).get("owner"), (state or {}).get("cos")):
        return "continuous"
    return "task-only"


def lifecycle_of(board, owner, master_state=None, workforce=None):
    """Effective seat lifecycle, separate from wake_mode and session_id.

    Explicit workforce.lifecycle wins. Otherwise the current master and CoS
    migrate to persistent; every other seat stays ephemeral. A persistent seat
    may still be task-only, continuous, or scheduled.
    """
    wf = workforce if workforce is not None else _safe(lambda: load_workforce(board), {})
    configured = ((wf or {}).get(owner, {}) or {}).get("lifecycle")
    if configured in LIFECYCLES:
        return configured
    state = master_state if master_state is not None else _safe(lambda: current_master(board), {})
    if owner and owner in ((state or {}).get("owner"), (state or {}).get("cos")):
        return "persistent"
    return "ephemeral"


def hours_since(stamp):
    try:
        d = datetime.strptime(stamp, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
        return (datetime.now(timezone.utc) - d).total_seconds() / 3600.0
    except (ValueError, TypeError):
        return 0.0


# --------------------------------------------------------------------------
# git awareness (agents must work on their own tree and commit)
# --------------------------------------------------------------------------

def git(*args, cwd=None):
    """Run git and return stdout, or None if it failed.

    `cwd=` picks the tree to ask. It matters: T-272 -- every caller used to
    inherit os.getcwd(), so `tickets review` pinned whichever repo the agent
    happened to be standing in rather than the repo the deliverable is in.

    cwd is necessary and NOT sufficient (T-287). An inherited GIT_DIR,
    GIT_COMMON_DIR or GIT_WORK_TREE overrides cwd-based discovery inside git
    itself, so under a leaked env `tickets review --artifact <dir>` pinned the
    wrong repo and reported success -- the defect T-272 exists to fix,
    reappearing on the command that fixes it. The scrub and the targeting are
    one mechanism; neither works alone, so they are applied together here at
    the single choke point every caller goes through.
    """
    import subprocess
    try:
        out = subprocess.run(["git"] + list(args), capture_output=True, text=True, timeout=10,
                             cwd=cwd or os.getcwd(), env=_clean_git_env())
    except (OSError, subprocess.TimeoutExpired):
        return None
    if out.returncode != 0:
        return None
    return out.stdout.strip()


def repo_identity(cwd):
    """A repo identity that is stable across every worktree of the SAME repo.

    T-215: 'tickets review' pins branch@sha from whatever repo the caller
    happened to be standing in, and cross-repo work is the norm on this board
    (every agent works in .worktrees/). The worktree's own toplevel path is
    useless for identity -- it differs per worktree of the same repo -- so
    this prefers the origin remote URL (identical across worktrees, and
    across separate clones of the same repo on different machines) and falls
    back to the shared .git directory's real path for a repo with no remote
    configured (e.g. a fresh local board in a test).
    """
    import subprocess

    def _git(*args):
        try:
            # T-243: cwd= alone is not enough -- an inherited GIT_DIR outranks
            # it during discovery, so without the scrub this returns the
            # identity of whatever repo the pollution names. That is precisely
            # the cross-repo mis-pin T-215 exists to prevent, reached through
            # T-243's mechanism instead of through the caller's cwd.
            out = subprocess.run(["git"] + list(args), cwd=cwd, capture_output=True,
                                 text=True, timeout=10, env=_clean_git_env())
        except (OSError, subprocess.TimeoutExpired):
            return None
        return out.stdout.strip() if out.returncode == 0 else None

    url = _git("config", "--get", "remote.origin.url")
    if url:
        return url
    common = _git("rev-parse", "--git-common-dir")
    if common:
        return os.path.realpath(os.path.join(cwd, common))
    return None


def _git_state_raw(cwd=None):
    """(state, mismatch). See git_state() -- this keeps the mismatch signal
    that git_state() deliberately throws away, for callers that record it.

    `cwd` is the tree to describe, default the process cwd (T-272). All four
    parts of the answer -- branch, sha, repo identity and dirty count -- are
    asked of that ONE tree, so the pin they form is internally consistent.
    The containment refusal below is applied to that tree too, not to the
    process cwd: with --artifact those are deliberately different directories,
    and checking the wrong one would refuse every correct cross-repo pin.
    """
    here = cwd or os.getcwd()
    top = git("rev-parse", "--show-toplevel", cwd=here)
    if not top:
        return None, False
    real_top = os.path.realpath(top)
    real_here = os.path.realpath(here)
    if os.path.commonpath([real_top, real_here]) != real_top:
        # git resolved a work tree that does not contain where we are standing
        # (e.g. a stale core.worktree). Refuse it rather than answer wrongly.
        sys.stderr.write(
            "tickets: git resolved toplevel %s which does not contain cwd %s -- "
            "treating as unresolved rather than trusting it (T-243)\n" % (real_top, real_here))
        return None, True
    branch = git("rev-parse", "--abbrev-ref", "HEAD", cwd=here) or "?"
    sha = git("rev-parse", "--short", "HEAD", cwd=here) or "?"
    dirty = git("status", "--porcelain", cwd=here)
    common = git("rev-parse", "--git-common-dir", cwd=here) or ""
    gitdir = git("rev-parse", "--git-dir", cwd=here) or ""
    is_main_tree = os.path.abspath(os.path.join(top, common)) == os.path.abspath(os.path.join(top, gitdir))
    return {
        "top": top,
        "branch": branch,
        "sha": sha,
        "dirty": len(dirty.splitlines()) if dirty else 0,
        "main_tree": is_main_tree,
        "repo": repo_identity(top),
    }, False


def git_state(cwd=None):
    """Branch, short sha, dirty-file count, and whether cwd is the main worktree.

    `cwd` selects the tree to describe (T-272); it defaults to the process cwd.

    Returns None -- never a partly-filled dict -- when resolution disagrees
    with cwd. Every caller guards with `g = git_state()` / `if not g`, and the
    on-main and dirty guards downstream read `branch` and `dirty`. A truthy
    placeholder such as {"branch": "?", "dirty": 0} therefore does not fail
    safe, it fails OPEN: it satisfies `not g`, passes the never-work-on-main
    check ("?" is not main) and passes the clean-tree check (0 is not dirty),
    so `tickets sync` would go on to run a real merge with all three guards
    disabled, and `tickets review` would pin an unmergeable "?@?" (T-259
    defect 2). Unresolved must mean None.
    """
    state, _mismatch = _git_state_raw(cwd)
    return state


def artifact_tree(a):
    """Resolve `--artifact` to a real git working tree, or None meaning "use cwd".

    `tickets review` used to derive branch, sha and repo from whatever
    directory the agent ran it in. That is systematically the wrong tree when
    the CLI is driven from a different clone than the deliverable, so the pin
    named a repo that never built the work.

    The agent supplies a LOCATION, not an assertion. Everything recorded is
    still derived by running git inside that tree, so a pin no real tree can
    produce cannot be recorded -- this is deliberately not a `--repo/--commit`
    pair taken on trust, because a hand-typed sha is exactly how fiction gets
    into the close record.
    """
    path = getattr(a, "artifact", "") or ""
    if not path:
        return None
    path = os.path.abspath(os.path.expanduser(path))
    if not os.path.isdir(path):
        sys.exit("--artifact %s is not a directory" % path)
    if git("rev-parse", "--show-toplevel", cwd=path) is None:
        sys.exit("--artifact %s is not inside a git working tree; point it at the "
                 "checkout the deliverable actually lives in" % path)
    return path


def _behind_trunk_refusal(g, art, trunk):
    """The text `tickets review` refuses with when the branch is behind trunk.

    T-422. The gate is correct and is T-272 working as designed: it measures
    the ARTIFACT tree. The text did not say so -- it said a bare "Run `tickets
    sync`", and cmd_sync without --artifact operates on the process cwd. On a
    cross-repo ticket those are different repos, so the instruction could not
    clear the refusal:

        review --artifact B  -> "your branch is behind main"
        sync                 -> "<branch> already contains main; nothing to do"
        review --artifact B  -> "your branch is behind main"

    Both lines are true and each is about a different repository, so the loop
    is closed and the text alone offers no way out. The agent's natural next
    move from "already contains main" is to conclude the gate is wrong and
    start merging, which on a less careful lane is a merge into the wrong
    repo's main -- so this is a message defect with a merge-shaped cost.

    Only the wording changes here; what the gate measures is untouched.

    The split is on the trees being genuinely DIFFERENT, not on whether
    --artifact was passed: `--artifact .` is the common path wearing a flag and
    gets the common path's message. Comparing resolved toplevels rather than
    repo_identity() is deliberate -- a second worktree of the SAME repo has an
    identical identity, and `tickets sync` there merges the cwd worktree's own
    branch, so it cannot clear this refusal either and the agent still has to
    be sent elsewhere.
    """
    tail = ("(merges %s in, so conflicts are yours to fix now, not the master's later), "
            "then submit again." % trunk)
    plain = "RULE: your branch is behind %s. Run `tickets sync` %s" % (trunk, tail)
    if not art:
        return plain
    cg = git_state()
    if cg and os.path.realpath(cg["top"]) == os.path.realpath(g["top"]):
        return plain
    if cg:
        wrong = "the tree you are standing in, %s (%s)" % (cg["top"], cg["repo"] or "no origin remote")
        kind = "repo" if cg["repo"] != g["repo"] else "worktree"
    else:
        wrong = "the directory you are standing in (not a git tree)"
        kind = "place"
    return (
        "RULE: branch %s in %s (%s) is behind %s.\n"
        "That ARTIFACT tree is what this gate measured (--artifact), NOT %s.\n"
        "Sync THERE, not here: `tickets sync --artifact %s` %s\n"
        "A bare `tickets sync` would sync that other %s and cannot clear this refusal."
        % (g["branch"], g["top"], g["repo"] or "no origin remote", trunk,
           wrong, art, tail, kind)
    )


def _record_pin(t, g, art):
    """Write the (repo, branch, sha) evidence triple onto a ticket.

    All three come from ONE tree (T-272). `repo` stays the field the merge
    guard compares, so no existing record needs a field rename; `repo_source`
    lets a reader tell a pin the agent aimed from one the tool merely
    inherited, and the cwd provenance is kept alongside rather than thrown
    away.
    """
    t["commit"] = "%s@%s" % (g["branch"], g["sha"])
    t["branch"] = g["branch"]
    t["repo"] = g["repo"]
    t["repo_source"] = "artifact" if art else "cwd"
    if art:
        t["artifact_dir"] = art
        cg = git_state()
        if cg:
            t["cwd_repo"] = cg["repo"]
            t["cwd_commit"] = "%s@%s" % (cg["branch"], cg["sha"])
    else:
        t.pop("artifact_dir", None)
        t.pop("cwd_repo", None)
        t.pop("cwd_commit", None)
    return t["commit"]


def agents_dir(board):
    return os.path.join(board, "agents")


class _AgentLock:
    """Serialize read-modify-write on one agents/<name>.json across processes.

    The lock lives on a SEPARATE <name>.json.lock file, never on the record
    itself: the record is published with os.replace, which swaps the inode out
    from under any lock held on it, so locking the record would appear to work
    and serialize nothing.

    Blocking LOCK_EX, not the LOCK_NB used by merge.lock. A heartbeat must wait
    its turn, never fail: the point of this lock is that no writer silently
    loses, and refusing a check-in would trade a lost write for a dead agent.
    flock is released by the kernel when the holder exits, so an agent killed
    mid-write cannot strand the file the way an O_EXCL lockfile would.
    """

    def __init__(self, board, owner):
        os.makedirs(agents_dir(board), exist_ok=True)
        self.path = os.path.join(agents_dir(board), owner + ".json.lock")
        self.fd = None

    def __enter__(self):
        try:
            import fcntl
        except ImportError:  # no flock on this platform; degrade to today's behaviour
            return self
        self.fd = os.open(self.path, os.O_CREAT | os.O_RDWR, 0o644)
        fcntl.flock(self.fd, fcntl.LOCK_EX)
        return self

    def __exit__(self, *exc):
        if self.fd is None:
            return
        try:
            import fcntl
            fcntl.flock(self.fd, fcntl.LOCK_UN)
        finally:
            os.close(self.fd)
            self.fd = None


def _agent_update(board, owner, mutate):
    """Read-modify-write an agent record with the READ INSIDE the lock.

    Every writer of agents/<name>.json goes through here. Doing the read
    outside is the whole defect: os.replace makes each write atomic, but two
    writers that both read the pre-image and then write in turn silently drop
    whichever field the loser did not know about -- and both print success.

    `mutate(rec)` edits the dict in place; returning False aborts without
    writing. Anything slow (git, load_all, subprocesses) belongs OUTSIDE this
    call: the critical section is meant to be a read, a dict update and a
    rename, so a blocking lock is never held long enough to matter.
    """
    path = os.path.join(agents_dir(board), owner + ".json")
    with _AgentLock(board, owner):
        rec = _agent_rec(board, owner) or {}
        if mutate(rec) is False:
            return None
        tmp = "%s.tmp.%d" % (path, os.getpid())
        with open(tmp, "w") as f:
            json.dump(rec, f, indent=2)
        os.replace(tmp, path)
    return rec


def _clear_agent_ticket(board, agent, tid):
    """T-437: drop a stale ticket= bind on a previous owner's agent record.

    Keeps cwd/branch/sha. Abort (no write) if the record is missing or names
    a different ticket.
    """
    if not agent or not tid:
        return

    def mutate(rec):
        if rec.get("ticket") != tid:
            return False
        rec["ticket"] = ""

    _agent_update(board, agent, mutate)


def _drop_unowned_agent_ticket(board, owner):
    """T-439: drop agents/<me>.json ticket= when the live owner of that id is not me.

    Also drop a leftover review bind when I already hold a different claimed
    ticket (assign-not-claim: json still has T-438 after planner assigned T-415).
    Keeps cwd/branch/sha. Missing ticket files count as unowned. Call at
    watch/spawn start so a STOPPED seat's leftover bind cannot reach run_start.
    """
    if not owner:
        return
    rec = _agent_rec(board, owner) or {}
    tid = rec.get("ticket") or ""
    if not tid:
        return
    claimed = [t["id"] for t in load_all(board)
               if t.get("status") == "claimed" and t.get("owner") == owner]
    if claimed and tid not in claimed:
        _clear_agent_ticket(board, owner, tid)
        return
    t = None
    try:
        with open(ticket_path(board, tid)) as f:
            t = json.load(f)
    except (IOError, ValueError):
        t = None
    if isinstance(t, dict) and t.get("owner") == owner:
        return
    _clear_agent_ticket(board, owner, tid)


def _bind_agent_ticket(board, agent, tid):
    """Set ticket= on the new owner's existing record without clobbering cwd."""
    if not agent or not tid:
        return
    rec = _agent_rec(board, agent)
    if rec:
        def mutate(r):
            r["ticket"] = tid
        _agent_update(board, agent, mutate)
    else:
        checkin(board, agent, tid)


def checkin(board, owner, ticket=None, note=""):
    """Record where this agent is working: cwd, worktree root, branch, sha."""
    _state, _mismatch = _git_state_raw()
    g = _state or {}
    # git_state and _current_ticket shell out; keep them off the critical section.
    fields = {
        "owner": owner,
        "cwd": os.getcwd(),
        "worktree": g.get("top", ""),
        "branch": g.get("branch", ""),
        "sha": g.get("sha", ""),
        "dirty": g.get("dirty", 0),
        # T-243: cwd above is recorded straight from os.getcwd() with no git
        # resolution in its path, so it stays trustworthy even here.
        "git_mismatch": bool(_mismatch),
        "ticket": ticket if ticket is not None else _current_ticket(board, owner),
        "note": note,
        "seen": now(),
    }

    def _apply(rec):
        # T-278 owns the WRITE (read-modify-write inside the flock); T-244 owns
        # the inbox_seen STAMP. Resolving this toward the T-244 side would
        # restore the unlocked json.dump/os.replace that T-278 exists to
        # remove, so the stamp moves inside the lambda instead of the write
        # moving back out.
        rec.update(fields)
        # NO inbox_seen STAMP HERE, and its absence is the fix, not an omission.
        #
        # T-244 stamped inbox_seen = now() at an agent's first check-in. Its
        # target was real -- unread()'s `if since and (...)` treats since=""
        # as "skip the archive check", which is backwards for the agent with
        # the least history to fall back on -- but the mechanism overloaded
        # inbox_seen to mean two different things: "the newest mail this agent
        # has been SHOWN" (a receipt) and "when this agent APPEARED" (a clock).
        #
        # Once T-327 landed joined_at, that second meaning has its own field,
        # and keeping the stamp is actively destructive: a message sent
        # `--to <name>` BEFORE the seat joins is older than the stamp and is
        # therefore never delivered. Posting the brief first is exactly how
        # seats get briefed on this board, and T-327 measured and rejected
        # this same regression (test_join_does_not_destroy_a_brief_posted_
        # before_the_seat_existed, which the stamp turns red).
        #
        # So inbox_seen goes back to being a pure receipt that starts empty,
        # T-327's joined_at owns the flood policy, and T-244's actual defect
        # is fixed where its own docstring says it lives: at the since=""
        # branch in _inbox_scan, which now reads the archives for an agent
        # that has never read anything instead of skipping them.

    # Other commands keep their own state in this record (inbox_seen, limit,
    # stop_blocks); a check-in must not erase it or every watch poll re-wakes
    # the agent. rec.update preserves them against the ORDERING hazard; the
    # lock in _agent_update is what preserves them against the CONCURRENCY one.
    return _agent_update(board, owner, _apply)


def _current_ticket(board, owner):
    """Return the ticket this agent is working on: claimed first, else review."""
    mine = [t for t in load_all(board) if t.get("owner") == owner]
    claimed = [t for t in mine if t.get("status") == "claimed"]
    if claimed:
        return claimed[0]["id"]
    review = [t for t in mine if t.get("status") == "review"]
    if not review:
        return ""

    def _last_touch(t):
        stamps = [t.get("review_at")] + [n.get("at") for n in t.get("notes", []) if n.get("at")]
        stamps = [s for s in stamps if s]
        return max(stamps) if stamps else ""

    review.sort(key=_last_touch, reverse=True)
    return review[0]["id"]


def _here_ticket(board, owner):
    """Ticket id for tickets here (T-543 / T-551).

    Stamp the claimed hold when present; otherwise clear. Never stamp an
    IN REVIEW id whose live owner is not me (T-551), and never re-bind stale
    IR when mine is empty (T-543).
    """
    claimed = [t["id"] for t in load_all(board)
               if t.get("status") == "claimed" and t.get("owner") == owner]
    if claimed:
        return claimed[0]
    return ""


def _watch_bind_ticket(board, owner):
    """Ticket id for watch run_start/run_end pairing.

    Claimed holds bind from the board. agent.json ticket= may bind claimed or
    blocked only — never IN REVIEW when mine is empty (T-543/T-563). T-425
    idle pulses pair via run_id, not a stale review ticket= field.
    """
    for t in load_all(board):
        if t.get("status") == "claimed" and t.get("owner") == owner:
            return t["id"]
    rec = _agent_rec(board, owner) or {}
    tid = rec.get("ticket") or ""
    if not tid:
        return ""
    try:
        with open(ticket_path(board, tid)) as f:
            t = json.load(f)
    except (IOError, ValueError):
        return ""
    if isinstance(t, dict) and t.get("owner") == owner and t.get("status") in (
            "claimed", "blocked"):
        return tid
    return ""


def load_agents(board):
    return _load_dir(agents_dir(board), "") if os.path.isdir(agents_dir(board)) else []


def worktree_warning(owner):
    """Text telling an agent on main / the primary tree to move to its own tree."""
    g = git_state()
    if not g:
        return None
    on_trunk = g["branch"] in ("main", "master")
    if on_trunk or g["main_tree"]:
        lane = "%s-work" % owner
        return (
            "RULE: you are on branch %r in %s. Work on your own tree:\n"
            "  git -C %s worktree add .worktrees/%s -b %s\n"
            "  cd %s/.worktrees/%s"
            % (g["branch"], "the primary worktree" if g["main_tree"] else "a shared branch",
               g["top"], lane, lane, g["top"], lane)
        )
    return None


def try_claim(board, tid, owner):
    """Atomically take a ticket. Returns the ticket, or None if someone beat us."""
    lock = os.path.join(board, tid + ".lock")
    try:
        fd = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except OSError as e:
        if e.errno == errno.EEXIST:
            return None
        raise
    os.write(fd, owner.encode())
    os.close(fd)
    t = load(board, tid)
    if t["status"] != "open":  # claimed by a slower path; give the lock back
        os.unlink(lock)
        return None
    if _ticket_lane(t) != "ready":
        os.unlink(lock)
        return None
    prev_owner = t.get("owner") or ""
    t["status"] = "claimed"
    t["owner"] = owner
    t["claimed_at"] = now()
    t["done_at"] = ""
    got = save(board, t)
    # Written here, not in cmd_next/cmd_claim: this is the single point where a
    # claim actually succeeds, so no future caller can add a claim path that
    # silently produces no trajectory.
    _safe(lambda: traj_event(board, "claim", agent=owner, ticket=got,
                             state_before="open", state_after="claimed",
                             **_traj_git()), None)
    if prev_owner and prev_owner != owner:
        _safe(lambda: _clear_agent_ticket(board, prev_owner, tid), None)
    return got


def fmt_hours(h):
    if h is None:
        return "?"
    if h < 1:
        return "%dm" % int(round(h * 60))
    if h < 48:
        return "%.1fh" % h
    return "%.1fd" % (h / 24.0)


def timing(t):
    """wait (created->claimed), active (claimed->done or now), and since last update."""
    created, claimed, done = t.get("created"), t.get("claimed_at"), t.get("done_at")
    out = {"wait": None, "active": None, "since_update": None}
    if created and claimed:
        out["wait"] = hours_since(created) - hours_since(claimed)
    if claimed:
        end = hours_since(done) if done else 0.0
        out["active"] = hours_since(claimed) - end
    if t["status"] == "claimed":
        stamps = [n.get("at") for n in t.get("notes", []) if n.get("at")]
        last = max(stamps) if stamps else claimed
        out["since_update"] = hours_since(last) if last else None
    return out


def _sounding():
    try:
        from ticket_board import sounding as m
        return m
    except ImportError:
        src = os.path.join(os.path.dirname(os.path.realpath(__file__)), "src")
        if src not in sys.path:
            sys.path.insert(0, src)
        from ticket_board import sounding as m
        return m


def _seat_schedule():
    try:
        from ticket_board import seat_schedule as m
        return m
    except ImportError:
        src = os.path.join(os.path.dirname(os.path.realpath(__file__)), "src")
        if src not in sys.path:
            sys.path.insert(0, src)
        from ticket_board import seat_schedule as m
        return m


def _ticket_lane(t):
    return _sounding().ticket_lane(t)


def unblocked(board, tickets):
    """Open tickets whose dependencies are all done and whose lane is ready.

    Capture and discarded tickets stay on the graph but are invisible to
    `tickets next` until a sound pass promotes them (Fatih write gate).
    """
    done = set(t["id"] for t in tickets if t["status"] == "done")
    return [
        t
        for t in tickets
        if t["status"] == "open"
        and _ticket_lane(t) == "ready"
        and all(d in done for d in t.get("deps", []))
    ]


def find_cycle(tickets):
    """Return the ids forming a dependency cycle, or None. Iterative DFS."""
    deps = dict((t["id"], list(t.get("deps", []))) for t in tickets)
    color = {}
    for root in deps:
        if color.get(root):
            continue
        stack = [(root, iter(deps.get(root, [])))]
        color[root] = 1
        path = [root]
        while stack:
            node, it = stack[-1]
            advanced = False
            for nxt in it:
                if nxt not in deps:
                    continue
                if color.get(nxt) == 1:
                    return path[path.index(nxt) :] + [nxt]
                if not color.get(nxt):
                    color[nxt] = 1
                    path.append(nxt)
                    stack.append((nxt, iter(deps.get(nxt, []))))
                    advanced = True
                    break
            if not advanced:
                color[node] = 2
                stack.pop()
                if path:
                    path.pop()
    return None


def dangling(tickets):
    """Map of ticket id -> dep ids that do not exist on the board."""
    ids = set(t["id"] for t in tickets)
    out = {}
    for t in tickets:
        missing = [d for d in t.get("deps", []) if d not in ids]
        if missing:
            out[t["id"]] = missing
    return out


def check_graph(board, extra=None):
    """Abort if the board (plus any pending edits) is cyclic or references ghosts."""
    tickets = load_all(board)
    if extra:
        by_id = dict((t["id"], t) for t in tickets)
        for tid, deps in extra.items():
            if tid in by_id:
                by_id[tid] = dict(by_id[tid], deps=deps)
        tickets = list(by_id.values())
    cyc = find_cycle(tickets)
    if cyc:
        sys.exit("refusing: that would create a dependency cycle (%s)" % " -> ".join(cyc))
    return tickets


def set_deps(board, tid, deps):
    t = load(board, tid)
    t["deps"] = deps
    return save(board, t)


# --------------------------------------------------------------------------
# rendering
# --------------------------------------------------------------------------

MARK = {"open": "[ ]", "claimed": "[>]", "review": "[r]", "done": "[x]", "blocked": "[!]"}


def line(t, tickets=None):
    s = "%s %-11s %s  %s" % (MARK.get(t["status"], "[?]"), LABEL.get(t["status"], t["status"]),
                             t["id"], t["title"])
    bits = []
    tags = [x for x in (t.get("epic"), t.get("sprint")) if x]
    if tags:
        bits.append("/".join(tags))
    if t.get("role"):
        bits.append("role=" + t["role"])
    if t.get("needs"):
        bits.append("needs " + ",".join(t["needs"]))
    if t.get("owner"):
        bits.append("owner=" + t["owner"])
    lane = _ticket_lane(t)
    if lane != "ready" or t.get("lane"):
        bits.append("lane=" + lane)
    if (t.get("reserved_for") or "").strip():
        bits.append("reserved: " + t["reserved_for"].strip())
    if _ticket_on_hold(t):
        bits.append("HOLD")
    if t.get("deps"):
        if tickets is not None:
            done = set(x["id"] for x in tickets if x["status"] == "done")
            pending = [d for d in t["deps"] if d not in done]
            if pending and t["status"] == "open":
                bits.append("BLOCKED-BY " + ",".join(pending))
            else:
                bits.append("after " + ",".join(t["deps"]))
        else:
            bits.append("after " + ",".join(t["deps"]))
    if bits:
        s += "  (%s)" % "; ".join(bits)
    return s


def _notes(t):
    return list(t.get("notes") or [])


def collect_handoffs(t, tickets):
    """Direct deps: every note. Transitive ancestors: latest note only."""
    by_id = dict((x["id"], x) for x in tickets)
    direct, earlier = [], []
    seen = set()

    def walk(tid, depth):
        if tid in seen or tid not in by_id:
            return
        seen.add(tid)
        node = by_id[tid]
        for d in node.get("deps", []):
            walk(d, depth + 1)
        notes = _notes(node)
        if not notes:
            return
        if depth == 1:
            for nt in notes:
                direct.append((tid, node["title"], nt.get("text", "")))
        else:
            earlier.append((tid, node["title"], notes[-1].get("text", "")))

    for d in t.get("deps", []):
        walk(d, 1)
    return direct, earlier


def detail(board, t, tickets):
    out = [line(t, tickets)]
    packs = context_paths(board, t.get("owner") or whoami())
    if packs:
        out.append("")
        out.append("Read this briefing before editing:")
        for p in packs:
            out.append("  " + p)
    discovered = os.path.dirname(board)
    if os.path.abspath(discovered) != os.path.abspath(os.getcwd()):
        out.append("")
        out.append("Board: %s  (cwd is %s)" % (board, os.getcwd()))
    # where this ticket sits: sprint goal, epic + siblings, who waits on it
    if t.get("sprint"):
        for s in load_sprints(board):
            if s["id"] == t["sprint"]:
                out.append("Sprint %s: %s" % (s["id"], s.get("goal", "")))
    if t.get("epic"):
        for e in load_epics(board):
            if e["id"] == t["epic"]:
                sib = [x for x in tickets if x.get("epic") == e["id"]]
                d, n, c, b = progress(sib)
                out.append("Epic %s: %s  %s" % (e["id"], e["title"], bar(d, n, 12)))
                if e.get("body"):
                    out.append("  " + e["body"].strip().replace("\n", "\n  "))
                others = [x for x in sib if x["id"] != t["id"] and x["status"] != "done"]
                if others:
                    out.append("  also in this epic: " + "; ".join(
                        "%s %s%s" % (x["id"], MARK[x["status"]], (" @" + x["owner"]) if x.get("owner") else "")
                        for x in others[:8]))
    downstream = [x for x in tickets if t["id"] in x.get("deps", [])]
    if downstream:
        out.append("Waiting on this: " + "; ".join(
            "%s %s" % (x["id"], x["title"][:40]) for x in downstream))
    tm = timing(t)
    if tm["active"] is not None:
        stamp = "active %s" % fmt_hours(tm["active"])
        if tm["wait"] is not None:
            stamp += ", waited %s before claim" % fmt_hours(tm["wait"])
        if t["status"] == "claimed" and tm["since_update"] is not None:
            stamp += ", last update %s ago" % fmt_hours(tm["since_update"])
        out.append("Time: " + stamp)
    out.append("lane: %s" % _ticket_lane(t))
    if t.get("sounded_at"):
        out.append("sounded: %s by %s" % (t.get("sounded_at"), t.get("sounded_by") or "?"))
    if t.get("cause"):
        out.append("Cause: %s" % t["cause"])
    if t.get("change"):
        out.append("Change: %s" % t["change"])
    if t.get("proof"):
        out.append("Proof: %s" % t["proof"])
    qs = t.get("open_questions")
    if qs:
        out.append("Open questions: %s" % (", ".join(qs) if isinstance(qs, list) else qs))
    if t.get("pr"):
        out.append("PR: %s" % t["pr"])
    if t.get("discarded_reason"):
        out.append("discarded: %s" % t["discarded_reason"])
    if t.get("body"):
        out.append("")
        out.append(t["body"])
    direct, earlier = collect_handoffs(t, tickets)
    if direct:
        out.append("")
        out.append("Handoff from dependencies (all notes):")
        for tid, title, text in direct:
            out.append("  %s (%s): %s" % (tid, title, text))
    if earlier:
        out.append("")
        out.append("Handoff from earlier ancestors (latest note each):")
        for tid, title, text in earlier:
            out.append("  %s (%s): %s" % (tid, title, text))
    if t.get("notes"):
        out.append("")
        out.append("Notes:")
        for nt in t["notes"]:
            out.append("  - [%s] %s" % (nt.get("by", "?"), nt["text"]))
    return "\n".join(out)


# --------------------------------------------------------------------------
# commands
# --------------------------------------------------------------------------

def _ids(csv):
    return [d.strip() for d in csv.split(",") if d.strip()] if csv else []


def cmd_create(a, board):
    deps = _ids(a.deps)
    blocks = _ids(a.blocks)
    existing = set(t["id"] for t in load_all(board))
    for ref in deps + blocks:
        if ref not in existing:
            sys.exit("no such ticket: %s" % ref)
    _ensure(board, "epic", a.epic)
    _ensure(board, "sprint", a.sprint)
    cur = active_sprint(board)
    sprint = a.sprint if a.sprint is not None else (cur["id"] if cur and a.in_sprint else "")
    t = create(board, a.title, a.body or "", a.role or "", deps, a.priority,
               a.epic or "", sprint, _ids(a.needs))
    pending = {}
    for other in blocks:
        o = load(board, other)
        pending[other] = o["deps"] + [t["id"]]
    if pending:
        try:
            check_graph(board, pending)
        except SystemExit:
            os.unlink(ticket_path(board, t["id"]))
            raise
        for other, d in pending.items():
            set_deps(board, other, d)
    print("created %s  %s" % (t["id"], t["title"]))
    if deps:
        print("  waits for: %s" % ", ".join(deps))
    if blocks:
        print("  now blocks: %s" % ", ".join(blocks))


def cmd_dep(a, board):
    """Rewire an existing ticket's dependencies."""
    t = load(board, a.id)
    deps = list(t.get("deps", []))
    existing = set(x["id"] for x in load_all(board))
    for ref in _ids(a.after):
        if ref not in existing:
            sys.exit("no such ticket: %s" % ref)
        if ref == a.id:
            sys.exit("a ticket cannot depend on itself")
        if ref not in deps:
            deps.append(ref)
    for ref in _ids(a.drop):
        if ref in deps:
            deps.remove(ref)
    check_graph(board, {a.id: deps})
    set_deps(board, a.id, deps)
    print("%s now waits for: %s" % (a.id, ", ".join(deps) if deps else "(nothing)"))


def cmd_plan(a, board):
    raw = sys.stdin.read().strip()
    if not raw:
        sys.exit("plan: expected JSON on stdin")
    try:
        items = json.loads(raw)
    except ValueError as e:
        sys.exit("plan: bad JSON (%s)" % e)
    defaults = {}
    if isinstance(items, dict):
        defaults = {"epic": items.get("epic", ""), "sprint": items.get("sprint", ""),
                    "role": items.get("role", "")}
        items = items.get("tickets", [])
    for k in ("epic", "sprint"):
        if defaults.get(k):
            _ensure(board, k, defaults[k])
        for it in items:
            if it.get(k):
                _ensure(board, k, it[k])
    keymap = {}
    made = []
    for it in items:
        t = create(board, it["title"], it.get("body", ""), it.get("role", defaults.get("role", "")),
                   [], it.get("priority", 2), it.get("epic", defaults.get("epic", "")),
                   it.get("sprint", defaults.get("sprint", "")), it.get("needs") or [])
        if it.get("key"):
            keymap[it["key"]] = t["id"]
        made.append(t)
    existing = set(x["id"] for x in load_all(board))
    pending = {}
    for it, t in zip(items, made):
        deps = []
        for d in it.get("deps", []) or []:
            rid = keymap.get(d, d)
            if rid not in existing:
                for x in made:
                    os.unlink(ticket_path(board, x["id"]))
                sys.exit("plan references unknown ticket %r (no such key or id)" % d)
            deps.append(rid)
        if deps:
            pending[t["id"]] = deps
    try:
        check_graph(board, pending)
    except SystemExit:
        for x in made:
            os.unlink(ticket_path(board, x["id"]))
        raise
    for tid, deps in pending.items():
        set_deps(board, tid, deps)
    S = _sounding()
    who = whoami()
    for it, t0 in zip(items, made):
        t = load(board, t0["id"])
        if it.get("sounded"):
            fields = S.merge_sound_fields(t.get("body") or it.get("body") or "", "")
            qs = S._questions_list(fields.get("open_questions"))
            if (not S.sound_fields_complete(fields) or qs
                    or S.is_no_change(fields.get("change"))):
                for x in made:
                    os.unlink(ticket_path(board, x["id"]))
                sys.exit("plan: %r marked sounded but needs cause/change/proof/deps and empty open questions"
                         % (it.get("key") or t["id"]))
            _apply_sounded(board, t, fields, who)
        else:
            t["lane"] = "capture"
            save(board, t)
        print("created %s  %s  lane=%s" % (t["id"], t["title"], _ticket_lane(t)))


def _apply_sounded(board, t, fields, who):
    S = _sounding()
    qs = S._questions_list(fields.get("open_questions"))
    t["cause"] = (fields.get("cause") or "").strip()
    t["change"] = (fields.get("change") or "").strip()
    t["proof"] = (fields.get("proof") or "").strip()
    t["open_questions"] = qs
    t["body"] = S.format_sound_body(t.get("body") or "", fields)
    deps_text = (fields.get("deps") or "").strip()
    t.setdefault("notes", [])
    t["notes"].append({"by": who, "at": now(), "text": "sound: deps=%s" % (deps_text or "none")})
    if qs or S.is_no_change(fields.get("change")):
        t["lane"] = "capture"
        t["sounded_at"] = None
        t["sounded_by"] = None
    else:
        t["lane"] = "ready"
        t["sounded_at"] = now()
        t["sounded_by"] = who
    return save(board, t)


def _capture_snapshot(board, thought):
    S = _sounding()
    tickets = load_all(board)
    obj = load_objective(board) or {}
    obj_text = (obj.get("text") or "(none)").strip().replace("\n", " ")[:400]
    ready_ids = [t["id"] for t in unblocked(board, tickets)]
    capture_ids = [t["id"] for t in tickets if _ticket_lane(t) == "capture"]
    related = S.related_done_notes(tickets, thought)
    lines = [
        thought.strip(),
        "",
        "## Surroundings",
        "Objective: %s" % obj_text,
        "Graph: %s" % S.graph_snapshot(tickets, ready_ids, capture_ids),
    ]
    if related:
        lines.append("Related done:")
        for _score, tid, title, last in related:
            lines.append("- %s %s -- %s" % (tid, title, last or "(no note)"))
    else:
        lines.append("Related done: (none)")
    return "\n".join(lines).strip() + "\n"


def cmd_capture(a, board):
    """Fatih /plan-add: a thought, not yet a claimable ticket."""
    S = _sounding()
    thought = (a.thought or "").strip()
    if getattr(a, "from_msg", ""):
        mid = a.from_msg.strip()
        msg = None
        for m in load_messages(board, include_archives=True):
            if m.get("id") == mid:
                msg = m
                break
        if not msg:
            sys.exit("capture: no such message %s" % mid)
        thought = thought or (msg.get("text") or "").strip()
        if not thought:
            sys.exit("capture: message %s has no text" % mid)
    if not thought:
        sys.exit('capture: tickets capture "a thought"  (or --from-msg <id>)')
    title = S.capture_title(thought, getattr(a, "area", "") or "")
    body = _capture_snapshot(board, thought)
    t = create(board, title, body, getattr(a, "role", "") or "", [],
               getattr(a, "priority", 2) or 2, "", "", [])
    t["lane"] = "capture"
    t["open_questions"] = ["not yet sounded"]
    t["notes"] = t.get("notes") or []
    t["notes"].append({"by": whoami(), "at": now(),
                       "text": "captured (not claimable until tickets sound)"})
    save(board, t)
    print("captured %s  %s  lane=capture" % (t["id"], t["title"]))
    print("  not visible to tickets next until `tickets sound %s`" % t["id"])


def cmd_sound(a, board):
    """Fatih /plan-write: high-reasoning gate. Master/CoS/operator, not the coder."""
    S = _sounding()
    t = load(board, a.id)
    who = whoami()
    if t.get("status") == "claimed" and (t.get("owner") or "") == who:
        sys.exit("sound: the implementer of %s cannot sound it; master/CoS/operator does"
                 % t["id"])
    if _ticket_lane(t) == "discarded":
        sys.exit("%s is discarded; reopen or capture a new ticket" % t["id"])
    fields = S.merge_sound_fields(t.get("body") or "", getattr(a, "notes", "") or "")
    qs = S._questions_list(fields.get("open_questions"))
    missing = [k for k in ("cause", "change", "proof", "deps")
               if not (fields.get(k) or "").strip()]
    if missing:
        sys.exit("sound: missing %s -- pass --notes \"cause=...; change=...; proof=...; deps=none\""
                 % ", ".join(missing))
    deps_text = (fields.get("deps") or "").strip()
    existing = set(x["id"] for x in load_all(board))
    if deps_text.lower() not in ("none", "(none)", "n/a", "-"):
        dep_ids = []
        for tok in re.split(r"[,\s]+", deps_text):
            if not tok:
                continue
            if tok not in existing:
                sys.exit("sound: deps must be real ticket ids or 'none' (unknown %s)" % tok)
            if tok != t["id"] and tok not in (t.get("deps") or []):
                dep_ids.append(tok)
        if dep_ids:
            new_deps = list(t.get("deps") or []) + dep_ids
            check_graph(board, {t["id"]: new_deps})
            t["deps"] = new_deps
    t = _apply_sounded(board, t, fields, who)
    if qs:
        print("%s stays lane=capture (open questions remain)" % t["id"])
    elif S.is_no_change(fields.get("change")):
        print("%s stays lane=capture (investigation with no code change; discard if abandoned)"
              % t["id"])
    else:
        print("%s -> lane=ready (sounded by %s)" % (t["id"], who))
        print("  CoS: tickets dispatch %s --to <seat> --harness cursor" % t["id"])
        print("  cause: %s" % t.get("cause"))
        print("  change: %s" % t.get("change"))
        print("  proof: %s" % t.get("proof"))


def _dispatch_skip_product_spawn(harness):
    """Why dispatch must not start a product job, or empty to allow spawn.

    Gemini is a first-class harness (records harness=gemini) but persist/hooks
    is the wake — never start a live Gemini product job from dispatch.
    TICKETS_DISPATCH_NO_SPAWN=1 skips spawn for every harness.
    """
    if os.environ.get("TICKETS_DISPATCH_NO_SPAWN") == "1":
        return "TICKETS_DISPATCH_NO_SPAWN=1"
    if (harness or "").strip().lower() == "gemini":
        return "gemini: persist/hooks wake; no product job"
    return ""


def cmd_dispatch(a, board):
    """Fatih /plan-dispatch: CoS staffs one ready ticket to one seat. Does not claim."""
    S = _sounding()
    t = load(board, a.id)
    seat = (a.to or "").strip()
    harness = (getattr(a, "harness", "") or "cursor").strip()
    if not seat:
        sys.exit("dispatch: tickets dispatch T-123 --to <seat> --harness cursor")
    if _ticket_lane(t) != "ready":
        sys.exit("dispatch: %s is lane=%s; sound it first" % (t["id"], _ticket_lane(t)))
    if S.ticket_on_hold(t):
        sys.exit("dispatch: %s is on HOLD" % t["id"])
    if t.get("status") != "open":
        sys.exit("dispatch: %s is %s; only open ready tickets" % (t["id"], t.get("status")))
    done = set(x["id"] for x in load_all(board) if x["status"] == "done")
    pending = [d for d in (t.get("deps") or []) if d not in done]
    if pending:
        sys.exit("dispatch: %s still waits on %s" % (t["id"], ", ".join(pending)))
    rows, _note = probe_integration_catalog()
    row = None
    for r in rows:
        if r["id"] == harness:
            row = r
            break
    if harness != "custom" and not harness.startswith("custom:"):
        if row is None:
            sys.exit("dispatch: unknown harness %s (tickets harness available)" % harness)
        reason = S.catalog_dispatch_fail(row)
        if reason:
            sys.exit("dispatch: %s -- will not spawn a FAIL seat" % reason)
    tickets = load_all(board)
    if (t.get("reserved_for") or "").strip() == seat:
        sys.exit("dispatch: %s already reserved for %s" % (t["id"], seat))
    busy = S.worker_busy(tickets, seat, except_id=t["id"])
    if busy:
        sys.exit("dispatch: %s already staffed with %s (one ticket per worker)"
                 % (seat, ", ".join(busy)))
    who = whoami()
    t["reserved_for"] = seat
    t["notes"] = t.get("notes") or []
    t["notes"].append({"by": who, "at": now(),
                       "text": "dispatch: reserved for %s harness=%s" % (seat, harness)})
    save(board, t)
    print("%s reserved for %s harness=%s (worker claims via tickets next / watch)"
          % (t["id"], seat, harness))
    skip_spawn = _dispatch_skip_product_spawn(harness)
    if skip_spawn:
        print("spawn skipped (%s)" % skip_spawn)
        return
    if _live_watch_pids(seat, board=board):
        print("watcher already running for %s" % seat)
        return
    ns = argparse.Namespace(
        name=seat, list=False, stop=False, worktree=getattr(a, "worktree", "") or "",
        harness=harness, tool="", cmd_template=getattr(a, "cmd_template", "") or "",
        roles=None, can=None, cost=None, model="", best_for="", wake_mode=None,
        brief="", exec=getattr(a, "exec", "") or "", master=False, cos=False,
        safe=False, every=60, run_timeout=90, heartbeat=0, persist=True,
        max_runs=getattr(a, "max_runs", None),
    )
    try:
        cmd_spawn(ns, board)
    except SystemExit:
        t2 = load(board, a.id)
        t2.pop("reserved_for", None)
        t2.setdefault("notes", []).append(
            {"by": who, "at": now(), "text": "dispatch: spawn failed; reservation dropped"})
        save(board, t2)
        raise


def cmd_pr_sync(a, board):
    """Fatih /plan-sync: PR merged + pin on trunk → ready to close. Does not self-done."""
    S = _sounding()
    tickets = load_all(board)
    wanted = (getattr(a, "id", "") or "").strip()
    review = [t for t in tickets if t.get("status") == "review"]
    if wanted:
        review = [t for t in review if t["id"] == wanted]
        if not review:
            t = load(board, wanted)
            sys.exit("%s is %s, not IN REVIEW" % (wanted, t.get("status")))
    if not review:
        print("no IN REVIEW tickets")
        return
    for t in review:
        pr = t.get("pr") or ""
        code = S.review_requires_pr(t) or bool(t.get("sounded_at"))
        if not pr:
            tag = "ask: code ticket in review has no --pr; not closable" if code else (
                "ask: IN REVIEW with no PR field")
            print("%s  %s" % (t["id"], tag))
            continue
        state = S.gh_pr_state(pr)
        owner = t.get("owner") or ""
        if state == "merged":
            if S.pin_is_trunk_ancestor(git, _trunk, t):
                print("%s  ready to close (PR %s merged; pin is trunk ancestor). "
                      "Master: tickets done %s --notes \"...\"" % (t["id"], pr, t["id"]))
            else:
                print("%s  waiting: PR %s merged but pin %s is not a trunk ancestor"
                      % (t["id"], pr, t.get("commit") or "(none)"))
        elif state == "waiting":
            print("%s  waiting: PR %s still open / CI or review in flight" % (t["id"], pr))
            if owner:
                post_message(board, whoami(),
                             "pr-sync: %s still waiting on PR %s" % (t["id"], pr),
                             to=owner, re=t["id"])
                print("  bounced to %s via tickets msg" % owner)
        else:
            print("%s  ask: could not read PR %s (missing, 403, or no gh)" % (t["id"], pr))


def cmd_plan_status(a, board):
    """Fatih /plan-status: capture / ready / waiting-on-merge / blocked-HOLD-discarded."""
    S = _sounding()
    tickets = load_all(board)
    done = set(t["id"] for t in tickets if t["status"] == "done")
    capture, ready, waiting, parked = [], [], [], []
    for t in tickets:
        lane = _ticket_lane(t)
        if lane == "capture":
            capture.append(t)
        elif lane == "discarded":
            parked.append(t)
        elif t.get("status") == "review":
            waiting.append(t)
        elif t.get("status") == "blocked" or S.ticket_on_hold(t):
            parked.append(t)
        elif (t.get("status") == "open" and lane == "ready"
              and all(d in done for d in t.get("deps") or [])
              and not S.ticket_on_hold(t)):
            ready.append(t)
    def _emit(title, rows):
        print(title)
        if not rows:
            print("  (none)")
            return
        for t in rows:
            extra = ""
            if t.get("pr"):
                extra = " pr=%s" % t["pr"]
            if _ticket_lane(t) != "ready":
                extra += " lane=%s" % _ticket_lane(t)
            print("  %s  %s%s" % (t["id"], t["title"][:70], extra))
    _emit("Capture (sound these)", capture)
    _emit("Ready (dispatchable)", ready)
    _emit("Waiting on merge", waiting)
    _emit("Blocked / HOLD / discarded", parked)
    if getattr(a, "write_master", False):
        path = master_path(board)
        if os.path.exists(path):
            body = open(path).read()
            section = ("\n## Plan\n\n"
                       "capture=%d ready=%d waiting-on-merge=%d parked=%d\n"
                       "(written by tickets plan-status --write-master)\n"
                       % (len(capture), len(ready), len(waiting), len(parked)))
            marker = "\n## Plan\n"
            if marker in body:
                pre, rest = body.split(marker, 1)
                nxt = rest.find("\n## ")
                body = pre + section + (rest[nxt:] if nxt != -1 else "")
            else:
                body = body.rstrip() + "\n" + section
            with open(path, "w") as f:
                f.write(body)
            print("wrote Plan section to %s" % path)


def cmd_schedule(a, board):
    """Cron recurring wake for a named seat. Persist/hooks poke; no product spawn."""
    S = _seat_schedule()
    data = S.load_schedule(board)
    seats = data.setdefault("seats", {})
    seat = (getattr(a, "seat", "") or "").strip()
    cron = (getattr(a, "cron", "") or "").strip()
    every_spec = (getattr(a, "every", "") or "").strip()
    if getattr(a, "uninstall", False):
        found = S.remove_crontab(board)
        print("crontab %s for this board" % ("removed" if found else "had no tickets-schedule line"))
        return
    if getattr(a, "due", False):
        now_iso = now()
        due = S.due_seats(data, now_iso)
        if not due:
            print("no due schedules")
            return
        now_dt = S.parse_iso(now_iso)
        for name in due:
            wf = load_workforce(board)
            if name not in wf:
                print("schedule: skip %s (not joined)" % name)
                continue
            ns = argparse.Namespace(
                owner="", text="scheduled wake", to=name,
                re=getattr(a, "re", "") or "", task=True)
            cmd_msg(ns, board)
            entry = seats.setdefault(name, {})
            entry["last"] = now_iso
            entry["next"] = S.bump_next(entry, now_dt)
            entry["enabled"] = True
        S.save_schedule(board, data)
        return
    if getattr(a, "list", False) or (
            not seat and not cron and not every_spec
            and not getattr(a, "remove", False)
            and not getattr(a, "install", False)):
        if not seats:
            print("no scheduled seats")
            return
        print("%-16s %-22s %-22s %s" % ("seat", "when", "next", "last"))
        for name, e in sorted(seats.items()):
            when = ("every %ss" % e["every_sec"]) if e.get("every_sec") else (e.get("cron") or "-")
            print("%-16s %-22s %-22s %s" % (
                name, when[:22], (e.get("next") or "-"), (e.get("last") or "-")))
        return
    if getattr(a, "remove", False):
        if not seat:
            sys.exit("schedule --remove needs a seat")
        if seat not in seats:
            sys.exit("schedule: no entry for %s" % seat)
        seats.pop(seat, None)
        S.save_schedule(board, data)
        print("removed schedule for %s" % seat)
        if not seats:
            S.remove_crontab(board)
        return
    if getattr(a, "install", False) and not seat and not cron and not every_spec:
        path, line = S.upsert_crontab(board, os.path.realpath(__file__), sys.executable)
        print("installed crontab (%s)" % path)
        print(line)
        return
    if not seat:
        sys.exit("schedule SEAT --cron '*/15 * * * *'  or  schedule SEAT --every 15m")
    wf = load_workforce(board)
    if seat not in wf:
        sys.exit("schedule: unknown seat %s (tickets join first)" % seat)
    if cron and every_spec:
        sys.exit("schedule: pass --cron or --every, not both")
    if not cron and not every_spec:
        sys.exit("schedule %s needs --cron or --every" % seat)
    every_sec = 0
    if every_spec:
        try:
            every_sec = S.parse_every(every_spec)
        except ValueError as e:
            sys.exit(str(e))
        if every_sec <= 0:
            sys.exit("schedule --every must be > 0")
    if cron:
        try:
            S.cron_match(cron, datetime.now(timezone.utc))
        except ValueError as e:
            sys.exit("schedule: %s" % e)
    now_iso = now()
    now_dt = S.parse_iso(now_iso)
    entry = seats.get(seat) or {}
    entry["enabled"] = True
    entry["by"] = whoami()
    entry["at"] = now_iso
    if every_sec:
        entry["every_sec"] = every_sec
        entry.pop("cron", None)
    else:
        entry["cron"] = cron
        entry.pop("every_sec", None)
    entry["next"] = now_iso  # due on the next `schedule --due`
    seats[seat] = entry
    S.save_schedule(board, data)
    print("scheduled %s %s next=%s (persist/hooks wake; no product spawn)" % (
        seat, ("every %s" % every_spec) if every_spec else "cron %s" % cron, entry["next"]))
    if getattr(a, "install", False):
        path, line = S.upsert_crontab(board, os.path.realpath(__file__), sys.executable)
        print("installed crontab (%s)" % path)
        print(line)


def cmd_discard(a, board):
    """Move a ticket to lane=discarded so retro can see it. Not done."""
    t = load(board, a.id)
    reason = (a.reason or "").strip()
    if not reason:
        sys.exit('discard needs --reason "why this was abandoned"')
    if t.get("status") == "done":
        sys.exit("%s is already done" % t["id"])
    t["lane"] = "discarded"
    t["discarded_reason"] = reason
    t["status"] = "open"
    t["owner"] = ""
    t.pop("reserved_for", None)
    t["notes"] = t.get("notes") or []
    t["notes"].append({"by": whoami(), "at": now(), "text": "discarded: %s" % reason})
    save(board, t)
    print("%s -> lane=discarded (%s)" % (t["id"], reason))


def cmd_retro(a, board):
    """Fatih /plan-retro: file a capture ticket proposing a brief/skill edit, or nothing."""
    S = _sounding()
    tickets = load_all(board)
    cutoff = S.since_cutoff(getattr(a, "since", "") or "")
    repeats = S.retro_repeats(tickets, cutoff)
    if not repeats:
        print("no retro ticket")
        return
    n, gram, ids = repeats[0]
    body = (
        "Repeated misunderstanding: %r appears in %s.\n\n"
        "Proposed edit (do not apply silently): add a paragraph to "
        ".tickets/briefs/_shared.md or the named role brief that states this "
        "once, in plain English.\n\n"
        "Proof: tickets show %s\n\n"
        "## Cause or spec\n"
        "The same misunderstanding repeated across finished/discarded work.\n\n"
        "## Change\n"
        "(sound this ticket, then a worker edits the brief)\n\n"
        "## Proof\n"
        "tickets show %s; the brief paragraph is present\n\n"
        "## Deps\n"
        "none\n\n"
        "## Open questions\n"
        "Which brief file should gain the paragraph?\n"
        % (gram, ", ".join(ids), ", ".join(ids), ", ".join(ids))
    )
    t = create(board, "Retro: %s" % gram[:50], body, "docs", [], 2, "", "", [])
    t["lane"] = "capture"
    t["open_questions"] = ["Which brief file should gain the paragraph?"]
    t["notes"] = t.get("notes") or []
    t["notes"].append({"by": whoami(), "at": now(),
                       "text": "retro from %s" % ", ".join(ids)})
    save(board, t)
    print("captured %s  %s  lane=capture" % (t["id"], t["title"]))
    print("  CoS/master: tickets sound %s after choosing the brief" % t["id"])


def cmd_list(a, board):
    tickets = load_all(board)
    if a.status:
        tickets = [t for t in tickets if t["status"] == a.status]
    if a.role:
        wanted = [r.strip() for r in a.role.split(",") if r.strip()]
        tickets = [t for t in tickets if t.get("role") in wanted]
    if a.owner:
        tickets = [t for t in tickets if t.get("owner") == a.owner]
    if a.json:
        print(json.dumps(tickets, indent=2))
        return
    if not tickets:
        print("no tickets")
        return
    all_t = load_all(board)
    for t in tickets:
        print(line(t, all_t))


def cmd_board(a, board):
    tickets = load_all(board)
    if not tickets:
        # Claude's SessionStart hook calls `tickets board`. An initialized
        # project may legitimately have no tickets yet, but its durable
        # knowledge is still useful. Stay silent when there is no board at
        # all so the global hook remains inert in unrelated directories.
        if os.path.isdir(board):
            owner = whoami()
            if not owner.startswith("agent-"):
                inherited = knowledge_context(board, owner, max_chars=1800)
                if inherited:
                    print(inherited)
        return
    counts = {}
    for t in tickets:
        counts[t["status"]] = counts.get(t["status"], 0) + 1
    head = "Ticket board (%s): %s" % (
        board,
        ", ".join("%d %s" % (counts[s], s) for s in STATUSES if s in counts),
    )
    print(head)
    cur = active_sprint(board)
    m = current_master(board)
    hdr = []
    if cur:
        mine = [t for t in tickets if t.get("sprint") == cur["id"]]
        d, n, _, _ = progress(mine)
        hdr.append("sprint %s %s" % (cur["id"], bar(d, n, 10)))
    hdr.append("master: %s" % (m["owner"] if m else "nobody (tickets master take)"))
    print("  " + " | ".join(hdr))
    for t in tickets:
        if t["status"] != "done" or a.all:
            print("  " + line(t, tickets))
    ready = unblocked(board, tickets)
    if ready:
        print("  -> ready to claim: %s" % ", ".join(t["id"] for t in ready))
    if not a.quiet:
        print(
            "Shared across Claude/Codex/Cursor. `tickets next` claims one atomically; "
            "`tickets done <id> --notes \"...\"` hands off to dependents."
        )
    owner = whoami()
    if not owner.startswith("agent-"):
        inherited = knowledge_context(board, owner, max_chars=1800)
        if inherited:
            print(inherited)


def workflow_graph(tickets, include_done=False):
    """JSON twin of `tickets graph` for the command board.

    Active tickets plus the specific deps they wait on. Edges are real
    `--after` links, not a dump of titles and not prose blockers.
    """
    by_id = dict((t["id"], t) for t in tickets)
    done = set(t["id"] for t in tickets if t["status"] == "done")
    kids = {}
    for t in tickets:
        for d in t.get("deps") or []:
            kids.setdefault(d, []).append(t["id"])
    if include_done:
        keep = set(by_id)
    else:
        keep = set(t["id"] for t in tickets if t["status"] in ("open", "claimed", "blocked", "review"))
        for tid in list(keep):
            for d in (by_id.get(tid) or {}).get("deps") or []:
                if d in by_id:
                    keep.add(d)
    order = [t["id"] for t in tickets if t["id"] in keep]

    def node_of(tid):
        t = by_id[tid]
        deps = list(t.get("deps") or [])
        waiting = [d for d in deps if d not in done]
        return {
            "id": tid,
            "title": t.get("title", ""),
            "status": t["status"],
            "label": LABEL.get(t["status"], t["status"]),
            "mark": MARK.get(t["status"], "[?]"),
            "owner": t.get("owner") or "",
            "role": t.get("role") or "",
            "deps": deps,
            "waiting": waiting,
            "children": [c for c in kids.get(tid, []) if c in keep],
        }

    nodes = [node_of(tid) for tid in order]
    edges = []
    for n in nodes:
        for d in n["deps"]:
            if d in keep:
                edges.append({"from": d, "to": n["id"], "waiting": d not in done})
    seen = set()

    def walk(tid):
        n = node_of(tid)
        if tid in seen:
            return {"id": tid, "repeat": True, "children": []}
        seen.add(tid)
        return {"id": tid, "children": [walk(c) for c in n["children"]]}

    roots = [t["id"] for t in tickets
             if t["id"] in keep and not [d for d in (t.get("deps") or []) if d in keep]]
    forest = [walk(r) for r in roots]
    orphans = [tid for tid in order if tid not in seen]
    counts = {}
    for t in tickets:
        if t["id"] in keep:
            counts[t["status"]] = counts.get(t["status"], 0) + 1
    return {
        "view": "all" if include_done else "active",
        "counts": counts,
        "nodes": nodes,
        "edges": edges,
        "forest": forest,
        "roots": roots,
        "orphans": orphans,
    }


def cmd_graph(a, board):
    tickets = load_all(board)
    if not tickets:
        print("no tickets")
        return
    by_id = dict((t["id"], t) for t in tickets)
    done = set(t["id"] for t in tickets if t["status"] == "done")
    kids = {}
    for t in tickets:
        for d in t.get("deps", []):
            kids.setdefault(d, []).append(t["id"])

    counts = {}
    for t in tickets:
        counts[t["status"]] = counts.get(t["status"], 0) + 1
    print("Dependency graph (%s)" % ", ".join("%d %s" % (counts[s], s) for s in STATUSES if s in counts))

    def label(tid):
        t = by_id[tid]
        s = "%s %s %s" % (MARK.get(t["status"], "[?]"), tid, t["title"])
        bits = []
        if t.get("role"):
            bits.append(t["role"])
        if t.get("owner"):
            bits.append("@" + t["owner"])
        lane = _ticket_lane(t)
        if lane != "ready":
            bits.append("lane=" + lane)
        waiting = [d for d in t.get("deps", []) if d not in done]
        if waiting and t["status"] == "open":
            bits.append("waiting on " + ",".join(waiting))
        if bits:
            s += "  (%s)" % "; ".join(bits)
        return s

    seen = set()

    def walk(tid, prefix, last, ancestors):
        if tid in ancestors:
            print(prefix + ("`- " if last else "|- ") + "%s (cycle)" % tid)
            return
        mark = "`- " if last else "|- "
        line_txt = label(tid)
        if tid in seen and kids.get(tid):
            line_txt += "  [shown above]"
        print(prefix + mark + line_txt)
        if tid in seen:
            return
        seen.add(tid)
        nxt = prefix + ("   " if last else "|  ")
        children = kids.get(tid, [])
        for i, c in enumerate(children):
            walk(c, nxt, i == len(children) - 1, ancestors | {tid})

    roots = [t["id"] for t in tickets if not [d for d in t.get("deps", []) if d in by_id]]
    for i, r in enumerate(roots):
        walk(r, "", i == len(roots) - 1, set())

    orphans = [t["id"] for t in tickets if t["id"] not in seen]
    if orphans:
        print("unreachable (part of a cycle):")
        for o in orphans:
            print("  " + label(o))
    ghosts = dangling(tickets)
    if ghosts:
        print("broken references:")
        for tid, miss in ghosts.items():
            print("  %s -> %s (no such ticket)" % (tid, ", ".join(miss)))


def cmd_map(a, board):
    """Sprint -> epic -> ticket, with deps inline. The whole board, traversable."""
    tickets = load_all(board)
    epics = load_epics(board)
    sprints = load_sprints(board)
    done = set(t["id"] for t in tickets if t["status"] == "done")

    def tline(t):
        s = "%s %s %s" % (MARK.get(t["status"], "[?]"), t["id"], t["title"][:60])
        bits = []
        if t.get("owner"):
            bits.append("@" + t["owner"])
        if t.get("role"):
            bits.append(t["role"])
        if t.get("needs"):
            bits.append("needs " + ",".join(t["needs"]))
        waiting = [d for d in t.get("deps", []) if d not in done]
        if waiting and t["status"] == "open":
            bits.append("waits " + ",".join(waiting))
        elif t.get("deps"):
            bits.append("after " + ",".join(t["deps"]))
        down = [x["id"] for x in tickets if t["id"] in x.get("deps", [])]
        if down:
            bits.append("-> " + ",".join(down))
        return s + ("  (%s)" % "; ".join(bits) if bits else "")

    def epic_block(indent, eid, pool):
        mine = [t for t in pool if t.get("epic") == eid]
        if not mine and eid:
            return
        if eid:
            e = next((x for x in epics if x["id"] == eid), {"title": "?"})
            d, n, c, b = progress(mine)
            print("%s%s %s  %s" % (indent, eid, e["title"][:50], bar(d, n, 12)))
        else:
            print("%s(no epic)" % indent)
        for t in mine:
            if t["status"] == "done" and not a.all:
                continue
            print("%s  %s" % (indent, tline(t)))

    groups = [s for s in sprints if s.get("status") != "done" or a.all]
    for s in groups:
        pool = [t for t in tickets if t.get("sprint") == s["id"]]
        d, n, c, b = progress(pool)
        print("%s [%s] %s  %s" % (s["id"], s.get("status"), s.get("goal", ""), bar(d, n)))
        for eid in [e["id"] for e in epics] + [""]:
            epic_block("  ", eid, pool)
    rest = [t for t in tickets if not t.get("sprint") or t["sprint"] not in set(s["id"] for s in groups)]
    if rest:
        print("Backlog (no sprint)")
        for eid in [e["id"] for e in epics] + [""]:
            epic_block("  ", eid, rest)
    if not a.all:
        print("(done tickets hidden; --all shows them)")


def cmd_show(a, board):
    tickets = load_all(board)
    t = load(board, a.id)
    if a.json:
        print(json.dumps(t, indent=2))
    else:
        print(detail(board, t, tickets))


def _filter_ready(ready, roles):
    if roles is None:
        return list(ready)
    if roles == []:
        return []
    return [t for t in ready if t.get("role") in ("",) or t.get("role") in roles]


def _reserved_agent(t):
    return (t.get("reserved_for") or "").strip()


def _reservation_blocks(t, owner, steal_id=""):
    """T-552: next skips tickets reserved for someone else unless --steal."""
    who = _reserved_agent(t)
    if not who or who == owner:
        return False
    if steal_id and t.get("id") == steal_id:
        return False
    return True


def _ticket_on_hold(t):
    """T-781: HOLD is a condition, not a claimable ready ticket.

    First-class `hold` wins. Body that starts with HOLD (T-773/T-774 style)
    is the same so next does not hand out tester-week parks.
    """
    if t.get("hold"):
        return True
    body = (t.get("body") or "").lstrip()
    return body.upper().startswith("HOLD")


def cmd_hold(a, board):
    t = load(board, a.id)
    if getattr(a, "clear", False):
        t["hold"] = False
        t.pop("hold_reason", None)
        text = "hold cleared"
    else:
        t["hold"] = True
        reason = (getattr(a, "reason", None) or "").strip()
        if reason:
            t["hold_reason"] = reason
        text = "HOLD" + ((": " + reason) if reason else "")
    t["notes"].append({"by": whoami(), "at": now(), "text": text})
    save(board, t)
    print("%s %s" % (a.id, text))


def _start_successors(board, finished_id):
    """T-781: a successful ticket starts unblocked children (no human next)."""
    tickets = load_all(board)
    children = [x for x in unblocked(board, tickets) if finished_id in x.get("deps", [])]
    freed = [x["id"] for x in children]
    started, held = [], []
    for child in children:
        if _ticket_on_hold(child):
            held.append(child["id"])
            continue
        who = _reserved_agent(child) or (child.get("suggested") or "").strip()
        text = "unblocked %s after %s -- start (success trigger)" % (child["id"], finished_id)
        if who:
            post_message(board, whoami(), text, to=who, re=child["id"], task=True)
            started.append("%s -> %s" % (child["id"], who))
        else:
            started.append(child["id"])
    return freed, started, held


def _may_set_reservation(board, who):
    if who in ("optimizer", "planner"):
        return True
    m = current_master(board) or {}
    return who in ((m.get("owner") or ""), (m.get("cos") or ""))


def _next_refusal_parts(ready_all, roles, owner, steal_id, board):
    """T-558 F2: leftover ready tickets are role-miss or reservation, not one bucket."""
    role_filtered = _filter_ready(ready_all, roles)
    role_ok = [t for t in role_filtered if can_do(board, owner, t)]
    role_miss = [t for t in ready_all if t not in role_filtered]
    reserved_miss = [t for t in role_ok if _reservation_blocks(t, owner, steal_id)]
    return role_miss, reserved_miss


def cmd_next(a, board):
    owner = whoami(a.owner)
    roles = roles_for(board, owner, a.role)
    if roles == [] and not a.role:
        print(
            "agent %r has no default roles (see .tickets/roles.json). "
            "Pass --role or do not claim." % owner
        )
        sys.exit(1)
    tickets = load_all(board)
    held = [t for t in tickets if t["status"] == "claimed" and t.get("owner") == owner]
    if held and not a.another:
        print("you already hold %s -- finish it (tickets done/block/reopen) before claiming "
              "more, or pass --another if you really want to work two in parallel."
              % ", ".join(t["id"] for t in held))
        sys.exit(1)
    steal_id = (getattr(a, "steal", None) or "").strip()
    ready_all = unblocked(board, tickets)
    ready = [t for t in _filter_ready(ready_all, roles) if can_do(board, owner, t)]
    ready = [t for t in ready if not _reservation_blocks(t, owner, steal_id)]
    ready = [t for t in ready if not _ticket_on_hold(t)]
    cur = active_sprint(board)
    cur_id = cur["id"] if cur else None
    rank = cost_rank(board, owner)

    def order(t):
        p = t.get("priority", 2)
        # expensive agents go to hard/critical work first; cheap ones to the
        # routine tickets first, so the master's budget stretches further
        cost_key = p if rank == 2 else (-p if rank == 0 else 0)
        needs_key = 0 if t.get("needs") else 1  # a ticket only I can do comes first
        steal_key = 0 if steal_id and t["id"] == steal_id else 1
        reserved_key = 0 if _reserved_agent(t) == owner else 1
        mine_key = 0 if t.get("suggested") == owner else (2 if t.get("suggested") else 1)
        return (steal_key, reserved_key, mine_key, 0 if cur_id and t.get("sprint") == cur_id else 1, needs_key, cost_key, p, t["id"])

    ready.sort(key=order)
    for t in ready:
        got = try_claim(board, t["id"], owner)
        if got:
            checkin(board, owner, got["id"])
            print(detail(board, got, load_all(board)))
            inherited = knowledge_context(board, owner)
            if inherited:
                print("")
                print(inherited)
            warn = worktree_warning(owner)
            if warn:
                print("")
                print(warn)
            print("")
            print("Post progress with `tickets update %s \"...\"` at least every %d min; "
                  "finish with `tickets done %s --notes \"branch@sha, paths, decisions\"`."
                  % (got["id"], UPDATE_EVERY_MIN, got["id"]))
            return
    open_blocked = [t for t in tickets if t["status"] == "open"]
    if not open_blocked:
        print("no tickets available (nothing open)")
        sys.exit(1)
    taken = [t["id"] for t in ready if load(board, t["id"])["status"] != "open"]
    if taken:
        print("lost the race; already claimed: %s" % ", ".join(taken))
        sys.exit(1)
    cannot = [t for t in _filter_ready(ready_all, roles) if not can_do(board, owner, t)]
    if cannot:
        print("%d ready ticket(s) need capabilities you have not registered: %s" % (
            len(cannot), "; ".join("%s needs %s" % (t["id"], ",".join(t["needs"])) for t in cannot)))
        print("register with: tickets join %s --can %s" % (owner, ",".join(sorted(set(
            n for t in cannot for n in t["needs"])))))
    role_miss, reserved_miss = _next_refusal_parts(
        ready_all, roles, owner, steal_id, board)
    parts = []
    if role_miss:
        parts.append("%d ready for other roles: %s" % (
            len(role_miss), ", ".join(t["id"] for t in role_miss)))
    if reserved_miss:
        parts.append("%d reserved for other agents: %s" % (
            len(reserved_miss), ", ".join(t["id"] for t in reserved_miss)))
    if roles is not None and parts:
        print("no ticket for roles %s; %s" % (roles, "; ".join(parts)))
        sys.exit(1)
    cyc = find_cycle(tickets)
    if cyc:
        print(
            "DEADLOCK: dependency cycle %s -- no ticket can ever start.\n"
            "Fix with: tickets dep %s --drop %s" % (" -> ".join(cyc), cyc[0], cyc[1])
        )
        sys.exit(2)
    ghosts = dangling(tickets)
    if ghosts:
        print("BROKEN: these wait on tickets that do not exist:")
        for tid, miss in ghosts.items():
            print("  %s -> %s" % (tid, ", ".join(miss)))
        print("Fix with: tickets dep <id> --drop <missing-id>")
        sys.exit(2)
    holders = sorted(set(t.get("owner") or "?" for t in tickets if t["status"] == "claimed"))
    msg = "no ticket ready: %d open, all waiting on unfinished work" % len(open_blocked)
    if holders:
        msg += " (in progress with: %s)" % ", ".join(holders)
    print(msg)
    sys.exit(1)


def cmd_claim(a, board):
    owner = whoami(a.owner)
    t = load(board, a.id)
    lane = _ticket_lane(t)
    if lane != "ready":
        sys.exit("%s is lane=%s; sound it before claiming (tickets sound %s)"
                 % (a.id, lane, a.id))
    got = try_claim(board, a.id, owner)
    if not got:
        sys.exit("%s is already taken" % a.id)
    checkin(board, owner, got["id"])
    print(detail(board, got, load_all(board)))
    warn = worktree_warning(owner)
    if warn:
        print("")
        print(warn)


def cmd_review(a, board):
    """Agent finished: submit for the master to review + merge. Records branch@sha."""
    t = load(board, a.id)
    if t["status"] not in ("claimed", "blocked", "open"):
        sys.exit("%s is %s; only in-progress work can be submitted" % (a.id, LABEL[t["status"]]))
    if not a.notes:
        sys.exit('review needs --notes "what to look at: paths, tests run, decisions"')
    if _sounding().review_requires_pr(t) and not (a.pr or "").strip():
        sys.exit('review: sounded code tickets require --pr N (or role=docs|pm and body says "no PR")')
    # T-272: every probe below asks the tree the DELIVERABLE is in, which is
    # not the tree the command was run from whenever --artifact is passed.
    art = artifact_tree(a)
    g = git_state(cwd=art)
    if g and g["branch"] in ("main", "master") and not a.force:
        sys.exit("RULE: submit from your own worktree branch, not %r (or --force)" % g["branch"])
    if g and g["dirty"] and not a.force:
        sys.exit("RULE: %d uncommitted files in %s -- commit before submitting for review "
                 "(or --force)" % (g["dirty"], g["top"]))
    if g and not a.force:
        trunk = _trunk(cwd=art)
        if git("merge-base", "--is-ancestor", trunk, "HEAD", cwd=art) is None:
            sys.exit(_behind_trunk_refusal(g, art, trunk))
    # `owner` decides who the ticket is filed under (unchanged: claim it via
    # review if nobody holds it yet, otherwise keep the existing owner).
    # `author` is who actually ran this command -- always whoami(), never
    # borrowed from `owner` -- so the note, checkin and message below credit
    # whoever really submitted, even when that is not the recorded owner
    # (T-238; this was the same by=owner bug as cmd_note, one level up).
    owner = t.get("owner") or whoami(a.owner)
    author = whoami(a.owner)
    t["status"] = "review"
    t["owner"] = owner
    t["review_at"] = now()
    text = a.notes
    if g:
        # T-215 records WHICH repo the pin belongs to so `tickets merge` can
        # refuse a cross-repo ancestry close. T-272 fixes what that repo is:
        # the artifact's, not the caller's cwd.
        stamp = _record_pin(t, g, art)
        text = "%s -- %s" % (stamp, text)
    if a.pr:
        t["pr"] = a.pr
        text += " (PR %s)" % a.pr
    t["notes"].append({"by": author, "at": now(), "text": "REVIEW: " + text})
    save(board, t)
    checkin(board, author, t["id"], "submitted %s for review" % t["id"])
    if owner != author:
        # T-428: the owner still holds the ticket in review; do not copy the
        # submitter's git location onto their agent record.
        _agent_set(board, owner, ticket=t["id"],
                   note="submitted %s for review by %s" % (t["id"], author))
    tmr = timing(t)
    _safe(lambda: traj_event(board, "review", agent=author, ticket=t,
                             state_before="claimed", state_after="review",
                             outcome="review", notes_len=len(a.notes or ""),
                             active_hours=_round3(tmr.get("active")),
                             pin=t.get("commit", ""),
                             **_traj_git(cwd=art)), None)
    m = current_master(board) or {}
    master_name, cos_name = _notify_review_submitted(board, author, t["id"], text, m)
    tm = timing(t)
    if t.get("commit"):
        print("pinned %s in %s (%s)" % (
            t["commit"], t.get("repo") or "?",
            "artifact tree %s" % t["artifact_dir"] if t.get("repo_source") == "artifact"
            else "this checkout -- pass --artifact <dir> if the deliverable is in another repo"))
    if cos_name:
        who = "CoS (%s) tasked" % cos_name
        if master_name and master_name != cos_name:
            who += "; master (%s) notified" % master_name
    else:
        who = "master%s notified" % ((" (%s)" % master_name) if master_name else "")
    print("%s -> IN REVIEW after %s of work; %s. Claim your next ticket." % (
        t["id"], fmt_hours(tm["active"]), who))
    _finish_followup(board, t["id"], "review")


def _trunk(cwd=None):
    for b in ("main", "master"):
        if git("rev-parse", "--verify", "-q", b, cwd=cwd) is not None:
            return b
    return "main"


def cmd_sync(a, board):
    """Agent side: bring main into my branch now, so the master's merge is trivial.

    T-272: `--artifact` syncs the tree the deliverable is in. `tickets review`
    checks the artifact branch against ITS trunk, so the sync that clears that
    check has to happen in the same tree -- otherwise the workflow instruction
    "sync before review" quietly syncs a repo the review never looks at.

    T-423: the local `main`/`master` ref is not the source of truth for "am I
    behind" -- nothing here moves it, so a plain `git fetch` (which only
    updates `origin/<trunk>`) leaves it stale indefinitely. Comparing against
    it made sync answer "already contains main; nothing to do" for a branch
    that was genuinely behind origin. Fetch origin's trunk and compare/merge
    against `origin/<trunk>` instead, which the fetch just made fresh. If the
    fetch cannot be done at all (no origin remote, or it is unreachable),
    refuse rather than silently falling back to the stale local ref -- that
    fallback is exactly the bug.
    """
    art = artifact_tree(a)
    g = git_state(cwd=art)
    if not g:
        sys.exit("not in a git repo")
    trunk = _trunk(cwd=art)
    if g["branch"] in ("main", "master"):
        sys.exit("you are on %s; sync is for your own worktree branch" % g["branch"])
    if g["dirty"] and not a.force:
        sys.exit("%d uncommitted files; commit first (sync merges %s into your branch)" % (g["dirty"], trunk))
    if git("remote", "get-url", "origin", cwd=art) is None:
        sys.exit("no 'origin' remote configured; cannot confirm %s is not stale without one "
                  "(comparing against the local %s ref is the bug this refusal exists to avoid)"
                  % (trunk, trunk))
    if git("fetch", "origin", trunk, cwd=art) is None:
        sys.exit("could not fetch origin/%s -- refusing to sync against a possibly-stale local "
                  "ref; retry once the remote is reachable" % trunk)
    remote_trunk = "origin/" + trunk
    if git("merge-base", "--is-ancestor", remote_trunk, "HEAD", cwd=art) is not None:
        print("%s already contains %s; nothing to do" % (g["branch"], remote_trunk))
        return
    import subprocess
    # cwd=art AND the scrub (T-287). Both, or this merge is wrong in one of two
    # ways: os.getcwd() merges the repo the agent is standing in rather than the
    # artifact repo every other call in this function targets, and without the
    # env scrub an ambient GIT_DIR re-points it anyway. Note this is a real
    # merge -- getting the tree wrong here writes commits into another repo.
    r = subprocess.run(["git", "merge", "--no-edit", "-m", "Sync %s into %s" % (remote_trunk, g["branch"]), remote_trunk],
                       cwd=art, env=_clean_git_env(), capture_output=True, text=True)
    if r.returncode == 0:
        print("merged %s into %s -> %s" % (remote_trunk, g["branch"], git("rev-parse", "--short", "HEAD", cwd=art)))
        checkin(board, whoami(), None, "synced with %s" % remote_trunk)
        return
    conflicted = (git("diff", "--name-only", "--diff-filter=U", cwd=art) or "").splitlines()
    print("CONFLICTS merging %s into %s -- these files need you:" % (remote_trunk, g["branch"]))
    for f in conflicted:
        print("  " + f)
    print("Resolve, `git add` them, `git commit`, then `tickets review` again. "
          "Or `git merge --abort` and ask the master (`tickets msg`).")
    sys.exit(1)


MERGE_CONFIG_DEFAULT = {
    "test": ".venv/bin/python -m pytest -q -p no:cacheprovider -x --ignore=.worktrees --ignore=.claude",
    "integration_dir": ".worktrees/integration",
    "doc_exts": [".md", ".txt"],
}


def merge_config(board):
    cfg = dict(MERGE_CONFIG_DEFAULT)
    p = os.path.join(board, "merge.json")
    if os.path.isfile(p):
        try:
            with open(p) as f:
                cfg.update(json.load(f))
        except (IOError, ValueError):
            pass
    return cfg


def parse_review_sha(stamp):
    """Extract the git object from a review stamp like 'branch@abc1234'."""
    if not stamp or "@" not in str(stamp):
        return None
    return str(stamp).rsplit("@", 1)[-1].strip() or None


def require_integrator(board, owner, force_master=False):
    """Only the designated master.json owner may run tickets merge."""
    m = current_master(board)
    if not m or not m.get("owner"):
        sys.exit("refused: no designated integrator in master.json (tickets master take first)")
    # The master (planner) and the chief of staff (review/unblock/merge) may both integrate.
    if owner not in (m["owner"], m.get("cos")) and not force_master:
        sys.exit("rejected owner: %s is not the designated integrator (master %s, cos %s); "
                 "pass --force-master only for break-glass recovery" % (owner, m["owner"], m.get("cos") or "-"))
    return m


class IntegrationLock:
    """Serialize merge across processes; failed acquisition means another integrator is active."""

    def __init__(self, board):
        self.path = os.path.join(board, "merge.lock")
        self.fd = None

    def __enter__(self):
        import fcntl
        self.fd = os.open(self.path, os.O_CREAT | os.O_RDWR, 0o644)
        try:
            fcntl.flock(self.fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except (BlockingIOError, OSError):
            os.close(self.fd)
            self.fd = None
            sys.exit("refused: another integrator holds merge.lock; only one merge at a time")
        os.ftruncate(self.fd, 0)
        os.write(self.fd, ("%s %s\n" % (whoami(), now())).encode())
        return self

    def __exit__(self, *exc):
        import fcntl
        if self.fd is not None:
            try:
                fcntl.flock(self.fd, fcntl.LOCK_UN)
            finally:
                os.close(self.fd)
                self.fd = None


def _order_shas_by_ancestry(sh, cwd, shas):
    """Oldest-first unique SHAs so parent commits merge before descendants."""
    unique, seen = [], set()
    for s in shas:
        full = sh("git", "rev-parse", s, cwd=cwd)
        if full.returncode != 0:
            continue
        key = full.stdout.strip()
        if key and key not in seen:
            seen.add(key)
            unique.append(key)
    ordered = []
    remaining = list(unique)
    while remaining:
        roots = [
            cand for cand in remaining
            if not any(
                other != cand
                and sh("git", "merge-base", "--is-ancestor", other, cand, cwd=cwd).returncode == 0
                for other in remaining
            )
        ]
        if not roots:
            ordered.extend(remaining)
            break
        for cand in roots:
            ordered.append(cand)
            remaining.remove(cand)
    return ordered


def cmd_merge(a, board):
    """Master side: integrate pinned review SHAs into main behind a green test run.

    Guards (T-064 / T-079):
      - only the designated integrator (master.json) may merge
      - process lock serializes concurrent integrators
      - merge recorded review commits, not moving branch tips
      - close only tickets whose submitted SHA is ancestor of resulting main
      - refuse blanket -X ours unless --discard-code
      - failed checks leave review tickets untouched
      - close only tickets IN REVIEW, and only from the repo their pin names
        (T-215): ancestry alone is not identity -- this repo's own history can
        trivially "contain" a foreign sha that was never built on it
    """
    import subprocess
    # T-272: the board lives in one repo, the deliverable may live in another.
    # Integration must be able to happen where the artifact is, or a correctly
    # pinned cross-repo ticket could never be closed by a merge at all --
    # `root` used to be the board's own parent unconditionally.
    root = artifact_tree(a) or os.path.dirname(board)
    cfg = merge_config(board)
    trunk = _trunk(cwd=root)
    owner = whoami(a.owner)
    require_integrator(board, owner, force_master=getattr(a, "force_master", False))
    merge_repo = repo_identity(root)

    def sh(*args, cwd=root):
        # cwd= alone does not stop a leaked GIT_DIR/GIT_COMMON_DIR from
        # overriding repo discovery (T-243) -- the env must be scrubbed too.
        # _clean_git_env keeps GIT_AUTHOR_*/GIT_COMMITTER_*, which this
        # function's `git commit` depends on for correct authorship.
        return subprocess.run(list(args), cwd=cwd, capture_output=True, text=True,
                              env=_clean_git_env())

    with IntegrationLock(board):
        tickets = load_all(board)
        queue = [t for t in tickets if t["status"] == "review"]
        branches = list(a.branches) or sorted(set(t.get("branch") for t in queue if t.get("branch")))
        if not branches:
            sys.exit("nothing to merge: no branches given and the review queue is empty")
        dirty = sh("git", "status", "--porcelain", "--untracked-files=no").stdout.strip()
        if dirty:
            sys.exit("main checkout has uncommitted tracked changes; commit or move them first:\n" + dirty)
        # T-272: the final step fast-forwards the CURRENT branch, and the close
        # loop then tests ancestry against `trunk`. If the repo is parked on
        # anything else those are two different commits, so every ticket is
        # skipped with no reason printed -- indistinguishable from the repo
        # guard rejecting them. Never silent: this is the merger's own repo and
        # they need to know which.
        head_branch = git("rev-parse", "--abbrev-ref", "HEAD", cwd=root)
        if head_branch and head_branch != trunk:
            sys.exit("%s is checked out on %r, not %s. `tickets merge` fast-forwards the "
                     "branch that is checked out, so merging from here would leave %s "
                     "untouched and close nothing, without saying why. "
                     "`git -C %s checkout %s` first." % (
                         root, head_branch, trunk, trunk, root, trunk))

        idir = os.path.join(root, cfg["integration_dir"])
        if os.path.isdir(idir):
            if sh("git", "status", "--porcelain", "--untracked-files=no", cwd=idir).stdout.strip():
                sys.exit("integration worktree %s has uncommitted changes; inspect it, then "
                         "`git worktree remove %s` and rerun" % (idir, idir))
            sh("git", "checkout", "-q", "-B", "integration", trunk, cwd=idir)
        else:
            sh("git", "branch", "-D", "integration")
            r = sh("git", "worktree", "add", "-q", idir, "-b", "integration", trunk)
            if r.returncode != 0:
                sys.exit("could not create integration worktree: " + (r.stderr or r.stdout).strip())
        print("integration: %s (from %s@%s in %s)" % (
            idir, trunk, git("rev-parse", "--short", trunk, cwd=root), merge_repo or root))
        stray = os.path.join(idir, ".tickets")
        if os.path.islink(stray):
            os.unlink(stray)

        merged_branches, merged_shas, actively_merged_shas, skipped, resolved_docs = [], [], [], [], []
        for b in branches:
            if sh("git", "rev-parse", "--verify", "-q", b).returncode != 0:
                skipped.append((b, "no such branch"))
                continue
            branch_tickets = [t for t in queue if t.get("branch") == b]
            pins = []
            for t in branch_tickets:
                pin = parse_review_sha(t.get("commit"))
                if not pin:
                    skipped.append((t["id"], "review ticket on %s has no recorded commit sha" % b))
                    continue
                if sh("git", "rev-parse", "--verify", "-q", pin).returncode != 0:
                    skipped.append((t["id"], "recorded sha %s not found" % pin))
                    continue
                pins.append(pin)
            if not pins:
                # Never fall back to moving tip — that was the T-064 hole.
                skipped.append((b, "no pinned review SHAs (refusing to merge live tip)"))
                continue
            if b in (a.ours or []) and not getattr(a, "discard_code", False):
                skipped.append((b, "refused -X ours without --discard-code"))
                print("  SKIPPED %s -- refused blanket conflict discard; pass --discard-code to override" % b)
                continue
            extra = ["-X", "ours"] if b in (a.ours or []) else []
            for pin in _order_shas_by_ancestry(sh, root, pins):
                if sh("git", "merge-base", "--is-ancestor", pin, "HEAD", cwd=idir).returncode == 0:
                    merged_shas.append(pin)
                    if b not in merged_branches:
                        merged_branches.append(b)
                    print("  already present %s (pinned %s)" % (b, pin[:12]))
                    continue
                short = sh("git", "rev-parse", "--short", pin).stdout.strip() or pin[:12]
                r = sh("git", "merge", "--no-edit", *extra, "-m",
                       "Integrate %s@%s into %s (master %s via tickets merge%s)" % (
                           b, short, trunk, owner,
                           "; conflicts resolved in favour of the integrated tree" if extra else ""),
                       pin, cwd=idir)
                if r.returncode == 0:
                    merged_shas.append(pin)
                    actively_merged_shas.append(pin)
                    if b not in merged_branches:
                        merged_branches.append(b)
                    print("  merged %s@%s%s" % (b, short, "  (-X ours)" if extra else ""))
                    continue
                conflicted = sh("git", "diff", "--name-only", "--diff-filter=U", cwd=idir).stdout.split()
                if not conflicted:
                    sh("git", "merge", "--abort", cwd=idir)
                    why = (r.stderr or r.stdout).strip().splitlines()
                    skipped.append((b, "merge refused for %s: %s" % (short, why[0] if why else "?")))
                    print("  SKIPPED %s@%s -- %s" % (b, short, "; ".join(why[:3])))
                    continue
                only_docs = all(any(f.endswith(x) for x in cfg["doc_exts"]) for f in conflicted)
                if only_docs:
                    keep = os.path.join(idir, "docs", "handoffs", "conflicts")
                    os.makedirs(keep, exist_ok=True)
                    for f in conflicted:
                        theirs = sh("git", "show", "%s:%s" % (pin, f), cwd=idir).stdout
                        alt = os.path.join(keep, "%s.%s" % (os.path.basename(f), b.replace("/", "_")))
                        with open(alt, "w") as fh:
                            fh.write(theirs)
                        sh("git", "checkout", "--ours", f, cwd=idir)
                        sh("git", "add", f, os.path.relpath(alt, idir), cwd=idir)
                        resolved_docs.append((f, os.path.relpath(alt, idir)))
                    sh("git", "commit", "-q", "--no-edit", cwd=idir)
                    merged_shas.append(pin)
                    actively_merged_shas.append(pin)
                    if b not in merged_branches:
                        merged_branches.append(b)
                    print("  merged %s@%s (doc-only conflicts kept both copies: %s)" % (
                        b, short, ", ".join(conflicted)))
                else:
                    sh("git", "merge", "--abort", cwd=idir)
                    skipped.append((b, "code conflicts at %s: %s" % (short, ", ".join(conflicted[:6]))))
                    print("  SKIPPED %s@%s -- code conflicts in %s; ask its owner to `tickets sync` and resolve"
                          % (b, short, ", ".join(conflicted[:6])))

        if not merged_shas and not merged_branches:
            print("nothing merged.")
            for b, why in skipped:
                print("  %s: %s" % (b, why))
            sys.exit(1)

        if sh("git", "ls-files", "--error-unmatch", ".tickets", cwd=idir).returncode == 0:
            sh("git", "rm", "-q", "-r", "--cached", ".tickets", cwd=idir)
            sh("git", "commit", "-q", "-m", "Untrack .tickets (the board is never versioned)", cwd=idir)
            print("  untracked .tickets that a branch had committed")

        test_cmd = cfg["test"].replace("{root}", root)
        if test_cmd.startswith(".venv/") and not os.path.exists(os.path.join(idir, ".venv")):
            test_cmd = os.path.join(root, test_cmd)
        print("tests: %s" % test_cmd)
        t = subprocess.run(test_cmd, shell=True, cwd=idir, capture_output=True, text=True)
        tail = (t.stdout or t.stderr).strip().splitlines()
        last = tail[-1] if tail else "(no output)"
        if t.returncode != 0 and not a.no_test:
            print("TESTS FAILED on the integrated tree; %s untouched; review tickets preserved. "
                  "Integration left at %s.\n%s" % (trunk, idir, "\n".join(tail[-8:])))
            _master_log(board, "merge of %s aborted: tests failed (%s); review state preserved" % (
                ", ".join(merged_branches), last), by=owner)
            sys.exit(2)
        print("  " + last)

        r = sh("git", "merge", "--ff-only", "integration")
        if r.returncode != 0:
            print("could not fast-forward %s. Usually an untracked file in the main checkout that a "
                  "branch now tracks; move it aside and rerun:\n%s" % (trunk, (r.stderr or r.stdout).strip()))
            sys.exit(3)
        sha = git("rev-parse", "--short", trunk, cwd=root)
        full_trunk = sh("git", "rev-parse", trunk).stdout.strip()
        if getattr(a, "push", False):
            pr = sh("git", "push", "origin", trunk)
            if pr.returncode != 0:
                why = (pr.stderr or pr.stdout).strip()
                print("PUSH FAILED: could not push %s to origin/%s\n%s" % (trunk, trunk, why))
                _master_log(board, "tickets merge: integrated %s@%s locally but PUSH FAILED (%s)" % (
                    trunk, sha, why.splitlines()[0] if why else "?"), by=owner)
                sys.exit(4)
            remote = sh("git", "ls-remote", "--heads", "origin", trunk).stdout.strip().split()
            remote_sha = remote[0] if remote else ""
            if remote_sha != full_trunk:
                print("PUSH FAILED: origin/%s is %s, expected %s" % (
                    trunk, remote_sha[:12] if remote_sha else "?", sha))
                _master_log(board, "tickets merge: push reported success but origin/%s != local %s" % (
                    trunk, sha), by=owner)
                sys.exit(4)
            print("%s -> %s   (pushed to origin/%s)" % (trunk, sha, trunk))
        else:
            print("%s -> %s   (push when ready: git push origin %s)" % (trunk, sha, trunk))

        # Resolve merged pin set to full SHAs for ancestry checks.
        pin_full = set()
        for pin in merged_shas:
            got = sh("git", "rev-parse", pin).stdout.strip()
            if got:
                pin_full.add(got)
        actively_merged_full = set()
        for pin in actively_merged_shas:
            got = sh("git", "rev-parse", pin).stdout.strip()
            if got:
                actively_merged_full.add(got)

        closed = []
        for t2 in queue:
            pin = parse_review_sha(t2.get("commit"))
            if not pin:
                skipped.append((t2["id"], "review ticket has no recorded commit sha"))
                continue
            # T-215: ancestry alone is not identity. This repo's history can
            # trivially "contain" a sha it never built on (a short-prefix
            # collision, or an unrelated commit that happens to be an
            # ancestor) -- refuse to close unless the pin names THIS repo.
            # A pin from before this field existed is not eligible for an
            # automatic close either: absence must never read as "matches
            # everything".
            recorded_repo = t2.get("repo")
            if not recorded_repo:
                skipped.append((t2["id"],
                    "no repo recorded on this review pin (reviewed before T-215) -- "
                    "not eligible for an automatic close; verify by hand which repo "
                    "its deliverable is in, then close it with `tickets done`"))
                continue
            if recorded_repo != merge_repo:
                skipped.append((t2["id"],
                    "recorded repo %s does not match this merge's repo %s -- "
                    "refusing a cross-repo close" % (recorded_repo, merge_repo)))
                continue
            got = sh("git", "rev-parse", pin)
            if got.returncode != 0:
                skipped.append((t2["id"], "recorded sha %s not found in this repo" % pin))
                continue
            full = got.stdout.strip()
            if sh("git", "merge-base", "--is-ancestor", full, full_trunk).returncode != 0:
                continue  # submitted SHA not on resulting main
            # Close only tickets whose recorded SHA was actually integrated (not merely same branch).
            if full not in pin_full:
                continue
            # T-324: sha membership in pin_full alone is not identity either --
            # a ticket's `commit` field can be corrupted to hold a sha that
            # some OTHER ticket's branch legitimately merged this pass (seen in
            # production: T-223 was recorded with a sha ("cos-opus@af27511")
            # that was really an unrelated sync commit, matched merge_repo,
            # resolved, was ancestor, and happened to be in pin_full anyway --
            # closing T-223 while its real fix stayed unmerged). Require that
            # the ticket's OWN recorded branch was actually part of this pass;
            # a coincidental sha match on some other branch is not evidence.
            if t2.get("branch") not in merged_branches:
                skipped.append((t2["id"],
                    "recorded sha %s matches a commit integrated this pass, but ticket's own "
                    "branch %r was not one of the branches merged -- refusing to close on a "
                    "sha coincidence alone" % (pin, t2.get("branch"))))
                continue
            # T-389: the idempotency path ("already present") folds trunk snapshots
            # into pin_full when some OTHER ticket on the same bare agent branch had
            # real work integrated this pass. Refuse to close a ticket whose pin was
            # only already-on-trunk unless it was the sole work merged on that branch.
            if full not in actively_merged_full:
                branch = t2.get("branch")
                sibling_active = False
                if branch:
                    for ot in queue:
                        if ot["id"] == t2["id"] or ot.get("branch") != branch:
                            continue
                        op = parse_review_sha(ot.get("commit"))
                        if not op:
                            continue
                        og = sh("git", "rev-parse", op)
                        if og.returncode != 0:
                            continue
                        if og.stdout.strip() in actively_merged_full:
                            sibling_active = True
                            break
                if sibling_active:
                    skipped.append((t2["id"],
                        "recorded sha %s is already on trunk but another ticket on branch %r "
                        "was the work integrated this pass -- refusing to close a pin that "
                        "contributed nothing this merge" % (pin, branch)))
                    continue
            t2["status"] = "done"
            t2["done_at"] = now()
            t2["notes"].append({"by": owner, "at": now(),
                                "text": "merged into %s as %s (tickets merge; pinned %s)" % (
                                    trunk, sha, pin)})
            save(board, t2)
            closed.append(t2["id"])
            _tm2 = timing(t2)
            _safe(lambda t2=t2, _tm2=_tm2: traj_event(
                board, "merge", agent=owner, ticket=t2,
                state_before="review", state_after="done", outcome="done",
                pin=pin, merged_as=sha, trunk=trunk,
                prev_owner=t2.get("owner", ""),
                active_hours=_round3(_tm2.get("active"))), None)
            post_message(board, owner, "%s merged into %s as %s" % (t2["id"], trunk, sha),
                         to=t2.get("owner", ""), re=t2["id"])
        if closed:
            print("closed: %s" % ", ".join(closed))
        for b, why in skipped:
            print("  skipped %s: %s" % (b, why))
        for f, alt in resolved_docs:
            print("  doc conflict %s: kept %s's copy, branch copy at %s" % (f, trunk, alt))
        _master_log(board, "tickets merge: pins %s -> %s@%s; tests %s; closed %s%s" % (
            ", ".join(p[:7] for p in merged_shas) or "-",
            trunk, sha, "green" if t.returncode == 0 else "skipped",
            ", ".join(closed) or "-",
            ("; skipped " + ", ".join(str(b) for b, _ in skipped)) if skipped else ""), by=owner)
        post_message(board, owner, "%s is now %s (merged %s). Everyone: run `tickets sync` in your worktree "
                     "before your next `tickets review`." % (trunk, sha, ", ".join(merged_branches)))
        _finish_followup(board, ",".join(closed) if closed else "merge", "done")


# ---- liveness truth (T-237, implementing the T-230 spec) ----------------
#
# `seen` on an agent record is written by checkin(), and the only caller that
# writes it on a schedule is the WATCHER, at loop boundaries: once at startup,
# and once more after the child session exits. It therefore measures "time
# since the watcher last came back from waiting on its child", not "time since
# the agent last did something". That single fact fails in BOTH directions at
# once, and they are the same mechanism seen from two sides:
#
#   - A long real session holds `seen` stale for its entire duration, so the
#     agent doing the most work reads as the deadest, and a master acting on
#     that number reopens tickets out from under a live session.
#   - A session that fails instantly (usage limit, dead login) returns in
#     seconds, so `checkin()` fires on nearly every poll tick and the agent
#     failing hardest reads as the healthiest.
#
# Polling faster cannot fix this: it makes the second direction strictly worse.
# So this module keeps THREE facts apart instead of folding them into one
# number, because they are different facts:
#
#   watcher alive  -- agents/<name>.watch.pid exists and that pid exists
#   run in flight  -- agents/<name>.run, heartbeated DURING the child run
#   agent working  -- the session's own transcript has a recent event
#
# Only the third is evidence that work is happening. A fresh in-run heartbeat
# over a session that cannot start is exactly the false signal this ticket
# exists to kill, so it is never rendered as "working" on its own.
#
# `unknown` is a real state with its own marker. It is never folded into
# `idle` or `working`: half the damage on this board came from a confident
# wrong answer, not from a missing one.

RUN_HEARTBEAT_SECS = int(os.environ.get("TICKETS_RUN_HEARTBEAT_SECS") or 30)
# How recent a transcript event has to be before we will call an agent working.
LIVENESS_FRESH_SECS = int(os.environ.get("TICKETS_LIVENESS_FRESH_SECS") or 300)
# A run that starts and exits inside this many seconds did not host real work.
FAST_FAIL_SECS = int(os.environ.get("TICKETS_FAST_FAIL_SECS") or 90)
FAST_FAIL_STREAK = int(os.environ.get("TICKETS_FAST_FAIL_STREAK") or 3)
# Newest codex rollout files to consider when matching one to a worktree.
CODEX_SCAN_FILES = int(os.environ.get("TICKETS_CODEX_SCAN_FILES") or 60)

# Strings the CLIs themselves print to stderr when a session cannot start.
# Matched ONLY against a single run's own slice of watch.log -- never against
# a raw scan of a transcript, which is how the old cmd_limits scan came to
# report every healthy codex session as limited (every turn writes a
# `rate_limits` telemetry block).
CLI_LIMIT_STRINGS = ("session limit", "usage limit", "hit your limit", "weekly limit",
                     "limit reached",
                     "actionrequirederror", "quota exceeded", "out of credits",
                     "429", "rate limit")
CLI_AUTH_STRINGS = ("authentication_error", "token has been revoked", "please run /login",
                    "invalid api key", "not logged in", "oauth token")

STATE_WORDS = ("working", "idle", "limited", "dead", "unknown")

# A source saying "I looked and there was nothing here" is not the same as one
# saying "I looked and could not make sense of what I found". The second is a
# real finding and must survive to the reader; folding both into one `unknown`
# is how a diagnosis gets thrown away on the way to the screen.
NOTHING_FOUND = ""


def fmt_age(secs):
    """Short age for a number of seconds. Distinct from fmt_hours() because a
    liveness answer is worth reading at second resolution: 'transcript active
    0s ago' is the whole point, and '0m' would throw it away."""
    if secs is None:
        return "?"
    if secs < 90:
        return "%ds" % int(secs)
    if secs < 3600:
        return "%dm" % int(round(secs / 60.0))
    if secs < 48 * 3600:
        return "%.1fh" % (secs / 3600.0)
    return "%.1fd" % (secs / 86400.0)


def _age_secs(stamp):
    """Seconds since an ISO-8601 stamp, tolerating both the tool's own
    '...Z' form and the fractional-second form the transcripts use."""
    if not stamp:
        return None
    s = str(stamp).strip()
    if s.endswith("Z"):
        s = s[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(s)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return max(0.0, datetime.now(timezone.utc).timestamp() - dt.timestamp())


def _tail_text(path, max_bytes=65536):
    """Last max_bytes of a file as text. Seeks from the end: these transcripts
    run to hundreds of KB and the answer is always in the last few lines."""
    try:
        with open(path, "rb") as f:
            f.seek(0, os.SEEK_END)
            f.seek(max(0, f.tell() - max_bytes))
            return f.read().decode("utf-8", "replace")
    except OSError:
        return ""


def _newest(paths):
    """(path, mtime) of the most recently modified path, ('', -1) if none."""
    best, best_mt = "", -1.0
    for p in paths:
        try:
            mt = os.stat(p).st_mtime
        except OSError:
            continue
        if mt > best_mt:
            best, best_mt = p, mt
    return best, best_mt


def _mtime_age(mtime):
    return None if mtime is None or mtime < 0 else max(0.0, datetime.now(timezone.utc).timestamp() - mtime)


def _looks_limited(text):
    low = (text or "").lower()
    return any(k in low for k in CLI_LIMIT_STRINGS)


def _structured_limit_signal(text):
    """True when the harness emitted a structured usage-limit error.

    Recognized envelopes (Anthropic / Claude Code, not invented strings):
      {"type":"error","error":{"type":"rate_limit_error",...}}
      {"type":"result","is_error":true,"error":{"type":"rate_limit_error",...}}
    Codex `token_count` `rate_limits` telemetry is NOT a limit (MASTER item 7 /
    T-230: every healthy turn writes that block).
    """
    for blob in _json_candidates(text):
        try:
            rec = json.loads(blob)
        except ValueError:
            continue
        if not isinstance(rec, dict):
            continue
        err = rec.get("error")
        if not isinstance(err, dict) or err.get("type") != "rate_limit_error":
            continue
        if rec.get("type") in ("error", "result"):
            return True
    return False


def _run_end_limit_outcome(rc, timed_out, log_text, bound_write=False):
    """run_end.outcome is 'limit' only on a limit-shaped failure, never prose.

    T-478: grepping CLI_LIMIT_STRINGS in the run log labelled productive
    exit=0 reviews as limit (r-opus-authz-1 talked about session resets).
    `limit` requires (nonzero exit OR timed_out OR a structured harness
    rate_limit_error) AND (that structured signal OR CLI limit text). An
    exit=0 bound-write run is never limit. Generic exit=1 with no limit
    evidence stays unlabelled so T-425 FLAG still treats it as a non-turn.
    `tickets limits` / liveness greps are unchanged.
    """
    if (rc in (0, None)) and not timed_out and bound_write:
        return None
    structured = _structured_limit_signal(log_text)
    failed = (rc not in (0, None)) or bool(timed_out)
    if not failed and not structured:
        return None
    if structured or _looks_limited(log_text):
        return "limit"
    return None


def _looks_auth(text):
    low = (text or "").lower()
    return any(k in low for k in CLI_AUTH_STRINGS)


# ---- in-run heartbeat ---------------------------------------------------

def _run_file(board, owner):
    return os.path.join(agents_dir(board), owner + ".run")


_RUN_BEAT_LOCK = threading.Lock()


class _RunFileLock:
    """Serialize one agent's run receipt across watcher and operator CLIs.

    The stable sidecar is required because the receipt itself is published
    with ``os.replace``. Locking the receipt would lock the old inode and let
    another process enter through the replacement inode.
    """

    def __init__(self, board, owner):
        self.path = _run_file(board, owner) + ".lock"
        self.fd = None

    def __enter__(self):
        try:
            import fcntl
        except ImportError:  # pragma: no cover - Windows keeps thread safety
            return self
        self.fd = os.open(self.path, os.O_CREAT | os.O_RDWR, 0o644)
        fcntl.flock(self.fd, fcntl.LOCK_EX)
        return self

    def __exit__(self, *exc):
        if self.fd is None:
            return
        try:
            import fcntl
            fcntl.flock(self.fd, fcntl.LOCK_UN)
        finally:
            os.close(self.fd)
            self.fd = None


def _run_beat(board, owner, **fields):
    """Write the in-run heartbeat.

    Deliberately its OWN file rather than a field on agents/<name>.json: the
    child session runs `tickets` commands that read-modify-write that record,
    and a heartbeat thread rewriting it every few seconds would race them and
    silently drop whatever the child had just written (inbox_seen, limit,
    ticket). One watcher per agent holds the pid lock, so this file has a
    single logical writer. Operator commands such as ``spawn --stop`` are a
    second process, so the complete receipt transaction also takes the stable
    ``.run.lock`` sidecar below.

    Updates are generation-fenced. `_run_begin(..., _new_run=True)` advances
    `generation` and may set active=True. A late heartbeat after stop/end
    (same or missing generation) cannot resurrect active=True once the
    receipt is closed — including when spawn --stop already found no live
    watcher.
    """
    try:
        os.makedirs(agents_dir(board), exist_ok=True)
        path = _run_file(board, owner)
        # The beat thread and the run-end write are both in this process and
        # can overlap: the lock keeps a read-modify-write whole, and the tmp
        # name is per-writer so two overlapping writers cannot truncate each
        # other's scratch file and leave a spliced record on disk.
        new_run = bool(fields.pop("_new_run", False))
        with _RUN_BEAT_LOCK:
            # A watcher heartbeat and ``spawn --stop`` run in different
            # processes. The generation check must share the same
            # inter-process critical section as the read and replace or a
            # heartbeat can publish a stale active=True snapshot after stop.
            with _RunFileLock(board, owner):
                rec = _read_run(board, owner)
                try:
                    current_gen = int(rec["generation"]) if rec.get("generation") is not None else 0
                except (TypeError, ValueError):
                    current_gen = 0
                incoming_gen = fields.get("generation")
                try:
                    incoming_gen = int(incoming_gen) if incoming_gen is not None else None
                except (TypeError, ValueError):
                    incoming_gen = None
                want_active = fields.get("active")
                was_active = rec.get("active")
                if new_run:
                    fields["generation"] = current_gen + 1
                    fields.setdefault("interrupted", False)
                    fields.setdefault("ended", "")
                    fields.setdefault("rc", None)
                elif incoming_gen is not None and incoming_gen < current_gen:
                    return
                elif want_active is True and was_active is False:
                    return
                elif want_active is False and was_active is not False:
                    fields.setdefault("generation", current_gen + 1)
                rec.update(fields)
                rec["beat"] = now()
                tmp = "%s.%d.%d.tmp" % (path, os.getpid(), threading.get_ident())
                with open(tmp, "w") as f:
                    json.dump(rec, f)
                os.replace(tmp, path)
    except OSError:
        pass


def _read_run(board, owner):
    try:
        with open(_run_file(board, owner)) as f:
            rec = json.load(f)
        return rec if isinstance(rec, dict) else {}
    except (IOError, ValueError):
        return {}


def _run_begin(board, owner, run_no, cwd, run_id="", ticket=""):
    fields = dict(pid=os.getpid(), run=run_no, cwd=cwd,
                  started=now(), active=True, rc=None, ended="",
                  _new_run=True)
    if run_id:
        fields["run_id"] = run_id
    if ticket:
        fields["ticket"] = ticket
    _run_beat(board, owner, **fields)


def _run_end(board, owner, run_no, rc):
    _run_beat(board, owner, run=run_no, active=False, rc=rc, ended=now())


def _mark_run_interrupted(board, owner):
    """Clear a recorded in-flight run after spawn --stop or a dead PID."""
    rec = _read_run(board, owner)
    if not rec:
        return
    _run_beat(board, owner, active=False, interrupted=True, ended=now())


def _finalize_active_watch_run(board, owner, rc=143):
    """SIGTERM/finally must not leave active=True without a run-end stamp."""
    rec = _read_run(board, owner)
    if not rec.get("active"):
        return False
    ended = now()
    _run_beat(board, owner, active=False, interrupted=True, rc=rc, ended=ended)
    _safe(lambda: traj_event(
        board, "run_end", agent=owner, ticket=rec.get("ticket") or None,
        run_no=rec.get("run"), run_id=rec.get("run_id") or None,
        exit=rc, interrupted=True, outcome="interrupted",
        started_at=rec.get("started") or None, ended_at=ended,
        worktree=rec.get("cwd") or None), None)
    return True


# ---- ground truth per tool ---------------------------------------------

def _claude_project_dir(cwd):
    """Claude Code stores a session transcript per working directory, under a
    name built by replacing '/', '.' and '_' in the absolute path with '-'."""
    import re as _re
    return os.path.join(os.path.expanduser("~"), ".claude", "projects", _re.sub(r"[/._]", "-", cwd or ""))


def _claude_transcript_state(cwd):
    """(state, age_secs, detail) from the Claude transcript for `cwd`.

    Reliable: every transcript line carries a `timestamp`, written as the turn
    happens, by the session itself -- not by the watcher wrapped around it.
    """
    if not cwd:
        return ("unknown", None, "no cwd recorded for this agent")
    d = _claude_project_dir(cwd)
    path, mt = _newest(glob.glob(os.path.join(d, "*.jsonl")))
    if not path:
        return ("unknown", None, "no Claude transcript under %s" % _tilde(d))
    for ln in reversed(_tail_text(path).splitlines()):
        try:
            ev = json.loads(ln)
        except ValueError:
            continue  # a tail read can slice the first line in half
        if not isinstance(ev, dict) or not ev.get("timestamp"):
            continue
        age = _age_secs(ev["timestamp"])
        if age is None:
            continue
        if age <= LIVENESS_FRESH_SECS:
            return ("working", age, "transcript active %s ago" % fmt_age(age))
        return ("idle", age, "transcript quiet %s" % fmt_age(age))
    age = _mtime_age(mt)
    if age is not None and age <= LIVENESS_FRESH_SECS:
        return ("working", age, "transcript written %s ago (no parseable timestamp)" % fmt_age(age))
    return ("unknown", age, "transcript tail carries no parseable timestamp")


def _codex_rollouts():
    """[(mtime, path)] newest first for the local codex rollout files, computed
    once per process -- `dash` asks for every agent's state on every refresh."""
    global _CODEX_ROLLOUTS
    try:
        return _CODEX_ROLLOUTS
    except NameError:
        pass
    home = os.path.expanduser("~")
    out = []
    for p in glob.glob(os.path.join(home, ".codex", "sessions", "**", "*.jsonl"), recursive=True):
        try:
            out.append((os.stat(p).st_mtime, p))
        except OSError:
            continue
    out.sort(reverse=True)
    _CODEX_ROLLOUTS = out
    return out


def _codex_transcript_state(cwd):
    """(state, age_secs, detail) from the newest codex rollout for `cwd`.

    Reads the last `task_complete` event's `payload.error`, which is the CLI's
    own verdict on the turn. Specifically NOT the raw substring scan the old
    cmd_limits used: every codex turn writes a `rate_limits` telemetry block,
    so that scan hits on healthy and dead sessions alike and carries zero
    information.
    """
    if not cwd:
        return ("unknown", None, "no cwd recorded for this agent")
    needles = ('"cwd":"%s"' % cwd, '"cwd": "%s"' % cwd)
    for mt, p in _codex_rollouts()[:CODEX_SCAN_FILES]:
        try:
            with open(p, "rb") as f:
                head = f.read(8192).decode("utf-8", "replace")
        except OSError:
            continue
        if not any(n in head for n in needles):
            continue
        return _codex_last_turn(p, mt)
    return ("unknown", None, NOTHING_FOUND)


def _codex_last_turn(path, mtime):
    file_age = _mtime_age(mtime)
    for ln in reversed(_tail_text(path, 262144).splitlines()):
        try:
            ev = json.loads(ln)
        except ValueError:
            continue
        if not isinstance(ev, dict):
            continue
        pay = ev.get("payload") if isinstance(ev.get("payload"), dict) else {}
        if pay.get("type") != "task_complete" and ev.get("type") != "task_complete":
            continue
        err = pay.get("error") if pay else ev.get("error")
        age = _age_secs(ev.get("timestamp"))
        if age is None:
            age = file_age
        if err:
            msg = (err.get("message") if isinstance(err, dict) else str(err)) or "turn failed"
            msg = " ".join(str(msg).split())
            if _looks_limited(msg):
                return ("limited", age, msg[:110])
            # A failed turn that is not a usage limit is a broken host, a dead
            # login, or something nobody here has classified. Saying "idle"
            # would be the confident wrong answer this ticket is about.
            return ("unknown", age, "last turn failed: %s" % msg[:90])
        if file_age is not None and file_age <= LIVENESS_FRESH_SECS:
            return ("working", file_age, "rollout active %s ago" % fmt_age(file_age))
        return ("idle", age if age is not None else file_age,
                "last turn ok %s ago" % fmt_age(age if age is not None else file_age))
    if file_age is not None and file_age <= LIVENESS_FRESH_SECS:
        return ("working", file_age, "rollout active %s ago (no task_complete yet)" % fmt_age(file_age))
    return ("unknown", file_age, "no task_complete in the rollout tail")


# ---- watcher / watch-log signals ---------------------------------------

def _pid_alive(pid):
    if not pid:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False
    return True


def _tickets_tool_path():
    return os.path.realpath(__file__)


def _argv_flag_value(argv, flag):
    """Return the value after `flag` in argv, or '' if absent."""
    for i, tok in enumerate(argv):
        if tok == flag and i + 1 < len(argv):
            return argv[i + 1]
        if tok.startswith(flag + "="):
            return tok[len(flag) + 1:]
    return ""


def _split_cmdline(cmd):
    """ps prints an unquoted command line; split it without dying on a stray
    quote inside an --exec value."""
    try:
        return shlex.split(cmd)
    except ValueError:
        return (cmd or "").split()


_RELEASE_TICKETS_RE = re.compile(
    r"/tickets-releases/[0-9a-f]{40}/tickets\.py(?:\s|$)"
)


def _is_python_interpreter(tok):
    """True when `tok` is a python interpreter argv token (ps shape)."""
    base = os.path.basename(tok or "")
    return base == "python" or base.startswith("python")


def _is_legitimate_watch_script(tok):
    """True when `tok` points at a real tickets.py, not a grep/search needle."""
    path = os.path.expanduser(tok or "")
    if _RELEASE_TICKETS_RE.search(path + " "):
        return True
    if path.endswith("/.claude/tools/tickets.py"):
        return True
    return os.path.isabs(path) and os.path.basename(path) == "tickets.py"


def _watch_cmd_agent(cmd):
    """The --agent name if `cmd` is a `tickets ... watch` loop, else ''.

    T-554: matching used to be `realpath(__file__) in cmd`, i.e. the release
    that happens to be INVOKING us. Every release recut then made the whole
    running fleet invisible at once -- loops still executing an older
    `tickets-releases/<sha>/tickets.py` (or the `~/.claude/tools/tickets.py`
    shim) matched nothing, so `spawn <seat> --stop` printed "no running
    watcher" and the very next `spawn <seat>` started a DUPLICATE loop beside
    the live one. A watch loop is identified by what it IS, not by which copy
    of the tool is asking: the first argv token named `tickets.py` whose next
    token is the `watch` subcommand. Scanning left to right means the real
    script token always wins over anything quoted inside `--exec`.

    T-562: a bare `tickets.py` token inside a grep/search argv (e.g.
    `grep -n tickets.py watch --agent optimizer`) must not count -- require
    a python interpreter immediately before the script token, or a path under
    tickets-releases/<sha>/, the ~/.claude/tools shim, or an absolute checkout.
    """
    argv = _split_cmdline(cmd)
    for i, tok in enumerate(argv):
        if os.path.basename(tok) != "tickets.py":
            continue
        rest = argv[i + 1:]
        if not rest or rest[0] != "watch":
            continue
        prev = argv[i - 1] if i > 0 else ""
        if _is_python_interpreter(prev) or _is_legitimate_watch_script(tok):
            return _argv_flag_value(rest, "--agent")
    return ""


def _is_python_interp(tok):
    base = os.path.basename((tok or "").rstrip("/")).lower()
    if base.endswith(".exe"):
        base = base[:-4]
    return base == "python" or base.startswith("python")


def _is_pytest_argv(argv):
    """True if this process IS pytest, not an agent whose prompt quotes it.

    T-559 / HB205: `ps aux` regex `[p]ytest.*-x.*--ignore=\\.worktrees` matches
    AGENT PROMPTS that quote the desk harness tokens (live: optimizer pid 5983,
    cursor-demo leftover 25662). Those processes are `tickets.py watch` /
    `claude` / `agent`, not pytest -- so a full-line grep never clears after
    the real harness exits. Identify pytest by the invocation PREFIX only:
    argv[0] basename `pytest`, `python -m pytest`, or `python /path/pytest`.
    A later `pytest` token inside `--exec` / `--prompt` does not count. A
    `tickets.py watch` loop is never pytest.
    """
    argv = list(argv or [])
    if not argv:
        return False
    for i, tok in enumerate(argv):
        base = os.path.basename(tok.rstrip("/"))
        if base == "tickets.py" and i + 1 < len(argv) and argv[i + 1] == "watch":
            return False
    head = os.path.basename(argv[0].rstrip("/"))
    if head in ("pytest", "pytest.exe"):
        return True
    if not _is_python_interp(argv[0]):
        return False
    i = 1
    while i < len(argv):
        tok = argv[i]
        if tok == "-m":
            return i + 1 < len(argv) and argv[i + 1] in ("pytest", "pytest.exe")
        if tok in ("-c", "--"):
            return False
        if tok.startswith("-"):
            if tok in ("-W", "-X") and i + 1 < len(argv) and not argv[i + 1].startswith("-"):
                i += 2
                continue
            i += 1
            continue
        return os.path.basename(tok.rstrip("/")) in ("pytest", "pytest.exe")
    return False


def _argv_has_ignore_worktrees(argv):
    for i, tok in enumerate(argv):
        if tok == "--ignore=.worktrees":
            return True
        if tok == "--ignore" and i + 1 < len(argv) and argv[i + 1] == ".worktrees":
            return True
        if tok.startswith("--ignore=") and tok.split("=", 1)[1] == ".worktrees":
            return True
    return False


def _is_desk_merge_pytest_cmd(cmd):
    """True if `cmd` is the desk merge pytest harness (T-559).

    ACCEPT: argv tokens pytest AND -x AND --ignore=.worktrees (any order,
    with gaps). Live counterexample 10:03Z pid 51475:
    `pytest -q -p no:cacheprovider -x --ignore=.worktrees --ignore=.claude`
    -- contiguous pgrep `pytest -x --ignore=.worktrees` is empty. Never
    cwd (51475 chdirs into /tmp/pytest-of-kavana mid-run; a cwd==desk-WT
    gate reads CLEAR while the suite is still alive). Never the full
    `ps aux` line. QUIET-BOX / T-486 (e) `desk_pytest_alive` /
    `desk_merge_alive` must call `_desk_pytest_alive`, not cwd and not
    `pgrep -f 'desk-cursor-fable/.venv/bin/python -m pytest'`.
    """
    argv = _split_cmdline(cmd)
    if not _is_pytest_argv(argv):
        return False
    return "-x" in argv and _argv_has_ignore_worktrees(argv)


def _desk_pytest_pids(extra_pids=()):
    """Pids of desk-merge-shaped pytest processes, plus any still-alive extra.

    Extra pids are the ACCEPT "or the pid" fallback (T-554 waiter also
    checked `ps -p 51475`). Never filters on cwd. Does not spawn --stop.
    """
    import subprocess

    extra = set()
    for p in extra_pids or ():
        try:
            extra.add(int(p))
        except (TypeError, ValueError):
            continue
    out = [p for p in extra if _pid_alive(p)]
    try:
        r = subprocess.run(["ps", "-ax", "-o", "pid=,command="],
                           capture_output=True, text=True)
    except OSError:
        return sorted(set(out))
    me = os.getpid()
    for line in (r.stdout or "").splitlines():
        line = line.strip()
        if not line:
            continue
        parts = line.split(None, 1)
        if len(parts) < 2:
            continue
        try:
            pid = int(parts[0])
        except ValueError:
            continue
        if pid == me:
            continue
        if pid in extra:
            continue
        if _is_desk_merge_pytest_cmd(parts[1]) and _pid_alive(pid):
            out.append(pid)
    return sorted(set(out))


def _desk_pytest_alive(extra_pids=()):
    """True if a desk-merge pytest is running (or an extra pid is still alive).

    Replacement for the T-486 (e) / QUIET-BOX `desk_pytest_alive` helper.
    False-ALIVE (another seat's merge-shaped pytest) is safer than false-CLEAR
    mid-desk-merge. Never spawn --stop a live seat from this predicate.
    """
    return bool(_desk_pytest_pids(extra_pids=extra_pids))


def _has_child_process(pid):
    """True if `pid` has at least one live child. Ground truth for "is this
    watcher mid-run" on loops that predate the T-237 agents/<name>.run
    heartbeat and therefore write no run file at all."""
    import subprocess

    if not pid:
        return False
    try:
        r = subprocess.run(["pgrep", "-P", str(int(pid))], capture_output=True, text=True)
    except (OSError, ValueError):
        return False
    return bool((r.stdout or "").strip())


def _watcher_run_active(board, owner, pid):
    """True if this watcher is inside a run (a child session is executing).

    The T-237 run file is authoritative when it belongs to this pid; its
    ABSENCE proves nothing, because a loop on a pre-T-237 release never writes
    one -- so fall back to the process table rather than reporting "idle".
    """
    rec = _read_run(board, owner) if board else {}
    if rec.get("pid") == pid and ("active" in rec):
        return bool(rec.get("active"))
    return _has_child_process(pid)


# One `ps` parse can be reused for every owner filter inside a board snapshot.
_WATCH_TABLE = threading.local()


def _parse_watch_table():
    """All live `tickets watch` rows from one process-table snapshot."""
    import subprocess

    out = []
    try:
        r = subprocess.run(["ps", "-ax", "-o", "pid=,command="], capture_output=True, text=True)
    except OSError:
        return out
    me = os.getpid()
    for line in (r.stdout or "").splitlines():
        line = line.strip()
        if not line:
            continue
        parts = line.split(None, 1)
        if len(parts) < 2:
            continue
        try:
            pid = int(parts[0])
        except ValueError:
            continue
        cmd = parts[1]
        if pid == me or "tickets.py" not in cmd:
            continue
        agent = _watch_cmd_agent(cmd)
        if not agent:
            continue
        argv = _split_cmdline(cmd)
        out.append({"pid": pid, "agent": agent, "cwd": _argv_flag_value(argv, "--cwd")})
    return out


class _shared_watch_table:
    """Bind one process-table snapshot for nested `_live_watch_pids` calls.

    Nested contexts reuse the outer snapshot instead of re-running `ps`.
    CEO/worker commands (`who`, `master`/`health`, `dash`, `spawn --list`)
    used to call `_live_watch_pids` once per seat; on a board with ~N agents
    that is O(N) process-table scans per pulse.
    """

    def __enter__(self):
        self._prev_bound = getattr(_WATCH_TABLE, "bound", False)
        self._prev_rows = getattr(_WATCH_TABLE, "rows", None)
        self._mine = not self._prev_bound
        if self._mine:
            _WATCH_TABLE.rows = _parse_watch_table()
            _WATCH_TABLE.bound = True
        return _WATCH_TABLE.rows

    def __exit__(self, *exc):
        if not self._mine:
            return
        _WATCH_TABLE.bound = self._prev_bound
        _WATCH_TABLE.rows = self._prev_rows


def _watch_table_rows():
    if getattr(_WATCH_TABLE, "bound", False):
        return _WATCH_TABLE.rows or []
    return _parse_watch_table()


def _live_watch_pids(owner=None, board=None):
    """Live `tickets watch --agent <name>` processes from the OS process table.

    The pid file only tracks one loop per board; duplicates (interrupted pytest
    runs, races before lock) show up here. `owner` filters to one agent name.
    When `board` is set, loops whose --cwd lies under that repo count, AND
    loops whose agents/<name>.watch.pid on THIS board matches the live pid
    even when --cwd is another repo (Steer board + Atman worktree). Foreign
    cwd without this board's pid file is still excluded (T-554).
    Omit `board` for fleet-wide stop of every loop for the name.
    Inside `_shared_watch_table`, every caller shares one `ps` snapshot.
    """
    repo = os.path.realpath(os.path.dirname(board)) if board else None
    bound = getattr(_WATCH_TABLE, "bound", False)
    out = []
    for row in _watch_table_rows():
        pid = row["pid"]
        agent = row["agent"]
        if owner is not None and agent != owner:
            continue
        if repo:
            watch_cwd = row.get("cwd") or ""
            under_repo = False
            if watch_cwd:
                try:
                    real = os.path.realpath(watch_cwd)
                    under_repo = real == repo or real.startswith(repo + os.sep)
                except OSError:
                    under_repo = False
            claimed = _watch_pid_claimed_on_board(board, agent, pid)
            if not under_repo and not claimed:
                continue
        if agent and (bound or _pid_alive(pid)):
            out.append(pid)
    return sorted(set(out))


def _watch_pid_claimed_on_board(board, agent, pid):
    """True when this board's agents/<agent>.watch.pid is that live pid.

    Steer-board + Atman-worktree is the normal persist shape: --cwd is not
    under the board repo, but the pid file still lives on this board.
    """
    if not board or not agent or not pid:
        return False
    try:
        with open(os.path.join(agents_dir(board), agent + ".watch.pid")) as f:
            return int((f.read() or "0").strip() or 0) == int(pid)
    except (IOError, OSError, ValueError):
        return False


def _watcher_pid(board, owner):
    """One live watcher pid: process table first, then the board pid file."""
    live = _live_watch_pids(owner, board=board)
    if live:
        return live[0]
    try:
        with open(os.path.join(agents_dir(board), owner + ".watch.pid")) as f:
            pid = int((f.read() or "0").strip() or 0)
    except (IOError, OSError, ValueError):
        return 0
    return pid if pid and _pid_alive(pid) else 0


def _watch_poke_file(board, owner):
    return os.path.join(agents_dir(board), owner + ".watch.poke")


def _process_command(pid):
    import subprocess

    try:
        r = subprocess.run(["ps", "-p", str(int(pid)), "-o", "command="],
                           capture_output=True, text=True)
    except (OSError, ValueError):
        return ""
    return (r.stdout or "").strip()


def _watch_pid_uses_this_cli(pid):
    """True when the live loop is executing THIS tickets.py.

    SIGUSR1 is fatal (default terminate) on older watchers that never
    installed a handler -- including the PATH sol-agy-harness shim. Only
    poke those processes with the signal.
    """
    cmd = _process_command(pid)
    if not cmd:
        return False
    tool = os.path.realpath(__file__)
    return tool in cmd and " watch" in cmd


PERSIST_POKE_ATTEMPTS = 3
_AUTONOMOUS_WAKE_LABELS = ("woken", "deduped", "watch-poked")


def _poke_persist_watch(board, owner, attempts=None):
    """Nudge a live persist watcher: poke file + SIGUSR1 when this CLI owns it.

    Returns True when a live watcher pid exists for the seat on this board.
    Does not claim T-706 'woken'. Default SIGUSR1 would kill a watcher that
    has no handler, so older shims get the poke file only. Bounded retries
    cover a pid that races teardown; they do not kill an in-flight child
    (T-820 wakeup pipe).
    """
    import signal

    tries = PERSIST_POKE_ATTEMPTS if attempts is None else max(1, int(attempts))
    for _ in range(tries):
        pid = _watcher_pid(board, owner)
        if not pid or not _pid_alive(pid):
            continue
        try:
            with open(_watch_poke_file(board, owner), "w") as f:
                f.write(now() + "\n")
                f.flush()
                os.fsync(f.fileno())
        except OSError:
            continue
        if _watch_pid_uses_this_cli(pid):
            try:
                os.kill(pid, signal.SIGUSR1)
            except (ProcessLookupError, OSError):
                continue
        return True
    return False


def _finish_followup(board, tid, event):
    """After review/done/merge: inbox-poke CoS+CEO and persist-wake their watchers.

    Harness-neutral (T-785 poke path). Not Claude-only: any seat with a live
    persist watcher on this CLI gets SIGUSR1; others still get the board msg.
    """
    m = current_master(board) or {}
    seats = []
    for name in ((m or {}).get("cos"), (m or {}).get("owner"), "cursor", "atman-ceo"):
        n = (name or "").strip()
        if n and n not in seats:
            seats.append(n)
    author = whoami()
    body = ("%s %s. Coordinator follow-up: inbox poke + persist wake "
            "(pr-sync after SHA is on origin/main)." % (tid, event))
    for seat in seats:
        post_message(board, author, body, to=seat, re=tid if str(tid).startswith("T-") else "",
                     kind="task", source=event)
        poked = _poke_persist_watch(board, seat)
        print("wake: %s -> %s" % (seat, "watch-poked" if poked else "inbox-posted"))
    return seats


def _watcher_count(owner, board=None):
    return len(_live_watch_pids(owner, board=board))


def _watch_runs(board, owner, keep=12):
    """Parse the tail of watch.log into [{n, start, exit_at, rc, text}] oldest
    first. `exit_at` is None for a run still executing."""
    import re as _re
    path = os.path.join(agents_dir(board), owner + ".watch.log")
    text = _tail_text(path, 131072)
    if not text:
        return []
    runs = {}
    order = []
    cur = None
    line_re = _re.compile(r"^(\S+) run (\d+) (trigger=|exit |TIMEOUT)(.*)$")
    for ln in text.splitlines():
        m = line_re.match(ln)
        if not m:
            if cur is not None:
                runs[cur]["text"].append(ln)
            continue
        stamp, n, kind, rest = m.group(1), int(m.group(2)), m.group(3), m.group(4)
        if kind == "trigger=":
            runs[n] = {"n": n, "start": stamp, "exit_at": None, "rc": None, "text": []}
            order.append(n)
            cur = n
        elif n in runs:
            if kind.startswith("exit"):
                runs[n]["exit_at"] = stamp
                runs[n]["rc"] = rest.strip()
            else:
                runs[n]["text"].append(ln)
    out = [runs[n] for n in order if n in runs]
    return out[-keep:]


def _watch_log_state(board, owner):
    """(state, detail) from the watcher's own log and pid.

    Corroborating evidence, always labeled heuristic when it is the only
    source: it can see that a run started, that it ended fast, and what the
    CLI printed while failing -- never that the agent thought about anything.
    """
    pid = _watcher_pid(board, owner)
    runs = _watch_runs(board, owner)
    if not runs:
        if pid and not _pid_alive(pid):
            return ("dead", "watcher pid %d is gone, no runs logged" % pid)
        return ("unknown", NOTHING_FOUND)
    last = runs[-1]
    if last["exit_at"] is None:
        age = _age_secs(last["start"])
        if pid and not _pid_alive(pid):
            return ("dead", "watcher pid %d died mid-run %d" % (pid, last["n"]))
        return ("working", "run %d in flight %s (watcher alive)" % (last["n"], fmt_age(age)))
    # Closed runs: walk back over CONSECUTIVE FAILING runs. The evidence that
    # makes this `limited` is the CLI's own error text inside those runs' own
    # log slices -- watch.log holds the CLI's human-readable stderr, never a
    # transcript's per-turn telemetry, which is what made the old raw scan
    # useless. Run duration is corroboration reported in the detail, NOT a
    # gate: a hard-limited agent can fail many times in a row, and gating on
    # speed dropped it the moment one of those failures happened to take
    # longer than the usual few seconds.
    streak = []
    for r in reversed(runs):
        if r["exit_at"] is None or r["rc"] in ("0", None):
            break
        streak.append(r)
    if len(streak) >= FAST_FAIL_STREAK:
        blob = "\n".join("\n".join(r["text"]) for r in streak)
        why = _first_match(blob, CLI_LIMIT_STRINGS + CLI_AUTH_STRINGS)
        kind = "auth" if _looks_auth(blob) and not _looks_limited(blob) else "limit"
        fast = len([r for r in streak
                    if (_span_secs(r["start"], r["exit_at"]) or FAST_FAIL_SECS + 1) <= FAST_FAIL_SECS])
        detail = "%d runs failed in a row, %d of them inside %ds%s" % (
            len(streak), fast, FAST_FAIL_SECS, (" -- %s" % why) if why else "")
        if why:
            return ("limited", ("dead login: " if kind == "auth" else "") + detail)
        # Repeated failure with nothing quotable is not enough to call an agent
        # limited, and it is certainly not enough to call it healthy.
        return ("unknown", detail + " -- no CLI error text in the log, read it by hand")
    if pid and not _pid_alive(pid):
        return ("dead", "watcher pid %d is gone" % pid)
    age = _age_secs(last["exit_at"])
    return ("idle", "last run %d exited %s ago" % (last["n"], fmt_age(age)))


def _span_secs(a, b):
    aa, bb = _age_secs(a), _age_secs(b)
    return None if aa is None or bb is None else max(0.0, aa - bb)


def _first_match(text, needles):
    """The first log LINE containing one of `needles`, trimmed. Line-scoped
    rather than a byte window around the match, so the quote does not drag in
    the tail of whatever unrelated line came before it."""
    for ln in (text or "").splitlines():
        low = ln.lower()
        if any(n in low for n in needles):
            return " ".join(ln.split())[:110]
    return ""


def _tilde(p):
    home = os.path.expanduser("~")
    return "~" + p[len(home):] if p and p.startswith(home) else (p or "")


# ---- the one answer who / dash / limits all read -----------------------

def _cwd_sharers(board, owner, cwd, peers=None):
    """Other agent records claiming the same working directory.

    A Claude transcript is keyed by directory and carries no agent name, so if
    two agents' records point at one directory the transcript cannot say whose
    activity it is. That is not hypothetical: several records can point at
    one worktree, and reading the transcript naively reports all of them as
    working when at most one is.
    """
    if not cwd:
        return []
    recs = peers if peers is not None else load_agents(board)
    return sorted(r["owner"] for r in recs
                  if r.get("owner") and r["owner"] != owner
                  and (r.get("cwd") or r.get("worktree") or "") == cwd)


def _dedup(paths):
    """Non-empty, order-preserving, no repeats."""
    out = []
    for p in paths:
        if p and p not in out:
            out.append(p)
    return out


def _transcript_over_cwds(cwds):
    """First cwd with a real Claude transcript wins; otherwise report against
    the first candidate so the 'nothing found' message names the directory a
    reader should actually go and look in.

    Returns (state, age, detail, cwd_used).
    """
    first = None
    for c in cwds:
        st, age, detail = _safe(lambda: _claude_transcript_state(c), ("unknown", None, "transcript unreadable"))
        if first is None:
            first = (st, age, detail, c)
        if st != "unknown":
            return st, age, detail, c
    return first or ("unknown", None, "transcript unreadable", "")


def agent_liveness(board, rec, peers=None):
    """One per-agent state, derived from ground truth rather than from the
    watcher's own `seen` field.

    Returns {state, detail, source, heuristic, watcher, run, seen_age}.
    `state` is one of STATE_WORDS. `heuristic` marks an answer that rests on
    run cadence or on an unattributable transcript rather than on this agent's
    own confirmed activity, so the renderer can show it as weaker evidence
    instead of dressing it up as a fact.
    """
    owner = (rec or {}).get("owner") or ""
    pid = _watcher_pid(board, owner)
    run = _read_run(board, owner)
    # Where does this agent's session actually live? The agent RECORD's cwd is
    # whatever directory the last `tickets` command was typed in, and for this
    # epic that is routinely a second repo -- `tickets review` may run in the
    # deliverable clone while the session lives in another worktree. The
    # WATCHER's cwd, recorded in the run file, is the session's
    # own directory and does not move when a command is run elsewhere, so it
    # is tried first. Both are kept: an agent running by hand has no run file.
    cwds = _dedup([run.get("cwd") or "",
                   (rec or {}).get("cwd") or "",
                   (rec or {}).get("worktree") or ""])
    cwd = cwds[0] if cwds else ""
    beat_age = _age_secs(run.get("beat"))
    watchers = _watcher_count(owner, board)
    out = {"state": "unknown", "detail": "", "source": "none", "heuristic": True,
           "watcher": watchers > 0,
           "watcher_count": watchers,
           "run": run if run.get("active") else {},
           "seen_age": _age_secs((rec or {}).get("seen"))}

    # 1. A human asserting a state always wins: `tickets limit` is a person
    #    saying "I read the log". Keep it, but it is no longer the ONLY path
    #    to a limited render -- that is how an agent can look healthy while
    #    hard-limited for a long window.
    lim = (rec or {}).get("limit")
    if lim:
        out.update(state="limited", source="manual", heuristic=False,
                   detail="asserted by hand%s%s" % (
                       (", back %s" % lim["until"]) if lim.get("until") else "",
                       (" -- %s" % lim["note"]) if lim.get("note") else ""))
        return out

    wstate, wdetail = _safe(lambda: _watch_log_state(board, owner), ("unknown", "watch log unreadable"))

    # 2. A fast-fail streak outranks any transcript freshness, because a
    #    session that fails instantly manufactures fresh-looking artefacts by
    #    construction -- that IS the bug. Checked before the transcript, not
    #    after it.
    if wstate == "limited":
        out.update(state="limited", source="watchlog", heuristic=True, detail=wdetail)
        return out

    tstate, tage, tdetail, cwd = _transcript_over_cwds(cwds)
    tsource = "claude"
    if tstate == "unknown":
        cstate, cage, cdetail = _safe(lambda: _codex_transcript_state(cwd), ("unknown", None, "rollout unreadable"))
        if cdetail != NOTHING_FOUND:
            # A codex rollout that exists but ended on an error nobody has
            # classified ("spawn codex-code-mode-host ENOENT") is a FINDING.
            # Taking it only when it resolved to a confident state would drop
            # exactly the cases a master needs to go and look at.
            tstate, tage, tdetail, tsource = cstate, cage, cdetail, "codex"
        elif "no Claude transcript" in tdetail:
            # Neither tool left a transcript for this cwd: say so, and say
            # which two we looked for, rather than picking one's error message.
            tdetail = "no Claude or Codex transcript for %s" % _tilde(cwd)

    if tstate != "unknown":
        out.update(state=tstate, source=tsource, heuristic=False, detail=tdetail)
        # A dead watcher under a quiet transcript is a real dead lane; a dead
        # watcher under a live transcript is just an agent running by hand.
        if tstate == "idle" and pid and not _pid_alive(pid):
            out.update(state="dead", detail="%s; watcher pid %d is gone" % (tdetail, pid))
        shared = _safe(lambda: _cwd_sharers(board, owner, cwd, peers), [])
        if shared:
            # Keep the state -- the activity is real -- but stop presenting it
            # as a fact about THIS agent, and name who else it could be so the
            # reader can go and settle it.
            out.update(heuristic=True,
                       detail="%s; cwd shared with %s -- transcript cannot say which"
                              % (out["detail"], ", ".join(shared)))
        return out

    if wstate == "dead":
        out.update(state="dead", source="watchlog", heuristic=True, detail=wdetail)
        return out

    # 3. A run in flight with a fresh heartbeat proves the WATCHER is alive and
    #    a child is up. It does not prove the agent is doing anything, and it
    #    must not be rendered as if it did -- so it stays `unknown` and says
    #    exactly what it knows.
    if run.get("active") and beat_age is not None and beat_age <= max(RUN_HEARTBEAT_SECS * 3, 90):
        out.update(state="unknown", source="heartbeat", heuristic=True,
                   detail="run %s in flight, heartbeat %s ago -- watcher alive, no transcript to confirm work"
                          % (run.get("run", "?"), fmt_age(beat_age)))
        return out

    if wstate == "working":
        out.update(state="unknown", source="watchlog", heuristic=True,
                   detail="%s -- no transcript ground truth for this tool" % wdetail)
        return out

    # Nothing was conclusive. Report everything that was actually observed,
    # both sources, rather than letting one source's "I found nothing" hide
    # the other's "I found something I cannot explain".
    parts = [d for d in (wdetail, tdetail) if d and d != NOTHING_FOUND]
    out.update(state="unknown", source="watchlog" if wdetail else "none", heuristic=True,
               detail="; ".join(parts) or "no ground-truth source for this agent")
    return out


def _clip(text, n):
    """Trim to a word boundary. Cutting mid-token turned '11 of them inside
    90s' into '...inside 9', which changes the number rather than shortening
    the sentence."""
    t = " ".join((text or "").split())
    if len(t) <= n:
        return t
    cut = t[:n]
    sp = cut.rfind(" ")
    return (cut[:sp] if sp > n // 2 else cut).rstrip(" ,;") + "..."


def liveness_mark(live):
    """One character telling a reader HOW the state was reached, so 'a human
    told us' never reads the same as 'the tool worked it out'."""
    if live.get("state") == "unknown":
        return "?"
    if live.get("source") == "manual":
        return "!"
    return "~" if live.get("heuristic") else " "


def liveness_line(live):
    m = liveness_mark(live)
    return "%-8s%s %s" % (live.get("state", "unknown"), m, live.get("detail", ""))


# ---- usage limits -------------------------------------------------------

LIMIT_PATTERNS = ("usage limit", "rate limit", "rate_limit", "hit your limit", "limit reached",
                  "out of credits", "quota exceeded", "429", "resets at", "try again in")
# A dead session is not always a rate limit: revoked tokens and expired logins
# look identical from the board (silence), so the scan reports them too.
AUTH_PATTERNS = ("authentication_error", "token has been revoked", "please run /login",
                 "invalid api key", "401", "not logged in", "oauth")


def _classify(line):
    low = line.lower()
    if any(k in low for k in AUTH_PATTERNS) and ("auth" in low or "revoked" in low or "login" in low or "401" in low):
        return "auth"
    if any(k in low for k in LIMIT_PATTERNS) and ("limit" in low or "429" in low or "credit" in low or "quota" in low):
        return "limit"
    return None


def _agent_from_path(path):
    """Best-effort: worktree names encode the agent (…-worktrees-claude-opus/…)."""
    import re
    m = re.search(r"worktrees?-([a-z0-9-]+?)(?:-work)?(?:/|$)", path)
    return m.group(1) if m else ""


def _scan_logs(paths, hours):
    """[(path, hits, last_mtime, kind, agent, last_line)] for recent files mentioning limits/auth."""
    import time
    cutoff = time.time() - hours * 3600
    out = []
    for p in paths:
        try:
            st = os.stat(p)
        except OSError:
            continue
        if st.st_mtime < cutoff or st.st_size > 200 * 1024 * 1024:
            continue
        hits = 0
        kind = None
        last = ""
        try:
            with open(p, "r", errors="ignore") as f:
                for ln in f:
                    k = _classify(ln)
                    if k:
                        hits += 1
                        kind = k if kind != "auth" else kind
                        i = ln.lower().find("error")
                        last = ln[max(0, i - 40):i + 160].strip() if i >= 0 else ln.strip()[:200]
        except OSError:
            continue
        if hits:
            out.append((p, hits, st.st_mtime, kind or "limit", _agent_from_path(p), last))
    return out


def cmd_limit(a, board):
    """Record (or clear) that an agent hit a usage limit; shown in who/master."""
    owner = a.agent or whoami()
    if not _agent_rec(board, owner):  # bootstrap outside the lock: checkin takes it too
        checkin(board, owner)
    if a.clear:
        mutate = lambda rec: rec.pop("limit", None)
        msg = "%s is back (limit cleared)" % owner
    else:
        limit = {"at": now(), "until": a.until or "", "note": a.note or ""}
        mutate = lambda rec: rec.update({"limit": limit})
        msg = "%s hit a usage limit%s%s" % (owner, (" until %s" % a.until) if a.until else "",
                                            (": %s" % a.note) if a.note else "")
    # Read-modify-write under the lock: a watch-loop heartbeat lands on this
    # same record every 60s, and unsynchronised it would drop the limit while
    # this command still printed success -- the exact bug this guards.
    _agent_update(board, owner, mutate)
    post_message(board, owner, msg)
    print(msg)
    held = [t for t in load_all(board) if t["status"] == "claimed" and t.get("owner") == owner]
    if held and not a.clear:
        print("still holding: %s -- master may `tickets reopen` them for someone else" % ", ".join(t["id"] for t in held))


def cmd_limits(a, board):
    """Who is limited: manual records + silence + a scan of local tool logs."""
    print("Recorded limits:")
    any_ = False
    for r in load_agents(board):
        lim = r.get("limit")
        if lim:
            any_ = True
            print("  %-14s hit %s ago%s%s" % (r["owner"], fmt_hours(hours_since(lim["at"])),
                                             (", back %s" % lim["until"]) if lim.get("until") else "",
                                             (" -- %s" % lim["note"]) if lim.get("note") else ""))
    if not any_:
        print("  none (agents record one with `tickets limit --until \"...\"`)")
    print("")
    print("Probably limited (holding a ticket, silent > %d min):" % (UPDATE_EVERY_MIN * 2))
    quiet = False
    for t in load_all(board):
        if t["status"] == "claimed":
            su = timing(t)["since_update"]
            if su is not None and su * 60 > UPDATE_EVERY_MIN * 2:
                quiet = True
                print("  %-14s %s silent %s" % (t.get("owner", "?"), t["id"], fmt_hours(su)))
    if not quiet:
        print("  nobody")
    print("")
    home = os.path.expanduser("~")
    # The old section here scanned every recent tool log for the substring
    # "rate_limit" and printed the hit count as if it were evidence. Every
    # codex turn writes a `rate_limits` telemetry block, so a perfectly
    # healthy session and a hard-limited one produced identical output --
    # MASTER.md already told masters not to trust it. It is replaced by the
    # per-agent state derived in agent_liveness(), and kept only behind
    # --raw-scan, labelled for what it is.
    print("Live state (from each session's own transcript, not from the watcher's `seen`):")
    agents = load_agents(board)
    if not agents:
        print("  nobody has checked in yet")
    with _shared_watch_table():
        for r in sorted(agents, key=lambda r: r.get("owner", "")):
            lv = _safe(lambda r=r: agent_liveness(board, r, agents),
                       {"state": "unknown", "detail": "liveness read failed",
                        "source": "none", "heuristic": True})
            print("  %-14s %-8s%s %-9s %s" % (r["owner"][:14], lv["state"], liveness_mark(lv),
                                              "via " + (lv.get("source") or "none"), (lv.get("detail") or "")[:70]))
    print("  legend: ! asserted by a human   ~ heuristic (run cadence only)   ? unknown -- read the log by hand")
    if getattr(a, "raw_scan", False):
        sources = {
            "claude": glob.glob(os.path.join(home, ".claude", "projects", "*", "*.jsonl")),
            "codex": glob.glob(os.path.join(home, ".codex", "sessions", "**", "*.jsonl"), recursive=True)
                     + glob.glob(os.path.join(home, ".codex", "log", "*")),
            "cursor": glob.glob(os.path.join(home, ".cursor", "chats", "**", "*.json*"), recursive=True)
                      + glob.glob(os.path.join(home, ".cursor", "*.log")),
        }
        print("")
        print("RAW SUBSTRING SCAN, last %dh -- NOT EVIDENCE OF ANYTHING." % a.hours)
        print("  Codex writes a `rate_limits` telemetry block on every turn, so a healthy session "
              "scores hits here exactly like a dead one. Shown only because it is occasionally useful "
              "for finding WHICH file to open by hand.")
        found = False
        for tool, paths in sources.items():
            hits = _scan_logs(paths, a.hours)
            hits.sort(key=lambda x: -x[2])
            for p, n, mt, kind, agent, last in hits[:6]:
                found = True
                short = p.replace(home, "~")
                print("  %-7s %-5s %3d hits  %s ago  agent=%s  %s" % (
                    tool, kind.upper(), n, fmt_hours((datetime.now(timezone.utc).timestamp() - mt) / 3600.0),
                    agent or "?", short[-70:]))
                if last and getattr(a, "verbose", False):
                    print("          last: %s" % last[:180])
        if not found:
            print("  no files matched")
    print("")
    print("AUTH means the session's login died (revoked/expired token): the fix is `/login` in that "
          "session, not waiting. LIMIT means a usage/rate cap: wait for the reset or switch accounts. "
          "Either way, if the agent holds a ticket, `tickets reopen` it so someone else can continue. "
          "UNKNOWN means this tool cannot tell you -- go read the log; it is not a synonym for fine, "
          "and nothing should be reopened on it alone.")


def cmd_status(a, board):
    """Generic status setter with the words agents actually use."""
    word = a.status.lower()
    target = STATUS_WORDS.get(word)
    if not target:
        sys.exit("unknown status %r; use one of: %s" % (a.status, ", ".join(sorted(STATUS_WORDS))))
    t = load(board, a.id)
    if target == t["status"]:
        print("%s already %s" % (a.id, LABEL[target]))
        return
    if target == "claimed":
        owner = whoami(a.owner)
        if t["status"] == "open":
            got = try_claim(board, a.id, owner)
            if not got:
                sys.exit("%s is already taken" % a.id)
            checkin(board, owner, a.id)
        else:
            t["status"] = "claimed"
            t["owner"] = t.get("owner") or owner
            t.setdefault("claimed_at", now())
            save(board, t)
        print("%s -> IN PROGRESS (@%s)" % (a.id, t.get("owner") or owner))
        return
    if target == "open":
        a.__dict__.setdefault("id", a.id)
        cmd_reopen(a, board)
        return
    if target == "blocked":
        if not a.notes:
            sys.exit("blocked needs --notes \"why\"")
        a.reason = a.notes
        cmd_block(a, board)
        return
    if target == "review":
        a.pr = getattr(a, "pr", "")
        a.force = getattr(a, "force", False)
        cmd_review(a, board)
        return
    if target == "done":
        a.no_notes = False
        a.force = getattr(a, "force", False)
        cmd_done(a, board)
        return


def cmd_repin(a, board):
    """Correct a review pin so it names the repo the artifact is really in (T-272).

    Backfill. Every pin recorded before `review` learned --artifact was taken
    from the caller's cwd, which on this board is systematically the wrong
    clone. Re-running `tickets review` would also fix it, but it resets
    review_at and re-notifies the master once per ticket, which for a queue
    this deep is worse than the problem -- so correcting evidence gets its own
    verb, and leaves an explicit REPIN note rather than silently rewriting
    history.

    The new pin is the artifact tree's CURRENT HEAD, derived by running git
    there. Check that tree out at the commit that was actually reviewed first.
    """
    t = load(board, a.id)
    if t["status"] != "review":
        sys.exit("%s is %s; repin corrects the pin on work that is IN REVIEW "
                 "(a closed ticket's record is history -- do not rewrite it)" % (
                     a.id, LABEL[t["status"]]))
    art = artifact_tree(a)
    g = git_state(cwd=art)
    if not g:
        sys.exit("--artifact %s is not a git working tree" % art)
    if g["dirty"] and not a.force:
        sys.exit("RULE: %d uncommitted files in %s -- the pin must name a committed "
                 "state; commit or check out the reviewed sha first (or --force)" % (
                     g["dirty"], g["top"]))
    was_commit, was_repo = t.get("commit"), t.get("repo")
    stamp = _record_pin(t, g, art)
    author = whoami(a.owner)
    t["notes"].append({"by": author, "at": now(), "text": "REPIN: %s in %s (was %s in %s)%s" % (
        stamp, t.get("repo") or "?", was_commit or "-", was_repo or "-",
        (" -- " + a.notes) if a.notes else "")})
    save(board, t)
    checkin(board, author, None, "repinned %s" % a.id)
    print("%s repinned" % a.id)
    print("  was %s in %s" % (was_commit or "-", was_repo or "-"))
    print("  now %s in %s  (from %s)" % (stamp, t.get("repo") or "?", art))
    print("  verify this is the sha you submitted; repin records that tree's HEAD.")


def cmd_done(a, board):
    t = load(board, a.id)
    if not a.notes and not a.no_notes:
        sys.exit(
            'done needs --notes "paths, names, decisions the next agent must match" '
            "(or --no-notes if there is truly nothing to hand off)"
        )
    # T-272: `done` must be aimable at the artifact tree too. Without this,
    # re-aiming `review` would leave every correctly-pinned cross-repo ticket
    # permanently unclosable -- T-254's guard below hard-exits on a repo
    # mismatch and explicitly refuses --force as an override.
    art = artifact_tree(a)
    g = git_state(cwd=art)
    # A branch/SHA match is not proof of repository identity (T-254).
    # Check before mutating the ticket; --force only bypasses worktree rules.
    recorded_repo = t.get("repo")
    current_repo = g.get("repo") if g else None
    if (g or recorded_repo) and not current_repo:
        sys.exit("RULE: %s cannot be closed without a verifiable repository; "
                 "run done from the deliverable's checkout, or pass "
                 "--artifact <dir> pointing at it." % a.id)
    if recorded_repo and recorded_repo != current_repo:
        sys.exit("RULE: %s recorded repository %r does not match %r; close it from the "
                 "recorded repository, or pass --artifact <dir> pointing at that "
                 "checkout. --force cannot override repository evidence." % (
                     a.id, recorded_repo, current_repo))
    if t["status"] == "review":
        # the master closes reviewed work from main after merging; the agent's
        # branch@sha is already on the ticket, so the branch/clean rules do not apply
        a.force = True
    if g and g["branch"] in ("main", "master") and not a.force:
        sys.exit(
            "RULE: %s is being closed from branch %r. Work belongs on the agent's own "
            "worktree branch; commit there and merge, then run done from that tree "
            "(or pass --force if this really was merged trunk work)." % (a.id, g["branch"])
        )
    if g and g["dirty"] and not a.force:
        sys.exit(
            "RULE: %d uncommitted files in %s. Commit before marking %s done "
            "(or --force to override)." % (g["dirty"], g["top"], a.id)
        )
    t["status"] = "done"
    t["done_at"] = now()
    if not t.get("claimed_at"):
        t["claimed_at"] = t.get("updated") or t["created"]
    text = a.notes
    if g:
        stamp = "%s@%s" % (g["branch"], g["sha"])
        if stamp not in text:
            text = ("%s -- %s" % (stamp, text)) if text else stamp
        if t.get("commit") and not recorded_repo:
            print("WARNING: %s previous pin has no recorded repository; its provenance "
                  "cannot be verified. Recording only the current completion repository."
                  % a.id, file=sys.stderr)
        _record_pin(t, g, art)
    if text:
        # by=whoami(), not t["owner"]: the note records who wrote it, which is
        # not always who the ticket is filed under (T-238 -- see cmd_note).
        t["notes"].append({"by": whoami(), "at": now(), "text": text})
    save(board, t)
    tm = timing(t)
    _safe(lambda: traj_event(board, "done", agent=whoami(), ticket=t,
                             state_before="review" if t.get("review_at") else "claimed",
                             state_after="done", outcome="done",
                             notes_len=len(a.notes or ""),
                             active_hours=_round3(tm.get("active")),
                             wait_hours=_round3(tm.get("wait")),
                             pin=t.get("commit", ""),
                             **_traj_git(cwd=art)), None)
    if t.get("owner"):
        # T-428: checkin() always writes THIS process's cwd/branch/sha. That is
        # the closer's location. Stamping it onto a different owner makes
        # `tickets who` lie (the owner appears to sit in the closer's tree).
        closer = whoami()
        note = "finished %s by %s" % (a.id, closer)
        if t["owner"] == closer:
            checkin(board, t["owner"], "", note)
        else:
            _agent_set(board, t["owner"], ticket="", note=note)
            checkin(board, closer, None, note)
    print("%s done in %s (waited %s before claim)" % (
        a.id, fmt_hours(tm["active"]), fmt_hours(tm["wait"])))
    if g:
        print("recorded %s" % t["commit"])
    freed, started, held = _start_successors(board, a.id)
    if freed:
        print("unblocked: %s" % ", ".join(freed))
    if started:
        print("started: %s" % ", ".join(started))
    if held:
        print("held (not started): %s" % ", ".join(held))
    _finish_followup(board, a.id, "done")


def cmd_block(a, board):
    t = load(board, a.id)
    before = t["status"]
    t["status"] = "blocked"
    t["notes"].append({"by": whoami(), "at": now(), "text": a.reason})
    save(board, t)
    _safe(lambda: traj_event(board, "block", agent=whoami(), ticket=t,
                             state_before=before, state_after="blocked",
                             outcome="blocked", notes_len=len(a.reason or ""),
                             **_traj_git()), None)
    print("%s blocked: %s" % (a.id, a.reason))


def cmd_note(a, board):
    """Add a note (`tickets note` / `tickets update`).

    `by` is always the caller's own identity (`--by`, else $TICKET_AGENT),
    never the ticket's `owner` field. T-238: a fallback to `t.get("owner")`
    here meant a second agent working the same ticket in parallel -- exactly
    the case a duplicate lane needs to be visible -- had its notes silently
    relabeled as the owner's, so nothing in the note history could ever
    reveal the second lane. This was a write-path bug: the on-disk `by` was
    wrong, not just its rendering in `tickets show`.
    """
    t = load(board, a.id)
    who = whoami(a.by)
    t["notes"].append({"by": who, "at": now(), "text": a.text})
    save(board, t)
    # notes_len only -- the note body is the agent's own prose and never enters
    # the trajectory log.
    _safe(lambda: traj_event(board, "update", agent=who, ticket=t,
                             notes_len=len(a.text or ""), state_after=t["status"],
                             **_traj_git()), None)
    if t["status"] == "claimed":
        checkin(board, who, t["id"], a.text[:80])
    owner = t.get("owner")
    if owner and owner != who:
        # Correct attribution only helps a reader who goes looking; a
        # stranger's note on a claimed ticket needs to actively surface to
        # the owner, since silent duplicate work is the actual harm.
        post_message(board, who, "posted on %s (owned by %s): %s" % (
            t["id"], owner, a.text[:120]), to=owner, re=t["id"])
        print("warning: %s is owned by %s, not %s -- %s notified" % (
            t["id"], owner, who, owner))
    tm = timing(t)
    if t["status"] == "claimed" and tm["active"] is not None:
        print("update on %s recorded (%s into the task)" % (a.id, fmt_hours(tm["active"])))
    else:
        print("noted on %s" % a.id)


def _ensure(board, kind, ref):
    if not ref:
        return
    if kind == "epic":
        get_epic(board, ref)
    else:
        get_sprint(board, ref)


def cmd_assign(a, board):
    """Modify an existing ticket: epic, sprint, role, owner, priority, title."""
    t = load(board, a.id)
    changed = []
    if a.epic is not None:
        _ensure(board, "epic", a.epic)
        t["epic"] = a.epic
        changed.append("epic=%s" % (a.epic or "(none)"))
    if a.sprint is not None:
        _ensure(board, "sprint", a.sprint)
        t["sprint"] = a.sprint
        changed.append("sprint=%s" % (a.sprint or "(none)"))
    if a.role is not None:
        t["role"] = a.role
        changed.append("role=%s" % (a.role or "(any)"))
    if a.priority is not None:
        t["priority"] = a.priority
        changed.append("priority=%d" % a.priority)
    if a.title:
        t["title"] = a.title
        changed.append("title")
    if a.needs is not None:
        t["needs"] = _ids(a.needs)
        changed.append("needs=%s" % (",".join(t["needs"]) or "(none)"))
    if a.owner is not None:
        # hard assignment by the master: takes the lock on their behalf
        prev_owner = t.get("owner") or ""
        if t["status"] == "open" and a.owner:
            got = try_claim(board, t["id"], a.owner)
            if not got:
                sys.exit("%s was claimed by someone else while assigning" % t["id"])
            t = got
            changed.append("claimed for %s" % a.owner)
            _safe(lambda: _bind_agent_ticket(board, a.owner, t["id"]), None)
        elif t["status"] in ("claimed", "review"):
            t["owner"] = a.owner
            changed.append("owner=%s" % a.owner)
            if prev_owner and prev_owner != a.owner:
                _safe(lambda: _clear_agent_ticket(board, prev_owner, t["id"]), None)
            if a.owner:
                _safe(lambda: _bind_agent_ticket(board, a.owner, t["id"]), None)
    if not changed:
        sys.exit("nothing to change; see tickets assign --help")
    note_text = "assign: " + ", ".join(changed)
    if getattr(a, "notes", ""):
        note_text += " -- " + a.notes
    t["notes"].append({"by": whoami(a.by), "at": now(), "text": note_text})
    save(board, t)
    print("%s: %s" % (t["id"], ", ".join(changed)))


def cmd_reserve(a, board):
    """Reserve an open ticket for an agent without claiming (T-552).

    Status stays TO DO; no wait-turns. Master/planner/optimizer may --for;
    anyone may --drop. tickets next --steal <id> and assign --owner override.
    """
    t = load(board, a.id)
    who = whoami(getattr(a, "owner", "") or "")
    drop = bool(getattr(a, "drop", False))
    target = (getattr(a, "for_agent", None) or "").strip()
    if drop and target:
        sys.exit("tickets reserve: use --for <agent> or --drop, not both")
    if not drop and not target:
        sys.exit("tickets reserve <id> --for <agent>  (or --drop)")
    if drop:
        prev = _reserved_agent(t)
        if not prev:
            sys.exit("%s is not reserved" % t["id"])
        t.pop("reserved_for", None)
        t["notes"].append({"by": who, "at": now(), "text": "reserve: dropped %s" % prev})
        save(board, t)
        print("%s: reservation dropped (was %s)" % (t["id"], prev))
        return
    if not _may_set_reservation(board, who):
        sys.exit("tickets reserve --for is master/planner/optimizer only (anyone may --drop)")
    if t.get("status") != "open":
        sys.exit("%s is %s; reserve only open tickets" % (t["id"], t.get("status") or "?"))
    unknown = not _agent_rec(board, target)
    t["reserved_for"] = target
    note = "reserve: for %s" % target
    if unknown:
        note += " (unknown agent; warning)"
        print("warning: %r is not a registered agent; reservation still set" % target)
    t["notes"].append({"by": who, "at": now(), "text": note})
    save(board, t)
    print("%s: reserved for %s" % (t["id"], target))


# ---- epics --------------------------------------------------------------

def cmd_epic(a, board):
    sub = a.epic_cmd
    if sub == "create":
        e = _alloc(epics_dir(board), "E", 3, {
            "title": a.title, "body": a.body or "", "status": "open",
            "created": now(), "updated": now(),
        })
        print("created %s  %s" % (e["id"], e["title"]))
        return
    if sub == "done":
        e = get_epic(board, a.id)
        e["status"] = "done"
        _save_in(epics_dir(board), e)
        print("%s done" % a.id)
        return
    tickets = load_all(board)
    epics = load_epics(board)
    if sub == "show":
        e = get_epic(board, a.id)
        mine = [t for t in tickets if t.get("epic") == e["id"]]
        d, n, c, b = progress(mine)
        print("%s  %s  %s  (%d in flight, %d blocked)" % (e["id"], e["title"], bar(d, n), c, b))
        if e.get("body"):
            print(e["body"])
        for t in mine:
            print("  " + line(t, tickets))
        return
    # list
    if not epics:
        print("no epics (tickets epic create \"title\")")
        return
    for e in epics:
        mine = [t for t in tickets if t.get("epic") == e["id"]]
        d, n, c, b = progress(mine)
        flag = " DONE" if e.get("status") == "done" else ""
        print("%s  %-40s %s  %d active, %d blocked%s" % (e["id"], e["title"][:40], bar(d, n), c, b, flag))
    loose = [t for t in tickets if not t.get("epic")]
    if loose:
        print("(%d tickets have no epic: %s)" % (len(loose), ", ".join(t["id"] for t in loose[:12])
                                                  + (" ..." if len(loose) > 12 else "")))


# ---- sprints ------------------------------------------------------------

def cmd_sprint(a, board):
    sub = a.sprint_cmd
    tickets = load_all(board)
    if sub == "create":
        s = _alloc(sprints_dir(board), "S", 2, {
            "goal": a.goal, "status": "planned", "start": a.start or "", "end": a.end or "",
            "created": now(), "updated": now(),
        })
        print("created %s  %s" % (s["id"], s["goal"]))
        if a.activate:
            a.id = s["id"]
            sub = "start"
        else:
            return
    if sub == "start":
        for s in load_sprints(board):
            if s.get("status") == "active" and s["id"] != a.id:
                s["status"] = "done" if a.close_previous else "planned"
                _save_in(sprints_dir(board), s)
        s = get_sprint(board, a.id)
        s["status"] = "active"
        if not s.get("start"):
            s["start"] = now()
        _save_in(sprints_dir(board), s)
        print("%s is now the active sprint: %s" % (s["id"], s["goal"]))
        return
    if sub == "add":
        _ensure(board, "sprint", a.id)
        for tid in _ids(a.tickets):
            t = load(board, tid)
            t["sprint"] = a.id
            save(board, t)
        print("%s <- %s" % (a.id, a.tickets))
        return
    if sub == "close":
        s = get_sprint(board, a.id)
        mine = [t for t in tickets if t.get("sprint") == s["id"]]
        left = [t for t in mine if t["status"] != "done"]
        s["status"] = "done"
        s["end"] = now()
        d, n, c, b = progress(mine)
        _save_in(sprints_dir(board), s)
        print("%s closed: %s" % (s["id"], bar(d, n)))
        if left:
            if a.carry:
                _ensure(board, "sprint", a.carry)
                for t in left:
                    t["sprint"] = a.carry
                    t["notes"].append({"by": whoami(), "at": now(),
                                       "text": "carried over from %s" % s["id"]})
                    save(board, t)
                print("carried %d unfinished into %s: %s" % (
                    len(left), a.carry, ", ".join(t["id"] for t in left)))
            else:
                print("unfinished (still tagged %s): %s" % (s["id"], ", ".join(t["id"] for t in left)))
        return
    if sub == "show":
        s = get_sprint(board, a.id) if a.id else active_sprint(board)
        if not s:
            print("no active sprint")
            return
        mine = [t for t in tickets if t.get("sprint") == s["id"]]
        d, n, c, b = progress(mine)
        print("%s [%s]  %s  %s  (%d in flight, %d blocked)" % (
            s["id"], s.get("status"), s["goal"], bar(d, n), c, b))
        if s.get("start"):
            print("started %s ago" % fmt_hours(hours_since(s["start"])))
        for t in mine:
            print("  " + line(t, tickets))
        return
    # list
    sprints = load_sprints(board)
    if not sprints:
        print("no sprints (tickets sprint create \"goal\" --activate)")
        return
    for s in sprints:
        mine = [t for t in tickets if t.get("sprint") == s["id"]]
        d, n, c, b = progress(mine)
        print("%s [%-7s] %-40s %s" % (s["id"], s.get("status"), s["goal"][:40], bar(d, n)))


# ---- master -------------------------------------------------------------

ONBOARDING_STARTUP = """**You are onboarding.**

This board is being set up. I will ask you four things, in order:
1. **What name** should I announce on the board?
2. **Which integrations** do you want to use? (I will list every one I
   know, and whether it is on this machine.)
3. I will **announce that name** on the board with the integrations you
   picked.
4. Then I will ask for **tasks and the objective**, and turn tasks into a
   `tickets plan` graph (real `--after` edges), not a flat list.

I will not spawn workers or create tickets until you answer.
Run `tickets harness available` to probe every catalog row (missing is a row).
It auto-checks usage; missing remaining/reset is a FAIL row.
"""

# Probe-only catalog for a new board. Codex stays listed with zero usage.
# Gemini dispatches like the others; persist/hooks is the wake. No new Claude fable.
# usage_args: non-spawning status/about only. Never -p/--print/exec/prompt.
INTEGRATION_CATALOG = (
    {"id": "cursor", "name": "Cursor", "binaries": ("agent", "cursor-agent"),
     "if_yes": "tickets spawn <seat> --harness cursor --persist",
     "policy": "ok to spawn if chosen",
     "usage_args": ("about", "--format", "json")},
    {"id": "agy", "name": "Antigravity", "binaries": ("agy",),
     "if_yes": "tickets spawn <seat> --harness agy --persist",
     "policy": "ok to spawn if chosen",
     "usage_args": ("help",)},
    {"id": "claude", "name": "Claude Code", "binaries": ("claude",),
     "if_yes": "tickets spawn <seat> --harness claude",
     "policy": "ok to spawn if chosen; no new Claude fable",
     "usage_args": ("auth", "status", "--json")},
    {"id": "codex", "name": "Codex", "binaries": ("codex",),
     "if_yes": "tickets spawn <seat> --harness codex",
     "policy": "catalog even with zero usage; do not spawn unless they say usage is back",
     "usage_args": ("login", "status")},
    {"id": "devin", "name": "Devin", "binaries": ("devin",),
     "if_yes": "tickets spawn <seat> --harness devin",
     "policy": "list; spawn only if the operator confirms the harness exists",
     "usage_args": ("auth", "status")},
    {"id": "gemini", "name": "Gemini CLI", "binaries": ("gemini",),
     "if_yes": "tickets dispatch T-id --to <seat> --harness gemini  # persist/hooks wake",
     "policy": "ok to dispatch; persist/hooks wake; do not spawn a Gemini product job",
     "usage_args": ("--version",)},
    {"id": "grok", "name": "Grok (Cursor persist / grokbots)",
     "binaries": ("agent", "cursor-agent"),
     "if_yes": "tickets spawn <seat> --harness grok --persist",
     "policy": "Cursor Grok seats and grok-worker; same persist wake as cursor",
     "usage_args": ("about", "--format", "json")},
)


def print_onboarding_startup():
    """First thing connect / boot / master brief show — not after MASTER.md history."""
    sys.stdout.write(ONBOARDING_STARTUP)
    if not ONBOARDING_STARTUP.endswith("\n"):
        sys.stdout.write("\n")
    sys.stdout.write("\n")


CEO_ONBOARDING_STARTUP = """**You are onboarding as Atman CEO.**

Connecting here is joining **Atman**, not Claude, Cursor, Codex, or any
other provider. Board identity is `atman-<seat>` (example: `atman-ceo`).

This is a living board. Do not invent a new team. Do not `tickets init`
or `tickets clear`. CoS (`cursor`) staffs; you do not spawn, and you do
not claim worker tickets.

Product flow (this order):
1. Catalog + usage
2. Attach the living board / objective
3. Join as `atman-<seat>`
4. Announce the Atman role
5. Ask the operator for feedback
6. Show tasks you can actually run (`tickets graph` / `tickets map`)
"""


def board_is_living(board):
    """True when this is an existing team, not a blank `tickets init`."""
    if not board or not os.path.isdir(board):
        return False
    obj = _safe(lambda: load_objective(board), {}) or {}
    if str(obj.get("text") or "").strip():
        return True
    tickets = _safe(lambda: load_all(board), []) or []
    return bool(tickets)


def atman_seat_name(seat="ceo"):
    raw = (seat or "ceo").strip().lower()
    if raw.startswith("atman-"):
        raw = raw[len("atman-"):]
    cleaned = []
    for ch in raw:
        cleaned.append(ch if (ch.isalnum() or ch == "-") else "-")
    raw = "".join(cleaned).strip("-") or "ceo"
    if raw in ("master", "everyone", "cursor"):
        raw = "ceo"
    return "atman-%s" % raw


def _join_is_ceo_path(owner, roles):
    owner = owner or ""
    if isinstance(roles, str):
        roles = [r.strip() for r in roles.split(",") if r.strip()]
    roles = list(roles or [])
    if owner.startswith("atman-"):
        return True
    if "master" in roles:
        return True
    return False


def print_integration_catalog(rows, note=""):
    print("INTEGRATIONS (probe only — do not spawn until the operator answers)")
    if note:
        print("codex: %s" % note)
    print("%-8s %-14s %-22s %-8s %s" % ("id", "name", "binaries", "on_disk", "path / policy"))
    for r in rows:
        bins = ", ".join(r["binaries"])
        print("%-8s %-14s %-22s %-8s %s" % (
            r["id"], r["name"][:14], bins[:22], "yes" if r["on_disk"] else "no",
            (r["path"] or "(missing)")))
        print("         %s" % r["policy"])
        print("         if they say yes: %s" % r["if_yes"])
        if "usage" in r:
            _print_catalog_usage_line(r)


def print_recorded_usage(board):
    print("USAGE (recorded limits; installed is not quota)")
    any_ = False
    for r in _safe(lambda: load_agents(board), []) or []:
        lim = r.get("limit")
        if lim:
            any_ = True
            print("  %-14s hit %s ago%s%s" % (
                r["owner"], fmt_hours(hours_since(lim["at"])),
                (", back %s" % lim["until"]) if lim.get("until") else "",
                (" -- %s" % lim["note"]) if lim.get("note") else ""))
    if not any_:
        print("  none recorded (`tickets limit --until` is how a seat says it is out)")
    print("Ask: which of these still have usage, and which should CoS start?")
    print("Installed + no usage = stay on the catalog; CoS does not spawn them.")


def print_ceo_connect(board, seat="ceo"):
    """Executable product flow for any new CEO. Does not print tickets next."""
    sys.stdout.write(CEO_ONBOARDING_STARTUP)
    if not CEO_ONBOARDING_STARTUP.endswith("\n"):
        sys.stdout.write("\n")
    sys.stdout.write("\n")
    name = atman_seat_name(seat)
    root = os.path.dirname(os.path.abspath(board)) if board else os.getcwd()
    rows, note = probe_integration_catalog()
    print("1. CATALOG + USAGE")
    print_integration_catalog(rows, note)
    print("Codex stays in the catalog with zero usage. Do not spawn Gemini. No new Claude fable.")
    print("Cursor-only spawns unless the operator says otherwise. CoS (`cursor`) staffs.")
    print("")
    print_recorded_usage(board)
    print("")
    print("2. LIVING BOARD (attach; do not invent a new team)")
    print("board: %s" % (board or "(none)"))
    obj = _safe(lambda: load_objective(board), {}) or {}
    text = str(obj.get("text") or "").strip()
    if text:
        print("objective: %s" % text[:400])
        print("Attach this objective. Do not `tickets objective --set` unless it is empty.")
    else:
        print("objective: (none yet — ask one sentence, then `tickets objective --set`)")
    print("Do not tickets init. Do not tickets clear. Do not name a new team.")
    print("")
    print("3. JOIN AS `%s`" % name)
    print("   export TICKET_AGENT=%s" % name)
    print("   export TICKETS_DIR=%s" % (board or "$PWD/.tickets"))
    print("   cd %s" % root)
    print("   tickets join %s --roles master --can own-machine,browser --cost high --persistent --wake-mode continuous --harness cursor" % name)
    print("   tickets hooks cursor --agent %s" % name)
    print("   tickets hooks codex --agent %s     # mail follow-up is not Claude-only" % name)
    print("   tickets hooks remote --agent %s" % name)
    print("   tickets master take")
    print("   tickets master")
    print("   tickets inbox")
    print("   tickets objective")
    print("")
    print("4. ANNOUNCE THE ATMAN ROLE")
    print('   tickets msg --to everyone "%s is Atman CEO on this living board. CoS is cursor. Integrating: <list from step 1>. @everyone"' % name)
    print('   tickets master log "ceo onboard: seat=%s integrations=<list>"' % name)
    print("")
    print("5. ASK FOR FEEDBACK")
    print("   Ask the operator: what should change about this connect path?")
    print('   tickets msg --to cursor "CEO %s onboarded. Operator feedback: <their answer>"' % name)
    print("")
    print("6. TASKS YOU CAN RUN (graph / map)")
    print("   tickets graph")
    print("   tickets map")
    print("   tickets drive")
    print("   tickets update / tickets here")
    print("   Mid-run: tickets plan (JSON keys+deps), tickets dep, tickets create --blocks")
    print("   CoS (cursor) staffs workers. CEO does not claim worker tickets.")
    print("")
    print("CoS is cursor. Mail: tickets msg --to cursor. Never Grok DMs.")
    print("HOLD T-773 T-774. No tickets clear.")
    print("If `tickets self` still points at sol-agy-harness, recut ~/.local/bin/tickets")
    print("onto this checkout before trusting PATH `tickets connect`.")


MASTER_TEMPLATE = """**You are onboarding.**

# MASTER -- coordination node for this board

Any agent can become master: run `tickets master take`, then `tickets master`
to get the full briefing. Keep this file current; it is the memory that
survives agent restarts and timeouts.

## ONBOARDING — startup (show the operator this first)

""" + ONBOARDING_STARTUP + """
Walk these four asks in order. Do not skip ahead. This is not a ticket claim
and not a merge pass.

### Step 1 — Name

Ask: **What name do you want to give this board / team?** (one word or a
short phrase; this is what everyone will see.)

Record it here: `Onboarding name:` _(none yet — ask)_

### Step 2 — Integrations (check all, then ask)

Run `tickets harness available`. It probes `command -v` for every catalog
entry (Cursor `agent`/`cursor-agent`, `agy`, `claude`, `codex`, `devin`,
`gemini`) and auto-checks usage. Missing binary is a row, not a skip.
Missing remaining or reset is a FAIL row. If `~/.local/bin/codex` is stale,
it retargets to the newest `openai.chatgpt-*` extension binary.

Ask: **Which of these do you want to use?** Do not spawn until they answer.
Codex stays in the catalog even with **no usage**. Gemini dispatch records
harness=gemini; persist/hooks is the wake (no Gemini product job).
No new Claude fable.

### Step 3 — Announce that name on the board

After they pick a name and integrations:

```
tickets msg --to everyone "<name> is onboarding. Integrating: <list>. Objective and tasks next. @everyone"
tickets master log "onboarding: name=<name> integrations=<list>"
```

Write the name into `Onboarding name:` above so successors do not re-ask.

### Step 4 — Objective, then a real dependency graph

Ask, in this order:

1. **What is the objective?** (one sentence the master will drive toward)
2. **What tasks** should be on the board now? (titles; split if they dump a list)
3. **What depends on what?** (edges. A flat list is allowed only when they
   said there are none.)

Then set the objective and create the graph in one shot. `deps` may be a
`key` from the same JSON or an existing `T-` id. Do **not** run one
`tickets create` per title. Do not invent extra tickets. Do not leave
blockers only in the ticket body.

```
tickets objective "<their sentence>"
tickets plan <<'EOF'
[{"key":"api","title":"Build REST API","role":"backend","deps":[]},
 {"key":"ui","title":"Build login UI","role":"frontend","deps":["api"]}]
EOF
tickets graph
tickets map
```

Mid-run (same loop — not a second planner product):

```
tickets dep T-004 --after T-003
tickets create "DB migration" --blocks T-002
tickets create "Add rate limiting" --deps T-002
```

Follow-up every wake (master or CoS): `tickets update` / `tickets here`;
reopen silent >90m claims (`tickets reopen`); `tickets drive` toward the
objective; drain the review queue. Show `tickets graph` / `tickets map`.
If HEALTH flags prose-only deps, wire `tickets dep` instead of leaving
them in the body.

Capture / sound / dispatch (Fatih loop on this board, not a plans/ tree):
`tickets capture`, `tickets sound`, CoS `tickets dispatch --harness …`,
`tickets pr-sync` after the PR is merged. CEO does not `tickets next`.

Do not implement those tasks in this session. Spawning children when a
parent is done is a separate success-trigger, not this onboarding step.
Spawn seats only from the integrations they confirmed, one ticket each.

## COS ONBOARDING — future (do not run on a living board unless asked)

**You are onboarding as chief of staff.** Master plans and scopes. CoS
reviews, unblocks, merges, and staffs. Same integration catalog as master.
After the board has an objective and a `tickets plan` graph:

1. `tickets graph` / `tickets map` — statuses and `--after` edges, not prose.
2. Follow-up: `tickets update` / `here`; `tickets reopen` silent >90m claims;
   `tickets drive` toward the objective; review queue.
3. Mid-run graph edits: `tickets dep` / `tickets create --blocks`.
4. Announce with `tickets master cos <name>` and `tickets msg --to everyone`.

Do not dump a live-board plan. Do not invent a second planner.

## CEO ONBOARDING — living board (product flow)

**You are onboarding as Atman CEO.** Connecting is joining Atman, not a
provider. Identity is `atman-<seat>` (example `atman-ceo`). CoS (`cursor`)
staffs. CEO does not claim worker tickets on this path.

Run `tickets connect` (or `tickets connect --ceo`). It executes, in order:

1. Catalog + usage (`tickets harness available` + recorded limits)
2. Attach the living board / objective — do not invent a new team
3. `tickets join atman-<seat> --roles master ...`
4. Announce the Atman role (`tickets msg --to everyone`)
5. Ask the operator for feedback
6. `tickets graph` / `tickets map` — tasks they can actually run

Do not `tickets init` or `tickets clear`. Do not one `tickets create` per
title — `tickets plan` with real deps if they add work. Cursor-only
spawns unless they say otherwise. Mail hooks are not Claude-only:
`tickets hooks cursor|codex|remote|claude --agent atman-<seat>`.

HANDOVER dated 2026-09-08 is historical, not live authority. Live:
`tickets master`, `tickets role list`, the message board, this section.

## Mission
(what we are building, one paragraph)

## Workforce
| agent (TICKET_AGENT) | tool | default roles | worktree |
|---|---|---|---|
| example | Claude Code | backend | .worktrees/example |

## Rules for every agent
1. Claim before you work (`tickets next`). Never work without a ticket; never
   edit `.tickets/` by hand.
2. Finish what you claim. If you cannot, `tickets block` with a reason or
   `tickets reopen` -- do not go silent. Hold one ticket at a time.
3. You may create, split, re-wire and assign tickets (`create --blocks`,
   `dep`, `assign`, `plan`). Extending the graph is expected, not exceptional.
4. Work on your own git worktree and branch, never on main. Commit as you go.
   `tickets done` refuses from main or with uncommitted files.
5. Post `tickets update <id> "..."` at least every 45 minutes and at each
   milestone. Silence longer than that is treated as a timeout.
6. `done --notes` must include branch@sha (added automatically), the paths
   you touched, and every decision a dependent ticket must match. Then merge
   (or open the PR) before claiming the next ticket. Workers submit
   `tickets review` (sounded code tickets need `--pr`); master `tickets merge`
   then `tickets done`. `tickets pr-sync` reports ready-to-close and does not
   self-done.
7. Set `TICKET_AGENT` to your own name so the board can tell agents apart.
8. Sound before staff: `tickets capture` is not claimable. `tickets sound`
   fills cause/change/proof/deps. CoS `tickets dispatch`. CEO does not
   `tickets next`.


## Sprint plan
(goals per sprint; `tickets sprint list` has the live numbers)

## Decision log
(append with `tickets master log "..."`)
"""


def cmd_master(a, board):
    sub = a.master_cmd or "brief"
    path = master_path(board)
    if sub == "init":
        if os.path.exists(path) and not a.force:
            print("MASTER.md exists; use --force to overwrite")
            return
        with open(path, "w") as f:
            f.write(MASTER_TEMPLATE)
        print("wrote %s -- fill in Mission, Workforce, Sprint plan" % path)
        return
    if sub == "cos":
        prev = current_master(board) or {}
        if not prev.get("owner"):
            sys.exit("no master yet (tickets master take first)")
        who = a.text or ""
        if not who:
            print("chief of staff: %s" % (prev.get("cos") or "(none)"))
            return
        prev["cos"] = "" if who in ("none", "-", "clear") else who
        with open(master_state_path(board), "w") as f:
            json.dump(prev, f)
        _master_log(board, "chief of staff set to %s (master %s keeps planning/scope; cos reviews, unblocks, merges)"
                    % (prev["cos"] or "none", prev["owner"]))
        print("chief of staff: %s" % (prev["cos"] or "none"))
        return
    if sub == "take":
        owner = whoami(a.owner)
        prev = current_master(board)
        with open(master_state_path(board), "w") as f:
            json.dump({"owner": owner, "since": now(), "cos": (prev or {}).get("cos", "")}, f)
        _master_log(board, "%s took over as master%s" % (
            owner, (" from %s" % prev["owner"]) if prev and prev.get("owner") != owner else ""))
        print("%s is master now. Run `tickets master` for the briefing." % owner)
        return
    if sub == "release":
        if os.path.exists(master_state_path(board)):
            os.unlink(master_state_path(board))
        _master_log(board, "%s released master" % whoami(a.owner))
        print("master released")
        return
    if sub == "log":
        _master_log(board, a.text, by=whoami(a.owner))
        print("logged")
        return
    # brief
    if not board_is_living(board):
        print_onboarding_startup()
    tickets = load_all(board)
    m = current_master(board)
    print("=" * 72)
    print("MASTER BRIEFING  %s" % board)
    obj = _safe(lambda: load_objective(board), {})
    if obj:
        print("OBJECTIVE%s: %s" % (" (met)" if obj.get("done") else "", obj.get("text", "")[:300]))
    me = whoami()
    if m:
        stale = hours_since(m["since"])
        print("current master: %s (for %s)%s" % (m["owner"], fmt_hours(stale),
              "  <- that is you" if m["owner"] == me else ""))
        if m["owner"] != me:
            print("TAKING OVER? Masters die on usage limits. Run `tickets master take`, then in order: "
                  "`tickets limits` (who is out), REVIEW QUEUE below (merge with `tickets merge`), "
                  "HEALTH below, `tickets who`. Everything decided so far is in the Decision log.")
    else:
        print("current master: nobody -- `tickets master take` to become it, then follow HANDOVER in MASTER.md")
    print("=" * 72)
    if os.path.exists(path):
        with open(path) as f:
            print(f.read().rstrip())
    else:
        print("(no MASTER.md yet -- `tickets master init`)")
    print("")
    print("-" * 72)
    print("LIVE STATUS")
    print("-" * 72)
    cur = active_sprint(board)
    if cur:
        mine = [t for t in tickets if t.get("sprint") == cur["id"]]
        d, n, c, b = progress(mine)
        print("Sprint %s: %s  %s  (%d in flight, %d blocked)" % (cur["id"], cur["goal"], bar(d, n), c, b))
    else:
        print("Sprint: none active")
    epics = load_epics(board)
    if epics:
        print("Epics:")
        for e in epics:
            mine = [t for t in tickets if t.get("epic") == e["id"]]
            d, n, c, b = progress(mine)
            print("  %s %-38s %s" % (e["id"], e["title"][:38], bar(d, n)))
    d, n, c, b = progress(tickets)
    print("Board: %s  %d in flight, %d blocked, %d ready" % (
        bar(d, n), c, b, len(unblocked(board, tickets))))
    wf = load_workforce(board)
    roles = load_roles(board)
    agents = dict((r["owner"], r) for r in load_agents(board))
    names = sorted(set(list(wf) + [r for r in roles if roles[r] or r in agents] + list(agents)))
    if names:
        print("")
        print("Workforce:")
        print("  %-14s %-8s %-22s %-26s %-8s %s" % ("agent", "cost", "roles", "can", "seen", "where"))
        for nme in names:
            e = wf.get(nme, {})
            r = agents.get(nme, {})
            where = ""
            if r.get("branch"):
                where = "%s%s" % (r["branch"], (" +%d" % r["dirty"]) if r.get("dirty") else "")
                if r.get("ticket"):
                    where += "  on %s" % r["ticket"]
            print("  %-14s %-8s %-22s %-26s %-8s %s" % (
                nme[:14], e.get("cost", "-"), ",".join(roles.get(nme, []))[:22] or "any",
                ",".join(e.get("can", []))[:26] or "-",
                (fmt_hours(hours_since(r["seen"])) if r.get("seen") else "never"), where))
    m_owner = m["owner"] if m else whoami()
    msgs = unread(board, m_owner)
    if msgs:
        print("")
        print("Unread messages for %s (%d):" % (m_owner, len(msgs)))
        for mm in msgs[-8:]:
            print("  " + fmt_msg(mm))
    queue = [t for t in tickets if t["status"] == "review"]
    if queue:
        print("")
        print("REVIEW QUEUE (%d) -- master: review, merge, then `tickets done <id> --notes \"merged as <sha>\"`:" % len(queue))
        for t in queue:
            print("  %s @%-12s %-46s %s  waiting %s%s" % (
                t["id"], t.get("owner", "?"), t["title"][:46], t.get("commit", "?"),
                fmt_hours(hours_since(t.get("review_at", t["updated"]))),
                ("  PR " + t["pr"]) if t.get("pr") else ""))
    print("")
    print("In flight:")
    for t in tickets:
        if t["status"] == "claimed":
            tm = timing(t)
            print("  %s @%-14s %-50s active %s, updated %s ago" % (
                t["id"], t.get("owner", "?"), t["title"][:50],
                fmt_hours(tm["active"]), fmt_hours(tm["since_update"])))
    finished = [t for t in tickets if t["status"] == "done" and t.get("claimed_at") and t.get("done_at")]
    if finished:
        avg = sum(timing(t)["active"] or 0 for t in finished) / len(finished)
        print("Cycle time: avg %s over %d timed tickets" % (fmt_hours(avg), len(finished)))
    print("")
    issues = health(board, tickets)
    print("-" * 72)
    print("HEALTH  (%d issues)" % len(issues))
    print("-" * 72)
    if not issues:
        print("  clean")
    for sev, msg, fix in issues:
        print("  [%s] %s" % (sev, msg))
        if fix:
            print("        fix: %s" % fix)


def _master_log(board, text, by=None):
    path = master_path(board)
    if not os.path.exists(path):
        with open(path, "w") as f:
            f.write(MASTER_TEMPLATE)
    elif os.path.getsize(path) > MASTER_LOG_MAX_BYTES:
        _trim_decision_log(board, path)
    with open(path) as f:
        body = f.read()
    entry = "- %s [%s] %s\n" % (datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M"), by or whoami(), text)
    if "## Decision log" in body:
        if not body.endswith("\n"):
            body += "\n"
        body += entry
    else:
        body += "\n## Decision log\n" + entry
    with open(path, "w") as f:
        f.write(body)


def _trim_decision_log(board, path):
    """MASTER.md's Decision log only ever grows. Once the file passes
    MASTER_LOG_MAX_BYTES, move all but the most recent MASTER_LOG_KEEP_ENTRIES
    '- ' entries to an append-only archive so the briefing stays readable and
    `tickets master log` stays a small, bounded rewrite."""
    try:
        with open(path) as f:
            body = f.read()
    except OSError:
        return
    head, marker, tail = body.partition("## Decision log\n")
    if not marker:
        return
    entries = [ln for ln in tail.split("\n") if ln.startswith("- ")]
    if len(entries) <= MASTER_LOG_KEEP_ENTRIES:
        return
    overflow = entries[: len(entries) - MASTER_LOG_KEEP_ENTRIES]
    kept = entries[len(entries) - MASTER_LOG_KEEP_ENTRIES:]
    archive = os.path.join(board, "MASTER.decisions.archive.md")
    fd = os.open(archive, os.O_CREAT | os.O_WRONLY | os.O_APPEND, 0o644)
    try:
        os.write(fd, ("\n".join(overflow) + "\n").encode())
    finally:
        os.close(fd)
    new_body = (head + "## Decision log\n"
                + "(%d older entries archived to MASTER.decisions.archive.md)\n" % len(overflow)
                + "\n".join(kept) + "\n")
    with open(path, "w") as f:
        f.write(new_body)


def health(board, tickets):
    """Things a master must act on. Returns [(severity, message, fix)]."""
    with _shared_watch_table():
        return _health_body(board, tickets)


def _health_body(board, tickets):
    out = []
    by_id = dict((t["id"], t) for t in tickets)
    done = set(t["id"] for t in tickets if t["status"] == "done")
    cyc = find_cycle(tickets)
    if cyc:
        out.append(("CRIT", "dependency cycle %s" % " -> ".join(cyc),
                    "tickets dep %s --drop %s" % (cyc[0], cyc[1])))
    for tid, miss in dangling(tickets).items():
        out.append(("CRIT", "%s depends on non-existent %s" % (tid, ",".join(miss)),
                    "tickets dep %s --drop %s" % (tid, ",".join(miss))))
    for t in tickets:
        if t["status"] != "claimed":
            continue
        tm = timing(t)
        su = tm["since_update"]
        if su is not None and su * 60 > UPDATE_EVERY_MIN * 2:
            out.append(("WARN", "%s (@%s) silent for %s -- likely timed out" % (
                t["id"], t.get("owner"), fmt_hours(su)),
                "tickets reopen %s   # or ping the agent" % t["id"]))
        elif su is not None and su * 60 > UPDATE_EVERY_MIN:
            out.append(("INFO", "%s (@%s) no update for %s" % (t["id"], t.get("owner"), fmt_hours(su)),
                        "ask for `tickets update %s`" % t["id"]))
    for t in tickets:
        if t["status"] != "blocked":
            continue
        reason = t["notes"][-1]["text"] if t.get("notes") else ""
        import re
        refs = [r for r in re.findall(r"\bT-\d{3}\b", reason) if r != t["id"] and r in by_id]
        unwired = [r for r in refs if r not in t.get("deps", [])]
        if unwired:
            ready = all(r in done for r in unwired)
            out.append(("WARN", "%s blocked 'on %s' in prose but no graph edge%s" % (
                t["id"], ",".join(unwired), " -- and those are DONE now" if ready else ""),
                "tickets dep %s --after %s && tickets reopen %s" % (t["id"], ",".join(unwired), t["id"])
                if ready else "tickets dep %s --after %s" % (t["id"], ",".join(unwired))))
        elif not refs and not t.get("deps"):
            out.append(("INFO", "%s blocked on something outside the board: %s" % (
                t["id"], reason[:70]), "create a ticket for the prerequisite with --blocks %s" % t["id"]))
    owners = {}
    for t in tickets:
        if t["status"] == "claimed":
            owners.setdefault(t.get("owner"), []).append(t["id"])
    for o, ids in owners.items():
        if len(ids) > 1:
            out.append(("INFO", "%s holds %d tickets at once: %s" % (o, len(ids), ",".join(ids)),
                        "one at a time unless deliberately parallel"))
    wf = load_workforce(board)
    all_can = set(c for e in wf.values() for c in e.get("can", []))
    for t in tickets:
        if t["status"] in ("open",) and t.get("needs"):
            missing = [n for n in t["needs"] if n not in all_can]
            if missing:
                out.append(("WARN", "%s needs %s but no registered agent can do that" % (
                    t["id"], ",".join(missing)),
                    "tickets join <agent> --can %s   # e.g. grok, it has its own machine" % ",".join(missing)))
    for r in load_agents(board):
        if r.get("limit"):
            held = [t["id"] for t in tickets if t["status"] == "claimed" and t.get("owner") == r["owner"]]
            out.append(("WARN", "%s hit a usage limit %s ago%s%s" % (
                r["owner"], fmt_hours(hours_since(r["limit"]["at"])),
                (", back %s" % r["limit"]["until"]) if r["limit"].get("until") else "",
                (" -- still holds %s" % ",".join(held)) if held else ""),
                ("tickets reopen %s   # hand to someone else" % held[0]) if held else "tickets limit %s --clear when back" % r["owner"]))
    for r in load_agents(board):
        if r.get("branch") in ("main", "master") and hours_since(r.get("seen", "")) < 24:
            out.append(("WARN", "%s is working on %s (rule 4)" % (r["owner"], r["branch"]),
                        "git worktree add .worktrees/%s -b %s-work" % (r["owner"], r["owner"])))
    for r in load_agents(board):
        n = _watcher_count(r["owner"], board)
        if n > 1:
            out.append(("WARN", "%s has %d watch loops running (expected 1)" % (r["owner"], n),
                        "tickets spawn %s --stop" % r["owner"]))
    ready_ids = set(t["id"] for t in unblocked(board, tickets))
    agents_by = dict((r.get("owner"), r) for r in load_agents(board))
    for t in tickets:
        who = _reserved_agent(t)
        if not who or t.get("status") != "open" or t["id"] not in ready_ids:
            continue
        rec = agents_by.get(who) or {}
        reason = ""
        if rec.get("limit"):
            reason = "limited"
        elif not rec.get("seen"):
            reason = "no heartbeat"
        elif hours_since(rec.get("seen", "")) > 1.5:
            reason = "silent %s" % fmt_hours(hours_since(rec["seen"]))
        if reason:
            out.append(("WARN", "%s is READY and reserved for %s who is down/limited (%s)" % (
                t["id"], who, reason),
                "tickets reserve %s --drop" % t["id"]))
    if not active_sprint(board):
        out.append(("INFO", "no active sprint", "tickets sprint create \"goal\" --activate"))
    loose = [t["id"] for t in tickets if not t.get("epic") and t["status"] != "done"]
    if loose and load_epics(board):
        out.append(("INFO", "%d open tickets have no epic" % len(loose),
                    "tickets assign <id> --epic E-xxx"))
    if not os.path.exists(master_path(board)):
        out.append(("WARN", "no MASTER.md -- nobody can take over cleanly", "tickets master init"))
    return out


def cmd_reopen(a, board):
    t = load(board, a.id)
    notes = getattr(a, "notes", "") or ""
    # T-394: silent reopen of IN REVIEW (or review_at leftover) returns the
    # ticket to `next` while notes still read as REVIEW. Claimed work that
    # never entered review stays reopenable without notes (T-246).
    if (t["status"] == "review" or t.get("review_at")) and not notes.strip():
        sys.exit(
            'reopen of IN REVIEW work needs --notes "why" '
            "(silent reopen returns it to next and looks like a next-reissue bug)"
        )
    if notes:
        # Attribute to the acting agent, not the ticket's outgoing owner --
        # reopen is very often one agent (a reviewer, the master) sending
        # BACK another agent's ticket, and stamping the reason as though the
        # outgoing owner wrote it is the same misattribution class as T-238.
        t["notes"].append({"by": whoami(getattr(a, "by", "")), "at": now(), "text": notes})
    before = t["status"]
    prev_owner = t.get("owner", "")
    t["status"] = "open"
    t["owner"] = ""
    save(board, t)
    _safe(lambda: traj_event(board, "reopen", agent=whoami(getattr(a, "by", "")),
                             ticket=t, state_before=before, state_after="open",
                             outcome="reopened", prev_owner=prev_owner,
                             notes_len=len(getattr(a, "notes", "") or ""),
                             **_traj_git()), None)
    lock = os.path.join(board, a.id + ".lock")
    if os.path.exists(lock):
        os.unlink(lock)
    print("%s reopened" % a.id)


def is_fixture_board(board):
    """True only when the board is explicitly marked disposable."""
    return os.path.isfile(os.path.join(board, ".fixture-board"))


def cmd_clear(a, board):
    """Delete ticket files — FIXTURE BOARDS ONLY."""
    if not is_fixture_board(board):
        sys.exit(
            "REFUSED: tickets clear will not wipe a live board (missing .fixture-board).\n"
            "This command only deletes tickets on disposable fixture boards.\n"
            "Use a disposable fixture board for tests, or:\n"
            "  tickets board-backup --out /tmp/board.tgz\n"
            "  tickets board-restore --archive /tmp/board.tgz --dest /tmp/fixture\n"
            "There is no --force that clears a live board."
        )
    if not getattr(a, "yes", False):
        sys.exit(
            "refusing: fixture board %s — pass --yes to delete T-*.json / locks there" % board
        )
    n = 0
    for path in glob.glob(os.path.join(board, "T-*.json")) + glob.glob(
        os.path.join(board, "T-*.lock")
    ):
        os.unlink(path)
        n += 1
    print("removed %d files from fixture board %s" % (n, board))


def cmd_board_backup(a, board):
    """Backup tickets/roles/coordination to a tarball (fixture by default)."""
    from pathlib import Path

    from board_backup import backup

    manifest = backup(
        Path(board),
        Path(a.out),
        allow_live=bool(getattr(a, "i_understand_live", False)),
    )
    print(json.dumps(manifest, indent=2))


def cmd_board_restore(a, board):
    """Restore a backup into a fixture destination board."""
    from pathlib import Path

    from board_backup import restore

    result = restore(
        Path(a.archive),
        Path(a.dest),
        allow_live=bool(getattr(a, "i_understand_live", False)),
    )
    print(json.dumps(result, indent=2))


def cmd_where(a, board):
    print(board)
    kids = child_boards(os.getcwd())
    if len(kids) > 1:
        print("other live boards in child dirs:")
        for k in kids:
            print("  " + k)
        print("set TICKETS_DIR to pick one")


def cmd_context(a, board):
    paths = context_paths(board)
    if not paths:
        print("no CONTEXT.md or docs/handoffs/AGENT_CONTEXT.md next to %s" % board)
        sys.exit(1)
    for p in paths:
        print("# " + p)
        with open(p) as f:
            print(f.read().rstrip())
        print()


def cmd_here(a, board):
    """Manually check in: where am I working, on what."""
    owner = whoami(a.owner)
    # T-543/T-551: stamp claimed hold only; never foreign IN REVIEW binds.
    rec = checkin(board, owner, _here_ticket(board, owner), a.note or "")
    print("%s @ %s" % (owner, rec["worktree"] or rec["cwd"]))
    print("  branch %s@%s%s" % (rec["branch"] or "?", rec["sha"] or "?",
                               "  (%d uncommitted)" % rec["dirty"] if rec["dirty"] else ""))
    print("  ticket %s" % (rec["ticket"] or "none"))
    warn = worktree_warning(owner)
    if warn:
        print(warn)


def cmd_who(a, board):
    """Everyone's last known location, branch and ticket."""
    agents = load_agents(board)
    if not agents:
        print("nobody has checked in yet (agents check in automatically on next/claim/update/done)")
        return
    tickets = dict((t["id"], t) for t in load_all(board))
    # One `ps` for the whole board: agent_liveness/_watcher_pid used to scan
    # the process table once per seat (and twice more for watcher_count).
    if getattr(a, "no_liveness", False):
        live = {}
    else:
        with _shared_watch_table():
            live = dict(
                (r["owner"], _safe(lambda r=r: agent_liveness(board, r, agents),
                                   {"state": "unknown", "detail": "liveness read failed",
                                    "source": "none", "heuristic": True}))
                for r in agents)
    wf = load_workforce(board)
    sa = _session_adapters()
    print("%-14s %-9s %-8s %-30s %-20s %s" % ("agent", "state", "loop-seen", "branch@sha", "ticket", "worktree"))
    for r in sorted(agents, key=lambda r: r.get("seen", ""), reverse=True):
        tid = r.get("ticket") or ""
        t = tickets.get(tid)
        tdesc = ("%s %s" % (tid, MARK.get(t["status"], "")) if t else (tid or "-"))
        b = "%s@%s" % (r.get("branch") or "?", r.get("sha") or "?")
        if r.get("dirty"):
            b += " +%d" % r["dirty"]
        # cwd is recorded straight from os.getcwd() with no git resolution in
        # its path, so it is ground truth even when the git-derived "worktree"
        # field was resolved against a polluted environment (T-243).
        wt = r.get("cwd") or r.get("worktree") or ""
        home = os.path.expanduser("~")
        if wt.startswith(home):
            wt = "~" + wt[len(home):]
        lv = live.get(r["owner"]) or {}
        # `loop-seen` is named, not hidden: it is still the honest answer to
        # "when did the watcher last idle", it is just not the answer to "is
        # this agent alive" -- which is what reading it as `seen` implied.
        print("%-14s %-8s%s %-8s %-30s %-20s %s" % (
            r["owner"][:14], (lv.get("state") or "-")[:8], liveness_mark(lv) if lv else " ",
            fmt_hours(hours_since(r.get("seen"))) + " ago", b[:30], tdesc[:20], wt))
        if lv.get("detail"):
            print("%-14s %s" % ("", lv["detail"][:100]))
        if r.get("git_mismatch"):
            print("%-14s !! git resolved a repo that does not contain this agent's cwd at its last "
                  "check-in -- branch/sha above are unreliable; cwd is ground truth (T-243)" % "")
        if r.get("limit"):
            lim = r["limit"]
            print("%-14s !! USAGE LIMIT hit %s ago%s" % ("", fmt_hours(hours_since(lim["at"])),
                                                        (", back %s" % lim["until"]) if lim.get("until") else ""))
        if r.get("note"):
            print("%-14s %s" % ("", "\"%s\"" % r["note"][:90]))
        entry = wf.get(r["owner"], {}) or {}
        harness_name = entry.get("harness") or entry.get("tool") or "claude"
        ep, _ = sa.live_endpoint(board, r["owner"])
        life = lifecycle_of(board, r["owner"], workforce=wf)
        native = bool(ep) and (ep or {}).get("mode") == "native"
        watcher_on = bool(lv.get("watcher") if lv else False)
        remote_on = False
        if harness_name == "remote":
            remote_on = _remote_lease_online(load_remote_state(board, r["owner"]))
        reachable = sa.is_reachable(native_online=native, watcher_online=watcher_on,
                                    remote_online=remote_on)
        print("%-14s lifecycle=%s provider=%s session=%s reachable=%s" % (
            "", life, (ep or {}).get("provider") or harness_name,
            (ep or {}).get("session_id") or (ep or {}).get("thread") or (ep or {}).get("pid") or "-",
            "yes" if reachable else "no"))
    # collisions
    by_branch = {}
    for r in agents:
        # "?" is the unresolved placeholder, not a real branch: agents sharing
        # it are not sharing a worktree, so it must not raise a clobber warning.
        if r.get("branch") and r["branch"] not in ("main", "master", "?"):
            by_branch.setdefault(r["branch"], []).append(r["owner"])
    for b, os_ in by_branch.items():
        if len(set(os_)) > 1:
            print("!! %s share branch %s -- they will clobber each other" % (", ".join(sorted(set(os_))), b))
    on_main = [r["owner"] for r in agents if r.get("branch") in ("main", "master")]
    if on_main:
        print("!! on main/master: %s -- rule 4, move to a worktree" % ", ".join(on_main))
    if live:
        print("")
        print("state is read from the session's own transcript, not from loop-seen.  "
              "! asserted by a human   ~ heuristic (run cadence only)   ? unknown -- go read the log")


# ---- trajectories: the team's own record of who did what, and at what cost --
#
# T-311. One append-only JSONL per board, alongside messages.jsonl, written by
# the writers that already know a fact rather than reconstructed later by a
# reader guessing from timestamps.
#
# The product objective is TASK COMPLETION IN THE FEWEST TURNS, so this file
# exists to make "turns" measurable: how many watch runs a ticket cost, how
# many updates and messages an agent needed, how often work came back. Nothing
# here is allowed to answer that with a guess -- a field the writer does not
# actually know is OMITTED, never defaulted. A reader can tell "0 tokens" from
# "this harness never told us", and only the first is a number.
#
# PRIVACY: ids, counts, outcomes and timings only. No prompt text, no tool
# input or output, no note or message bodies -- their LENGTH is recorded
# (notes_len, text_len) and nothing else. `msg` events carry from/to/re only.

TRAJ_VERSION = 1
TRAJ_MAX_BYTES = int(os.environ.get("TICKETS_TRAJECTORIES_MAX_BYTES", 50 * 1024 * 1024))
TRAJ_KINDS = ("run_start", "run_end", "claim", "update", "review", "done",
              "reopen", "block", "msg", "merge", "shadow_decision")


def trajectories_path(board):
    return os.path.join(board, "trajectories.jsonl")


def _rotate_trajectories_if_big(board):
    """Same swap-under-a-lock as messages.jsonl, and the same honest caveat:
    a writer already inside an append during the swap can land its line in the
    archive. Archives are read back by every reader that matters here
    (`trajectories`, `export`, `backfill`), so a line in the archive is not a
    lost line -- unlike the live-file-only fast path on the message side.
    """
    path = trajectories_path(board)
    try:
        if os.path.getsize(path) <= TRAJ_MAX_BYTES:
            return
    except OSError:
        return
    lock_path = path + ".rotate.lock"
    try:
        os.close(os.open(lock_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY))
    except FileExistsError:
        return
    try:
        if os.path.getsize(path) <= TRAJ_MAX_BYTES:
            return
        archive = os.path.join(board, "trajectories.%s.jsonl" % now()[:10])
        empty_tmp = path + ".rotate.tmp"
        open(empty_tmp, "wb").close()
        if os.path.exists(archive):
            with open(path, "rb") as src:
                data = src.read()
            fd = os.open(archive, os.O_CREAT | os.O_WRONLY | os.O_APPEND, 0o644)
            try:
                os.write(fd, data)
            finally:
                os.close(fd)
            os.replace(empty_tmp, path)
        else:
            os.replace(path, archive)
            os.replace(empty_tmp, path)
    except OSError:
        pass
    finally:
        try:
            os.unlink(lock_path)
        except OSError:
            pass


def _objective_id(board):
    """A stable id for the standing objective.

    `tickets objective` stores no id, and adding one would rewrite a file
    other seats read, so the id is DERIVED: a hash of the objective's text and
    set-time. Same objective -> same id across every agent and every process;
    editing the objective starts a new id, which is the behaviour a reader
    grouping trajectories by objective actually wants.
    """
    obj = load_objective(board)
    text = obj.get("text") if isinstance(obj, dict) else None
    if not text:
        return ""
    import hashlib
    h = hashlib.sha1(("%s|%s" % (text, obj.get("at", ""))).encode("utf-8", "replace"))
    return "obj-" + h.hexdigest()[:8]


def _agent_harness(board, owner):
    """(harness, model, effort) for an agent, from what it registered with
    `tickets join`. Unregistered -> empty strings, and the caller omits the
    fields rather than writing a plausible-looking default."""
    entry = {}
    try:
        entry = load_workforce(board).get(owner) or {}
    except (IOError, ValueError, OSError):
        entry = {}
    return (entry.get("harness") or entry.get("tool", "") or "",
            entry.get("model", "") or "",
            entry.get("effort", "") or "")


def traj_write(board, rec):
    """Append one event. Best-effort by design: the trajectory log is
    instrumentation, and instrumentation that can fail a `tickets done` is
    worse than a missing line. Every writer here is on a command's success
    path, so a raised exception would abort work that already happened."""
    try:
        _rotate_trajectories_if_big(board)
        line_ = json.dumps(rec) + "\n"
        fd = os.open(trajectories_path(board), os.O_CREAT | os.O_WRONLY | os.O_APPEND, 0o644)
        try:
            os.write(fd, line_.encode())
        finally:
            os.close(fd)
    except (OSError, ValueError, TypeError):
        return None
    return rec


def traj_event(board, kind, agent="", ticket=None, **fields):
    """Build and append one trajectory event.

    `ticket` may be a ticket dict (epic/sprint/state are read off it) or an id
    string. Optional fields are dropped when empty so a reader can distinguish
    "not known" from a real zero; `exit` and the token counts are the
    exception -- 0 is meaningful there, so only None is dropped.
    """
    if kind not in TRAJ_KINDS:
        return None
    rec = {"v": TRAJ_VERSION, "at": now(), "kind": kind}
    tid = ""
    if isinstance(ticket, dict):
        tid = ticket.get("id", "")
        for src, dst in (("epic", "epic"), ("sprint", "sprint")):
            if ticket.get(src):
                rec[dst] = ticket[src]
        # NOT the ticket's pinned branch/sha: `branch`/`sha` on an event mean
        # "the tree this event was recorded from", and a review pin can name a
        # different repo entirely (T-272). Only the repo id, which is
        # unambiguous, is taken from the ticket, and callers that know the
        # deliverable tree pass worktree/branch/sha explicitly.
        if ticket.get("repo"):
            rec.setdefault("repo", ticket["repo"])
    elif ticket:
        tid = str(ticket)
    if tid:
        rec["ticket"] = tid
    if agent:
        rec["agent"] = agent
        harness, model, effort = _agent_harness(board, agent)
        if harness:
            rec["harness"] = harness
        if model:
            rec["model"] = model
        if effort:
            rec["effort"] = effort
    oid = _safe(lambda: _objective_id(board), "")
    if oid:
        rec["objective_id"] = oid
    for k, v in fields.items():
        if v is None:
            continue
        if v == "" or v == [] or v == {}:
            continue
        rec[k] = v
    # T-425/T-481: stamp the watch run so writes attribute to THAT run_id
    # (or agent+run_no on Cursor-harness rows). Omitted outside a watch child.
    if "run_id" not in rec:
        rid = (os.environ.get("TICKETS_RUN_ID") or "").strip()
        if rid:
            rec["run_id"] = rid
    if "run_no" not in rec:
        raw_no = (os.environ.get("TICKETS_RUN_NO") or "").strip()
        if raw_no:
            try:
                rec["run_no"] = int(raw_no)
            except ValueError:
                pass
    return traj_write(board, rec)


_BOUND_WRITE_KINDS = ("claim", "update", "review", "done", "block", "reopen")


def _run_had_bound_write(board, run_id, ticket, agent=None, run_no=None):
    """True when THIS run wrote a bound-ticket event (no idle: grep).

    Pairing key is run_id if present, else (agent, run_no) for Cursor-harness
    rows that carry run_no only.
    """
    if not ticket:
        return False
    if not run_id and (not agent or run_no is None):
        return False
    for e in _safe(lambda: load_trajectories(board), []) or []:
        if e.get("ticket") != ticket:
            continue
        kind = e.get("kind")
        if kind not in _BOUND_WRITE_KINDS and kind != "msg":
            continue
        if run_id and e.get("run_id") == run_id:
            return True
        if (not e.get("run_id") and agent and run_no is not None
                and e.get("agent") == agent and e.get("run_no") == run_no):
            return True
    return False


def _traj_git(cwd=None):
    """worktree/branch/sha for an event, or {} when git cannot answer.

    git_state() returns None rather than a partly-filled dict on purpose
    (T-259); this keeps that contract instead of inventing "?" placeholders
    that would read back as real branches.
    """
    g = _safe(lambda: git_state(cwd=cwd), None)
    if not g:
        return {}
    return {"worktree": g.get("top", ""), "branch": g.get("branch", ""),
            "sha": g.get("sha", "")}


def load_trajectories(board, include_archives=True):
    """Read events oldest-first. Unlike load_messages, archives are ON by
    default: every reader of this file is analytical (a metric, an export, a
    backfill dedup check) and a silently truncated history would corrupt the
    answer rather than merely delay a message."""
    paths = [trajectories_path(board)]
    if include_archives:
        paths = sorted(glob.glob(os.path.join(board, "trajectories.*.jsonl"))) + paths
    out = []
    for p in paths:
        try:
            with open(p) as f:
                for ln in f:
                    ln = ln.strip()
                    if not ln:
                        continue
                    try:
                        rec = json.loads(ln)
                    except ValueError:
                        continue
                    if isinstance(rec, dict):
                        out.append(rec)
        except IOError:
            pass
    return out


# ---- harness usage: parse it when the harness reports it, never estimate --

class HarnessUsageError(Exception):
    """The harness reported usage that could not be read.

    Deliberately NOT the same outcome as "reported nothing". An absent count
    means UNMEASURED, which the cost-learning layer is entitled to treat as
    unknown; but a parser that silently swallowed a malformed blob would file
    a real, billed run as unmeasured forever, and nothing downstream could
    tell that from a genuinely quiet harness. T-396 keeps the two apart:
    reported nothing -> {}, reported something unreadable -> this.
    """


def _num(v):
    """A JSON number that is not a bool.

    `True` is an `int` in Python, so without this a `"input_tokens": true`
    would land in the trajectory log as a token count of 1.
    """
    return v if isinstance(v, (int, float)) and not isinstance(v, bool) else None


# claude's names for the four counts -> the board's landed field names. The
# board's names are the ones T-311 shipped and the dashboard and the T-372
# promise surfaces already read; they are not renamed here (T-396 note).
CLAUDE_USAGE_FIELDS = (("input_tokens", "tokens_in"),
                       ("output_tokens", "tokens_out"),
                       ("cache_read_input_tokens", "tokens_cache_read"),
                       ("cache_creation_input_tokens", "tokens_cache_write"))

# codex's names for the same four counts, in `exec --json` and in its rollout
# files. Same meanings, different spellings -- the mapping is the whole reason
# a second parser exists.
CODEX_USAGE_FIELDS = (("input_tokens", "tokens_in"),
                      ("output_tokens", "tokens_out"),
                      ("cached_input_tokens", "tokens_cache_read"),
                      ("cache_write_input_tokens", "tokens_cache_write"))


def _usage_from_claude_result(rec):
    """The final result object of `claude -p --output-format json`.

    This is the only source on the board that carries a cost the harness
    itself computed, so it is the only one that sets cost_source='harness'.
    """
    if "usage" not in rec and "total_cost_usd" not in rec:
        return None
    usage = rec.get("usage")
    # Checked BEFORE the "is this even a usage record" shortcut: a record that
    # carries a `usage` key at all IS reporting usage, so an unreadable one is
    # an error even when no cost accompanies it. Testing for a dict first let
    # exactly that case fall through as "reported nothing".
    if "usage" in rec and not isinstance(usage, dict):
        raise HarnessUsageError(
            "claude result 'usage' is %s, not an object" % type(usage).__name__)
    usage = usage if isinstance(usage, dict) else {}
    out = {}
    for src, dst in CLAUDE_USAGE_FIELDS:
        if src in usage:
            n = _num(usage[src])
            if n is None:
                raise HarnessUsageError(
                    "claude usage.%s is not a number: %r" % (src, usage[src]))
            out[dst] = int(n)
    if "total_cost_usd" in rec:
        n = _num(rec["total_cost_usd"])
        if n is None:
            raise HarnessUsageError(
                "claude total_cost_usd is not a number: %r" % rec["total_cost_usd"])
        out["cost_usd"] = n
        out["cost_source"] = "harness"
    for src, dst in (("num_turns", "turns"), ("duration_ms", "harness_duration_ms")):
        if src in rec:
            n = _num(rec[src])
            if n is None:
                raise HarnessUsageError("claude %s is not a number: %r" % (src, rec[src]))
            out[dst] = int(n)
    return out or None


def _codex_total_usage(info):
    """`total_token_usage` off one codex token_count event.

    It is CUMULATIVE for the session, so callers take the last one seen and
    never sum: summing a cumulative counter multiplies the bill.
    """
    if not isinstance(info, dict):
        raise HarnessUsageError(
            "codex token_count info is %s, not an object" % type(info).__name__)
    tot = info.get("total_token_usage")
    if not isinstance(tot, dict):
        raise HarnessUsageError("codex token_count carries no total_token_usage object")
    out = {}
    for src, dst in CODEX_USAGE_FIELDS:
        if src in tot:
            n = _num(tot[src])
            if n is None:
                raise HarnessUsageError(
                    "codex total_token_usage.%s is not a number: %r" % (src, tot[src]))
            out[dst] = int(n)
    # No cost field: codex reports tokens only. The ticket forbids inventing a
    # price table, and a made-up cost is worse than an absent one because
    # nothing downstream could tell it from a measured one.
    return out or None


def _usage_from_codex_event(rec):
    """One line of `codex exec --json`, or one line of a rollout file."""
    p = rec.get("payload") if isinstance(rec.get("payload"), dict) else rec
    if not isinstance(p, dict) or p.get("type") != "token_count":
        return None
    if p.get("info") is None:
        # A real event that carried no usage -- this is what every
        # usage-limited codex run writes. Reported nothing, not unreadable.
        return None
    return _codex_total_usage(p["info"])


def _json_candidates(text):
    """Every JSON object a run's output might end with, newest first."""
    blobs = []
    stripped = (text or "").strip()
    if not stripped:
        return blobs
    if stripped.startswith("{"):
        blobs.append(stripped)
    lines = stripped.splitlines()
    for i in range(len(lines) - 1, max(-1, len(lines) - 400) - 1, -1):
        ln = lines[i].strip()
        if not ln.startswith("{"):
            continue
        # A complete object on its own line: this is the shape of every event
        # in a `codex exec --json` stream, and of a compact claude result.
        if ln.endswith("}"):
            blobs.append(ln)
        # A pretty-printed object runs to the end of the output, so the tail
        # from this line is the candidate. Both are tried: a stream line at
        # column 0 is complete on its own AND starts a (bad) tail, and only
        # trying the tail would miss every codex event but the last.
        if lines[i].startswith("{"):
            blobs.append("\n".join(lines[i:]))
    return blobs


def parse_harness_usage(text):
    """Pull token/cost/turn counts out of a run's own stdout.

    `claude -p --output-format json` ends a run with one JSON object carrying
    usage, total_cost_usd, num_turns and duration_ms; `codex exec --json`
    streams token_count events. When the operator has NOT asked for either
    format -- which is the case for the default watch commands -- there is
    nothing here to parse and this returns {}; `_session_store_usage` is the
    fallback that reads the harness's own session store instead.

    It never estimates from output length: a made-up token count is worse than
    no token count, because the cost-learning layer this data feeds cannot
    tell the two apart.

    Returns a dict with any of: tokens_in, tokens_out, tokens_cache_read,
    tokens_cache_write, cost_usd, cost_source, turns, harness_duration_ms.
    Raises HarnessUsageError if a blob IS a usage report but cannot be read.
    """
    if not text:
        return {}
    for blob in _json_candidates(text):
        try:
            rec = json.loads(blob)
        except ValueError:
            continue
        if not isinstance(rec, dict):
            continue
        out = _usage_from_claude_result(rec)
        if out:
            return out
        out = _usage_from_codex_event(rec)
        if out:
            return out
    return {}


# ---- harness usage, second source: the harness's own session store --------
#
# The default watch commands (_worker_cmd) are `claude -p ...` and
# `codex exec ...` with no JSON output format, so parse_harness_usage sees
# only prose and every run_end on the live board went out with no tokens at
# all -- measured 09-08: 105 run_end records, 0 with tokens_in. Rather than
# change how the harness is invoked (which would turn the watch log into JSON
# and break the text greps that _looks_limited and the T-230 liveness read
# depend on), the counts are read afterwards from the store the harness
# writes for itself, attributed to this run by its own time window.
#
# Tokens only. Neither store records a cost, so cost_usd stays absent and
# cost_source stays null on this path.

CLAUDE_PROJECTS_DIR = os.path.expanduser("~/.claude/projects")
CODEX_SESSIONS_DIR = os.path.expanduser("~/.codex/sessions")


def _epoch(stamp):
    """An ISO stamp -> epoch seconds, or None. Naive stamps are read as UTC,
    which is what every writer here emits."""
    try:
        dt = datetime.fromisoformat(str(stamp).replace("Z", "+00:00"))
    except (ValueError, TypeError, AttributeError):
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.timestamp()


# A session transcript grows to tens of megabytes over a long day, and this
# runs at the end of EVERY watch run. Only the tail can hold events from the
# run that just finished, so only the tail is read (T-230: mtime-first, no
# full-file scans).
SESSION_TAIL_BYTES = int(os.environ.get("TICKETS_SESSION_TAIL_BYTES", 4 * 1024 * 1024))


def _iter_jsonl(path, cap=None):
    """Every readable JSON object in the tail of a .jsonl.

    A torn line -- the first one when the tail starts mid-record, or the last
    one while the harness is still writing -- is skipped, not fatal.
    """
    cap = SESSION_TAIL_BYTES if cap is None else cap
    try:
        with open(path, "rb") as f:
            size = os.fstat(f.fileno()).st_size
            if size > cap:
                f.seek(size - cap)
                f.readline()  # discard the partial record the seek landed in
            for raw in f:
                ln = raw.decode("utf-8", "replace").strip()
                if not ln:
                    continue
                try:
                    rec = json.loads(ln)
                except ValueError:
                    continue
                if isinstance(rec, dict):
                    yield rec
    except OSError:
        return


def _claude_transcript_dir(cwd):
    """~/.claude/projects/<slug>, where the CLI's slug is the absolute path
    with every non-alphanumeric character replaced by '-'."""
    path = os.path.abspath(cwd or os.getcwd())
    return os.path.join(CLAUDE_PROJECTS_DIR, re.sub(r"[^A-Za-z0-9]", "-", path))


def _session_usage_claude(cwd, t0, t1):
    """Sum the per-message usage Claude Code wrote for this worktree during
    the run window.

    Per-message counts ARE additive (unlike codex's cumulative totals): each
    assistant message reports the usage of its own request.
    """
    tot, seen = {}, False
    for p in glob.glob(os.path.join(_claude_transcript_dir(cwd), "*.jsonl")):
        # mtime first: a transcript last written before this run started
        # cannot hold any of its messages, and this avoids reading megabytes
        # of other sessions on every run.
        try:
            if os.path.getmtime(p) < t0 - 1:
                continue
        except OSError:
            continue
        for rec in _iter_jsonl(p):
            ts = _epoch(rec.get("timestamp"))
            if ts is None or ts < t0 or ts > t1:
                continue
            m = rec.get("message")
            u = m.get("usage") if isinstance(m, dict) else None
            if not isinstance(u, dict):
                continue
            for src, dst in CLAUDE_USAGE_FIELDS:
                n = _num(u.get(src))
                if n is not None:
                    tot[dst] = tot.get(dst, 0) + int(n)
                    seen = True
    return tot if seen else {}


def _rollout_cwd(path):
    """The cwd a codex rollout announces in its session_meta header.

    Read from the HEAD of the file: the header is the first record, and the
    tail-bounded read below would miss it on any long session -- which would
    silently un-attribute exactly the busiest runs.
    """
    try:
        with open(path, "rb") as f:
            for _ in range(5):          # the header is the first record
                raw = f.readline()
                if not raw:
                    break
                try:
                    rec = json.loads(raw.decode("utf-8", "replace"))
                except ValueError:
                    continue
                if not isinstance(rec, dict):
                    continue
                pay = rec.get("payload") if isinstance(rec.get("payload"), dict) else {}
                if isinstance(pay.get("cwd"), str):
                    return os.path.abspath(pay["cwd"])
    except OSError:
        return None
    return None


def _session_usage_codex(cwd, t0, t1):
    """The last cumulative total_token_usage a codex rollout for this worktree
    wrote during the run window.

    Cumulative, so the LAST one in the window is the answer; summing them
    would multiply-count the same tokens once per turn.
    """
    want = os.path.abspath(cwd or os.getcwd())
    best, best_at = None, None
    for p in glob.glob(os.path.join(CODEX_SESSIONS_DIR, "**", "*.jsonl"), recursive=True):
        try:
            if os.path.getmtime(p) < t0 - 1:
                continue
        except OSError:
            continue
        rollout_cwd, last, last_at = _rollout_cwd(p), None, None
        for rec in _iter_jsonl(p):
            pay = rec.get("payload") if isinstance(rec.get("payload"), dict) else {}
            if isinstance(pay.get("cwd"), str):
                rollout_cwd = os.path.abspath(pay["cwd"])
            ts = _epoch(rec.get("timestamp"))
            if ts is None or ts < t0 or ts > t1:
                continue
            u = _usage_from_codex_event(rec)
            if u:
                last, last_at = u, ts
        # A rollout that never named a cwd is not attributed to this agent:
        # two agents run codex on this box and guessing would bill one for the
        # other's tokens.
        if not last or rollout_cwd != want:
            continue
        # If an older session for this same worktree was merely touched inside
        # the window, glob order would decide the bill. The newest matching
        # event wins instead.
        if best_at is None or last_at > best_at:
            best, best_at = last, last_at
    return best or {}


def _session_store_usage(harness, cwd, started_at, ended_at):
    """Token counts for one run, from the harness's own session store.

    `harness` is the name off the watch command line, so a custom or unknown
    harness reads nothing rather than being guessed at.
    """
    t0, t1 = _epoch(started_at), _epoch(ended_at)
    if t0 is None or t1 is None or t1 < t0:
        return {}
    if harness == "claude":
        return _session_usage_claude(cwd, t0, t1)
    if harness == "codex":
        return _session_usage_codex(cwd, t0, t1)
    # cursor: verified 09-08 to report usage nowhere -- `agent -p` emits none
    # in text or json output format, and its transcripts
    # (~/.cursor/projects/*/agent-transcripts/**/*.jsonl) carry only
    # role/message/status/type. Absent, not zero, until it does.
    return {}


def _run_usage(log_path, log_before, harness, cwd, started_at, ended_at):
    """(usage, error) for one finished run: stdout first, session store as the
    fallback. Never raises -- the watch loop must not die over accounting."""
    err = None
    try:
        usage = parse_harness_usage(_read_run_slice(log_path, log_before)) or {}
    except HarnessUsageError as e:
        usage, err = {}, str(e)
    except Exception as e:                                    # noqa: BLE001
        usage, err = {}, "stdout usage parse failed: %s" % e
    if usage:
        return usage, err
    try:
        return _session_store_usage(harness, cwd, started_at, ended_at) or {}, err
    except HarnessUsageError as e:
        return {}, err or str(e)
    except Exception as e:                                    # noqa: BLE001
        return {}, err or ("session store usage read failed: %s" % e)


TRAJ_USAGE_SCAN_BYTES = int(os.environ.get("TICKETS_TRAJECTORIES_SCAN_BYTES", 256 * 1024))


def _read_run_slice(log_path, offset, cap=None):
    """The tail of ONE run's own output from the shared watch log.

    `offset` is the log size taken just before the run started. If the file
    shrank (the between-run rotation in cmd_watch's log(), or an operator
    truncating it) the offset no longer means anything, so this returns "" --
    reading from 0 would hand the parser a previous run's result object and
    attribute its tokens here.
    """
    cap = cap or TRAJ_USAGE_SCAN_BYTES
    try:
        size = os.path.getsize(log_path)
        if size < offset:
            return ""
        with open(log_path, "rb") as f:
            start = max(offset, size - cap)
            f.seek(start)
            return f.read().decode("utf-8", "replace")
    except OSError:
        return ""


def _harness_of_cmd(cmd):
    """Best-effort harness name from the watch command line."""
    head = os.path.basename(shlex.split(cmd or "")[0]) if (cmd or "").strip() else ""
    known = {
        "claude": "claude", "codex": "codex", "cursor": "cursor", "agent": "cursor",
        "agy": "agy", "antigravity": "agy", "devin": "devin", "cognition": "devin",
        "gemini": "gemini", "grok": "grok",
    }
    if head in known:
        return known[head]
    return "custom" if head else ""


def _round3(x):
    """timing() reports HOURS as floats; keep the unit in the field name and
    the precision short enough to diff."""
    # + 0.0 folds the -0.0 that float subtraction produces for a same-second
    # span; a negative zero in the log reads like a clock error.
    return None if x is None else round(float(x), 3) + 0.0


def _iso_span_secs(a, b):
    try:
        ta = datetime.fromisoformat(str(a).replace("Z", "+00:00"))
        tb = datetime.fromisoformat(str(b).replace("Z", "+00:00"))
        return round((tb - ta).total_seconds(), 3)
    except (ValueError, TypeError, AttributeError):
        return None


# ---- message board ------------------------------------------------------

def messages_path(board):
    return os.path.join(board, "messages.jsonl")


def _rotate_messages_if_big(board):
    """Move an overflowing live messages.jsonl to a dated archive before the
    next append. Best-effort: a lock file makes concurrent rotators no-op
    instead of double-rotating, but a poster mid-append during the swap can
    still land its message in the archived file -- acceptable for this
    tool's scale (a handful of agents), and far better than unbounded growth.
    """
    path = messages_path(board)
    try:
        if os.path.getsize(path) <= MESSAGES_MAX_BYTES:
            return
    except OSError:
        return
    lock_path = path + ".rotate.lock"
    try:
        os.close(os.open(lock_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY))
    except FileExistsError:
        return
    try:
        if os.path.getsize(path) <= MESSAGES_MAX_BYTES:
            return
        archive = os.path.join(board, "messages.%s.jsonl" % now()[:10])
        empty_tmp = path + ".rotate.tmp"
        open(empty_tmp, "wb").close()
        if os.path.exists(archive):
            # already rotated once today: fold the live file onto it instead
            # of clobbering an existing archive.
            with open(path, "rb") as src:
                data = src.read()
            fd = os.open(archive, os.O_CREAT | os.O_WRONLY | os.O_APPEND, 0o644)
            try:
                os.write(fd, data)
            finally:
                os.close(fd)
            os.replace(empty_tmp, path)
        else:
            os.replace(path, archive)
            os.replace(empty_tmp, path)
    except OSError:
        pass
    finally:
        try:
            os.unlink(lock_path)
        except OSError:
            pass


# @cursor, @cursor-2, @claude-fable, @everyone -- not emails, not @@foo.
# Inline so the installed single-file copy stays self-contained (T-221).
_MENTION_RE = re.compile(r"(?<![A-Za-z0-9_@])@([A-Za-z][A-Za-z0-9_-]{0,47})")
_MENTION_BROADCAST = frozenset({"everyone", "all"})
_CODE_SPAN_RE = re.compile(r"```.*?```|`[^`]*`", re.DOTALL)


def _strip_code_spans(text):
    return _CODE_SPAN_RE.sub(" ", text)


def parse_mentions(text):
    """Unique @handles in first-seen order (no @). Skip `code` / ```fences```."""
    out, seen = [], set()
    for match in _MENTION_RE.finditer(_strip_code_spans(text or "")):
        handle = match.group(1)
        key = handle.lower()
        if key in seen:
            continue
        seen.add(key)
        out.append(handle)
    return out


def _registered_handles(board):
    """Lowercase names from workforce.json and agents/*.json, plus broadcast words."""
    names = set(_MENTION_BROADCAST)
    for key in load_workforce(board):
        names.add(str(key).lower())
    for rec in load_agents(board):
        if not isinstance(rec, dict):
            continue
        owner = rec.get("owner") or ""
        if owner:
            names.add(owner.lower())
    return names


def _split_to_tokens(to):
    """Comma-separated --to into first-seen tokens (whitespace stripped)."""
    out, seen = [], set()
    for part in str(to or "").split(","):
        tok = part.strip()
        if not tok:
            continue
        key = tok.lower()
        if key in seen:
            continue
        seen.add(key)
        out.append(tok)
    return out


def _to_recipient_set(to):
    return {t.lower() for t in _split_to_tokens(to)}


def resolve_to_and_mentions(text, to="", registered=None, master_owner=""):
    """Empty --to + exactly one *registered* named mention becomes that recipient.

    Unknown handles stay in `mentions` (history unchanged) but do not become
    implicit --to. Returns
    (to, mentions, unknown_implicit_or_empty, explicit_unknown, dropped_mentions).

    Explicit --to is stored (spawn/probe) even when the name is unregistered;
    comma lists become a recipient set. `--to master` aliases to master.json
    owner when that name is set. Unknown named mentions are filtered out before
    the one-registered-mention rule (T-497).
    """
    mentions = parse_mentions(text)
    to = (to or "").strip()
    empty = []
    if to:
        tokens = _split_to_tokens(to)
        holder = (master_owner or "").strip()
        if holder:
            tokens = [holder if t.lower() == "master" else t for t in tokens]
            tokens = _split_to_tokens(",".join(tokens))
        stored = ",".join(tokens)
        unknown_explicit = []
        if registered is not None:
            for tok in tokens:
                if tok.lower() not in registered:
                    unknown_explicit.append(tok)
        return stored, mentions, "", unknown_explicit, empty
    named = [h for h in mentions if h.lower() not in _MENTION_BROADCAST]
    if registered is None:
        if len(named) == 1 and len(named) == len(mentions):
            return named[0], mentions, "", empty, empty
        return to, mentions, "", empty, empty
    known = [h for h in named if h.lower() in registered]
    unknown_named = [h for h in named if h.lower() not in registered]
    if len(named) != len(mentions):
        return to, mentions, "", empty, empty
    if len(known) == 1:
        return known[0], mentions, "", empty, unknown_named
    if len(known) == 0 and len(named) == 1:
        return "", mentions, named[0], empty, empty
    return to, mentions, "", empty, empty


def post_message(board, sender, text, to="", re="", kind="", task=False, source=""):
    _rotate_messages_if_big(board)
    holder = ((current_master(board) or {}) or {}).get("owner") or ""
    to, mentions, unknown, explicit_unknown, dropped = resolve_to_and_mentions(
        text, to, _registered_handles(board), master_owner=holder)
    rec = {"id": "msg_" + uuid.uuid4().hex, "at": now(), "from": sender,
           "to": to, "re": re, "text": text}
    if mentions:
        rec["mentions"] = mentions
    kind = (kind or "").strip()
    if task or kind == "task":
        rec["kind"] = "task"
    elif kind and kind != "message":
        rec["kind"] = kind
    if source:
        rec["source"] = source
    line_ = json.dumps(rec) + "\n"
    # O_APPEND writes under PIPE_BUF are atomic, so concurrent posters never interleave
    fd = os.open(messages_path(board), os.O_CREAT | os.O_WRONLY | os.O_APPEND, 0o644)
    try:
        os.write(fd, line_.encode())
    finally:
        os.close(fd)
    # ids and a length, never the body (T-311 privacy rule). Best-effort: a
    # message that reached the board must not be undone by instrumentation.
    _safe(lambda: traj_event(board, "msg", agent=sender, ticket=re or None,
                             to=to or "", text_len=len(text or "")), None)
    if unknown:
        rec["_unregistered_implicit"] = unknown
    if explicit_unknown:
        rec["_unregistered_explicit"] = explicit_unknown
    if dropped:
        rec["_unregistered_dropped"] = dropped
    return rec


def load_messages(board, include_archives=False):
    """By default reads only the live messages.jsonl (cheap, since the UI and
    every watch poll re-read this every few seconds). Pass include_archives=True
    to also read rotated messages.<date>.jsonl archives, oldest first, for
    full history.
    """
    paths = [messages_path(board)]
    if include_archives:
        paths = sorted(glob.glob(os.path.join(board, "messages.*.jsonl"))) + paths
    out = []
    for p in paths:
        try:
            with open(p) as f:
                for ln in f:
                    ln = ln.strip()
                    if not ln:
                        continue
                    try:
                        out.append(json.loads(ln))
                    except ValueError:
                        continue
        except IOError:
            pass
    return out


def _agent_set(board, owner, **fields):
    """Update fields on an agent record without touching the rest of it."""
    if not _agent_rec(board, owner):  # bootstrap outside the lock: checkin takes it too
        checkin(board, owner)
    if "auth_check" in fields:
        from auth_v2_contract import redact_auth_check
        fields = dict(fields)
        fields["auth_check"] = redact_auth_check(fields.get("auth_check") or {})
    return _agent_update(board, owner, lambda rec: rec.update(fields))


def _agent_rec(board, owner):
    path = os.path.join(agents_dir(board), owner + ".json")
    try:
        with open(path) as f:
            return json.load(f)
    except (IOError, ValueError):
        return {}


def _msg_id(m):
    """A stable identity for a message record.

    New records carry an id so at-least-once replay can be deduplicated. Legacy
    records keep their content-derived identity and multiset behavior; no old
    board needs a migration and lossless legacy import stays unchanged.
    """
    if m.get("id"):
        return str(m["id"])
    raw = json.dumps([m.get("at", ""), m.get("from", ""), m.get("to", ""),
                      m.get("re", ""), m.get("text", "")], sort_keys=True)
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:16]


def _addressed_to(msg, owner, registered=None):
    """True if *owner* should see *msg*: broadcast, explicit --to, or @mention.

    Unregistered mention handles are kept on the record for history (T-490)
    but do not address a mailbox. A body whose only mention is an unknown
    handle is therefore a broadcast once implicit --to is refused.
    Comma-separated --to is a recipient set (T-497).
    """
    recipients = _to_recipient_set(msg.get("to") or "")
    mentions = {h.lower() for h in (msg.get("mentions") or [])}
    if registered is not None:
        mentions = {h for h in mentions if h in registered}
    target = owner.lower()
    if not recipients and not mentions:
        return True
    if recipients & _MENTION_BROADCAST or mentions & _MENTION_BROADCAST:
        return True
    return target in recipients or target in mentions


def is_board_broadcast(msg):
    """Channel-wide mail: a bystander would see it (empty --to, @everyone/@all).

    Advitiya PRIORITY agent chats: directed `--to` / named @mentions are seat
    threads, not this board channel. Same messages.jsonl either way.
    """
    recipients = _to_recipient_set(msg.get("to") or "")
    mentions = {h.lower() for h in (msg.get("mentions") or [])}
    if recipients & _MENTION_BROADCAST or mentions & _MENTION_BROADCAST:
        return True
    return not recipients and not mentions


def message_involves_seat(msg, seat):
    """True if *msg* belongs in the agent-scoped (1:1) thread for *seat*.

    Extends `tickets msg` / inbox — no second chat store, no shared-memory
    brain. A seat's own board broadcasts stay on the Board thread.
    """
    target = (seat or "").strip().lower()
    if not target:
        return False
    recipients = _to_recipient_set(msg.get("to") or "")
    frm = (msg.get("from") or "").strip().lower()
    mentions = {h.lower() for h in (msg.get("mentions") or [])}
    if target in recipients or target in mentions:
        return True
    return frm == target and not is_board_broadcast(msg)


def filter_messages_for_scope(msgs, seat=""):
    """seat='' → Board (everyone) thread; seat=name → that BYOA seat's thread."""
    if not (seat or "").strip():
        return [m for m in msgs if is_board_broadcast(m)]
    return [m for m in msgs if message_involves_seat(m, seat)]


def seat_thread_summaries(messages, seat_names):
    """Rail summaries from the existing message log. Counts are for this window."""
    board_msgs = filter_messages_for_scope(messages, "")
    last_b = board_msgs[-1] if board_msgs else {}
    seats = []
    seen = set()
    for name in seat_names or []:
        name = (name or "").strip()
        key = name.lower()
        if not name or key in seen:
            continue
        seen.add(key)
        scoped = filter_messages_for_scope(messages, name)
        last = scoped[-1] if scoped else {}
        seats.append({
            "name": name,
            "kind": "seat",
            "n": len(scoped),
            "last_at": last.get("at", ""),
            "preview": (last.get("text") or "")[:80],
        })
    return {
        "board": {
            "kind": "board",
            "label": "Board",
            "n": len(board_msgs),
            "last_at": last_b.get("at", ""),
            "preview": (last_b.get("text") or "")[:80],
        },
        "seats": seats,
    }


def board_snapshot_for_request(board, seat="", messages=40):
    """board_snapshot plus optional ?seat= filter. Same store, scoped view."""
    snap = _snapshot_single_flight(board, messages=messages)
    seat = (seat or "").strip()
    if not seat:
        return snap
    scoped = dict(snap)
    scoped["messages"] = [m for m in snap["messages"] if message_involves_seat(m, seat)]
    scoped["seat"] = seat
    return scoped


def _visible_after_join(msgs, owner, joined):
    """Hide BROADCAST history from before this agent existed -- never directed mail.

    A brand-new agent has no inbox_seen, so every message ever posted comes back
    unread (measured: 1392 broadcasts on a real seat's first wake). It then
    spends its first turn reading other people's mail, which is a direct hit on
    the fewest-turns objective.

    The tempting fix -- stamp inbox_seen = now() at join -- is wrong in two ways
    this board would feel, and both are silent mail loss:
      * a brief posted with `--to <name>` BEFORE the seat joins is destroyed,
        and posting the brief first is exactly how seats get briefed here;
      * `tickets spawn` calls cmd_join, so RESPAWNING an existing seat would
        re-stamp its watermark and wipe everything pending -- including the
        master's answer to that agent's own `stuck:` message.
    So scope the suppression to what the ticket actually names, "mail addressed
    to nobody": drop only broadcasts strictly before `joined`. Same-second
    posts after join (the wakeup suite does not sleep) must still count.
    Anything addressed to this agent by name is delivered no matter how old
    it is. Nothing is deleted -- `tickets inbox --all` still shows history.
    """
    if not joined:
        return msgs  # every pre-existing agent: unchanged, by construction
    target = (owner or "").lower()
    return [m for m in msgs
            if target in _to_recipient_set(m.get("to")) or m.get("at", "") >= joined]


def _seen_counts(seen_ids):
    counts = {}
    for k in seen_ids or []:
        counts[k] = counts.get(k, 0) + 1
    return counts


def _addressed(m, owner, registered=None):
    """Would this message ever be shown to `owner`? (Own mail is never echoed.)

    Addressing itself is main's `_addressed_to` (T-221 @mentions, T-327), not a
    second copy of the rule: this function only adds "never echo an agent its
    own mail", which is the one part the inbox owns.
    """
    return m.get("from") != owner and _addressed_to(m, owner, registered)


def _seen_since(rec):
    """The agent's watermark, never read as being AHEAD of the present.

    A stored `inbox_seen` in the future is not evidence that anything was
    observed -- it is a clock, not a receipt -- and reading it verbatim makes
    every message posted before wall-clock catches up older than the watermark,
    and so invisible, silently, for the whole length of the skew.

    The clamp lives on the READ side as well as the write side on purpose: a
    record poisoned before this fix shipped would otherwise stay blind until
    its skew expired, and a preventive-only clamp cannot heal it. Clamping down
    can re-deliver a message already shown; that is the safe direction, and the
    identity set below keeps it to at most one repeat.
    """
    since = rec.get("inbox_seen", "")
    ceiling = now()
    return ceiling if since > ceiling else since


def _is_unread(m, since, remaining):
    """Is `m` new to an agent whose watermark is `since`?

    Two whole-second stamps cannot order events inside one second, so a strict
    `at > since` silently and permanently drops everything posted during the
    very second an agent read its inbox -- the mail stays on disk while the
    agent is told its inbox is empty. The obvious repair, `>=`, redelivers on
    every poll forever (a wake storm on a board whose watch loops wake on
    unread mail), so the ambiguous region is disambiguated by IDENTITY instead:
    anything at or ahead of the watermark is unread unless this agent has
    already been shown that specific message.

    "At or ahead" rather than "exactly at" is what makes a future-stamped
    record safe. The watermark is clamped to the present, so a message stamped
    ahead of now() sits above the watermark for the length of the skew; it is
    delivered once, its identity is retained while it stays above the
    watermark, and it is not redelivered on the next poll.

    Consumes from `remaining` (a multiset of identities already delivered) so
    that two identical messages in the same second are delivered twice, not
    once.
    """
    at = m.get("at", "")
    if not at or at < since:
        return False
    k = _msg_id(m)
    if remaining.get(k):
        # Explicit IDs are delivery identities: every replay of the same
        # record is already seen. Legacy content hashes remain a multiset so
        # two old, byte-identical sends are still delivered twice.
        if not m.get("id"):
            remaining[k] -= 1
        return False
    return True


def _inbox_scan(board, owner):
    """The read side of the inbox: (unread, watermark, retained_ids).

    The watermark returned is the newest `at` actually observed, clamped to the
    present, and never wall-clock now() on its own. Both halves are load-bearing
    and they fail in opposite directions:

      * Stamping now() reopens the same-second hole one second later -- a
        message posted between the read and the stamp is older than the stamp
        and newer than anything delivered, so it is skipped forever.
      * Letting max(at) run free trusts a timestamp that was never observed.
        One future-stamped record drags the watermark past the present and
        every message posted after it is invisible until the clock catches up.
        Note that the max runs over the whole file, BEFORE the addressing
        filter, so a skewed DM between two other agents blinds a bystander --
        one bad record blinds every agent that reads its inbox after it.

    So the watermark only ever advances to something this agent has actually
    seen, and never past the present.
    """
    rec = _agent_rec(board, owner)
    # since="" means "live file only" below, which would silently drop any
    # already-rotated mail -- checkin() (T-244) stamps inbox_seen at an
    # agent's first-ever check-in specifically so real agents never reach
    # this function with since="". It stays possible here (e.g. a record
    # written before that fix existed) rather than being asserted against.
    since = _seen_since(rec)
    # T-327's pre-join broadcast suppression is applied INSIDE this scan rather
    # than as a filter over unread()'s result, so that the retained-identity set
    # below is drawn from the same list the agent is actually shown. If the two
    # disagreed, a broadcast hidden as pre-join could still occupy the watermark
    # second and be recorded as delivered -- or, worse, not be.
    joined = rec.get("joined_at", "")
    seen_ids = rec.get("inbox_seen_ids") or []
    msgs = load_messages(board)
    # An agent that slept through a rotation has its unread mail sitting in an
    # archive the fast path never reads, so its inbox would come back silently
    # empty -- the one failure this whole board is built to prevent. Pay for
    # the archives only when `since` predates what is left in the live file.
    # since="" is an agent that has never read its inbox. That is the agent
    # with the MOST to catch up on, not the least, so it reads the archives
    # too -- the original `if since and ...` skipped them for exactly that
    # agent, which is T-244's defect stated in T-244's own words.
    if (not since) or (not msgs) or since < msgs[0].get("at", ""):
        if glob.glob(os.path.join(board, "messages.*.jsonl")):
            msgs = load_messages(board, include_archives=True)
    remaining = _seen_counts(seen_ids)
    registered = _registered_handles(board)
    visible = _visible_after_join(
        [m for m in msgs if _addressed(m, owner, registered)],
        owner, joined)
    out = []
    explicit_ids = set()
    for message in visible:
        if not _is_unread(message, since, remaining):
            continue
        explicit = message.get("id")
        if explicit and explicit in explicit_ids:
            continue
        if explicit:
            explicit_ids.add(explicit)
        out.append(message)
    watermark = max([m.get("at", "") for m in msgs] or [""])
    ceiling = now()
    if watermark > ceiling:
        watermark = ceiling    # a future stamp is not something anyone observed
    if watermark < since:
        watermark = since      # nothing newer than the agent already knew
    if not watermark:
        # A board with no messages at all and no prior watermark: there is
        # nothing that could be lost by starting from the current second.
        watermark = now()
    # Identities are needed only where the timestamp alone cannot settle the
    # question -- at the watermark second, and above it while a future-stamped
    # record is waiting for the clock. Everything older is settled by `at`, so
    # this list is bounded by one second of traffic plus any skewed records,
    # not by history. It is rebuilt from the file rather than carried forward,
    # because a message delivered on an EARLIER poll is no longer in `out` and
    # would otherwise lose its identity and be redelivered.
    retained = []
    retained_explicit = set()
    for message in visible:
        if message.get("at", "") < watermark:
            continue
        identity = _msg_id(message)
        if message.get("id") and identity in retained_explicit:
            continue
        if message.get("id"):
            retained_explicit.add(identity)
        retained.append(identity)
    return out, watermark, retained


def unread(board, owner):
    return _inbox_scan(board, owner)[0]


def _mark_inbox_read(board, owner, scan=None):
    """Advance the watermark to what was actually observed, inside the lock.

    This function was written against the pre-T-278 world, where every writer
    of an agent record did its own read / json.dump / os.replace. Keeping that
    shape after T-278 landed would have made the inbox the one writer still
    racing outside the flock -- and the field it drops on a lost race is
    inbox_seen itself, which silently re-delivers or silently swallows mail.
    So the whole read-modify-write goes through _agent_update.

    _inbox_scan stays OUTSIDE the critical section deliberately: it reads
    messages.jsonl and possibly a rotated archive, which is exactly the kind
    of slow work _agent_update's docstring says must not hold the lock.
    """
    if not _agent_rec(board, owner):  # bootstrap outside the lock: checkin takes it too
        checkin(board, owner)
    if scan is None:
        scan = _inbox_scan(board, owner)
    watermark, retained = scan[1], scan[2]

    def _apply(rec):
        rec["inbox_seen"] = watermark
        if retained:
            rec["inbox_seen_ids"] = retained
        else:
            rec.pop("inbox_seen_ids", None)

    _agent_update(board, owner, _apply)


def fmt_local(iso):
    """Render a stored UTC ISO timestamp in the reader's LOCAL time.

    Stored values stay UTC; only the display converts. Falls back to the old
    UTC substring on anything unparseable -- a bad timestamp must never break
    `tickets msg` or `inbox`, and a wrong-looking time is a smaller failure
    than a traceback.
    """
    try:
        from datetime import datetime, timezone
        dt = datetime.fromisoformat(str(iso).replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        local = dt.astimezone()
        zone = local.tzname() or local.strftime("%z")
        return ("%s %s" % (local.strftime("%m-%d %H:%M"), zone)).strip()
    except (ValueError, TypeError, AttributeError):
        return str(iso)[5:16].replace("T", " ")


def fmt_msg(m):
    to = (" -> %s" % m["to"]) if m.get("to") and m["to"] != "all" else ""
    re_ = (" [%s]" % m["re"]) if m.get("re") else ""
    return "%s  %s%s%s: %s" % (fmt_local(m.get("at")), m.get("from", "?"), to, re_, m.get("text", ""))


def _message_wakes_seat(board, seat, message):
    """Whether a posted message should attempt a native session wake for seat."""
    obj_state = objective_state(_safe(lambda: load_objective(board), {}))
    return (_message_wakes(message, obj_state)
            or _continuous_message_wakes(board, seat, message))


def _session_adapters():
    here = os.path.dirname(os.path.realpath(__file__))
    if here not in sys.path:
        sys.path.insert(0, here)
    import session_adapters as mod
    return mod


def cmd_msg(a, board):
    sender = whoami(a.owner)
    if a.re:
        load(board, a.re)  # validate the ticket exists
    # Board first, native wake second: the board is the source of truth.
    m = post_message(board, sender, a.text, a.to or "", a.re or "",
                     task=bool(getattr(a, "task", False)))
    unknown = m.pop("_unregistered_implicit", None)
    explicit_unknown = m.pop("_unregistered_explicit", None) or []
    dropped = m.pop("_unregistered_dropped", None) or []
    if unknown:
        print("WARNING: @handle %s is not a registered agent, message broadcast."
              % unknown)
    for handle in explicit_unknown:
        print("WARNING: --to %s is not a registered agent." % handle)
    dest = (m.get("to") or "").strip()
    for handle in dropped:
        print("WARNING: @handle %s is not a registered agent, directed to %s."
              % (handle, dest))
    print("posted: " + fmt_msg(m))
    sa = None
    mid = _msg_id(m)
    for to in _split_to_tokens(m.get("to") or ""):
        if to.lower() in _MENTION_BROADCAST:
            continue
        if not _message_wakes_seat(board, to, m):
            continue
        if _already_autonomous_wake(board, to, mid):
            print("wake: %s -> deduped" % to)
            continue
        harness = _seat_harness(board, to)
        if sa is None:
            sa = _session_adapters()
        label = sa.wake_seat(board, to, sa.wake_payload(fmt_msg, m), harness=harness,
                             message_id=mid)
        poked = False
        if _should_poke_persist(label):
            poked = _poke_persist_watch(board, to)
            if poked:
                label = "watch-poked"
        print("wake: %s -> %s" % (to, label))
        _note_wake_delivery(board, to, label, mid, poked=poked)
        _safe(lambda to=to, label=label: _note_native_wake_result(
            board, to, label, mid), None)


def _seat_harness(board, seat):
    """Workforce harness, then tool. Empty means unknown -- never invent claude."""
    entry = load_workforce(board).get(seat, {}) or {}
    return (entry.get("harness") or entry.get("tool") or "").strip()


def _should_poke_persist(label):
    """Native inject missed or only queued in the host UI; persist-watch remains.

    `queued-offline` / `supervised` is the T-808 case: Codex/Cursor show the
    mail and wait for a keystroke. A live persist watcher is the autonomous
    path. Remote bridge stays its own transport. Rebound leases belong to
    another live session, so they are not stolen here.
    """
    s = str(label or "")
    if not s or s in ("woken", "deduped", "remote bridge required"):
        return False
    if s.startswith("stale (rebound"):
        return False
    return (
        s in ("no live endpoint", "endpoint stale (removed)", "unsupported provider",
              "queued-offline")
        or s.startswith("endpoint stale")
        or s.startswith("supervised")
        or s.startswith("refused")
    )


def _already_autonomous_wake(board, seat, message_id):
    mid = str(message_id or "")
    if not mid:
        return False
    rec = _agent_rec(board, seat) or {}
    wd = rec.get("wake_delivery") or {}
    if wd.get("message_id") == mid and wd.get("label") in _AUTONOMOUS_WAKE_LABELS:
        return True
    return mid in (rec.get("wake_delivery_ids") or [])


def _note_wake_delivery(board, seat, label, message_id, poked=False):
    """Visible last-wake receipt on the agent record (idempotent per message_id)."""
    if not seat:
        return None
    payload = {
        "message_id": str(message_id or ""),
        "label": str(label or ""),
        "poked": bool(poked),
        "at": now(),
    }

    def mutate(rec):
        rec["wake_delivery"] = payload
        mid = payload["message_id"]
        if mid and payload["label"] in _AUTONOMOUS_WAKE_LABELS:
            ids = [x for x in (rec.get("wake_delivery_ids") or []) if x != mid]
            ids.append(mid)
            rec["wake_delivery_ids"] = ids[-32:]

    return _agent_update(board, seat, mutate)


def _native_wake_succeeded(label):
    """Only a live pause-resume inject counts. queue/poll/supervised is not a PASS."""
    return bool(label) and label in ("woken", "deduped")


def _native_injection_failed(label):
    """Only refused/stale native injection is a local adapter failure.

    Remote bridge required, no live endpoint, retained-but-offline Codex, and
    supervised Cursor stay durable queued-offline (T-640).
    """
    s = str(label or "")
    return s.startswith("refused") or s.startswith("stale (rebound")


def _note_native_wake_result(board, seat, label, message_id):
    """Record native wake outcome. Never delete an endpoint by seat name alone."""
    if _native_wake_succeeded(label):
        def clear(rec):
            rec.pop("adapter_failure", None)
        _agent_update(board, seat, clear)
        return
    if not _native_injection_failed(label):
        return
    harness = _seat_harness(board, seat)
    provider = _session_adapters().provider_for_harness(harness) or harness
    _agent_set(board, seat, adapter_failure={
        "state": "failed",
        "trigger": str(message_id or ""),
        "reason": "native wake %s" % label,
        "at": now(),
        "provider": provider,
        "harness": harness,
    })


def _adapter_provider(harness_name):
    """Stable adapter identity for a workforce harness (empty for custom)."""
    name = harness_name or ""
    return _session_adapters().provider_for_harness(name) or name


def _failure_provider(failure):
    """Provider/harness stamped on a failure record; empty means legacy unscoped."""
    failure = failure or {}
    return failure.get("provider") or _adapter_provider(failure.get("harness") or "")


def _local_adapter_failure(rec, harness_name):
    """Native/watcher refusal is provider-local. Remote seats use the bridge failure only."""
    failure = (rec or {}).get("adapter_failure") or {}
    if not failure:
        return {}
    current = _adapter_provider(harness_name)
    scoped = _failure_provider(failure)
    if (harness_name == "remote" or current == "remote") and scoped != "remote":
        return {}
    if scoped and current and scoped != current:
        return {}
    return failure


def _clear_adapter_failure_on_provider_change(board, owner, new_harness,
                                             previous_harness=""):
    """Drop leftover failure when the captured previous provider/harness changes."""
    rec = _agent_rec(board, owner) or {}
    if not rec.get("adapter_failure"):
        return
    old_key = _adapter_provider(previous_harness)
    new_key = _adapter_provider(new_harness)
    scoped = _failure_provider(rec.get("adapter_failure") or {})
    same_provider = bool(old_key) and old_key == new_key
    same_harness = bool(previous_harness) and previous_harness == new_harness
    if (same_provider or same_harness) and (not scoped or scoped == new_key):
        return
    def clear(agent):
        agent.pop("adapter_failure", None)
    _agent_update(board, owner, clear)


def cmd_inbox(a, board):
    owner = whoami(a.owner)
    seat = (getattr(a, "seat", None) or "").strip()
    scan = None
    if seat:
        # Advitiya PRIORITY agent chats: read one seat thread without touching
        # the viewer's inbox watermark — this is a scoped history view.
        msgs = filter_messages_for_scope(
            load_messages(board, include_archives=True), seat)[-a.limit:]
        if not msgs:
            print("no messages in %s's seat thread (tickets msg \"text\" --to %s)" % (seat, seat))
            return
        print("seat thread · %s (%d) — same messages.jsonl as tickets msg --to" % (seat, len(msgs)))
        for m in msgs:
            print(fmt_msg(m))
        return
    if a.all:
        msgs = load_messages(board, include_archives=True)[-a.limit:]
        if not msgs:
            print("no messages yet (tickets msg \"text\" [--to agent] [--re T-001])")
            return
        for m in msgs:
            print(fmt_msg(m))
    else:
        scan = _inbox_scan(board, owner)
        msgs = scan[0]
        if not msgs:
            print("inbox empty for %s (tickets inbox --all for history)" % owner)
        else:
            print("%d unread for %s:" % (len(msgs), owner))
            for m in msgs:
                print("  " + fmt_msg(m))
    if not a.keep:
        # Mark exactly what this read observed. Recomputing here instead
        # would advance the watermark past anything that landed in between.
        _mark_inbox_read(board, owner, scan)


# ---- trajectories: reader, export, backfill ------------------------------

def _traj_filter(events, ticket="", agent="", kind="", since="", until=""):
    out = []
    kinds = [k.strip() for k in (kind or "").split(",") if k.strip()]
    for e in events:
        if ticket and e.get("ticket") != ticket:
            continue
        if agent and e.get("agent") != agent:
            continue
        if kinds and e.get("kind") not in kinds:
            continue
        at = e.get("at", "")
        if since and at < since:
            continue
        if until and at > until:
            continue
        out.append(e)
    return out


def _traj_line(e):
    bits = ["%s %-9s" % (e.get("at", "?"), e.get("kind", "?"))]
    bits.append("%-16s" % (e.get("agent") or "-"))
    bits.append("%-7s" % (e.get("ticket") or "-"))
    extra = []
    for k in ("run_no", "exit", "duration_s", "turns", "tokens_in", "tokens_out",
              "tokens_cache_read", "tokens_cache_write", "cost_usd", "cost_source",
              "usage_error", "outcome", "state_before", "state_after", "trigger",
              "notes_len", "text_len", "to", "pin", "merged_as", "active_hours",
              "wait_hours", "harness", "harness_cmd", "model", "effort",
              "timed_out", "src"):
        if k in e:
            v = e[k]
            extra.append("%s=%s" % (k, ",".join(v) if isinstance(v, list) else v))
    return " ".join(bits) + ("  " + " ".join(extra) if extra else "")


def _prices_mod():
    try:
        from ticket_board import prices as mod
    except ImportError:
        src = os.path.join(os.path.dirname(os.path.realpath(__file__)), "src")
        if src not in sys.path:
            sys.path.insert(0, src)
        from ticket_board import prices as mod
    return mod


def _traj_summary(events):
    """The numbers this log exists for: turns-to-done per ticket."""
    prices = _prices_mod()
    table = prices.load_price_table()
    by_ticket = {}
    for e in events:
        tid = e.get("ticket")
        if not tid:
            continue
        s = by_ticket.setdefault(tid, prices.traj_summary_bucket())
        prices.traj_summary_add_event(s, e, table=table)
    return by_ticket


def cmd_trajectories(a, board):
    """Read, export or backfill the trajectory log."""
    sub = getattr(a, "traj_cmd", "list") or "list"
    if sub == "backfill":
        return _traj_backfill(a, board)
    events = load_trajectories(board)
    sel = _traj_filter(events, ticket=getattr(a, "ticket", "") or "",
                       agent=getattr(a, "agent", "") or "",
                       kind=getattr(a, "kind", "") or "",
                       since=getattr(a, "since", "") or "",
                       until=getattr(a, "until", "") or "")
    if sub == "export":
        out = getattr(a, "out", "") or ""
        if not out:
            sys.exit("export needs --out <file.jsonl>")
        tmp = out + ".tmp"
        with open(tmp, "w") as f:
            for e in sel:
                f.write(json.dumps(e) + "\n")
        os.replace(tmp, out)
        print("exported %d event(s) of %d to %s" % (len(sel), len(events), out))
        return
    limit = int(getattr(a, "limit", 0) or 0)
    shown = sel[-limit:] if limit else sel
    if getattr(a, "json", False):
        print(json.dumps(shown, indent=2))
        return
    if not events:
        print("no trajectory events yet (%s)" % trajectories_path(board))
        print("the log fills as agents claim, update, review and run; "
              "`tickets trajectories backfill` synthesises the history already on the board")
        return
    if not shown:
        print("no events match (%d in the log)" % len(events))
        return
    for e in shown:
        print(_traj_line(e))
    print("")
    print("%d of %d event(s)%s" % (len(shown), len(events),
                                   " (showing the last %d)" % limit if limit and len(sel) > limit else ""))
    if getattr(a, "summary", False):
        print("")
        # cost is appended, never inserted: the existing columns are a
        # positional contract that tests and operators already read.
        print("%-8s %5s %8s %5s %5s %8s %10s  %s" % (
            "ticket", "runs", "turns", "upd", "msgs", "reopens", "cost",
            "agents / outcome"))
        for tid, s in sorted(_traj_summary(sel).items()):
            cost = _prices_mod().traj_summary_cost_cell(s)
            print("%-8s %5d %8s %5d %5d %8d %10s  %s %s" % (
                tid, s["runs"], (s["turns"] or "-"), s["updates"], s["msgs"],
                s["reopens"], cost, ",".join(sorted(s["agents"])) or "-",
                ("-> " + s["outcome"]) if s["outcome"] else ""))
        print("")
        print("runs = watch runs that reached run_end (the board's own turn count).  "
              "turns = turns the harness itself reported, '-' when it reported none "
              "-- an unreported count is never estimated from the run.")


def _traj_backfill(a, board):
    """Synthesise claim/update/review/done events from what the board already
    records, so the 190+ tickets finished before this log existed are usable.

    Two rules keep a re-run honest:

      1. A synthesised event is stamped src=backfill and carries a
         deterministic bf_key, so running backfill twice writes nothing new.
      2. Backfill never writes into the instrumented era. Two separate guards,
         because the two kinds of event fail differently:

           - claim/review/done happen at most once per ticket, so if a LIVE
             writer already recorded one, backfill refuses that kind for that
             ticket outright. The timestamp cannot be trusted to separate
             them: `claimed_at` is rewritten in place on a re-claim, so a
             ticket whose stored claim time predates the log would otherwise
             be counted once by the writer and once by the reader, and a
             turns metric built on it would be silently inflated.
           - updates happen many times per ticket, so they are cut at the
             floor instead: the earliest instant a live writer recorded for
             that ticket. The pre-instrumentation half of a ticket's note
             history is recoverable; the instrumented half is already there.

    What it cannot know, it does not write: a synthesised event has no run_no,
    no exit, no tokens and no cost, because no such record was ever kept.
    """
    ONCE_PER_TICKET = ("claim", "review", "done")
    existing = load_trajectories(board)
    have_keys = set()
    live_floor = {}
    live_kinds = set()
    for e in existing:
        if e.get("bf_key"):
            have_keys.add(e["bf_key"])
        tid = e.get("ticket")
        if tid and e.get("src") != "backfill":
            live_kinds.add((tid, e.get("kind")))
            at = e.get("at", "")
            if at and (tid not in live_floor or at < live_floor[tid]):
                live_floor[tid] = at
    planned = []

    def plan(t, kind, at, agent, **fields):
        if not at:
            return
        if kind in ONCE_PER_TICKET and (t["id"], kind) in live_kinds:
            return
        floor = live_floor.get(t["id"])
        if floor and at >= floor:
            return
        key = "%s:%s:%s" % (t["id"], kind, at)
        if key in have_keys:
            return
        have_keys.add(key)
        planned.append((t, kind, at, agent, fields))

    for t in load_all(board):
        owner = t.get("owner") or ""
        plan(t, "claim", t.get("claimed_at", ""), owner,
             state_before="open", state_after="claimed")
        for n in t.get("notes", []):
            text = str(n.get("text", ""))
            if text.startswith("REVIEW: "):
                continue  # the review stamp itself, not a progress update
            plan(t, "update", n.get("at", ""), n.get("by", "") or owner,
                 notes_len=len(text))
        plan(t, "review", t.get("review_at", ""), owner,
             state_before="claimed", state_after="review", outcome="review",
             pin=t.get("commit", ""))
        if t.get("done_at"):
            plan(t, "done", t["done_at"], owner,
                 state_before="review" if t.get("review_at") else "claimed",
                 state_after="done", outcome="done", pin=t.get("commit", ""))
        # No `block` events are synthesised: the board records a block REASON
        # as a note but never a block timestamp, and `updated` is only "when
        # this file was last written by anything". Dating a block from it
        # would reorder the very sequence a turns metric reads.
    planned.sort(key=lambda x: (x[2], x[0]["id"]))
    if getattr(a, "dry_run", False):
        print("would write %d event(s):" % len(planned))
        for t, kind, at, agent, fields in planned[:40]:
            print("  %s %-7s %-8s %s" % (at, kind, t["id"], agent or "-"))
        if len(planned) > 40:
            print("  ... and %d more" % (len(planned) - 40))
        return
    written = 0
    ran_at = now()
    for t, kind, at, agent, fields in planned:
        # `at` overrides traj_event's wall clock: a synthesised event must
        # carry the time the thing actually happened, or every backfilled
        # ticket collapses onto the minute the backfill ran. `backfilled_at`
        # keeps the wall clock available without pretending it is the event.
        rec = traj_event(board, kind, agent=agent, ticket=t, at=at,
                         src="backfill", backfilled_at=ran_at,
                         bf_key="%s:%s:%s" % (t["id"], kind, at), **fields)
        if rec:
            written += 1
    print("backfill wrote %d event(s) into %s" % (written, trajectories_path(board)))
    if not planned:
        print("nothing to synthesise: every claim/update/review/done on this board "
              "is already in the log")


def _turns_cmd():
    try:
        from ticket_board.turns import cmd_turns as impl
        return impl
    except ImportError:
        src = os.path.join(os.path.dirname(os.path.realpath(__file__)), "src")
        if src not in sys.path:
            sys.path.insert(0, src)
        from ticket_board.turns import cmd_turns as impl
        return impl


def cmd_turns(a, board):
    """T-312: table / --json of watch-run turns per ticket. See docs/turns.md."""
    return _turns_cmd()(
        a, board, load_all, load_workforce,
        lambda b: load_messages(b, include_archives=True))


def _scheduler_cmd():
    try:
        from ticket_board.scheduler import cmd_route_shadow as impl
        return impl
    except ImportError:
        src = os.path.join(os.path.dirname(os.path.realpath(__file__)), "src")
        if src not in sys.path:
            sys.path.insert(0, src)
        from ticket_board.scheduler import cmd_route_shadow as impl
        return impl


# ---- routing: which agent should take which open ticket -----------------

def score_agent(board, name, entry, roles, ticket):
    """Higher is better. None = cannot take it."""
    if ticket.get("needs") and not all(n in entry.get("can", []) for n in ticket["needs"]):
        return None
    my_roles = roles.get(name)
    role = ticket.get("role") or ""
    if my_roles == []:
        return None
    if role and my_roles is not None and role not in my_roles:
        return None
    s = 10.0
    if role and my_roles and role == my_roles[0]:
        s += 3  # primary role
    cost = {"low": 0, "medium": 1, "high": 2}.get(entry.get("cost", "medium"), 1)
    p = ticket.get("priority", 2)
    # priority 1 -> want high cost; priority 3 -> want low cost
    s += 3 - abs((3 - p) - cost)
    if ticket.get("needs"):
        s += 2  # scarce capability, use it where it is required
    task = (ticket.get("title", "") + " " + ticket.get("body", "")).lower()
    for kw in entry.get("best_for", "").lower().replace(",", " ").split():
        if len(kw) > 3 and kw in task:
            s += 0.5
    return s


def cmd_route(a, board):
    """Suggest an owner for every unassigned open ticket, by model, roles,
    capabilities and cost. Writes `suggested`; `tickets next` honours it.
    Agents still pull -- this is a hint, not a lock -- unless --claim.

    `--shadow` (T-315) is print-only plus one `shadow_decision` event per
    ready ticket. `--apply` is unimplemented.
    """
    if getattr(a, "apply", False) or getattr(a, "shadow", False) or getattr(a, "report", False) or getattr(a, "score", False):
        return _scheduler_cmd()(
            a, board, load_all, load_workforce, load_roles, load_agents,
            score_agent, traj_event, DEFAULT_ROLES)
    try:
        from ticket_board.scheduler import (
            DEFAULT_ALIVE_WITHIN_MIN, filter_eligible, format_excluded, _candidate_names)
    except ImportError:
        src = os.path.join(os.path.dirname(os.path.realpath(__file__)), "src")
        if src not in sys.path:
            sys.path.insert(0, src)
        from ticket_board.scheduler import (
            DEFAULT_ALIVE_WITHIN_MIN, filter_eligible, format_excluded, _candidate_names)
    tickets = load_all(board)
    wf = load_workforce(board)
    roles = load_roles(board)
    agents = dict((r["owner"], r) for r in load_agents(board))
    alive_within = int(getattr(a, "alive_within", None) or DEFAULT_ALIVE_WITHIN_MIN)
    done = set(t["id"] for t in tickets if t["status"] == "done")
    load_ = {}
    for t in tickets:
        if t["status"] == "claimed":
            load_[t.get("owner")] = load_.get(t.get("owner"), 0) + 1
    names, excluded = filter_eligible(
        _candidate_names(wf, roles, only=a.only), wf, roles, agents, load_, DEFAULT_ROLES,
        alive_within_min=alive_within)
    print(format_excluded(excluded))
    def _deps_done(t):
        return all(d in done for d in t.get("deps", []))

    def _needs_route(t):
        if t["status"] != "open":
            return False
        if _ticket_on_hold(t):
            return False
        if _ticket_lane(t) != "ready":
            return False
        if a.redo:
            return True
        if _deps_done(t):
            return not t.get("suggested") and not _reserved_agent(t)
        return not _reserved_agent(t)

    ready_first = sorted(
        [t for t in tickets if _needs_route(t)],
        key=lambda t: (0 if _deps_done(t) else 1, t.get("priority", 2), t["id"]))
    print("%-6s %-3s %-44s %-14s %s" % ("ticket", "pri", "title", "suggested", "why"))
    changed = 0
    for t in ready_first:
        best, best_s, why = None, None, ""
        for n in names:
            e = wf.get(n, {})
            s = score_agent(board, n, e, roles, t)
            if s is None:
                continue
            s -= 1.5 * load_.get(n, 0)  # spread work; agents already holding tickets rank lower
            if best_s is None or s > best_s:
                best, best_s = n, s
                why = "%s %s" % (e.get("model") or e.get("tool") or "", e.get("cost", ""))
        if best:
            if _deps_done(t):
                t["suggested"] = best
            else:
                t["reserved_for"] = best
                why = ("reserved  " + why).strip()
            save(board, t)
            changed += 1
            load_[best] = load_.get(best, 0) + 0.5  # soft-count suggestions too
            if a.claim and t["status"] == "open" and _deps_done(t):
                got = try_claim(board, t["id"], best)
                if got:
                    t = got
                    why += "  CLAIMED"
        print("%-6s %-3s %-44s %-14s %s" % (t["id"], t.get("priority", 2), t["title"][:44],
                                          best or "(nobody fits)", why))
    if changed:
        _master_log(board, "route: suggested owners for %d tickets%s" % (changed, " and claimed ready ones" if a.claim else ""))
    print("\nAgents pull with `tickets next`; their suggested tickets come first. "
          "Dep-blocked tickets get reserved_for instead of a note. "
          "`tickets route --claim` hard-assigns the ready ones.")


# ---- onboarding ---------------------------------------------------------

CONNECT = """## Connecting an agent to this board

Paste this at the start of ANY agent session (Claude Code, Codex, Cursor,
Grok, a human shell). Replace the name and roles.

    export TICKET_AGENT=claude-opus          # unique per agent, never reuse
    cd {root}
    tickets join $TICKET_AGENT --roles backend   # registers you, prints the loop
    tickets master                            # read the briefing first
    tickets inbox                             # anything addressed to you
    tickets next                              # claim work; prints handoffs

`join` without `--harness` prints `harness=claude (default)`: a label on the
agent record, not a running process. Claude is not started until `tickets watch`
or `tickets spawn`. Dry BYOA is join then `tickets next` in your own shell;
`--harness custom --cmd '...'` is for when a watcher should run your harness
(docs/byoa.md).

Then the loop, until `tickets next` says nothing is ready:

    # work on your own worktree (join prints the exact command)
    tickets update <id> "what changed, what is next"     # every {every} min
    tickets msg "..." --to <agent> --re <id>             # questions, blockers
    git add -A && git commit -m "..."                    # commit as you go
    tickets done <id> --notes "paths, decisions"         # refuses on main / dirty
    # merge or open a PR, then:
    tickets next

Tool-specific:
- Claude Code: `tickets hooks claude --agent claude-opus` installs a project
  SessionStart/inbox/Stop hook with that identity baked in.
- Codex: reads AGENTS.md in the repo root (installed by `tickets init`);
  `tickets hooks codex --agent codex --worktree "$PWD"` adds scoped context.
- Cursor: reads AGENTS.md and .cursor/rules/tickets.mdc; `tickets hooks cursor
  --agent cursor --worktree "$PWD"` adds identity-pinned message hooks.
- Anything else that can run a shell: the same commands work; `tickets` is one
  stdlib Python file at ~/.claude/tools/tickets.py. `tickets hooks remote
  --agent <name>` creates a pinned wrapper and event-command manifest.

To take coordination: `tickets master take`, then `tickets master` and act on
the HEALTH section.

## Making agents start on their own

A session cannot be woken by a hook once its turn has ended, so use both:

- Keep going while there is work (Claude Code): `tickets hooks claude --agent <name>` installs a
  `Stop` hook that blocks the stop when the agent has unread messages or a
  ticket in hand (loop-guarded: one extra continuation per user turn).
- Start when work appears, without a human: run a watcher per agent from that
  agent's worktree. It polls `tickets pending` and launches a headless run only
  when there is something to do:

      cd {root}/.worktrees/claude-opus
      TICKET_AGENT=claude-opus tickets watch --every 60 --cwd "$PWD" \
          --exec 'claude -p "$(tickets prompt)" --permission-mode acceptEdits'

  Any tool works in `--exec` (codex, cursor-agent, a shell script). Logs go to
  `.tickets/agents/<agent>.watch.log`. `tickets watch --once` is the cron-able
  form (exit 0 = work exists).
- Interactive sessions you keep open: `/loop 10m` with the prompt
  `tickets inbox && tickets mine; continue my ticket or tickets next` re-checks
  the board on a timer.
"""


STABLE_ROLE_ALIASES = ("ceo", "cos")
IDENTITY_BOUND_AGENT_KEYS = (
    "auth_check", "runner_context", "limit", "adapter_failure",
    "auth_resume_at", "harness_check",
)


def aliases_path(board):
    return os.path.join(board, "aliases.json")


def load_aliases(board):
    path = aliases_path(board)
    try:
        with open(path) as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except (IOError, ValueError):
        return {}


def save_aliases(board, aliases):
    os.makedirs(board, exist_ok=True)
    path = aliases_path(board)
    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        json.dump(aliases, f, indent=2)
    os.replace(tmp, path)


def _unbind_aliases_for(board, owner):
    aliases = load_aliases(board)
    kept = dict((k, v) for k, v in aliases.items() if v != owner)
    if kept != aliases:
        save_aliases(board, kept)


def _identity_harness_key(spec):
    harness, _ = _split_harness(spec or "")
    return (harness or "").strip()


def _bound_identity_harness(board, owner):
    entry = load_workforce(board).get(owner) or {}
    return _identity_harness_key(entry.get("harness") or entry.get("tool") or "")


def _identity_reuse_conflict(board, owner, incoming_harness):
    incoming = _identity_harness_key(incoming_harness)
    if not incoming:
        return ""
    prev = _bound_identity_harness(board, owner)
    if not prev or prev == incoming:
        return ""
    return prev


def _identity_reuse_error(owner, prev, incoming):
    incoming = _identity_harness_key(incoming) or incoming
    return (
        "refusing: %s is bound to %s (auth/session/runner). "
        "Join a unique provider-specific agent id and bind it with --alias ceo|cos, "
        "or pass --transfer to audit handover of this name. "
        "Historical aliases: tickets retire <name>."
        % (owner, prev)
    )


def _strip_identity_bound_state(board, owner):
    def clear(rec):
        for key in IDENTITY_BOUND_AGENT_KEYS:
            rec.pop(key, None)
        rec["ticket"] = ""
    if _agent_rec(board, owner):
        _agent_update(board, owner, clear)
    _safe(lambda: _session_adapters().remove_endpoint(board, owner), None)


def _guard_seat_identity(board, owner, incoming_harness, transfer=False, alias=""):
    """Refuse provider reuse, or audited-transfer that drops identity-bound state."""
    alias = (alias or "").strip().lower()
    if alias and alias not in STABLE_ROLE_ALIASES:
        sys.exit("--alias must be one of: %s" % ", ".join(STABLE_ROLE_ALIASES))
    prev = _identity_reuse_conflict(board, owner, incoming_harness)
    alias_holder = load_aliases(board).get(alias, "") if alias else ""
    alias_clash = bool(alias and alias_holder and alias_holder != owner)
    if prev and not transfer:
        sys.exit(_identity_reuse_error(owner, prev, incoming_harness))
    if alias_clash and not transfer:
        sys.exit("refusing: alias %s is bound to %s. Pass --transfer to rebind, "
                 "or join as that unique id." % (alias, alias_holder))
    if prev and transfer:
        if _agent_holds_ticket(board, owner):
            sys.exit("refusing --transfer: %s holds a ticket; reopen or finish it first" % owner)
        _strip_identity_bound_state(board, owner)
        incoming = _identity_harness_key(incoming_harness)
        post_message(
            board, owner,
            "transferred %s %s -> %s (--transfer; auth/session/limit/endpoint/ticket cleared; history kept)"
            % (owner, prev, incoming))
        print("transferred %s %s -> %s (--transfer; identity-bound state cleared, history kept)"
              % (owner, prev, incoming))
    return prev


def _bind_role_alias(board, alias, owner):
    alias = (alias or "").strip().lower()
    if not alias:
        return
    aliases = load_aliases(board)
    aliases[alias] = owner
    save_aliases(board, aliases)


def _join_namespace(a, owner):
    """Every field cmd_join reads, so a new join flag fails here instead of at first-run."""
    return argparse.Namespace(
        name=owner,
        roles=getattr(a, "roles", None),
        can=getattr(a, "can", None),
        cost=getattr(a, "cost", None),
        tool=getattr(a, "tool", "") or "",
        harness=getattr(a, "harness", "") or "",
        cmd_template=getattr(a, "cmd_template", "") or "",
        model=getattr(a, "model", "") or "",
        best_for=getattr(a, "best_for", "") or "",
        wake_mode=getattr(a, "wake_mode", None),
        lifecycle=getattr(a, "lifecycle", None),
        persistent=bool(getattr(a, "persistent", False)),
        knowledge_dir=getattr(a, "knowledge_dir", "") or "",
        transfer=bool(getattr(a, "transfer", False)),
        alias=getattr(a, "alias", "") or "",
    )


def cmd_join(a, board):
    _refuse_join_tickets_dir_shadow(board)
    owner = a.name or whoami()
    if owner.startswith("agent-"):
        sys.exit("give yourself a real name: tickets join <name> --roles ...")
    knowledge_dir = (getattr(a, "knowledge_dir", "") or "").strip()
    if knowledge_dir:
        knowledge_dir = os.path.abspath(os.path.expanduser(knowledge_dir))
        if _knowledge_inside_board(knowledge_dir, board):
            sys.exit("--knowledge-dir must live outside the ticket board: %s" % knowledge_dir)
        if not _knowledge_graph(knowledge_dir):
            sys.exit("--knowledge-dir needs a graph containing manifest.json: %s" % knowledge_dir)
        _, _, knowledge_errors = _knowledge_records(knowledge_dir)
        if knowledge_errors:
            sys.exit("--knowledge-dir graph is invalid; run `tickets knowledge validate`: %s" %
                     knowledge_dir)
    harness, inline_cmd = _split_harness(getattr(a, "harness", "") or a.tool)
    _guard_seat_identity(
        board, owner, harness,
        transfer=bool(getattr(a, "transfer", False)),
        alias=(getattr(a, "alias", "") or "").strip())
    # Read this BEFORE checkin(), which creates the record. Only a genuinely new
    # agent gets a joined_at watermark; a re-join (and `tickets spawn`, which
    # calls straight through here) must leave delivery completely alone.
    first_join = not _agent_rec(board, owner)
    roles_path = os.path.join(board, "roles.json")
    roles = {}
    if os.path.isfile(roles_path):
        try:
            with open(roles_path) as f:
                roles = json.load(f)
        except (IOError, ValueError):
            roles = {}
    if a.roles is not None:
        roles[owner] = [r.strip() for r in a.roles.split(",") if r.strip()]
    elif owner not in roles and owner not in DEFAULT_ROLES:
        roles[owner] = []  # explicit: must pass --role until master assigns
    os.makedirs(board, exist_ok=True)
    tmp = roles_path + ".tmp"
    with open(tmp, "w") as f:
        json.dump(roles, f, indent=2)
    os.replace(tmp, roles_path)
    wf = load_workforce(board)
    entry = wf.get(owner, {})
    # BYOA: --harness is the current spelling, --tool the original one; they are
    # the same field. `custom:<cmd>` folds the command into the harness flag.
    cmd_template = (getattr(a, "cmd_template", "") or "").strip() or inline_cmd
    # A bare executable name still works (it is the command). "custom" with
    # nothing to run is a typo that would otherwise register an agent no
    # watcher can ever start, so it is refused at the point of the mistake.
    if harness == "custom" and not cmd_template:
        sys.exit("--harness custom needs --cmd '<shell template>' "
                 "(placeholders: %s)" % " ".join(HARNESS_PLACEHOLDERS))
    if cmd_template and not harness:
        harness = "custom"
    prev_harness = entry.get("harness") or entry.get("tool") or ""
    if harness:
        entry["harness"] = harness
        entry["tool"] = harness  # back-compat: older records/readers say "tool"
    if cmd_template:
        entry["cmd"] = cmd_template
    elif harness and prev_harness and harness != prev_harness and entry.pop("cmd", None):
        # A command template belongs to the harness it was registered with.
        # Switching harness without a new --cmd must DROP it: keeping it would
        # make `spawn --harness codex` silently launch the old custom runner,
        # which is the exact "it ran something else" failure BYOA is here to
        # make impossible.
        print("dropped the %s command template (harness is now %s)" % (prev_harness, harness))
    if a.model:
        entry["model"] = a.model
    if a.can is not None:
        entry["can"] = sorted(set([c.strip() for c in a.can.split(",") if c.strip()]))
    if a.cost:
        entry["cost"] = a.cost
    if a.best_for:
        entry["best_for"] = a.best_for
    wake_mode = getattr(a, "wake_mode", None)
    if wake_mode is not None:
        if wake_mode not in WAKE_MODES:
            sys.exit("--wake-mode must be one of: %s" % ", ".join(WAKE_MODES))
        entry["wake_mode"] = wake_mode
    lifecycle = getattr(a, "lifecycle", None)
    if getattr(a, "persistent", False):
        if lifecycle == "ephemeral":
            sys.exit("--persistent cannot be combined with --lifecycle ephemeral")
        lifecycle = lifecycle or "persistent"
    if lifecycle is not None:
        if lifecycle not in LIFECYCLES:
            sys.exit("--lifecycle must be one of: %s" % ", ".join(LIFECYCLES))
        entry["lifecycle"] = lifecycle
    entry["agent_id"] = owner
    if harness:
        entry["provider"] = harness
    alias = (getattr(a, "alias", "") or "").strip().lower()
    if alias:
        _bind_role_alias(board, alias, owner)
        entry["role_alias"] = alias
    if knowledge_dir:
        entry["knowledge_dir"] = knowledge_dir
    entry.setdefault("can", [])
    entry.setdefault("cost", "medium")
    wf[owner] = entry
    save_workforce(board, wf)
    if getattr(a, "persistent", False):
        sa = _session_adapters()
        reg = sa.register_persistent(board, owner, harness or entry.get("harness") or "claude", now())
        if reg.get("ok"):
            pid = (reg.get("record") or {}).get("pid")
            mode = reg.get("mode") or (reg.get("record") or {}).get("mode") or "native"
            extra = ("pid %s" % pid if pid else
                     "no session pid published; liveness follows transport")
            lease = reg.get("lease_id") or (reg.get("record") or {}).get("lease_id") or ""
            if lease:
                extra += "; lease %s" % lease
            print("persistent: %s %s endpoint registered for %s (%s)" % (
                mode, reg.get("provider"), owner, extra))
        else:
            print("persistent: %s" % reg.get("reason", "registration failed"))
    rec = checkin(board, owner, None, "joined" + (" (%s)" % harness if harness else ""))
    if harness:
        _safe(lambda: _clear_adapter_failure_on_provider_change(
            board, owner, harness, prev_harness), None)
    if first_join:
        # setdefault, not update: if two joins race, the earlier stamp wins and
        # neither can move the watermark forward over unread mail.
        _agent_update(board, owner, lambda r: r.setdefault("joined_at", now()))
    post_message(board, owner, "joined the board%s; roles=%s; at %s [%s]" % (
        (" via %s" % harness) if harness else "", roles.get(owner, DEFAULT_ROLES.get(owner, [])),
        rec["worktree"] or rec["cwd"], rec["branch"] or "?"))
    root = os.path.dirname(board)
    shown_alias = alias or next(
        (name for name, holder in load_aliases(board).items() if holder == owner), "-")
    print("joined as %s  roles=%s  can=%s  cost=%s  harness=%s  wake=%s  lifecycle=%s  alias=%s" % (
        owner, roles.get(owner, DEFAULT_ROLES.get(owner, "any")), entry["can"] or "-", entry["cost"],
        entry.get("harness") or "claude (default)", wake_mode_of(board, owner, workforce=wf),
        lifecycle_of(board, owner, workforce=wf), shown_alias or "-"))
    if entry.get("cmd"):
        print("cmd: %s" % entry["cmd"])
    if entry.get("knowledge_dir"):
        print("knowledge: %s" % entry["knowledge_dir"])
    elif harness and harness not in BUILTIN_HARNESSES:
        # A typo'd built-in name ("cluade") is indistinguishable from a
        # deliberate bare executable, so say which reading was taken rather
        # than discovering it a poll interval later in the watch log.
        print("note: %r is not a built-in harness, so it is run as the command itself; "
              "`tickets harness check %s` proves it works" % (harness, owner))
    print("board: %s" % board)
    m = current_master(board)
    print("master: %s" % (m["owner"] if m else "nobody -- `tickets master take` if you are it"))
    n = len(unread(board, owner))
    if n:
        print("inbox: %d unread (tickets inbox)" % n)
    warn = worktree_warning(owner)
    print("")
    if warn:
        print(warn)
    else:
        g = git_state()
        if g:
            print("working tree OK: %s @ %s" % (g["branch"], g["top"]))
    print("")
    if _join_is_ceo_path(owner, roles.get(owner, [])):
        print("Atman CEO loop:  tickets inbox  ->  tickets objective  ->  "
              "tickets graph / tickets map  ->  tickets drive  ->  "
              "tickets msg --to cursor (CoS staffs). CEO does not claim worker tickets.")
        print("Full instructions: tickets connect")
    else:
        print("Loop:  tickets master  ->  tickets next  ->  work + commit  ->  "
              "tickets update <id> \"...\" (every %d min)  ->  tickets done <id> --notes \"...\"  "
              "->  merge  ->  tickets next" % UPDATE_EVERY_MIN)
        print("Full instructions: tickets connect --worker")
    if not os.path.exists(os.path.join(root, "AGENTS.md")):
        print("(no AGENTS.md here -- run `tickets init` once so Codex/Cursor see the rules)")
    _safe(lambda: _enroll_runner_context(board, owner), None)


def _agent_holds_ticket(board, owner):
    return any(
        t.get("owner") == owner and t.get("status") in ("claimed", "review", "blocked")
        for t in load_all(board)
    )


def cmd_retire(a, board):
    """Remove a seat from the board (inverse of join). Refused while it holds a ticket."""
    owner = (a.name or whoami()).strip()
    if not owner:
        sys.exit("usage: %s retire <name>" % cli_prog())
    if owner.startswith("agent-"):
        sys.exit("give a real agent name")
    agent_path = os.path.join(agents_dir(board), owner + ".json")
    wf = load_workforce(board)
    roles_path = os.path.join(board, "roles.json")
    roles = {}
    if os.path.isfile(roles_path):
        try:
            with open(roles_path) as f:
                roles = json.load(f)
        except (IOError, ValueError):
            roles = {}
    if not (os.path.isfile(agent_path) or owner in wf or owner in roles):
        sys.exit("no seat %r on this board" % owner)
    if _agent_holds_ticket(board, owner):
        sys.exit("refusing: %s holds a ticket; reopen or finish it first" % owner)
    if os.path.isfile(agent_path):
        os.remove(agent_path)
    if owner in wf:
        del wf[owner]
        save_workforce(board, wf)
    _safe(lambda: _session_adapters().remove_endpoint(board, owner), None)
    if owner in roles:
        del roles[owner]
        os.makedirs(board, exist_ok=True)
        tmp = roles_path + ".tmp"
        with open(tmp, "w") as f:
            json.dump(roles, f, indent=2)
        os.replace(tmp, roles_path)
    _unbind_aliases_for(board, owner)
    retirer = whoami(getattr(a, "owner", None))
    post_message(board, retirer, "retired seat %s from the board" % owner)
    print("retired %s" % owner)


def cmd_connect(a, board):
    worker = bool(getattr(a, "worker", False))
    ceo = bool(getattr(a, "ceo", False))
    seat = (getattr(a, "seat", None) or "ceo").strip() or "ceo"
    living = board_is_living(board)
    if ceo or (living and not worker):
        print_ceo_connect(board, seat=seat)
        return
    print_onboarding_startup()
    print("Then probe integrations: `tickets harness available`")
    print("It auto-checks usage; missing remaining/reset is a FAIL row.")
    print("Ask which to integrate; do not spawn until they answer.")
    print("Announce the board/team name with `tickets msg --to everyone`, then ask")
    print("for the objective and tasks. Turn tasks into a graph with `tickets plan`")
    print("(JSON keys + deps), then `tickets graph` / `tickets map`. Follow up with")
    print("`tickets update` / `here`, reopen silent >90m claims, `tickets drive`.")
    print("")
    print(CONNECT.format(root=os.path.dirname(board), every=UPDATE_EVERY_MIN))


# ---- wake-up: is there work for this agent, and how to start it ----------
#
# Two mechanisms, because a session cannot be woken by a hook once its turn
# has ended: `stop-hook` keeps a Claude Code turn alive while board work
# remains; `watch` starts a headless run when work appears. Both are built on
# `pending_work`, which never raises and never mutates the board.

STOP_HOOK_MAX_PER_HOUR = 4
WATCH_MIN_INTERVAL = 5
# T-509: notice SIGTERM / spawn --stop during the poll wait AND an in-flight
# --exec. PEP 475 retries a single time.sleep after a flag-only handler
# returns, so one sleep(--every) delayed exit by up to --every. Event.wait
# + InterruptedError from the same-thread handler deadlocked the wait and
# still missed the stop-file during wait.
WATCH_STOP_SLICE = 0.2


def _watch_poll_wait(wait, stop, board, owner):
    """Sleep up to `wait` seconds. True = SIGTERM or spawn --stop file.

    KeyboardInterrupt is not caught here (cmd_watch still breaks immediately).
    """
    import time as _time
    deadline = _time.monotonic() + max(0.0, float(wait))
    while not stop["now"]:
        if os.path.exists(_stop_file(board, owner)):
            return True
        left = deadline - _time.monotonic()
        if left <= 0:
            return False
        _time.sleep(min(WATCH_STOP_SLICE, left))
    return True


# Bounds so long-lived boards do not grow files without limit. All overridable
# for tests; defaults are generous enough to never matter in normal use.
WATCH_LOG_MAX_BYTES = int(os.environ.get("TICKETS_WATCH_LOG_MAX_BYTES", 5 * 1024 * 1024))
MESSAGES_MAX_BYTES = int(os.environ.get("TICKETS_MESSAGES_MAX_BYTES", 5 * 1024 * 1024))
MASTER_LOG_MAX_BYTES = int(os.environ.get("TICKETS_MASTER_LOG_MAX_BYTES", 2 * 1024 * 1024))
MASTER_LOG_KEEP_ENTRIES = int(os.environ.get("TICKETS_MASTER_LOG_KEEP_ENTRIES", 200))


def _safe(fn, default):
    try:
        return fn()
    except Exception:  # noqa: BLE001 - wake-up paths must never take a session down
        return default


def pending_work(board, owner):
    """What would make `owner` act right now. Empty dict = nothing.

    Keys: messages_to_me, broadcasts (count), holding, suggested_for_me,
    ready_in_my_lane, limited (agent recorded a usage limit; do not wake).
    For a continuous seat, an ordinary directed message is promoted into the
    existing task_messages wake queue. The source message remains the receipt
    and inbox watermark; no parallel queue can drift from it.
    """
    out = {}
    if not owner or not os.path.isdir(board):
        return out
    rec = _safe(lambda: _agent_rec(board, owner), {}) or {}
    if rec.get("limit"):
        out["limited"] = rec["limit"].get("until") or rec["limit"].get("at") or "yes"
        return out
    obj = _safe(lambda: load_objective(board), {})
    obj_state = objective_state(obj)
    msgs = _safe(lambda: unread(board, owner), [])
    # unread() already applied registered, case-insensitive addressing and
    # suppressed the author's own mail. Anything non-broadcast in that result
    # is therefore a DM or named mention for this seat. This also collapses a
    # message carrying BOTH --to and @mention to one record / one wake.
    direct = [m for m in msgs if not is_board_broadcast(m)]
    if direct:
        out["messages_to_me"] = [_wake_message_summary(m) for m in direct[-WAKE_MESSAGE_LIMIT:]]
        tasks = [_wake_message_summary(m) for m in direct
                 if (_message_wakes(m, obj_state)
                     or _continuous_message_wakes(board, owner, m))]
        if tasks:
            out["task_messages"] = tasks[-WAKE_MESSAGE_LIMIT:]
    elif msgs:
        out["broadcasts"] = len(msgs)
    tickets = _safe(lambda: load_all(board), [])
    held = [t for t in tickets if t.get("status") == "claimed" and t.get("owner") == owner]
    if held:
        out["holding"] = [t["id"] + " " + t.get("title", "")[:60] for t in held]
    roles = _safe(lambda: roles_for(board, owner, None), None)
    ready = _safe(lambda: [t for t in _filter_ready(unblocked(board, tickets), roles)
                           if can_do(board, owner, t)
                           and not _reservation_blocks(t, owner)
                           and not _ticket_on_hold(t)], [])
    mine_first = [t for t in ready if t.get("suggested") == owner or _reserved_agent(t) == owner]
    if mine_first:
        out["suggested_for_me"] = [t["id"] + " " + t.get("title", "")[:60] for t in mine_first[:3]]
    elif ready and not held:
        out["ready_in_my_lane"] = [t["id"] + " " + t.get("title", "")[:60] for t in ready[:3]]
    # the master wakes for different reasons: reviews to merge, stuck agents, health
    m = _safe(lambda: current_master(board), None)
    if m and owner in (m.get("owner"), m.get("cos")):
        rq = [t["id"] for t in tickets if t.get("status") == "review"]
        if rq:
            out["review_queue"] = rq[:6]
        # A stuck message wakes both seats whoever it was addressed to.
        # Same rule as the inbox itself (T-228), clamp included: a "stuck"
        # posted in the second the master last read its mail must still wake
        # it, and a future-stamped record must not blind the master to every
        # "stuck" that follows it. This scan does not consume, so it gets
        # its own counts.
        since = _seen_since(rec)
        _rem = _seen_counts(rec.get("inbox_seen_ids"))
        stuck = [_wake_message_summary(x) for x in _visible_after_join(
                     _safe(lambda: load_messages(board), []), owner,
                     rec.get("joined_at", ""))
                 if x.get("from") != owner and _is_unread(x, since, _rem)
                 and str(x.get("text", "")).lower().startswith(("stuck", "blocked"))]
        if stuck:
            out["stuck_messages"] = stuck[-WAKE_MESSAGE_LIMIT:]
        crit = [i for i in _safe(lambda: health(board, tickets), []) if i[0] == "CRIT"]
        if crit:
            out["health_crit"] = [i[1][:80] for i in crit[:3]]
    # The objective heartbeat: any seat spawned with --heartbeat N (the master,
    # or a standing seat such as an optimizer) is woken every N minutes even
    # when nothing else is pending, so it keeps working its brief toward the
    # objective instead of going quiet. Opt-in per seat; off for plain workers.
    every = int(rec.get("drive_every") or 0)
    # Heartbeat wakes only an active objective that has a measurable exit.
    if (obj and objective_state(obj) == "active" and objective_exit_ok(obj)
            and every > 0):
        last = rec.get("drive_at", "")
        if not last or hours_since(last) * 60 >= every:
            out["drive"] = {"objective": obj.get("text", "")[:100], "last": last or "never"}
    return out


def _message_wakes(m, obj_state=""):
    """Harness-neutral base gate: explicit task / stuck / blocked mail wakes every mode."""
    if m.get("source") == "review" and obj_state in ("blocked", "achieved", "replaced"):
        return False
    if m.get("kind") == "task" or m.get("task"):
        return True
    text = str(m.get("text") or "").strip().lower()
    if text.startswith(("stuck", "blocked", "task:", "task ")):
        return True
    return False


def _wake_message_summary(message):
    """Bound message text before it reaches pending JSON, logs, or a prompt."""
    rendered = fmt_msg(message)
    if len(rendered) <= WAKE_MESSAGE_MAX_CHARS:
        return rendered
    return rendered[:WAKE_MESSAGE_MAX_CHARS - 1] + "…"


def _continuous_message_wakes(board, owner, message):
    """An ordinary DM/@mention wakes only a seat configured continuous.

    Review copies and acknowledgement/status replies remain notification-only;
    otherwise a master and CoS acknowledging each other can create a paid
    ping-pong loop. Explicit kind=task still wins through `_message_wakes`.
    """
    if wake_mode_of(board, owner) != "continuous" or is_board_broadcast(message):
        return False
    if message.get("source") in ("review", "receipt"):
        return False
    if message.get("kind") in ("ack", "receipt"):
        return False
    text = str(message.get("text") or "").strip().lower()
    return not text.startswith(("ack", "idle:"))


def wake_reason_of(pending):
    # A newly directed instruction must stay visible even when leadership has
    # an older board-wide stuck escalation. Both gates remain in `pending`, but
    # this primary reason drives the UI/log and tells the harness what arrived.
    if pending.get("forced"):
        return "forced"
    tasks = pending.get("task_messages") or []
    stuck = pending.get("stuck_messages") or []
    # A directed `stuck:` record appears in both lists. Preserve the established
    # stuck reason when that is all there is, while allowing any distinct/new
    # directed instruction to take priority over an older escalation.
    def _same_as_stuck(summary):
        return any(s == summary or (summary.endswith("…") and s.startswith(summary[:-1]))
                   for s in stuck)
    if tasks and (not stuck or any(not _same_as_stuck(t) for t in tasks)):
        return "task_messages"
    if stuck:
        return "stuck_messages"
    if tasks:
        return "task_messages"
    for k in ("holding", "suggested_for_me", "ready_in_my_lane", "drive"):
        if pending.get(k):
            return k
    return "none"


def pending_view(pending, force=False):
    """pending_work plus why/stop, for CLI/UI. Does not change wake gates."""
    out = dict(pending)
    if force:
        out["forced"] = True
    out["wake_reason"] = wake_reason_of(out)
    out["stop_condition"] = STOP_CONDITION
    return out


def actionable(pending):
    """Only held/ready work, explicit task or stuck mail, force, or a valid drive."""
    if not pending or pending.get("limited"):
        return False
    return any(k in WAKE_KEYS for k in pending)


def _watch_trigger_fingerprint(board, owner, pending):
    """Stable identity of what would start a watch run (T-561 retrigger guard).

    Uses message ids and ticket ids, not the human-readable strings pending_work
    prints -- those can differ in formatting while the underlying mail is the same.
    """
    parts = []
    if pending.get("messages_to_me"):
        msgs = _safe(lambda: unread(board, owner), []) or []
        direct = [m for m in msgs if not is_board_broadcast(m)]
        obj_state = objective_state(_safe(lambda: load_objective(board), {}))
        waking = [m for m in direct
                  if (_message_wakes(m, obj_state)
                      or _continuous_message_wakes(board, owner, m))]
        parts.extend("msg:" + _msg_id(m) for m in waking[-WAKE_MESSAGE_LIMIT:])
    for key in ("holding", "suggested_for_me", "ready_in_my_lane", "review_queue"):
        if key in pending:
            for item in pending[key]:
                parts.append("%s:%s" % (key, item.split(" ")[0]))
    if pending.get("stuck_messages"):
        parts.extend("stuck:" + s[:80] for s in pending["stuck_messages"])
    if pending.get("health_crit"):
        parts.extend("health:" + s[:80] for s in pending["health_crit"])
    if pending.get("drive"):
        parts.append("drive:" + json.dumps(pending["drive"], sort_keys=True))
    return tuple(sorted(parts)) if parts else None


# ---- remote adapters: fenced lease, atomic wake claim, measured run --------

class RemoteProtocolError(Exception):
    pass


def _remote_state_path(board, owner):
    return os.path.join(board, "adapters", owner + ".json")


def _remote_epoch():
    return datetime.now(timezone.utc).timestamp()


def _load_remote_state_unlocked(board, owner):
    try:
        with open(_remote_state_path(board, owner)) as stream:
            state = json.load(stream)
        return state if isinstance(state, dict) else {}
    except (IOError, ValueError):
        return {}


def load_remote_state(board, owner):
    """Read a remote adapter state record. Invalid seat names have no state."""
    if not HOOK_AGENT_RE.fullmatch(str(owner or "")):
        return {}
    return _load_remote_state_unlocked(board, owner)


def _remote_update(board, owner, mutate):
    """Serialize a remote lease/claim transition and publish it atomically."""
    path = _remote_state_path(board, owner)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    result = []
    with _AgentLock(board, owner + ".remote"):
        state = _load_remote_state_unlocked(board, owner)
        state.setdefault("schema", 1)
        state.setdefault("agent", owner)
        value = mutate(state)
        temporary = "%s.tmp.%d.%s" % (path, os.getpid(), uuid.uuid4().hex[:8])
        with open(temporary, "w") as stream:
            json.dump(state, stream, indent=2, sort_keys=True)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        result.append(value)
    return result[0] if result else None


def _remote_lease_online(state, at=None):
    lease = (state or {}).get("lease") or {}
    return bool(lease.get("id") and float(lease.get("expires_epoch") or 0) >
                (at if at is not None else _remote_epoch()))


def _remote_public_state(state):
    """Remote state safe for UI/status; the bearer lease id never leaves it."""
    state = state or {}
    lease = state.get("lease") or {}
    claim = state.get("claim") or {}
    failure = state.get("failure") or {}
    return {
        "online": _remote_lease_online(state),
        "bridge_id": lease.get("bridge_id", ""),
        "fence": int(lease.get("fence") or state.get("fence") or 0),
        "heartbeat_at": lease.get("heartbeat_at", ""),
        "expires_at": lease.get("expires_at", ""),
        "claim_id": claim.get("id", ""),
        "run_id": claim.get("run_id", ""),
        "run_state": ("recovery-required" if claim.get("recovery_required") else
                      ("running" if claim.get("started_at") else
                       ("claimed" if claim else ""))),
        "attempt": int(claim.get("attempt") or failure.get("attempts") or 0),
        "failure_state": failure.get("state", ""),
        "failure_reason": failure.get("reason", ""),
        "retry_at": failure.get("retry_at", ""),
        "last_run": state.get("last_run") or {},
    }


def _remote_require_lease(state, lease_id, fence, renew=True):
    lease = state.get("lease") or {}
    if not _remote_lease_online(state):
        raise RemoteProtocolError("adapter lease expired; register again")
    if lease.get("id") != lease_id or int(lease.get("fence") or 0) != int(fence):
        raise RemoteProtocolError("stale adapter lease/fence")
    if renew:
        ttl = int(lease.get("ttl") or REMOTE_LEASE_TTL)
        lease["heartbeat_at"] = now()
        lease["expires_epoch"] = _remote_epoch() + ttl
        lease["expires_at"] = datetime.fromtimestamp(
            lease["expires_epoch"], timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    return lease


def _remote_trigger_key(fingerprint):
    if not fingerprint:
        return ""
    return hashlib.sha256(json.dumps(list(fingerprint), sort_keys=True).encode("utf-8")).hexdigest()


def _remote_register(board, owner, bridge_id, ttl, max_attempts, replace=False, worktree=""):
    ttl = max(5, min(int(ttl or REMOTE_LEASE_TTL), 300))
    max_attempts = max(1, min(int(max_attempts or REMOTE_MAX_ATTEMPTS), 10))

    def mutate(state):
        current = state.get("lease") or {}
        if _remote_lease_online(state):
            if current.get("bridge_id") != bridge_id:
                raise RemoteProtocolError("adapter already online under bridge %s" %
                                          current.get("bridge_id", "unknown"))
            if not replace:
                # Register creates a bearer credential. Never disclose the
                # existing credential to another process merely because it
                # guessed the same human-readable bridge id. The holder uses
                # heartbeat for renewal; deliberate same-bridge recovery must
                # say --replace, which increments the fence and invalidates
                # the old bearer.
                raise RemoteProtocolError(
                    "adapter bridge is already registered; heartbeat it or use --replace")
        fence = int(state.get("fence") or 0) + 1
        expiry = _remote_epoch() + ttl
        lease = {"id": "lease_" + uuid.uuid4().hex, "bridge_id": bridge_id,
                 "fence": fence, "ttl": ttl, "registered_at": now(),
                 "heartbeat_at": now(), "expires_epoch": expiry,
                 "expires_at": datetime.fromtimestamp(
                     expiry, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
                 "worktree": os.path.abspath(worktree) if worktree else ""}
        state["fence"] = fence
        state["lease"] = lease
        state["max_attempts"] = max_attempts
        claim = state.get("claim") or {}
        if claim:
            claim["fence"] = fence
            if claim.get("started_at"):
                # The old process may have crossed an external side-effect
                # boundary. Require an explicit recovery choice; never run it
                # twice merely because a network lease expired.
                claim["recovery_required"] = True
            else:
                claim["delivered_at"] = ""
        return {"status": "online", "lease_id": lease["id"], "fence": fence,
                "expires_at": lease["expires_at"], "reused": False,
                "recovery_required": bool(claim.get("recovery_required"))}

    return _remote_update(board, owner, mutate)


def _remote_claim_once(board, owner, lease_id, fence, prompt_kind=""):
    pending = pending_work(board, owner)
    fingerprint = _watch_trigger_fingerprint(board, owner, pending) if actionable(pending) else None
    trigger_key = _remote_trigger_key(fingerprint)

    def mutate(state):
        lease = _remote_require_lease(state, lease_id, fence)
        claim = state.get("claim") or {}
        if claim:
            if claim.get("recovery_required"):
                return {"status": "recovery-required", "claim_id": claim.get("id"),
                        "run_id": claim.get("run_id"), "fence": int(lease["fence"])}
            if claim.get("delivered_at"):
                return {"status": "busy", "claim_id": claim.get("id"),
                        "run_id": claim.get("run_id"), "fence": int(lease["fence"])}
            claim["delivered_at"] = now()
            return {"status": "wake", "claim": dict(claim), "recovered": True}
        if not trigger_key:
            return {"status": "idle", "fence": int(lease["fence"])}
        if state.get("completed_trigger") == trigger_key:
            return {"status": "handled", "fence": int(lease["fence"])}
        failure = state.get("failure") or {}
        if failure.get("trigger") == trigger_key:
            if failure.get("state") == "failed":
                return {"status": "failed", "attempts": int(failure.get("attempts") or 0),
                        "reason": failure.get("reason", ""), "fence": int(lease["fence"])}
            if float(failure.get("retry_epoch") or 0) > _remote_epoch():
                return {"status": "retrying", "attempts": int(failure.get("attempts") or 0),
                        "retry_at": failure.get("retry_at", ""), "fence": int(lease["fence"])}
            attempt = int(failure.get("attempts") or 0) + 1
        else:
            attempt = 1
        seq = int(state.get("run_seq") or 0) + 1
        state["run_seq"] = seq
        held = (pending.get("holding") or [""])[0].split(" ")[0] or _watch_bind_ticket(board, owner) or ""
        claim = {"id": "claim_" + uuid.uuid4().hex,
                 "run_id": "rr-%s-%d-%s" % (owner, seq, uuid.uuid4().hex[:12]),
                 "run_no": seq, "trigger": trigger_key,
                 "trigger_keys": sorted(k for k in pending if k in WAKE_KEYS),
                 "ticket": held, "attempt": attempt, "fence": int(lease["fence"]),
                 "claimed_at": now(), "delivered_at": now(), "started_at": ""}
        state["claim"] = claim
        return {"status": "wake", "claim": dict(claim), "recovered": False}

    result = _remote_update(board, owner, mutate)
    if result.get("status") == "wake":
        claim = result.pop("claim")
        result.update({"claim_id": claim["id"], "run_id": claim["run_id"],
                       "run_no": claim["run_no"], "attempt": claim["attempt"],
                       "fence": claim["fence"], "wake": pending_view(pending)})
        kind = prompt_kind or ("cos" if owner == (current_master(board) or {}).get("cos")
                               else ("master" if owner == (current_master(board) or {}).get("owner") else ""))
        result["prompt"] = prompt_text(argparse.Namespace(
            agent=owner, master=kind == "master", cos=kind == "cos", extra=""), board)
    return result


def _remote_run_start(board, owner, lease_id, fence, claim_id):
    emitted = []

    def mutate(state):
        lease = _remote_require_lease(state, lease_id, fence)
        claim = state.get("claim") or {}
        if claim.get("id") != claim_id or int(claim.get("fence") or 0) != int(fence):
            raise RemoteProtocolError("claim is absent or fenced")
        if claim.get("recovery_required"):
            raise RemoteProtocolError("claim needs explicit remote retry recovery")
        if claim.get("started_at"):
            return {"status": "running", "run_id": claim["run_id"], "idempotent": True}
        claim["started_at"] = now()
        emitted.append(dict(claim, bridge_id=lease.get("bridge_id", ""),
                            worktree=lease.get("worktree", "")))
        return {"status": "running", "run_id": claim["run_id"], "idempotent": False}

    result = _remote_update(board, owner, mutate)
    if emitted:
        claim = emitted[0]
        traj_event(board, "run_start", agent=owner, ticket=claim.get("ticket") or None,
                   run_no=claim["run_no"], run_id=claim["run_id"],
                   trigger=claim.get("trigger_keys") or [], harness_cmd="remote",
                   worktree=claim.get("worktree") or None, release_sha=_release_commit(),
                   bridge_id=claim.get("bridge_id"), fence=int(fence), attempt=claim.get("attempt"))
    return result


def _remote_run_end(board, owner, lease_id, fence, claim_id, exit_code,
                    input_tokens=None, output_tokens=None, cost_usd=None, reason=""):
    ended = []

    def mutate(state):
        _remote_require_lease(state, lease_id, fence)
        previous = state.get("last_run") or {}
        if previous.get("claim_id") == claim_id:
            return {"status": previous.get("status", "completed"),
                    "run_id": previous.get("run_id"), "idempotent": True}
        claim = state.get("claim") or {}
        if claim.get("id") != claim_id or int(claim.get("fence") or 0) != int(fence):
            raise RemoteProtocolError("claim is absent or fenced")
        if not claim.get("started_at"):
            raise RemoteProtocolError("remote start must succeed before remote end")
        attempt = int(claim.get("attempt") or 1)
        max_attempts = int(state.get("max_attempts") or REMOTE_MAX_ATTEMPTS)
        status = "completed"
        if int(exit_code) == 0:
            state["completed_trigger"] = claim.get("trigger", "")
            state.pop("failure", None)
        else:
            status = "retrying" if attempt < max_attempts else "failed"
            delay = min(2 ** max(0, attempt - 1), 30) if status == "retrying" else 0
            retry_epoch = _remote_epoch() + delay if delay else 0
            state["failure"] = {"state": status, "trigger": claim.get("trigger", ""),
                                "attempts": attempt, "max_attempts": max_attempts,
                                "reason": (reason or "remote harness exit %s" % exit_code)[:240],
                                "retry_epoch": retry_epoch,
                                "retry_at": (datetime.fromtimestamp(retry_epoch, timezone.utc).strftime(
                                    "%Y-%m-%dT%H:%M:%SZ") if retry_epoch else "")}
        record = {"claim_id": claim_id, "run_id": claim.get("run_id"), "run_no": claim.get("run_no"),
                  "ticket": claim.get("ticket", ""), "started_at": claim.get("started_at"),
                  "ended_at": now(), "exit": int(exit_code), "status": status,
                  "attempt": attempt, "fence": int(fence), "reason": reason[:240] if reason else ""}
        state["last_run"] = record
        state.pop("claim", None)
        ended.append((record, status))
        return {"status": status, "run_id": record["run_id"], "attempt": attempt,
                "max_attempts": max_attempts, "idempotent": False,
                "retry_at": (state.get("failure") or {}).get("retry_at", "")}

    result = _remote_update(board, owner, mutate)
    if ended:
        record, status = ended[0]
        traj_event(board, "run_end", agent=owner, ticket=record.get("ticket") or None,
                   run_no=record.get("run_no"), run_id=record.get("run_id"),
                   started_at=record.get("started_at"), ended_at=record.get("ended_at"),
                   duration_s=_iso_span_secs(record.get("started_at"), record.get("ended_at")),
                   exit=int(exit_code), outcome=status, harness_cmd="remote", fence=int(fence),
                   attempt=record.get("attempt"), input_tokens=input_tokens,
                   output_tokens=output_tokens, cost_usd=cost_usd,
                   usage_error=None if any(v is not None for v in
                                           (input_tokens, output_tokens, cost_usd)) else "unreported")
    return result


def cmd_remote(a, board):
    """Fenced local-board protocol used by a remote model/session bridge."""
    owner = _hook_agent(a.agent)
    operation = a.remote_cmd
    try:
        pinned = whoami()
        if operation != "status" and pinned != owner:
            raise RemoteProtocolError(
                "remote wrapper identity is %s, not requested agent %s" % (pinned, owner))
        registered, _ = harness_of(board, owner)
        if operation not in ("status", "release") and registered != "remote":
            raise RemoteProtocolError(
                "agent %s is registered for harness %s, not remote" % (owner, registered))
        if operation == "register":
            bridge_id = _hook_agent(a.bridge_id)
            result = _remote_register(board, owner, bridge_id, a.ttl, a.max_attempts,
                                      replace=a.replace, worktree=a.worktree)
        elif operation == "heartbeat":
            result = _remote_update(board, owner, lambda state: {
                "status": "online", "fence": int(_remote_require_lease(
                    state, a.lease_id, a.fence)["fence"]),
                "expires_at": state["lease"]["expires_at"]})
        elif operation == "next":
            wait = max(0, min(int(a.wait or 0), REMOTE_LONG_POLL_MAX))
            deadline = _remote_epoch() + wait
            while True:
                result = _remote_claim_once(board, owner, a.lease_id, a.fence,
                                            prompt_kind=a.prompt_kind)
                if result.get("status") not in ("idle", "handled") or _remote_epoch() >= deadline:
                    break
                import time as _time
                _time.sleep(min(0.25, max(0, deadline - _remote_epoch())))
        elif operation == "start":
            result = _remote_run_start(board, owner, a.lease_id, a.fence, a.claim_id)
        elif operation == "end":
            for value, label in ((a.input_tokens, "input tokens"),
                                 (a.output_tokens, "output tokens"), (a.cost_usd, "cost")):
                if value is not None and value < 0:
                    raise RemoteProtocolError("%s cannot be negative" % label)
            result = _remote_run_end(board, owner, a.lease_id, a.fence, a.claim_id,
                                     a.exit, input_tokens=a.input_tokens,
                                     output_tokens=a.output_tokens, cost_usd=a.cost_usd,
                                     reason=a.reason)
        elif operation == "retry":
            abandoned = []

            def retry(state):
                _remote_require_lease(state, a.lease_id, a.fence)
                claim = state.get("claim") or {}
                if claim and claim.get("id") != (a.claim_id or claim.get("id")):
                    raise RemoteProtocolError("claim is absent or fenced")
                if claim.get("started_at"):
                    abandoned.append(dict(claim))
                # An uncertain in-flight recovery continues the prior attempt
                # count. A deliberate retry after a terminal failure grants a
                # fresh bounded budget instead of failing again immediately.
                attempts = int(claim.get("attempt") or 0) if claim else 0
                trigger = claim.get("trigger") or (state.get("failure") or {}).get("trigger", "")
                if claim:
                    state.pop("claim", None)
                state["failure"] = {"state": "retrying", "trigger": trigger,
                                    "attempts": attempts, "max_attempts": int(
                                        state.get("max_attempts") or REMOTE_MAX_ATTEMPTS),
                                    "reason": "operator-authorized retry", "retry_epoch": 0,
                                    "retry_at": ""}
                return {"status": "retrying", "attempts": attempts}

            result = _remote_update(board, owner, retry)
            if abandoned:
                old = abandoned[0]
                traj_event(board, "run_end", agent=owner, ticket=old.get("ticket") or None,
                           run_no=old.get("run_no"), run_id=old.get("run_id"),
                           started_at=old.get("started_at"), ended_at=now(), exit=75,
                           outcome="lease_lost", harness_cmd="remote", fence=int(a.fence))
        elif operation == "release":
            def release(state):
                lease = _remote_require_lease(state, a.lease_id, a.fence, renew=False)
                lease["expires_epoch"] = 0
                lease["expires_at"] = now()
                claim = state.get("claim") or {}
                if claim and not claim.get("started_at"):
                    claim["delivered_at"] = ""
                return {"status": "offline", "fence": int(lease["fence"])}
            result = _remote_update(board, owner, release)
        elif operation == "status":
            result = _remote_public_state(load_remote_state(board, owner))
        else:
            raise RemoteProtocolError("remote operation required")
    except (RemoteProtocolError, ValueError) as exc:
        print(json.dumps({"status": "error", "error": str(exc)}), file=sys.stderr)
        raise SystemExit(2)
    print(json.dumps(result, sort_keys=True))


def _watch_note_limit_from_log(board, owner, log_slice):
    """Record limit when the child exits before tickets inbox (T-561).

    pending_work already suppresses wake on rec['limit']; this is the lever for
    weekly-limit exits that never reach inbox.
    """
    if not log_slice or not _looks_limited(log_slice):
        return
    if (_agent_rec(board, owner) or {}).get("limit"):
        return
    import re as _re
    note = _first_match(log_slice, CLI_LIMIT_STRINGS) or "usage limit"
    until = ""
    m = _re.search(r"resets?\s+([^\n\r\.]{3,40})", log_slice, _re.I)
    if m:
        until = m.group(1).strip()
    lim = {"at": now(), "until": until, "note": note}
    _agent_update(board, owner, lambda rec: rec.update({"limit": lim}))


WORKER_PROMPT = """You are {agent}, a worker on the shared ticket board at {board} (repo {root}).
TICKET_AGENT is already set in your environment; run `atm ...` commands plainly (no env prefix). `tickets` is a compatibility alias for the same implementation and board.
Rules: one ticket at a time; own git worktree, never main; `tickets sync` before `tickets review`;
`tickets update <id> "..."` every 45 minutes; finish with `tickets review <id> --notes "paths, tests, decisions"`;
never edit .tickets/ by hand; never run `tickets clear`. Board-only comms: `tickets msg`.
STUCK RULE: if anything blocks you -- a permission you cannot get, a failing test you cannot fix,
an unclear ticket, missing access or a decision that is not yours -- do not wait and do not stop
silently. Post `tickets msg "stuck: <what, what you tried, what you need>" --to {master} --re <id>`,
then `tickets update <id> "stuck: ..."`; if you cannot continue at all, `tickets block <id> --reason "..."`.
The master's job is to unblock you; yours is to say so early.
Do now, in order:
1. `tickets inbox` -- read and, if anything is addressed to you, answer with `tickets msg --to <who>`.
2. `tickets mine` -- if you hold a ticket, continue it from where the notes left off.
3. Otherwise `tickets next` -- if it hands you a ticket, read the printed briefing files, then work it.
4. When the ticket is finished and tests pass: commit, `tickets sync`, `tickets review <id> --notes ...`, then go to 3.
5. If `tickets next` says nothing is ready and you hold nothing: post one line with `tickets msg "idle: <what you checked>"` and stop.
{extra}"""

MASTER_PROMPT = """You are {agent}, the MASTER of the shared ticket board at {board} (repo {root}).
TICKET_AGENT is set; run `atm ...` plainly (`tickets` is the same CLI). You do not take feature tickets.
This run: pick ONE concrete outcome (one unblock, one merge batch, or one routing act) and stop.
Ordinary messages and ACKs are notification-only and must not extend the run.
If there is no standing objective with a measurable --exit criterion, ask for one
(`tickets objective "<what done looks like>" --exit "<observable end>"`) and do not invent it.
Transition the objective with `--done`/`--achieved`, `--blocked`, or `--replaced` when that is the outcome.
Your three jobs, every wake-up:
1. UNBLOCK: `tickets inbox` -- every message starting with "stuck:" or addressed to you gets an answer within this run:
   grant context (`tickets brief <agent> "..."` or `--ticket <id>`), re-scope or split the ticket
   (`tickets create ... --blocks <id>`, `tickets dep`), reassign (`tickets assign <id> --owner <who>`), or
   decide and say so. Never leave a stuck agent without a reply.
2. REVIEW + MERGE: `tickets master` shows the REVIEW QUEUE. For each entry read the diff against main
   (`git diff main...<branch>`), check tests ran, then `tickets merge <branch>` (runs the suite, fast-forwards
   main, closes the ticket). If it is not mergeable, `tickets msg --to <owner> --re <id>` with what to change
   and `tickets reopen <id>`. Push main with `git push origin main` after merges.
3. COORDINATE: `tickets dash --once` and `tickets util` -- reopen tickets whose owner is silent > 90 min
   (`tickets limits` first: AUTH means /login is needed, not a wait), `tickets route` new tickets,
   keep one ticket per agent, spawn or brief workers when lanes are empty (`tickets spawn <name> --model ...`).
   Log every non-obvious call: `tickets master log "..."`. Post a short status pulse with `tickets msg`.
Stop after that one bounded batch even if the review queue or inbox still has notification-only mail.
{extra}"""

COS_PROMPT = """You are {agent}, the CHIEF OF STAFF of the shared ticket board at {board} (repo {root}).
TICKET_AGENT is set; run `tickets ...` plainly. You do not take feature tickets.
This run: pick ONE concrete outcome (one unblock, one merge batch, or one routing act) and stop.
Ordinary messages and ACKs are notification-only and must not extend the run.
Read the current standing objective as context only. Do not ask for, set, replace, or invent an objective;
escalate those decisions to the master with `tickets msg --to <master>`.
The master planner sets scope and routes by complexity; you review, unblock and merge.
Your three jobs, every wake-up:
1. UNBLOCK: `tickets inbox` -- every message starting with "stuck:" or addressed to you gets an answer within this run:
   grant context (`tickets brief <agent> "..."` or `--ticket <id>`), re-scope or split the ticket
   (`tickets create ... --blocks <id>`, `tickets dep`), reassign (`tickets assign <id> --owner <who>`), or
   decide and say so. Never leave a stuck agent without a reply.
2. REVIEW + MERGE: `tickets master` shows the REVIEW QUEUE. For each entry read the diff against main
   (`git diff main...<branch>`), check tests ran, then `tickets merge <branch>` (runs the suite, fast-forwards
   main, closes the ticket). If it is not mergeable, `tickets msg --to <owner> --re <id>` with what to change
   and `tickets reopen <id>`. Push main with `git push origin main` after merges.
3. COORDINATE: `tickets dash --once` and `tickets util` -- reopen tickets whose owner is silent > 90 min
   (`tickets limits` first: AUTH means /login is needed, not a wait), `tickets route` new tickets,
   keep one ticket per agent, spawn or brief workers when lanes are empty (`tickets spawn <name> --model ...`).
   Log every non-obvious call: `tickets master log "..."`. Post a short status pulse with `tickets msg`.
Stop after that one bounded batch even if the review queue or inbox still has notification-only mail.
{extra}"""

PLANNER_PROMPT = """You are {agent}, the MASTER PLANNER of the shared ticket board at {board} (repo {root}).
TICKET_AGENT is set; run `tickets ...` plainly. A chief of staff ({cos}) handles review, merge and day-to-day
unblocking; you do not take feature tickets and you do not merge unless the cos is silent.
You own objective discipline: if none is active, ask for one with --exit; plan and route only work that advances it.
Do not invent objectives. This run: ONE concrete outcome, then stop. ACKs are notification-only.
Your jobs, every wake-up:
1. SCOPE + VISION: `tickets master` and `tickets dash --once`. Keep the sprint pointed at what the user wants
   (MASTER.md "CEO memo" and decision log). Split, re-scope, or cut tickets that drift; add the ones that are missing
   (`tickets create ... --blocks/--deps`, `tickets plan`). Log every call: `tickets master log "..."`.
2. ROUTE BY COMPLEXITY: `tickets route`, then hard-assign where it matters (`tickets assign <id> --owner <agent>`):
   priority-1 / contract / migration work to high-tier agents (opus); routine docs, tests, polish to low-tier
   (sonnet, codex). Give each worker the context it needs: `tickets brief <agent> "..."` or `--ticket <id>`.
3. ESCALATIONS: answer messages addressed to you (scope questions, decisions the cos escalated, anything
   starting with "stuck:" that the cos has not answered within an hour). Decisions the user reserved
   (spend, deploy provider, default flips) get a `tickets msg` to the user's attention, not a guess.
4. STAFFING: if a lane has ready work and no live worker, `tickets spawn <name> --model <tier> --roles ...`;
   if a worker is silent > 90 min, `tickets limits` then `tickets reopen`.
Stop after that one bounded batch. Do not keep the model awake for acknowledgements or a broad review queue alone.
{extra}"""


STANDING_SEAT_PROMPT = """HEARTBEAT: this seat is woken every {every} minutes only when the objective has an --exit criterion.
Do the standing brief as one bounded batch, post one short finding with numbers, and stop. The board's
objective, which your brief serves: {objective}"""

DRIVE_PROMPT = """OBJECTIVE (set by {set_by}; state={state}; `tickets objective` to read it in full):
{objective}
EXIT CRITERION: {exit_criterion}

DRIVE STATUS now:
{status}

5. DRIVE THE OBJECTIVE (one bounded batch this run, then stop):
   compare the status above with the exit criterion. Plan or route only the next slice that advances it:
   `tickets plan`/`tickets create`, `tickets route` + `tickets assign`, `tickets brief`, `tickets spawn` if a
   lane has no live worker, and `tickets master log`. If the criterion is met, run
   `tickets objective --done "<evidence>"`. If blocked on the user, `tickets objective --blocked "<why>"`.
   If a better objective replaced it, `tickets objective --replaced "<why>"`. Do not invent a new objective.
   Stop after this batch; do not keep looping on acknowledgements or an unbounded heartbeat."""


def cmd_objective(a, board):
    """Set, show or close the standing objective the master drives toward."""
    path = objective_path(board)
    cur = load_objective(board)
    blocked = getattr(a, "blocked", None)
    replaced = getattr(a, "replaced", None)
    done = a.done
    terminals = [(blocked, "blocked"), (replaced, "replaced"), (done, "achieved")]
    chosen = [(ev, st) for ev, st in terminals if ev is not None]
    if len(chosen) > 1:
        sys.exit("use only one of --done/--achieved, --blocked, --replaced")
    if chosen:
        if not cur:
            sys.exit("no objective set")
        evidence, state = chosen[0]
        cur["state"] = state
        cur["done"] = state == "achieved"
        cur["done_at"] = now()
        cur["evidence"] = evidence
        with open(path, "w") as f:
            json.dump(cur, f, indent=2)
        label = {"achieved": "met", "blocked": "blocked", "replaced": "replaced"}[state]
        post_message(board, whoami(a.by), "objective %s: %s -- %s" % (
            label, cur.get("text", "")[:120], evidence))
        _master_log(board, "objective %s: %s" % (label, evidence), by=whoami(a.by))
        print("objective marked %s" % state)
        return
    if a.text and getattr(a, "set_text", ""):
        sys.exit("objective: use positional text or --set, not both")
    text = (a.text or getattr(a, "set_text", "") or "").strip()
    if text:
        exit_c = (getattr(a, "exit_criterion", None) or "").strip()
        rec = {"text": text, "set_by": whoami(a.by), "at": now(), "done": False,
               "state": "active", "exit_criterion": exit_c, "exit_missing": not bool(exit_c)}
        with open(path, "w") as f:
            json.dump(rec, f, indent=2)
        post_message(board, whoami(a.by), "objective set: %s" % text[:200])
        _master_log(board, "objective set: %s" % text, by=whoami(a.by))
        print("objective set")
        if rec["exit_missing"]:
            print("FLAG: no measurable exit criterion; add --exit \"<observable end state>\"")
        return
    if not cur:
        print("no objective set; `tickets objective \"<what done looks like>\" --exit \"<observable end>\"`")
        return
    st = objective_state(cur).upper()
    print("OBJECTIVE -- %s%s (set by %s, %s)" % (
        st, " / MET" if st == "ACHIEVED" else "", cur.get("set_by", "?"), cur.get("at", "")))
    print(cur.get("text", ""))
    exit_c = (cur.get("exit_criterion") or "").strip()
    print("exit: %s" % (exit_c or "(none)"))
    if objective_exit_missing(cur):
        print("FLAG: no measurable exit criterion; add --exit \"<observable end state>\"")
    if cur.get("evidence"):
        print("evidence: %s" % cur.get("evidence", ""))
    print()
    print(drive_status(board))


def cmd_drive(a, board):
    """Set the objective and spawn the master seat with a heartbeat, in one go:
    `tickets drive "<objective>" --as boss --tool cursor+claude --heartbeat 30`."""
    owner = whoami(a.by)
    if a.text:
        ns = argparse.Namespace(text=a.text, done=None, by=owner, blocked=None, replaced=None,
                                exit_criterion=getattr(a, "exit_criterion", None))
        cmd_objective(ns, board)
    elif not load_objective(board):
        sys.exit("give an objective: tickets drive \"<what done looks like>\"")
    argv = [sys.executable, os.path.realpath(__file__), "spawn", owner, "--master",
            "--heartbeat", str(a.heartbeat), "--every", str(a.every)]
    if a.tool:  # absent means "the harness `tickets join` registered for this seat"
        argv += ["--harness", a.tool]
    if getattr(a, "cmd_template", ""):
        argv += ["--cmd", a.cmd_template]
    if a.model:
        argv += ["--model", a.model]
    if getattr(a, "wake_mode", None):
        argv += ["--wake-mode", a.wake_mode]
    import subprocess
    if a.restart:
        subprocess.call([sys.executable, os.path.realpath(__file__), "spawn", owner, "--stop"])
    sys.exit(subprocess.call(argv))


def cos_prompt_text(agent, board, root, extra):
    return COS_PROMPT.format(agent=agent, board=board, root=root, extra=extra)


def cmd_pending(a, board):
    owner = whoami(a.agent)
    force = bool(getattr(a, "force", False))
    p = pending_view(pending_work(board, owner), force=force)
    if a.json:
        print(json.dumps({"agent": owner, "pending": actionable(p), **p}))
    elif p:
        for k, v in p.items():
            print("%s: %s" % (k, v if not isinstance(v, list) else "; ".join(v)))
    else:
        print("nothing pending for %s" % owner)
    sys.exit(0 if actionable(p) else 1)


def brief_path(board, owner):
    return os.path.join(board, "briefs", owner + ".md")


def agent_brief(board, owner, limit=6000):
    """The agent's standing context (.tickets/briefs/<agent>.md), if any."""
    try:
        with open(brief_path(board, owner)) as f:
            text = f.read().strip()
    except OSError:
        return ""
    return text if len(text) <= limit else text[:limit] + "\n...(brief truncated; read the file)"


# T-529 / E-013: board-canonical role context on watch/spawn. Not a memory product.
# Store (pm-atman-role-context-v1 / T-530): .tickets/briefs/_shared.md and
# .tickets/briefs/roles/<role>.md. Inject does not read repo-root roles/.
ROLE_CONTEXT_LIMIT = 6000
_ROLE_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]*$")


def shared_brief_path(board):
    """Board-canonical store: {board}/briefs/_shared.md."""
    return os.path.join(os.path.abspath(board), "briefs", "_shared.md")


def role_brief_path(board, role):
    """Board-canonical store: {board}/briefs/roles/<role>.md. None if unsafe."""
    if role == "_shared" or not _ROLE_NAME_RE.match(role or ""):
        return None
    return os.path.join(os.path.abspath(board), "briefs", "roles", role + ".md")


def _role_file_candidates(name, board=None):
    """Only the board-canonical brief store. No repo-root roles/, no TICKETS_ROLES_DIR."""
    if not board:
        return []
    if name == "_shared":
        return [shared_brief_path(board)]
    p = role_brief_path(board, name)
    return [p] if p else []


def _read_role_file(name, board=None, limit=ROLE_CONTEXT_LIMIT):
    """Return (path, text) from .tickets/briefs/, or (None, "")."""
    for path in _role_file_candidates(name, board):
        if not os.path.isfile(path):
            continue
        try:
            with open(path, encoding="utf-8", errors="replace") as f:
                text = f.read().strip()
        except OSError:
            continue
        if not text:
            continue
        if len(text) > limit:
            text = text[:limit] + "\n...(role context truncated; read the file)"
        return path, text
    return None, ""


def role_context(board, owner, explicit=None, limit=ROLE_CONTEXT_LIMIT):
    """Seat markdown injected on watch/spawn from .tickets/briefs/. Not a shared-memory product."""
    names = ["_shared"]
    mapped = roles_for(board, owner, explicit)
    if mapped:
        for r in mapped:
            if r and r != "_shared" and r not in names:
                names.append(r)
    parts = []
    for name in names:
        path, text = _read_role_file(name, board, limit=limit)
        if path and text:
            parts.append("Role context (%s):\n%s" % (path, text))
    return "\n\n".join(parts)


def _safe_role_slug(role):
    """CLI guard for `brief --role`. Same names as role_brief_path; exits on bad input."""
    name = (role or "").strip()
    if role_brief_path(".", name):
        return name
    if not name:
        sys.exit("brief --role needs a role name")
    if name == "_shared":
        sys.exit("brief --role _shared refused; shared baseline is briefs/_shared.md")
    sys.exit("brief --role %r is not a safe role name" % role)


def _read_brief_file(path, limit=6000):
    """Read one board brief file (the T-529 inject source; no other store)."""
    try:
        with open(path) as f:
            text = f.read().strip()
    except OSError:
        return ""
    return text if len(text) <= limit else text[:limit] + "\n...(brief truncated; read the file)"


def ticket_context(board, owner):
    """Context notes attached to the agent's held ticket(s)."""
    out = []
    for t in load_all(board):
        if t.get("status") == "claimed" and t.get("owner") == owner:
            ctx = [n["text"] for n in t.get("notes", []) if n.get("kind") == "context"]
            if ctx:
                out.append("%s %s:\n  - %s" % (t["id"], t.get("title", "")[:60], "\n  - ".join(ctx)))
    return "\n".join(out)


def _task_dominant_extra(board, owner):
    """Held ticket + explicit task beat unrelated CoS review/notification traffic."""
    pending = _safe(lambda: pending_work(board, owner), {}) or {}
    holding = pending.get("holding") or []
    if not holding:
        return ""
    tasks = pending.get("task_messages") or []
    return (
        "HELD WORK DOMINATES THIS RUN.\n"
        "You currently hold: %s\n"
        "Explicit task/DM (do this now; do not review, merge, or pulse unrelated tickets):\n%s\n"
        "Unrelated review_queue and notification-only mail are suppressed until this held "
        "work is finished, reviewed, or blocked."
    ) % (
        "; ".join(holding),
        "\n".join(tasks) if tasks else "(continue from the held ticket notes)",
    )


def cmd_prompt(a, board):
    print(prompt_text(a, board))


def prompt_text(a, board):
    """The worker/master/cos prompt as a string.

    Split out of cmd_prompt so the watcher can write it to a {prompt_file} for
    a BYOA harness without shelling back out to `tickets prompt` -- one
    renderer, so a custom harness and the built-in ones cannot drift.
    """
    owner = whoami(a.agent)
    m = current_master(board)
    master = (m["owner"] if m else "the master")
    cos = (m or {}).get("cos") or ""
    role_ctx = role_context(board, owner)
    knowledge_ctx = knowledge_context(board, owner, extra=getattr(a, "extra", "") or "")
    held_first = _task_dominant_extra(board, owner)
    if getattr(a, "cos", False) or (cos and owner == cos and not getattr(a, "master", False)):
        extra = "\n\n".join(x for x in (role_ctx, knowledge_ctx, a.extra or "") if x)
        body = cos_prompt_text(owner, board, os.path.dirname(board), extra)
        return (held_first + "\n\n" + body) if held_first else body
    if getattr(a, "master", False):
        rest = "\n\n".join(x for x in (role_ctx, knowledge_ctx, a.extra or "") if x)
        extra = rest
        obj = _safe(lambda: load_objective(board), {})
        if obj and not obj.get("done"):
            _safe(lambda: _agent_set(board, owner, drive_at=now()), None)
            drive = DRIVE_PROMPT.format(
                objective=obj.get("text", ""), set_by=obj.get("set_by", "?"),
                state=objective_state(obj) or "active",
                exit_criterion=(obj.get("exit_criterion") or "(none — FLAG: add --exit)"),
                status=_safe(lambda: drive_status(board), ""))
            extra = drive + ("\n" + extra if extra else "")
        if cos and owner != cos:
            body = PLANNER_PROMPT.format(agent=owner, board=board, root=os.path.dirname(board), cos=cos,
                                         extra=extra)
        else:
            body = MASTER_PROMPT.format(agent=owner, board=board, root=os.path.dirname(board), extra=extra)
        return (held_first + "\n\n" + body) if held_first else body
    parts = []
    if held_first:
        parts.append(held_first)
    if role_ctx:
        parts.append(role_ctx)
    if knowledge_ctx:
        parts.append(knowledge_ctx)
    brief = agent_brief(board, owner)
    if brief:
        parts.append("Your standing brief (%s):\n%s" % (brief_path(board, owner), brief))
    rec = _safe(lambda: _agent_rec(board, owner), {}) or {}
    obj = _safe(lambda: load_objective(board), {})
    if int(rec.get("drive_every") or 0) > 0 and obj and objective_state(obj) == "active" and objective_exit_ok(obj):
        _safe(lambda: _agent_set(board, owner, drive_at=now()), None)
        parts.append(STANDING_SEAT_PROMPT.format(every=int(rec.get("drive_every")), objective=obj.get("text", "")))
    tctx = ticket_context(board, owner)
    if tctx:
        parts.append("Context attached to your ticket(s):\n" + tctx)
    if a.extra:
        parts.append(a.extra)
    return WORKER_PROMPT.format(agent=owner, board=board, root=os.path.dirname(board), master=master,
                                extra="\n\n".join(parts))


def cmd_brief(a, board):
    """Give an agent, a role, or a ticket context. Agent and ticket briefs are
    shown on every claim, in `tickets prompt`, and in `boot`. `--role` updates
    the T-529 inject source at .tickets/briefs/roles/<role>.md (watch/spawn
    reads the same path). Appends with a timestamp; --file replaces from a
    file; --show prints. Exactly one target: agent name, --role, or --ticket."""
    who = whoami(a.by)
    role = getattr(a, "role", "") or ""
    knowledge_id = (getattr(a, "knowledge_id", "") or "").strip()
    if knowledge_id and not a.ticket:
        sys.exit("brief --knowledge needs --ticket; tickets reference knowledge, they do not store it")
    if knowledge_id and (a.text or a.file or a.agent):
        sys.exit("brief --knowledge cannot be combined with text, --file, or an agent target")
    if role and a.ticket:
        sys.exit("brief: use --role or --ticket, not both")
    if role and a.agent and a.text:
        sys.exit("brief: use --role or an agent name, not both")
    if (role or a.ticket) and not a.text and a.agent:
        a.text, a.agent = a.agent, ""  # `brief --role backend "text"` / `--ticket T-1 "text"`
    if role and a.agent:
        sys.exit("brief: use --role or an agent name, not both")
    if role:
        slug = _safe_role_slug(role)
        path = role_brief_path(board, slug)
        if a.show:
            print(_read_brief_file(path) or "(no role brief for %s)" % slug)
            return
        os.makedirs(os.path.dirname(path), exist_ok=True)
        if a.file:
            with open(a.file) as f:
                body = f.read()
            with open(path, "w") as f:
                f.write(body if body.endswith("\n") else body + "\n")
            print("brief for role %s replaced from %s" % (slug, a.file))
        elif a.text:
            exists = os.path.exists(path)
            with open(path, "a") as f:
                if not exists:
                    f.write("# Role brief for %s\n\n" % slug)
                f.write("- %s [%s] %s\n" % (datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M"), who, a.text))
            print("added to %s" % path)
        else:
            sys.exit("give context text, --file, or --show")
        return
    if a.ticket:
        t = load(board, a.ticket)
        if a.show:
            for n in t.get("notes", []):
                if n.get("kind") == "context":
                    print("- [%s] %s" % (n.get("by", "?"), n["text"]))
            return
        if knowledge_id:
            root = knowledge_root(board, who)
            if _knowledge_inside_board(root, board):
                sys.exit("knowledge graph must live outside the ticket board: %s" % root)
            nodes, edges, errors = _knowledge_records(root) if _knowledge_graph(root) else ([], [], [])
            if errors:
                sys.exit("knowledge graph invalid; run `tickets knowledge validate`")
            node_ids = {r["id"] for r in nodes}
            if knowledge_id not in node_ids:
                if knowledge_id in {r["id"] for r in edges}:
                    sys.exit("knowledge references on tickets must name a node, not edge %r" %
                             knowledge_id)
                sys.exit("no knowledge node %r under %s" % (knowledge_id, root))
            text = "knowledge:" + knowledge_id
        else:
            text = a.text or (open(a.file).read().strip() if a.file else "")
        if not text:
            sys.exit("give context text, --file, or --show")
        t["notes"].append({"by": who, "at": now(), "kind": "context", "text": text})
        save(board, t)
        if t.get("owner") and t.get("status") == "claimed":
            post_message(board, who, "context added to %s: %s" % (t["id"], text[:160]), to=t["owner"], re=t["id"])
        print("context attached to %s%s" % (t["id"], (" (owner %s messaged)" % t["owner"]) if t.get("owner") else ""))
        return
    if not a.agent:
        sys.exit("brief needs an agent name, --role, or --ticket")
    path = brief_path(board, a.agent)
    if a.show:
        print(agent_brief(board, a.agent) or "(no brief for %s)" % a.agent)
        return
    os.makedirs(os.path.dirname(path), exist_ok=True)
    if a.file:
        with open(a.file) as f:
            body = f.read()
        with open(path, "w") as f:
            f.write(body if body.endswith("\n") else body + "\n")
        print("brief for %s replaced from %s" % (a.agent, a.file))
    elif a.text:
        exists = os.path.exists(path)
        with open(path, "a") as f:
            if not exists:
                f.write("# Brief for %s\n\n" % a.agent)
            f.write("- %s [%s] %s\n" % (datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M"), who, a.text))
        print("added to %s" % path)
    else:
        sys.exit("give context text, --file, or --show")
    post_message(board, who, "brief updated for %s: %s" % (a.agent, (a.text or a.file)[:160]), to=a.agent)


# Atman knowledge is a repo-backed graph beside, and deliberately outside, the
# ticket board. Tickets coordinate work. Knowledge nodes preserve evidence,
# decisions and reusable skills after a ticket is closed or a seat disappears.
# The old Markdown catalog remains readable for backwards compatibility, but
# only a validated graph is eligible for bounded prompt inheritance.
_KNOWLEDGE_SLUG_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]*$")
_KNOWLEDGE_NODE_TYPES = frozenset({
    "project", "component", "decision", "artifact", "model_pin", "experiment",
    "failure", "runbook", "skill", "agent_capability",
})
_KNOWLEDGE_EDGE_TYPES = frozenset({
    "depends_on", "supersedes", "produced_by", "failed_because", "verified_by",
    "applies_to", "requires",
})
_KNOWLEDGE_VERIFICATION = frozenset({
    "unverified", "inferred", "observed", "verified", "superseded",
})
_KNOWLEDGE_VERIFY_RANK = {
    "unverified": 0, "inferred": 1, "observed": 2, "verified": 3,
    "superseded": -1,
}
_KNOWLEDGE_DEFAULT_BUDGET = 3600
_KNOWLEDGE_MAX_BUDGET = 6000


def knowledge_root(board=None, owner=None):
    """Repo-tracked graph. Overrides win; never place it below .tickets/."""
    env = (os.environ.get("ATMAN_KNOWLEDGE_DIR") or
           os.environ.get("TICKETS_KNOWLEDGE_DIR") or "").strip()
    if env:
        return os.path.abspath(env)
    if board and owner:
        configured = (load_workforce(board).get(owner, {}) or {}).get("knowledge_dir")
        if configured:
            return os.path.abspath(os.path.expanduser(configured))
    cands = []
    here = _init_cwd_worktree_root()
    if here:
        cands.append(os.path.join(here, "knowledge"))
    main = _repo_root()
    if main:
        p = os.path.join(main, "knowledge")
        if p not in cands:
            cands.append(p)
    if board:
        p = os.path.join(os.path.dirname(os.path.abspath(board)), "knowledge")
        if p not in cands:
            cands.append(p)
    # A checked out old release may only have the v0 Markdown catalog. Keep it
    # readable; it is not treated as inherited graph context.
    legacy = []
    for base in (here, main, os.path.dirname(os.path.abspath(board)) if board else None):
        if base:
            p = os.path.join(base, "docs", "knowledge")
            if p not in legacy:
                legacy.append(p)
    for p in cands:
        if os.path.isdir(p):
            return os.path.abspath(p)
    for p in legacy:
        if os.path.isdir(p):
            return os.path.abspath(p)
    if cands:
        return os.path.abspath(cands[0])
    return os.path.abspath(os.path.join(os.getcwd(), "knowledge"))


def _knowledge_graph(root):
    return os.path.isfile(os.path.join(root, "manifest.json"))


def _knowledge_inside_board(root, board):
    """True when a graph path would collapse durable facts into coordination."""
    if not root or not board:
        return False
    try:
        root = os.path.realpath(root)
        board = os.path.realpath(board)
        return os.path.commonpath([root, board]) == board
    except (OSError, ValueError):
        return True


def _knowledge_path_inside_root(root, path):
    """Reject graph files and directories whose symlinks escape `root`."""
    try:
        root = os.path.realpath(root)
        path = os.path.realpath(path)
        return os.path.commonpath([root, path]) == root
    except (OSError, ValueError):
        return False


def _knowledge_json(path):
    try:
        with open(path, encoding="utf-8") as f:
            value = json.load(f)
    except (OSError, ValueError) as exc:
        return None, "%s: %s" % (path, exc)
    if not isinstance(value, dict):
        return None, "%s: top level must be an object" % path
    return value, ""


def _knowledge_timestamp(value):
    try:
        stamp = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        return stamp if stamp.tzinfo is not None else None
    except (TypeError, ValueError):
        return None


def _knowledge_validate_record(record, kind=None):
    """Return validation errors for one node or edge. Stdlib-only by design."""
    errors = []
    got_kind = record.get("kind")
    if kind and got_kind != kind:
        errors.append("kind must be %s" % kind)
    if got_kind not in ("node", "edge"):
        errors.append("kind must be node or edge")
        return errors
    rid = record.get("id")
    if not isinstance(rid, str) or not _KNOWLEDGE_SLUG_RE.match(rid):
        errors.append("id must be a safe stable slug")
    if got_kind == "node":
        if record.get("type") not in _KNOWLEDGE_NODE_TYPES:
            errors.append("unknown node type %r" % record.get("type"))
        for field in ("title", "summary", "owner"):
            if not isinstance(record.get(field), str) or not record[field].strip():
                errors.append("%s is required" % field)
        for field in ("tags", "applies_to"):
            if not isinstance(record.get(field), list) or not all(
                    isinstance(x, str) and x.strip() for x in record.get(field, [])):
                errors.append("%s must be a list of non-empty strings" % field)
        revision = record.get("revision")
        if not isinstance(revision, int) or revision < 1:
            errors.append("revision must be an integer >= 1")
        canonical = record.get("canonical_key")
        if canonical is not None and (
                not isinstance(canonical, str) or
                not _KNOWLEDGE_SLUG_RE.match(canonical)):
            errors.append("canonical_key must be a safe stable slug")
    else:
        if record.get("type") not in _KNOWLEDGE_EDGE_TYPES:
            errors.append("unknown edge type %r" % record.get("type"))
        for field in ("from", "to", "owner"):
            if not isinstance(record.get(field), str) or not record[field].strip():
                errors.append("%s is required" % field)
        for field in ("from", "to"):
            if isinstance(record.get(field), str) and not _KNOWLEDGE_SLUG_RE.match(record[field]):
                errors.append("%s must be a safe node id" % field)
    source = record.get("source")
    if not isinstance(source, dict) or not isinstance(source.get("ref"), str) or not source["ref"].strip():
        errors.append("source.ref is required")
    future_cutoff = datetime.now(timezone.utc).timestamp() + 300
    for field in ("recorded_at", "last_verified_at"):
        stamp = _knowledge_timestamp(record.get(field))
        if not stamp:
            errors.append("%s must be an ISO-8601 timestamp" % field)
        elif stamp.timestamp() > future_cutoff:
            errors.append("%s cannot be more than 5 minutes in the future" % field)
    if record.get("verification") not in _KNOWLEDGE_VERIFICATION:
        errors.append("verification must be one of %s" % ", ".join(sorted(_KNOWLEDGE_VERIFICATION)))
    confidence = record.get("confidence")
    if not isinstance(confidence, (int, float)) or isinstance(confidence, bool) or not 0 <= confidence <= 1:
        errors.append("confidence must be between 0 and 1")
    stale = record.get("stale_after_days")
    if stale is not None and (not isinstance(stale, int) or isinstance(stale, bool) or stale < 1):
        errors.append("stale_after_days must be null or an integer >= 1")
    return errors


def _knowledge_records(root):
    """Load and validate graph records without ever consulting ticket files."""
    nodes, edges, errors = [], [], []
    manifest_path = os.path.join(root, "manifest.json")
    if not _knowledge_path_inside_root(root, manifest_path):
        manifest = None
        errors.append("%s: symlink escapes the knowledge graph root" % manifest_path)
    else:
        manifest, manifest_error = _knowledge_json(manifest_path)
        if manifest_error:
            errors.append(manifest_error)
    if manifest is not None:
        if manifest.get("schema_version") != 1:
            errors.append("manifest schema_version must be 1")
        budget = manifest.get("context_budget_chars")
        if not isinstance(budget, int) or isinstance(budget, bool) or not 500 <= budget <= _KNOWLEDGE_MAX_BUDGET:
            errors.append("manifest context_budget_chars must be between 500 and %d" %
                          _KNOWLEDGE_MAX_BUDGET)
    for kind, folder in (("node", "nodes"), ("edge", "edges")):
        for path in sorted(glob.glob(os.path.join(root, folder, "*.json"))):
            if not _knowledge_path_inside_root(root, path):
                errors.append("%s: symlink escapes the knowledge graph root" % path)
                continue
            record, error = _knowledge_json(path)
            if error:
                errors.append(error)
                continue
            found = _knowledge_validate_record(record, kind)
            if found:
                errors.extend("%s: %s" % (path, item) for item in found)
                continue
            record = dict(record)
            record["path"] = os.path.abspath(path)
            (nodes if kind == "node" else edges).append(record)
    ids = set()
    for node in nodes:
        if node["id"] in ids:
            errors.append("duplicate node id %s" % node["id"])
        ids.add(node["id"])
    edge_ids = set()
    for edge in edges:
        if edge["id"] in edge_ids:
            errors.append("duplicate edge id %s" % edge["id"])
        edge_ids.add(edge["id"])
        if edge["from"] not in ids:
            errors.append("edge %s references missing from node %s" % (edge["id"], edge["from"]))
        if edge["to"] not in ids:
            errors.append("edge %s references missing to node %s" % (edge["id"], edge["to"]))
    return nodes, edges, errors


def _knowledge_stale(record, at=None):
    if record.get("verification") == "superseded":
        return True
    days = record.get("stale_after_days")
    stamp = _knowledge_timestamp(record.get("last_verified_at"))
    if not days or not stamp:
        return False
    at = at or datetime.now(timezone.utc)
    if at.tzinfo is None:
        at = at.replace(tzinfo=timezone.utc)
    return (at - stamp).total_seconds() > days * 86400


def _knowledge_strings(value):
    if isinstance(value, str):
        return [value]
    if isinstance(value, list):
        out = []
        for item in value:
            out.extend(_knowledge_strings(item))
        return out
    if isinstance(value, dict):
        out = []
        for item in value.values():
            out.extend(_knowledge_strings(item))
        return out
    return []


def _knowledge_terms(text):
    stop = {"the", "and", "for", "with", "from", "this", "that", "into", "your", "you",
            "are", "was", "were", "have", "has", "ticket", "task", "work", "agent"}
    return {x for x in re.findall(r"[a-z0-9][a-z0-9_.-]+", (text or "").lower())
            if len(x) > 2 and x not in stop}


def _knowledge_refs(text):
    """Return exact knowledge:<id> references without prefix collisions."""
    pattern = r"(?<![A-Za-z0-9_.-])knowledge:([A-Za-z0-9][A-Za-z0-9_.-]*)(?![A-Za-z0-9_.-])"
    return {match.lower() for match in re.findall(pattern, text or "", flags=re.IGNORECASE)}


def _knowledge_preference(node):
    return (_KNOWLEDGE_VERIFY_RANK.get(node.get("verification"), -2),
            int(node.get("revision") or 0),
            node.get("last_verified_at") or "",
            float(node.get("confidence") or 0))


def _knowledge_dedup(nodes):
    """One current fact per canonical key; deterministic under concurrent files."""
    picked = {}
    for node in nodes:
        key = node.get("canonical_key") or node["id"]
        old = picked.get(key)
        if old is None or _knowledge_preference(node) > _knowledge_preference(old):
            picked[key] = node
    return list(picked.values())


def _knowledge_query(root, text="", scopes=None, max_nodes=12):
    nodes, edges, errors = _knowledge_records(root)
    if errors:
        return [], [], errors
    nodes = _knowledge_dedup(nodes)
    by_id = {n["id"]: n for n in nodes}
    terms = _knowledge_terms(text)
    scopes = {str(x).lower() for x in (scopes or []) if x}
    referenced = _knowledge_refs(text)
    explicit = {n["id"] for n in nodes if n["id"].lower() in referenced}
    scored = {}
    for node in nodes:
        fields = " ".join(_knowledge_strings({
            "id": node.get("id"), "type": node.get("type"), "title": node.get("title"),
            "summary": node.get("summary"), "tags": node.get("tags"),
            "applies_to": node.get("applies_to"), "data": node.get("data", {}),
        })).lower()
        words = _knowledge_terms(fields)
        score = 0
        if node["id"] in explicit:
            score += 1000
        score += 12 * len(terms & words)
        applies = {str(x).lower() for x in node.get("applies_to", [])}
        if "all" in applies:
            score += 2
        score += 9 * len(scopes & applies)
        if score:
            scored[node["id"]] = score
    # Bring the evidence, failure or runbook connected to a direct match. This
    # is the useful part of a graph: a failure can carry its corrective command
    # without copying the command into every ticket or brief.
    direct = set(scored)
    direct_scores = dict(scored)
    for edge in edges:
        if edge["from"] in direct and edge["to"] in by_id:
            scored[edge["to"]] = max(scored.get(edge["to"], 0),
                                     direct_scores[edge["from"]] - 1)
        if edge["to"] in direct and edge["from"] in by_id:
            scored[edge["from"]] = max(scored.get(edge["from"], 0),
                                       direct_scores[edge["to"]] - 1)
    type_bias = {"failure": 5, "runbook": 4, "skill": 3, "decision": 2,
                 "artifact": 1, "experiment": 1}
    ranked = sorted((by_id[nid] for nid in scored), key=lambda n: (
        -(scored[n["id"]] + type_bias.get(n.get("type"), 0)),
        _knowledge_stale(n), -_knowledge_preference(n)[0], n["id"]))[:max_nodes]
    selected = {n["id"] for n in ranked}
    selected_edges = [e for e in edges if e["from"] in selected and e["to"] in selected]
    return ranked, selected_edges, []


def _knowledge_budget(root, requested=None):
    budget = requested
    if budget is None:
        manifest, _ = _knowledge_json(os.path.join(root, "manifest.json"))
        budget = (manifest or {}).get("context_budget_chars", _KNOWLEDGE_DEFAULT_BUDGET)
    try:
        budget = int(budget)
    except (TypeError, ValueError):
        budget = _KNOWLEDGE_DEFAULT_BUDGET
    return max(500, min(budget, _KNOWLEDGE_MAX_BUDGET))


def _knowledge_render(nodes, edges, max_chars):
    if not nodes:
        return ""
    relation = {}
    for edge in edges:
        relation.setdefault(edge["from"], []).append("%s→%s" % (edge["type"], edge["to"]))
    lines = ["Inherited knowledge (repo-backed; open a source before changing a fact):"]
    for node in nodes:
        age = "STALE" if _knowledge_stale(node) else node["verification"].upper()
        summary = (" ".join(node["title"].split()) + ": " +
                   " ".join(node["summary"].split()))[:330]
        source = node.get("source", {}).get("ref", "")[:170]
        rels = ",".join(sorted(relation.get(node["id"], [])))[:170]
        line = "- knowledge:%s [%s; confidence %.2f] %s" % (
            node["id"], age, float(node["confidence"]), summary)
        if rels:
            line += " relations=" + rels
        if source:
            line += " source=" + source
        lines.append(line[:620])
    text = "\n".join(lines)
    if len(text) <= max_chars:
        return text
    kept = [lines[0]]
    marker = "\n...(knowledge budget reached; run `tickets knowledge query ...`)"
    for line in lines[1:]:
        candidate = "\n".join(kept + [line]) + marker
        if len(candidate) > max_chars:
            # A single verbose fact should still be useful under the minimum
            # budget. Fit a visibly truncated line instead of returning only a
            # budget notice.
            if len(kept) == 1:
                room = max_chars - len("\n".join(kept)) - len(marker) - 2
                if room > 40:
                    kept.append(line[:room - 3] + "...")
            break
        kept.append(line)
    return "\n".join(kept) + marker


def knowledge_context(board, owner, extra="", max_chars=None):
    """Compact task/seat inheritance shared by every prompt-file harness."""
    root = knowledge_root(board, owner)
    if _knowledge_inside_board(root, board):
        return "Knowledge graph configuration invalid: graph must live outside the ticket board."
    if not _knowledge_graph(root):
        return ""
    rec = _safe(lambda: _agent_rec(board, owner), {}) or {}
    wf = _safe(lambda: load_workforce(board), {}).get(owner, {}) or {}
    roles = roles_for(board, owner) or []
    tickets = [t for t in load_all(board)
               if t.get("owner") == owner and t.get("status") in ("claimed", "review")]
    chunks = [extra, " ".join(roles), " ".join(wf.get("can", [])),
              wf.get("harness") or wf.get("tool", ""), rec.get("note", "")]
    scopes = list(roles) + list(wf.get("can", [])) + [wf.get("harness") or wf.get("tool", "")]
    for ticket in tickets:
        chunks.extend([ticket.get("id", ""), ticket.get("title", ""), ticket.get("body", ""),
                       ticket.get("role", ""), " ".join(ticket.get("needs", []))])
        for note in ticket.get("notes", []):
            if note.get("kind") == "context":
                chunks.append(note.get("text", ""))
    nodes, edges, errors = _knowledge_query(root, " ".join(chunks), scopes=scopes)
    if errors:
        return "Knowledge graph invalid; run `tickets knowledge validate`."
    return _knowledge_render(nodes, edges, _knowledge_budget(root, max_chars))


def _parse_knowledge_frontmatter(text):
    """Minimal YAML-ish header. Stdlib only; unknown keys kept as strings."""
    meta, body = {}, text
    if not text.startswith("---"):
        return meta, body
    end = text.find("\n---", 3)
    if end < 0:
        return meta, body
    raw = text[3:end].strip()
    body = text[end + 4:].lstrip("\n")
    for line in raw.splitlines():
        if ":" not in line:
            continue
        key, val = line.split(":", 1)
        key = key.strip().lower()
        val = val.strip()
        if key == "tags":
            val = val.strip("[]")
            meta[key] = [x.strip().strip("'\"") for x in val.split(",") if x.strip()]
        else:
            meta[key] = val.strip("'\"")
    return meta, body


def _knowledge_docs(root):
    """Markdown under the knowledge tree. Paths must stay inside root."""
    root = os.path.realpath(root)
    out = []
    if not os.path.isdir(root):
        return out
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if not d.startswith(".")]
        for name in sorted(filenames):
            if not name.endswith(".md") or name.startswith("."):
                continue
            path = os.path.realpath(os.path.join(dirpath, name))
            if path != root and not path.startswith(root + os.sep):
                continue
            try:
                with open(path, encoding="utf-8", errors="replace") as f:
                    text = f.read()
            except OSError:
                continue
            meta, body = _parse_knowledge_frontmatter(text)
            rel = os.path.relpath(path, root)
            slug = (meta.get("id") or os.path.splitext(os.path.basename(path))[0]).strip()
            if not _KNOWLEDGE_SLUG_RE.match(slug):
                continue
            out.append({
                "id": slug,
                "title": (meta.get("title") or slug).strip(),
                "tags": list(meta.get("tags") or []),
                "path": path,
                "rel": rel.replace("\\", "/"),
                "body": body,
            })
    out.sort(key=lambda d: d["rel"])
    seen = {}
    unique = []
    for d in out:
        if d["id"] in seen:
            continue
        seen[d["id"]] = True
        unique.append(d)
    return unique


def _find_knowledge_doc(root, slug):
    if not _KNOWLEDGE_SLUG_RE.match(slug or ""):
        return None
    for d in _knowledge_docs(root):
        if d["id"] == slug:
            return d
    return None


def cmd_knowledge(a, board):
    """Query or author the separate repo-backed knowledge graph."""
    root = knowledge_root(board, whoami(getattr(a, "agent", "") or ""))
    if _knowledge_inside_board(root, board):
        sys.exit("knowledge graph must live outside the ticket board: %s" % root)
    sub = getattr(a, "knowledge_cmd", None) or "list"
    tag = (getattr(a, "tag", "") or "").strip()
    if _knowledge_graph(root):
        nodes, edges, errors = _knowledge_records(root)
        if sub == "validate":
            if errors:
                for error in errors:
                    print("ERROR " + error)
                sys.exit("knowledge graph invalid: %d error(s)" % len(errors))
            print("knowledge graph valid: %d node(s), %d edge(s) at %s" %
                  (len(nodes), len(edges), root))
            return
        # Never answer from a partial graph. A malformed record may carry the
        # corrective runbook or superseding evidence for an otherwise valid
        # fact; silently omitting it would turn corruption into bad advice.
        if errors and sub in ("list", "show", "query"):
            sys.exit("knowledge graph invalid; run `tickets knowledge validate`")
        if sub == "list":
            node_type = (getattr(a, "node_type", "") or "").strip()
            stale_filter = bool(getattr(a, "stale", False))
            rows = _knowledge_dedup(nodes)
            if tag:
                rows = [n for n in rows if tag in n.get("tags", [])]
            if node_type:
                rows = [n for n in rows if n.get("type") == node_type]
            if stale_filter:
                rows = [n for n in rows if _knowledge_stale(n)]
            rows.sort(key=lambda n: (n["type"], n["id"]))
            payload = [{
                "id": n["id"], "type": n["type"], "title": n["title"],
                "tags": n["tags"], "verification": n["verification"],
                "confidence": n["confidence"], "stale": _knowledge_stale(n),
                "source": n["source"], "path": n["path"],
            } for n in rows]
            if getattr(a, "json", False):
                print(json.dumps(payload, indent=2))
                return
            for row in payload:
                state = "STALE" if row["stale"] else row["verification"].upper()
                print("%-30s %-16s %-10s %s" %
                      (row["id"][:30], row["type"][:16], state, row["title"][:72]))
            print("\n%d current fact(s); graph=%s" % (len(payload), root))
            return
        if sub == "show":
            slug = (getattr(a, "slug", "") or "").strip()
            if not _KNOWLEDGE_SLUG_RE.match(slug):
                sys.exit("knowledge slug %r is not a safe name" % slug)
            for record in nodes + edges:
                if record["id"] == slug:
                    payload = {k: v for k, v in record.items() if k != "path"}
                    payload["stale"] = _knowledge_stale(record)
                    payload["path"] = record["path"]
                    print(json.dumps(payload, indent=2))
                    return
            sys.exit("no knowledge record %r under %s" % (slug, root))
        if sub == "query":
            query = (getattr(a, "query", "") or "").strip()
            owner = whoami(getattr(a, "agent", "") or "")
            scopes = list(getattr(a, "role", []) or []) + list(getattr(a, "cap", []) or [])
            if getattr(a, "harness", ""):
                scopes.append(a.harness)
            if getattr(a, "ticket", ""):
                t = load(board, a.ticket)
                query = " ".join([query, t.get("id", ""), t.get("title", ""),
                                  t.get("body", ""), t.get("role", ""),
                                  " ".join(t.get("needs", []))])
                for note in t.get("notes", []):
                    if note.get("kind") == "context":
                        query += " " + note.get("text", "")
            if owner:
                wf = load_workforce(board).get(owner, {}) or {}
                scopes.extend(roles_for(board, owner) or [])
                scopes.extend(wf.get("can", []))
                scopes.append(wf.get("harness") or wf.get("tool", ""))
                for ticket in load_all(board):
                    if ticket.get("owner") != owner or ticket.get("status") not in ("claimed", "review"):
                        continue
                    query += " " + " ".join([
                        ticket.get("id", ""), ticket.get("title", ""), ticket.get("body", ""),
                        ticket.get("role", ""), " ".join(ticket.get("needs", [])),
                    ])
                    for note in ticket.get("notes", []):
                        if note.get("kind") == "context":
                            query += " " + note.get("text", "")
            selected, selected_edges, query_errors = _knowledge_query(
                root, query, scopes=scopes, max_nodes=getattr(a, "max_nodes", 12))
            if query_errors:
                sys.exit("knowledge graph invalid; run `tickets knowledge validate`")
            if getattr(a, "json", False):
                print(json.dumps({
                    "query": query, "scopes": sorted(set(x for x in scopes if x)),
                    "nodes": [{**{k: v for k, v in n.items() if k != "path"},
                               "stale": _knowledge_stale(n), "path": n["path"]}
                              for n in selected],
                    "edges": [{k: v for k, v in e.items() if k != "path"}
                              for e in selected_edges],
                }, indent=2))
                return
            rendered = _knowledge_render(selected, selected_edges,
                                         _knowledge_budget(root, getattr(a, "max_chars", None)))
            print(rendered or "no relevant knowledge found")
            return
        if sub in ("add", "update"):
            source_path = os.path.abspath(os.path.expanduser(a.file))
            record, error = _knowledge_json(source_path)
            if error:
                sys.exit("NO CHANGE WAS MADE: " + error)
            found = _knowledge_validate_record(record)
            if found:
                sys.exit("NO CHANGE WAS MADE: " + "; ".join(found))
            folder = "nodes" if record["kind"] == "node" else "edges"
            target = os.path.join(root, folder, record["id"] + ".json")
            if not _knowledge_path_inside_root(root, os.path.dirname(target)):
                sys.exit("NO CHANGE WAS MADE: knowledge destination escapes graph root")
            current, _ = _knowledge_json(target)
            if sub == "add" and current is not None:
                sys.exit("NO CHANGE WAS MADE: knowledge record %s already exists" % record["id"])
            if sub == "update" and current is None:
                sys.exit("NO CHANGE WAS MADE: knowledge record %s does not exist" % record["id"])
            if sub == "update" and current.get("kind") != record["kind"]:
                sys.exit("NO CHANGE WAS MADE: kind cannot change")
            if sub == "update" and record["kind"] == "node":
                record["revision"] = int(current.get("revision") or 1) + 1
            os.makedirs(os.path.dirname(target), exist_ok=True)
            tmp = target + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(record, f, indent=2, sort_keys=True)
                f.write("\n")
            os.replace(tmp, target)
            # Validate the full graph after a staged write. Roll back only the
            # new bytes, preserving the old record exactly on update failure.
            _, _, after_errors = _knowledge_records(root)
            if after_errors:
                if current is None:
                    os.unlink(target)
                else:
                    with open(tmp, "w", encoding="utf-8") as f:
                        json.dump(current, f, indent=2, sort_keys=True)
                        f.write("\n")
                    os.replace(tmp, target)
                sys.exit("NO CHANGE WAS MADE: " + "; ".join(after_errors))
            print("%s knowledge %s at %s" % ("added" if sub == "add" else "updated",
                                               record["id"], target))
            return
        sys.exit("knowledge: list | show | query | add | update | validate")

    if sub not in ("list", "show"):
        sys.exit("no knowledge graph at %s; expected manifest.json" % root)
    # Legacy v0 Markdown catalog: read-only, never injected.
    if sub == "list":
        if not os.path.isdir(root):
            print("no team knowledge tree at %s" % root)
            print("expected docs/knowledge/ (repo-tracked markdown). "
                  "See docs/knowledge/README.md")
            return
        docs = _knowledge_docs(root)
        if tag:
            docs = [d for d in docs if tag in (d.get("tags") or [])]
        if getattr(a, "json", False):
            payload = [{k: d[k] for k in ("id", "title", "tags", "path", "rel")}
                       for d in docs]
            print(json.dumps(payload, indent=2))
            return
        if not docs:
            print("no knowledge docs%s at %s" % ((" tagged %s" % tag) if tag else "", root))
            return
        for d in docs:
            tags = ",".join(d["tags"]) if d["tags"] else "-"
            print("%-16s %-36s [%s]  docs/knowledge/%s" % (
                d["id"], d["title"][:36], tags, d["rel"]))
        print("")
        print("%d legacy doc(s); create knowledge/manifest.json for graph inheritance" % len(docs))
        return
    if sub == "show":
        slug = (getattr(a, "slug", "") or "").strip()
        if not slug:
            sys.exit("knowledge show needs a doc id")
        if not _KNOWLEDGE_SLUG_RE.match(slug):
            sys.exit("knowledge slug %r is not a safe name" % slug)
        doc = _find_knowledge_doc(root, slug)
        if not doc:
            sys.exit("no knowledge doc %r under %s" % (slug, root))
        print("# %s" % doc["path"])
        print(doc["body"].rstrip())
        print()
        return
    sys.exit("knowledge: list | show <id>")


def utilization(board, tickets=None, hours=24, live=None):
    """Per-agent throughput and load over the window, plus sprint burn."""
    tickets = tickets if tickets is not None else load_all(board)
    cutoff = datetime.now(timezone.utc).timestamp() - hours * 3600
    agents = {r["owner"]: r for r in load_agents(board)}
    names = sorted(set(list(agents) + [t.get("owner") for t in tickets if t.get("owner")]))
    rows = []
    for n in names:
        if not n or n.startswith("agent-"):
            continue
        mine = [t for t in tickets if t.get("owner") == n]
        done = [t for t in mine if t["status"] == "done" and t.get("done_at")]
        recent = [t for t in done if _safe(lambda: datetime.strptime(t["done_at"], "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc).timestamp(), 0) >= cutoff]
        cycles = [timing(t)["active"] for t in recent if timing(t)["active"] is not None]
        active_hours = sum(cycles)
        held = [t for t in mine if t["status"] in ("claimed", "review")]
        r = agents.get(n, {})
        seen = hours_since(r["seen"]) if r.get("seen") else None
        # DOWN is no longer reachable only through a hand-typed `tickets limit`
        # record: an agent whose sessions cannot start is down whether or not
        # anyone remembered to say so (T-237).
        # `live` is this refresh's already-computed states. Recomputing here
        # would double every transcript read on a dash that refreshes in place.
        # It is passed in per refresh and never cached across refreshes: a
        # stale liveness cache is precisely the bug this ticket exists to fix.
        lv = (live.get(n) if live is not None
              else (_safe(lambda: agent_liveness(board, r, list(agents.values())), {}) if r else {})) or {}
        state = ("DOWN" if lv.get("state") in ("limited", "dead")
                 else ("busy" if any(t["status"] == "claimed" for t in held) else "idle"))
        rows.append({
            "agent": n, "state": state, "done": len(recent), "done_total": len(done),
            "avg_cycle_h": (sum(cycles) / len(cycles)) if cycles else None,
            "active_h": active_hours, "util_pct": min(100.0, 100.0 * active_hours / hours) if hours else 0.0,
            "in_flight": len([t for t in held if t["status"] == "claimed"]),
            "in_review": len([t for t in held if t["status"] == "review"]),
            "seen_h": seen,
        })
    cur = active_sprint(board)
    burn = None
    if cur:
        mine = [t for t in tickets if t.get("sprint") == cur["id"]]
        d, n, c, b = progress(mine)
        started = hours_since(cur["start"]) if cur.get("start") else None
        burn = {"sprint": cur["id"], "done": d, "total": n, "in_flight": c, "blocked": b,
                "elapsed_h": started, "done_per_day": (d / (started / 24.0)) if started and started > 1 else None,
                "eta_days": ((n - d) / (d / (started / 24.0))) if started and started > 1 and d else None}
    return rows, burn


def cmd_util(a, board):
    rows, burn = utilization(board, hours=a.hours)
    if a.json:
        print(json.dumps({"window_hours": a.hours, "agents": rows, "sprint": burn}, indent=2))
        return
    print("Utilization, last %dh" % a.hours)
    print("  %-14s %-5s %5s %8s %8s %6s %6s %6s %s" % ("agent", "state", "done", "avg cyc", "active", "util%", "wip", "review", "seen"))
    for r in rows:
        print("  %-14s %-5s %5d %8s %8s %5.0f%% %6d %6d %s" % (
            r["agent"][:14], r["state"], r["done"], fmt_hours(r["avg_cycle_h"]) if r["avg_cycle_h"] is not None else "-",
            fmt_hours(r["active_h"]), r["util_pct"], r["in_flight"], r["in_review"],
            (fmt_hours(r["seen_h"]) + " ago") if r["seen_h"] is not None else "never"))
    if burn:
        print("Sprint %s: %d/%d done, %d in flight, %d blocked, elapsed %s, %s/day, ETA %s" % (
            burn["sprint"], burn["done"], burn["total"], burn["in_flight"], burn["blocked"],
            fmt_hours(burn["elapsed_h"]) if burn["elapsed_h"] is not None else "?",
            ("%.1f" % burn["done_per_day"]) if burn["done_per_day"] else "?",
            ("%.1f days" % burn["eta_days"]) if burn["eta_days"] else "?"))
    print("util% = hours of measured ticket cycle time completed in the window / window; 'active' is that sum.")


def cmd_dash(a, board):
    """Master's one-screen view, refreshing in place: sprint, in flight with
    staleness, review queue, per-agent pending, health, last messages."""
    import time as _time
    while True:
      with _shared_watch_table():
        tickets = load_all(board)
        cur = active_sprint(board)
        m = current_master(board)
        lines = []
        lines.append("=" * 78)
        lines.append("BOARD %s   %s   master: %s" % (os.path.basename(os.path.dirname(board)), now(),
                                                    m["owner"] if m else "nobody"))
        if cur:
            mine = [t for t in tickets if t.get("sprint") == cur["id"]]
            d, n, c, b = progress(mine)
            lines.append("sprint %s %s  %d in flight, %d blocked | %s" % (cur["id"], bar(d, n), c, b, cur.get("goal", "")[:60]))
        lines.append("-" * 78)
        lines.append("IN FLIGHT")
        for t in tickets:
            if t["status"] == "claimed":
                tm = timing(t)
                su = tm["since_update"]
                flag = "!!" if su is not None and su * 60 > UPDATE_EVERY_MIN * 2 else ("! " if su is not None and su * 60 > UPDATE_EVERY_MIN else "  ")
                lines.append(" %s %-6s @%-13s %-40s upd %s" % (flag, t["id"], (t.get("owner") or "?")[:13], t["title"][:40], fmt_hours(su)))
        rq = [t for t in tickets if t["status"] == "review"]
        lines.append("REVIEW QUEUE (%d)%s" % (len(rq), ": tickets merge" if rq else ""))
        for t in rq:
            lines.append("    %-6s @%-13s %-40s %s%s" % (t["id"], (t.get("owner") or "?")[:13], t["title"][:40], t.get("commit", "")[:24], (" PR " + t["pr"]) if t.get("pr") else ""))
        lines.append("AGENTS")
        agents = load_agents(board)
        names = sorted(set([r["owner"] for r in agents] + [k for k, v in load_roles(board).items() if v]))
        live = {}
        for nme in names:
            r = next((x for x in agents if x["owner"] == nme), {})
            live[nme] = _safe(lambda r=r, nme=nme: agent_liveness(board, dict(r, owner=nme), agents), {})
        for nme in names:
            r = next((x for x in agents if x["owner"] == nme), {})
            lv = live[nme]
            state, mark = (lv.get("state") or "unknown"), liveness_mark(lv)
            if state in ("limited", "dead"):
                # DOWN whether a human said so (!) or the tool worked it out
                # (~) -- but the reader can still tell which, because those are
                # different levels of evidence.
                lines.append("  %-13s DOWN%s (%s)" % (nme[:13], mark, _clip(lv.get("detail") or state, 52)))
                continue
            p = pending_work(board, nme)
            keys = [k for k in p if k != "broadcasts"]
            wc = _watcher_count(nme, board)
            wnote = ("watchers=%d !! " % wc) if wc != 1 else ""
            lines.append("  %-13s %-8s%s %-9s %s%s" % (
                nme[:13], state, mark,
                ("seen " + fmt_hours(hours_since(r["seen"]))) if r.get("seen") else "never",
                wnote, ("pending: " + ", ".join(keys)) if keys else (lv.get("detail") or "")[:40]))
        rows, burn = utilization(board, tickets, hours=24, live=live)
        live = [r for r in rows if r["state"] != "DOWN"]
        lines.append("UTILIZATION 24h  (%d live agents, %d down)" % (len(live), len(rows) - len(live)))
        for r in sorted(live, key=lambda r: -r["done"])[:8]:
            lines.append("  %-13s %-4s done %2d  avg %-5s  wip %d  rev %d  util %3.0f%%" % (
                r["agent"][:13], r["state"], r["done"], fmt_hours(r["avg_cycle_h"]) if r["avg_cycle_h"] is not None else "-",
                r["in_flight"], r["in_review"], r["util_pct"]))
        if burn and burn.get("done_per_day"):
            lines.append("  burn: %.1f tickets/day, ETA %s" % (burn["done_per_day"], ("%.1f days" % burn["eta_days"]) if burn.get("eta_days") else "-"))
        issues = health(board, tickets)
        crit = [i for i in issues if i[0] in ("CRIT", "WARN")]
        lines.append("HEALTH  %d issues (%d need action)" % (len(issues), len(crit)))
        for sev, msg, fix in crit[:6]:
            lines.append("  [%s] %s" % (sev, msg[:70]))
        lines.append("MESSAGES")
        for mm in load_messages(board)[-a.messages:]:
            lines.append("  " + fmt_msg(mm)[:76])
        lines.append("-" * 78)
        lines.append("tickets assign <id> --owner X | tickets brief X \"...\" | tickets msg \"...\" --to X | tickets merge | tickets reopen <id>")
        if not a.once:
            sys.stdout.write("\033[2J\033[H")
        print("\n".join(lines))
        if a.once:
            return
        try:
            _time.sleep(max(3, a.every))
        except KeyboardInterrupt:
            return


def _record_stop_block(board, owner):
    """Rate-limit continuations: returns False when the hourly cap is reached.

    Counting and appending both happen inside the lock. Read the count outside
    it and two stop hooks firing together each see the same pre-image, each
    decide they are under the cap, and the agent gets more continuations than
    the cap allows -- the same read-modify-write hole as the limit/heartbeat
    race, cashed out as a rate limit that does not hold.
    """
    cutoff = datetime.now(timezone.utc).timestamp() - 3600
    capped = []

    def mutate(rec):
        stamps = [s for s in rec.get("stop_blocks", []) if _safe(lambda: datetime.strptime(
            s, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc).timestamp(), 0) > cutoff]
        if len(stamps) >= STOP_HOOK_MAX_PER_HOUR:
            capped.append(True)
            return False  # over the cap: abort without writing
        stamps.append(now())
        rec["stop_blocks"] = stamps

    # Never raises: this is a hook, and a write failure must not stop the turn.
    _safe(lambda: _agent_update(board, owner, mutate), None)
    return not capped


def cmd_stop_hook(a, board):
    """Claude Code `Stop` hook: keep the turn alive while this agent still has work.

    Never raises, always exits 0. Lets the session stop when: no TICKET_AGENT,
    TICKETS_STOP_HOOK=off, the event says stop_hook_active (we already continued
    once this turn), the agent recorded a usage limit, only broadcasts are
    unread, or the hourly cap of continuations is reached.
    """
    try:
        raw = sys.stdin.read() if not sys.stdin.isatty() else ""
        event = json.loads(raw or "{}")
        if not isinstance(event, dict):
            event = {}
    except Exception:  # noqa: BLE001
        event = {}
    owner = os.environ.get("TICKET_AGENT") or ""
    if (not owner or os.environ.get("TICKETS_STOP_HOOK", "").lower() in ("off", "0", "false")
            or event.get("stop_hook_active")):
        print("{}")
        return
    p = _safe(lambda: pending_work(board, owner), {})
    p.pop("drive", None)  # the heartbeat wakes the watcher; it must never pin an interactive turn open
    if not actionable(p) or not _record_stop_block(board, owner):
        print("{}")
        return
    detail = "; ".join("%s=%s" % (k, v if not isinstance(v, list) else " | ".join(v))
                       for k, v in p.items() if k != "broadcasts")
    reason = ("Ticket board still has work for %s: %s. Run `TICKET_AGENT=%s tickets inbox`, then "
              "continue your ticket (`tickets mine`) or claim one (`tickets next`). If you are truly "
              "done or blocked, say so with `tickets msg` and stop." % (owner, detail, owner))
    print(json.dumps({"decision": "block", "reason": reason[:1500]}))


def _pid_alive(pid):
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False


def _watch_lock(board, owner):
    """One watcher per agent name. Returns the lock path or None if another runs."""
    os.makedirs(agents_dir(board), exist_ok=True)
    path = os.path.join(agents_dir(board), owner + ".watch.pid")
    for _ in range(2):
        try:
            fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            os.write(fd, str(os.getpid()).encode())
            os.close(fd)
            return path
        except FileExistsError:
            try:
                with open(path) as f:
                    other = int(f.read().strip() or "0")
            except (OSError, ValueError):
                other = 0
            if other and _pid_alive(other):
                if other == os.getpid():
                    return path  # T-427: execv keeps this pid; lock is still ours
                return None
            try:
                os.unlink(path)  # stale lock from a dead watcher
            except OSError:
                return None
    return None


def reclaim_stale_watch_lock(board, owner):
    """Inspect and, when safe, reclaim one dead watch pid file."""
    path = os.path.join(agents_dir(board), owner + ".watch.pid")
    existed = os.path.lexists(path)
    lock = _watch_lock(board, owner)
    if lock is None:
        return {"state": "live", "detail": "watcher is running"}
    try:
        with open(lock) as f:
            ours = int((f.read() or "0").strip() or 0) == os.getpid()
        if ours:
            os.unlink(lock)
    except (OSError, ValueError):
        pass
    return {"state": "reclaimed" if existed else "clear",
            "detail": "stale watcher lock reclaimed" if existed else "no watcher lock"}


def _watch_run_capped(cmd, cwd, env, log_path, timeout_s, cap_bytes,
                      on_beat=None, beat_secs=None, should_stop=None):
    """Run cmd with stdout+stderr teed into log_path, capped at cap_bytes for
    this run alone -- a single verbose run must not be able to blow past the
    log's rotation budget before the between-run rotation in cmd_watch's
    log() ever gets a chance to fire. Keeps draining the pipe past the cap so
    the child never blocks on a full pipe buffer. Returns (rc, timed_out);
    rc is 124 on timeout, matching the previous subprocess.call behavior.

    `on_beat` is called every `beat_secs` for as long as the child is running
    (T-237). It is a separate ticker thread rather than a hook on the output
    pump because a session that is thinking writes nothing for minutes, and a
    heartbeat that only fires when the child speaks reports silence as death.
    """
    import subprocess
    import threading

    proc = subprocess.Popen(cmd, shell=True, cwd=cwd, env=env,
                             stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    state = {"written": 0, "capped": False}

    def pump():
        def note_capped(lf):
            if not state["capped"]:
                lf.write("\n[watch log: run output capped at %d bytes]\n" % cap_bytes)
                lf.flush()
                state["capped"] = True

        try:
            with open(log_path, "a") as lf:
                for chunk in iter(lambda: proc.stdout.read(65536), b""):
                    if state["written"] >= cap_bytes:
                        note_capped(lf)
                        continue
                    take = chunk[: cap_bytes - state["written"]]
                    lf.write(take.decode("utf-8", "replace"))
                    state["written"] += len(take)
                    if len(take) < len(chunk):
                        # this single chunk already carried past the cap --
                        # there may be no further chunk to trigger the note.
                        note_capped(lf)
        except OSError:
            pass

    pump_thread = threading.Thread(target=pump, daemon=True)
    pump_thread.start()

    beat_stop = threading.Event()
    beat_thread = None
    try:
        if on_beat:
            interval = beat_secs or RUN_HEARTBEAT_SECS

            def beat():
                while not beat_stop.wait(interval):
                    _safe(on_beat, None)

            # Keep setup inside the InterruptedError boundary. SIGTERM is
            # delivered to the main thread at any bytecode; wrapping this in
            # _safe would swallow the signal handler's InterruptedError and
            # leave the child running until its timeout.
            try:
                on_beat()  # stamp the start of the run, do not wait a tick
            except InterruptedError:
                raise
            except Exception:
                pass
            beat_thread = threading.Thread(target=beat, daemon=True)
            beat_thread.start()

        import time as _time
        deadline = None if timeout_s is None else (_time.monotonic() + float(timeout_s))
        rc = None
        while True:
            if should_stop and should_stop():
                raise InterruptedError()
            remaining = None if deadline is None else (deadline - _time.monotonic())
            if remaining is not None and remaining <= 0:
                raise subprocess.TimeoutExpired(proc.args, timeout_s)
            slice_s = WATCH_STOP_SLICE if remaining is None else min(WATCH_STOP_SLICE, remaining)
            try:
                rc = proc.wait(timeout=slice_s)
                break
            except subprocess.TimeoutExpired:
                continue
        timed_out = False
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait()
        rc = 124
        timed_out = True
    except InterruptedError:
        try:
            proc.terminate()
            proc.wait(timeout=5)
        except Exception:
            try:
                proc.kill()
                proc.wait()
            except Exception:
                pass
        raise
    finally:
        beat_stop.set()
        # Join, do not just signal: a beat already inside its write would
        # otherwise land AFTER the caller's run-end record and resurrect
        # active=True on a finished run -- the exact class of lie this
        # heartbeat exists to remove.
        if beat_thread is not None:
            beat_thread.join(timeout=10)
    pump_thread.join(timeout=5)
    return rc, timed_out


# --- T-427: watch idle-boundary self-execv (byte-identical in tickets.py and cli.py) ---
# A tickets-releases/<sha> dir is safe to delete only when (1) it is not the
# live shim target and (2) no watch process is executing that dir. T-388 kept
# 8f513fe and 1c8335b as rollback pins even after cutover. After this hop,
# idle loops move themselves; leftover recycle tickets are only for processes
# that never reach this boundary (stuck in a run).


def _t427_release_dir(path):
    real = os.path.realpath(path or "")
    parts = real.split(os.sep)
    try:
        i = parts.index("tickets-releases")
    except ValueError:
        return ""
    if i + 1 >= len(parts) or not parts[i + 1]:
        return ""
    return os.sep.join(parts[: i + 2])


def _t427_shim_tickets(shim_path):
    """Path the live shim execv's into, or None if unreadable, or '' if unknown."""
    import re as _re
    try:
        raw = open(shim_path, "rb").read()
    except OSError:
        return None
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        text = ""
    m = _re.search(r"\[sys\.executable,\s*(['\"])(.+?)\1\]", text)
    if m:
        return m.group(2)
    real = os.path.realpath(shim_path)
    if os.path.isfile(real):
        return real
    return ""


def _t427_verified_sha(tickets_py):
    """Commit sha if tickets_py is a verified release, else ''."""
    root = os.path.dirname(os.path.realpath(tickets_py))
    manifest = os.path.join(root, "release.json")
    try:
        with open(manifest) as source:
            release = json.load(source)
        for name in ("tickets.py", "ticket_coordination.py", "board_backup.py"):
            path = os.path.join(root, name)
            recorded = release["files"][name]
            expected_sha, expected_size = (
                (recorded["sha256"], recorded["size"]) if isinstance(recorded, dict)
                else (recorded, None))
            if expected_size is not None and os.stat(path).st_size != expected_size:
                return ""
            with open(path, "rb") as source:
                actual = hashlib.sha256(source.read()).hexdigest()
            if actual != expected_sha:
                return ""
        return release.get("commit") or ""
    except (OSError, ValueError, KeyError, TypeError):
        return ""


def _t427_idle_shim_path(executing_file):
    """Shim for idle-boundary hop, or '' when hop must not run."""
    if not _t427_release_dir(executing_file):
        return ""
    live = os.environ.get("TICKETS_LIVE_SHIM")
    if live:
        return live
    if os.environ.get("PYTEST_CURRENT_TEST"):
        return ""
    return os.path.expanduser("~/.claude/tools/tickets.py")


def watch_idle_reexec(executing_file, shim_path, argv, log, pid_path=None,
                      executable=None, execv=None):
    """If idle and the live shim points at a different verified release, execv.

    Returns False when this process should keep running. Does not return on hop.
    Caller invokes only at the idle boundary so an in-flight child is never
    severed. Never hops onto an unverified release. Unreadable/missing shim:
    log once, stay put, do not busy-loop.
    """
    warned = watch_idle_reexec._warned
    current = _t427_release_dir(executing_file)
    if not current or not shim_path:
        return False
    target_py = _t427_shim_tickets(shim_path)
    if target_py is None:
        key = "unreadable:" + shim_path
        if key not in warned:
            warned.add(key)
            log("release shim unreadable; staying on %s" % os.path.basename(current))
        return False
    if not target_py:
        key = "missing:" + shim_path
        if key not in warned:
            warned.add(key)
            log("release shim target missing; staying on %s" % os.path.basename(current))
        return False
    new_dir = _t427_release_dir(target_py)
    if not new_dir:
        key = "not-release:" + target_py
        if key not in warned:
            warned.add(key)
            log("release shim is not a tickets-releases dir; staying on %s"
                % os.path.basename(current))
        return False
    if os.path.dirname(current) != os.path.dirname(new_dir):
        return False
    if os.path.realpath(current) == os.path.realpath(new_dir):
        return False
    if not os.path.isfile(target_py):
        key = "missing-py:" + target_py
        if key not in warned:
            warned.add(key)
            log("release target missing; staying on %s" % os.path.basename(current))
        return False
    new_sha = _t427_verified_sha(target_py)
    if not new_sha:
        key = "unverified:" + new_dir
        if key not in warned:
            warned.add(key)
            log("release %s is not a verified release; staying on %s"
                % (os.path.basename(new_dir), os.path.basename(current)))
        return False
    old_sha = os.path.basename(current)
    log("release %s -> %s, re-exec" % (old_sha, new_sha))
    if pid_path:
        try:
            with open(pid_path, "w") as pf:
                pf.write(str(os.getpid()))
        except OSError:
            pass
    exe = executable or sys.executable
    hop = execv or os.execv
    hop(exe, [exe, os.path.realpath(shim_path)] + list(argv))
    return False


watch_idle_reexec._warned = set()
# --- end T-427 ---


def cmd_watch(a, board):
    """Poll the board; when there is work for the agent, launch a worker command.

    One watcher per agent (pid lock), one run at a time, per-run timeout,
    exponential backoff after failed runs. SIGTERM and spawn --stop take
    effect within WATCH_STOP_SLICE, including during an in-flight --exec
    (the child is terminated and run_end is written). Ctrl-C still breaks
    immediately.
    """
    import signal
    import time as _time

    owner = whoami(a.agent)
    if owner.startswith("agent-"):
        sys.exit("set --agent or TICKET_AGENT to a real name")
    _safe(lambda: _drop_unowned_agent_ticket(board, owner), None)
    root = os.path.dirname(board)
    cwd = os.path.abspath(a.cwd or root)
    if not os.path.isdir(cwd):
        sys.exit("--cwd %s does not exist" % cwd)
    if a.exec:
        cmd = a.exec
    else:
        # No --exec: run whatever `tickets join` registered for this agent.
        # Defaulting to claude here would make `tickets watch --agent qwen`
        # (the cron-able form, used without spawn) launch the wrong harness.
        harness, cmd_template = harness_of(board, owner)
        if harness == "claude" and not cmd_template:
            cmd = ('claude -p "$(tickets prompt)" --permission-mode %s%s'
                   % (a.permission_mode, (" --allowedTools %s" % a.allowed_tools) if a.allowed_tools else ""))
        else:
            cmd = _worker_cmd(board, owner, "", a.permission_mode, harness,
                              master=getattr(a, "prompt_kind", "") or "", cmd_template=cmd_template)
    # BYOA: a custom harness command is a template, not a finished command line.
    # Expansion happens per run rather than once here because {prompt_file} must
    # be a FRESH prompt every time -- the whole point of the watcher is that the
    # board changed since the last run.
    templated = any(ph in cmd for ph in HARNESS_PLACEHOLDERS)
    wake_mode = wake_mode_of(board, owner)
    requested_every = max(1, int(a.every))
    # Continuous adapters are the event bridge for already-ended model turns.
    # Keep their durable poll inside the local <5s acceptance target even when
    # the generic worker default is 60s. This is a cheap file read, not a model
    # call; the message gates still decide whether any paid turn starts.
    every = min(WATCH_MIN_INTERVAL, requested_every) if wake_mode == "continuous" else max(
        WATCH_MIN_INTERVAL, requested_every)
    # Retry/cost gates follow the enrolled seat, not argv[0]. Built-in Cursor
    # is `agent -p` (and cursor+claude starts the same way), which _harness_of_cmd
    # reports as custom — scoping from that would ignore the failure and relaunch.
    workforce_rec = load_workforce(board).get(owner, {}) or {}
    workforce_harness = (workforce_rec.get("harness") or workforce_rec.get("tool") or "").strip()
    if not workforce_harness:
        workforce_harness, _ = harness_of(board, owner)
    harness = _safe(lambda: _harness_of_cmd(cmd), "") or ""
    retry_harness = workforce_harness or harness
    # Cron/--once used to bypass this lock and could overlap a persistent
    # adapter. All launch paths now share one lease per seat.
    sa = _session_adapters()
    if sa.has_live_native_session(board, owner) and not getattr(a, "force", False):
        ep, _ = sa.live_endpoint(board, owner)
        print("skip: %s has a live native session (provider=%s, pid=%s) -- "
              "a headless watcher would double up on the seat; use watch --force to override"
              % (owner, (ep or {}).get("provider", "?"), (ep or {}).get("pid", "?")))
        if lifecycle_of(board, owner) == "ephemeral":
            sa.remove_endpoint(board, owner)
        sys.exit(0)
    lock = _watch_lock(board, owner)
    if lock is None:
        sys.exit("another watcher for %s is already running (see %s)" % (
            owner, os.path.join(agents_dir(board), owner + ".watch.pid")))
    log_path = os.path.join(agents_dir(board), owner + ".watch.log")
    # T-243: strip Git's LOCATION vars before handing the parent's environment
    # to a spawned/exec'd child, or an ambient GIT_DIR in *this* process
    # cascades into every agent this launches. _clean_git_env is deliberately
    # narrow: GIT_AUTHOR_*/GIT_COMMITTER_* survive, because this is the
    # fleet-launch env and stripping identity here would be a T-238-class
    # attribution loss (T-259 defect 3).
    env = dict(_clean_git_env(), TICKET_AGENT=owner, TICKETS_DIR=board,
               TICKETS_PY=os.path.realpath(__file__),
               PATH=os.path.expanduser("~/.local/bin") + ":/opt/homebrew/bin:" + os.environ.get("PATH", ""))
    import select
    poke_read, poke_write = os.pipe()
    os.set_blocking(poke_read, False)
    os.set_blocking(poke_write, False)
    stop = {"now": False}
    persist = bool(getattr(a, "persist", False))
    max_runs = int(getattr(a, "max_runs", 1) or 0)
    if persist:
        max_runs = 0
    stop_cond = STOP_CONDITION if max_runs else "until spawn --stop or SIGTERM (--persist)"

    def _term(signum, frame):
        # Flag only: sliced _watch_poll_wait / _watch_run_capped observe this
        # within WATCH_STOP_SLICE. Do not raise InterruptedError here —
        # Event.wait + a same-thread handler deadlocked the poll wait.
        stop["now"] = True

    def _should_stop_watch():
        return stop["now"] or os.path.exists(_stop_file(board, owner))

    def _usr1(signum, frame):
        # The interpreter writes this signal to poke_write through
        # set_wakeup_fd below. Returning normally is essential: raising here
        # used to let _WatchPoke escape proc.wait() and kill the watcher.
        pass

    signal.signal(signal.SIGTERM, _term)
    signal.signal(signal.SIGUSR1, _usr1)
    previous_wakeup_fd = signal.set_wakeup_fd(poke_write, warn_on_full_buffer=False)

    def drain_pokes():
        """Coalesce all queued SIGUSR1 bytes into one board rescan."""
        while True:
            try:
                if not os.read(poke_read, 4096):
                    return
            except BlockingIOError:
                return
            except OSError:
                return

    def log(line):
        try:
            if os.path.exists(log_path) and os.path.getsize(log_path) > WATCH_LOG_MAX_BYTES:
                os.replace(log_path, log_path + ".1")
            with open(log_path, "a") as lf:
                lf.write(line.rstrip("\n") + "\n")
        except OSError:
            pass

    runs = failures = 0
    try:
        if not a.once:
            print("watching %s for %s every %ds; wake=%s; cwd=%s; cmd=%s" % (
                board, owner, every, wake_mode, cwd, cmd))
            _safe(lambda: checkin(board, owner, None, "watch loop online (%s, every %ds)" % (
                wake_mode, every)), None)
        _safe(lambda: _agent_set(board, owner, drive_every=int(getattr(a, "heartbeat", 0) or 0)), None)
        while not stop["now"]:
            # Consume every queued edge at the start of one scan. A signal that
            # races this drain remains readable and forces another scan after
            # the current child, while the message itself stays durable.
            drain_pokes()
            try:
                os.unlink(_watch_poke_file(board, owner))
            except OSError:
                pass
            if os.path.exists(_stop_file(board, owner)):
                try:
                    os.unlink(_stop_file(board, owner))
                except OSError:
                    pass
                print("stop requested via tickets spawn --stop")
                break
            if not a.once:
                shim = _t427_idle_shim_path(__file__)
                if shim:
                    watch_idle_reexec(
                        executing_file=__file__,
                        shim_path=shim,
                        argv=sys.argv[1:],
                        log=log,
                        pid_path=lock,
                    )
            p = _safe(lambda: pending_work(board, owner), {})
            force = bool(getattr(a, "force", False))
            if force and not actionable(p):
                p = dict(p or {}, forced=True)
            trigger_fp = _watch_trigger_fingerprint(board, owner, p) if actionable(p) else None
            trigger_key = _remote_trigger_key(trigger_fp)
            retry_state = _local_adapter_failure(
                _agent_rec(board, owner) or {}, retry_harness)
            same_failure = bool(trigger_key and retry_state.get("trigger") == trigger_key)
            retry_deferred = (not a.once and same_failure and not force and
                              (retry_state.get("state") == "failed" or
                               float(retry_state.get("retry_epoch") or 0) > _remote_epoch()))
            if actionable(p) and retry_deferred:
                log("%s skip retrigger on unchanged trigger; state=%s attempts=%s retry_at=%s" % (
                    now(), retry_state.get("state"), retry_state.get("attempts"),
                    retry_state.get("retry_at") or "manual"))
                if a.verbose:
                    print("%s dispatch %s (attempts=%s; queued trigger unchanged)" % (
                        now(), retry_state.get("state"), retry_state.get("attempts")))
            elif actionable(p):
                if _auth_gates_spawn(retry_harness) and not getattr(a, "exec", None):
                    auth = _refresh_auth_check(board, owner)
                    if (auth.get("state") != "ready"
                            and ((auth.get("pause") or {}).get("retry_model") is False)):
                        log("%s skip model run; auth=%s pause retry_model=%s" % (
                            now(), auth.get("state"), (auth.get("pause") or {}).get("retry_model")))
                        print("  auth paused (%s); not starting a model turn" % auth.get("state"))
                        if a.once:
                            sys.exit(1)
                        _time.sleep(every)
                        continue
                if not same_failure:
                    failures = 0
                    def _clear_stale_failure(rec):
                        rec.pop("adapter_failure", None)
                    _safe(lambda: _agent_update(board, owner, _clear_stale_failure), None)
                runs += 1
                log("%s run %d trigger=%s" % (now(), runs, json.dumps(p)[:400]))
                print("%s work found (%s) wake=%s stop=%s -> run %d" % (
                    now(), ", ".join(k for k in p if k in WAKE_KEYS or k in ("messages_to_me", "review_queue")),
                    wake_reason_of(p), stop_cond, runs))
                run_cmd, cleanup = cmd, None
                if templated:
                    pf = ""
                    if "{prompt_file}" in cmd:
                        try:
                            pf, cleanup = _render_prompt_file(board, owner, getattr(a, "prompt_kind", "") or "")
                        except OSError as e:
                            log("%s run %d could not write the prompt file: %s" % (now(), runs, e))
                            print("  run %d skipped: could not write the prompt file (%s)" % (runs, e))
                            failures += 1
                            if a.once:
                                sys.exit(1)
                            if _watch_poll_wait(min(every * (2 ** min(failures, 5)), 900),
                                                stop, board, owner):
                                break
                            continue
                    run_cmd = _expand_harness_cmd(cmd, agent=owner, cwd=cwd, prompt_file=pf)
                # The trigger is recorded as the pending KEYS only: the values
                # are message text and ticket titles, and neither belongs in
                # the trajectory log (T-311 privacy rule).
                run_started = now()
                # After T-439 drop, bind via holding or agent.json ticket= left
                # by review submit — not a board scan that ignores here-empty.
                held_ticket = (p.get("holding") or [""])[0].split(" ")[0] or _watch_bind_ticket(board, owner) or None
                # T-425 FLAG: writes in the child stamp this id; aggregator
                # credits THAT run_id, not a time window (two seats, one ticket).
                run_id = "r-%s-%d-%s" % (
                    owner, runs,
                    hashlib.sha1(("%s:%d:%s:%d:%d" % (
                        owner, runs, run_started, os.getpid(), _time.time_ns()
                    )).encode()).hexdigest()[:12])
                env["TICKETS_RUN_ID"] = run_id
                env["TICKETS_RUN_NO"] = str(runs)
                release_sha = _release_commit()
                _safe(lambda rid=run_id, ht=held_ticket, rs=release_sha: traj_event(
                    board, "run_start", agent=owner, ticket=ht, run_no=runs,
                    run_id=rid, trigger=sorted(p), harness_cmd=harness,
                    worktree=cwd, release_sha=rs), None)
                if a.dry_run:
                    print("  dry-run; would execute: %s" % run_cmd)
                    if cleanup:
                        cleanup()
                    rc = 0
                    bound = _run_had_bound_write(
                        board, run_id, held_ticket, agent=owner, run_no=runs)
                    _safe(lambda rid=run_id, ht=held_ticket, bw=bound: traj_event(
                        board, "run_end", agent=owner, ticket=ht, run_no=runs,
                        run_id=rid, trigger=sorted(p), harness_cmd=harness,
                        worktree=cwd, started_at=run_started, ended_at=now(),
                        exit=0, dry_run=True,
                        bound_write=True if bw else None,
                        duration_s=_iso_span_secs(run_started, now())), None)
                else:
                    # The whole point of T-237: something must record that this
                    # agent is alive WHILE the child runs. checkin() cannot --
                    # the next call to it is on the far side of this line.
                    _safe(lambda: _run_begin(board, owner, runs, cwd,
                                             run_id=run_id, ticket=held_ticket), None)
                    # Where this run's own output starts in the shared log, so
                    # the usage parse below reads THIS run's tail and not the
                    # previous run's result object (T-311: a stale JSON blob
                    # would attribute one run's tokens to another).
                    log_before = _safe(lambda: os.path.getsize(log_path), 0) or 0
                    try:
                        rc, timed_out = _watch_run_capped(
                            run_cmd, cwd, env, log_path,
                            a.run_timeout * 60 if a.run_timeout else None,
                            WATCH_LOG_MAX_BYTES,
                            on_beat=lambda: _run_beat(board, owner, pid=os.getpid(), run=runs,
                                                      cwd=cwd, active=True),
                            beat_secs=int(getattr(a, "beat_every", 0) or RUN_HEARTBEAT_SECS),
                            should_stop=_should_stop_watch,
                        )
                    except InterruptedError:
                        _safe(lambda: _finalize_active_watch_run(board, owner), None)
                        if cleanup:
                            cleanup()
                        raise
                    _safe(lambda: _run_end(board, owner, runs, rc), None)
                    if cleanup:
                        cleanup()
                    ended = now()
                    run_output = _read_run_slice(log_path, log_before)
                    if _auth_gates_spawn(retry_harness) or retry_harness == "cursor":
                        run_auth_state = _classify_auth_output(rc, run_output)
                        if run_auth_state in ("login_required", "expired", "quota", "network") or rc == 0:
                            previous_auth = (_agent_rec(board, owner) or {}).get("auth_check") or {}
                            spec = _harness_auth_spec(board, owner, retry_harness)
                            status_cmd, login_cmd = spec if spec else (["status"], [""])
                            run_auth = {
                                "state": "ready" if rc == 0 else run_auth_state,
                                "harness": retry_harness or "cursor", "at": now(), "exit": rc,
                                "detail": ("headless run succeeded" if rc == 0 else
                                           ((run_output or "run failed").strip().splitlines()[-1][:240])),
                                "identity": previous_auth.get("identity", "") if rc == 0 else "",
                                "identity_label": previous_auth.get("identity_label", "") if rc == 0 else "",
                                "status_cmd": " ".join(status_cmd),
                                "login_cmd": " ".join(login_cmd) if login_cmd else previous_auth.get("login_cmd", ""),
                            }
                            _safe(lambda ra=run_auth: _store_auth_check(board, owner, ra), None)
                    # Tokens/cost for THIS run: the harness's own stdout when
                    # it was asked for a JSON format, else its session store.
                    # usage_error records "reported something unreadable",
                    # which must never be confused with "reported nothing".
                    usage, usage_error = _run_usage(
                        log_path, log_before, _harness_of_cmd(run_cmd), cwd,
                        run_started, ended)
                    if usage_error:
                        log("%s run %d usage not recorded: %s" % (now(), runs, usage_error))
                    bound = _run_had_bound_write(
                        board, run_id, held_ticket, agent=owner, run_no=runs)
                    _safe(lambda rid=run_id, ht=held_ticket, bw=bound: traj_event(
                        board, "run_end", agent=owner, ticket=ht,
                        run_no=runs, run_id=rid, trigger=sorted(p),
                        harness_cmd=harness, worktree=cwd,
                        started_at=run_started, ended_at=ended,
                        exit=rc, timed_out=bool(timed_out),
                        bound_write=True if bw else None,
                        duration_s=_iso_span_secs(run_started, ended),
                        outcome=_run_end_limit_outcome(
                            rc, timed_out,
                            _read_run_slice(log_path, log_before),
                            bound_write=bool(bw)),
                        usage_error=usage_error, **usage), None)
                    if timed_out:
                        with open(log_path, "a") as lf:
                            lf.write("%s run %d TIMEOUT after %d min\n" % (now(), runs, a.run_timeout))
                    log("%s run %d exit %s" % (now(), runs, rc))
                    print("  run %d finished exit=%s (log: %s)" % (runs, rc, log_path))
                    run_slice = _read_run_slice(log_path, log_before)
                    _safe(lambda rs=run_slice: _watch_note_limit_from_log(board, owner, rs), None)
                if not a.once and rc not in (0, None):
                    previous = ((_agent_rec(board, owner) or {}).get("adapter_failure") or {})
                    attempts = (int(previous.get("attempts") or 0) + 1
                                if previous.get("trigger") == trigger_key else 1)
                    retrying = attempts < LOCAL_DISPATCH_MAX_ATTEMPTS
                    delay = min(every * (2 ** max(0, attempts - 1)), 60) if retrying else 0
                    retry_epoch = _remote_epoch() + delay if delay else 0
                    failure_record = {
                        "state": "retrying" if retrying else "failed",
                        "trigger": trigger_key, "attempts": attempts,
                        "max_attempts": LOCAL_DISPATCH_MAX_ATTEMPTS,
                        "reason": "local harness exit %s" % rc,
                        "retry_epoch": retry_epoch,
                        "retry_at": (datetime.fromtimestamp(retry_epoch, timezone.utc).strftime(
                            "%Y-%m-%dT%H:%M:%SZ") if retry_epoch else ""),
                        "at": now(),
                        "provider": _adapter_provider(retry_harness),
                        "harness": retry_harness or "",
                    }
                    _safe(lambda fr=failure_record: _agent_set(board, owner, adapter_failure=fr), None)
                    log("%s skip retrigger armed for unchanged failed trigger; bounded attempt %d/%d state=%s" % (
                        now(), attempts, LOCAL_DISPATCH_MAX_ATTEMPTS, failure_record["state"]))
                elif not a.once:
                    def clear_failure(rec):
                        rec.pop("adapter_failure", None)
                    _safe(lambda: _agent_update(board, owner, clear_failure), None)
                failures = failures + 1 if rc not in (0, None) else 0
                if max_runs and runs >= max_runs:
                    print("max-runs reached")
                    break
            elif a.verbose:
                print("%s nothing pending%s" % (now(), " (limited)" if p.get("limited") else ""))
            if a.once:
                sys.exit(0 if actionable(p) else 1)
            wait = min(every * (2 ** min(failures, 5)), 900) if failures else every
            _safe(lambda: checkin(board, owner, None, "watching (%d runs, %d failed in a row)" % (runs, failures)), None)
            import time as _time
            deadline = _time.monotonic() + wait
            poked = False
            while not stop["now"]:
                remaining = deadline - _time.monotonic()
                if remaining <= 0:
                    break
                if select.select([poke_read], [], [], 0)[0]:
                    poked = True
                    break
                if _watch_poll_wait(
                    min(WATCH_STOP_SLICE, remaining), stop, board, owner
                ):
                    stop["now"] = True
                    break
            if stop["now"]:
                break
            if poked:
                continue
    except InterruptedError:
        pass
    finally:
        signal.set_wakeup_fd(previous_wakeup_fd)
        os.close(poke_read)
        os.close(poke_write)
        _safe(lambda: _finalize_active_watch_run(board, owner), None)
        if lock:
            try:
                os.unlink(lock)
            except OSError:
                pass
        if lifecycle_of(board, owner) == "ephemeral":
            _safe(lambda: _session_adapters().remove_endpoint(board, owner), None)
        if not a.once:
            print("watch stopped")


# ---- native Codex hook (SessionStart / UserPromptSubmit context) ----------

def cmd_codex_hook(a, board):
    """Codex hook body: print board context as hookSpecificOutput.additionalContext.

    Scoped to --worktree via the event cwd so one install is silent elsewhere.
    Does not acknowledge messages or touch tickets; it records the durable
    identity check-in required by every hook path.
    """
    owner = _hook_agent(a.agent)
    try:
        event = json.loads(sys.stdin.read() or "{}")
        if not isinstance(event, dict):
            event = {}
    except Exception:  # noqa: BLE001
        event = {}
    name = event.get("hook_event_name") or ""
    if name not in ("SessionStart", "UserPromptSubmit"):
        return
    cwd = event.get("cwd") or os.getcwd()
    if a.worktree:
        try:
            os.path.relpath(os.path.realpath(cwd), os.path.realpath(a.worktree)).startswith("..") and (_ for _ in ()).throw(ValueError())
        except (ValueError, OSError):
            return
    _pinned_hook_identity(board, owner)
    _safe(lambda: _session_adapters().heartbeat_session(
        board, owner,
        presented_lease=(os.environ.get("TICKETS_SESSION_LEASE") or "").strip(),
        thread=(os.environ.get("CODEX_THREAD_ID") or os.environ.get("CODEX_SESSION_ID") or "").strip(),
        session_id=(os.environ.get("CURSOR_CONVERSATION_ID")
                    or os.environ.get("CURSOR_SESSION_ID") or "").strip()), None)
    p = _safe(lambda: pending_work(board, owner), {})
    lines = [
        "Ticket board context (%s):" % owner,
        "- Agent: %s. Prefix every ticket command with TICKET_AGENT=%s." % (owner, owner),
        "- Board: %s" % board,
    ]
    for k in ("holding", "messages_to_me", "suggested_for_me", "ready_in_my_lane", "limited"):
        if k in p:
            v = p[k]
            lines.append("- %s: %s" % (k, "; ".join(v) if isinstance(v, list) else v))
    if p.get("broadcasts"):
        lines.append("- %d unread broadcasts: `tickets inbox`" % p["broadcasts"])
    lines.append("- Loop: tickets inbox -> tickets mine / tickets next -> work -> tickets update -> tickets sync -> tickets review")
    used = len("\n".join(lines))
    inherited = knowledge_context(board, owner, max_chars=max(500, 1850 - used))
    if inherited:
        lines.append(inherited)
    print(json.dumps({"hookSpecificOutput": {"hookEventName": name, "additionalContext": "\n".join(lines)[:1900]}}))


# ---- boot: every startup step for any tool, in one command -----------------

def cmd_boot(a, board):
    """Bring an agent online: join, hooks, check-in, briefing, next action.

    Idempotent; safe to run at every session start. --watch continues into the
    watch loop; --tool installs that tool's hooks.
    """
    owner = whoami(a.agent)
    if owner.startswith("agent-"):
        sys.exit("boot needs --agent <name> or TICKET_AGENT")
    if not os.path.isdir(board):
        sys.exit("no board at %s (run `tickets init` in the project first)" % board)
    root = os.path.dirname(board)
    steps = []
    # 1. identity + roles
    wf = load_workforce(board)
    roles = load_roles(board)
    if (owner not in wf or a.roles is not None or getattr(a, "wake_mode", None) is not None
            or getattr(a, "harness", "") or getattr(a, "cmd_template", "")):
        ns = _join_namespace(a, owner)
        _silent(lambda: cmd_join(ns, board))
        steps.append("joined as %s (roles=%s)" % (owner, a.roles or roles.get(owner, [])))
    else:
        steps.append("identity ok: %s roles=%s" % (owner, roles.get(owner, [])))
    # 2. hooks for the tool
    if a.tool in ("claude", "cursor", "codex"):
        hn = argparse.Namespace(tool=a.tool, agent=owner, force=False, settings=a.settings,
                                hooks_file=a.hooks_file, worktree=a.worktree or "", stop=True)
        _silent(lambda: cmd_hooks(hn, board))
        steps.append("%s hooks installed/verified" % a.tool)
    # 3. check-in with location
    rec = checkin(board, owner, None, "boot (%s)" % (a.tool or "shell"))
    warn = worktree_warning(owner)
    steps.append("checked in at %s [%s]" % (rec.get("worktree") or rec.get("cwd"), rec.get("branch") or "?"))
    # 4. briefing
    p = pending_work(board, owner)
    m = current_master(board)
    if not board_is_living(board):
        print_onboarding_startup()
    print("BOOT %s @ %s" % (owner, board))
    for s in steps:
        print("  - " + s)
    print("  - master: %s" % (m["owner"] if m else "nobody (tickets master take)"))
    if warn:
        print("  ! " + warn.replace("\n", "\n    "))
    if not p:
        print("  - nothing pending: no messages, no held ticket, nothing ready in your lane")
    for k, v in p.items():
        print("  - %s: %s" % (k, "; ".join(v) if isinstance(v, list) else v))
    if _join_is_ceo_path(owner, roles_for(board, owner, None) or []):
        print("NEXT: TICKET_AGENT=%s tickets connect" % owner)
    else:
        nxt = ("tickets mine" if p.get("holding") else "tickets next") if actionable(p) else "tickets msg \"idle\""
        print("NEXT: TICKET_AGENT=%s %s" % (owner, nxt))
    if a.watch:
        wn = argparse.Namespace(agent=owner, every=a.every, exec=a.exec, cwd=a.cwd or root,
                                permission_mode="acceptEdits", allowed_tools="", max_runs=1,
                                once=False, dry_run=False, verbose=False, run_timeout=a.run_timeout,
                                heartbeat=0, persist=False, force=False, prompt_kind="", beat_every=0)
        cmd_watch(wn, board)


def _silent(fn):
    import io
    import contextlib
    buf = io.StringIO()
    try:
        with contextlib.redirect_stdout(buf):
            fn()
    except SystemExit:
        pass
    return buf.getvalue()


GUIDE = """# Startup guide -- connecting any agent to the board

NEW HERE? Do this first, then come back:

    cd <your repo> && tickets quickstart        # board + sample work + you, registered
                                                # then: tickets next
    docs/first-session.md                       # the same run, captured and annotated
    README.md                                   # the model, worker loop, master loop

This guide is the next step after that: wiring a REAL agent (Claude Code,
Codex, Cursor, your own harness) to a board that already exists.

One command does every step (join, hooks, check-in, briefing):

    export TICKET_AGENT=<unique-name>          # used for this one-time boot command
    cd <repo or your worktree>
    tickets boot --tool claude|codex|cursor [--roles backend] [--watch]

What `boot` guarantees, idempotently:
  1. identity registered (roles/cost/capabilities) so routing knows you
  2. the tool's hooks installed:
       claude : SessionStart -> board, UserPromptSubmit -> inbox, Stop -> keep working while work remains
       codex  : SessionStart + UserPromptSubmit -> board context (scoped to your worktree)
       cursor : .cursor/hooks.json sessionStart / beforeSubmitPrompt / stop -> board digest
  3. check-in (worktree, branch, sha) so `tickets who` is truthful
  4. a briefing: unread messages, held ticket, ready tickets in your lane, and the exact NEXT command

Per tool, after boot:
  Claude Code (interactive):  claude                          -- the project hook carries its own identity
  Claude Code (unattended):   tickets boot --tool claude --watch    (or: tickets watch --every 60)
                              runs `claude -p "$(tickets prompt)"` only when `tickets pending` says there is work
  Codex:                      codex                           -- the scoped hook carries its own identity
  Cursor:                     open the configured worktree and enable Hooks in settings
  Anything else:              `tickets hooks remote --agent <name> --wrapper <path>` writes an identity-pinned
                              wrapper plus SessionStart/inbox/Stop/taskWake command manifest

Every generated hook pins both its board and agent. An unrelated
`TICKET_AGENT` in the launching shell cannot change hook attribution.

Safety rails (all on by default):
  - Stop hook: at most one extra continuation per user turn (stop_hook_active) and 4 per hour;
    off with TICKETS_STOP_HOOK=off; never fires without TICKET_AGENT; never for broadcasts only.
  - watch: one watcher per agent name (pid lock), one run at a time, --run-timeout, backoff on failures,
    logs in .tickets/agents/<name>.watch.log, stops on SIGTERM/spawn --stop within WATCH_STOP_SLICE.
  - An agent that recorded `tickets limit` is never woken until `tickets limit --clear`.

Spawning a team from a master session (models per agent):
  tickets spawn scribe --model sonnet --roles docs --brief "house style: ..."     # cheap worker
  tickets spawn core   --model opus   --roles backend --cost high                # hard tickets
  tickets spawn boss   --master --model sonnet                                   # coordinate / unblock / review / merge
  tickets drive "<what done looks like>" --as boss --heartbeat 30               # objective + master seat that wakes
                                                                                #   every 30 min to plan toward it
  tickets spawn --list | tickets spawn <name> --stop

Bring your own agent (any harness, same prompt contract -- docs/byoa.md):
  tickets join without --harness prints harness=claude (default) — a label, not a process.
  Claude is not started until watch/spawn. Dry join + tickets next is the BYOA plug path.
  tickets join qwen --roles backend --harness custom --cmd 'ollama run qwen3:8b < {prompt_file}'
  tickets harness check qwen          # runs it on 'reply OK' under a 60s cap, records pass/fail + latency
  tickets spawn qwen                  # no --harness: uses what `join` registered
  Placeholders: {prompt_file} (this wake-up's prompt, fresh per run) {cwd} (the agent's worktree) {agent}.
  Each spawn = register + own worktree (.worktrees/<name>, project .claude settings copied in) +
  a detached watcher that runs the tool with that model only when `tickets pending` says there is
  work. Workers persist until --stop, logout or reboot; new tickets created later are picked up on
  the next poll. Spawned workers run unattended (no permission prompts); --safe keeps prompts.
  To survive reboot, add the watcher command from `spawn --list`'s log to a login item / launchd job.

Stuck rule (in every worker prompt): if blocked -- permission, failing test, unclear scope, missing
access, a decision that is not yours -- post `tickets msg "stuck: ..." --to <master> --re <id>`
immediately. The master wakes on stuck messages, the review queue and CRIT health, and its prompt
is: unblock, review+merge, coordinate. Nobody waits silently.

Check yourself:  tickets pending --agent <name>   (exit 0 = there is work)
                 tickets who                       (where everyone is)
"""


# ---- BYOA: bring your own agent ------------------------------------------
#
# A harness is whatever actually runs the model: the Claude CLI, the Codex CLI,
# the Cursor agent, or ANY command of your own (`custom:<cmd>` / --cmd). The
# runtime's side of the contract never changes -- it hands the harness a prompt
# and a working directory, and reads the board afterwards. docs/byoa.md is the
# operator-facing version of this.
BUILTIN_HARNESSES = ("claude", "codex", "cursor", "cursor+claude", "remote",
                     "agy", "antigravity", "devin", "cognition", "gemini",
                     "grok", "grokbots")
HOOK_TOOLS = ("claude", "cursor", "codex", "remote",
              "agy", "antigravity", "devin", "cognition", "gemini",
              "grok", "grokbots")
# Documented shell-template placeholders for custom harnesses. `harness check`
# refuses templates with any other {name} token or without {prompt_file}.
HARNESS_PLACEHOLDERS = ("{prompt_file}", "{cwd}", "{agent}")
_HARNESS_PLACEHOLDER_RE = re.compile(r"\{([a-z_]+)\}")


def _harness_template_error(template):
    """Return an error string when a custom harness template is invalid."""
    if not template:
        return None
    for name in _HARNESS_PLACEHOLDER_RE.findall(template):
        token = "{%s}" % name
        if token not in HARNESS_PLACEHOLDERS:
            return "unknown harness placeholder %s (allowed: %s)" % (
                token, " ".join(HARNESS_PLACEHOLDERS))
    if "{prompt_file}" not in template:
        return "harness template must include {prompt_file}"
    return None


def _split_harness(spec):
    """`custom:<cmd>` -> ("custom", "<cmd>"); anything else -> (spec, "").

    The inline form exists so a harness and its command fit in one flag
    (`--harness 'custom:my-agent --prompt {prompt_file}'`), which is what a
    spawn line in a shell script wants. `--cmd` is the same thing, spelled out.
    """
    spec = (spec or "").strip()
    if spec.startswith("custom:"):
        return "custom", spec[len("custom:"):].strip()
    return spec, ""


def harness_of(board, owner, harness="", cmd=""):
    """Resolve (harness, cmd_template) for an agent: explicit flags win, then
    the workforce record written by `tickets join`, then the claude default.

    This is the single place that answers "what runs this agent", so `spawn`,
    `watch` and `harness check` cannot disagree about it.
    """
    harness, inline = _split_harness(harness)
    cmd = cmd or inline
    entry = load_workforce(board).get(owner, {}) or {}
    if not harness:
        stored, stored_inline = _split_harness(entry.get("harness") or entry.get("tool") or "")
        harness = stored
        cmd = cmd or stored_inline or (entry.get("cmd") or "")
    elif not cmd and harness == (entry.get("harness") or entry.get("tool") or ""):
        cmd = entry.get("cmd") or ""
    return (harness or "claude"), cmd


def _expand_harness_cmd(template, agent="", cwd="", prompt_file=""):
    """Substitute {prompt_file} {cwd} {agent} in a custom harness command.

    str.replace, deliberately not str.format: a real harness command carries
    braces of its own (a curl with a JSON body, an awk program), and .format
    would raise KeyError on them -- the agent would simply never start, with
    the failure a hundred lines away from the template that caused it.

    Every substituted value is shell-quoted because the result is run through
    the shell. The TEMPLATE itself is not: it is a command line by definition,
    and it comes from whoever ran `tickets join` for this agent.
    """
    out = template
    for name, value in (("{prompt_file}", prompt_file), ("{cwd}", cwd), ("{agent}", agent)):
        if name in out:
            out = out.replace(name, shlex.quote(value or ""))
    return out


def _render_prompt_file(board, owner, kind="", text=""):
    """Write this agent's prompt to a fresh temp file; return (path, cleanup).

    A file, not an argv string: a worker prompt is thousands of characters of
    briefs and ticket context, and pasting that into a command line is how you
    meet ARG_MAX on a long board. The file is mode 0600 and removed by the
    cleanup callable, which never raises -- a harness run must not fail because
    the prompt file was already gone.
    """
    import tempfile

    if not text:
        ns = argparse.Namespace(agent=owner, master=(kind == "master"), cos=(kind == "cos"), extra="")
        text = _safe(lambda: prompt_text(ns, board), "") or ""
    fd, path = tempfile.mkstemp(prefix="tickets-prompt-%s-" % re.sub(r"[^A-Za-z0-9_.-]", "_", owner)[:32],
                                suffix=".txt")
    try:
        with os.fdopen(fd, "w") as f:
            f.write(text)
    except OSError:
        os.close(fd)
        raise

    def cleanup():
        try:
            os.unlink(path)
        except OSError:
            pass

    return path, cleanup


def _worker_cmd(board, owner, model="", permission_mode="bypassPermissions", tool="claude", master=False,
                cmd_template="", prompt_expr=""):
    """The headless command a spawned worker runs. Model comes from --model or
    the workforce record (`tickets join --model`). Spawned workers run without
    permission prompts by default: nobody is there to answer them, and the
    blast radius is the agent's own worktree and branch (--safe for acceptEdits).

    `cmd_template` (from `join --cmd` / `spawn --cmd`) overrides the built-in
    line for ANY harness, so bringing your own agent is not a second code path:
    the watcher runs one string either way. `prompt_expr` replaces the shell
    expression that produces the prompt -- `harness check` passes a literal
    probe prompt through it so the probe exercises the real command shape.
    """
    # The workforce record is writable by any agent, so the model name is quoted
    # before it reaches `watch`, which runs this string through the shell.
    model = shlex.quote(model or load_workforce(board).get(owner, {}).get("model", "") or "")
    model = "" if model == "''" else model
    if cmd_template:
        # Placeholders are expanded per run by the watcher (a fresh prompt file
        # each time), so the template is returned as written.
        return cmd_template
    if tool == "custom":
        # Without a template there is nothing to run, and the fall-through below
        # would launch a binary literally named "custom".
        sys.exit("%s is registered as a custom harness with no command; "
                 "re-register it: tickets join %s --harness custom --cmd '<shell template>'" % (owner, owner))
    if tool == "remote":
        sys.exit("%s uses a remote adapter; connect the generated remote hook/bridge "
                 "or register a custom command. Refusing to substitute a local model." % owner)
    prompt = {"master": "tickets prompt --master", "cos": "tickets prompt --cos"}.get(
        master if isinstance(master, str) else ("master" if master else ""), "tickets prompt")
    prompt = prompt_expr or '"$(%s)"' % prompt
    if tool == "claude":
        flag = ("--dangerously-skip-permissions" if permission_mode == "bypassPermissions"
                else "--permission-mode %s" % permission_mode)
        return 'claude -p %s %s%s' % (prompt, flag, (" --model %s" % model) if model else "")
    if tool == "codex":
        # Codex CLI headless: exec mode; unattended needs the bypass flag (no one
        # can answer approvals), --safe keeps the workspace-write sandbox instead.
        mode = ("--dangerously-bypass-approvals-and-sandbox" if permission_mode == "bypassPermissions"
                else "-s workspace-write")
        return 'codex exec --skip-git-repo-check %s%s %s' % (mode, (" -m %s" % model) if model else "", prompt)
    if tool == "cursor" or tool in ("grok", "grokbots"):
        # Cursor CLI (`agent`): Cursor Grok seats and grokbots use the same persist path.
        force = "--force" if permission_mode == "bypassPermissions" else ""
        return 'agent -p --output-format text %s%s %s' % (force, (" --model %s" % model) if model else "", prompt)
    if tool in ("agy", "antigravity"):
        flag = ("--dangerously-skip-permissions" if permission_mode == "bypassPermissions"
                else "--mode accept-edits")
        return 'agy -p %s %s%s' % (prompt, flag, (" --model %s" % model) if model else "")
    if tool in ("devin", "cognition"):
        flag = ("--dangerously-skip-permissions" if permission_mode == "bypassPermissions"
                else "")
        return 'devin --print %s%s%s' % (prompt, (" " + flag) if flag else "", (" --model %s" % model) if model else "")
    if tool == "gemini":
        return 'gemini -p %s%s' % (prompt, (" --model %s" % model) if model else "")
    if tool == "cursor+claude":
        # Fable through Cursor first; if that run errors, the same prompt through the
        # Claude CLI (opus). One identity, two engines -- the master never goes dark.
        first = _worker_cmd(board, owner, model or "claude-fable-5-1-thinking-high", permission_mode, "cursor",
                            master, prompt_expr=prompt_expr)
        second = _worker_cmd(board, owner, "opus", permission_mode, "claude", master, prompt_expr=prompt_expr)
        return "%s || %s" % (first, second)
    # Any other executable, named directly: it is the command, and it reads the
    # prompt itself. Kept for the pre-BYOA `spawn --tool ./my-runner` form.
    return tool


def _inherit_settings(root, wt):
    """Copy the project's .claude and .agents settings into a new worktree so permission
    allow-lists and hooks are the same there (a worktree does not inherit the
    root checkout's .claude/ or .agents/ directory)."""
    import shutil
    copied = []
    for dname, fnames, prefixed in ((".claude", ("settings.json", "settings.local.json"), False),
                                    (".agents", ("hooks.json",), True)):
        src = os.path.join(root, dname)
        dst = os.path.join(wt, dname)
        if not os.path.isdir(src) or os.path.abspath(src) == os.path.abspath(dst):
            continue
        os.makedirs(dst, exist_ok=True)
        for name in fnames:
            s, d = os.path.join(src, name), os.path.join(dst, name)
            if os.path.isfile(s) and not os.path.exists(d):
                shutil.copy2(s, d)
                copied.append(os.path.join(dname, name) if prefixed else name)
    return copied


def _stop_file(board, owner):
    return os.path.join(agents_dir(board), owner + ".watch.stop")


def cmd_spawn(a, board):
    """Bring up a persistent worker: register it, give it a worktree, and start a
    detached watcher that launches the tool (with the chosen model) whenever the
    board has work for it. --stop asks the watcher to exit at its next poll;
    --list shows who is running. The watcher lives until stopped, logout or
    reboot; `tickets guide` shows how to make it a login item."""
    import subprocess

    root = os.path.dirname(board)
    if a.list:
        wf = load_workforce(board)
        print("%-14s %-9s %-9s %-10s %-8s %-12s %-8s %s" % (
            "agent", "watcher", "harness", "wake", "model", "check", "seen", "worktree"))
        with _shared_watch_table():
          for r in sorted(load_agents(board), key=lambda r: r["owner"]):
            pids = _live_watch_pids(r["owner"], board=board)
            wc = len(pids)
            wlabel = ("pid %d" % pids[0]) if wc == 1 else ("%d pids" % wc if wc else "-")
            if wc > 1:
                wlabel += " !!"
            entry = wf.get(r["owner"], {})
            print("%-14s %-9s %-9s %-10s %-8s %-12s %-8s %s" % (
                r["owner"][:14], wlabel,
                (entry.get("harness") or entry.get("tool") or "claude")[:9],
                wake_mode_of(board, r["owner"], workforce=wf)[:10],
                (entry.get("model") or "-")[:8],
                _harness_check_label(r.get("harness_check")),
                (fmt_hours(hours_since(r["seen"])) + " ago") if r.get("seen") else "never",
                (r.get("worktree") or "").replace(os.path.expanduser("~"), "~")))
        return
    if not a.name:
        sys.exit("spawn needs a name (or --list)")
    owner = a.name
    if a.stop:
        import signal

        pids = _live_watch_pids(owner)
        _mark_run_interrupted(board, owner)
        if not pids:
            # Watcher is already gone; a late heartbeat from the dead run
            # must not reopen the receipt. Re-apply the stop fence after the
            # liveness check so a beat that raced the first mark stays closed.
            _mark_run_interrupted(board, owner)
            print("no running watcher for %s" % owner)
            return
        busy = [p for p in pids if _watcher_run_active(board, owner, p)]
        try:
            with open(_stop_file(board, owner), "w") as f:
                f.write(now())
        except OSError:
            pass
        stopped = 0
        for pid in pids:
            try:
                os.kill(pid, signal.SIGTERM)
                stopped += 1
            except ProcessLookupError:
                pass
        print("stopped %d watcher(s) for %s (pids %s)" % (
            stopped, owner, ", ".join(str(p) for p in pids)))
        if busy:
            # SIGTERM + stop-file are observed within WATCH_STOP_SLICE, including
            # mid-run. The duplicate guard in the start path below only sees a
            # pid that has actually gone, so say so rather than let the operator
            # spawn into a still-live loop (T-554).
            print("mid-run: %s -- wait for the pid(s) to exit before `tickets spawn %s`" % (
                ", ".join(str(p) for p in busy), owner))
        post_message(board, whoami(), "%s watcher asked to stop (%d loop(s))" % (owner, stopped))
        return
    requested_harness = getattr(a, "harness", "") or a.tool
    incoming_harness, _ = _split_harness(requested_harness)
    conflict = _identity_reuse_conflict(board, owner, incoming_harness)
    if conflict:
        if not getattr(a, "transfer", False):
            sys.exit(_identity_reuse_error(owner, conflict, incoming_harness))
        if _agent_holds_ticket(board, owner):
            sys.exit("refusing --transfer: %s holds a ticket; reopen or finish it first" % owner)
        _strip_identity_bound_state(board, owner)
    wt = os.path.abspath(a.worktree) if a.worktree else os.path.join(root, ".worktrees", owner)
    git_root, origin_err = _spawn_git_root(board, owner, wt)
    if origin_err:
        sys.exit(origin_err)
    resolved_harness, _ = harness_of(board, owner, requested_harness,
                                     getattr(a, "cmd_template", ""))
    sa = _session_adapters()
    if sa.has_live_native_session(board, owner):
        ep, _ = sa.live_endpoint(board, owner)
        print("skip: %s has a live native session (provider=%s, pid=%s) -- "
              "spawn would double up on the interactive seat"
              % (owner, (ep or {}).get("provider", "?"), (ep or {}).get("pid", "?")))
        return
    # Zero-model preflight. Merge uses the enrolled runner (join-time host), so
    # a sandboxed coordinator cannot clobber it. Built-in Claude/Codex/Cursor
    # must be ready before a watcher starts. Keep this before cmd_join so a
    # failed relaunch cannot alter roles/harness/worktree. --exec skips it.
    if _auth_gates_spawn(resolved_harness) and not a.exec:
        auth = _refresh_auth_check(board, owner, requested_harness)
        if auth.get("state") != "ready":
            _print_auth_result(owner, auth)
            sys.exit("watcher not started; fix the state above, then rerun `tickets spawn %s`" % owner)
    ns = _join_namespace(a, owner)
    _silent(lambda: cmd_join(ns, board))
    # --tool/--harness no longer defaults to "claude" in the parser: an absent
    # flag must mean "use what `tickets join` registered for this agent",
    # otherwise a BYOA agent silently reverts to the Claude CLI on every spawn.
    harness, cmd_template = harness_of(board, owner, getattr(a, "harness", "") or a.tool,
                                       getattr(a, "cmd_template", ""))
    if harness == "remote" and not (cmd_template or a.exec):
        sys.exit("remote adapter is offline; no local executable was selected. "
                 "Run `tickets hooks remote --agent %s`, connect its long-poll/callback bridge, "
                 "or pass --cmd for a local adapter. Pending wakes remain queued." % owner)
    if not os.path.isdir(wt):
        base = a.base or _trunk()
        r = subprocess.run(["git", "-C", git_root, "worktree", "add", "-q", wt, "-b", owner, base],
                           capture_output=True, text=True)
        if r.returncode != 0:
            r = subprocess.run(["git", "-C", git_root, "worktree", "add", "-q", wt, owner],
                               capture_output=True, text=True)
        if r.returncode != 0:
            sys.exit("could not create worktree %s: %s" % (wt, (r.stderr or r.stdout).strip()))
        print("worktree %s (branch %s)" % (wt, owner))
    if a.brief:
        bn = argparse.Namespace(agent=owner, text=a.brief, ticket="", file="", show=False,
                                role="", by=whoami())
        _silent(lambda: cmd_brief(bn, board))
    inherited = _inherit_settings(root, wt)
    if inherited:
        print("inherited project settings into the worktree: %s" % ", ".join(inherited))
    if a.master:
        prev = current_master(board) or {}
        with open(master_state_path(board), "w") as f:
            json.dump({"owner": owner, "since": now(), "cos": prev.get("cos", "")}, f)
        _master_log(board, "%s spawned as persistent master (planner)" % owner, by=whoami())
    if a.cos:
        prev = current_master(board) or {}
        if not prev.get("owner"):
            sys.exit("no master yet; spawn or take the master seat first")
        prev["cos"] = owner
        with open(master_state_path(board), "w") as f:
            json.dump(prev, f)
        _master_log(board, "%s spawned as persistent chief of staff (review/unblock/merge)" % owner, by=whoami())
    sa = _session_adapters()
    if sa.has_live_native_session(board, owner):
        ep, _ = sa.live_endpoint(board, owner)
        print("skip: %s has a live native session (provider=%s, pid=%s) -- "
              "spawn would double up on the interactive seat"
              % (owner, (ep or {}).get("provider", "?"), (ep or {}).get("pid", "?")))
        return
    live = _live_watch_pids(owner, board=board)
    if live:
        print("watcher for %s already running (%d process(es), pids %s); --stop first" % (
            owner, len(live), ", ".join(str(p) for p in live)))
        return
    _safe(lambda: _drop_unowned_agent_ticket(board, owner), None)
    try:
        os.unlink(_stop_file(board, owner))
    except OSError:
        pass
    mode = "acceptEdits" if a.safe else "bypassPermissions"
    kind = "cos" if a.cos else ("master" if a.master else "")
    cmd = a.exec or _worker_cmd(board, owner, a.model, mode, harness, master=kind, cmd_template=cmd_template)
    argv = [sys.executable, os.path.realpath(__file__), "watch", "--agent", owner, "--every", str(a.every),
            "--cwd", wt, "--exec", cmd, "--run-timeout", str(a.run_timeout),
            "--prompt-kind", kind,
            "--heartbeat", str(int(getattr(a, "heartbeat", 0) or 0))]
    effective_wake_mode = wake_mode_of(board, owner)
    max_runs = spawn_watch_max_runs(
        wake_mode=effective_wake_mode, persist=bool(getattr(a, "persist", False)),
        max_runs=getattr(a, "max_runs", None))
    if max_runs == 0:
        argv += ["--persist", "--max-runs", "0"]
    else:
        argv += ["--max-runs", str(max_runs)]
    # T-243: strip Git's LOCATION vars before handing the parent's environment
    # to a spawned/exec'd child, or an ambient GIT_DIR in *this* process
    # cascades into every agent this launches. _clean_git_env is deliberately
    # narrow: GIT_AUTHOR_*/GIT_COMMITTER_* survive, because this is the
    # fleet-launch env and stripping identity here would be a T-238-class
    # attribution loss (T-259 defect 3).
    env = dict(_clean_git_env(), TICKET_AGENT=owner, TICKETS_DIR=board,
               TICKETS_PY=os.path.realpath(__file__),
               PATH=os.path.expanduser("~/.local/bin") + ":/opt/homebrew/bin:" + os.environ.get("PATH", ""))
    log_path = os.path.join(agents_dir(board), owner + ".watch.log")
    with open(log_path, "a") as lf:
        subprocess.Popen(argv, cwd=wt, env=env, stdout=lf, stderr=subprocess.STDOUT,
                         stdin=subprocess.DEVNULL, start_new_session=True)
    import time as _time
    _time.sleep(1.0)
    pid = _watcher_pid(board, owner)
    model = a.model or load_workforce(board).get(owner, {}).get("model") or "default"
    print("watcher for %s started%s; harness=%s; model=%s; wake=%s; persist=%s; max-runs=%s; log %s" % (
        owner, (" (pid %d)" % pid) if pid else "", harness, model,
        effective_wake_mode, "yes" if max_runs == 0 else "no", max_runs, log_path))
    print("cmd: %s" % cmd)
    post_message(board, whoami(), "%s spawned as a persistent worker (%s, model %s); it wakes whenever the board has work for it"
                 % (owner, harness, model))


HARNESS_PROBE_PROMPT = "reply OK"
HARNESS_CHECK_TIMEOUT = 60

HARNESS_AUTH_COMMANDS = {
    "cursor": (["agent", "status"], ["agent", "login"]),
    "cursor+claude": (["agent", "status"], ["agent", "login"]),
    "claude": (["claude", "auth", "status"], ["claude", "auth", "login"]),
    "codex": (["codex", "login", "status"], ["codex", "login"]),
}

_AUTH_ENV_NAMES = (
    "HOME", "USER", "LOGNAME", "USERNAME",
    "XDG_CONFIG_HOME", "XDG_CACHE_HOME", "XDG_RUNTIME_DIR",
)
_AUTH_ENV_PREFIXES = (
    "ANTHROPIC", "CLAUDE", "CODEX", "CURSOR", "OPENAI", "OPENROUTER", "XAI",
)
_AUTH_EXPIRED_STRINGS = (
    "token expired", "session expired", "credential expired",
    "token has been revoked", "refresh token",
)
_AUTH_NETWORK_STRINGS = (
    "connection refused", "network is unreachable", "network unreachable",
    "temporary failure in name resolution", "could not resolve",
    "nodename nor servname", "connection reset", "connection timed out",
    "tls handshake", "ssl:", "name or service not known",
)
_DEFAULT_PROFILE_KIND = {
    "cursor": "browser",
    "cursor+claude": "browser",
    "claude": "subscription",
    "codex": "chatgpt",
    "remote": "adapter",
    "custom": "adapter",
}


def _auth_probe_env():
    """Keep the caller's PATH first so a host probe sees the same CLIs as the runner."""
    env = dict(os.environ)
    extra = os.path.expanduser("~/.local/bin") + ":/opt/homebrew/bin"
    env["PATH"] = (os.environ.get("PATH") or "") + ":" + extra
    return env


def _auth_gates_spawn(harness):
    return (harness or "") in ("claude", "codex", "cursor", "cursor+claude")


def _detect_runner_kind():
    kind = (os.environ.get("ATMAN_RUNNER_KIND") or "").strip()
    if kind in ("host", "sandbox", "container"):
        return kind
    if os.path.exists("/.dockerenv") or os.environ.get("container"):
        return "container"
    if os.environ.get("CURSOR_SANDBOX") or os.environ.get("ATMAN_SANDBOX"):
        return "sandbox"
    return "host"


def _env_fingerprint(env=None):
    env = env if env is not None else os.environ
    names = set(_AUTH_ENV_NAMES)
    for key in env:
        up = key.upper()
        if any(up == p or up.startswith(p + "_") for p in _AUTH_ENV_PREFIXES):
            names.add(key)
    present = sorted(n for n in names if n in env)
    return hashlib.sha256("|".join(present).encode()).hexdigest()[:16]


def _git_remote_origin(cwd):
    import subprocess
    if not cwd or not os.path.isdir(cwd):
        return ""
    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    try:
        r = subprocess.run(["git", "-C", cwd, "remote", "get-url", "origin"],
                           capture_output=True, text=True, timeout=5, env=env)
        return (r.stdout or "").strip() if r.returncode == 0 else ""
    except (OSError, subprocess.TimeoutExpired):
        return ""


def _git_head_sha(cwd):
    import subprocess
    if not cwd or not os.path.isdir(cwd):
        return ""
    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    try:
        r = subprocess.run(["git", "-C", cwd, "rev-parse", "HEAD"],
                           capture_output=True, text=True, timeout=5, env=env)
        return (r.stdout or "").strip() if r.returncode == 0 else ""
    except (OSError, subprocess.TimeoutExpired):
        return ""


def _which_binary(argv0):
    import shutil
    if not argv0:
        return argv0 or ""
    if os.path.isabs(argv0) and os.path.isfile(argv0):
        return argv0
    found = shutil.which(argv0)
    return os.path.realpath(found) if found else argv0


def _expected_origin_for(board, owner, worktree=""):
    from auth_v2_contract import normalize_git_origin
    wf = (load_workforce(board).get(owner, {}) or {})
    pinned = (wf.get("expected_origin") or os.environ.get("ATMAN_EXPECTED_ORIGIN") or "").strip()
    if pinned:
        return normalize_git_origin(pinned) or pinned
    rec = _agent_rec(board, owner) or {}
    for path in (worktree, rec.get("worktree"), rec.get("cwd")):
        origin = _git_remote_origin(path)
        if origin:
            return normalize_git_origin(origin) or origin
    return ""


def _auth_execution_context(board, owner, status_cmd=None, worktree=""):
    import getpass
    rec = _agent_rec(board, owner) or {}
    wt = worktree or rec.get("worktree") or rec.get("cwd") or os.path.dirname(board)
    repo_root = _init_cwd_worktree_root(wt) or wt
    origin = _git_remote_origin(wt) or _git_remote_origin(repo_root)
    expected = _expected_origin_for(board, owner, wt) or origin
    if not origin:
        origin = "local/" + hashlib.sha1(
            os.path.realpath(repo_root or wt or board).encode()).hexdigest()[:12]
        if not expected:
            expected = origin
    argv0 = (status_cmd or ["tickets"])[0]
    binary = _which_binary(argv0)
    host = os.uname().nodename if hasattr(os, "uname") else ""
    try:
        user = getpass.getuser()
    except Exception:
        user = os.environ.get("USER") or os.environ.get("LOGNAME") or "operator"
    life = lifecycle_of(board, owner)
    runner_id = "rnr_%s" % hashlib.sha1(
        ("%s|%s|%s|%s" % (host, user, binary, owner)).encode()).hexdigest()[:12]
    return {
        "runner_id": runner_id,
        "runner_kind": _detect_runner_kind(),
        "hostname": host,
        "username": user,
        "binary": binary,
        "argv0": argv0,
        "env_fingerprint": _env_fingerprint(),
        "worktree": os.path.abspath(wt) if wt else "",
        "repo_root": repo_root,
        "origin_url": origin,
        "expected_origin": expected or origin,
        "head": _git_head_sha(wt or repo_root),
        "agent_id": owner,
        "ticket_agent": owner,
        "lifecycle": life,
    }


def _enroll_runner_context(board, owner):
    from auth_v2_contract import context_is_complete
    rec = _agent_rec(board, owner) or {}
    stored = rec.get("runner_context")
    if context_is_complete(stored):
        return stored
    ctx = _auth_execution_context(board, owner)
    if context_is_complete(ctx):
        _agent_set(board, owner, runner_context=ctx)
        return ctx
    return stored or ctx


def _enrolled_runner_ctx(board, owner):
    from auth_v2_contract import context_is_complete
    rec = _agent_rec(board, owner) or {}
    stored = rec.get("runner_context")
    if context_is_complete(stored):
        return stored
    return _enroll_runner_context(board, owner)


def _harness_auth_spec(board, owner, harness=""):
    resolved, _ = harness_of(board, owner, harness, "")
    spec = HARNESS_AUTH_COMMANDS.get(resolved)
    if spec:
        return spec
    entry = load_workforce(board).get(owner, {}) or {}
    status = (entry.get("auth_status_cmd") or "").strip()
    if not status:
        return None
    login = (entry.get("auth_login_cmd") or "").strip()
    return (shlex.split(status), shlex.split(login) if login else [])


def _profile_kind_for(harness):
    return _DEFAULT_PROFILE_KIND.get(harness or "", "adapter")


def _persist_auth_profile(board, owner, harness, identity_label, kind):
    from auth_v2_contract import PROFILE_DIR_MODE, PROFILE_STORE_MODE, profile_store_path
    digest = hashlib.sha1(("%s:%s:%s" % (os.path.realpath(board), owner, harness)).encode()).hexdigest()[:12]
    ref = "prf_%s" % digest
    cache = os.environ.get("TICKETS_CACHE_DIR") or os.path.expanduser("~/.cache/atman")
    board_hash = hashlib.sha1(os.path.realpath(board).encode()).hexdigest()[:16]
    path = profile_store_path(cache, board_hash, ref)
    os.makedirs(os.path.dirname(path), mode=0o700, exist_ok=True)
    try:
        os.chmod(os.path.dirname(path), PROFILE_DIR_MODE)
    except OSError:
        pass
    payload = {
        "provider": harness,
        "profile_kind": kind,
        "identity_label": identity_label or "",
        "agent": owner,
    }
    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        json.dump(payload, f, sort_keys=True)
    os.replace(tmp, path)
    os.chmod(path, PROFILE_STORE_MODE)
    return ref


def _store_auth_check(board, owner, incoming, runner_ctx=None):
    from auth_v2_contract import merge_auth_check, redact_auth_check
    if not _agent_rec(board, owner):
        checkin(board, owner)
    incoming = dict(incoming or {})
    if not incoming.get("execution_context"):
        incoming["execution_context"] = _auth_execution_context(
            board, owner, shlex.split(incoming.get("status_cmd") or "tickets"))
    probe_ctx = incoming.get("execution_context") or {}
    enrolled = _enrolled_runner_ctx(board, owner)
    previous = ((_agent_rec(board, owner) or {}).get("auth_check") or {})
    prev_ctx = (previous or {}).get("execution_context") or {}
    from auth_v2_contract import context_is_complete
    if (previous or {}).get("authoritative") and context_is_complete(prev_ctx):
        ctx = runner_ctx or prev_ctx
    elif (enrolled.get("runner_kind") == probe_ctx.get("runner_kind")
          and enrolled.get("hostname") == probe_ctx.get("hostname")
          and enrolled.get("username") == probe_ctx.get("username")
          and enrolled.get("agent_id") == probe_ctx.get("agent_id")):
        ctx = probe_ctx
    else:
        ctx = runner_ctx or enrolled
    harness = incoming.get("harness") or ""
    if incoming.get("state") == "ready" and not incoming.get("credential_profile_ref"):
        label = incoming.get("identity_label") or incoming.get("identity") or ""
        incoming["profile_kind"] = incoming.get("profile_kind") or _profile_kind_for(harness)
        try:
            incoming["credential_profile_ref"] = _persist_auth_profile(
                board, owner, harness, label, incoming["profile_kind"])
        except (OSError, ValueError):
            pass
    merged = merge_auth_check(previous, incoming, ctx)
    _agent_set(board, owner, auth_check=redact_auth_check(merged))
    _maybe_resume_auth_wake(board, owner, previous, merged)
    return (_agent_rec(board, owner) or {}).get("auth_check") or merged


def _maybe_resume_auth_wake(board, owner, previous, merged):
    from auth_v2_contract import NO_SPEND_STATES
    prev_state = (previous or {}).get("state")
    if prev_state not in NO_SPEND_STATES:
        return
    if (merged or {}).get("state") != "ready":
        return
    if lifecycle_of(board, owner) != "persistent":
        return
    rec = _agent_rec(board, owner) or {}
    if rec.get("auth_resume_at"):
        return
    harness, _ = harness_of(board, owner)
    try:
        from session_adapters import wake_seat
        wake_seat(board, owner, "auth resume", harness=harness, message_id="auth-resume")
    except Exception:
        pass
    _agent_set(board, owner, auth_resume_at=now())


def _refresh_auth_check(board, owner, harness=""):
    incoming = harness_auth_probe(board, owner, harness)
    return _store_auth_check(board, owner, incoming)


def _auth_blocks_model(auth):
    from auth_v2_contract import NO_SPEND_STATES
    rec = auth or {}
    pause = rec.get("pause") or {}
    if rec.get("state") in NO_SPEND_STATES and pause.get("retry_model") is False:
        if pause.get("paused") or rec.get("state") in NO_SPEND_STATES:
            return True
    return False


def _spawn_git_root(board, owner, worktree):
    """Git object database for `worktree add`. Origin, not dirname(board)."""
    from auth_v2_contract import repo_identity_matches, spawn_repo_identity_ok
    board_root = os.path.dirname(board)
    expected = _expected_origin_for(board, owner, worktree)
    exists = os.path.isdir(worktree)
    wt_origin = _git_remote_origin(worktree) if exists else ""
    if expected:
        candidates = []
        for path in (worktree if exists else "", board_root, os.getcwd()):
            root = _init_cwd_worktree_root(path) if path else ""
            if root and root not in candidates:
                candidates.append(root)
        git_root = ""
        for root in candidates:
            if repo_identity_matches(expected, _git_remote_origin(root)):
                git_root = root
                break
        if not git_root:
            return board_root, (
                "repo_mismatch: spawn git root origin must be %s (dirname(board) is not identity)"
                % expected)
        spawn_origin = _git_remote_origin(git_root)
        if not spawn_repo_identity_ok(expected, wt_origin or spawn_origin, spawn_origin,
                                     worktree_exists=exists):
            return git_root, "repo_mismatch: worktree origin does not match %s" % expected
        return git_root, ""
    return board_root, ""


def _classify_auth_output(rc, output):
    """Keep credentials, quota, expiry, network, and host failures separate."""
    text = (output or "").strip()
    low = text.lower()
    if _looks_limited(text):
        return "quota"
    if any(s in low for s in _AUTH_EXPIRED_STRINGS):
        return "expired"
    if _looks_auth(text) or "authentication required" in low or "login required" in low:
        return "login_required"
    if any(s in low for s in _AUTH_NETWORK_STRINGS):
        return "network"
    if rc == 0:
        return "ready"
    return "unavailable"


def harness_auth_probe(board, owner, harness="", timeout=15):
    """Cheap credential preflight; never starts a model or consumes a turn."""
    import subprocess

    resolved, _ = harness_of(board, owner, harness, "")
    spec = _harness_auth_spec(board, owner, resolved)
    ctx = _auth_execution_context(board, owner, (spec or (["tickets"], []))[0])
    if not spec:
        rec = {"state": "unsupported", "harness": resolved, "at": now(),
               "detail": "auth preflight is not defined for this harness", "login_cmd": "",
               "execution_context": ctx, "profile_kind": _profile_kind_for(resolved)}
        return rec
    status_cmd, login_cmd = spec
    ctx = _auth_execution_context(board, owner, status_cmd)
    env = _auth_probe_env()
    try:
        r = subprocess.run(status_cmd, capture_output=True, text=True, timeout=timeout,
                           stdin=subprocess.DEVNULL, env=env)
        output = ((r.stdout or "") + (r.stderr or "")).strip()
        state = _classify_auth_output(r.returncode, output)
        rc = r.returncode
    except FileNotFoundError:
        state, rc, output = "unavailable", 127, "%s is not installed" % status_cmd[0]
    except subprocess.TimeoutExpired:
        output = "authentication status timed out"
        state, rc = ("network" if any(s in output.lower() for s in _AUTH_NETWORK_STRINGS)
                    else "unavailable"), 124
    line = (output.splitlines()[0][:240] if output else "status returned no identity")
    identity = line if state == "ready" and output else ""
    return {
        "state": state, "harness": resolved, "at": now(), "exit": rc,
        "detail": line, "identity": identity, "identity_label": identity,
        "status_cmd": " ".join(status_cmd), "login_cmd": " ".join(login_cmd),
        "execution_context": ctx, "profile_kind": _profile_kind_for(resolved),
    }


def _print_auth_result(owner, result):
    labels = {
        "ready": "Ready",
        "login_required": "Login required",
        "expired": "Credential expired",
        "quota": "Usage quota reached",
        "network": "Network error",
        "unavailable": "Harness unavailable",
        "unsupported": "Auth check unsupported",
    }
    print("%s: %s" % (owner, labels.get(result.get("state"), result.get("state", "unknown"))))
    if result.get("identity_label") or result.get("identity"):
        print("  identity: %s" % (result.get("identity_label") or result.get("identity")))
    elif result.get("detail"):
        print("  detail:   %s" % result["detail"])
    if result.get("authoritative") is False:
        print("  context:  non-authoritative observation (enrolled runner unchanged)")
    ctx = result.get("execution_context") or {}
    if ctx.get("hostname"):
        print("  context:  %s@%s %s %s" % (
            ctx.get("username") or "?", ctx.get("hostname"),
            ctx.get("runner_kind") or "?", ctx.get("origin_url") or ""))
    if result.get("credential_profile_ref"):
        print("  profile:  %s" % result["credential_profile_ref"])
    path = (result.get("pause") or {}).get("operator_path")
    if result.get("state") in ("login_required", "expired") and result.get("login_cmd"):
        print("  recover:  %s" % result["login_cmd"])
        print("  verify:   tickets harness auth %s" % owner)
    elif path and path != "ready":
        print("  recover:  %s" % (result.get("login_cmd") or path))


def cmd_harness_auth(a, board):
    """Show auth, optionally run the interactive login, and verify afterward."""
    import subprocess

    owner = a.name or whoami()
    if owner.startswith("agent-"):
        sys.exit("harness auth needs an agent name: tickets harness auth <name>")
    if a.recover_stale:
        recovered = reclaim_stale_watch_lock(board, owner)
        print("watcher: %s" % recovered["detail"])
        if recovered["state"] == "live":
            sys.exit(2)
    result = _refresh_auth_check(board, owner, a.harness)
    _print_auth_result(owner, result)
    if a.login:
        if result.get("state") == "unsupported":
            sys.exit("interactive login is not supported for %s" % result.get("harness"))
        login_line = result.get("login_cmd") or ""
        spec = _harness_auth_spec(board, owner, result.get("harness") or a.harness)
        login_cmd = shlex.split(login_line) if login_line else (spec[1] if spec else [])
        if not login_cmd:
            sys.exit("no safe recovery command for %s" % owner)
        env = _auth_probe_env()
        rc = subprocess.call(login_cmd, env=env)
        if rc:
            sys.exit(rc)
        result = _refresh_auth_check(board, owner, a.harness)
        print("verification:")
        _print_auth_result(owner, result)
    if result.get("state") != "ready":
        sys.exit(1)


def _harness_check_label(check):
    """One column's worth of the last `harness check`, for `spawn --list`."""
    if not check:
        return "-"
    age = hours_since(check.get("at", ""))
    ms = check.get("latency_ms")
    return "%s %s" % ("ok" if check.get("ok") else "FAIL",
                      ("%.1fs" % (ms / 1000.0)) if isinstance(ms, (int, float)) else
                      ((fmt_hours(age) + " ago") if age is not None else ""))


def harness_probe(board, owner, harness="", cmd="", model="", cwd="", timeout=HARNESS_CHECK_TIMEOUT):
    """Run the agent's harness on a trivial prompt and report what happened.

    Returns the record stored on the agent: harness, cmd, ok, exit, latency_ms,
    output (head), at. The probe runs the REAL command shape -- same binary,
    same flags, same template -- with only the prompt swapped, because the
    failure this is for ("that harness is not installed / not logged in / the
    template is wrong") lives in the command, not in the model's answer.

    `ok` is exit status only. Whether the model actually said OK is reported in
    `replied`, and deliberately does not gate `ok`: a harness that answers a
    trivial prompt with a preamble is working, and a check that called that a
    failure would take a live agent out of the fleet.
    """
    import subprocess
    import time as _time

    harness, cmd_template = harness_of(board, owner, harness, cmd)
    cwd = cwd or (_agent_rec(board, owner) or {}).get("worktree") or os.path.dirname(board)
    if not os.path.isdir(cwd):
        cwd = os.path.dirname(board)
    prompt_file, cleanup = "", None
    if cmd_template:
        template_err = _harness_template_error(cmd_template)
        if template_err:
            return {"harness": harness, "cmd": cmd_template, "ok": False, "exit": 1,
                    "timed_out": False, "latency_ms": 0, "replied": False,
                    "output": template_err, "at": now()}
        if "{prompt_file}" in cmd_template:
            prompt_file, cleanup = _render_prompt_file(board, owner, text=HARNESS_PROBE_PROMPT)
        run_cmd = _expand_harness_cmd(cmd_template, agent=owner, cwd=cwd, prompt_file=prompt_file)
    else:
        run_cmd = _worker_cmd(board, owner, model, "bypassPermissions", harness,
                              prompt_expr=shlex.quote(HARNESS_PROBE_PROMPT))
    env = dict(_clean_git_env(), TICKET_AGENT=owner, TICKETS_DIR=board,
               TICKETS_PY=os.path.realpath(__file__),
               PATH=os.path.expanduser("~/.local/bin") + ":/opt/homebrew/bin:" + os.environ.get("PATH", ""))
    started = _time.time()
    out, rc, timed_out = "", None, False
    try:
        r = subprocess.run(run_cmd, shell=True, cwd=cwd, env=env, stdin=subprocess.DEVNULL,
                           capture_output=True, text=True, timeout=timeout)
        rc, out = r.returncode, ((r.stdout or "") + (r.stderr or ""))
    except subprocess.TimeoutExpired as e:
        timed_out, rc = True, 124
        out = "".join(x.decode("utf-8", "replace") if isinstance(x, bytes) else (x or "")
                      for x in (e.stdout, e.stderr))
    except OSError as e:
        rc, out = 127, str(e)
    finally:
        if cleanup:
            cleanup()
    latency_ms = int((_time.time() - started) * 1000)
    out = out.strip()
    return {"harness": harness, "cmd": run_cmd, "ok": rc == 0, "exit": rc, "timed_out": timed_out,
            "latency_ms": latency_ms, "replied": "ok" in out.lower()[:400],
            "output": out[:400], "at": now()}


def _chatgpt_codex_binaries(home):
    hits = []
    for rel in (
        os.path.join(".vscode", "extensions", "openai.chatgpt-*", "bin", "*", "codex"),
        os.path.join(".cursor", "extensions", "openai.chatgpt-*", "bin", "*", "codex"),
    ):
        hits.extend(glob.glob(os.path.join(home, rel)))
    out = []
    for p in hits:
        if os.path.isfile(p) and os.access(p, os.X_OK):
            out.append(p)
    out.sort(key=lambda p: os.path.getmtime(p), reverse=True)
    return out


def retarget_stale_local_codex(home):
    """Point ~/.local/bin/codex at the newest openai.chatgpt-* binary when stale.

    Stale means missing, a dangling symlink, or a symlink whose target is not
    the newest extension binary. Never overwrite a regular (non-symlink) file.
    """
    newest_list = _chatgpt_codex_binaries(home)
    if not newest_list:
        return ""
    newest = os.path.realpath(newest_list[0])
    local_dir = os.path.join(home, ".local", "bin")
    local = os.path.join(local_dir, "codex")
    if os.path.lexists(local) and not os.path.islink(local):
        return ""
    if os.path.islink(local) and os.path.realpath(local) == newest:
        return ""
    os.makedirs(local_dir, exist_ok=True)
    tmp = "%s.tmp-%s" % (local, os.getpid())
    try:
        os.symlink(newest, tmp)
        os.replace(tmp, local)
    except OSError:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        return ""
    return "retargeted %s -> %s" % (local, newest)


def _which_on_path(name, search_path):
    import shutil
    return shutil.which(name, path=search_path)


# Status/about only. A usage probe that includes these would spawn a model.
HARNESS_USAGE_SPAWN_TOKENS = frozenset({
    "-p", "--print", "--prompt", "--prompt-interactive", "-i", "exec", "--yolo",
})
HARNESS_USAGE_TIMEOUT = 8
_USAGE_REMAINING_KEYS = frozenset({
    "remaining", "remaining_percent", "remainingpercent", "remaining_pct",
    "remainingpct", "remaining_tokens", "remainingtokens", "percent_remaining",
    "percentremaining", "quota_remaining", "quotaremaining",
})
_USAGE_RESET_KEYS = frozenset({
    "reset", "reset_at", "resets_at", "resetsat", "resetat", "reset_time",
    "resettime", "resets", "resets_on", "resetson",
})


def usage_probe_is_spawn(argv):
    """True if argv would start a model session. Usage probes must stay False."""
    for t in (str(x).lower() for x in (argv or ())):
        if t in HARNESS_USAGE_SPAWN_TOKENS or t.split("=", 1)[0] in HARNESS_USAGE_SPAWN_TOKENS:
            return True
    return False


def catalog_usage_argv(spec_or_id):
    """Non-spawning usage argv for a catalog row (empty = no probe)."""
    spec = spec_or_id
    if isinstance(spec_or_id, str):
        spec = next((s for s in INTEGRATION_CATALOG if s["id"] == spec_or_id), {})
    args = tuple(spec.get("usage_args") or ())
    if usage_probe_is_spawn(args):
        return ()
    return args


def _usage_key_norm(key):
    return str(key or "").replace("-", "").replace("_", "").lower()


def _usage_field_str(value):
    if value is None or value is False:
        return None
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        if isinstance(value, float) and value != value:  # NaN
            return None
        if float(value) == int(value):
            return str(int(value))
        return str(value)
    text = str(value).strip()
    if not text or text.lower() in ("none", "null", "missing", "unknown", "n/a", "-"):
        return None
    return text


def _usage_fields_from_mapping(obj, remaining=None, reset=None):
    """Pull remaining/reset from a dict. Never invent; first hit wins."""
    if not isinstance(obj, dict):
        return remaining, reset
    for key, val in obj.items():
        norm = _usage_key_norm(key)
        if remaining is None and norm in _USAGE_REMAINING_KEYS:
            remaining = _usage_field_str(val)
        elif reset is None and norm in _USAGE_RESET_KEYS:
            reset = _usage_field_str(val)
    prefer = []
    for key, val in obj.items():
        if _usage_key_norm(key) in ("usage", "quota", "ratelimit", "ratelimits", "limits"):
            prefer.append(val)
    for val in prefer:
        remaining, reset = _usage_fields_from_mapping(val, remaining, reset)
        if remaining is not None and reset is not None:
            return remaining, reset
    for val in obj.values():
        if isinstance(val, dict):
            remaining, reset = _usage_fields_from_mapping(val, remaining, reset)
        elif isinstance(val, list):
            for item in val:
                remaining, reset = _usage_fields_from_mapping(item, remaining, reset)
        if remaining is not None and reset is not None:
            return remaining, reset
    return remaining, reset


def parse_usage_remaining_reset(text):
    """Extract remaining and reset when a harness reports them. Absent stays None."""
    remaining, reset = None, None
    for blob in _json_candidates(text):
        try:
            rec = json.loads(blob)
        except ValueError:
            continue
        if isinstance(rec, dict):
            remaining, reset = _usage_fields_from_mapping(rec, remaining, reset)
            if remaining is not None and reset is not None:
                return remaining, reset
    raw = text or ""
    if remaining is None:
        m = re.search(r"remaining[:\s]+([^\n,;]+)", raw, re.I)
        if m:
            remaining = _usage_field_str(m.group(1))
    if reset is None:
        m = re.search(r"resets?\s+(?:at\s+)?([^\n]+)", raw, re.I)
        if m:
            reset = _usage_field_str(m.group(1).rstrip("."))
    return remaining, reset


def _run_usage_probe(argv, env, timeout=HARNESS_USAGE_TIMEOUT):
    """Run a status/about probe. Never a model prompt. Returns (output, detail)."""
    import subprocess

    if not argv or usage_probe_is_spawn(argv):
        return "", "spawn probe refused" if argv else "no usage probe"
    try:
        r = subprocess.run(list(argv), capture_output=True, text=True, timeout=timeout,
                           stdin=subprocess.DEVNULL, env=env)
        out = ((r.stdout or "") + (r.stderr or "")).strip()
        return out, ""
    except FileNotFoundError:
        return "", "probe binary missing"
    except subprocess.TimeoutExpired:
        return "", "usage probe timed out"
    except OSError as e:
        return "", str(e)


def probe_catalog_usage(row, env=None, timeout=HARNESS_USAGE_TIMEOUT):
    """Usage check for one catalog row. Missing remaining or reset is FAIL."""
    spec = next((s for s in INTEGRATION_CATALOG if s["id"] == row.get("id")), {})
    args = catalog_usage_argv(spec)
    reason = ""
    output = ""
    if not row.get("on_disk") or not row.get("path"):
        remaining, reset = None, None
        reason = "missing binary"
    elif not args:
        remaining, reset = None, None
        reason = "no usage probe"
    else:
        probe_env = dict(env if env is not None else os.environ)
        argv = [row["path"]] + list(args)
        output, err = _run_usage_probe(argv, probe_env, timeout=timeout)
        remaining, reset = parse_usage_remaining_reset(output)
        reason = err
        if remaining is None or reset is None:
            if not reason:
                missing = []
                if remaining is None:
                    missing.append("remaining")
                if reset is None:
                    missing.append("reset")
                reason = "missing " + " and ".join(missing)
    ok = remaining is not None and reset is not None
    return {
        "usage": "ok" if ok else "FAIL",
        "remaining": remaining,
        "reset": reset,
        "usage_reason": reason,
        "usage_ok": ok,
    }


def attach_catalog_usage(rows, env=None, timeout=HARNESS_USAGE_TIMEOUT):
    """Fill usage/remaining/reset on every catalog row. Does not spawn."""
    for row in rows:
        row.update(probe_catalog_usage(row, env=env, timeout=timeout))
    return rows


def _fmt_usage_field(value):
    return value if value else "(missing)"


def probe_integration_catalog(home=None, search_path=None):
    """Every catalog row, including missing binaries. Does not spawn."""
    home = home if home is not None else os.path.expanduser("~")
    search_path = search_path if search_path is not None else os.environ.get("PATH", "")
    local_bin = os.path.join(home, ".local", "bin")
    if local_bin not in search_path.split(os.pathsep):
        search_path = local_bin + os.pathsep + search_path
    note = retarget_stale_local_codex(home)
    rows = []
    for spec in INTEGRATION_CATALOG:
        found = []
        for b in spec["binaries"]:
            loc = _which_on_path(b, search_path)
            if loc:
                found.append((b, loc))
        rows.append({
            "id": spec["id"],
            "name": spec["name"],
            "binaries": spec["binaries"],
            "found": found,
            "on_disk": bool(found),
            "path": found[0][1] if found else "",
            "if_yes": spec["if_yes"],
            "policy": spec["policy"],
            "usage_status": "",
        })
    return rows, note


def _print_catalog_usage_line(row):
    print("         usage %-4s remaining=%s  reset=%s%s" % (
        row.get("usage") or "FAIL",
        _fmt_usage_field(row.get("remaining")),
        _fmt_usage_field(row.get("reset")),
        ("  (%s)" % row["usage_reason"]) if row.get("usage_reason") and row.get("usage") != "ok" else ""))


def cmd_harness_usage(a, board):
    """Auto-check remaining/reset for every catalog row. Do not spawn."""
    home = os.path.expanduser("~")
    rows, note = probe_integration_catalog(home=home)
    attach_catalog_usage(rows)
    print("USAGE (probe only — do not spawn)")
    if note:
        print("codex: %s" % note)
    print("%-8s %-6s %-18s %s" % ("id", "usage", "remaining", "reset"))
    for r in rows:
        print("%-8s %-6s %-18s %s" % (
            r["id"], r.get("usage") or "FAIL",
            (_fmt_usage_field(r.get("remaining")))[:18],
            _fmt_usage_field(r.get("reset"))))
        if r.get("usage") != "ok" and r.get("usage_reason"):
            print("         %s" % r["usage_reason"])
        fail = _sounding().catalog_dispatch_fail(r)
        if fail:
            print("         FAIL %s" % r["policy"])
    print("")
    print("Missing remaining/reset is a FAIL row. Codex stays cataloged with zero usage.")
    print("Gemini dispatch records harness; persist/hooks wake (do not spawn a Gemini product job).")
    print("No new Claude fable. Cursor is the only spawn this desk uses.")


def cmd_harness_available(a, board):
    """Probe the integration catalog and auto-check usage. Print every row. Do not spawn."""
    home = os.path.expanduser("~")
    rows, note = probe_integration_catalog(home=home)
    attach_catalog_usage(rows)
    print_integration_catalog(rows, note)
    print("")
    print_recorded_usage(board)
    print("")
    print("USAGE: missing remaining/reset is a FAIL row. Do not spawn a FAIL seat.")
    if board_is_living(board):
        print("This is a living board. Do not invent a new team.")
        print("Announce the Atman role as atman-<seat>. CoS (cursor) staffs.")
        print("CEO does not claim worker tickets.")
    else:
        print("Ask: Which of these do you want to use?")
        print("Then ask the board/team name, then:")
        print('  tickets msg --to everyone "<name> is onboarding. Integrating: <list>. Objective and tasks next. @everyone"')
        print("Then ask for the objective and tasks. Do not spawn until they answer.")
    print("Codex stays in the catalog with zero usage. Gemini dispatch records harness; persist/hooks wake (do not spawn a Gemini product job). No new Claude fable.")


def cmd_harness(a, board):
    """`tickets harness check <name>` / `tickets harness list` / `available` / `usage`.

    check: prove the agent's harness actually runs before a watcher spends a
    poll interval discovering it does not. The result is written to the agent
    record so `spawn --list` and the master can see who is really reachable.
    available: probe command -v for every catalog integration (missing is a row)
    and auto-check usage (missing remaining/reset is FAIL). Does not spawn.
    usage: the usage table alone (same probes as available).
    """
    if a.harness_cmd == "available":
        return cmd_harness_available(a, board)
    if a.harness_cmd == "usage":
        return cmd_harness_usage(a, board)
    if a.harness_cmd == "auth":
        return cmd_harness_auth(a, board)
    if a.harness_cmd == "list":
        wf = load_workforce(board)
        names = sorted(set(list(wf) + [r["owner"] for r in load_agents(board)]))
        if not names:
            print("no agents registered yet -- tickets join <name> --harness ...")
            return
        print("%-16s %-12s %-14s %s" % ("agent", "harness", "check", "cmd"))
        for n in names:
            e = wf.get(n, {}) or {}
            rec = _agent_rec(board, n) or {}
            print("%-16s %-12s %-14s %s" % (
                n[:16], (e.get("harness") or e.get("tool") or "claude")[:12],
                _harness_check_label(rec.get("harness_check")),
                e.get("cmd") or "(built-in)"))
        return
    owner = a.name or whoami()
    if owner.startswith("agent-"):
        sys.exit("harness check needs an agent name: tickets harness check <name>")
    harness, cmd_template = harness_of(board, owner, a.harness, a.cmd_template)
    print("checking %s: harness=%s%s" % (owner, harness, (" cmd=%s" % cmd_template) if cmd_template else ""))
    res = harness_probe(board, owner, a.harness, a.cmd_template, a.model, a.cwd, a.timeout)
    _safe(lambda: _agent_set(board, owner, harness_check=res), None)
    print("  cmd:      %s" % res["cmd"])
    print("  exit:     %s%s" % (res["exit"], " (TIMEOUT after %ds)" % a.timeout if res["timed_out"] else ""))
    print("  latency:  %.1fs" % (res["latency_ms"] / 1000.0))
    print("  replied:  %s" % ("saw 'OK' in the output" if res["replied"] else "no 'OK' in the first 400 chars"))
    if res["output"]:
        print("  output:   %s" % res["output"].splitlines()[0][:160])
    print("%s: %s" % (owner, "harness OK" if res["ok"] else "HARNESS FAILED"))
    if not res["ok"]:
        sys.exit(1)


UI_HTML = r"""<!doctype html><html><head><meta charset="utf-8"><title>atman</title>
<meta name="viewport" content="width=device-width,initial-scale=1">
<style>
:root{color-scheme:dark;--bg:#0c0e12;--fg:#ece8e1;--mute:#9a958c;--line:#2a2d34;--card:#161820;--surface:#12141a;--chip:#1c2028;--acc:#c4b49a;--on-acc:#14120e;--ok:#6f9e96;--warn:#e0a53d;--bad:#e85d4c;--blocked:#e85d4c;--ready:#9a958c;--flight:#e0a53d;--review:#a99be8;--progress:#6f8c8f}
body[data-theme=light]{color-scheme:light;--bg:#f4f1eb;--fg:#14120e;--mute:#6e6a63;--line:#d8d3ca;--card:#fcfaf6;--surface:#eeeae3;--chip:#e8e3d9;--on-acc:#14120e;--ok:#3d6e68;--warn:#93610a;--bad:#b43a31;--blocked:#b43a31;--ready:#6e6a63;--flight:#93610a;--review:#6954a5;--progress:#789396}
*{box-sizing:border-box}html,body{height:100%;overflow-x:hidden}
body{margin:0;background:var(--bg);color:var(--fg);font:14px/1.45 ui-sans-serif,system-ui,-apple-system,Segoe UI,Helvetica,Arial,sans-serif;display:flex;flex-direction:column}
body.loading main{opacity:.55;pointer-events:none}
header.cmd{position:sticky;top:0;z-index:4;display:flex;flex-wrap:wrap;gap:10px 16px;align-items:center;padding:10px 16px;background:var(--card);border-bottom:1px solid var(--line)}
.brand{display:flex;align-items:center;gap:10px;min-width:148px}
.mark{color:var(--fg)}
.brand .mark{flex:none;width:22px;height:22px}
.wordmark{font:650 16px/1.2 ui-sans-serif,system-ui,-apple-system,Segoe UI,Helvetica,Arial,sans-serif;letter-spacing:.22em;text-transform:lowercase}
.brand .board-name{margin:2px 0 0;font:11px/1.3 ui-monospace,Menlo,monospace;letter-spacing:.02em;text-transform:none}
.sr-only{position:absolute!important;width:1px;height:1px;padding:0;margin:-1px;overflow:hidden;clip:rect(0,0,0,0);white-space:nowrap;border:0}
.portfolio{position:relative}
.portfolio summary{display:inline-flex;align-items:center;gap:5px;list-style:none;padding:2px 4px;border:0;border-radius:7px;background:transparent;color:var(--mute);font:11px/1.3 ui-monospace,Menlo,monospace;cursor:pointer}
.portfolio summary::-webkit-details-marker{display:none}
.portfolio .caret{color:var(--acc);font-weight:800}
.portfolio-menu{position:absolute;z-index:8;top:calc(100% + 7px);left:0;width:260px;padding:5px;background:var(--card);border:1px solid var(--line);border-radius:10px;box-shadow:0 14px 36px color-mix(in srgb,var(--bg) 72%,transparent)}
.product-row{display:grid;grid-template-columns:68px 1fr;gap:8px;padding:7px 9px;border-left:2px solid transparent;color:var(--mute)}
.product-row b{color:var(--fg);font:600 11px/1.35 ui-monospace,Menlo,monospace}
.product-row small{font-size:11px;line-height:1.35}
.product-row.current{border-left-color:var(--acc);background:var(--surface)}
.chips{display:flex;gap:6px;flex-wrap:wrap;align-items:center}
.chip{display:inline-flex;align-items:center;gap:6px;padding:3px 9px;border-radius:99px;background:var(--chip);border:1px solid var(--line);font-size:12px}
.chip b{font-weight:650}
.chip.master,.chip.cos{border-color:var(--line)}
.sprint{display:flex;flex-direction:column;gap:3px;min-width:180px;flex:1}
.sprint[hidden]{display:none}
.sprint .row{display:flex;justify-content:space-between;gap:8px;font-size:11px;color:var(--mute)}
.sprint .row span:first-child{min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.bar{height:5px;background:var(--surface);border-radius:99px;overflow:hidden}
.bar i{display:block;height:100%;background:var(--progress)}
.health{display:inline-flex;align-items:center;gap:6px;font-size:12px;font-weight:650}
.health i{width:8px;height:8px;border-radius:2px;background:var(--ok)}
.health.warn i{background:var(--warn)}
.health.bad i{background:var(--bad)}
#clock{font:12px/1.2 ui-monospace,Menlo,monospace;color:var(--mute)}
.conn{display:flex;align-items:center;gap:8px;margin-left:auto;flex-wrap:wrap}
.live-meta{position:relative}
.live-meta>summary{list-style:none;cursor:pointer;display:inline-flex;align-items:center}
.live-meta>summary::-webkit-details-marker{display:none}
.live-meta-pop{position:absolute;right:0;top:calc(100% + 6px);z-index:6;min-width:168px;padding:8px 10px;background:var(--card);border:1px solid var(--line);border-radius:8px;display:flex;flex-direction:column;gap:4px;font-size:11px;color:var(--mute);box-shadow:0 10px 24px color-mix(in srgb,var(--bg) 72%,transparent)}
.conn-status{font-size:11px;font-weight:700;letter-spacing:.06em;text-transform:uppercase;padding:2px 8px;border-radius:99px;border:1px solid var(--line)}
.conn-status.live{color:var(--ok);border-color:color-mix(in srgb,var(--ok) 45%,var(--line))}
.conn-status.reconnecting{color:var(--warn);border-color:color-mix(in srgb,var(--warn) 45%,var(--line))}
.conn-status.offline{color:var(--bad);border-color:color-mix(in srgb,var(--bad) 45%,var(--line))}
#lastUpdated{font-size:11px;color:var(--mute)}
#refreshBtn,#themeBtn{appearance:none;background:var(--surface);color:var(--fg);border:1px solid var(--line);border-radius:6px;padding:4px 8px;font:12px inherit;cursor:pointer}
#refreshBtn:disabled{opacity:.5;cursor:default}
#refreshBtn.spin{animation:spin .8s linear infinite}
@keyframes spin{to{transform:rotate(360deg)}}
.attention{margin:0 16px;padding:8px 0;border-bottom:1px solid var(--line)}
.attention summary{cursor:pointer;list-style:none;font-size:11px;letter-spacing:.08em;text-transform:uppercase;color:var(--mute);font-weight:700}
.attention summary::-webkit-details-marker{display:none}
.attn-list{display:flex;flex-direction:column;gap:6px;margin-top:8px}
.attn-item{font-size:12px;padding:6px 8px;border-radius:8px;background:var(--card);border:1px solid var(--line)}
.attn-item.CRIT{border-color:color-mix(in srgb,var(--bad) 55%,var(--line))}
.attn-item.WARN{border-color:color-mix(in srgb,var(--warn) 55%,var(--line))}
.epics{display:flex;flex-wrap:wrap;gap:8px;margin-bottom:4px}
.epic{min-width:180px;flex:1;background:var(--card);border:1px solid var(--line);border-radius:10px;padding:8px 10px}
.epic .row{display:flex;justify-content:space-between;gap:8px;font-size:11px;color:var(--mute);margin-bottom:4px}
.epic .id{font-weight:700;color:var(--fg)}
.tag.task{border-color:color-mix(in srgb,var(--flight) 45%,var(--line));color:var(--flight)}
.tag.ack{border-color:color-mix(in srgb,var(--ok) 45%,var(--line));color:var(--ok)}
.tag.pending{border-color:color-mix(in srgb,var(--warn) 45%,var(--line));color:var(--warn)}
.tag.limit{border-color:color-mix(in srgb,var(--bad) 45%,var(--line));color:var(--bad)}
.mission{margin:0;background:var(--card);border:1px solid var(--line);border-radius:12px;padding:0 12px}
.mission summary{cursor:pointer;list-style:none;padding:11px 0;color:var(--mute);font-size:12px;display:flex;gap:8px;align-items:baseline}
.mission summary::-webkit-details-marker{display:none}
.mission summary .k{letter-spacing:.08em;text-transform:uppercase;font-weight:700;color:var(--fg)}
.mission summary .one{flex:1;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;color:var(--fg)}
.mission pre{margin:0 0 10px;white-space:pre-wrap;font:12px/1.45 ui-monospace,Menlo,monospace;color:var(--mute)}
nav.tabs{display:flex;gap:4px;padding:8px 16px 0;border-bottom:1px solid var(--line)}
nav.tabs button{appearance:none;background:transparent;border:0;border-bottom:2px solid transparent;color:var(--mute);padding:8px 12px;font:13px/1 inherit;font-weight:650;cursor:pointer}
nav.tabs button.on{color:var(--fg);border-bottom-color:var(--acc)}
button:focus-visible,summary:focus-visible,select:focus-visible,input:focus-visible,textarea:focus-visible{outline:2px solid var(--acc);outline-offset:2px}
main{flex:1;min-width:0;min-height:0;width:100%;padding:14px 16px 18px;overflow:auto}
.pane{display:none;height:100%}
body[data-tab=objective] #pane-objective,body[data-tab=board] #pane-board,body[data-tab=agents] #pane-agents,body[data-tab=messages] #pane-messages{display:flex;flex-direction:column;gap:12px}
body[data-empty-board][data-tab=board] .kanban,
body[data-empty-board][data-tab=board] #objectivePromise,
body[data-empty-board][data-tab=board] #turnsPanel,
body[data-empty-board][data-tab=board] #onboardBox,
body[data-empty-board][data-tab=board] #workflowGraph,
body[data-empty-board][data-tab=board] #workViews,
body[data-empty-board][data-tab=board] #graphLede{display:none}
.kanban{display:grid;grid-template-columns:repeat(4,minmax(200px,1fr));gap:10px;align-items:start}
body[data-work-view=graph] .kanban{display:none}
body[data-work-view=columns] #workflowGraph,
body[data-work-view=columns] #graphLede{display:none}
.work-views{display:flex;gap:6px;align-items:center;flex-wrap:wrap}
.work-views button{appearance:none;background:transparent;border:1px solid var(--line);color:var(--mute);padding:4px 10px;font:12px/1 inherit;font-weight:650;border-radius:6px;cursor:pointer}
.work-views button.on{color:var(--fg);border-color:var(--acc);background:var(--chip)}
.graph-lede{margin:0;font-size:12px;color:var(--mute);max-width:720px}
.graph-lede b{color:var(--fg)}
.workflow-graph{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:12px 14px}
.g-roots,.g-kids{list-style:none;margin:0;padding:0}
.g-kids{margin:4px 0 0 14px;padding-left:12px;border-left:1px solid var(--line)}
.g-node{margin:6px 0}
.g-row{display:flex;flex-wrap:wrap;gap:6px 10px;align-items:baseline}
.g-row .mark{font:12px/1.3 ui-monospace,Menlo,monospace;color:var(--mute)}
.g-title{font-weight:600;font-size:13px}
.graph-empty{margin:0 0 10px;font-size:12px;color:var(--mute)}
@media(max-width:980px){.kanban{grid-template-columns:repeat(2,minmax(200px,1fr))}}
.col{background:var(--card);border:1px solid var(--line);border-radius:12px;min-height:120px;display:flex;flex-direction:column}
.col h2{margin:0;padding:10px 12px 8px;font-size:11px;letter-spacing:.08em;text-transform:uppercase;display:flex;justify-content:space-between;align-items:center}
.col h2 .n{font-variant-numeric:tabular-nums;color:var(--mute)}
.col.blocked h2{color:var(--blocked)}.col.ready h2{color:var(--ready)}.col.flight h2{color:var(--flight)}.col.review h2{color:var(--review)}
.col .list{padding:0 8px 10px;display:flex;flex-direction:column;gap:8px}
.card{background:var(--surface);border:1px solid var(--line);border-radius:10px;padding:8px 10px;display:flex;flex-direction:column;gap:4px}
.card.stale-warn{border-color:color-mix(in srgb,var(--warn) 55%,var(--line))}
.card.stale-bad{border-color:color-mix(in srgb,var(--bad) 70%,var(--line));background:color-mix(in srgb,var(--bad) 8%,var(--surface))}
.card-top{display:flex;gap:8px;align-items:center;font:11px/1 ui-monospace,Menlo,monospace}
.card-top .id{font-weight:700;color:var(--fg)}
.pri{color:var(--mute)}.card-title{font-weight:600;font-size:13px}
.card-meta{display:flex;gap:8px;align-items:center;flex-wrap:wrap;color:var(--mute);font-size:12px}
.wait{font-size:11px;color:var(--warn)}
.empty{color:var(--mute);font-size:12px;padding:8px}
.ok{color:var(--ok)}.warn{color:var(--warn)}.bad{color:var(--bad)}.mute{color:var(--mute)}
.mono{font-family:ui-monospace,Menlo,monospace;font-size:12px}
.who{display:inline-flex;align-items:center;gap:6px;white-space:nowrap}
.av{display:inline-flex;align-items:center;justify-content:center;width:18px;height:18px;border-radius:50%;background:var(--chip);color:var(--fg);border:1px solid var(--line);font-size:9px;font-weight:700;flex:none}
.agents{display:grid;grid-template-columns:repeat(auto-fill,minmax(240px,1fr));gap:10px}
.agent{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:12px;display:flex;flex-direction:column;gap:8px}
.agent.head{display:flex;justify-content:space-between;align-items:center;gap:8px}
.agent .st{font-size:11px;font-weight:700;letter-spacing:.04em;text-transform:uppercase}
.stats{display:grid;grid-template-columns:repeat(3,1fr);gap:6px;font-size:11px;color:var(--mute)}
.stats b{display:block;color:var(--fg);font-size:14px}
.mention{color:var(--fg);font-weight:650;background:var(--chip);border-radius:4px;padding:0 3px}
.msgs{display:flex;flex-direction:column;gap:8px;padding-bottom:8px}
.m{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:8px 10px}
.m .hd{display:flex;gap:8px;align-items:center;flex-wrap:wrap;font-size:12px;color:var(--mute);margin-bottom:4px}
.tag{display:inline-block;padding:1px 7px;border-radius:99px;font-size:11px;font-weight:600;border:1px solid var(--line);color:var(--mute)}
#composer{position:sticky;bottom:0;background:color-mix(in srgb,var(--bg) 88%,transparent);backdrop-filter:blur(8px);border:1px solid var(--line);border-radius:12px;padding:10px}
#composer textarea{width:100%;resize:vertical;min-height:56px;font:13px/1.4 inherit;background:var(--surface);color:var(--fg);border:1px solid var(--line);border-radius:8px;padding:8px}
#composer select,#composer button,#composer input{font:13px inherit;background:var(--surface);color:var(--fg);border:1px solid var(--line);border-radius:6px;padding:5px 8px}
#composer button{background:var(--acc);color:var(--on-acc);border-color:var(--acc);cursor:pointer;font-weight:700}
#composer button:disabled{opacity:.5;cursor:default}
#composerRow{display:flex;gap:8px;align-items:center;margin-bottom:6px;flex-wrap:wrap}
#mentionBar{display:flex;gap:6px;flex-wrap:wrap;margin-top:6px;min-height:22px}
.mchip{cursor:pointer;border:1px solid var(--line);background:var(--chip);border-radius:99px;padding:1px 9px;font-size:12px}
.mchip:hover{border-color:var(--acc)}
#composerMsg{font-size:12px;margin-top:4px;min-height:14px}
.seats-head{display:flex;gap:12px;align-items:flex-start;max-width:760px}
.seats-head .mark{flex:none;width:22px;height:22px;margin-top:2px}
.seats-title{margin:0 0 2px;font-size:11px;letter-spacing:.08em;text-transform:uppercase;font-weight:650}
.seats-lede{color:var(--mute);font-size:12px;margin:0;max-width:720px}
.seats-lede b{color:var(--fg)}
.next-step{display:flex;gap:10px 14px;align-items:flex-start;padding:10px 16px;background:var(--card);border-bottom:1px solid var(--line);font-size:13px;flex-wrap:wrap}
.next-step .lbl{font-weight:700;color:var(--acc);white-space:nowrap}
.next-step .msg{flex:1;min-width:160px}
.next-step .cmd{font:12px/1.35 ui-monospace,Menlo,monospace;color:var(--mute);white-space:nowrap}
.next-step.unreachable{background:color-mix(in srgb,var(--bad) 14%,var(--card));border-bottom-color:color-mix(in srgb,var(--bad) 35%,var(--line))}
.next-step.unreachable .lbl{color:var(--bad)}
.onboard{margin:0 16px;padding:10px 0 12px;border-bottom:1px solid var(--line)}
.onboard summary{cursor:pointer;list-style:none;font-size:11px;letter-spacing:.08em;text-transform:uppercase;color:var(--mute);font-weight:700;display:flex;gap:8px;align-items:center}
.onboard summary::-webkit-details-marker{display:none}
.onboard .ob-body{margin-top:8px}
.ob-steps{display:flex;flex-wrap:wrap;gap:8px 14px;font-size:12px}
.ob-step{display:inline-flex;align-items:center;gap:5px;color:var(--mute);flex-wrap:wrap}
.ob-step.done{color:var(--fg)}
.ob-step i{width:14px;height:14px;border-radius:3px;border:1px solid var(--line);display:inline-flex;align-items:center;justify-content:center;font-size:10px;font-style:normal;flex:none}
.ob-step.done i{background:var(--ok);border-color:var(--ok);color:#fff}
.empty-board{background:var(--card);border:1px dashed var(--line);border-radius:14px;padding:20px 18px;max-width:720px;margin-bottom:12px}
.empty-board.empty-secondary{padding:16px;margin:8px 0}
.agents>.empty-board{grid-column:1/-1}
.msgs>.empty-board{max-width:none}
.empty-board .empty-kicker{margin:0 0 6px;font-size:11px;letter-spacing:.08em;text-transform:uppercase;color:var(--mute);font-weight:700}
.empty-board p{margin:0 0 10px;color:var(--mute);font-size:13px;line-height:1.55}
.empty-board b{color:var(--fg)}
.empty-steps{list-style:none;margin:0;padding:0;display:flex;flex-direction:column;gap:10px}
.empty-steps li{display:flex;gap:10px;align-items:flex-start}
.empty-steps .n{flex:none;width:22px;height:22px;border-radius:50%;background:var(--chip);border:1px solid var(--line);display:inline-flex;align-items:center;justify-content:center;font-size:11px;font-weight:700;color:var(--fg)}
.empty-board .cta{margin-top:6px;font:12px/1.4 ui-monospace,Menlo,monospace;color:var(--acc)}
.empty-board .empty-cta{margin-top:14px}
.empty-honesty,.empty-intervene{margin-top:14px}
.col h2 .hint{font-weight:400;text-transform:none;letter-spacing:0;font-size:10px;color:var(--mute);display:block;margin-top:2px}
.stat-lbl{cursor:help;border-bottom:1px dotted var(--line)}
.hero-eyebrow{margin:0 0 6px;font-size:12px;color:var(--mute);font-weight:650}
.promise-strip{display:flex;gap:10px 14px;align-items:baseline;padding:6px 16px;border-bottom:1px solid var(--line);font-size:13px}
.promise-strip .lbl{font-weight:700;letter-spacing:.08em;text-transform:uppercase;font-size:11px;color:var(--mute)}
.promise-strip .msg{color:var(--mute)}
.chip.promise{border-color:var(--line)}
.promise-hero{display:flex;gap:32px;align-items:flex-end;padding:2px 0 12px;border-bottom:1px solid var(--line)}
.promise-card{display:flex;flex-direction:column;gap:2px;min-width:132px;background:transparent;border:0;padding:0}
.promise-card .k{font-size:11px;letter-spacing:.08em;text-transform:uppercase;color:var(--mute);font-weight:650}
.promise-card .v{font-size:32px;font-weight:650;font-variant-numeric:tabular-nums;letter-spacing:-.02em;line-height:1.05}
.promise-card .h{font-size:11px;color:var(--mute)}
.promise-panel{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:12px 14px}
.promise-panel h2{margin:0 0 4px;font-size:13px}
.promise-panel summary{cursor:pointer;list-style:none;font-size:13px;font-weight:650}
.promise-panel summary::-webkit-details-marker{display:none}
.turns-grid{display:grid;grid-template-columns:1fr 1fr;gap:12px}
.subh{font-size:11px;letter-spacing:.08em;text-transform:uppercase;color:var(--mute);margin:8px 0 4px}
.promise-table{width:100%;border-collapse:collapse;font-size:13px}
.promise-table th,.promise-table td{text-align:left;padding:4px 6px;border-bottom:1px solid var(--line)}
.promise-table .num{text-align:right;font-variant-numeric:tabular-nums}
.usage-cards{display:grid;grid-template-columns:repeat(auto-fill,minmax(140px,1fr));gap:8px;margin:8px 0}
.usage-card{background:var(--surface);border:1px solid var(--line);border-radius:10px;padding:10px}
.usage-card .k{font-size:11px;letter-spacing:.06em;text-transform:uppercase;color:var(--mute)}
.usage-card .v{font-size:18px;font-weight:650;font-variant-numeric:tabular-nums}
.seats{display:flex;flex-direction:column;gap:8px}
.lane{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:10px 12px}
.lane .lbl{font-size:10px;letter-spacing:.08em;text-transform:uppercase;color:var(--mute);font-weight:700;margin-bottom:8px}
.lane .row{display:flex;gap:8px;flex-wrap:wrap;align-items:stretch}
.lane.ready .lbl{color:var(--ready)}
.lane.flight .lbl{color:var(--flight)}
.lane.review .lbl{color:var(--review)}
.seat{min-width:120px;max-width:160px;background:var(--surface);border:1px solid var(--line);border-radius:10px;padding:8px 10px;display:flex;flex-direction:column;gap:4px}
.seat .top{display:flex;align-items:center;gap:8px}
.seat .av{width:22px;height:22px;font-size:9px}
.seat .nm{font-size:12px;font-weight:650;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.seat .cov{font-size:11px;color:var(--mute)}
.seat.operator .av,.seat.cover .av{background:var(--chip)}
.seat.ghost{border-style:dashed}
.seat.ghost .av{background:transparent;border:1px dashed var(--mute);color:var(--mute)}
.seat.idle .av{background:var(--chip);color:var(--mute)}
.seat[data-seat-chat]{cursor:pointer}
.intervene{appearance:none;background:var(--acc);border:1px solid var(--acc);color:var(--on-acc);border-radius:6px;padding:3px 9px;font:11px/1.2 inherit;font-weight:700;cursor:pointer;align-self:flex-start}
.intervene:hover{filter:brightness(.96)}
.chat-layout{display:flex;gap:12px;align-items:stretch;min-height:0;flex:1}
.chat-rail{min-width:168px;max-width:220px;display:flex;flex-direction:column;gap:4px}
.chat-rail .seats-title{margin:0 0 4px}
.chat-rail button.thread{appearance:none;display:flex;justify-content:space-between;gap:8px;width:100%;text-align:left;background:var(--surface);color:var(--fg);border:1px solid var(--line);border-radius:8px;padding:7px 9px;font:12px/1.3 inherit;cursor:pointer}
.chat-rail button.thread.on{border-color:var(--acc);color:var(--fg)}
.chat-rail button.thread .n{color:var(--mute);font-variant-numeric:tabular-nums}
.chat-main{flex:1;min-width:0;display:flex;flex-direction:column;gap:8px}
.chat-head .seats-title{margin:0 0 2px}
#cTo:disabled{opacity:.7}
@media(max-width:700px){.seat{min-width:108px}.chat-layout{flex-direction:column}.chat-rail{max-width:none;flex-direction:row;flex-wrap:wrap}}
@media(max-width:600px){
  header.cmd{flex-direction:column;align-items:stretch}
  .conn{display:flex;justify-content:flex-start;margin-left:0}
  .live-meta-pop{left:0;right:auto}
  .next-step,.promise-strip{flex-direction:column;align-items:flex-start}
  .next-step .cmd{white-space:normal;overflow-wrap:anywhere}
  .promise-strip .msg{max-width:100%;overflow-wrap:anywhere}
  .ob-steps{flex-direction:column;align-items:flex-start}
  .kanban{grid-template-columns:1fr}
  .promise-hero{flex-wrap:wrap;gap:16px}
  .turns-grid{grid-template-columns:1fr}
  nav.tabs{overflow-x:auto;flex-wrap:nowrap;-webkit-overflow-scrolling:touch}
  .agents{grid-template-columns:1fr}
  .sprint{min-width:0}
  .epic{min-width:100%;flex-basis:100%}
  .portfolio-menu{position:fixed;left:12px;right:12px;top:auto;width:auto}
}
@media(prefers-reduced-motion:reduce){*{scroll-behavior:auto!important;transition:none!important;animation:none!important}}
</style></head><body data-tab="board" data-work-view="graph">
<header class="cmd">
  <div class="brand">
    <svg class="mark" viewBox="0 0 32 32" width="22" height="22" role="img" aria-label="atman">
      <circle cx="10" cy="7.8" r="3.35" fill="currentColor"/>
      <circle cx="22.4" cy="8.8" r="3.35" fill="currentColor"/>
      <circle cx="6.6" cy="17.6" r="3.35" fill="currentColor"/>
      <circle cx="25.4" cy="18.2" r="3.35" fill="currentColor"/>
      <circle cx="16" cy="24.6" r="3.35" fill="currentColor"/>
    </svg>
    <div>
      <span class="wordmark">atman</span>
      <h1 id="title" class="sr-only">Atman</h1>
      <p class="board-name mute" id="boardName" hidden data-kind="workspace"></p>
    </div>
  </div>
  <details class="portfolio" id="portfolioSwitch">
    <summary aria-label="Products, current product Atman"><span class="caret" aria-hidden="true">^</span><span>Products · Atman</span></summary>
    <div class="portfolio-menu" aria-label="Product workspaces">
      <div class="product-row current" aria-current="page"><b>Atman</b><small>Coordinate this team</small></div>
      <div class="product-row"><b>Brahman</b><small>Model communication · not connected</small></div>
      <div class="product-row"><b>ATI</b><small>Experiment lab · not connected</small></div>
      <div class="product-row"><b>steer.md</b><small>Policy for output · separate product</small></div>
    </div>
  </details>
  <div class="chips" id="chips"></div>
  <div class="chips promise-chips" id="promiseChips">
    <span class="chip promise" id="hdrMedian"><b>median turns</b> <span id="hdrMedianVal">—</span></span>
    <span class="chip promise" id="hdrYield"><b>yield@cost</b> <span id="hdrYieldVal">—</span></span>
  </div>
  <div class="sprint" id="sprint"></div>
  <div class="health" id="pulse"><i></i><span>No alerts</span></div>
  <div class="conn" id="connBar">
    <details class="live-meta" id="liveMeta">
      <summary aria-label="Connection and last updated">
        <span id="connStatus" class="conn-status live" aria-live="polite">live</span>
      </summary>
      <div class="live-meta-pop">
        <span id="clock"></span>
        <span id="lastUpdated" class="mute">—</span>
      </div>
    </details>
    <button type="button" id="refreshBtn" title="Refresh board" aria-label="Refresh board">↻</button>
    <button type="button" id="themeBtn" title="Switch to light mode" aria-label="Switch to light mode" aria-pressed="false"><span id="themeLabel">Light mode</span></button>
  </div>
</header>
<details class="attention" id="attentionBox" hidden><summary>Needs attention <span id="attnCount" class="mute">0</span></summary>
  <div class="attn-list" id="attnList"></div></details>
<div class="next-step" id="nextStep" hidden><span class="lbl">Next</span><span class="msg">loading…</span></div>
<div class="promise-strip" id="promiseStrip" data-fold="objective"><span class="lbl">Objective</span><span class="msg" id="promiseStripLine">Fewest turns. Max output at least cost.</span><span id="wakeGates" hidden></span></div>
<details class="onboard" id="onboardBox"><summary>Onboarding <span id="obProgress" class="mute">0/7</span></summary>
  <div class="ob-body"><div class="ob-steps" id="obSteps"></div></div></details>
<nav class="tabs" role="tablist" aria-label="Atman workspace">
  <button type="button" id="tab-objective" role="tab" aria-controls="pane-objective" aria-selected="false" data-tab-btn="objective">Objective</button>
  <button type="button" id="tab-agents" role="tab" aria-controls="pane-agents" aria-selected="false" data-tab-btn="agents">Team</button>
  <button type="button" id="tab-board" role="tab" aria-controls="pane-board" aria-selected="true" data-tab-btn="board" class="on">Work</button>
  <button type="button" id="tab-messages" role="tab" aria-controls="pane-messages" aria-selected="false" data-tab-btn="messages">Intervene</button>
</nav>
<main>
<div class="pane" id="pane-objective" role="tabpanel" aria-labelledby="tab-objective" tabindex="0">
  <details class="mission" id="missionBox" open><summary><span class="k">Standing objective</span><span class="one" id="missionOne"></span></summary><pre id="goals"></pre></details>
</div>
<div class="pane" id="pane-board" role="tabpanel" aria-labelledby="tab-board" tabindex="0">
  <div id="emptyBoard" class="empty-board" hidden></div>
  <section id="epicsPanel" class="epics" hidden aria-label="Epic progress"></section>
  <section id="objectivePromise" data-fold="objective">
    <p class="hero-eyebrow" id="heroEyebrow" hidden>Fewest turns. Max output at least cost.</p>
    <div class="promise-hero" id="promiseHero" role="region" aria-label="Fewest turns. Max output at least cost."><!-- V1 MUST: home median turns + yield@cost; T-344 worst-10 is NICE only -->
      <article class="promise-card" id="heroMedian"><div class="k">Median turns</div><div class="v" id="heroMedianVal">—</div><div class="h" id="heroMedianHint">Lower is better · unknown is not zero</div></article>
      <article class="promise-card" id="heroYield"><div class="k">Yield@cost</div><div class="v" id="heroYieldVal">—</div><div class="h" id="heroYieldHint">Done tickets per USD of harness-reported cost</div></article>
    </div>
  </section>
  <div class="work-views" id="workViews" role="tablist" aria-label="Work view">
    <button type="button" id="view-graph" data-work-view="graph" class="on" aria-selected="true">Graph</button>
    <button type="button" id="view-columns" data-work-view="columns" aria-selected="false">Columns</button>
  </div>
  <p class="graph-lede" id="graphLede"><b>What waits on what.</b> Same <span class="mono">--after</span> edges as <span class="mono">tickets graph</span> / <span class="mono">tickets map</span> — not a list of titles. Follow-up: <span class="mono">tickets update</span> / <span class="mono">here</span>. Silent &gt;90m: <span class="mono">tickets reopen</span>. Review: <span class="mono">tickets review</span> then <span class="mono">tickets merge</span>.</p>
  <div id="workflowGraph" class="workflow-graph" aria-label="Workflow dependency graph"></div>
  <div class="kanban">
    <section class="col blocked"><h2 title="Work that cannot proceed until a dependency or blocker is resolved">Blocked <span class="n" id="n-blocked">0</span><span class="hint">waiting on a fix or dependency</span></h2><div class="list" id="col-blocked"></div></section>
    <section class="col ready"><h2 title="Tickets unblocked and waiting for an agent to claim">Ready <span class="n" id="n-ready">0</span><span class="hint">unowned work anyone can take</span></h2><div class="list" id="col-ready"></div></section>
    <section class="col flight"><h2 title="Tickets actively being worked right now">In flight <span class="n" id="n-flight">0</span><span class="hint">claimed and in progress</span></h2><div class="list" id="col-flight"></div></section>
    <section class="col review"><h2 title="Finished work waiting for master to merge to main">Review <span class="n" id="n-review">0</span><span class="hint">submitted, awaiting merge</span></h2><div class="list" id="col-review"></div></section>
  </div>
  <details class="promise-panel" id="turnsPanel">
    <summary>Turns efficiency</summary>
    <small id="turnsSummary" class="mute"></small>
    <div class="turns-grid"><div><h3 class="subh">Worst tickets (watch runs)</h3><table class="promise-table" id="turnsWorst"></table></div><div><h3 class="subh">Per-agent median</h3><table class="promise-table" id="turnsAgents"></table></div></div>
  </details>
</div>
<div class="pane" id="pane-agents" role="tabpanel" aria-labelledby="tab-agents" tabindex="0">
  <div class="seats-head">
    <svg class="mark" viewBox="0 0 32 32" width="22" height="22" role="img" aria-label="atman">
      <circle cx="10" cy="7.8" r="3.35" fill="currentColor"/>
      <circle cx="22.4" cy="8.8" r="3.35" fill="currentColor"/>
      <circle cx="6.6" cy="17.6" r="3.35" fill="currentColor"/>
      <circle cx="25.4" cy="18.2" r="3.35" fill="currentColor"/>
      <circle cx="16" cy="24.6" r="3.35" fill="currentColor"/>
    </svg>
    <div>
      <h2 class="seats-title">Team</h2>
      <p class="seats-lede" id="coverageLede"><b>Who’s present. What’s uncovered.</b> Coverage by work, not fixed role.</p>
      <p class="seats-lede">Intervene · <b>Msg</b> opens that seat’s thread — <span class="mono">tickets msg --to</span>.</p>
    </div>
  </div>
  <div class="seats" id="seats">
    <section class="lane ready" data-lane="ready"><div class="lbl">Ready</div><div class="row" id="lane-ready"></div></section>
    <section class="lane flight" data-lane="flight"><div class="lbl">In flight</div><div class="row" id="lane-flight"></div></section>
    <section class="lane review" data-lane="review"><div class="lbl">Review</div><div class="row" id="lane-review"></div></section>
    <section class="lane" data-lane="operator"><div class="lbl">Operator · master / CoS</div><div class="row" id="lane-operator"></div></section>
    <section class="lane" data-lane="idle"><div class="lbl">Idle · down</div><div class="row" id="lane-idle"></div></section>
  </div>
  <section class="promise-panel" id="usagePanel">
    <h2>Usage / cost</h2>
    <small id="usageHonesty" class="mute">Harness-reported only. Not reported by harness stays — never a made-up $0.</small>
    <div class="usage-cards" id="usageCards"></div>
    <table class="promise-table" id="usageAgents"></table>
  </section>
  <div class="agents" id="agents"></div>
</div>
<div class="pane" id="pane-messages" role="tabpanel" aria-labelledby="tab-messages" tabindex="0">
  <div class="chat-layout">
    <nav class="chat-rail" id="chatRail" aria-label="Threads"></nav>
    <div class="chat-main">
      <div class="chat-head" id="chatHead"></div>
      <div class="msgs" id="msgs"></div>
      <section id="composer">
        <div id="composerRow">
          <label class="who"><small>from</small> <select id="cFrom"></select></label>
          <label class="who"><small>to</small> <select id="cTo"><option value="">everyone</option></select></label>
          <label class="who"><small>re</small> <input id="cRe" placeholder="T-000" size="6" style="width:80px"></label>
          <label class="who"><small>type</small> <select id="cKind"><option value="message">message</option><option value="task">task</option></select></label>
        </div>
        <textarea id="cText" aria-label="Message" placeholder="Message the board or a seat. Type @ to tag an agent."></textarea>
        <div id="mentionBar"></div>
        <div id="composerRow" style="margin-top:8px"><button id="cSend">Post</button><small id="composerMsg"></small></div>
      </section>
    </div>
  </div>
</div>
</main>
<script>
const esc=s=>String(s??'').replace(/[&<>]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;'}[c]));
const h=x=>x==null?'-':(x<1?Math.round(x*60)+'m':x<48?x.toFixed(1)+'h':(x/24).toFixed(1)+'d');
// Server sends timestamps as raw ISO-8601 UTC. Render in whatever timezone
// this browser is actually in — no explicit timeZone option, so Intl uses
// the host local zone. A missing offset is treated as UTC, not already-local.
const parseUtc=iso=>{const s=String(iso||'').trim();return /^\d{4}-\d{2}-\d{2}[ T]\d{2}:\d{2}(:\d{2})?(\.\d+)?$/.test(s)?s.replace(' ','T')+'Z':s};
const fmtLocal=iso=>{if(!iso)return '-';const d=new Date(parseUtc(iso));return isNaN(d)?String(iso):d.toLocaleString(undefined,{month:'short',day:'numeric',hour:'2-digit',minute:'2-digit',timeZoneName:'short'});};
const fmtRel=iso=>{const d=new Date(parseUtc(iso));if(isNaN(d))return '';const ms=Date.now()-d.getTime(),abs=Math.abs(ms),m=Math.round(abs/6e4),h=Math.round(abs/36e5),day=Math.round(abs/864e5);const u=m<1?'just now':m<60?m+'m':h<48?h+'h':day+'d';return u==='just now'?u:(ms>=0?u+' ago':'in '+u);};
const fmtWhen=iso=>{const a=fmtLocal(iso);if(!iso||a==='-'||a===String(iso))return a;const r=fmtRel(iso);return r?r+' · '+a:a};
function initials(name){const s=String(name||'?').split(/[-_ ]/).filter(Boolean);
  return ((s[0]||'?')[0]+(s.length>1?s[1][0]:(s[0]||'?')[1]||'')).toUpperCase()}
function who(name){if(!name)return '';return '<span class="who"><span class="av">'+esc(initials(name))+'</span>'+esc(name)+'</span>'}
function mentionText(text){
  return esc(text).split(/(```[\s\S]*?```|`[^`]*`)/g).map((part,i)=>i%2
    ?part
    :part.replace(/(^|[^\w@])@([A-Za-z][A-Za-z0-9_-]{0,47})/g,
      (m,pre,handle)=>pre+'<span class="mention">@'+handle+'</span>')
  ).join('')
}
function missionLine(goals){
  const raw=String(goals||'').trim();
  if(!raw)return '(no MASTER.md yet -- tickets master init)';
  const lines=raw.split(/\n/).map(l=>l.trim()).filter(Boolean);
  const first=lines.find(l=>!/^(OBJECTIVE|MISSION|CEO MEMO|SPRINT PLAN)\b/i.test(l))||lines[0];
  return first.replace(/^[#>*\-]+\s*/,'').slice(0,180);
}
function staleClass(t){
  if(t.since_update==null)return '';
  if(t.since_update>1.5)return 'stale-bad';
  if(t.since_update>0.75)return 'stale-warn';
  return '';
}
function card(t,extra){
  const waiting=(t.waiting||[]).length?'<div class="wait">waiting on '+esc((t.waiting||[]).join(', '))+'</div>':'';
  const owner=t.owner?who(t.owner):'<span class="mute">unassigned</span>';
  const pri=t.priority!=null?'<span class="pri">P'+esc(t.priority)+'</span>':'';
  const age=t.since_update!=null?'<span class="'+(t.since_update>1.5?'bad':t.since_update>0.75?'warn':'ok')+'">'+h(t.since_update)+'</span>':'';
  const msg=t.owner?'<button type="button" class="intervene" data-seat-chat="'+esc(t.owner)+'" data-testid="card-msg-'+esc(t.id)+'">Msg</button>':'';
  return '<article class="card '+staleClass(t)+'"><div class="card-top"><span class="id">'+esc(t.id)+'</span>'+pri+'</div><div class="card-title">'+esc(t.title)+'</div><div class="card-meta">'+owner+age+'</div>'+waiting+msg+(extra||'')+'</article>';
}
function fillCol(id,items,html){
  document.getElementById('n-'+id).textContent=items.length;
  document.getElementById('col-'+id).innerHTML=items.length?html:('<div class="empty">none</div>');
}
function emptyCraft(kicker,lead,honesty,cta){
  const cmds=(Array.isArray(cta)?cta:[cta]).map(c=>'<div class="cta">'+c+'</div>').join('');
  return '<div class="empty-board empty-secondary">'+
    '<p class="empty-kicker">'+kicker+'</p>'+
    '<p>'+lead+'</p>'+
    '<p class="empty-honesty">'+honesty+'</p>'+
    cmds+'</div>';
}
const dash=x=>x==null?'—':String(x);
function money(n){return n==null?'—':('$'+(Number(n)<0.01&&Number(n)>0?Number(n).toFixed(4):Number(n).toFixed(2)))}
function fmtMedian(p){return(!p||p.median_turns==null)?'—':Number(p.median_turns).toFixed(p.median_turns%1?2:0)}
function fmtYield(p){return(!p||p.yield_per_usd==null)?'—':(Number(p.yield_per_usd).toFixed(2)+' per $')}
function setTxt(id,v){const el=document.getElementById(id);if(el)el.textContent=v}
function renderObjective(o){
  const line=document.getElementById('promiseStripLine');
  const gates=document.getElementById('wakeGates');
  if(gates)gates.textContent=(o&&o.wake_gates)||'';
  if(!line)return;
  if(!o||!o.text){line.textContent='Fewest turns. Max output at least cost.';return;}
  const bits=[(o.state||'active'), o.text];
  if(o.exit_criterion)bits.push('exit: '+o.exit_criterion);
  else if(o.exit_missing)bits.push('FLAG: no exit criterion');
  if(o.stop_condition)bits.push('stop: '+o.stop_condition);
  line.textContent=bits.join(' · ');
}
function renderPromise(p){
  const med=fmtMedian(p),yld=fmtYield(p);
  setTxt('heroMedianVal',med);setTxt('hdrMedianVal',med);
  setTxt('heroYieldVal',yld);setTxt('hdrYieldVal',yld);
  const yh=document.getElementById('heroYieldHint');
  if(yh){
    if(!p||p.yield_per_usd==null)yh.textContent=(p&&p.n_unmeasured_cost)?'done tickets with no harness cost — yield@cost unknown, not $0':'done tickets per USD of harness-reported cost';
    else yh.textContent=(p.done_with_cost||0)+' done / '+money(p.cost_usd)+' · '+(p.n_unmeasured_cost||0)+' done with cost unknown';
  }
  const panel=document.getElementById('turnsPanel');
  const empty=document.body.hasAttribute('data-empty-board');
  if(panel){
    if(empty)panel.open=false;
    else if((!p||p.median_turns==null)&&(!p||p.yield_per_usd==null))panel.open=true;
  }
}
function renderTurns(t){
  const sum=document.getElementById('turnsSummary'),worst=document.getElementById('turnsWorst'),agents=document.getElementById('turnsAgents');
  const row=cells=>'<tr>'+cells.map(c=>'<td>'+c+'</td>').join('')+'</tr>';
  if(!t||t.v!==1){sum.textContent='';worst.innerHTML=row(['—','','','']);agents.innerHTML=row(['—','','','']);return;}
  const agg=t.aggregates||{},measured=(t.tickets||[]).filter(r=>r.turns!=null).sort((a,b)=>b.turns-a.turns||(a.ticket>b.ticket?1:-1)).slice(0,10);
  sum.textContent='measured '+((agg.n)||0)+' ticket(s); unmeasured '+((agg.n_unmeasured)||0)+' (no run_end — typically backfill)';
  worst.innerHTML='<tr><th>ticket</th><th>owner</th><th class="num">turns</th><th>outcome</th></tr>'+(measured.length?measured.map(r=>row([esc(r.ticket),esc(r.owner||'—'),'<span class="num">'+r.turns+'</span>',esc(r.outcome||'-')])).join(''):row(['—','','','']));
  const by=agg.by_agent||[];
  agents.innerHTML='<tr><th>agent</th><th class="num">n</th><th class="num">median</th><th class="num">mean</th></tr>'+(by.length?by.map(r=>row([esc(r.agent),'<span class="num">'+r.n+'</span>','<span class="num">'+dash(r.median)+'</span>','<span class="num">'+dash(r.mean)+'</span>'])).join(''):row(['—','','','']));
}
function renderUsage(u){
  const cards=document.getElementById('usageCards'),tbl=document.getElementById('usageAgents');
  const cell=(k,v)=>'<div class="usage-card"><div class="k">'+esc(k)+'</div><div class="v">'+v+'</div></div>';
  if(!u){cards.innerHTML=cell('Cost','—');tbl.innerHTML='';return;}
  cards.innerHTML=cell('Cost',money(u.cost_usd))+cell('Tokens in',dash(u.tokens_in))+cell('Tokens out',dash(u.tokens_out))+cell('Runs with cost',String(u.n_runs_with_cost||0))+cell('Runs unmeasured',String(u.n_runs_unmeasured||0));
  const by=u.by_agent||[];
  tbl.innerHTML='<tr><th>agent</th><th class="num">cost</th><th class="num">tokens in</th><th class="num">with cost</th><th class="num">unmeasured</th></tr>'+(by.length?by.map(r=>'<tr><td>'+esc(r.agent)+'</td><td class="num">'+money(r.cost_usd)+'</td><td class="num">'+dash(r.tokens_in)+'</td><td class="num">'+esc(r.n_runs_with_cost)+'</td><td class="num">'+esc(r.n_runs_unmeasured)+'</td></tr>').join(''):'<tr><td colspan="5">Not reported by harness</td></tr>');
}
function seatChip(name,cover,kind){
  const chat=kind==='ghost'?'':' data-seat-chat="'+esc(name)+'"';
  const act=kind==='ghost'?'':'<button type="button" class="intervene" data-seat-chat="'+esc(name)+'">Msg</button>';
  return '<article class="seat '+(kind||'cover')+'"'+chat+'><div class="top"><span class="av">'+esc(initials(name))+'</span><span class="nm">'+esc(name)+'</span></div><span class="cov">'+esc(cover||'')+'</span>'+act+'</article>';
}
function renderSeats(d){
  const placed=new Set();
  const operator=[],review=[],flight=[],ready=[],idle=[];
  const add=(arr,name,cover,kind)=>{if(!name||placed.has(name))return;placed.add(name);arr.push(seatChip(name,cover,kind))};
  add(operator,d.master,'master','operator');
  add(operator,d.cos,'CoS','operator');
  (d.review||[]).forEach(t=>add(review,t.owner,t.id+' · review','cover'));
  (d.in_flight||[]).forEach(t=>add(flight,t.owner,t.id+' · busy','cover'));
  const openReady=(d.open||[]).filter(t=>t.status!=='BLOCKED'&&!(t.waiting||[]).length);
  openReady.forEach(t=>{if(t.owner)add(ready,t.owner,t.id+' · ready','cover');else ready.push(seatChip(t.id,'Open seat — uncovered work.','ghost'))});
  const utilBy={};(d.util||[]).forEach(u=>{utilBy[u.agent]=u});
  (d.agents||[]).forEach(a=>{
    if(placed.has(a.name))return;
    if(a.lifecycle==='ephemeral' && a.reachable===false)return;
    const hint=(a.roles&&a.roles.length)?a.roles.join('/'):'any lane';
    const u=utilBy[a.name]||{};
    const quota=u.util_pct!=null?' · '+Math.round(u.util_pct)+'%':'';
    const st=a.state==='DOWN'?'down':'idle';
    idle.push(seatChip(a.name,st+' · '+hint+quota,a.state==='DOWN'||a.reachable===false?'ghost':'idle'));
  });
  const put=(id,html,empty)=>document.getElementById(id).innerHTML=html||('<div class="empty">'+empty+'</div>');
  put('lane-operator',operator.join(''),'no operator');
  put('lane-review',review.join(''),'nobody covering review');
  put('lane-flight',flight.join(''),'nobody in flight');
  put('lane-ready',ready.join(''),'Open seat — uncovered work.');
  put('lane-idle',idle.join(''),'no idle seats');
}
function setTab(name){
  const allowed=new Set(['objective','agents','board','messages']);
  if(!allowed.has(name))name='board';
  document.body.dataset.tab=name;
  try{localStorage.setItem('tickets-ui-tab',name)}catch(e){}
  document.querySelectorAll('[data-tab-btn]').forEach(b=>{
    const on=b.dataset.tabBtn===name;
    b.classList.toggle('on',on);
    b.setAttribute('aria-selected',on?'true':'false');
    b.tabIndex=on?0:-1;
  });
}
function setWorkView(name, persistHash){
  if(name!=='columns')name='graph';
  document.body.dataset.workView=name;
  try{localStorage.setItem('tickets-ui-work-view',name)}catch(e){}
  document.querySelectorAll('#workViews [data-work-view]').forEach(b=>{
    const on=b.dataset.workView===name;
    b.classList.toggle('on',on);
    b.setAttribute('aria-selected',on?'true':'false');
  });
  if(persistHash){
    try{history.replaceState(null,'','#'+name)}catch(e){}
  }
}
function renderGraph(g){
  const host=document.getElementById('workflowGraph');
  if(!host)return;
  const by={};(g&&g.nodes||[]).forEach(n=>{by[n.id]=n});
  const edges=(g&&g.edges)||[];
  if(!g||!(g.nodes||[]).length){
    host.innerHTML='<p class="graph-empty">No workflow yet. <span class="mono">tickets plan</span> creates real --after edges.</p>';
    return;
  }
  const notice=edges.length?'':'<p class="graph-empty">No --after edges on the active board. <span class="mono">tickets plan</span> with deps, or <span class="mono">tickets dep T-002 --after T-001</span>.</p>';
  function row(n){
    const wait=(n.waiting||[]).length?'<span class="wait">waiting on '+esc(n.waiting.join(', '))+'</span>':((n.deps||[]).length?'<span class="mute">after '+esc(n.deps.join(', '))+'</span>':'');
    const own=n.owner?'<span class="mute">@'+esc(n.owner)+'</span>':'';
    const role=n.role?'<span class="mute">'+esc(n.role)+'</span>':'';
    return '<div class="g-row"><span class="mark">'+esc(n.mark||'')+'</span><span class="id">'+esc(n.id)+'</span><span class="g-title">'+esc(n.title)+'</span><span class="tag">'+esc(n.label||n.status||'')+'</span>'+role+own+wait+'</div>';
  }
  function walk(fr){
    const n=by[fr.id]||{id:fr.id,title:'',waiting:[],deps:[]};
    const kids=(fr.children||[]).map(walk).join('');
    const rpt=fr.repeat?'<span class="mute"> [shown above]</span>':'';
    return '<li class="g-node" data-id="'+esc(n.id)+'">'+row(n)+rpt+(kids?'<ul class="g-kids">'+kids+'</ul>':'')+'</li>';
  }
  host.innerHTML=notice+'<ul class="g-roots" id="graphTree">'+(g.forest||[]).map(walk).join('')+'</ul>';
}
document.querySelectorAll('[data-tab-btn]').forEach(b=>b.addEventListener('click',()=>setTab(b.dataset.tabBtn)));
document.querySelector('nav.tabs').addEventListener('keydown',e=>{
  if(e.key!=='ArrowLeft'&&e.key!=='ArrowRight')return;
  const tabs=[...document.querySelectorAll('[data-tab-btn]')];
  const at=tabs.indexOf(document.activeElement);
  if(at<0)return;
  e.preventDefault();
  const step=e.key==='ArrowRight'?1:-1;
  const next=tabs[(at+step+tabs.length)%tabs.length];
  setTab(next.dataset.tabBtn);next.focus();
});
try{const saved=localStorage.getItem('tickets-ui-tab');if(saved)setTab(saved)}catch(e){}
document.querySelectorAll('#workViews [data-work-view]').forEach(b=>b.addEventListener('click',()=>setWorkView(b.dataset.workView,true)));
try{const wv=localStorage.getItem('tickets-ui-work-view');if(wv)setWorkView(wv)}catch(e){}
(function applyWorkHash(){
  const h=(location.hash||'').replace('#','');
  if(h==='graph'||h==='columns'){setTab('board');setWorkView(h)}
})();
window.addEventListener('hashchange',()=>{
  const h=(location.hash||'').replace('#','');
  if(h==='graph'||h==='columns'){setTab('board');setWorkView(h)}
});
let AGENTS=[];
let THREAD_SEAT='';
try{THREAD_SEAT=localStorage.getItem('tickets-ui-seat')||''}catch(e){}
const BROADCAST=new Set(['','all','everyone']);
function isBoardBroadcast(m){
  const to=String(m.to||'').trim().toLowerCase();
  const mentions=(m.mentions||[]).map(h=>String(h).toLowerCase());
  if(BROADCAST.has(to)||mentions.some(h=>h==='everyone'||h==='all'))return true;
  return !to && mentions.length===0;
}
function involvesSeat(m,seat){
  const t=String(seat||'').trim().toLowerCase();
  if(!t)return false;
  const to=String(m.to||'').trim().toLowerCase();
  const frm=String(m.from||'').trim().toLowerCase();
  const mentions=(m.mentions||[]).map(h=>String(h).toLowerCase());
  if(to===t||mentions.indexOf(t)>=0)return true;
  return frm===t && !isBoardBroadcast(m);
}
function visibleMessages(msgs){
  return THREAD_SEAT?msgs.filter(m=>involvesSeat(m,THREAD_SEAT)):msgs.filter(isBoardBroadcast);
}
function openSeatChat(seat){
  THREAD_SEAT=seat||'';
  try{localStorage.setItem('tickets-ui-seat',THREAD_SEAT)}catch(e){}
  setTab('messages');
  loadAgentPickers();
  renderChatHead();
  const rail=document.getElementById('chatRail');
  if(rail)rail.querySelectorAll('button.thread').forEach(b=>b.classList.toggle('on',(b.dataset.seatChat||'')===THREAD_SEAT));
}
function renderChatHead(){
  const el=document.getElementById('chatHead');
  if(!el)return;
  if(THREAD_SEAT){
    el.innerHTML='<h2 class="seats-title">Seat · '+esc(THREAD_SEAT)+'</h2>'+
      '<p class="seats-lede">1:1 with this BYOA seat. <span class="mono">tickets msg --to '+esc(THREAD_SEAT)+'</span>.</p>';
  }else{
    el.innerHTML='<h2 class="seats-title">Board</h2>'+
      '<p class="seats-lede">Channel-wide. Directed seat mail lives on that seat’s thread.</p>';
  }
  const to=document.getElementById('cTo');
  if(to){to.disabled=!!THREAD_SEAT;if(THREAD_SEAT)to.value=THREAD_SEAT}
}
function renderChatRail(d){
  const rail=document.getElementById('chatRail');
  if(!rail)return;
  const threads=d.seat_threads||{board:{n:0,label:'Board'},seats:[]};
  const board=threads.board||{n:0,label:'Board'};
  const seats=threads.seats||[];
  const items=[{id:'',label:board.label||'Board',n:board.n||0}].concat(seats.map(s=>({id:s.name,label:s.name,n:s.n||0})));
  rail.innerHTML='<p class="seats-title">Threads</p>'+items.map(it=>{
    const on=((THREAD_SEAT||'')===(it.id||''))?' on':'';
    return '<button type="button" class="thread'+on+'" data-seat-chat="'+esc(it.id)+'" data-testid="thread-'+(it.id||'board')+'"><span>'+esc(it.label)+'</span><span class="n">'+esc(it.n)+'</span></button>';
  }).join('');
}
function ensureToOption(name){
  const to=document.getElementById('cTo');
  if(!to||!name)return;
  if(![...to.options].some(o=>o.value===name)){
    const o=document.createElement('option');o.value=name;o.textContent=name;to.appendChild(o);
  }
}
function loadAgentPickers(){
  const from=document.getElementById('cFrom'),to=document.getElementById('cTo');
  const savedFrom=localStorage.getItem('tickets-ui-from')||'';
  const prevFrom=from.value||savedFrom, prevTo=to.value;
  from.innerHTML='<option value="">(pick agent)</option>'+AGENTS.map(a=>'<option value="'+esc(a)+'">'+esc(a)+'</option>').join('');
  to.innerHTML='<option value="">everyone</option>'+AGENTS.map(a=>'<option value="'+esc(a)+'">'+esc(a)+'</option>').join('');
  if(AGENTS.includes(prevFrom))from.value=prevFrom;
  if(THREAD_SEAT){ensureToOption(THREAD_SEAT);to.value=THREAD_SEAT;to.disabled=true}
  else{to.disabled=false;if(AGENTS.includes(prevTo))to.value=prevTo}
}
document.addEventListener('click',e=>{
  const btn=e.target.closest('[data-seat-chat]');
  if(!btn||btn.closest('#composer'))return;
  e.preventDefault();
  openSeatChat(btn.dataset.seatChat||'');
  load();
});
function mentionQueryAt(text,caret){
  const upto=text.slice(0,caret);const m=upto.match(/(?:^|[^\w@])@([A-Za-z0-9_-]*)$/);
  return m?m[1]:null;
}
function renderMentionBar(){
  const ta=document.getElementById('cText'),bar=document.getElementById('mentionBar');
  const q=mentionQueryAt(ta.value,ta.selectionStart||0);
  if(q===null){bar.innerHTML='';return}
  const ql=q.toLowerCase();
  const matches=AGENTS.filter(a=>a.toLowerCase().startsWith(ql)).slice(0,8);
  bar.innerHTML=(matches.length?matches:AGENTS.slice(0,8)).map(a=>'<span class="mchip" data-agent="'+esc(a)+'">@'+esc(a)+'</span>').join('')
    || '<small class="mute">no known agents yet</small>';
}
function insertMention(agent){
  const ta=document.getElementById('cText');const caret=ta.selectionStart||0;
  const before=ta.value.slice(0,caret),after=ta.value.slice(caret);
  const m=before.match(/(?:^|[^\w@])@([A-Za-z0-9_-]*)$/);
  const start=m?caret-m[1].length:caret;
  ta.value=ta.value.slice(0,start)+agent+' '+after;
  const pos=start+agent.length+1;ta.focus();ta.setSelectionRange(pos,pos);
  renderMentionBar();
}
document.getElementById('mentionBar').addEventListener('click',e=>{
  const el=e.target.closest('.mchip');if(el)insertMention(el.dataset.agent);
});
document.getElementById('cText').addEventListener('input',renderMentionBar);
document.getElementById('cText').addEventListener('click',renderMentionBar);
document.getElementById('cText').addEventListener('keyup',e=>{if(e.key!=='Enter')renderMentionBar()});
document.getElementById('cSend').addEventListener('click',async()=>{
  const from=document.getElementById('cFrom').value.trim();
  const text=document.getElementById('cText').value.trim();
  const to=document.getElementById('cTo').value.trim();
  const re=document.getElementById('cRe').value.trim();
  const kind=document.getElementById('cKind').value.trim()||'message';
  const btn=document.getElementById('cSend'),msg=document.getElementById('composerMsg');
  if(!from){msg.className='bad';msg.textContent='pick who you are posting as';return}
  if(!text){msg.className='bad';msg.textContent='message is empty';return}
  localStorage.setItem('tickets-ui-from',from);
  btn.disabled=true;msg.className='';msg.textContent='posting…';
  try{
    const r=await fetch('/msg',{method:'POST',headers:{'Content-Type':'application/json'},
      body:JSON.stringify({from,text,to,re,kind})});
    const out=await r.json();
    if(out.ok){document.getElementById('cText').value='';document.getElementById('cRe').value='';
      document.getElementById('mentionBar').innerHTML='';msg.className='ok';msg.textContent='posted';
      if(to)openSeatChat(to);load()}
    else{msg.className='bad';msg.textContent='error: '+(out.error||r.status)}
  }catch(e){msg.className='bad';msg.textContent='error: '+e}
  finally{btn.disabled=false}
});
function tickClock(){document.getElementById('clock').textContent=new Date().toLocaleTimeString()}
let snapshotFails=0;
let _loadCtl=null;
function unreachableNextStep(msg,cmd){
  return{kind:'unreachable',label:'Board unavailable',message:msg,cmd:cmd||''};
}
async function load(manual){
  if(_loadCtl){if(!manual)return;_loadCtl.abort()}
  const refreshBtn=document.getElementById('refreshBtn');
  if(manual&&refreshBtn){refreshBtn.disabled=true;refreshBtn.classList.add('spin')}
  if(firstLoad)document.body.classList.add('loading');
  if(snapshotFails)setConn('reconnecting');
  const ctl=new AbortController();
  _loadCtl=ctl;
  let d;
  try{
    const r=await fetch('/board.json?'+Date.now(),{signal:ctl.signal});
    if(!r.ok)throw new Error('HTTP '+r.status);
    d=await r.json();
  }catch(e){
    if(e&&e.name==='AbortError')return;
    snapshotFails++;
    setConn('offline');
    renderNextStep(unreachableNextStep(
      snapshotFails>2?'Cannot reach the board server — is `tickets ui` still running? ('+e+')'
        :'Board unreachable — retrying… ('+e+')',
      'tickets ui'));
    document.body.classList.remove('loading');
    if(refreshBtn){refreshBtn.disabled=false;refreshBtn.classList.remove('spin')}
    if(_loadCtl===ctl)_loadCtl=null;
    return;
  }
  if(_loadCtl===ctl)_loadCtl=null;
  if(d.error){
    snapshotFails++;
    renderNextStep(d.next_step||unreachableNextStep(
      'Board snapshot failed — '+d.error+(snapshotFails>2?' (still failing; check TICKETS_DIR and board files)':''),
      'tickets ui --json'));
    d.counts=d.counts||{total:0,done:0};
  }else snapshotFails=0;
  setConn('live',d.generated);
  firstLoad=false;
  document.body.classList.remove('loading');
  if(refreshBtn){refreshBtn.disabled=false;refreshBtn.classList.remove('spin')}
  document.getElementById('title').textContent='Atman';
  const boardName=document.getElementById('boardName');
  if(boardName){
    const proj=String(d.project||'').trim();
    const show=proj&&proj.toLowerCase()!=='atman';
    boardName.textContent=show?('workspace · '+proj):'';
    boardName.hidden=!show;
  }
  const counts=d.counts||{total:0,done:0};
  document.getElementById('chips').innerHTML=
    '<span class="chip master"><b>master</b> '+esc(d.master||'nobody')+'</span>'+
    '<span class="chip cos"><b>CoS</b> '+esc(d.cos||'—')+'</span>'+
    '<span class="chip"><b>'+esc(counts.done)+'</b>/'+esc(counts.total)+' done</span>';
  const s=d.sprint;
  const sprintEl=document.getElementById('sprint');
  sprintEl.hidden=!s;
  sprintEl.innerHTML=s
    ?('<div class="row"><span>'+esc(s.id)+(s.goal?' · '+esc(s.goal):'')+'</span><span>'+s.done+'/'+s.total+'</span></div><div class="bar"><i style="width:'+(100*s.done/Math.max(1,s.total))+'%"></i></div>')
    :'';
  const crit=(d.health||[]).filter(x=>x.sev==='CRIT').length;
  const warn=(d.health||[]).filter(x=>x.sev==='WARN').length;
  const pulse=document.getElementById('pulse');
  pulse.className='health'+(crit?' bad':warn?' warn':'');
  pulse.innerHTML='<i></i><span>'+(crit?'Critical · '+crit:warn?'Warning · '+warn:'No alerts')+'</span>';
  tickClock();
  const goals=d.goals||'(no MASTER.md yet -- tickets master init)';
  document.getElementById('goals').textContent=goals;
  document.getElementById('missionOne').textContent=missionLine(goals);
  const blocked=(d.open||[]).filter(t=>t.status==='BLOCKED'||(t.waiting||[]).length);
  const ready=(d.open||[]).filter(t=>t.status!=='BLOCKED'&&!(t.waiting||[]).length);
  fillCol('blocked',blocked,blocked.map(t=>card(t)).join(''));
  fillCol('ready',ready,ready.map(t=>card(t)).join(''));
  fillCol('flight',d.in_flight||[],(d.in_flight||[]).map(t=>card(t)).join(''));
  fillCol('review',d.review||[],(d.review||[]).map(t=>card(t,t.commit?'<div class="mono mute">'+esc(t.commit)+(t.pr?' · PR '+esc(t.pr):'')+'</div>':'')).join(''));
  renderGraph(d.graph);
  renderEmptyBoard(d);
  renderAttention(d.attention);
  renderEpics(d.epics);
  if(!d.error)renderNextStep(d.next_step);
  renderOnboarding(d.onboarding);
  renderPromise(d.promise);
  renderObjective(d.objective);
  renderTurns(d.turns);
  renderUsage(d.usage);
  renderSeats(d);
  AGENTS=(d.agents||[]).map(a=>a.name).filter(Boolean).sort();loadAgentPickers();
  renderChatRail(d);renderChatHead();
  const utilBy={};(d.util||[]).forEach(u=>{utilBy[u.agent]=u});
  document.getElementById('agents').innerHTML=(d.agents||[]).map(a=>{
    const u=utilBy[a.name]||{};
    const st=a.state==='DOWN'?'bad':a.state==='busy'?'ok':'mute';
    const auth=a.auth==='login_required'?'<span class="tag limit" title="'+esc(a.auth_detail||'')+'">Login required · '+esc(a.auth_login_cmd||'agent login')+'</span>':'';
    const expired=a.auth==='expired'?'<span class="tag limit" title="'+esc(a.auth_detail||'')+'">Expired</span>':'';
    const network=a.auth==='network'?'<span class="tag limit" title="'+esc(a.auth_detail||'')+'">Network</span>':'';
    const unavail=a.auth==='unavailable'?'<span class="tag limit" title="'+esc(a.auth_detail||'')+'">Unavailable</span>':'';
    const unsup=a.auth==='unsupported'?'<span class="tag mute" title="'+esc(a.auth_detail||'')+'">Unsupported</span>':'';
    const pausedAuth=a.auth_paused?'<span class="tag pending" title="paused-auth">paused-auth</span>':'';
    const quota=a.auth==='quota'?'<span class="tag limit" title="'+esc(a.auth_detail||'')+'">Usage quota</span>':'';
    const lim=a.limit?'<span class="tag limit" title="'+esc(a.limit_until||'usage limit')+'">limited</span>':'';
    const wake=a.adapter_state==='conflict'?'<span class="tag limit" title="'+esc(a.adapter_reason||'')+'">adapter conflict</span>':(a.adapter_state==='failed'?'<span class="tag limit" title="'+esc(a.adapter_reason||'')+'">dispatch failed</span>':(a.adapter_state==='retrying'?'<span class="tag pending" title="'+esc(a.adapter_reason||'')+'">retrying</span>':(a.adapter_state==='running'||a.adapter_state==='claimed'||a.adapter_state==='recovery-required'?'<span class="tag pending" title="'+esc(a.adapter_reason||'')+'">'+esc(a.adapter_state)+'</span>':(a.wake_pending?'<span class="tag pending" title="'+esc(a.adapter_reason||'')+'">'+(a.adapter_online?'wake queued':'queued · offline')+'</span>':''))));
    const seen=a.seen_h!=null?'<span class="mute"> · seen '+h(a.seen_h)+'</span>':'';
    const life='<span class="tag" title="lifecycle is separate from wake_mode">'+esc(a.lifecycle||'ephemeral')+'</span>';
    const onlineDot=(a.reachable!==false && a.adapter_online)?' ●':'';
    return '<article class="agent"><div class="head">'+who(a.name)+'<span class="st '+st+'">'+esc(a.state)+onlineDot+'</span>'+life+auth+expired+network+unavail+unsup+pausedAuth+quota+lim+wake+'</div>'+
      '<div class="mute mono">'+esc(a.agent_id||a.name)+' · '+esc(a.adapter_provider||a.harness||'—')+' · '+esc(a.adapter_mode||'supervised')+' · '+esc(a.adapter_delivery||'offline')+' · '+esc(a.wake_mode||'task-only')+' · usage '+esc(a.adapter_usage||'unmeasured')+(a.ticket?' · '+esc(a.ticket):'')+seen+'</div>'+
      '<div class="bar"><i style="width:'+Math.round(u.util_pct||0)+'%"></i></div>'+
      '<div class="stats"><div><b>'+esc(a.done)+'</b><span class="stat-lbl" title="Tickets this agent finished in the last 24 hours — not lifetime done">Done (24h)</span></div>'+
      '<div><b>'+Math.round(u.util_pct||0)+'%</b><span class="stat-lbl" title="Share of the last 24 hours this agent was actively working a ticket">Utilization</span></div>'+
      '<div><b>'+esc((a.roles&&a.roles.length)?a.roles.join('/'):'any')+'</b><span class="stat-lbl" title="Roles this agent registered — determines which tickets they can claim">Lane</span></div></div>'+
      '<button type="button" class="intervene" data-seat-chat="'+esc(a.name)+'">Msg</button></article>';
  }).join('')||emptyCraft('Team','<b>No agents checked in.</b> Plug a harness so coverage is by work, not a vacant roster.','Utilization and Done (24h) stay — until a seat heartbeats.',['tickets join &lt;name&gt; --roles … --harness','tickets quickstart --agent &lt;you&gt;']);
  const thread=visibleMessages(d.messages||[]).slice().reverse();
  const msgEmpty=THREAD_SEAT
    ?emptyCraft('Intervene','<b>No messages with this seat yet.</b> Msg opens the thread.','Delivery receipts only — never implied progress on tickets.','tickets msg --to '+esc(THREAD_SEAT))
    :emptyCraft('Intervene','<b>No messages yet.</b> Post when a seat should see something.','Receipts show delivery — never implied progress on tickets.','tickets msg "text" [--to agent] [--re T-001]');
  document.getElementById('msgs').innerHTML=thread.map(m=>'<div class="m"><div class="hd">'+who(m.from)+(m.to?' → '+who(m.to):'')+(m.re?' <span class="tag">'+esc(m.re)+'</span>':'')+deliveryTags(m)+'<span class="mute">'+esc(fmtWhen(m.at))+'</span></div>'+mentionText(m.text)+'</div>').join('')
    ||msgEmpty;
}
function renderOnboarding(ob){
  // Labels/cmds aligned with T-322 quickstart + README (opus-console/t322-quickstart).
  const steps=[
    ['initialized','Board ready','tickets quickstart --agent <you>'],
    ['first_ticket','Work on the board','tickets quickstart'],
    ['first_agent','You registered','tickets quickstart --agent <you>'],
    ['first_review','First review submitted','tickets review <id> --notes "..."'],
    ['first_merge','First merge','tickets merge'],
    ['second_harness','Second harness','tickets join <name> --harness …'],
    ['objective_set','Objective set','tickets objective "..."']
  ];
  const done=steps.filter(s=>ob&&ob[s[0]]).length;
  document.getElementById('obProgress').textContent=done+'/'+steps.length;
  document.getElementById('obSteps').innerHTML=steps.map(s=>{
    const ok=ob&&ob[s[0]];
    return '<span class="ob-step'+(ok?' done':'')+'"><i>'+(ok?'✓':'')+'</i><span>'+esc(s[1])+'</span><span class="mute mono">'+esc(s[2])+'</span></span>';
  }).join('');
}
function renderNextStep(ns){
  const el=document.getElementById('nextStep');
  if(!ns||!ns.message){el.hidden=true;el.className='next-step';return}
  el.hidden=false;
  el.className='next-step'+(ns.kind==='unreachable'?' unreachable':'');
  el.innerHTML='<span class="lbl">'+esc(ns.label||'Next')+'</span><span class="msg">'+esc(ns.message)+'</span>'+
    (ns.cmd?'<span class="cmd">'+esc(ns.cmd)+'</span>':'');
}
function renderEmptyBoard(d){
  const el=document.getElementById('emptyBoard');
  const empty=!!(d.empty_board||!(d.counts&&d.counts.total));
  if(empty)document.body.setAttribute('data-empty-board','');
  else document.body.removeAttribute('data-empty-board');
  el.hidden=!empty;
  if(!empty)return;
  el.innerHTML='<p class="empty-kicker">Day one</p>'+
    '<p><b>Three steps.</b> Work · Team · Objective — then intervene when a seat needs you.</p>'+
    '<ol class="empty-steps" id="emptySteps">'+
      '<li><span class="n">1</span><div><b>Work</b> — seed the board and claim a first ticket.<div class="cta">tickets quickstart --agent &lt;you&gt;</div></div></li>'+
      '<li><span class="n">2</span><div><b>Team</b> — plug a second harness. Coverage by work, not a fixed role.<div class="cta">tickets join … --harness</div></div></li>'+
      '<li><span class="n">3</span><div><b>Objective</b> — name what the team finishes.<div class="cta">tickets objective "…"</div></div></li>'+
    '</ol>'+
    '<p class="empty-honesty">Median turns and yield@cost stay — until a done ticket reports.</p>'+
    '<p class="empty-intervene">Intervene is always available — <b>Msg</b> a seat, route, or unblock.</p>';
}
function renderAttention(items){
  const box=document.getElementById('attentionBox'),list=document.getElementById('attnList'),cnt=document.getElementById('attnCount');
  const rows=items||[];
  if(!box)return;
  box.hidden=!rows.length;
  if(cnt)cnt.textContent=String(rows.length);
  if(list)list.innerHTML=rows.map(a=>'<div class="attn-item '+esc(a.sev||'')+'">'+esc(a.msg||'')+'</div>').join('');
}
function renderEpics(epics){
  const el=document.getElementById('epicsPanel');
  if(!el)return;
  const rows=epics||[];
  el.hidden=!rows.length;
  el.innerHTML=rows.map(e=>{
    const pct=Math.round(100*(e.done||0)/Math.max(1,e.total||1));
    return '<article class="epic"><div class="row"><span><span class="id">'+esc(e.id)+'</span> '+esc(e.title||'')+'</span><span>'+e.done+'/'+e.total+'</span></div>'+
      '<div class="bar"><i style="width:'+pct+'%"></i></div>'+
      '<div class="row"><span class="mute">flight '+esc(e.in_flight||0)+' · review '+esc(e.review||0)+' · blocked '+esc(e.blocked||0)+'</span></div></article>';
  }).join('');
}
function deliveryTags(m){
  const tags=[];
  if((m.kind||'message')==='task')tags.push('<span class="tag task">task</span>');
  const d=m.delivery||{};
  if(d.status==='broadcast')tags.push('<span class="tag">broadcast</span>');
  else if((d.acks||[]).length){
    const all=d.acks.every(a=>a.acked);
    const any=d.acks.some(a=>a.acked);
    if(all)tags.push('<span class="tag ack">acked</span>');
    else if(any)tags.push('<span class="tag pending">partial ack</span>');
    else tags.push('<span class="tag pending">pending</span>');
  }
  return tags.join(' ');
}
function setConn(phase,updated){
  const st=document.getElementById('connStatus'),lu=document.getElementById('lastUpdated');
  if(st){st.className='conn-status '+phase;st.textContent=phase==='live'?'connected':phase}
  if(lu)lu.textContent=updated?('updated '+fmtRel(updated)+' · '+fmtLocal(updated)):'—';
}
let firstLoad=true;
function updateThemeControl(){
  const light=document.body.dataset.theme==='light';
  const btn=document.getElementById('themeBtn'),label=document.getElementById('themeLabel');
  const action=light?'Dark mode':'Light mode';
  if(label)label.textContent=action;
  if(btn){btn.setAttribute('aria-label','Switch to '+action.toLowerCase());btn.title='Switch to '+action.toLowerCase();btn.setAttribute('aria-pressed',light?'true':'false')}
}
try{
  const savedTheme=localStorage.getItem('tickets-ui-theme');
  if(savedTheme)document.body.dataset.theme=savedTheme;
  else if(window.matchMedia&&window.matchMedia('(prefers-color-scheme: light)').matches)document.body.dataset.theme='light';
}catch(e){}
updateThemeControl();
document.getElementById('themeBtn').addEventListener('click',()=>{
  const next=document.body.dataset.theme==='light'?'':'light';
  document.body.dataset.theme=next;
  try{localStorage.setItem('tickets-ui-theme',next)}catch(e){}
  updateThemeControl();
});
document.getElementById('refreshBtn').addEventListener('click',()=>load(true));
load();setInterval(load,5000);setInterval(tickClock,1000);
</script></body></html>"""


def _onboarding_checklist(board, tickets):
    """First-run checklist ticks for the UI.

    Display labels/cmds in UI_HTML match T-322 quickstart + README
    (opus-console/t322-quickstart); keys here are the board-state probes.
    """
    initialized = os.path.isfile(master_path(board)) or os.path.isfile(os.path.join(board, "roles.json"))
    agents = load_agents(board) if os.path.isdir(agents_dir(board)) else []
    workforce = load_workforce(board)
    names = set()
    for rec in agents:
        if rec.get("name"):
            names.add(rec["name"])
    names.update(workforce)
    return {
        "initialized": initialized,
        "first_ticket": any(t.get("claimed_at") for t in tickets),
        "first_agent": bool(names),
        "second_harness": len(names) >= 2,
        "first_review": any(t.get("status") in ("review", "done") for t in tickets),
        "first_merge": any(t.get("status") == "done" for t in tickets),
        "objective_set": bool((load_objective(board) or {}).get("text")),
    }


def _next_step_hint(board, tickets, done_ids):
    """One-line guidance for the persistent next-step strip."""
    if not tickets:
        return {"kind": "start", "label": "Get started",
                "message": "Board is empty — run quickstart to seed your first tickets.",
                "cmd": "tickets quickstart --agent <you>"}
    blocked = [t for t in tickets if t.get("status") == "blocked"]
    if blocked:
        t = blocked[0]
        return {"kind": "unblock", "label": "Unblock",
                "message": "%s is blocked — read why and clear the blocker." % t["id"],
                "cmd": "tickets show %s" % t["id"]}
    review = [t for t in tickets if t.get("status") == "review"]
    if review:
        return {"kind": "merge", "label": "Review queue",
                "message": "%d ticket(s) waiting for master to merge." % len(review),
                "cmd": "tickets done %s --notes \"...\"" % review[0]["id"]}
    agents = load_agents(board) if os.path.isdir(agents_dir(board)) else []
    workforce = load_workforce(board)
    if not (agents or workforce):
        return {"kind": "spawn", "label": "Spawn an agent",
                "message": "No agents on the board yet — register one to claim work.",
                "cmd": "tickets join <name> --roles backend"}
    ready = [t for t in tickets if t.get("status") == "open"
             and all(d in done_ids for d in t.get("deps", []))]
    unowned = [t for t in ready if not t.get("owner")]
    if unowned:
        return {"kind": "route", "label": "Route work",
                "message": "Ready tickets are unowned — claim or assign them.",
                "cmd": "tickets next"}
    return {"kind": "ok", "label": "On track",
            "message": "Workers are moving — post updates every 45 minutes.",
            "cmd": "tickets update <id> \"...\""}


def _turns_mod():
    try:
        from ticket_board.turns import build_turns_report, load_trajectory_events
    except ImportError:
        src = os.path.join(os.path.dirname(os.path.realpath(__file__)), "src")
        if src not in sys.path:
            sys.path.insert(0, src)
        from ticket_board.turns import build_turns_report, load_trajectory_events
    return build_turns_report, load_trajectory_events


def _empty_turns_snapshot():
    return {
        "v": 1,
        "tickets": [],
        "aggregates": {
            "mean": None, "median": None, "n": 0, "n_unmeasured": 0,
            "by_agent": [], "by_model": [], "by_role": [], "by_priority": [],
        },
    }


def _turns_snapshot(board, tickets):
    """Frozen `tickets turns --json` for the console (T-372 / T-344)."""
    build_turns_report, load_trajectory_events = _turns_mod()
    return build_turns_report(
        load_trajectory_events(board),
        tickets=tickets,
        workforce=load_workforce(board),
        messages=load_messages(board, include_archives=True),
    )


def _empty_usage_snapshot():
    return {
        "cost_usd": None, "tokens_in": None, "tokens_out": None,
        "n_runs_with_cost": 0, "n_runs_unmeasured": 0, "by_agent": [],
    }


def _usage_snapshot(events):
    """Harness-reported usage only. Unknown is omitted, never defaulted to 0."""
    by = {}
    n_cost = n_uncost = 0
    cost_sum = 0.0
    tin = tout = 0
    have_cost = have_tin = have_tout = False
    for e in events or []:
        if e.get("kind") != "run_end":
            continue
        agent = e.get("agent") or ""
        rec = by.setdefault(agent, {
            "agent": agent, "cost_usd": None, "tokens_in": None, "tokens_out": None,
            "n_runs_with_cost": 0, "n_runs_unmeasured": 0,
        })
        c = e.get("cost_usd")
        if isinstance(c, (int, float)):
            have_cost = True
            cost_sum += float(c)
            n_cost += 1
            rec["n_runs_with_cost"] += 1
            rec["cost_usd"] = round((rec["cost_usd"] or 0.0) + float(c), 6)
        else:
            n_uncost += 1
            rec["n_runs_unmeasured"] += 1
        if isinstance(e.get("tokens_in"), int):
            have_tin = True
            tin += e["tokens_in"]
            rec["tokens_in"] = (rec["tokens_in"] or 0) + e["tokens_in"]
        if isinstance(e.get("tokens_out"), int):
            have_tout = True
            tout += e["tokens_out"]
            rec["tokens_out"] = (rec["tokens_out"] or 0) + e["tokens_out"]
    return {
        "cost_usd": round(cost_sum, 6) if have_cost else None,
        "tokens_in": tin if have_tin else None,
        "tokens_out": tout if have_tout else None,
        "n_runs_with_cost": n_cost,
        "n_runs_unmeasured": n_uncost,
        "by_agent": [by[k] for k in sorted(by) if k],
    }


def _ticket_costs(events):
    out = {}
    for e in events or []:
        if e.get("kind") != "run_end":
            continue
        tid = e.get("ticket")
        c = e.get("cost_usd")
        if not tid or not isinstance(c, (int, float)):
            continue
        out[tid] = out.get(tid, 0.0) + float(c)
    return out


def _promise_hero(turns, tickets, events):
    """CEO home hero: median turns + yield@cost. Unknown is never 0."""
    agg = (turns or {}).get("aggregates") or {}
    costs = _ticket_costs(events)
    idx = {t.get("id"): t for t in (tickets or []) if t.get("id")}
    done_with_cost = 0
    done_no_cost = 0
    cost_for_done = 0.0
    done_turns = []
    for row in (turns or {}).get("tickets") or []:
        tid = row.get("ticket")
        outcome = row.get("outcome")
        st = (idx.get(tid) or {}).get("status")
        if outcome not in ("done", "merge") and st != "done":
            continue
        if row.get("turns") is not None:
            done_turns.append(row["turns"])
        if tid in costs:
            done_with_cost += 1
            cost_for_done += costs[tid]
        else:
            done_no_cost += 1
    yield_per_usd = None
    if done_with_cost and cost_for_done > 0:
        yield_per_usd = round(done_with_cost / cost_for_done, 4)
    return {
        "median_turns": agg.get("median") if done_turns else None,
        "n_turns": agg.get("n") or 0,
        "n_unmeasured_turns": agg.get("n_unmeasured") or 0,
        "yield_per_usd": yield_per_usd,
        "done_with_cost": done_with_cost,
        "cost_usd": round(cost_for_done, 6) if done_with_cost else None,
        "n_unmeasured_cost": done_no_cost,
    }


def _coverage_snapshot(master, cos, open_rows, in_flight, review, agents):
    """Team coverage: empty seats = uncovered ready work."""
    uncovered = [{"id": t["id"], "title": t.get("title", "")}
                 for t in (open_rows or [])
                 if t.get("status") != "BLOCKED" and not t.get("waiting") and not t.get("owner")]
    return {
        "uncovered_ready": uncovered,
        "in_flight": [{"id": t["id"], "owner": t.get("owner", "")} for t in (in_flight or [])],
        "review": [{"id": t["id"], "owner": t.get("owner", "")} for t in (review or [])],
        "operator": ([{"name": master, "role": "master"}] if master else []) + (
            [{"name": cos, "role": "cos"}] if cos else []),
        "idle": [{"name": a["name"], "state": a.get("state", "")}
                 for a in (agents or []) if a.get("name")],
    }


def _empty_promise_hero():
    return {
        "median_turns": None, "n_turns": 0, "n_unmeasured_turns": 0,
        "yield_per_usd": None, "done_with_cost": 0, "cost_usd": None,
        "n_unmeasured_cost": 0,
    }


def _epics_snapshot(board, tickets):
    """Per-epic progress for the live dashboard."""
    out = []
    for e in load_epics(board):
        mine = [t for t in tickets if t.get("epic") == e["id"]]
        if not mine:
            continue
        d, n, c, b = progress(mine)
        out.append({
            "id": e["id"], "title": e.get("title", ""),
            "done": d, "total": n, "in_flight": c, "blocked": b,
            "review": sum(1 for t in mine if t["status"] == "review"),
        })
    return out


def _message_recipients(msg):
    tokens = _split_to_tokens(msg.get("to") or "")
    mentions = msg.get("mentions") or []
    if tokens:
        return tokens
    if mentions:
        return list(mentions)
    return []


def _agent_acked_message(board, agent, msg, rec=None):
    rec = rec if rec is not None else _agent_rec(board, agent)
    if not rec:
        return False
    since = _seen_since(rec)
    remaining = _seen_counts(rec.get("inbox_seen_ids") or [])
    return not _is_unread(msg, since, remaining)


def _message_delivery(board, msg, agents_by=None):
    """Delivery/ack status for the UI composer thread."""
    recipients = _message_recipients(msg)
    if not recipients:
        return {"status": "broadcast"}
    acks = []
    for r in recipients:
        rec = (agents_by or {}).get(r)
        acks.append({"agent": r, "acked": _agent_acked_message(board, r, msg, rec=rec)})
    return {"status": "direct", "acks": acks}


def _attention_snapshot(health_items, coverage):
    """Blockers and items requiring operator attention."""
    out = [{"sev": h["sev"], "msg": h["msg"]} for h in (health_items or [])]
    for u in (coverage or {}).get("uncovered_ready", []):
        title = (u.get("title") or "")[:72]
        out.append({"sev": "WARN", "msg": "%s ready and unowned%s" % (
            u.get("id", "?"), (" — " + title) if title else "")})
    return out[:16]


_ANALYTICS_TTL_S = 3.0
_ANALYTICS_LOCK = threading.Lock()
_ANALYTICS_CACHE = {}
_SNAP_GATE = threading.Lock()
_SNAP_SLOTS = {}


def _file_mtime(path):
    try:
        return os.path.getmtime(path)
    except OSError:
        return 0.0


def _analytics_stamp(board):
    return (_file_mtime(os.path.join(board, "trajectories.jsonl")),
            _file_mtime(messages_path(board)))


def _cached_turns_usage_promise(board, tickets):
    """Reuse turns/usage/promise across a few warm UI refreshes.

    Tickets, messages, and master stay uncached so leadership and mail stay live.
    Trajectory analytics are bounded: recompute when the log mtime changes or
    the short TTL expires.
    """
    import time as _time

    stamp = _analytics_stamp(board)
    key = os.path.realpath(board)
    now_m = _time.monotonic()
    with _ANALYTICS_LOCK:
        hit = _ANALYTICS_CACHE.get(key)
        if hit and hit["stamp"] == stamp and now_m - hit["at"] < _ANALYTICS_TTL_S:
            return hit["turns"], hit["usage"], hit["promise"]
    turns = _safe(lambda: _turns_snapshot(board, tickets), _empty_turns_snapshot())
    events = _safe(lambda: _turns_mod()[1](board), [])
    usage = _safe(lambda: _usage_snapshot(events), _empty_usage_snapshot())
    promise = _safe(lambda: _promise_hero(turns, tickets, events), _empty_promise_hero())
    with _ANALYTICS_LOCK:
        _ANALYTICS_CACHE[key] = {
            "stamp": stamp, "at": now_m, "turns": turns, "usage": usage, "promise": promise,
        }
    return turns, usage, promise


def _snapshot_single_flight(board, messages=40):
    """One in-flight board_snapshot per board so overlapping HTTP refreshes share work."""
    key = (os.path.realpath(board), int(messages))
    with _SNAP_GATE:
        slot = _SNAP_SLOTS.get(key)
        if slot is None:
            slot = {"event": threading.Event(), "snap": None, "exc": None}
            _SNAP_SLOTS[key] = slot
            owner = True
        else:
            owner = False
    if owner:
        try:
            slot["snap"] = board_snapshot(board, messages=messages)
        except Exception as exc:
            slot["exc"] = exc
            raise
        finally:
            slot["event"].set()
            with _SNAP_GATE:
                if _SNAP_SLOTS.get(key) is slot:
                    _SNAP_SLOTS.pop(key, None)
        return slot["snap"]
    slot["event"].wait(timeout=60)
    if slot["exc"] is not None:
        raise slot["exc"]
    if slot["snap"] is None:
        return board_snapshot(board, messages=messages)
    return slot["snap"]


def board_snapshot(board, messages=40):
    """Everything the UI shows, as plain data. Read-only."""
    with _shared_watch_table():
        return _board_snapshot_body(board, messages)


def _board_snapshot_body(board, messages=40):
    tickets = load_all(board)
    cur = active_sprint(board)
    m = current_master(board) or {}
    done = set(t["id"] for t in tickets if t["status"] == "done")
    counts = {"total": len(tickets), "done": len(done)}
    sprint = None
    if cur:
        mine = [t for t in tickets if t.get("sprint") == cur["id"]]
        d, n, c, b = progress(mine)
        sprint = {"id": cur["id"], "goal": cur.get("goal", ""), "done": d, "total": n, "in_flight": c, "blocked": b,
                  "review": len([t for t in mine if t["status"] == "review"])}
    agent_list = load_agents(board)
    agents = {r["owner"]: r for r in agent_list}
    live = {}
    for rec in agent_list:
        n = rec.get("owner") or ""
        if not n or n.startswith("agent-"):
            continue
        live[n] = _safe(lambda rec=rec: agent_liveness(board, rec, agent_list), {}) or {}
    rows, burn = utilization(board, tickets, hours=24, live=live)
    wf = load_workforce(board)
    roles = load_roles(board)
    out_agents = []
    for r in rows:
        rec = agents.get(r["agent"], {})
        agent_wf = wf.get(r["agent"], {}) or {}
        harness_name = agent_wf.get("harness") or agent_wf.get("tool") or "claude"
        lim = rec.get("limit")
        wc = _watcher_count(r["agent"], board)
        wake = pending_view(_safe(lambda name=r["agent"]: pending_work(board, name), {}))
        wake_pending = actionable(wake)
        remote = (_remote_public_state(load_remote_state(board, r["agent"]))
                  if harness_name == "remote" else {})
        sa = _session_adapters()
        local_failure = _local_adapter_failure(rec, harness_name)
        failure_state = remote.get("failure_state", "") or local_failure.get("state", "")
        failure_reason = remote.get("failure_reason", "") or local_failure.get("reason", "")
        adapter_extra = sa.public_adapter_state(
            board, r["agent"], harness_name, False, wake_pending)
        native_online = bool(adapter_extra.get("adapter_native_online"))
        remote_online = bool(remote.get("online")) if harness_name == "remote" else False
        watcher_online = wc == 1
        if wc > 1 or (harness_name == "remote" and wc > 0) or (native_online and wc > 0):
            adapter_state = "conflict"
            adapter_reason = "Multiple adapters detected; stop extras so one identity-pinned lease remains."
            adapter_online = False
        else:
            adapter_online = sa.is_reachable(native_online=native_online,
                                            watcher_online=watcher_online,
                                            remote_online=remote_online)
            adapter_extra = sa.public_adapter_state(
                board, r["agent"], harness_name, adapter_online, wake_pending)
            if failure_state == "failed":
                adapter_state = "failed"
                adapter_reason = failure_reason or "Dispatch exhausted its bounded retries; work remains queued."
            elif failure_state == "retrying":
                adapter_state = "retrying"
                adapter_reason = failure_reason or "Dispatch failed and is waiting for its bounded retry."
            elif remote.get("run_state") and adapter_online:
                adapter_state = remote["run_state"]
                adapter_reason = "Remote run is active under fence %s." % remote.get("fence")
            elif remote.get("run_state"):
                adapter_state = ("recovery-required" if remote["run_state"] == "recovery-required"
                                 else "queued-offline")
                adapter_reason = "Remote run lost its adapter lease; reconnect to recover it."
            elif wake_pending and not adapter_online:
                adapter_state = "queued-offline"
                adapter_reason = "Wake queued — adapter offline; delivery resumes when it reconnects."
            elif wake_pending:
                adapter_state = "queued"
                adapter_reason = "Wake queued for the connected adapter."
            elif adapter_online:
                adapter_state = "online"
                adapter_reason = "Adapter connected; no wake pending."
            else:
                adapter_state = "offline"
                adapter_reason = "Adapter offline; directed work will remain queued."
        watcher_count = wc + (1 if remote.get("online") else 0) + (1 if native_online else 0)
        life = lifecycle_of(board, r["agent"], master_state=m, workforce=wf)
        reachable = adapter_online
        if life == "ephemeral" and not reachable and adapter_state == "offline":
            adapter_extra["adapter_delivery"] = "exited"
        out_agents.append({"name": r["agent"], "state": r["state"], "model": agent_wf.get("model", ""),
                           "harness": harness_name,
                           "agent_id": agent_wf.get("agent_id") or r["agent"],
                           "lifecycle": life,
                           "reachable": reachable,
                           **adapter_extra,
                           "wake_mode": wake_mode_of(board, r["agent"], master_state=m, workforce=wf),
                           "done": r["done"], "seen_h": r["seen_h"], "ticket": rec.get("ticket", ""),
                           "watcher": adapter_online,
                           "watcher_count": watcher_count,
                           "adapter_online": adapter_online,
                           "adapter_state": adapter_state,
                           "adapter_reason": adapter_reason,
                           "adapter_bridge_id": remote.get("bridge_id", ""),
                           "adapter_fence": remote.get("fence", 0),
                           "adapter_heartbeat_at": remote.get("heartbeat_at", ""),
                           "adapter_run_id": remote.get("run_id", ""),
                           "adapter_attempts": remote.get("attempt", 0) or local_failure.get("attempts", 0),
                           "adapter_retry_at": remote.get("retry_at", "") or local_failure.get("retry_at", ""),
                           "wake_pending": wake_pending,
                           "wake_reason": wake_reason_of(wake),
                           "wake_delivery": rec.get("wake_delivery") or {},
                           "roles": roles.get(r["agent"]) or [],
                           "auth": (rec.get("auth_check") or {}).get("state", ""),
                           "auth_detail": (rec.get("auth_check") or {}).get("detail", ""),
                           "auth_login_cmd": (rec.get("auth_check") or {}).get("login_cmd", ""),
                           "auth_profile_ref": (rec.get("auth_check") or {}).get("credential_profile_ref", ""),
                           "auth_profile_kind": (rec.get("auth_check") or {}).get("profile_kind", ""),
                           "auth_authoritative": (rec.get("auth_check") or {}).get("authoritative"),
                           "auth_paused": ((rec.get("auth_check") or {}).get("pause") or {}).get("paused"),
                           "limit": lim,
                           "limit_until": (lim or {}).get("until", "") if lim else ""})
    out_agents.sort(key=lambda a: (a["state"] == "DOWN", a["state"] != "busy", a["name"]))
    goals = ""
    try:
        with open(master_path(board)) as f:
            md = f.read()
        import re as _re
        picked = []
        for head in ("CEO memo", "Mission", "Sprint plan"):
            mm = _re.search(r"^## [^\n]*%s[^\n]*\n(.*?)(?=^## |\Z)" % _re.escape(head), md, _re.S | _re.M)
            if mm:
                picked.append("%s\n%s" % (head.upper(), mm.group(1).strip()[:1800]))
        goals = "\n\n".join(picked)
    except OSError:
        pass
    obj = _safe(lambda: load_objective(board), {})
    if obj:
        st = objective_state(obj)
        flag = " FLAG:no-exit" if objective_exit_missing(obj) else ""
        goals = "OBJECTIVE (%s%s)\n%s\nexit: %s\n\n%s" % (
            st, flag, obj.get("text", ""), obj.get("exit_criterion") or "(none)", goals)
    util_rows = [r for r in rows if r["state"] != "DOWN"]
    in_flight = [{"id": t["id"], "owner": t.get("owner", ""), "title": t["title"],
                  "priority": t.get("priority", 2), "since_update": timing(t)["since_update"],
                  "waiting": [d for d in t.get("deps", []) if d not in done]}
                 for t in tickets if t["status"] == "claimed"]
    review = [{"id": t["id"], "owner": t.get("owner", ""), "title": t["title"],
               "priority": t.get("priority", 2), "commit": t.get("commit", ""), "pr": t.get("pr", "")}
              for t in tickets if t["status"] == "review"]
    open_rows = [{"id": t["id"], "status": LABEL.get(t["status"], t["status"]), "priority": t.get("priority", 2),
                  "title": t["title"], "role": t.get("role", ""), "owner": t.get("owner", ""),
                  "waiting": [d for d in t.get("deps", []) if d not in done]}
                 for t in tickets if t["status"] in ("open", "blocked")]
    turns, usage, promise = _cached_turns_usage_promise(board, tickets)
    raw_msgs = []
    for x in load_messages(board)[-messages:]:
        row = {"id": _msg_id(x), "at": x.get("at", ""), "from": x.get("from", ""), "to": x.get("to", ""),
               "re": x.get("re", ""), "text": x.get("text", ""), "mentions": x.get("mentions") or [],
               "kind": x.get("kind") or "message",
               "delivery": _message_delivery(board, x, agents_by=agents)}
        raw_msgs.append(row)
    seat_names = []
    seen_seats = set()
    def _add_seat(name):
        name = (name or "").strip()
        key = name.lower()
        if name and key not in seen_seats:
            seen_seats.add(key)
            seat_names.append(name)
    for a in out_agents:
        _add_seat(a.get("name"))
    _add_seat(m.get("owner", ""))
    _add_seat(m.get("cos", ""))
    health_items = [{"sev": s, "msg": msg} for s, msg, _fix in health(board, tickets) if s in ("CRIT", "WARN")][:12]
    coverage = _coverage_snapshot(m.get("owner", ""), m.get("cos", ""),
                                open_rows, in_flight, review, out_agents)
    return {
        "project": os.path.basename(os.path.dirname(board)), "generated": now(),
        "master": m.get("owner", ""), "cos": m.get("cos", ""), "counts": counts, "sprint": sprint, "burn": burn,
        "goals": goals,
        "util": sorted(util_rows, key=lambda r: (-r["done"], r["agent"])),
        "in_flight": in_flight,
        "review": review,
        "open": open_rows,
        "agents": out_agents,
        "epics": _epics_snapshot(board, tickets),
        "attention": _attention_snapshot(health_items, coverage),
        "health": health_items,
        # "at" is sent as the raw ISO-8601 (UTC, "...Z") timestamp, unmodified,
        # so the UI can render it in whatever timezone the viewer's browser is
        # actually in -- truncating/reformatting it here would bake in UTC.
        "messages": raw_msgs[::-1],
        # Advitiya PRIORITY agent chats: per-seat thread rail from the same log.
        "seat_threads": seat_thread_summaries(raw_msgs, seat_names),
        "onboarding": _onboarding_checklist(board, tickets),
        "next_step": _next_step_hint(board, tickets, done),
        "empty_board": counts["total"] == 0,
        "turns": turns,
        "usage": usage,
        "promise": promise,
        "objective": {
            "text": (obj or {}).get("text", ""),
            "state": objective_state(obj) if obj else "",
            "exit_criterion": (obj or {}).get("exit_criterion") or "",
            "exit_missing": bool(obj) and objective_exit_missing(obj),
            "wake_gates": "continuous seats: directed DM/@mention; task-only/scheduled seats: explicit tasks; all: stuck/blocked, held, assigned work",
            "stop_condition": STOP_CONDITION,
        },
        "coverage": coverage,
        "graph": workflow_graph(tickets),
    }


_UI_MSG_MAX_BYTES = 65536
_UI_MSG_KINDS = frozenset(("message", "task"))


def _ui_msg_is_json(headers):
    raw = (headers.get("Content-Type") or "").split(";", 1)[0].strip().lower()
    return raw == "application/json"


def _ui_msg_origin_ok(headers):
    """Browser writes send Origin; it must match Host (same-origin).

    Local API clients (curl, urllib, tickets tests) omit Origin — that is
    allowed once Content-Type is JSON and `from` is a registered agent.
    A present Origin that is missing, `null`, or a different host is rejected.
    """
    origin = (headers.get("Origin") or "").strip()
    if not origin:
        return True
    host = (headers.get("Host") or "").strip()
    if not host or origin.lower() == "null":
        return False
    from urllib.parse import urlparse
    parsed = urlparse(origin)
    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        return False
    return parsed.netloc.lower() == host.lower()


def cmd_ui(a, board):
    """Local status UI: serves an auto-refreshing page, /board.json, and a
    composer POST at /msg that posts through post_message() -- same board,
    same messages.jsonl, no second store. /board.json?seat=<name> filters
    messages to that agent-scoped thread (Advitiya PRIORITY agent chats)."""
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

    if a.json:
        print(json.dumps(board_snapshot(board), indent=2))
        return

    class H(BaseHTTPRequestHandler):
        def do_GET(self):
            if self.path.startswith("/board.json"):
                from urllib.parse import parse_qs, urlparse
                seat = (parse_qs(urlparse(self.path).query).get("seat") or [""])[0]
                body = json.dumps(_safe(lambda: board_snapshot_for_request(board, seat=seat), {
                    "error": "snapshot failed",
                    "counts": {"total": 0, "done": 0},
                    "next_step": {"kind": "unreachable", "label": "Snapshot failed",
                                  "message": "Could not read the board — check TICKETS_DIR and board files.",
                                  "cmd": "tickets ui --json"},
                })).encode()
                ctype = "application/json"
            else:
                body = UI_HTML.encode()
                ctype = "text/html; charset=utf-8"
            self.send_response(200)
            self.send_header("Content-Type", ctype)
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_POST(self):
            if not self.path.startswith("/msg"):
                self.send_response(404)
                self.end_headers()
                return
            try:
                try:
                    length = int(self.headers.get("Content-Length") or 0)
                except ValueError:
                    length = -1
                if length < 0 or length > _UI_MSG_MAX_BYTES:
                    raise ValueError("body exceeds %d bytes" % _UI_MSG_MAX_BYTES)
                raw = self.rfile.read(length) if length else b"{}"
                if not _ui_msg_is_json(self.headers):
                    raise ValueError("Content-Type must be application/json")
                if not _ui_msg_origin_ok(self.headers):
                    raise ValueError("origin mismatch")
                payload = json.loads(raw or b"{}")
                sender = str(payload.get("from") or "").strip()
                text = str(payload.get("text") or "").strip()
                to = str(payload.get("to") or "").strip()
                re_ = str(payload.get("re") or "").strip()
                kind = str(payload.get("kind") or "message").strip() or "message"
                if not sender or not text:
                    raise ValueError("from and text are required")
                if kind not in _UI_MSG_KINDS:
                    raise ValueError("kind must be message or task")
                if not _agent_rec(board, sender):
                    raise ValueError("from must be a registered agent")
                rec = post_message(board, sender, text, to, re_, kind=kind)
                status, out = 200, {"ok": True, "posted": fmt_msg(rec)}
            except Exception as e:  # noqa: BLE001 - always answer the composer, never hang it
                status, out = 400, {"ok": False, "error": str(e)}
            body = json.dumps(out).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args):
            pass

    srv = ThreadingHTTPServer((a.host, a.port), H)
    srv.daemon_threads = True
    print("board UI: http://%s:%d  (Ctrl-C to stop; localhost-only; composer posts via tickets msg)" % (a.host, a.port))
    if a.open:
        import subprocess
        subprocess.Popen(["open", "http://%s:%d" % (a.host, a.port)])
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass


QUICKSTART_MARKER = "quickstart.json"

QUICKSTART_TICKETS = [
    {"key": "schema", "title": "Sample: design the data model",
     "role": "backend", "priority": 1,
     "body": "A sample ticket created by `tickets quickstart`.\n\n"
             "It has no dependencies, so it is the one `tickets next` hands out first.\n"
             "Work it like a real ticket: claim it, post an update, then send it to review.\n"
             "Delete the samples whenever you like: tickets quickstart --remove"},
    {"key": "api", "title": "Sample: build the API on top of the model",
     "role": "backend", "priority": 2, "deps": ["schema"],
     "body": "A sample ticket that DEPENDS on the first one.\n\n"
             "`tickets next` will not offer it until the schema ticket is done -- that is\n"
             "the dependency graph doing its job, not the board being empty."},
    {"key": "ui", "title": "Sample: put a screen on the API",
     "role": "console", "priority": 2, "deps": ["api"],
     "body": "The third sample, two hops down the chain.\n\n"
             "Run `tickets graph` to see all three and what is blocking what."},
]


def _quickstart_state(board):
    """What a previous quickstart made here, or None. Makes the command idempotent."""
    path = os.path.join(board, QUICKSTART_MARKER)
    if not os.path.isfile(path):
        return None
    try:
        with open(path) as f:
            return json.load(f)
    except (IOError, ValueError):
        return None


def _quickstart_save(board, state):
    path = os.path.join(board, QUICKSTART_MARKER)
    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        json.dump(state, f, indent=2)
    os.replace(tmp, path)


def _quickstart_alive(board, state):
    """The sample ids from a previous run that still exist (a user may have deleted them)."""
    if not state:
        return []
    have = set(t["id"] for t in load_all(board))
    return [tid for tid in state.get("tickets", []) if tid in have]


def _quickstart_harness():
    """Which agent harness this machine can actually launch, best first.

    Print-only: T-314's `tickets harness check` is the real probe. Quickstart
    only names a harness so it can print a spawn line; it never starts one.
    Returns (name, argv-prefix) or (None, None).
    """
    import shutil
    for name, argv in (("claude", ["claude", "-p"]),
                       ("codex", ["codex", "exec"]),
                       ("cursor-agent", ["cursor-agent", "-p"])):
        if shutil.which(name):
            return name, argv
    return None, None


def cmd_quickstart(a, board):
    """Zero to a first ticket claimed by a real agent. Non-interactive, idempotent."""
    if a.remove:
        state = _quickstart_state(board)
        alive = _quickstart_alive(board, state)
        for tid in alive:
            try:
                os.unlink(ticket_path(board, tid))
            except OSError:
                pass
        try:
            os.unlink(os.path.join(board, QUICKSTART_MARKER))
        except OSError:
            pass
        print("removed %d sample ticket(s)%s" % (
            len(alive), (" (%s)" % ", ".join(alive)) if alive else ""))
        if state and state.get("epic"):
            print("epic %s left in place (it may hold your own work now)" % state["epic"])
        return

    # 1. Bind before writing anything -- the same T-263 condition `init` enforces,
    # through the same primitives, so there is one guard and not a second copy
    # of it that can drift.  Asking only "is there a board?" is not enough: when
    # an ancestor project already has one, the ambient resolution finds it and
    # quickstart would cheerfully populate SOMEONE ELSE'S board.
    explicit = bool(getattr(a, "board", None))
    target, why = _init_resolve_board(a)
    if not explicit and not _same_board(target, board):
        print("board: %s" % target)
        print("  resolved from %s" % why)
        print("  ambient:      %s" % board)
        sys.exit(_init_refusal(target, board))

    if not os.path.isdir(target):
        # Mirror `tickets init`'s own arguments. The quickstart regression test
        # runs this path on a fresh repo, so a new init flag fails loudly there
        # rather than silently at a user's first command.
        init_args = argparse.Namespace(
            board=getattr(a, "board", None), track=False, force=False)
        cmd_init(init_args, board)
        print("")
    board = target

    print("board: %s" % board)

    # 2. sample epic + three tickets with a real dependency chain, created once
    state = _quickstart_state(board)
    alive = _quickstart_alive(board, state)
    if alive:
        print("samples: already here (%s) -- not creating them again" % ", ".join(alive))
        epic_id = (state or {}).get("epic", "")
    else:
        epic = _alloc(epics_dir(board), "E", 3, {
            "title": "Sample epic: a first slice end to end",
            "body": "Created by `tickets quickstart` so the board is not empty on day one.\n"
                    "Remove the samples with `tickets quickstart --remove`.",
            "status": "open", "created": now(), "updated": now(),
        })
        epic_id = epic["id"]
        keymap, made = {}, []
        for spec in QUICKSTART_TICKETS:
            t = create(board, spec["title"], spec["body"], spec.get("role", ""),
                       [], spec.get("priority", 2), epic_id, "", [])
            keymap[spec["key"]] = t["id"]
            made.append(t)
        for spec, t in zip(QUICKSTART_TICKETS, made):
            deps = [keymap[d] for d in spec.get("deps", []) if d in keymap]
            if deps:
                set_deps(board, t["id"], deps)
        _quickstart_save(board, {"epic": epic_id,
                                 "tickets": [t["id"] for t in made],
                                 "created": now()})
        print("epic:  %s  %s" % (epic_id, epic["title"]))
        for spec, t in zip(QUICKSTART_TICKETS, made):
            dep = spec.get("deps") or []
            print("  %s  %-42s %s" % (
                t["id"], t["title"][:42],
                ("after %s" % keymap[dep[0]]) if dep else "ready now"))

    # 3. register whoever is running this, so `next` has someone to hand work to
    agent = a.agent or os.environ.get("TICKET_AGENT") or whoami()
    if agent and not agent.startswith("agent-"):
        # T-314 grew --harness/--cmd; pin every field cmd_join reads so a new
        # join flag fails this Namespace in tests instead of at first-run.
        join_args = _join_namespace(argparse.Namespace(
            roles=a.roles, tool="", model="", can=None, cost=None, best_for="",
            harness="", cmd_template=""), agent)
        # cmd_join prints a full worker briefing; quickstart has its own ending,
        # so keep the one line that matters and drop the rest.
        import io, contextlib
        buf = io.StringIO()
        try:
            with contextlib.redirect_stdout(buf):
                cmd_join(join_args, board)
        except SystemExit:
            print("agent: could not register %r automatically" % agent)
            print("       run: tickets join <name> --roles backend")
        else:
            joined = [ln for ln in buf.getvalue().splitlines() if ln.startswith("joined as ")]
            print(joined[0] if joined else "agent: %s" % agent)
    else:
        agent = ""
        print("agent: none registered (set TICKET_AGENT, or: tickets join <name> --roles backend)")

    # 4. optionally put a real worker on it
    if a.with_agent:
        _quickstart_spawn(a, board, a.with_agent)

    _quickstart_next_steps(board, agent)


def _quickstart_spawn(a, board, name):
    harness, argv = _quickstart_harness()
    if not harness:
        print("")
        print("--with-agent: no agent harness found on PATH (looked for claude, codex, cursor-agent).")
        print("  Install one, or start a worker by hand:  TICKET_AGENT=%s tickets next" % name)
        return
    print("")
    print("worker: %s will run as %s" % (harness, name))
    print("  tickets spawn %s --tool %s" % (name, harness))
    print("  (not launched for you -- quickstart never starts a background process without asking;")
    print("   run the line above, or: TICKET_AGENT=%s %s \"$(tickets prompt)\")" % (name, " ".join(argv)))


def _quickstart_next_steps(board, agent):
    ident = ("TICKET_AGENT=%s " % agent) if agent else ""
    print("")
    print("The three commands that matter:")
    print("  %stickets next                      claim the next ready ticket" % ident)
    print("  %stickets update <id> \"...\"         say where you are, at least every 45 min" % ident)
    print("  %stickets review <id> --notes \"...\" hand it back with evidence" % ident)
    print("")
    print("See it: tickets ui        ->  http://127.0.0.1:8765   (read-only, auto-refresh)")
    print("Learn it: tickets guide   |   docs/first-session.md   |   README.md")


def cmd_guide(a, board):
    print(GUIDE)


# ---- hooks: wire a tool so the board reaches the agent every turn ----------

HOOK_AGENT_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,100}\Z")


def _hook_agent(value):
    """Return a hook-safe, durable agent name.

    Hook commands are shell strings written to another program's settings.  A
    permissive name here would be both an attribution bug and a command
    injection bug, so hook identities use the same alphabet as the durable
    coordination identity extension.
    """
    owner = (value or "").strip()
    if not HOOK_AGENT_RE.fullmatch(owner):
        sys.exit("hooks need --agent with 1-101 letters, digits, dots, underscores or hyphens")
    return owner


def _hook_command(script, board, owner, event, extra=()):
    """Build one command with identity and board frozen into its bytes."""
    words = [
        "env", "TICKET_AGENT=" + owner, "TICKETS_DIR=" + os.path.abspath(board),
        os.path.realpath(script), "hook-run", "--agent", owner, "--event", event,
    ] + list(extra)
    return " ".join(shlex.quote(str(word)) for word in words)


def _atomic_hook_write(path, content, mode=None):
    """Install one generated hook file and retain an exact one-step rollback.

    Rollback refuses if somebody edited the installed file afterwards.  This
    keeps an old receipt from erasing a human or another tool's later change.
    """
    import base64
    import tempfile

    path = os.path.abspath(os.path.expanduser(path))
    data = content.encode("utf-8") if isinstance(content, str) else content
    before_exists = os.path.exists(path) or os.path.islink(path)
    before = open(path, "rb").read() if before_exists else b""
    if before_exists and before == data:
        return False
    previous_mode = (os.stat(path).st_mode & 0o777) if before_exists else None
    receipt = {
        "schema": 1,
        "target": path,
        "before_exists": before_exists,
        "before_mode": previous_mode,
        "before_b64": base64.b64encode(before).decode("ascii"),
        "after_sha256": hashlib.sha256(data).hexdigest(),
    }
    parent = os.path.dirname(path) or "."
    os.makedirs(parent, exist_ok=True)

    def replace_bytes(target, payload, target_mode):
        fd, temporary = tempfile.mkstemp(prefix=".tickets-hook-", dir=parent)
        try:
            with os.fdopen(fd, "wb") as out:
                out.write(payload)
                out.flush()
                os.fsync(out.fileno())
            os.chmod(temporary, target_mode)
            os.replace(temporary, target)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)

    receipt_path = path + ".tickets-rollback.json"
    replace_bytes(receipt_path, (json.dumps(receipt, sort_keys=True) + "\n").encode("utf-8"), 0o600)
    replace_bytes(path, data, mode if mode is not None else (previous_mode or 0o600))
    return True


def _rollback_hook_file(path):
    """Restore the exact bytes replaced by the most recent hook install."""
    import base64
    import tempfile

    path = os.path.abspath(os.path.expanduser(path))
    receipt_path = path + ".tickets-rollback.json"
    try:
        receipt = json.loads(open(receipt_path).read())
    except (IOError, ValueError):
        sys.exit("no hook rollback receipt for %s" % path)
    current = open(path, "rb").read() if (os.path.exists(path) or os.path.islink(path)) else b""
    if hashlib.sha256(current).hexdigest() != receipt.get("after_sha256"):
        sys.exit("refusing rollback: %s changed after the hook install" % path)
    if receipt.get("before_exists"):
        payload = base64.b64decode(receipt.get("before_b64", ""), validate=True)
        parent = os.path.dirname(path) or "."
        fd, temporary = tempfile.mkstemp(prefix=".tickets-hook-rollback-", dir=parent)
        try:
            with os.fdopen(fd, "wb") as out:
                out.write(payload)
                out.flush()
                os.fsync(out.fileno())
            os.chmod(temporary, int(receipt.get("before_mode") or 0o600))
            os.replace(temporary, path)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)
    elif os.path.exists(path) or os.path.islink(path):
        os.unlink(path)
    os.unlink(receipt_path)
    return path


def _pinned_hook_identity(board, owner):
    """Pin and persist identity before a hook reads or writes the board."""
    import contextlib
    import io

    owner = _hook_agent(owner)
    os.environ["TICKET_AGENT"] = owner
    os.environ["TICKETS_DIR"] = os.path.abspath(board)
    try:
        from ticket_coordination import run as coordination_run
    except ImportError:
        sys.exit("ticket_coordination.py is missing; hook identity cannot be verified")
    capture = io.StringIO()
    with contextlib.redirect_stdout(capture):
        coordination_run("identity", argparse.Namespace(), board, globals())
    try:
        identity = json.loads(capture.getvalue())
    except ValueError:
        sys.exit("hook identity verification returned invalid data")
    if identity.get("agent_id") != owner:
        sys.exit("hook identity mismatch: expected %s, got %s" % (owner, identity.get("agent_id")))
    return identity


def cmd_hook_run(a, board):
    """Run a hook action under its baked identity, ignoring ambient identity."""
    owner = _hook_agent(a.agent)
    identity = _pinned_hook_identity(board, owner)
    if a.event == "identity":
        print(json.dumps(identity, sort_keys=True))
        return
    if a.event == "session-start":
        cmd_board(argparse.Namespace(all=False, quiet=False), board)
        return
    if a.event in ("inbox", "agy-inbox"):
        if a.event == "agy-inbox":
            import io
            from contextlib import redirect_stdout
            buf = io.StringIO()
            with redirect_stdout(buf):
                cmd_inbox(argparse.Namespace(owner=owner, seat="", all=False, limit=8, keep=True), board)
            text = buf.getvalue().strip()
            if text and "inbox empty" not in text:
                print(json.dumps({"injectSteps": [{"ephemeralMessage": text}]}))
            else:
                print("{}")
            return
        cmd_inbox(argparse.Namespace(owner=owner, seat="", all=False, limit=8, keep=True), board)
        return
    if a.event in ("stop", "agy-stop"):
        if a.event == "agy-stop":
            import io
            from contextlib import redirect_stdout
            buf = io.StringIO()
            with redirect_stdout(buf):
                cmd_stop_hook(a, board)
            out = buf.getvalue().strip()
            try:
                res = json.loads(out or "{}")
                if res.get("decision") == "block":
                    res["decision"] = "continue"
                print(json.dumps(res))
            except Exception:
                print("{}")
            return
        cmd_stop_hook(a, board)
        return
    if a.event == "task-wake":
        kind = getattr(a, "prompt_kind", "") or ""
        cmd_prompt(argparse.Namespace(agent=owner, master=kind == "master", cos=kind == "cos", extra=""), board)
        return
    sys.exit("unsupported hook event %s" % a.event)

CURSOR_HOOK = r'''#!/usr/bin/env python3
"""Cursor hook installed by `tickets hooks cursor`: surface new board messages.
Reads the hook event JSON on stdin, writes {additional_context, user_message}
on stdout. Keeps its own watermark so `tickets inbox` read-state is untouched.
Identity and board are baked at install; ambient shell values are ignored."""
import argparse, json, os, subprocess, sys
from pathlib import Path

AGENT = %(agent)r
BOARD = Path(%(board)r)
TICKETS = %(script)r
STATE = Path(__file__).resolve().parent / "state" / ("board-%%s.json" %% AGENT)

def main():
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--agent", required=True)
    args = parser.parse_args()
    if args.agent != AGENT:
        print(json.dumps({"user_message": "Ticket board hook identity mismatch"}))
        return 0
    try:
        event = json.loads(sys.stdin.read() or "{}")
    except ValueError:
        event = {}
    env = dict(os.environ, TICKETS_DIR=str(BOARD), TICKET_AGENT=AGENT)
    lines = []
    try:
        verified = subprocess.run(
            [sys.executable, TICKETS, "hook-run", "--agent", AGENT, "--event", "identity"],
            capture_output=True, text=True, timeout=4, env=env)
        identity = json.loads(verified.stdout) if verified.returncode == 0 else {}
        if identity.get("agent_id") != AGENT:
            lines.append("Ticket board identity verification failed for %%s." %% AGENT)
    except (OSError, ValueError, subprocess.SubprocessError):
        lines.append("Ticket board identity verification failed for %%s." %% AGENT)
    path = BOARD / "messages.jsonl"
    STATE.parent.mkdir(parents=True, exist_ok=True)
    last = ""
    if STATE.exists():
        try: last = json.loads(STATE.read_text()).get("last_at", "")
        except ValueError: pass
    new = []
    for ln in path.read_text().splitlines() if path.is_file() else []:
        try: m = json.loads(ln)
        except ValueError: continue
        if (m.get("at") or "") <= last: continue
        to = m.get("to") or ""
        if to not in ("", "all", "everyone", AGENT): continue
        if m.get("from") == AGENT and to in ("", "all", "everyone"): continue
        new.append(m)
    out = {}
    if new:
        lines.append("Ticket board: %%d new message(s) for %%s. Reply with `tickets msg`." %% (len(new), AGENT))
        for m in new[-12:]:
            lines.append("- %%s %%s -> %%s%%s: %%s" %% (m.get("at","?")[5:16], m.get("from","?"), m.get("to") or "everyone",
                         (" [%%s]" %% m["re"]) if m.get("re") else "", (m.get("text") or "")[:220]))
        out["user_message"] = "%%d new ticket-board message(s) for %%s" %% (len(new), AGENT)
        STATE.write_text(json.dumps({"last_at": new[-1].get("at", last)}))
    if event.get("hook_event_name") in ("SessionStart", "UserPromptSubmit"):
        try:
            result = subprocess.run(
                [sys.executable, TICKETS, "knowledge", "query", "--agent", AGENT,
                 "--max-chars", "1600"], capture_output=True, text=True, timeout=4, env=env)
            if result.returncode == 0 and result.stdout.strip() != "no relevant knowledge found":
                lines.append(result.stdout.strip())
        except (OSError, subprocess.SubprocessError):
            pass
    if lines:
        out["additional_context"] = "\n".join(lines)[:3500]
    print(json.dumps(out)); return 0

if __name__ == "__main__":
    raise SystemExit(main())
'''


def cmd_hooks(a, board):
    """Install identity-pinned hooks for Claude, Codex, Cursor, or a remote harness."""
    root = os.path.dirname(board)
    script = os.path.realpath(__file__)

    def rollback(paths):
        restored = []
        for target in paths:
            restored.append(_rollback_hook_file(target))
        print("rolled back ticket hooks: %s" % ", ".join(restored))

    def ours(entry):
        for hk in entry.get("hooks", []) if isinstance(entry, dict) else []:
            cmd = str(hk.get("command", ""))
            if (script in cmd or "tickets.py" in cmd or "tickets inbox --keep" in cmd
                    or " hook-run --agent " in cmd):
                return True
        return False
    if a.tool == "claude":
        owner = _hook_agent(a.agent)
        # A project/worktree-scoped default lets two Claude terminals keep
        # different identities. Operators can still name a global file
        # explicitly with --settings when that is truly what they want.
        path = os.path.expanduser(getattr(a, "settings", "") or os.path.join(os.getcwd(), ".claude", "settings.json"))
        if getattr(a, "rollback", False):
            rollback([path])
            return
        try:
            with open(path) as f:
                s = json.load(f)
        except (IOError, ValueError):
            s = {}
        hooks = s.setdefault("hooks", {})
        ss = hooks.setdefault("SessionStart", [])
        ss[:] = [h for h in ss if not ours(h)]
        session_cmd = _hook_command(script, board, owner, "session-start")
        ss.append({"matcher": "startup|resume|clear|compact",
                   "hooks": [{"type": "command", "command": session_cmd, "timeout": 10}]})
        ups = hooks.setdefault("UserPromptSubmit", [])
        ups[:] = [h for h in ups if not ours(h)]
        ups.append({"hooks": [{"type": "command", "command": _hook_command(
            script, board, owner, "inbox"), "timeout": 10}]})
        # Keep a turn alive while the agent still has board work. Loop-guarded
        # (stop_hook_active) and rate-capped; --no-stop removes it.
        st = hooks.setdefault("Stop", [])
        st[:] = [h for h in st if not ours(h)]
        if getattr(a, "stop", True):
            st.append({"hooks": [{"type": "command", "command": _hook_command(
                script, board, owner, "stop"), "timeout": 15}]})
        if not st:
            hooks.pop("Stop", None)
        allow = s.setdefault("permissions", {}).setdefault("allow", [])
        for p in ("Bash(tickets:*)", "Bash(%s:*)" % script):
            if p not in allow:
                allow.append(p)
        _atomic_hook_write(path, json.dumps(s, indent=2) + "\n", 0o600)
        print("Claude Code hooks in %s for %s: SessionStart -> board; UserPromptSubmit -> inbox; Stop -> %s."
              % (path, owner, "keep working while board work remains" if getattr(a, "stop", True) else "off"))
        print("Identity is baked into the hook; the launching shell's TICKET_AGENT is ignored.")
        return
    if a.tool == "codex":
        hp = os.path.expanduser(getattr(a, "hooks_file", "") or "~/.codex/hooks.json")
        if getattr(a, "rollback", False):
            rollback([hp])
            return
        agent = _hook_agent(a.agent)
        wt = os.path.abspath(a.worktree) if getattr(a, "worktree", "") else ""
        try:
            with open(hp) as f:
                cfg = json.load(f)
        except (IOError, ValueError):
            cfg = {}
        hooks = cfg.setdefault("hooks", {})
        cmd = " ".join(shlex.quote(str(word)) for word in (
            ["env", "TICKET_AGENT=" + agent, "TICKETS_DIR=" + os.path.abspath(board),
             script, "codex-hook", "--agent", agent]
            + (["--worktree", wt] if wt else [])))
        def same_codex_scope(entry):
            rendered = json.dumps(entry)
            if "codex-hook" not in rendered:
                return False
            # A global Codex hook file may serve several worktrees. Replace
            # every Atman-generated identity for this worktree, including
            # entries written by an older immutable release. Otherwise one
            # lifecycle event can run twice under conflicting identities.
            if wt:
                return ("--worktree %s" % wt) in rendered
            return "--worktree" not in rendered
        for ev in ("SessionStart", "UserPromptSubmit"):
            lst = hooks.setdefault(ev, [])
            lst[:] = [e for e in lst if not same_codex_scope(e)]
            lst.append({"hooks": [{"type": "command", "command": cmd, "timeout": 5,
                                   "statusMessage": "Checking the ticket board",
                                   "additionalContextLimit": 2000}]})
        _atomic_hook_write(hp, json.dumps(cfg, indent=2) + "\n", 0o600)
        print("Codex hooks in %s for %s%s: SessionStart + UserPromptSubmit -> board context. "
              "Trust it with /hooks in Codex; AGENTS.md carries the rules." % (hp, agent, (" (scoped to %s)" % wt) if wt else ""))
        return
    if a.tool == "cursor":
        agent = _hook_agent(a.agent)
        cursor_root = os.path.abspath(a.worktree) if getattr(a, "worktree", "") else root
        hdir = os.path.join(cursor_root, ".cursor", "hooks")
        os.makedirs(hdir, exist_ok=True)
        sp = os.path.join(hdir, "tickets-board.py")
        hp = os.path.join(cursor_root, ".cursor", "hooks.json")
        if getattr(a, "rollback", False):
            rollback([hp, sp])
            return
        rendered = CURSOR_HOOK % {"agent": agent, "board": os.path.abspath(board), "script": script}
        existing_script = open(sp).read() if os.path.exists(sp) else ""
        if (existing_script and "Cursor hook installed by `tickets hooks cursor`" not in existing_script
                and not a.force):
            sys.exit("%s is not a Ticket Board hook; pass --force only after reviewing it" % sp)
        if not existing_script or a.force or existing_script != rendered:
            _atomic_hook_write(sp, rendered, 0o755)
        try:
            with open(hp) as f:
                cfg = json.load(f)
        except (IOError, ValueError):
            cfg = {"version": 1, "hooks": {}}
        entry = {"command": "%s --agent %s" % (shlex.quote(sp), shlex.quote(agent)), "timeout": 15}
        for ev in ("sessionStart", "beforeSubmitPrompt", "stop"):
            lst = cfg.setdefault("hooks", {}).setdefault(ev, [])
            # Replace both the current generated hook and the pre-T-633 global
            # message-board hook. Leaving the latter installed would run two
            # identities for one Cursor event and let its ambient identity
            # disagree with the newly baked one.
            lst[:] = [x for x in lst if not any(marker in json.dumps(x) for marker in (
                "tickets-board", "check-message-board.py"))]
            lst.append(dict(entry, **({"loop_limit": 2} if ev == "stop" else {})))
        _atomic_hook_write(hp, json.dumps(cfg, indent=2) + "\n", 0o600)
        print("Cursor: %s + %s for %s (sessionStart, beforeSubmitPrompt, stop). Enable Hooks in Cursor settings."
              % (os.path.relpath(hp, cursor_root), os.path.relpath(sp, cursor_root), agent))
        print("Identity is baked into the hook; the launching shell's TICKET_AGENT is ignored.")
        return
    if a.tool in ("grok", "grokbots"):
        a.tool = "cursor"
        cmd_hooks(a, board)
        print("Grok: Cursor persist hooks (grok-worker / grokbots / Cursor Grok seats).")
        return
    if a.tool in ("agy", "antigravity"):
        owner = _hook_agent(a.agent)
        wt = os.path.abspath(getattr(a, "worktree", "") or os.getcwd())
        agents_path = os.path.join(wt, ".agents")
        os.makedirs(agents_path, exist_ok=True)
        hp = os.path.join(agents_path, "hooks.json")
        if getattr(a, "rollback", False):
            rollback([hp])
            return
        try:
            with open(hp) as f:
                cfg = json.load(f)
        except (IOError, ValueError):
            cfg = {}
        inbox_cmd = _hook_command(script, board, owner, "agy-inbox")
        stop_cmd = _hook_command(script, board, owner, "agy-stop")
        entry = cfg.setdefault("tickets-board", {})
        entry["PreInvocation"] = [{"type": "command", "command": inbox_cmd, "timeout": 10}]
        if getattr(a, "stop", True):
            entry["Stop"] = [{"type": "command", "command": stop_cmd, "timeout": 15}]
        else:
            entry.pop("Stop", None)
        _atomic_hook_write(hp, json.dumps(cfg, indent=2) + "\n", 0o600)
        print("Antigravity hooks in %s for %s: PreInvocation -> inbox; Stop -> %s."
              % (hp, owner, "keep working while board work remains" if getattr(a, "stop", True) else "off"))
        print("Identity is baked into the hook; the launching shell's TICKET_AGENT is ignored.")
        return
    if a.tool in ("devin", "cognition", "gemini"):
        owner = _hook_agent(a.agent)
        named = a.tool
        a.tool = "remote"
        a.prompt_kind = getattr(a, "prompt_kind", "") or ""
        cmd_hooks(a, board)
        print("%s: identity-pinned hook wrapper configured for %s." % (named, owner))
        return
    if a.tool == "remote":
        owner = _hook_agent(a.agent)
        wrapper = os.path.expanduser(getattr(a, "wrapper", "") or ("~/.local/bin/tickets-" + owner))
        manifest = wrapper + ".hooks.json"
        if getattr(a, "rollback", False):
            rollback([manifest, wrapper])
            return
        prompt_kind = getattr(a, "prompt_kind", "") or ""
        commands = {
            "identity": _hook_command(script, board, owner, "identity"),
            "SessionStart": _hook_command(script, board, owner, "session-start"),
            "inbox": _hook_command(script, board, owner, "inbox"),
            "Stop": _hook_command(script, board, owner, "stop"),
            "taskWake": _hook_command(script, board, owner, "task-wake",
                                       ("--prompt-kind", prompt_kind) if prompt_kind else ()),
        }
        wrapper_text = """#!/bin/sh
# Generated by tickets hooks remote. This identity is deliberate and local to this wrapper.
export TICKET_AGENT=%s
export TICKETS_DIR=%s
if [ \"$#\" -eq 0 ]; then
  exec %s hook-run --agent %s --event task-wake%s
fi
exec %s \"$@\"
""" % (shlex.quote(owner), shlex.quote(os.path.abspath(board)), shlex.quote(script),
       shlex.quote(owner), (" --prompt-kind " + shlex.quote(prompt_kind)) if prompt_kind else "",
       shlex.quote(script))
        _atomic_hook_write(wrapper, wrapper_text, 0o700)
        pinned = shlex.quote(os.path.abspath(wrapper))
        remote_base = "%s remote" % pinned
        payload = {"schema": 2, "agent": owner, "board": os.path.abspath(board),
                   "wrapper": os.path.abspath(wrapper), "commands": commands,
                   "adapter": {
                       "wake_mode": wake_mode_of(board, owner),
                       "protocol": "fenced-wake-v1",
                       "delivery": "atomic-claim-long-poll",
                       "register_command": "%s register --agent %s --bridge-id {bridge_id}" % (
                           remote_base, shlex.quote(owner)),
                       "heartbeat_command": "%s heartbeat --agent %s --lease-id {lease_id} --fence {fence}" % (
                           remote_base, shlex.quote(owner)),
                       "next_command": "%s next --agent %s --lease-id {lease_id} --fence {fence} --wait 25%s" % (
                           remote_base, shlex.quote(owner),
                           (" --prompt-kind " + shlex.quote(prompt_kind)) if prompt_kind else ""),
                       "start_command": "%s start --agent %s --lease-id {lease_id} --fence {fence} --claim-id {claim_id}" % (
                           remote_base, shlex.quote(owner)),
                       "end_command": "%s end --agent %s --lease-id {lease_id} --fence {fence} --claim-id {claim_id} --exit {exit}" % (
                           remote_base, shlex.quote(owner)),
                       "release_command": "%s release --agent %s --lease-id {lease_id} --fence {fence}" % (
                           remote_base, shlex.quote(owner)),
                       "dedupe": "atomic trigger claim under one exclusive fenced bridge lease",
                       "offline": "wake stays queued; register/reconnect before claiming it",
                   }}
        _atomic_hook_write(manifest, json.dumps(payload, indent=2, sort_keys=True) + "\n", 0o600)
        print("Remote hook wrapper for %s: %s" % (owner, wrapper))
        print("Manifest schema 2: register one fenced bridge, long-poll next, then start/end each claimed run.")
        print("No arguments prints the %s task-wake prompt; normal ticket commands stay pinned to this identity."
              % (prompt_kind or "worker"))
        return


def cmd_mine(a, board):
    owner = whoami(a.owner)
    tickets = load_all(board)
    mine = [t for t in tickets if t.get("status") == "claimed" and t.get("owner") == owner]
    if not mine:
        print("no claimed tickets for %s" % owner)
        return
    for t in mine:
        print(line(t, tickets))


def cmd_init(a, board):
    # `board` is the AMBIENT resolution (board_dir()): where every later
    # `tickets` command from this cwd will go.  `target` is resolved
    # independently, from cwd alone.  T-263: they used to be the same value by
    # construction, so init could report success and write somewhere else.
    target, why = _init_resolve_board(a)
    explicit = bool(getattr(a, "board", None))

    # Acceptance 3 -- "which board am I about to write to" is answered BEFORE
    # the first write, never after it.
    print("board: %s" % target)
    print("  resolved from %s" % why)
    if not _same_board(target, board):
        print("  ambient:      %s" % board)

    # Acceptance 1 -- create AND bind, or fail loudly. Never success-then-
    # resolve-elsewhere.
    if not explicit and not _same_board(target, board):
        sys.exit(_init_refusal(target, board))

    board = target
    root = os.path.dirname(board)
    os.makedirs(board, exist_ok=True)
    written = []

    rules_dir = os.path.join(root, ".cursor", "rules")
    os.makedirs(rules_dir, exist_ok=True)
    rule = os.path.join(rules_dir, "tickets.mdc")
    with open(rule, "w") as f:
        f.write(CURSOR_RULE)
    written.append(rule)

    agents = os.path.join(root, "AGENTS.md")
    existing = ""
    if os.path.exists(agents):
        with open(agents) as f:
            existing = f.read()
    if "Shared ticket board" not in existing:
        with open(agents, "a") as f:
            if existing and not existing.endswith("\n"):
                f.write("\n")
            if existing:
                f.write("\n")
            f.write(PROTOCOL)
        written.append(agents)
    else:
        written.append(agents + " (already had it, left alone)")

    gitignore = os.path.join(root, ".gitignore")
    if not a.track:
        body = ""
        if os.path.exists(gitignore):
            with open(gitignore) as f:
                body = f.read()
        if ".tickets/" not in body:
            with open(gitignore, "a") as f:
                if body and not body.endswith("\n"):
                    f.write("\n")
                f.write(".tickets/\n")
            written.append(gitignore)

    if not os.path.exists(master_path(board)):
        with open(master_path(board), "w") as f:
            f.write(MASTER_TEMPLATE)
        written.append(master_path(board))

    for w in written:
        print("wrote: %s" % w)

    # Acceptance 1 again, after the fact: VERIFY the bind with the real
    # resolver rather than asserting it. An init that wrote files but did not
    # bind is exactly the failure this ticket exists to stop.
    bound = board_dir(discover_children=True)
    if _same_board(bound, board):
        print("bound: `tickets` run from %s resolves to this board." % os.getcwd())
    elif explicit:
        print("\nNOT BOUND: you asked for --board %s, but `tickets` run from %s "
              "still resolves to %s.\n  To use the board you just wrote:  "
              "export TICKETS_DIR=%s" % (board, os.getcwd(), bound, board))
    else:
        sys.exit(
            "INIT WROTE THE BOARD BUT IT IS NOT BOUND: wrote %s, yet `tickets` "
            "from %s still resolves to %s. Refusing to report success (T-263)."
            % (board, os.getcwd(), bound))
    print("\nClaude Code: install a scoped hook with `tickets hooks claude --agent <name>`.")
    print("Codex and Cursor read AGENTS.md; Cursor also gets .cursor/rules/tickets.mdc.")


PROTOCOL = """## Shared ticket board

Work here is coordinated through a ticket board that Claude Code, Codex and
Cursor all share. It lives in `.tickets/` and is driven only through the
Atman CLI -- never edit files in `.tickets/` by hand, or atomic claiming
breaks and two agents will do the same work. The primary public name is `atm`;
`tickets` is a compatibility alias for the same implementation, arguments,
exit codes, and board.

Run `tickets board` for the current state, or `tickets graph` to see the whole
dependency tree with each node's status and owner.

**Connect first** (once per session; `tickets connect` prints the long form):

    export TICKET_AGENT=<your-unique-name>     # claude-opus, codex, grok ...
    tickets join $TICKET_AGENT --roles backend --can docker,browser --cost high
    # omit --harness: prints harness=claude (default); label only until watch/spawn
    tickets master                             # briefing + health
    tickets inbox                              # messages addressed to you

**Rules for every agent**

1. Claim before you work (`tickets next`). Never work without a ticket; never
   edit `.tickets/` by hand. Hold one ticket at a time.
2. Finish what you claim. If you cannot, `tickets block --reason` or
   `tickets reopen` -- never go silent.
3. You may create, split, re-wire and assign tickets. Extending the graph is
   expected. Anyone can become master with `tickets master take`.
4. Work on your own git worktree and branch, never on main. Commit as you go.
   `tickets review` and `tickets done` refuse from main or with uncommitted files.
5. Post `tickets update <id> "..."` at least every 45 minutes and at every
   milestone. Longer silence is treated as a timeout and the ticket may be
   reopened for someone else.
6. When finished, submit -- do not close: `tickets review <id> --notes "paths
   touched, tests run, decisions dependents must match"` (branch@sha is added
   automatically; `--pr N` if you opened one). The MASTER reviews, merges to
   main and closes it with `tickets done`. Claim your next ticket right away.
7. Tickets can declare `needs` (docker, browser, own-machine, gpu ...). You only
   receive tickets whose needs you registered with `--can`. Expensive agents
   are steered to priority-1 work, cheap agents to routine work.
8. Stuck? Say so at once: `tickets msg "stuck: <what, tried, need>" --to <master>
   --re <id>` and `tickets update <id> "stuck: ..."`; `tickets block` if you
   cannot continue. The master's job is to unblock you. Never wait silently.

Statuses: TO DO -> IN PROGRESS -> IN REVIEW -> DONE, or BLOCKED. Set them with
`tickets status <id> todo|in-progress|review|blocked|done` or the dedicated
commands below; `tickets list` shows the label on every line.

**The loop**

    tickets next                          # claims -> IN PROGRESS; prints handoffs + timing
    tickets update T-002 "..."            # progress, every 45 min
    tickets msg "question" --to claude-opus --re T-002
    tickets review T-002 --notes "..."    # -> IN REVIEW; master is messaged
    tickets who                           # where everyone is: worktree, branch, ticket

`tickets next` prints the ticket body, briefing paths, every note on direct
dependencies, and the latest note on earlier ancestors. Only one agent can
ever hold a ticket.

**Epics and sprints.** Tickets carry `epic` (E-001) and `sprint` (S-01).
`tickets next` prefers the active sprint. `tickets epic list` / `tickets
sprint show` give progress bars; `tickets sprint close S-01 --carry S-02`
rolls unfinished work forward.

The `--notes` text on `done` is shown to whoever picks up a dependent ticket.
Write what the next agent needs -- file paths, names, decisions they must match
-- not a summary of your effort.

**As the planner**, create the whole dependency graph in one shot. `deps` may
reference a `key` from the same plan or an existing `T-` id:

    tickets plan <<'EOF'
    [{"key":"api","title":"Build REST API","role":"backend","body":"details","deps":[]},
     {"key":"ui","title":"Build login UI","role":"frontend","deps":["api"]}]
    EOF

Tickets whose dependencies are unfinished stay invisible to `tickets next`
until those dependencies are marked done, so workers cannot start too early.

**Adding work to a graph that already exists.** Any agent can extend the graph
mid-run -- this is normal, not a last resort:

    tickets create "Add rate limiting" --deps T-002
    tickets create "DB migration" --blocks T-002
    tickets dep T-004 --after T-003
    tickets dep T-004 --drop T-003
"""

CURSOR_RULE = """---
description: Shared ticket board used to coordinate work with other AI agents
alwaysApply: true
---

""" + PROTOCOL


class _LoudArgumentParser(argparse.ArgumentParser):
    """argparse's default error() writes to stderr only and exits 2. A caller
    that reads just stdout (a hook, a pipe through tail, a skim) sees an empty
    string and can reasonably conclude nothing went wrong -- worse, when the
    rejected argument is free text like a --notes value, the "unrecognized
    arguments" message ECHOES THAT TEXT BACK, so it reads exactly like the
    caller's own note succeeding. (T-246: this masked two silent no-op
    `tickets reopen --notes ...` calls for a full pass before anyone noticed.)
    Make the failure impossible to miss: still argparse's own usage+error on
    stderr, but with an explicit NO CHANGE WAS MADE as the trailing line, so
    the tail of the output is the warning rather than the caller's own text.
    add_subparsers() propagates this class to every subparser by default
    (parser_class defaults to type(self)), so this covers all of them."""
    def error(self, message):
        self.print_usage(sys.stderr)
        self.exit(2, "%(prog)s: error: %(message)s\n%(prog)s: NO CHANGE WAS MADE\n" % {
            "prog": self.prog, "message": message,
        })


def _release_commit():
    """Resolved release commit from release.json, or None if uninstalled."""
    root = os.path.dirname(os.path.realpath(__file__))
    manifest = os.path.join(root, "release.json")
    if not os.path.isfile(manifest):
        return None
    try:
        with open(manifest) as source:
            release = json.load(source)
        commit = release.get("commit")
        return str(commit).strip() if commit else None
    except (OSError, ValueError, KeyError, TypeError):
        return None


def release_status():
    """Report installed provenance without discovering or touching a board.

    Every manifest entry is hashed.  Size alone cannot establish integrity:
    different bytes of the same length must never be reported as verified.
    Package trees are also closed over the manifest so an extra importable
    file cannot enter an otherwise pinned release.
    """
    import hashlib
    root = os.path.dirname(os.path.realpath(__file__))
    manifest = os.path.join(root, "release.json")
    if not os.path.isfile(manifest):
        return "tickets (uninstalled checkout; no pinned release)"
    try:
        with open(manifest) as source:
            release = json.load(source)
        files = release["files"]
        package_roots = sorted({"/".join(name.split("/")[:2])
                                for name in files if name.startswith("src/")
                                and len(name.split("/")) > 2})
        for package_root in package_roots:
            package_path = os.path.join(root, *package_root.split("/"))
            expected_dirs = {os.path.dirname(name) for name in files
                             if name.startswith(package_root + "/")}
            expected_dirs.add(package_root)
            for directory in tuple(expected_dirs):
                parent = os.path.dirname(directory)
                while parent.startswith(package_root):
                    expected_dirs.add(parent)
                    if parent == package_root:
                        break
                    parent = os.path.dirname(parent)
            if os.path.islink(package_path):
                return "tickets DRIFTED release %s (%s)" % (
                    release["commit"], package_root)
            for dirpath, dirnames, filenames in os.walk(package_path):
                for dirname in dirnames:
                    actual_name = os.path.relpath(
                        os.path.join(dirpath, dirname), root).replace(os.sep, "/")
                    if actual_name not in expected_dirs or os.path.islink(
                            os.path.join(dirpath, dirname)):
                        return "tickets DRIFTED release %s (%s)" % (
                            release["commit"], actual_name)
                for filename in filenames:
                    actual_name = os.path.relpath(
                        os.path.join(dirpath, filename), root).replace(os.sep, "/")
                    if actual_name not in files:
                        return "tickets DRIFTED release %s (%s)" % (
                            release["commit"], actual_name)
        for name in sorted(files):
            path = os.path.join(root, name)
            recorded = files[name]
            expected_sha, expected_size = (
                (recorded["sha256"], recorded["size"]) if isinstance(recorded, dict)
                else (recorded, None))
            with open(path, "rb") as source:
                data = source.read()
            actual = hashlib.sha256(data).hexdigest()
            if ((expected_size is not None and len(data) != expected_size)
                    or actual != expected_sha):
                return "tickets DRIFTED release %s (%s)" % (release["commit"], name)
        return "tickets commit %s (verified release)" % release["commit"]
    except (OSError, ValueError, KeyError, TypeError):
        return "tickets INVALID release provenance"


# Backward-compatible alias: scripts/tests may still import the old name.
release_version = release_status


def cmd_self(a, board):
    """Print which tickets.py is executing and how it was installed."""
    import shutil
    script = os.path.realpath(__file__)
    print("script: %s" % script)
    print("status: %s" % release_status())
    seat = whoami()
    if board and seat and not seat.startswith("agent-"):
        harness = (load_workforce(board).get(seat, {}) or {}).get("harness") or "claude"
        sa = _session_adapters()
        ep, was_stale = sa.live_endpoint(board, seat)
        stored = sa.read_endpoint(board, seat)
        if ep:
            print("persistent: yes -- seat %s native %s endpoint (pid %s, registered %s)" % (
                seat, ep.get("provider"), ep.get("pid", "?"), ep.get("at", "?")))
        elif stored and was_stale:
            print("persistent: no -- seat %s native identity is retained but offline after TTL "
                  "(reconnect with `tickets join %s --persistent` or a session heartbeat)"
                  % (seat, seat))
        elif was_stale:
            print("persistent: no -- seat %s had a native endpoint but it went stale "
                  "(re-register with `tickets join %s --persistent`)" % (seat, seat))
        else:
            probe = sa.probe_provider(sa.provider_for_harness(harness))
            print("persistent: no -- seat %s has no native endpoint (probe: %s)" % (
                seat, probe.get("reason", "ok") if not probe.get("ok") else "transport available"))
    print("cli:    primary=%s alias=%s (one implementation)" % (PRIMARY_CLI_NAME, COMPAT_CLI_NAME))
    on_path = None
    for path_name in (PRIMARY_CLI_NAME, COMPAT_CLI_NAME):
        found = shutil.which(path_name)
        if not found:
            continue
        resolved = os.path.realpath(found)
        print("PATH:   %s (%s)" % (found, path_name))
        if resolved != found:
            print("        -> %s" % resolved)
        if resolved != script:
            print("        running %s" % script)
        if path_name == COMPAT_CLI_NAME:
            on_path = found
        try:
            with open(resolved) as source:
                launcher = source.read(512)
            if "tickets-releases" in launcher and "execv" in launcher:
                print("        (release launcher shim)")
        except OSError:
            pass
    invoked = os.path.realpath(sys.argv[0])
    if invoked != script and (not on_path or invoked != os.path.realpath(on_path)):
        print("invoked: %s" % sys.argv[0])


def main():
    status = release_status()
    p = _LoudArgumentParser(prog=cli_prog(), description=__doc__.split("\n")[0],
                           epilog=status)
    p.add_argument("--version", action="version", version=status)
    sub = p.add_subparsers(dest="cmd")

    c = sub.add_parser("create", help="create one ticket")
    c.add_argument("title")
    c.add_argument("--body", "-b", default="")
    c.add_argument("--role", "-r", default="")
    c.add_argument("--deps", "-d", default="", help="wait for these tickets")
    c.add_argument("--blocks", "-B", default="", help="insert upstream of these tickets")
    c.add_argument("--priority", "-p", type=int, default=2, help="1=critical/hard, 2=normal, 3=routine")
    c.add_argument("--epic", "-e", default="")
    c.add_argument("--sprint", "-s", default=None, help="S-01; default: active sprint if --in-sprint")
    c.add_argument("--in-sprint", action="store_true", help="tag with the active sprint")
    c.add_argument("--needs", default="", help="capabilities required: docker,browser,own-machine")
    c.set_defaults(fn=cmd_create)

    c = sub.add_parser("assign", help="modify a ticket: epic, sprint, role, owner, needs, priority")
    c.add_argument("id")
    c.add_argument("--epic", default=None)
    c.add_argument("--sprint", default=None)
    c.add_argument("--role", default=None)
    c.add_argument("--owner", default=None, help="hard-assign (claims on their behalf)")
    c.add_argument("--needs", default=None)
    c.add_argument("--priority", type=int, default=None)
    c.add_argument("--title", default="")
    c.add_argument("--by", default="")
    c.add_argument("--notes", "-n", default="", help="why, appended to the recorded change note")
    c.set_defaults(fn=cmd_assign)

    c = sub.add_parser("reserve", help="reserve a ticket for an agent without claiming it")
    c.add_argument("id")
    c.add_argument("--for", dest="for_agent", default="", help="agent who should claim it when it is ready")
    c.add_argument("--drop", action="store_true", help="clear reserved_for (anyone)")
    c.add_argument("--owner", "-o", default="")
    c.set_defaults(fn=cmd_reserve)

    c = sub.add_parser("hold", help="park a ticket so next/route will not claim it (tester-week HOLD)")
    c.add_argument("id")
    c.add_argument("--reason", default="", help="why it is parked")
    c.add_argument("--clear", action="store_true", help="allow next to claim it again")
    c.set_defaults(fn=cmd_hold)

    c = sub.add_parser("epic", help="epics: create | list | show | done")
    es = c.add_subparsers(dest="epic_cmd")
    x = es.add_parser("create"); x.add_argument("title"); x.add_argument("--body", "-b", default="")
    es.add_parser("list")
    x = es.add_parser("show"); x.add_argument("id")
    x = es.add_parser("done"); x.add_argument("id")
    c.set_defaults(fn=cmd_epic, epic_cmd="list")

    c = sub.add_parser("sprint", help="sprints: create | start | add | show | close | list")
    ss = c.add_subparsers(dest="sprint_cmd")
    x = ss.add_parser("create"); x.add_argument("goal"); x.add_argument("--start", default="")
    x.add_argument("--end", default=""); x.add_argument("--activate", action="store_true")
    x.add_argument("--close-previous", action="store_true")
    x = ss.add_parser("start"); x.add_argument("id"); x.add_argument("--close-previous", action="store_true")
    x = ss.add_parser("add"); x.add_argument("id"); x.add_argument("tickets", help="T-001,T-002")
    x = ss.add_parser("show"); x.add_argument("id", nargs="?", default="")
    x = ss.add_parser("close"); x.add_argument("id"); x.add_argument("--carry", default="", help="move unfinished to this sprint")
    ss.add_parser("list")
    c.set_defaults(fn=cmd_sprint, sprint_cmd="list")

    c = sub.add_parser("master", help="master node: brief | take | release | log | init")
    ms = c.add_subparsers(dest="master_cmd")
    ms.add_parser("brief")
    x = ms.add_parser("take"); x.add_argument("--owner", "-o")
    x = ms.add_parser("release"); x.add_argument("--owner", "-o")
    x = ms.add_parser("log"); x.add_argument("text"); x.add_argument("--owner", "-o")
    x = ms.add_parser("cos", help="set/show the chief of staff (review, unblock, merge)"); x.add_argument("text", nargs="?", default=""); x.add_argument("--owner", "-o")
    x = ms.add_parser("init"); x.add_argument("--force", action="store_true")
    c.set_defaults(fn=cmd_master, master_cmd="brief", owner=None, force=False)

    c = sub.add_parser("join", help="register this agent: name, roles, capabilities, cost")
    c.add_argument("name", nargs="?", default="")
    c.add_argument("--roles", default=None, help="backend,console")
    c.add_argument("--can", default=None, help="docker,browser,own-machine,gpu")
    c.add_argument("--cost", choices=("low", "medium", "high"), default=None)
    c.add_argument("--harness", default="",
                   help="label for watch/spawn, not invoked at join: claude | codex | "
                        "cursor | cursor+claude | remote | custom | custom:<cmd> | <executable>. "
                        "omit prints harness=claude (default) and does not start Claude")
    c.add_argument("--tool", default="", help="original spelling of --harness")
    # dest is cmd_template, not cmd: `sub = p.add_subparsers(dest="cmd")` above
    # means main() dispatches on a.cmd, and a --cmd flag would overwrite the
    # subcommand name with the harness template (or with "" when absent, which
    # sends every `join` straight to print_help).
    c.add_argument("--cmd", dest="cmd_template", default="",
                   help="shell template for a custom harness; placeholders {prompt_file} {cwd} {agent}")
    c.add_argument("--model", default="", help="e.g. opus, sonnet, gpt-5, grok-4")
    c.add_argument("--best-for", default="", help="free text; keywords are matched against ticket titles by `route`")
    c.add_argument("--wake-mode", choices=WAKE_MODES, default=None,
                   help="durable DM policy: continuous wakes on directed messages; task-only needs --task; scheduled uses heartbeat/task gates")
    c.add_argument("--lifecycle", choices=LIFECYCLES, default=None,
                   help="persistent seats stay known after exit; ephemeral seats are one-shot and not reachable after the session ends")
    c.add_argument("--knowledge-dir", default="",
                   help="canonical repo-backed knowledge/ directory inherited by this seat")
    c.add_argument("--persistent", action="store_true",
                   help="lifecycle=persistent and register this interactive session's native wake endpoint (socket/queue/resume)")
    c.add_argument("--alias", default="",
                   help="stable role alias (ceo or cos) pointing at this unique runtime identity")
    c.add_argument("--transfer", action="store_true",
                   help="audited handover when this name's provider/session/runner identity changes; "
                        "strips auth, endpoint, session, ticket pointer, and limits; keeps message history")
    c.set_defaults(fn=cmd_join)

    c = sub.add_parser("retire", help="remove a seat from the board (inverse of join)")
    c.add_argument("name", nargs="?", default="")
    c.add_argument("--owner", "-o", default="", help="who is performing the retire (default: TICKET_AGENT)")
    c.set_defaults(fn=cmd_retire)

    c = sub.add_parser("harness", help="BYOA: check or list the harness that actually runs each agent")
    hs = c.add_subparsers(dest="harness_cmd")
    x = hs.add_parser("check", help="run the agent's harness on a trivial prompt under a time cap")
    x.add_argument("name", nargs="?", default="")
    x.add_argument("--harness", default="", help="override the registered harness for this check")
    x.add_argument("--cmd", dest="cmd_template", default="", help="override the registered command template")
    x.add_argument("--model", default="")
    x.add_argument("--cwd", default="", help="run the probe here (default: the agent's worktree)")
    x.add_argument("--timeout", type=int, default=HARNESS_CHECK_TIMEOUT, help="seconds (default 60)")
    x = hs.add_parser("auth", help="check login without a model run; optionally log in and recover a stale watch lock")
    x.add_argument("name", nargs="?", default="")
    x.add_argument("--harness", default="", help="override the registered harness for this check")
    x.add_argument("--login", action="store_true", help="run the harness's interactive login, then verify identity")
    x.add_argument("--recover-stale", action="store_true", help="atomically reclaim a dead watcher pid lock")
    x.add_argument("--timeout", type=int, default=15)
    hs.add_parser("list", help="every registered agent, its harness and its last check")
    hs.add_parser("available",
                  help="probe command -v and usage remaining/reset for every catalog row (missing remaining/reset is FAIL; do not spawn)")
    hs.add_parser("usage",
                  help="auto-check remaining/reset for every catalog row (missing is FAIL; do not spawn)")
    c.set_defaults(fn=cmd_harness, harness_cmd="list", name="", harness="", cmd_template="", model="", cwd="",
                   timeout=HARNESS_CHECK_TIMEOUT, login=False, recover_stale=False)

    c = sub.add_parser("route", help="master: suggest an owner for every open ticket by model/roles/capabilities/cost")
    c.add_argument("--claim", action="store_true", help="hard-assign the ready ones (claims on their behalf)")
    c.add_argument("--redo", action="store_true", help="recompute tickets that already have a suggestion")
    c.add_argument("--only", nargs="*", help="restrict to these agents")
    c.add_argument("--shadow", action="store_true",
                   help="T-315: print rule vs learned/prior pick; do not assign")
    c.add_argument("--report", action="store_true",
                   help="with --shadow: agreement rate and realized turns")
    c.add_argument("--by", choices=["turns", "cost"], default="turns",
                   help="with --shadow: rank learned pick by turns then cost (default) or cost then turns")
    c.add_argument("--json", action="store_true",
                   help="with --shadow: print additive JSON (T-315/T-415 shape)")
    c.add_argument("--score", action="store_true",
                   help="with --shadow: retrospective shadow-vs-actual scorecard (read-only)")
    c.add_argument("--era-by-time", action="store_true",
                   help="with --shadow --score: fall back to wall-clock era when release_sha absent (prints warning)")
    c.add_argument("--write-scorecard", nargs="?", const="docs/turns-scorecard.md",
                   metavar="PATH",
                   help="with --shadow --score: also write markdown scorecard (default docs/turns-scorecard.md)")
    c.add_argument("--alive-within", type=int, default=90,
                   help="exclude seats with no heartbeat/here/run within N minutes (default 90)")
    c.add_argument("--apply", action="store_true",
                   help="unimplemented (T-315); exits non-zero")
    c.set_defaults(fn=cmd_route)

    c = sub.add_parser("connect", help="Atman product flow: CEO connect, or worker loop")
    c.add_argument("--ceo", action="store_true",
                   help="CEO path even on a blank board (catalog+usage → atman-<seat>)")
    c.add_argument("--worker", action="store_true",
                   help="worker claim loop (prints tickets next)")
    c.add_argument("--seat", default="ceo",
                   help="board identity suffix; join as atman-<seat> (default: ceo)")
    c.set_defaults(fn=cmd_connect)

    c = sub.add_parser("self", help="print which tickets.py is live (script path and install kind)")
    c.set_defaults(fn=cmd_self)

    c = sub.add_parser("pending", help="exit 0 if there is work for the agent (messages, held or ready ticket)")
    c.add_argument("--agent", default="")
    c.add_argument("--json", action="store_true")
    c.add_argument("--force", action="store_true", help="treat as actionable even without a wake gate (manual override)")
    c.set_defaults(fn=cmd_pending)

    c = sub.add_parser("remote", help="fenced bridge protocol: register | heartbeat | next | start | end | retry | release | status")
    rs = c.add_subparsers(dest="remote_cmd")
    x = rs.add_parser("register", help="acquire the one remote bridge lease")
    x.add_argument("--agent", required=True)
    x.add_argument("--bridge-id", required=True)
    x.add_argument("--ttl", type=int, default=REMOTE_LEASE_TTL)
    x.add_argument("--max-attempts", type=int, default=REMOTE_MAX_ATTEMPTS)
    x.add_argument("--replace", action="store_true", help="fence an online lease with the same bridge id")
    x.add_argument("--worktree", default="", help="remote execution working tree, for run receipts")
    x = rs.add_parser("heartbeat", help="renew an acquired bridge lease")
    x.add_argument("--agent", required=True); x.add_argument("--lease-id", required=True)
    x.add_argument("--fence", required=True, type=int)
    x = rs.add_parser("next", help="long-poll and atomically claim one wake")
    x.add_argument("--agent", required=True); x.add_argument("--lease-id", required=True)
    x.add_argument("--fence", required=True, type=int)
    x.add_argument("--wait", type=int, default=25, help="long-poll seconds, capped at 30")
    x.add_argument("--prompt-kind", default="", choices=("", "master", "cos"))
    x = rs.add_parser("start", help="record that the claimed remote model turn started")
    x.add_argument("--agent", required=True); x.add_argument("--lease-id", required=True)
    x.add_argument("--fence", required=True, type=int); x.add_argument("--claim-id", required=True)
    x = rs.add_parser("end", help="finish a remote turn and report measured usage")
    x.add_argument("--agent", required=True); x.add_argument("--lease-id", required=True)
    x.add_argument("--fence", required=True, type=int); x.add_argument("--claim-id", required=True)
    x.add_argument("--exit", type=int, required=True)
    x.add_argument("--input-tokens", type=int, default=None)
    x.add_argument("--output-tokens", type=int, default=None)
    x.add_argument("--cost-usd", type=float, default=None)
    x.add_argument("--reason", default="")
    x = rs.add_parser("retry", help="authorize retry after failure or uncertain lease loss")
    x.add_argument("--agent", required=True); x.add_argument("--lease-id", required=True)
    x.add_argument("--fence", required=True, type=int); x.add_argument("--claim-id", default="")
    x = rs.add_parser("release", help="release the bridge lease")
    x.add_argument("--agent", required=True); x.add_argument("--lease-id", required=True)
    x.add_argument("--fence", required=True, type=int)
    x = rs.add_parser("status", help="show public adapter state without its bearer lease")
    x.add_argument("--agent", required=True)
    c.set_defaults(fn=cmd_remote)

    c = sub.add_parser("prompt", help="print the standard worker (or --master) prompt for a headless run")
    c.add_argument("--agent", default="")
    c.add_argument("--master", action="store_true", help="the master/planner loop (or full master loop if no cos)")
    c.add_argument("--cos", action="store_true", help="the chief-of-staff loop: review, unblock, merge")
    c.add_argument("--extra", default="", help="extra instructions appended to the prompt")
    c.set_defaults(fn=cmd_prompt)

    c = sub.add_parser("stop-hook", help="Claude Code Stop hook: block the stop while board work remains")
    c.set_defaults(fn=cmd_stop_hook)

    c = sub.add_parser("objective", help="set/show/close the standing objective the master drives toward")
    c.add_argument("text", nargs="?", default="")
    c.add_argument("--set", dest="set_text", default="", metavar="TEXT",
                   help="same as the positional text: tickets objective --set \"<sentence>\"")
    c.add_argument("--exit", dest="exit_criterion", default=None, metavar="CRITERION",
                   help="measurable exit criterion (observable end state)")
    c.add_argument("--done", "--achieved", dest="done", default=None, metavar="EVIDENCE",
                   help="mark the objective achieved, with evidence")
    c.add_argument("--blocked", default=None, metavar="REASON", help="mark the objective blocked")
    c.add_argument("--replaced", default=None, metavar="REASON", help="mark the objective replaced")
    c.add_argument("--by", default="")
    c.set_defaults(fn=cmd_objective)

    c = sub.add_parser("drive", help="set the objective and spawn the master seat with a heartbeat")
    c.add_argument("text", nargs="?", default="")
    c.add_argument("--exit", dest="exit_criterion", default=None, metavar="CRITERION",
                   help="measurable exit criterion (observable end state)")
    c.add_argument("--by", "--as", dest="by", default="", help="agent name for the master seat (default TICKET_AGENT)")
    c.add_argument("--heartbeat", type=int, default=30, help="minutes between objective wake-ups")
    c.add_argument("--every", type=int, default=60, help="seconds between board polls")
    c.add_argument("--harness", "--tool", dest="tool", default="",
                   help="claude | codex | cursor | cursor+claude | custom:<cmd> (default: the registered harness)")
    c.add_argument("--cmd", dest="cmd_template", default="", help="shell template for a custom harness")
    c.add_argument("--model", default="")
    c.add_argument("--wake-mode", choices=WAKE_MODES, default=None)
    c.add_argument("--restart", action="store_true", help="stop an existing watcher for this seat first")
    c.set_defaults(fn=cmd_drive)

    c = sub.add_parser("watch", help="poll the board and launch a worker when there is work for the agent")
    c.add_argument("--agent", default="")
    c.add_argument("--every", type=int, default=60, help="seconds between polls")
    c.add_argument("--heartbeat", type=int, default=0,
                   help="master seat only: also wake every N minutes to drive the objective (0 = off)")
    c.add_argument("--exec", default="", help="command to run (default: headless claude with `tickets prompt`); "
                                              "{prompt_file} {cwd} {agent} are expanded per run")
    c.add_argument("--prompt-kind", dest="prompt_kind", default="", choices=("", "master", "cos"),
                   help="which prompt to write to {prompt_file} (default: the worker prompt)")
    c.add_argument("--cwd", default="", help="directory to run in (default: repo root; use the agent's worktree)")
    c.add_argument("--permission-mode", default="acceptEdits", help="for the default claude command")
    c.add_argument("--allowed-tools", default="", help='e.g. "Bash Edit Write Read"')
    c.add_argument("--max-runs", type=int, default=1,
                   help="model runs this session then stop (default 1; 0 = loop until --stop)")
    c.add_argument("--persist", action="store_true", help="loop until spawn --stop / SIGTERM (sets --max-runs 0)")
    c.add_argument("--force", action="store_true", help="run once even if wake gates are empty")
    c.add_argument("--run-timeout", type=int, default=90, help="minutes per run before it is killed (0 = none)")
    c.add_argument("--beat-every", type=int, default=0,
                   help="seconds between in-run heartbeats (0 = TICKETS_RUN_HEARTBEAT_SECS, default 30)")
    c.add_argument("--once", action="store_true", help="check once; exit 0 if work, 1 if not")
    c.add_argument("--dry-run", action="store_true")
    c.add_argument("--verbose", action="store_true")
    c.set_defaults(fn=cmd_watch)

    c = sub.add_parser("hook-run", help="(hook body) run one event under a baked agent identity")
    c.add_argument("--agent", required=True)
    c.add_argument("--event", required=True,
                   choices=("identity", "session-start", "inbox", "stop", "task-wake",
                            "agy-inbox", "agy-stop"))
    c.add_argument("--prompt-kind", default="", choices=("", "master", "cos"))
    c.set_defaults(fn=cmd_hook_run)

    c = sub.add_parser("codex-hook", help="(hook body) Codex SessionStart/UserPromptSubmit board context")
    c.add_argument("--agent", required=True)
    c.add_argument("--worktree", default="")
    c.set_defaults(fn=cmd_codex_hook)

    c = sub.add_parser("boot", help="every startup step: join, hooks, check-in, briefing (idempotent)")
    c.add_argument("--agent", default="")
    c.add_argument("--tool", default="", help="claude | cursor | codex | agy | gemini | devin | grok | remote")
    c.add_argument("--harness", default="", help="harness to register for this agent (see `tickets join --harness`)")
    c.add_argument("--cmd", dest="cmd_template", default="", help="shell template for a custom harness")
    c.add_argument("--roles", default=None)
    c.add_argument("--can", default=None)
    c.add_argument("--cost", choices=("low", "medium", "high"), default=None)
    c.add_argument("--model", default="")
    c.add_argument("--wake-mode", choices=WAKE_MODES, default=None)
    c.add_argument("--worktree", default="", help="codex: scope the hook to this worktree")
    c.add_argument("--settings", default="", help="claude: settings.json to write (default ~/.claude/settings.json)")
    c.add_argument("--hooks-file", default="", help="codex: hooks.json to write (default ~/.codex/hooks.json)")
    c.add_argument("--watch", action="store_true", help="continue into the watch loop")
    c.add_argument("--every", type=int, default=60)
    c.add_argument("--exec", default="")
    c.add_argument("--cwd", default="")
    c.add_argument("--run-timeout", type=int, default=90)
    c.set_defaults(fn=cmd_boot)

    c = sub.add_parser("spawn", help="start a persistent worker: register, worktree, detached watcher (model per agent)")
    c.add_argument("name", nargs="?", default="")
    c.add_argument("--model", default="", help="claude: opus | sonnet | haiku (or a full model id); codex: its model name")
    c.add_argument("--harness", default="",
                   help="claude | codex | cursor | cursor+claude | custom:<cmd> | <executable>; "
                        "default: whatever `tickets join` registered for this agent")
    c.add_argument("--tool", default="", help="original spelling of --harness")
    c.add_argument("--cmd", dest="cmd_template", default="",
                   help="shell template for a custom harness; placeholders {prompt_file} {cwd} {agent}")
    c.add_argument("--roles", default=None)
    c.add_argument("--can", default=None)
    c.add_argument("--cost", choices=("low", "medium", "high"), default=None)
    c.add_argument("--best-for", default="")
    c.add_argument("--wake-mode", choices=WAKE_MODES, default=None,
                   help="persist the seat's wake policy (master/CoS default continuous; workers task-only)")
    c.add_argument("--brief", default="", help="standing context for this worker")
    c.add_argument("--worktree", default="", help="default .worktrees/<name>")
    c.add_argument("--base", default="", help="branch/ref to create the worktree from (default main)")
    c.add_argument("--every", type=int, default=60)
    c.add_argument("--run-timeout", type=int, default=90)
    c.add_argument("--heartbeat", type=int, default=0,
                   help="with --master: also wake every N minutes to drive the objective (0 = off)")
    c.add_argument("--persist", action="store_true",
                   help="keep the watcher looping (default follows --wake-mode; continuous/scheduled persist)")
    c.add_argument("--max-runs", type=int, default=None,
                   help="passed to watch; task-only defaults 1; continuous/scheduled default 0; "
                        "pass 1 for a diagnostic one-shot")
    c.add_argument("--safe", action="store_true", help="worker confirms edits instead of running unattended")
    c.add_argument("--master", action="store_true",
                   help="spawn the board master/planner (scope, routing by complexity, escalations)")
    c.add_argument("--cos", action="store_true",
                   help="spawn the chief of staff (review, unblock, merge) under the current master; "
                        "persistent watcher by default (override with --max-runs 1)")
    c.add_argument("--exec", default="", help="override the worker command entirely")
    c.add_argument("--stop", action="store_true", help="ask the watcher to exit at its next poll")
    c.add_argument("--list", action="store_true")
    c.add_argument("--alias", default="",
                   help="stable role alias (ceo or cos) pointing at this unique runtime identity")
    c.add_argument("--transfer", action="store_true",
                   help="audited handover when this name's provider/session/runner identity changes; "
                        "strips auth, endpoint, session, ticket pointer, and limits; keeps message history")
    c.set_defaults(fn=cmd_spawn)

    c = sub.add_parser("ui", help="local command board: http://localhost:8765 (auto-refresh + composer)")
    c.add_argument("--port", type=int, default=8765)
    c.add_argument("--host", default="127.0.0.1")
    c.add_argument("--open", action="store_true", help="open it in the browser")
    c.add_argument("--json", action="store_true", help="print the snapshot instead of serving")
    c.set_defaults(fn=cmd_ui)

    c = sub.add_parser("quickstart", help="zero to a first ticket claimed by an agent, in one command")
    c.add_argument("--agent", help="register under this name (default: $TICKET_AGENT)")
    c.add_argument("--roles", default="backend", help="roles for that agent (default: backend)")
    c.add_argument("--with-agent", metavar="NAME", help="also print how to put a real worker on the board")
    c.add_argument("--board", help="initialise this board directory explicitly")
    c.add_argument("--remove", action="store_true", help="delete the sample tickets this created")
    c.set_defaults(fn=cmd_quickstart)

    c = sub.add_parser("guide", help="print the startup guide for claude / codex / cursor")
    c.set_defaults(fn=cmd_guide)

    c = sub.add_parser("brief", help="give an agent, role, or ticket context (shown on claim, in prompt, in boot)")
    c.add_argument("agent", nargs="?", default="")
    c.add_argument("text", nargs="?", default="")
    c.add_argument("--role", default="",
                   help="update role standing context at .tickets/briefs/roles/<role>.md (T-529 inject source)")
    c.add_argument("--ticket", default="", help="attach to a ticket instead (owner is messaged)")
    c.add_argument("--knowledge", dest="knowledge_id", default="",
                   help="attach only a knowledge:<id> reference to --ticket")
    c.add_argument("--file", default="", help="replace the agent or role brief from a file")
    c.add_argument("--show", action="store_true")
    c.add_argument("--by", default="")
    c.set_defaults(fn=cmd_brief)

    c = sub.add_parser("util", help="per-agent throughput, cycle time, load and sprint burn")
    c.add_argument("--hours", type=int, default=24)
    c.add_argument("--json", action="store_true")
    c.set_defaults(fn=cmd_util)

    c = sub.add_parser("dash", help="master dashboard, refreshing in place (Ctrl-C to leave)")
    c.add_argument("--every", type=int, default=10)
    c.add_argument("--messages", type=int, default=6)
    c.add_argument("--once", action="store_true")
    c.set_defaults(fn=cmd_dash)

    c = sub.add_parser("hooks", help="wire a tool to the board: claude | cursor | codex | agy | gemini | devin | grok | remote")
    c.add_argument("tool", choices=HOOK_TOOLS)
    c.add_argument("--agent", default="", help="required agent name baked into every generated hook command")
    c.add_argument("--worktree", default="", help="codex/cursor: scope hooks to this worktree")
    c.add_argument("--settings", default="", help="claude: settings.json path (default ./.claude/settings.json)")
    c.add_argument("--hooks-file", default="", help="codex: hooks.json path (default ~/.codex/hooks.json)")
    c.add_argument("--wrapper", default="", help="remote: identity-pinned wrapper path")
    c.add_argument("--prompt-kind", default="", choices=("", "master", "cos"),
                   help="remote: taskWake prompt type")
    c.add_argument("--no-stop", dest="stop", action="store_false", help="claude: do not install the Stop hook")
    c.add_argument("--rollback", action="store_true", help="restore exact files replaced by the latest install")
    c.add_argument("--force", action="store_true")
    c.set_defaults(fn=cmd_hooks, stop=True)

    c = sub.add_parser("here", help="check in: record my worktree, branch and ticket")
    c.add_argument("--owner", "-o")
    c.add_argument("--note", default="")
    c.set_defaults(fn=cmd_here)

    c = sub.add_parser("who", help="where every agent is working")
    c.add_argument("--no-liveness", action="store_true",
                   help="skip the transcript reads and show locations only")
    c.set_defaults(fn=cmd_who)

    c = sub.add_parser("msg", help="post to the board or a seat thread")
    c.add_argument("text")
    c.add_argument("--to", default="", help="seat / agent name, or omit for everyone")
    c.add_argument("--re", default="", help="ticket id this is about")
    c.add_argument("--task", action="store_true",
                   help="explicit task message: wakes every mode (ordinary DMs wake only continuous; ACKs never auto-wake)")
    c.add_argument("--owner", "-o")
    c.set_defaults(fn=cmd_msg)

    c = sub.add_parser("inbox", help="unread messages for me")
    c.add_argument("--all", action="store_true", help="full history")
    c.add_argument("--seat", default="",
                   help="only this agent's seat thread (same messages.jsonl; does not mark read)")
    c.add_argument("--limit", type=int, default=40)
    c.add_argument("--keep", action="store_true", help="do not mark as read")
    c.add_argument("--owner", "-o")
    c.set_defaults(fn=cmd_inbox)

    c = sub.add_parser("trajectories", aliases=["traj"],
                       help="the team trajectory log: query | export | backfill")
    c.add_argument("--ticket", "-t", default="", help="only this ticket")
    c.add_argument("--agent", "-a", default="", help="only this agent")
    c.add_argument("--kind", "-k", default="",
                   help="run_start,run_end,claim,update,review,done,reopen,block,msg,merge")
    c.add_argument("--since", default="", help="ISO timestamp, inclusive")
    c.add_argument("--until", default="", help="ISO timestamp, inclusive")
    c.add_argument("--limit", type=int, default=200, help="show the last N; 0 = all")
    c.add_argument("--summary", action="store_true",
                   help="per-ticket runs/turns/updates/messages/reopens")
    c.add_argument("--json", action="store_true")
    ts = c.add_subparsers(dest="traj_cmd")
    x = ts.add_parser("export", help="write the filtered events to a file")
    x.add_argument("--out", required=True, help="destination .jsonl")
    x = ts.add_parser("backfill",
                      help="synthesise claim/update/review/done from tickets already on the board")
    x.add_argument("--dry-run", action="store_true", dest="dry_run")
    c.set_defaults(fn=cmd_trajectories, traj_cmd="list", out="", dry_run=False)

    c = sub.add_parser("turns",
                       help="watch-run turns per ticket (T-312); --json is frozen for the optimizer")
    c.add_argument("--ticket", "-t", default="", help="only this ticket")
    c.add_argument("--agent", "-a", default="", help="only events from this agent")
    c.add_argument("--model", default="", help="only this model")
    c.add_argument("--epic", default="", help="only this epic")
    c.add_argument("--since", default="", help="ISO timestamp, inclusive")
    c.add_argument("--until", default="", help="ISO timestamp, inclusive")
    c.add_argument("--json", action="store_true", dest="json",
                   help="frozen shape: tickets[] + aggregates mean/median")
    c.set_defaults(fn=cmd_turns)

    c = sub.add_parser("review", help="submit finished work for the master to review + merge")
    c.add_argument("id")
    c.add_argument("--notes", "-n", default="", help="what to look at: paths, tests run, decisions")
    c.add_argument("--pr", default="", help="PR number or URL if you opened one")
    c.add_argument("--owner", "-o")
    c.add_argument("--artifact", default="", metavar="DIR", help='directory the DELIVERABLE lives in, when that is a different repo from the one you are running this command in; branch, sha and repo are all derived from that tree')
    c.add_argument("--force", action="store_true")
    c.set_defaults(fn=cmd_review)

    c = sub.add_parser("sync", help="agent: merge main into my branch now (do this before review)")
    c.add_argument("--artifact", default="", metavar="DIR",
                   help="tree the deliverable is in, when it is not this checkout")
    c.add_argument("--force", action="store_true")
    c.set_defaults(fn=cmd_sync)

    c = sub.add_parser("merge", help="master: integrate pinned review SHAs -> test -> FF main -> close by ancestry")
    c.add_argument("branches", nargs="*", help="branches to merge; default = branches in the review queue")
    c.add_argument("--artifact", default="", metavar="DIR",
                   help="repo to integrate in, when the deliverable does not live in the "
                        "board's own repo; only tickets pinned to THAT repo are eligible")
    c.add_argument("--push", action="store_true", help="push main to origin after fast-forward")
    c.add_argument("--no-test", action="store_true", help="merge even if tests fail (not recommended)")
    c.add_argument("--ours", action="append", default=[], metavar="BRANCH",
                   help="request -X ours for this branch (also requires --discard-code)")
    c.add_argument("--discard-code", action="store_true",
                   help="allow blanket -X ours; refused without this flag (T-079)")
    c.add_argument("--force-master", action="store_true",
                   help="break-glass: merge even if caller is not master.json owner")
    c.add_argument("--force", action="store_true")
    c.add_argument("--owner", "-o")
    c.set_defaults(fn=cmd_merge)

    c = sub.add_parser("limit", help="record that an agent hit a usage limit (or --clear)")
    c.add_argument("agent", nargs="?", default="")
    c.add_argument("--until", default="", help='e.g. "2026-09-06 14:00" or "in 5h"')
    c.add_argument("--note", default="")
    c.add_argument("--clear", action="store_true")
    c.set_defaults(fn=cmd_limit)

    c = sub.add_parser("limits", help="who is limited: records, silence, and per-agent live state")
    c.add_argument("--hours", type=int, default=24)
    c.add_argument("--raw-scan", action="store_true",
                   help="also print the old substring hit-count scan (not evidence; see the label)")
    c.add_argument("--verbose", action="store_true")
    c.set_defaults(fn=cmd_limits)

    c = sub.add_parser("status", help="set status: todo | in-progress | review | blocked | done")
    c.add_argument("id")
    c.add_argument("status")
    c.add_argument("--notes", "-n", default="")
    c.add_argument("--owner", "-o")
    c.add_argument("--pr", default="")
    c.add_argument("--artifact", default="", metavar="DIR", help='directory the DELIVERABLE lives in, when that is a different repo from the one you are running this command in; branch, sha and repo are all derived from that tree')
    c.add_argument("--force", action="store_true")
    c.set_defaults(fn=cmd_status)

    c = sub.add_parser("update", help="progress update on a ticket you hold (alias of note)")
    c.add_argument("id")
    c.add_argument("text")
    c.add_argument("--by", default="")
    c.set_defaults(fn=cmd_note)

    c = sub.add_parser("dep", help="rewire an existing ticket's dependencies")
    c.add_argument("id")
    c.add_argument("--after", "-a", default="", help="also wait for these")
    c.add_argument("--drop", "-x", default="", help="stop waiting for these")
    c.set_defaults(fn=cmd_dep)

    c = sub.add_parser("graph", help="show the dependency graph with statuses")
    c.set_defaults(fn=cmd_graph)

    c = sub.add_parser("map", help="sprint -> epic -> tickets, with deps; the whole board")
    c.add_argument("--all", action="store_true", help="include done tickets and closed sprints")
    c.set_defaults(fn=cmd_map)

    c = sub.add_parser("plan", help="bulk-create tickets from JSON on stdin (default lane=capture until sounded)")
    c.set_defaults(fn=cmd_plan)

    c = sub.add_parser("capture", help="capture a thought as lane=capture (not claimable until tickets sound)")
    c.add_argument("thought", nargs="?", default="", help="the raw idea; no decisions yet")
    c.add_argument("--area", default="", help="optional prefix for the title slug")
    c.add_argument("--from-msg", dest="from_msg", default="", help="board message id to turn into a capture")
    c.add_argument("--role", "-r", default="")
    c.add_argument("--priority", "-p", type=int, default=2)
    c.set_defaults(fn=cmd_capture)

    c = sub.add_parser("sound", help="turn a capture into an implementable ticket (cause/change/proof/deps)")
    c.add_argument("id")
    c.add_argument("--notes", "-n", default="",
                   help='cause=...; change=...; proof=...; deps=none; questions=')
    c.set_defaults(fn=cmd_sound)

    c = sub.add_parser("dispatch", help="CoS: reserve one ready ticket for one seat; spawn if needed")
    c.add_argument("id")
    c.add_argument("--to", required=True, help="seat that will tickets next")
    c.add_argument("--harness", default="cursor", help="default cursor; FAIL usage rows are refused")
    c.add_argument("--cmd", dest="cmd_template", default="")
    c.add_argument("--exec", default="")
    c.add_argument("--worktree", default="")
    c.set_defaults(fn=cmd_dispatch)

    c = sub.add_parser("pr-sync", help="IN REVIEW + PR: merged+ancestor → ready to close (does not tickets done)")
    c.add_argument("id", nargs="?", default="")
    c.set_defaults(fn=cmd_pr_sync)

    c = sub.add_parser("schedule", help="cron recurring wake for a named seat (persist poke; no product spawn)")
    c.add_argument("seat", nargs="?", default="", help="seat to wake")
    c.add_argument("--cron", default="", help='5-field UTC cron, e.g. "*/15 * * * *"')
    c.add_argument("--every", default="", help="interval: 30s, 15m, 1h")
    c.add_argument("--list", action="store_true")
    c.add_argument("--remove", action="store_true")
    c.add_argument("--due", action="store_true", help="fire due entries (crontab runs this)")
    c.add_argument("--install", action="store_true", help="install crontab line for schedule --due")
    c.add_argument("--uninstall", action="store_true")
    c.add_argument("--re", default="", help="ticket id on the wake message")
    c.set_defaults(fn=cmd_schedule)

    c = sub.add_parser("plan-status", help="capture / ready / waiting-on-merge / blocked-HOLD-discarded")
    c.add_argument("--write-master", action="store_true", help="write a Plan section into MASTER.md")
    c.set_defaults(fn=cmd_plan_status)

    c = sub.add_parser("discard", help="abandon a ticket (lane=discarded, not done)")
    c.add_argument("id")
    c.add_argument("--reason", required=True)
    c.set_defaults(fn=cmd_discard)

    c = sub.add_parser("retro", help="from done+discarded, file a capture proposing a brief/skill edit")
    c.add_argument("--since", default="", help="7d, 24h, or ISO timestamp")
    c.set_defaults(fn=cmd_retro)

    c = sub.add_parser("list", help="list tickets")
    c.add_argument("--status", choices=STATUSES)
    c.add_argument("--role")
    c.add_argument("--owner")
    c.add_argument("--json", action="store_true")
    c.set_defaults(fn=cmd_list)

    c = sub.add_parser("board", help="compact board summary (used by the hook)")
    c.add_argument("--all", action="store_true", help="include done tickets")
    c.add_argument("--quiet", "-q", action="store_true", help="omit the protocol hint")
    c.set_defaults(fn=cmd_board)

    c = sub.add_parser("show", help="show one ticket in full")
    c.add_argument("id")
    c.add_argument("--json", action="store_true")
    c.set_defaults(fn=cmd_show)

    c = sub.add_parser("next", help="atomically claim the next available ticket")
    c.add_argument("--role", "-r", help="role or comma-separated roles")
    c.add_argument("--owner", "-o")
    c.add_argument("--another", action="store_true", help="claim even though I already hold one")
    c.add_argument("--steal", default="", metavar="ID",
                   help="claim this ticket even if reserved_for someone else")
    c.set_defaults(fn=cmd_next)

    c = sub.add_parser("claim", help="atomically claim a specific ticket")
    c.add_argument("id")
    c.add_argument("--owner", "-o")
    c.set_defaults(fn=cmd_claim)

    c = sub.add_parser("done", help="mark a ticket done")
    c.add_argument("id")
    c.add_argument("--notes", "-n", default="", help="handoff text for dependent tickets")
    c.add_argument("--no-notes", action="store_true", help="allow empty handoff")
    c.add_argument("--artifact", default="", metavar="DIR", help='directory the DELIVERABLE lives in, when that is a different repo from the one you are running this command in; branch, sha and repo are all derived from that tree')
    c.add_argument("--force", action="store_true", help="skip the branch/clean-tree rule")
    c.set_defaults(fn=cmd_done)

    c = sub.add_parser("repin", help="correct a review pin to name the repo the artifact is in")
    c.add_argument("id")
    c.add_argument("--artifact", required=True, metavar="DIR",
                   help="checkout the deliverable is in; its CURRENT HEAD becomes the pin")
    c.add_argument("--notes", "-n", default="", help="why the pin was wrong")
    c.add_argument("--owner", "-o")
    c.add_argument("--force", action="store_true", help="allow a dirty artifact tree")
    c.set_defaults(fn=cmd_repin)

    c = sub.add_parser("block", help="mark a ticket blocked")
    c.add_argument("id")
    c.add_argument("--reason", "--notes", "-n", dest="reason", required=True,
                    help="why it's blocked (--notes accepted as an alias -- same shape as done/review)")
    c.set_defaults(fn=cmd_block)

    c = sub.add_parser("note", help="add a note to a ticket")
    c.add_argument("id")
    c.add_argument("text")
    c.add_argument("--by", default="")
    c.set_defaults(fn=cmd_note)

    c = sub.add_parser("reopen", help="release a claimed ticket back to open")
    c.add_argument("id")
    c.add_argument("--notes", "-n", default="",
                   help="why it's being reopened (required for IN REVIEW / review_at)")
    c.add_argument("--by", default="", help="who is reopening it, if not the acting agent")
    c.set_defaults(fn=cmd_reopen)

    c = sub.add_parser(
        "clear",
        help="FIXTURE ONLY: delete T-*.json on a .fixture-board (never live)",
    )
    c.add_argument(
        "--yes",
        action="store_true",
        help="required confirmation for clearing a fixture board",
    )
    c.set_defaults(fn=cmd_clear)

    c = sub.add_parser(
        "board-backup",
        help="backup tickets/roles/coordination (fixture board by default)",
    )
    c.add_argument("--out", required=True, help="output .tgz path")
    c.add_argument(
        "--i-understand-live",
        action="store_true",
        help="allow backing up a non-fixture board (still redacts note text)",
    )
    c.set_defaults(fn=cmd_board_backup)

    c = sub.add_parser(
        "board-restore",
        help="restore a backup into a fixture destination board",
    )
    c.add_argument("--archive", required=True, help="input .tgz from board-backup")
    c.add_argument("--dest", required=True, help="destination board directory")
    c.add_argument(
        "--i-understand-live",
        action="store_true",
        help="allow restore into a non-fixture destination (discouraged)",
    )
    c.set_defaults(fn=cmd_board_restore)

    c = sub.add_parser("where", help="print the board directory")
    c.set_defaults(fn=cmd_where)

    c = sub.add_parser("context", help="print the shared briefing file")
    c.set_defaults(fn=cmd_context)

    c = sub.add_parser("knowledge", aliases=["kb"],
                       help="query or author the separate repo-backed knowledge graph")
    c.add_argument("--tag", default="", help="filter list by tag")
    c.add_argument("--type", dest="node_type", default="", help="filter list by node type")
    c.add_argument("--stale", action="store_true", help="list only stale facts")
    c.add_argument("--json", action="store_true")
    ks = c.add_subparsers(dest="knowledge_cmd")
    x = ks.add_parser("list", help="list current graph facts")
    x.add_argument("--tag", default="", help="filter list by tag")
    x.add_argument("--type", dest="node_type", default="", help="filter by node type")
    x.add_argument("--stale", action="store_true", help="list only stale facts")
    x.add_argument("--json", action="store_true")
    x = ks.add_parser("show", help="print one node or edge with provenance")
    x.add_argument("slug")
    x = ks.add_parser("query", help="return a compact task-relevant subgraph")
    x.add_argument("query", nargs="?", default="")
    x.add_argument("--agent", default="", help="include this seat's role/harness/capabilities")
    x.add_argument("--ticket", default="", help="include one ticket as query input; ticket is not the store")
    x.add_argument("--role", action="append", default=[])
    x.add_argument("--cap", action="append", default=[])
    x.add_argument("--harness", default="")
    x.add_argument("--max-chars", type=int, default=None)
    x.add_argument("--max-nodes", type=int, default=12)
    x.add_argument("--json", action="store_true")
    x = ks.add_parser("add", help="add one validated node/edge JSON file")
    x.add_argument("file")
    x = ks.add_parser("update", help="replace one record and increment node revision")
    x.add_argument("file")
    ks.add_parser("validate", help="validate schema and graph references")
    c.set_defaults(fn=cmd_knowledge, slug="")

    c = sub.add_parser("mine", help="list tickets claimed by this agent")
    c.add_argument("--owner", "-o")
    c.set_defaults(fn=cmd_mine)

    c = sub.add_parser("init", help="install the board protocol into this project")
    c.add_argument("--track", action="store_true", help="commit the board to git instead of gitignoring it")
    c.add_argument("--board", metavar="DIR", help="write the board HERE instead of the "
                   "directory init resolves from cwd -- the explicit escape hatch for "
                   "the case init would otherwise refuse (T-263)")
    c.set_defaults(fn=cmd_init)

    # Optional extension (identity / role / handover / pulse) installed beside
    # this file by tools/tickets/install.py; the core CLI must not depend on it.
    try:
        sys.path.insert(0, os.path.dirname(os.path.realpath(__file__)))
        from ticket_coordination import register
    except ImportError:
        register = None
    if register:
        register(sub, globals())

    a = p.parse_args()
    if status.startswith("tickets DRIFTED") or status.startswith("tickets INVALID"):
        print("WARNING: %s -- see 'tickets --version'" % status, file=sys.stderr)
    if not a.cmd:
        p.print_help()
        return
    if a.cmd == "self":
        cmd_self(a, None)
        return
    discover = a.cmd != "board"
    board = board_dir(discover_children=discover)
    if a.cmd not in (
        "create",
        "plan",
        "where",
        "init",
        "quickstart",
        "join",
        "epic",
        "sprint",
        "master",
        "connect",
        "self",
        "board-restore",
        "knowledge",
        "kb",
    ):
        if not os.path.isdir(board):
            if a.cmd in ("board", "stop-hook"):
                return
            sys.exit("no board at %s (create a ticket first)" % board)
    # board-restore uses --dest, not the discovered board
    if a.cmd == "board-restore":
        a.fn(a, board)
        return
    a.fn(a, board)


if __name__ == "__main__":
    main()
