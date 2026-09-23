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
  the only live .tickets/ in a child directory (so `atm next` from a
  parent folder like Downloads still finds the project)
  cwd/.tickets

`atm board` does not scan children — SessionStart hooks stay silent in
folders that are not the project. `atm next` / `show` / `done` do.

Agent identity for a single command comes from $TICKET_AGENT (set it per tool:
claude, codex, cursor), or $TICKET_SEAT for a run a supervisor deliberately
launched. Session-scoped surfaces (`board`'s "you:" line, `msg`/`inbox` with
no --owner) resolve through session_seat(): explicit --owner, then
$TICKET_SEAT, then this session's join record, then $TICKET_AGENT.
Default roles for those names can be overridden by .tickets/roles.json.
"""

import argparse
import errno
import glob
import hashlib
import importlib.util
import json
import os
import sys
from datetime import datetime, timezone

try:
    from . import trajectories as _traj
except ImportError:  # run as a plain script path, not as a package module
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    import trajectories as _traj

try:
    from . import review_verdict as _rv
except ImportError:
    import review_verdict as _rv


def _work_view():
    """Package-safe Work view import (relative first, then sibling src)."""
    try:
        from . import work_view as m
        return m
    except ImportError:
        src = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
        if src not in sys.path:
            sys.path.insert(0, src)
        from ticket_board import work_view as m
        return m


def _worktree_gc():
    """T-946 automated worktree cleanup + atm gc sweep."""
    try:
        from . import worktree_gc as m
        return m
    except ImportError:
        src = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
        if src not in sys.path:
            sys.path.insert(0, src)
        from ticket_board import worktree_gc as m
        return m


def _gc_hooks():
    return _worktree_gc().CliHooks(_GCApi())


def _gc_probes():
    gc = _worktree_gc()
    mode = (os.environ.get("TICKETS_GC_OPEN_PRS") or "").strip()
    gh_fn = None
    if mode == "none":
        gh_fn = lambda branch, cwd: []
    elif mode:
        gh_fn = lambda branch, cwd: [{"number": 1, "url": mode}]
    extra = [p for p in [(os.environ.get("TICKETS_GC_RUNTIME_LINK") or "").strip()] if p]
    origin = (os.environ.get("TICKETS_GC_ORIGIN_REF") or "").strip() or None
    return gc.Probes(gh_fn=gh_fn, runtime_extra=extra, origin_ref=origin)


class _GCApi:
    """Minimal surface worktree_gc.CliHooks needs from this module."""

    load_all = staticmethod(lambda board: load_all(board))
    load = staticmethod(lambda board, tid: load(board, tid))
    save = staticmethod(lambda board, t: save(board, t))
    create = staticmethod(lambda board, title, body="", role="", deps=None,
                          priority=3, epic="", sprint="", needs=None:
                          create(board, title, body, role, deps, priority,
                                 epic, sprint, needs))
    now = staticmethod(lambda: now())
    whoami = staticmethod(lambda: whoami())
    current_master = staticmethod(lambda board: current_master(board))


def _is_automated_cleanup(t):
    """T-946 x T-1031: only cleanup_worktree is exempt from the accept gate."""
    return ((t.get("kind") or "").strip() == "automated"
            and (t.get("automated") or {}).get("action") == "cleanup_worktree")

STATUSES = ("open", "claimed", "review", "blocked", "done")
LABEL = {"open": "TO DO", "claimed": "IN PROGRESS", "review": "IN REVIEW",
         "blocked": "BLOCKED", "done": "DONE"}
# words agents may type for `atm status <id> <word>`
STATUS_WORDS = {
    "todo": "open", "to-do": "open", "open": "open", "unassigned": "open", "backlog": "open",
    "in-progress": "claimed", "inprogress": "claimed", "progress": "claimed", "wip": "claimed",
    "doing": "claimed", "claimed": "claimed", "started": "claimed",
    "review": "review", "in-review": "review", "pr": "review", "ready": "review", "submitted": "review",
    "blocked": "blocked", "stuck": "blocked",
    "done": "done", "merged": "done", "closed": "done", "complete": "done",
}
UPDATE_EVERY_MIN = 45  # agents must post `atm update` at least this often

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


PRIMARY_BOARD_MARKER = ".primary"


def _atman_config_path():
    """Where the machine-level "which repo uses which shared board" map lives.
    See tickets.py's copy of this function for the full rationale (T-959);
    kept identical across both entry points so a fix ported to one does not
    leave the other behaving differently (T-243)."""
    override = os.environ.get("ATMAN_BOARD_CONFIG")
    if override:
        return os.path.abspath(os.path.expanduser(override))
    return os.path.expanduser("~/.config/atman/board.json")


def _configured_shared_board(repo_root):
    if not repo_root:
        return None
    try:
        with open(_atman_config_path(), encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError):
        return None
    boards = data.get("boards") if isinstance(data, dict) else None
    if not isinstance(boards, dict):
        return None
    real_root = os.path.realpath(repo_root)
    for key, val in boards.items():
        if not val:
            continue
        try:
            if os.path.realpath(os.path.expanduser(key)) == real_root:
                return os.path.abspath(os.path.expanduser(val))
        except (OSError, TypeError):
            continue
    return None


def _is_marked_primary(path):
    return os.path.isfile(os.path.join(path, PRIMARY_BOARD_MARKER))


def _board_has_content(path):
    if _live_board(path):
        return True
    if not os.path.isdir(path):
        return False
    for name in ("messages.jsonl", "workforce.json", "roles.json"):
        p = os.path.join(path, name)
        if os.path.isfile(p) and os.path.getsize(p) > 0:
            return True
    agents_dir = os.path.join(path, "agents")
    if os.path.isdir(agents_dir):
        try:
            if any(n.endswith(".json") for n in os.listdir(agents_dir)):
                return True
        except OSError:
            pass
    return False


def _shadow_board_refusal(candidate, configured):
    real_candidate = os.path.realpath(candidate)
    sys.exit(
        "REFUSING BOARD %r: this repo has a configured shared board (%s) that "
        "disagrees with the local .tickets found here, and the local one is "
        "NOT empty and NOT marked primary -- resolving to it risks stranding "
        "real data (messages, agent registrations) on a board nothing else "
        "reads, the T-959 shape. Pick one:\n"
        "  * use the shared board:      export TICKETS_DIR=%s\n"
        "  * inspect what is here:      atm doctor\n"
        "  * keep this repo's own board on purpose:\n"
        "                                atm board-mark-primary\n"
        "  * archive this board aside (never deletes):\n"
        "                                atm board-archive-shadow %s --yes\n"
        % (real_candidate, configured, configured, real_candidate)
    )


def _apply_shared_board_config(candidate):
    if os.environ.get("TICKETS_DIR"):
        return candidate
    root = _repo_root()
    configured = _configured_shared_board(root)
    if not configured:
        return candidate
    configured = os.path.realpath(configured)
    real_candidate = os.path.realpath(candidate)
    if configured == real_candidate:
        return candidate
    if _is_marked_primary(candidate):
        return candidate
    if not _board_has_content(candidate):
        sys.stderr.write(
            "atm: cwd .tickets is empty -- using this repo's configured "
            "shared board %s\n" % configured
        )
        return configured
    _shadow_board_refusal(candidate, configured)


def _repo_root():
    """Root of the MAIN worktree, so every linked worktree shares one board."""
    import subprocess
    try:
        out = subprocess.run(["git", "rev-parse", "--git-common-dir"],
                             capture_output=True, text=True, timeout=5)
    except (OSError, subprocess.TimeoutExpired):
        return None
    if out.returncode != 0:
        return None
    common = os.path.abspath(out.stdout.strip())
    if os.path.basename(common) == ".git":
        return os.path.dirname(common)
    return None  # bare repo or unusual layout


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
        "    that is actually in effect: atm init --board %s\n"
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
            PYTEST_CURRENT_TEST="fake::test (call)" TMPDIR=$HOME/Downloads \
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
    configured = _configured_shared_board(root) if root else None
    if configured and os.path.realpath(configured) == board:
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
    result = _apply_shared_board_config(result)
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
    """Resolve who a SINGLE COMMAND acts as. Never reads the board."""
    if explicit:
        return explicit
    return (os.environ.get("TICKET_SEAT")
            or os.environ.get("TICKET_AGENT")
            or "agent-%d" % os.getpid())


IDENTITY_FILE = ".agent-identity"
IDENTITY_DIR = ".identities"
SESSION_ID_VARS = (
    "TICKET_SESSION_ID",
    "CLAUDE_CODE_SESSION_ID",
    "CODEX_SESSION_ID",
    "CURSOR_SESSION_ID",
    "TERM_SESSION_ID",
)


def agent_session_key():
    for var in SESSION_ID_VARS:
        val = (os.environ.get(var) or "").strip()
        if val:
            return hashlib.sha256(val.encode("utf-8")).hexdigest()[:16]
    return None


def _identity_path(board, key=None):
    if key:
        return os.path.join(board, IDENTITY_DIR, key)
    return os.path.join(board, IDENTITY_FILE)


def _read_identity_file(path):
    try:
        with open(path) as f:
            return (f.read() or "").strip() or None
    except (OSError, IOError):
        return None


def read_identity(board):
    key = agent_session_key()
    if key:
        return _read_identity_file(_identity_path(board, key))
    return _read_identity_file(_identity_path(board))


def write_identity(board, name):
    key = agent_session_key()
    path = _identity_path(board, key)
    d = os.path.dirname(path)
    if d and not os.path.isdir(d):
        os.makedirs(d, exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        f.write((name or "").strip() + "\n")
    os.replace(tmp, path)


def identity_resolution(board, explicit=None):
    """(seat, why) matching session_seat(). Must match tickets.py."""
    if explicit:
        return explicit, "explicit --owner"
    seat = (os.environ.get("TICKET_SEAT") or "").strip()
    if seat:
        return seat, "TICKET_SEAT (supervisor assignment)"
    keyed = bool(agent_session_key())
    recorded = None
    if board:
        try:
            recorded = read_identity(board)
        except Exception:
            recorded = None
    if keyed and recorded:
        return recorded, "session-keyed join record"
    env_agent = (os.environ.get("TICKET_AGENT") or "").strip()
    if env_agent:
        return env_agent, "TICKET_AGENT"
    if recorded:
        return recorded, "legacy flat identity file"
    return "agent-%d" % os.getpid(), "pid fallback"


def _join_binds_this_session(board, owner, on_behalf=False):
    """True when this join may write the caller's session-keyed identity.

    Must match tickets.py:_join_binds_this_session (T-954 / T-839).
    """
    if on_behalf:
        return False
    owner = (owner or "").strip()
    caller = whoami()
    if owner and owner == caller:
        return True
    keyed = bool(agent_session_key())
    recorded = None
    if board and keyed:
        try:
            recorded = read_identity(board)
        except Exception:
            recorded = None
    if recorded:
        return False
    if caller and not caller.startswith("agent-") and _agent_rec(board, caller):
        return False
    return True


def seat_confirmed(board):
    try:
        if os.environ.get("TICKET_SEAT"):
            return True
        if agent_session_key():
            return bool(read_identity(board))
        return bool(read_identity(board) or os.environ.get("TICKET_AGENT"))
    except Exception:
        return False


def session_seat(board, explicit=None):
    """Who THIS SESSION is.

    explicit > TICKET_SEAT > a SESSION-KEYED recorded join > TICKET_AGENT >
    the flat legacy recorded identity > pid. A recorded identity outranks the
    ambient TICKET_AGENT only when it is keyed to THIS session; the flat
    legacy file belongs to whichever agent joined last on this machine, so an
    explicit TICKET_AGENT beats it. Must match tickets.py:session_seat().
    """
    if explicit:
        return explicit
    seat = (os.environ.get("TICKET_SEAT") or "").strip()
    if seat:
        return seat
    keyed = bool(agent_session_key())
    recorded = None
    if board:
        try:
            recorded = read_identity(board)
        except Exception:
            recorded = None
    if keyed and recorded:
        return recorded
    env_agent = (os.environ.get("TICKET_AGENT") or "").strip()
    if env_agent:
        return env_agent
    if recorded:
        return recorded
    return "agent-%d" % os.getpid()


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


def _recovery():
    """Optional coordination extension: structured handoff + ownership lease."""
    try:
        from . import ticket_coordination as tc
        return tc
    except ImportError:
        try:
            import ticket_coordination as tc
            return tc
        except ImportError:
            return None


def _lease_harness(board, owner):
    try:
        return (load_workforce(board).get(owner) or {}).get("harness") or ""
    except Exception:
        return ""


def save(board, t, expected_generation=None):
    t["updated"] = now()
    path = ticket_path(board, t["id"])
    tc = _recovery()
    lock = tc.ticket_mutation_lock(board, t["id"]) if tc is not None else None

    def _publish():
        # Every active write (including update and owner transfers) shares the
        # same dependency gate; command-specific checks are only early errors.
        current = load(board, t["id"]) if os.path.isfile(path) else {}
        # The automated worktree-cleanup node is exempt: it hands no work to
        # a seat and its own checks escalate instead of deleting unmerged,
        # dirty or in-use work (see _run_cleanup_nodes).
        if (not _is_automated_cleanup(t)
                and (t.get("status") == "claimed"
                     or (t.get("owner") and t.get("owner") != current.get("owner")))):
            _refuse_unreleased_deps(t, load_all(board))
        if tc is not None and os.path.isfile(path):
            try:
                with open(path) as f:
                    current = json.load(f)
            except (ValueError, IOError):
                current = None
            if current:
                err = tc.generation_publish_error(current, t, expected_generation)
                if err:
                    sys.exit(err)
        tmp = path + ".tmp"
        with open(tmp, "w") as f:
            json.dump(t, f, indent=2)
        if tc is not None and os.path.isfile(path):
            try:
                with open(path) as f:
                    current = json.load(f)
            except (ValueError, IOError):
                current = None
            if current:
                err = tc.generation_publish_error(current, t, expected_generation)
                if err:
                    try:
                        os.unlink(tmp)
                    except OSError:
                        pass
                    sys.exit(err)
        os.replace(tmp, path)  # atomic
        return t

    if lock is None:
        result = _publish()
    else:
        with lock:
            result = _publish()
    # Run after the parent mutation lock: child recovery takes its own lock.
    # This covers accept, merge records, overrides and done after accept.
    if _work_view().dep_released(t):
        _reopen_unverified_successors(board, t["id"])
    return result


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


def master_state_path(board):
    return os.path.join(board, "master.json")


def current_master(board):
    try:
        with open(master_state_path(board)) as f:
            return json.load(f)
    except (IOError, ValueError):
        return None


def hours_since(stamp):
    try:
        d = datetime.strptime(stamp, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
        return (datetime.now(timezone.utc) - d).total_seconds() / 3600.0
    except (ValueError, TypeError):
        return 0.0


# --------------------------------------------------------------------------
# git awareness (agents must work on their own tree and commit)
# --------------------------------------------------------------------------

def git(*args):
    import subprocess
    try:
        out = subprocess.run(["git"] + list(args), capture_output=True, text=True, timeout=10)
    except (OSError, subprocess.TimeoutExpired):
        return None
    if out.returncode != 0:
        return None
    return out.stdout.strip()


def git_state():
    """Branch, short sha, dirty-file count, and whether cwd is the main worktree."""
    top = git("rev-parse", "--show-toplevel")
    if not top:
        return None
    branch = git("rev-parse", "--abbrev-ref", "HEAD") or "?"
    sha = git("rev-parse", "--short", "HEAD") or "?"
    sha_full = git("rev-parse", "HEAD") or ""
    dirty = git("status", "--porcelain")
    common = git("rev-parse", "--git-common-dir") or ""
    gitdir = git("rev-parse", "--git-dir") or ""
    is_main_tree = os.path.abspath(os.path.join(top, common)) == os.path.abspath(os.path.join(top, gitdir))
    return {
        "top": top,
        "branch": branch,
        "sha": sha,
        "sha_full": sha_full,
        "dirty": len(dirty.splitlines()) if dirty else 0,
        "main_tree": is_main_tree,
    }


def agents_dir(board):
    return os.path.join(board, "agents")


try:
    from .agent_checkin import checkin  # canonical; no root tickets.py
except ImportError:  # python src/ticket_board/cli.py
    from agent_checkin import checkin


def _clear_agent_ticket(board, agent, tid):
    """T-437: drop a stale ticket= bind on a previous owner's agent record."""
    if not agent or not tid:
        return
    path = os.path.join(agents_dir(board), agent + ".json")
    try:
        with open(path) as f:
            rec = json.load(f)
    except (IOError, ValueError):
        return
    if not isinstance(rec, dict) or rec.get("ticket") != tid:
        return
    rec["ticket"] = ""
    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        json.dump(rec, f, indent=2)
    os.replace(tmp, path)


def _drop_unowned_agent_ticket(board, owner):
    """T-439: drop ticket= when the live owner of that id is not me. Keep cwd/branch/sha.

    Also drop a leftover review bind when I already hold a different claimed ticket.
    """
    if not owner:
        return
    path = os.path.join(agents_dir(board), owner + ".json")
    try:
        with open(path) as f:
            rec = json.load(f)
    except (IOError, ValueError):
        return
    if not isinstance(rec, dict):
        return
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
    if not agent or not tid:
        return
    path = os.path.join(agents_dir(board), agent + ".json")
    try:
        with open(path) as f:
            rec = json.load(f)
    except (IOError, ValueError):
        rec = None
    if isinstance(rec, dict):
        rec["ticket"] = tid
        tmp = path + ".tmp"
        with open(tmp, "w") as f:
            json.dump(rec, f, indent=2)
        os.replace(tmp, path)
        return
    checkin(board, agent, tid)


def _current_ticket(board, owner):
    for t in load_all(board):
        if t["status"] == "claimed" and t.get("owner") == owner:
            return t["id"]
    return ""


def _here_ticket(board, owner):
    """Ticket id for atm here (T-543 / T-551). Two-copy of tickets.py."""
    claimed = [t["id"] for t in load_all(board)
               if t.get("status") == "claimed" and t.get("owner") == owner]
    if claimed:
        return claimed[0]
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


# --------------------------------------------------------------------------
# trajectory instrumentation (T-311)
#
# The writers live in trajectories.py so this entry point and the root
# tickets.py agree on the event shape; see that module's docstring for why the
# root script cannot simply import it, and
# tests/test_trajectories_entrypoints.py for what fails when the two drift.
# --------------------------------------------------------------------------

def _traj_safe(fn):
    """Instrumentation must never fail the command it observes. Every call
    site below sits on a command's success path, so a raised exception would
    abort work that has already happened and been saved."""
    try:
        return fn()
    except Exception:
        return None


def _traj_git():
    """worktree/branch/sha for an event, or {} when git cannot answer.

    This entry point's git_state() fills branch and sha with "?" placeholders
    where the root script returns None instead (T-259). A "?" written into the
    log reads back as a real branch name, so it is dropped rather than
    recorded -- a field the writer does not know is omitted, never defaulted.
    """
    g = _traj_safe(git_state)
    if not g:
        return {}
    out = {}
    for dst, src in (("worktree", "top"), ("branch", "branch"), ("sha", "sha")):
        v = g.get(src) or ""
        if v and v != "?":
            out[dst] = v
    return out


def traj_event(board, kind, agent="", ticket=None, **fields):
    """Append one trajectory event, best-effort."""
    return _traj_safe(
        lambda: _traj.event(board, kind, agent=agent, ticket=ticket, **fields))


def _traj_round3(x):
    return None if x is None else round(x, 3)


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
    tickets = load_all(board)
    reason = _work_view().refuse_unreleased_reason(t, tickets)
    if reason:
        os.unlink(lock)
        sys.exit(reason)
    prev_owner = t.get("owner") or ""
    t["status"] = "claimed"
    t["owner"] = owner
    t["claimed_at"] = now()
    t["done_at"] = ""
    tc = _recovery()
    if tc is not None:
        tc.issue_owner_lease(t, owner, harness=_lease_harness(board, owner),
                             reason="claim", previous_owner=prev_owner)
    got = save(board, t)
    # Written here, not in cmd_next/cmd_claim: this is the single point where a
    # claim actually succeeds, so no future caller can add a claim path that
    # silently produces no trajectory.
    traj_event(board, "claim", agent=owner, ticket=got,
               state_before="open", state_after="claimed", **_traj_git())
    if prev_owner and prev_owner != owner:
        _clear_agent_ticket(board, prev_owner, tid)
    return got


def _try_lock_ticket_excl(board, tid, owner):
    """Acquire the O_EXCL claim lock. Returns lock path, or None if taken."""
    lock = os.path.join(board, tid + ".lock")
    try:
        fd = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except OSError as e:
        if e.errno == errno.EEXIST:
            return None
        raise
    os.write(fd, owner.encode())
    os.close(fd)
    return lock


def _release_ticket_excl(lock):
    if not lock:
        return
    try:
        os.unlink(lock)
    except OSError:
        pass


def _held_claimed(board, owner, except_id=None):
    """Claimed tickets currently held by owner (T-979 one-active-hold)."""
    if not owner:
        return []
    return [t for t in load_all(board)
            if t.get("status") == "claimed"
            and t.get("owner") == owner
            and t.get("id") != except_id]


def _already_hold_msg(held):
    ids = ", ".join(t["id"] for t in held)
    return ("you already hold %s -- finish it (atm review/block/reopen) before claiming "
            "more, or pass --another if you really want to work two in parallel." % ids)


class _OwnerHoldLock:
    """Same path as agent_checkin._AgentLock so root and package serialize together."""

    def __init__(self, board, owner):
        os.makedirs(os.path.join(board, "agents"), exist_ok=True)
        self.path = os.path.join(board, "agents", owner + ".json.lock")
        self.fd = None

    def __enter__(self):
        try:
            import fcntl
        except ImportError:
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


def try_claim_one_active(board, tid, owner, *, another=False):
    """Claim tid unless owner already holds a different claimed ticket.

    Serializes on the per-agent lock so concurrent assign/claim/next cannot
    both create an active hold. Returns (ticket, None) on success, (None, held)
    when refused for a second hold, or (None, None) when the ticket lock lost.
    """
    with _OwnerHoldLock(board, owner):
        held = _held_claimed(board, owner, except_id=tid)
        if held and not another:
            return None, held
        return try_claim(board, tid, owner), None


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


def unblocked(board, tickets):
    """Open tickets whose dependencies are released (T-1031)."""
    released = _work_view().released_ids(tickets)
    return [
        t
        for t in tickets
        if t["status"] == "open" and all(d in released for d in t.get("deps", []))
    ]


def _refuse_unreleased_deps(t, tickets, only_done=False):
    """One gate: refuse if any predecessor is not dep_released (T-1031)."""
    reason = _work_view().refuse_unreleased_reason(t, tickets, only_done=only_done)
    if reason:
        sys.exit(reason)


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
    """Successor handoff: accept verdict is current; gate/REVIEW claims are not."""
    return _work_view().handoff_notes(t)


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
    head = _rv.displayed_review_head(t)
    if head:
        out.append("review_head: %s" % head)
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
    evs = _rv.format_detail(t)
    if evs:
        out.append("")
        out.append("Review events:")
        out.extend(evs)
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
    for it, t0 in zip(items, made):
        t = load(board, t0["id"])
        _plan_keep_gated_unstarted(board, it, t)
        print("created %s  %s" % (t["id"], t["title"]))


def _plan_keep_gated_unstarted(board, item, t):
    """Plan JSON owner/status cannot start work whose deps are not released."""
    want_owner = (item.get("owner") or "").strip()
    want_status = str(item.get("status") or "").lower()
    if not want_owner and want_status not in (
            "claimed", "in-progress", "inprogress", "wip", "started"):
        return t
    if _work_view().unreleased_dep_id(t, load_all(board)):
        t["owner"] = ""
        t["status"] = "open"
        t.pop("claimed_at", None)
        return save(board, t)
    return t


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
    hdr.append("master: %s" % (m["owner"] if m else "nobody (atm master take)"))
    seat = session_seat(board)
    recorded = seat_confirmed(board)
    hdr.append("you: %s%s" % (seat, "" if recorded else " (UNCONFIRMED)"))
    print("  " + " | ".join(hdr))
    if not recorded:
        print("  ^ this session has NOT recorded a seat; %r is a guess from the "
              "environment." % seat)
        print("    Run `atm join <your-name> --roles <role>` before acting on "
              "anything addressed to a seat.")
    for t in tickets:
        if t["status"] != "done" or a.all:
            print("  " + line(t, tickets))
    ready = unblocked(board, tickets)
    if ready:
        print("  -> ready to claim: %s" % ", ".join(t["id"] for t in ready))
    if not a.quiet:
        print(
            "Shared across Claude/Codex/Cursor. `atm next` claims one atomically; "
            "`atm review <id> --notes \"...\"` submits it. A dependent opens only when a "
            "DIFFERENT seat runs `atm accept <id> --sha <sha>` -- `atm done` alone releases "
            "nothing (checkout `atm quickstart --gate` shows it; this packaged copy has no quickstart)."
        )


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
            print("    repair: atm dep %s --drop %s   (re-point: add --after <id>)"
                  % (tid, ",".join(miss)))


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
    """T-781: HOLD is a condition, not a claimable ready ticket."""
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
    pred = next((x for x in tickets if x["id"] == finished_id), None)
    sha = _work_view().accepted_release_sha(pred) if pred else ""
    children = [x for x in unblocked(board, tickets) if finished_id in x.get("deps", [])]
    # T-946 x T-1031: the automated worktree-cleanup node decides keep/remove
    # for the finished ticket's own worktree. It hands no work to a seat, and
    # its own checks never delete unmerged, dirty or in-use work (they
    # escalate), so it is not gated on acceptance -- gating it would strand
    # exactly the worktrees of tickets closed without verification. Every
    # other successor stays gated.
    seen = set(x["id"] for x in children)
    children += [x for x in tickets
                 if x["id"] not in seen and x.get("status") == "open"
                 and finished_id in (x.get("deps") or [])
                 and _is_automated_cleanup(x)]
    freed = [x["id"] for x in children]
    started, held = [], []
    for child in children:
        if _ticket_on_hold(child):
            held.append(child["id"])
            continue
        if _worktree_gc().is_automated(child):
            # T-946: deterministic executor. No model turn, no task post.
            result = _worktree_gc().run_cleanup_node(
                board, child, _gc_hooks(), probes=_gc_probes(),
                repo_root=os.path.dirname(board), apply=True)
            started.append("%s [automated:%s]" % (child["id"], result.get("status")))
            continue
        who = _reserved_agent(child) or (child.get("suggested") or "").strip()
        text = "unblocked %s after %s" % (child["id"], finished_id)
        if sha:
            text += " accepted %s" % sha
        text += " -- start (success trigger)"
        child.setdefault("notes", []).append({
            "by": whoami(), "at": now(), "text": text,
        })
        save(board, child)
        if who:
            post_message(board, whoami(), text, to=who, re=child["id"], task=True)
            started.append("%s -> %s" % (child["id"], who))
        else:
            started.append(child["id"])
    return freed, started, held


def _retire_stale_gated_start(board, child):
    """Drop owner/lock left by a start that never became IN PROGRESS."""
    tc = _recovery()
    previous_owner = child.get("owner") or ""
    if previous_owner and tc:
        tc.issue_owner_lease(child, "", reason="dependency released",
                             previous_owner=previous_owner)
    child["owner"] = ""
    child.pop("claimed_at", None)
    lock = os.path.join(board, child["id"] + ".lock")
    if os.path.exists(lock):
        try:
            os.unlink(lock)
        except OSError:
            pass
    return child


def _block_unverified_successors(board, finished_id):
    wv = _work_view()
    reason = wv.unverified_block_reason(finished_id)
    tickets = load_all(board)
    released = wv.released_ids(tickets)
    blocked = []
    who = whoami()
    at = now()
    for child in wv.successors_waiting_on(tickets, finished_id, released):
        if _is_automated_cleanup(child):
            # T-946: the worktree-cleanup node is not work for a seat. It only
            # decides keep/remove for the finished ticket's own worktree, and
            # its own checks escalate rather than delete unmerged, dirty or
            # in-use work. See the matching exemption in _start_successors.
            continue
        child["status"] = "blocked"
        child["unverified_block"] = finished_id
        _retire_stale_gated_start(board, child)
        child.setdefault("notes", []).append({"by": who, "at": at, "text": reason})
        save(board, child)
        blocked.append(child["id"])
    return blocked, reason


def _run_cleanup_nodes(board, finished_id):
    """T-946 x T-1031: run the automated worktree-cleanup node even when the
    predecessor closed without an ACCEPT.

    The node hands no work to a seat: it decides keep/remove for the finished
    ticket's own worktree and escalates instead of deleting unmerged, dirty or
    in-use work. Gating it on acceptance would strand exactly the worktrees of
    tickets closed unverified. Every other successor stays gated.
    """
    gc = _worktree_gc()
    ran = []
    for child in load_all(board):
        if child.get("status") != "open" or finished_id not in (child.get("deps") or []):
            continue
        if not _is_automated_cleanup(child):
            continue
        result = gc.run_cleanup_node(board, child, _gc_hooks(), probes=_gc_probes(),
                                    repo_root=os.path.dirname(board), apply=True)
        ran.append("%s [automated:%s]" % (child["id"], result.get("status")))
    return ran


def _reopen_unverified_successors(board, finished_id, sha="", seat=""):
    """Reconcile gate-owned blocks whenever a predecessor becomes released."""
    from contextlib import nullcontext
    wv = _work_view()
    tickets = load_all(board)
    pred = next((x for x in tickets if x["id"] == finished_id), None)
    if not pred or not wv.dep_released(pred):
        return []
    sha = sha or wv.accepted_release_sha(pred)
    accepts = [e for e in pred.get("review_events", [])
               if e.get("kind") == "accept" and not e.get("superseded")
               and (e.get("sha") or "").strip().lower() == (sha or "").strip().lower()]
    seat = seat or (accepts[-1].get("by") if accepts else "") or whoami()
    opened = []
    for snapshot in tickets:
        if finished_id not in snapshot.get("deps", []):
            continue
        tc = _recovery()
        lock = tc.ticket_mutation_lock(board, snapshot["id"]) if tc else nullcontext()
        with lock:
            child = load(board, snapshot["id"])
            gated = (child.get("unverified_block") == finished_id or any(
                wv.is_live_unverified_gate_note(n.get("text"), finished_id)
                for n in child.get("notes", [])))
            # Claimed+gated is a start that was refused or bypassed, not real work.
            never_started = child.get("status") in ("blocked", "open") or (
                gated and child.get("status") == "claimed")
            claim_lock = os.path.join(board, child["id"] + ".lock")
            stale_start = never_started and bool(
                child.get("owner") or os.path.exists(claim_lock))
            if not gated and not stale_start:
                continue
            if gated:
                wv.drop_unverified_gate_notes(child, finished_id)
                if child.get("unverified_block") == finished_id:
                    child.pop("unverified_block", None)
            if never_started:
                # Assignment never started. Keep routing hints, retire the
                # lease and lock so a real claim is not "already taken".
                _retire_stale_gated_start(board, child)
                child["status"] = "open"
                remaining = wv.unreleased_dep_id(child, load_all(board))
                if remaining:
                    child["status"] = "blocked"
                    child["unverified_block"] = remaining
                    child.setdefault("notes", []).append({
                        "by": whoami(), "at": now(),
                        "text": wv.unverified_block_reason(remaining)})
                else:
                    opened.append(child["id"])
            override = pred.get("release_override") or {}
            text = (wv.accepted_release_note(finished_id, sha, seat) if sha else
                    "%s released (%s override) by %s -- %s" % (
                        finished_id, override.get("kind", ""),
                        override.get("by", seat), override.get("reason", "")))
            if not any((n.get("text") or "") == text for n in child.get("notes") or []):
                child.setdefault("notes", []).append(
                    {"by": whoami(), "at": now(), "text": text})
            save(board, child)
    return opened


def _maybe_record_release_override(t, a, board):
    wv = _work_view()
    if wv.dep_released(t):
        return True
    closer = whoami()
    at = now()
    if getattr(a, "release_unverified", False):
        t["release_override"] = wv.make_release_override(
            "operator", closer, at, "operator --release-unverified")
        save(board, t)
        return True
    if wv.is_docs_exempt(t):
        t["release_override"] = wv.make_release_override(
            "docs-exempt", closer, at,
            "docs-exempt: role=%s never submitted for review" % (t.get("role") or ""))
        save(board, t)
        return True
    return False


def _print_successor_release(t, freed, started, held):
    ov = t.get("release_override") or {}
    verified = _work_view().review_of(t)["verified"]
    if ov.get("kind") and not verified:
        print("released (%s override)%s" % (
            ov["kind"], (": %s" % ", ".join(freed)) if freed else ""))
    if freed:
        print("unblocked: %s" % ", ".join(freed))
    if started:
        print("started: %s" % ", ".join(started))
    if held:
        print("held (not started): %s" % ", ".join(held))


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


def _work_view():
    """T-889 Work view (packaged with sounding). Never import as a top-level
    module: work_view.py uses relative ``.sounding``, which crashes
    ``python src/ticket_board/cli.py next`` on every reopened ticket."""
    try:
        from ticket_board import work_view as m
        return m
    except ImportError:
        src = os.path.dirname(os.path.dirname(os.path.realpath(__file__)))
        if src not in sys.path:
            sys.path.insert(0, src)
        from ticket_board import work_view as m
        return m


def _task_life_actionable(board, message):
    """False when a ticket-scoped task is previous-life or same-second unknown."""
    tid = (message.get("re") or "").strip()
    if not tid:
        return True
    kind = (message.get("kind") or "").strip()
    text = str(message.get("text") or "").strip().lower()
    is_task = kind == "task" or message.get("task") or text.startswith(
        ("stuck", "blocked", "task:", "task "))
    if not is_task:
        return True
    try:
        t = load(board, tid)
    except Exception:
        return True
    if not t:
        return True
    wv = _work_view()
    return wv._life_of(t, message) == wv.LIFE_CURRENT


def _reopen_blocks_automation(board, t, messages=None):
    """True when the only ticket-scoped tasks after reopen are unknown-ordered."""
    if not ((t.get("reopened_at") or "").strip() or t.get("reopened_seen")):
        return False
    wv = _work_view()
    lives = []
    for m in (messages if messages is not None else load_messages(board)):
        if (m.get("re") or "").strip() != t["id"]:
            continue
        kind = (m.get("kind") or "").strip()
        text = str(m.get("text") or "").strip().lower()
        if kind != "task" and not m.get("task") and not text.startswith(
                ("stuck", "blocked", "task:", "task ")):
            continue
        lives.append(wv._life_of(t, m))
    if not lives:
        return False
    return wv.LIFE_CURRENT not in lives and wv.LIFE_UNKNOWN in lives


def cmd_next(a, board):
    if getattr(a, "dispatch", False):
        return _cmd_next_dispatch(a, board)
    owner = whoami(a.owner)
    _refuse_limited_seat(board, owner, "next")
    roles = roles_for(board, owner, a.role)
    if roles == [] and not a.role:
        print(
            "agent %r has no default roles (see .tickets/roles.json). "
            "Pass --role or do not claim." % owner
        )
        sys.exit(1)
    tickets = load_all(board)
    held = _held_claimed(board, owner)
    if held and not a.another:
        print(_already_hold_msg(held))
        sys.exit(1)
    steal_id = (getattr(a, "steal", None) or "").strip()
    ready_all = unblocked(board, tickets)
    ready = [t for t in _filter_ready(ready_all, roles) if can_do(board, owner, t)]
    ready = [t for t in ready if not _reservation_blocks(t, owner, steal_id)]
    ready = [t for t in ready if not _ticket_on_hold(t)]
    ready = [t for t in ready if not _reopen_blocks_automation(board, t)]
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
        got, held_now = try_claim_one_active(board, t["id"], owner, another=a.another)
        if held_now:
            print(_already_hold_msg(held_now))
            sys.exit(1)
        if got:
            checkin(board, owner, got["id"])
            print(detail(board, got, load_all(board)))
            warn = worktree_warning(owner)
            if warn:
                print("")
                print(warn)
            print("")
            print("Post progress with `atm update %s \"...\"` at least every %d min; "
                  "finish with `atm review %s --notes \"exact SHA, paths, decisions\"`."
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
        print("register with: atm join %s --can %s" % (owner, ",".join(sorted(set(
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
            "Fix with: atm dep %s --drop %s" % (" -> ".join(cyc), cyc[0], cyc[1])
        )
        sys.exit(2)
    ghosts = dangling(tickets)
    if ghosts:
        print("BROKEN: these wait on tickets that do not exist:")
        for tid, miss in ghosts.items():
            print("  %s -> %s" % (tid, ", ".join(miss)))
        print("Fix with: atm dep <id> --drop <missing-id>")
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
    _refuse_unreleased_deps(t, load_all(board))
    got, held = try_claim_one_active(board, a.id, owner, another=bool(getattr(a, "another", False)))
    if held:
        sys.exit(_already_hold_msg(held))
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
    _refuse_unreleased_deps(t, load_all(board))
    if t["status"] not in ("claimed", "blocked", "open"):
        sys.exit("%s is %s; only in-progress work can be submitted" % (a.id, LABEL[t["status"]]))
    if not a.notes:
        sys.exit('review needs --notes "what to look at: paths, tests run, decisions"')
    g = git_state()
    if g and g["branch"] in ("main", "master") and not a.force:
        sys.exit("RULE: submit from your own worktree branch, not %r (or --force)" % g["branch"])
    if g and g["dirty"] and not a.force:
        sys.exit("RULE: %d uncommitted files -- commit before submitting for review (or --force)" % g["dirty"])
    if g and not a.force:
        trunk = _trunk()
        if git("merge-base", "--is-ancestor", trunk, "HEAD") is None:
            sys.exit("RULE: your branch is behind %s. Run `atm sync` (merges %s in, so conflicts "
                     "are yours to fix now, not the master's later), then submit again." % (trunk, trunk))
    if (a.pr or "").strip():
        if not g:
            sys.exit("review --pr: not in a git working tree; cannot verify a submitted SHA")
        origin = git("config", "--get", "remote.origin.url") or ""
        pin_err = _rv.verify_submit(
            lambda *args, cwd=None: git(*args),
            sha_full=g.get("sha_full") or "", branch=g.get("branch") or "",
            origin_url=origin, pr=a.pr, dirty=g.get("dirty") or 0)
        if pin_err:
            sys.exit(pin_err)
    owner = t.get("owner") or whoami(a.owner)
    author = whoami(a.owner)
    tc = _recovery()
    if tc is not None:
        err = tc.stale_accept_error(t, author, kind="review")
        if err:
            sys.exit(err)
    expected_generation = t.get("owner_generation")
    _work_view().supersede_release_evidence(t)
    t.pop("review_head", None)
    t["status"] = "review"
    t["owner"] = owner
    t["review_at"] = now()
    text = a.notes
    if g:
        stamp = "%s@%s" % (g["branch"], g["sha"])
        text = "%s -- %s" % (stamp, text)
        t["commit"] = stamp
        t["branch"] = g["branch"]
        if g.get("sha_full"):
            _rv.record_verified_head(t, g["sha_full"], pr=a.pr)
    if a.pr:
        t["pr"] = a.pr
        text += " (PR %s)" % a.pr
    t["notes"].append({"by": owner, "at": now(), "text": "REVIEW: " + text})
    save(board, t, expected_generation=expected_generation)
    checkin(board, author, t["id"], "submitted %s for review" % t["id"])
    if owner != author:
        # T-428: do not copy the submitter's cwd/branch/sha onto the owner.
        _agent_set(board, owner, ticket=t["id"],
                   note="submitted %s for review by %s" % (t["id"], author))
    _tmr = timing(t)
    traj_event(board, "review", agent=owner, ticket=t,
               state_before="claimed", state_after="review",
               outcome="review", notes_len=len(a.notes or ""),
               active_hours=_traj_round3(_tmr.get("active")),
               pin=t.get("commit", ""), **_traj_git())
    m = current_master(board)
    post_message(board, owner, "%s ready for review: %s" % (t["id"], text),
                 to=(m["owner"] if m else ""), re=t["id"])
    tm = timing(t)
    print("%s -> IN REVIEW after %s of work; master%s notified. Claim your next ticket." % (
        t["id"], fmt_hours(tm["active"]), (" (%s)" % m["owner"]) if m else ""))
    if t.get("commit"):
        print("pinned %s" % t["commit"])
    head = t.get("review_head") or ""
    if head:
        print("review_head: %s" % head)


def cmd_accept(a, board):
    """Record a structured accept bound to the submitted review head (T-944)."""
    t = load(board, a.id)
    ev, err = _rv.apply(
        t, whoami(), a.sha, "accept", notes=a.notes, require_full=True)
    if err:
        sys.exit(err)
    save(board, t)
    print("%s accepted %s by %s" % (a.id, ev["sha"], ev["by"]))
    if t.get("status") == "done":
        _reopen_unverified_successors(board, a.id, sha=ev["sha"], seat=ev["by"])
        freed, started, held = _start_successors(board, a.id)
        _print_successor_release(t, freed, started, held)


def cmd_reject(a, board):
    """Record a structured reject and return the ticket to its author (T-944/T-1460).

    API decideReview(reject) already flips state to claimed with the same owner.
    CLI used to leave status=review, so atm mine stayed empty and the author
    idled until a coordinator ran reopen+assign. Reject now matches the API.
    """
    t = load(board, a.id)
    reviewer = whoami()
    ev, err = _rv.apply(
        t, reviewer, a.sha, "reject", reason=a.reason, require_full=False)
    if err:
        sys.exit(err)
    author = _rv.return_to_author_for_revision(
        t, actor=reviewer, reason=ev.get("reason") or a.reason, sha=ev["sha"],
        kind="reject")
    tc = _recovery()
    if tc is not None and author:
        tc.issue_owner_lease(
            t, author, harness=_lease_harness(board, author),
            reason="reject-revision", previous_owner=author)
        tc.rewrite_claim_lock(board, t["id"], author)
    save(board, t)
    if author:
        _bind_agent_ticket(board, author, t["id"])
        post_message(
            board, reviewer,
            "%s rejected %s -- revise and resubmit: %s" % (
                t["id"], ev["sha"][:12], ev.get("reason") or ""),
            to=author, re=t["id"], task=True)
    print("%s rejected %s by %s; returned to %s as claimed for revision" % (
        a.id, ev["sha"], ev["by"], author or "?"))


def _trunk():
    for b in ("main", "master"):
        if git("rev-parse", "--verify", "-q", b) is not None:
            return b
    return "main"


def cmd_sync(a, board):
    """Agent side: bring main into my branch now, so the master's merge is trivial.

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
    g = git_state()
    if not g:
        sys.exit("not in a git repo")
    trunk = _trunk()
    if g["branch"] in ("main", "master"):
        sys.exit("you are on %s; sync is for your own worktree branch" % g["branch"])
    if g["dirty"] and not a.force:
        sys.exit("%d uncommitted files; commit first (sync merges %s into your branch)" % (g["dirty"], trunk))
    if git("remote", "get-url", "origin") is None:
        sys.exit("no 'origin' remote configured; cannot confirm %s is not stale without one "
                  "(comparing against the local %s ref is the bug this refusal exists to avoid)"
                  % (trunk, trunk))
    if git("fetch", "origin", trunk) is None:
        sys.exit("could not fetch origin/%s -- refusing to sync against a possibly-stale local "
                  "ref; retry once the remote is reachable" % trunk)
    remote_trunk = "origin/" + trunk
    if git("merge-base", "--is-ancestor", remote_trunk, "HEAD") is not None:
        print("%s already contains %s; nothing to do" % (g["branch"], remote_trunk))
        return
    import subprocess
    r = subprocess.run(["git", "merge", "--no-edit", "-m", "Sync %s into %s" % (remote_trunk, g["branch"]), remote_trunk],
                       capture_output=True, text=True)
    if r.returncode == 0:
        print("merged %s into %s -> %s" % (remote_trunk, g["branch"], git("rev-parse", "--short", "HEAD")))
        checkin(board, whoami(), None, "synced with %s" % remote_trunk)
        return
    conflicted = (git("diff", "--name-only", "--diff-filter=U") or "").splitlines()
    print("CONFLICTS merging %s into %s -- these files need you:" % (remote_trunk, g["branch"]))
    for f in conflicted:
        print("  " + f)
    print("Resolve, `git add` them, `git commit`, then `atm review` again. "
          "Or `git merge --abort` and ask the master (`atm msg`).")
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
    """Only the designated master.json owner may run atm merge."""
    m = current_master(board)
    if not m or not m.get("owner"):
        sys.exit("refused: no designated integrator in master.json (atm master take first)")
    if m["owner"] != owner and not force_master:
        sys.exit("rejected owner: %s is not the designated integrator (%s); "
                 "pass --force-master only for break-glass recovery" % (owner, m["owner"]))
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
    """
    import subprocess
    root = os.path.dirname(board)
    cfg = merge_config(board)
    trunk = _trunk()
    owner = whoami(a.owner)
    require_integrator(board, owner, force_master=getattr(a, "force_master", False))

    def sh(*args, cwd=root):
        return subprocess.run(list(args), cwd=cwd, capture_output=True, text=True)

    with IntegrationLock(board):
        tickets = load_all(board)
        queue = [t for t in tickets if t["status"] == "review"]
        branches = list(a.branches) or sorted(set(t.get("branch") for t in queue if t.get("branch")))
        if not branches:
            sys.exit("nothing to merge: no branches given and the review queue is empty")
        dirty = sh("git", "status", "--porcelain", "--untracked-files=no").stdout.strip()
        if dirty:
            sys.exit("main checkout has uncommitted tracked changes; commit or move them first:\n" + dirty)

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
        print("integration: %s (from %s@%s)" % (idir, trunk, git("rev-parse", "--short", trunk)))
        stray = os.path.join(idir, ".tickets")
        if os.path.islink(stray):
            os.unlink(stray)

        merged_branches, merged_shas, skipped, resolved_docs = [], [], [], []
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
                       "Integrate %s@%s into %s (master %s via atm merge%s)" % (
                           b, short, trunk, owner,
                           "; conflicts resolved in favour of the integrated tree" if extra else ""),
                       pin, cwd=idir)
                if r.returncode == 0:
                    merged_shas.append(pin)
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
                    if b not in merged_branches:
                        merged_branches.append(b)
                    print("  merged %s@%s (doc-only conflicts kept both copies: %s)" % (
                        b, short, ", ".join(conflicted)))
                else:
                    sh("git", "merge", "--abort", cwd=idir)
                    skipped.append((b, "code conflicts at %s: %s" % (short, ", ".join(conflicted[:6]))))
                    print("  SKIPPED %s@%s -- code conflicts in %s; ask its owner to `atm sync` and resolve"
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
        sha = git("rev-parse", "--short", trunk)
        full_trunk = sh("git", "rev-parse", trunk).stdout.strip()
        if getattr(a, "push", False):
            pr = sh("git", "push", "origin", trunk)
            if pr.returncode != 0:
                why = (pr.stderr or pr.stdout).strip()
                print("PUSH FAILED: could not push %s to origin/%s\n%s" % (trunk, trunk, why))
                _master_log(board, "atm merge: integrated %s@%s locally but PUSH FAILED (%s)" % (
                    trunk, sha, why.splitlines()[0] if why else "?"), by=owner)
                sys.exit(4)
            remote = sh("git", "ls-remote", "--heads", "origin", trunk).stdout.strip().split()
            remote_sha = remote[0] if remote else ""
            if remote_sha != full_trunk:
                print("PUSH FAILED: origin/%s is %s, expected %s" % (
                    trunk, remote_sha[:12] if remote_sha else "?", sha))
                _master_log(board, "atm merge: push reported success but origin/%s != local %s" % (
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

        closed = []
        for t2 in queue:
            pin = parse_review_sha(t2.get("commit"))
            if not pin:
                continue
            got = sh("git", "rev-parse", pin)
            if got.returncode != 0:
                continue
            full = got.stdout.strip()
            if sh("git", "merge-base", "--is-ancestor", full, full_trunk).returncode != 0:
                continue  # submitted SHA not on resulting main
            # Close only tickets whose recorded SHA was actually integrated (not merely same branch).
            if full not in pin_full:
                continue
            t2["status"] = "done"
            t2["done_at"] = now()
            t2["merge_record"] = _work_view().make_merge_record(
                owner, now(), full_trunk or sha, pin=full, trunk=trunk)
            t2["notes"].append({"by": owner, "at": now(),
                                "text": "merged into %s as %s (atm merge; pinned %s)" % (
                                    trunk, sha, pin)})
            save(board, t2)
            closed.append(t2["id"])
            _tm2 = timing(t2)
            traj_event(board, "merge", agent=owner, ticket=t2,
                       state_before="review", state_after="done", outcome="done",
                       pin=pin, merged_as=sha, trunk=trunk,
                       prev_owner=t2.get("owner", ""),
                       active_hours=_traj_round3(_tm2.get("active")))
            post_message(board, owner, "%s merged into %s as %s" % (t2["id"], trunk, sha),
                         to=t2.get("owner", ""), re=t2["id"])
        if closed:
            print("closed: %s" % ", ".join(closed))
        for b, why in skipped:
            print("  skipped %s: %s" % (b, why))
        for f, alt in resolved_docs:
            print("  doc conflict %s: kept %s's copy, branch copy at %s" % (f, trunk, alt))
        _master_log(board, "atm merge: pins %s -> %s@%s; tests %s; closed %s%s" % (
            ", ".join(p[:7] for p in merged_shas) or "-",
            trunk, sha, "green" if t.returncode == 0 else "skipped",
            ", ".join(closed) or "-",
            ("; skipped " + ", ".join(str(b) for b, _ in skipped)) if skipped else ""), by=owner)
        post_message(board, owner, "%s is now %s (merged %s). Everyone: run `atm sync` in your worktree "
                     "before your next `atm review`." % (trunk, sha, ", ".join(merged_branches)))


# ---- usage limits -------------------------------------------------------

LIMIT_PATTERNS = ("usage limit", "rate limit", "rate_limit", "hit your limit", "limit reached",
                  "out of credits", "quota exceeded", "429", "resets at", "try again in")


def _scan_logs(paths, hours):
    """Return [(path, hits, last_mtime)] for files touched within `hours` containing limit text."""
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
        try:
            with open(p, "r", errors="ignore") as f:
                for ln in f:
                    low = ln.lower()
                    if any(k in low for k in LIMIT_PATTERNS) and ("limit" in low or "429" in low or "credit" in low or "quota" in low):
                        hits += 1
        except OSError:
            continue
        if hits:
            out.append((p, hits, st.st_mtime))
    return out


def cmd_limit(a, board):
    """Record (or clear) that an agent hit a usage limit; shown in who/master."""
    owner = a.agent or whoami()
    from ticket_board.agent_checkin import _agent_update as _locked_agent_update

    def mutate(rec):
        if a.clear:
            rec.pop("limit", None)
        else:
            rec["limit"] = {"at": now(), "until": a.until or "", "note": a.note or ""}
            lim = rec["limit"]
            lim["reset_at"] = _route_headroom().provider_reset_at(lim["until"] or lim["note"], lim["at"])

    rec = _locked_agent_update(board, owner, mutate)
    if rec is None:
        rec = checkin(board, owner)
        rec = _locked_agent_update(board, owner, mutate)
    if a.clear:
        msg = "%s is back (limit cleared)" % owner
    else:
        msg = "%s hit a usage limit%s%s" % (owner, (" until %s" % a.until) if a.until else "",
                                            (": %s" % a.note) if a.note else "")
    post_message(board, owner, msg)
    print(msg)
    held = [t for t in load_all(board) if t["status"] == "claimed" and t.get("owner") == owner]
    if held and not a.clear:
        print("still holding: %s -- master may `atm reopen` them for someone else" % ", ".join(t["id"] for t in held))


def cmd_limits(a, board):
    """Who is limited: manual records + silence + a scan of local tool logs."""
    print("Recorded limits:")
    any_ = False
    records = load_agents(board)
    records.sort(key=lambda r: _route_headroom()._stamp(
        (r.get("limit") or r.get("expired_limit") or {}).get("at")) or datetime.min.replace(tzinfo=timezone.utc))
    for r in records:
        lim = r.get("limit") or r.get("expired_limit")
        if lim:
            any_ = True
            expired, _, reason = _route_headroom().limit_expiry(lim)
            print("  %-14s hit %s ago%s%s%s" % (r["owner"], fmt_hours(hours_since(lim.get("at", ""))),
                                             (", back %s" % lim["until"]) if lim.get("until") else "",
                                             (" -- %s" % lim["note"]) if lim.get("note") else "",
                                             (" [STALE: %s; no longer blocks]" % reason) if expired else ""))
    if not any_:
        print("  none (agents record one with `atm limit --until \"...\"`)")
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
    sources = {
        "claude": glob.glob(os.path.join(home, ".claude", "projects", "*", "*.jsonl")),
        "codex": glob.glob(os.path.join(home, ".codex", "sessions", "**", "*.jsonl"), recursive=True)
                 + glob.glob(os.path.join(home, ".codex", "log", "*")),
        "cursor": glob.glob(os.path.join(home, ".cursor", "chats", "**", "*.json*"), recursive=True)
                  + glob.glob(os.path.join(home, ".cursor", "*.log")),
    }
    print("Local tool logs mentioning limits (last %dh):" % a.hours)
    found = False
    for tool, paths in sources.items():
        hits = _scan_logs(paths, a.hours)
        hits.sort(key=lambda x: -x[2])
        for p, n, mt in hits[:5]:
            found = True
            short = p.replace(home, "~")
            print("  %-7s %3d hits  %s ago  %s" % (tool, n, fmt_hours((datetime.now(timezone.utc).timestamp() - mt) / 3600.0), short[-90:]))
    if not found:
        print("  none found (grok/other tools: record manually with `atm limit`)")


def cmd_status(a, board):
    """Generic status setter with the words agents actually use."""
    word = a.status.lower()
    target = STATUS_WORDS.get(word)
    if not target:
        sys.exit("unknown status %r; use one of: %s" % (a.status, ", ".join(sorted(STATUS_WORDS))))
    t = load(board, a.id)
    if target == "claimed":
        _refuse_unreleased_deps(t, load_all(board))
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


def cmd_done(a, board):
    t = load(board, a.id)
    closer = whoami()
    tc = _recovery()
    if tc is not None:
        err = tc.stale_accept_error(t, closer, kind="done")
        if err:
            sys.exit(err)
    expected_generation = t.get("owner_generation")
    if not a.notes and not a.no_notes:
        sys.exit(
            'done needs --notes "paths, names, decisions the next agent must match" '
            "(or --no-notes if there is truly nothing to hand off)"
        )
    g = git_state()
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
    # T-1082: accepted_sha wins over cwd HEAD (cli.py has no --artifact).
    # honor_cwd never overrides the pin; --artifact is a location, not a verdict.
    pin_g, pin_warn = _work_view().done_pin_state(t, g, honor_cwd=False)
    if pin_warn:
        print(pin_warn, file=sys.stderr)
    g = pin_g
    t["status"] = "done"
    t["done_at"] = now()
    if not t.get("claimed_at"):
        t["claimed_at"] = t.get("updated") or t["created"]
    text = a.notes
    if g:
        stamp = "%s@%s" % (g["branch"], g["sha"])
        if stamp not in text:
            text = ("%s -- %s" % (stamp, text)) if text else stamp
        t["commit"] = stamp
    if text:
        t["notes"].append({"by": t.get("owner") or "agent", "at": now(), "text": text})
    save(board, t, expected_generation=expected_generation)
    if t.get("owner"):
        # T-428: checkin() always writes THIS process's cwd/branch/sha. That is
        # the closer's location. Stamping it onto a different owner makes
        # `atm who` lie (the owner appears to sit in the closer's tree).
        closer = whoami()
        note = "finished %s by %s" % (a.id, closer)
        if t["owner"] == closer:
            checkin(board, t["owner"], "", note)
        else:
            _agent_set(board, t["owner"], ticket="", note=note)
            checkin(board, closer, None, note)
    tm = timing(t)
    traj_event(board, "done", agent=whoami(), ticket=t,
               state_before="review" if t.get("review_at") else "claimed",
               state_after="done", outcome="done",
               notes_len=len(a.notes or ""),
               active_hours=_traj_round3(tm.get("active")),
               wait_hours=_traj_round3(tm.get("wait")),
               pin=t.get("commit", ""), **_traj_git())
    print("%s done in %s (waited %s before claim)" % (
        a.id, fmt_hours(tm["active"]), fmt_hours(tm["wait"])))
    if g:
        print("recorded %s" % t["commit"])
    if _maybe_record_release_override(t, a, board):
        _reopen_unverified_successors(board, a.id)
        freed, started, held = _start_successors(board, a.id)
        _print_successor_release(t, freed, started, held)
    else:
        blocked, reason = _block_unverified_successors(board, a.id)
        if blocked:
            print("blocked: %s -- %s" % (", ".join(blocked), reason))
        for line in _run_cleanup_nodes(board, a.id):
            print("cleanup: %s" % line)


def cmd_block(a, board):
    t = load(board, a.id)
    before = t["status"]
    t["status"] = "blocked"
    t["notes"].append({"by": t.get("owner") or "agent", "at": now(), "text": a.reason})
    save(board, t)
    traj_event(board, "block", agent=whoami(), ticket=t,
               state_before=before, state_after="blocked",
               outcome="blocked", notes_len=len(a.reason or ""), **_traj_git())
    print("%s blocked: %s" % (a.id, a.reason))


def cmd_note(a, board):
    t = load(board, a.id)
    who = whoami(a.by)
    t["notes"].append({"by": who, "at": now(), "text": a.text})
    save(board, t)
    # notes_len only -- the note body is the agent's own prose and never enters
    # the trajectory log.
    traj_event(board, "update", agent=who or whoami(), ticket=t,
               notes_len=len(a.text or ""), state_after=t["status"], **_traj_git())
    if who and t["status"] == "claimed":
        checkin(board, who, t["id"], a.text[:80])
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
    bind_owner = None
    clear_prev = None
    transfer_owner = None
    reserve_lock = None
    expected_generation = None
    rewrite_lock_to = None
    try:
        if a.owner is not None:
            # hard assignment by the master: takes the lock on their behalf
            prev_owner = t.get("owner") or ""
            if a.owner:
                _refuse_unreleased_deps(t, load_all(board))
            if t["status"] == "open" and a.owner:
                got, held = try_claim_one_active(board, t["id"], a.owner)
                if held:
                    # Queued work stays queued: reserve, do not fabricate a
                    # second in-progress claim (T-979 / T-972). Re-read under
                    # the claim lock so a concurrent other-owner claim cannot
                    # be overwritten by this stale open image.
                    reserve_lock = _try_lock_ticket_excl(board, t["id"], a.owner)
                    if reserve_lock is None:
                        sys.exit("%s was claimed by someone else while assigning" % t["id"])
                    t = load(board, t["id"])
                    if t.get("status") != "open":
                        sys.exit("%s was claimed by someone else while assigning" % t["id"])
                    t["reserved_for"] = a.owner
                    changed.append("reserved for %s (already holds %s)" % (
                        a.owner, ", ".join(x["id"] for x in held)))
                elif not got:
                    sys.exit("%s was claimed by someone else while assigning" % t["id"])
                else:
                    t = got
                    changed.append("claimed for %s" % a.owner)
                    bind_owner = a.owner
            elif t["status"] == "review" and a.owner:
                # T-1460: retarget an IN REVIEW ticket so the assignee can act.
                # Free seat → claimed; already holding another → open+reserved
                # (same one-active-hold rule as assign on open).
                held = _held_claimed(board, a.owner, except_id=t["id"])
                if held:
                    t["status"] = "open"
                    t["owner"] = ""
                    t["reserved_for"] = a.owner
                    if prev_owner:
                        clear_prev = prev_owner
                    lock = os.path.join(board, t["id"] + ".lock")
                    if os.path.exists(lock):
                        try:
                            os.unlink(lock)
                        except OSError:
                            pass
                    changed.append("reserved for %s (already holds %s); left IN REVIEW for revision" % (
                        a.owner, ", ".join(x["id"] for x in held)))
                else:
                    if prev_owner and prev_owner != a.owner:
                        clear_prev = prev_owner
                        tc = _recovery()
                        if tc is not None:
                            expected_generation = tc.owner_generation(t)
                            tc.issue_owner_lease(
                                t, a.owner, harness=_lease_harness(board, a.owner),
                                reason="review-retarget",
                                previous_owner=prev_owner)
                            rewrite_lock_to = a.owner
                    t["status"] = "claimed"
                    t["owner"] = a.owner
                    if not t.get("claimed_at"):
                        t["claimed_at"] = now()
                    bind_owner = a.owner
                    transfer_owner = a.owner
                    changed.append("claimed for %s (returned from review)" % a.owner)
            elif t["status"] in ("claimed", "review"):
                if t["status"] == "claimed" and a.owner and a.owner != prev_owner:
                    transfer_owner = a.owner
                t["owner"] = a.owner
                changed.append("owner=%s" % a.owner)
                if prev_owner and prev_owner != a.owner:
                    tc = _recovery()
                    if tc is not None:
                        expected_generation = tc.owner_generation(t)
                        tc.issue_owner_lease(
                            t, a.owner, harness=_lease_harness(board, a.owner),
                            reason="reassign", previous_owner=prev_owner)
                        rewrite_lock_to = a.owner
                    clear_prev = prev_owner
                if a.owner:
                    bind_owner = a.owner
        if not changed:
            sys.exit("nothing to change; see atm assign --help")
        t["notes"].append({"by": whoami(a.by), "at": now(), "text": "assign: " + ", ".join(changed)})

        def _save_and_rewrite_lock():
            # Hold/generation already validated. Publish JSON and relabel the
            # claim lock in the same ticket-json critical section so a refused
            # transfer or lost-generation save cannot leave Alice on T002.lock.
            tc_pub = _recovery() if rewrite_lock_to else None
            if tc_pub is not None:
                with tc_pub.ticket_mutation_lock(board, t["id"]):
                    save(board, t, expected_generation=expected_generation)
                    tc_pub.rewrite_claim_lock(board, t["id"], rewrite_lock_to)
            else:
                save(board, t, expected_generation=expected_generation)

        if transfer_owner:
            with _OwnerHoldLock(board, transfer_owner):
                held = _held_claimed(board, transfer_owner, except_id=t["id"])
                if held:
                    sys.exit("%s already holds %s -- finish that before taking an active assignment of %s"
                             % (transfer_owner, ", ".join(x["id"] for x in held), t["id"]))
                _save_and_rewrite_lock()
        else:
            _save_and_rewrite_lock()
    finally:
        _release_ticket_excl(reserve_lock)
    if clear_prev:
        _clear_agent_ticket(board, clear_prev, t["id"])
    if bind_owner:
        _bind_agent_ticket(board, bind_owner, t["id"])
    print("%s: %s" % (t["id"], ", ".join(changed)))


def cmd_reserve(a, board):
    """Reserve an open ticket for an agent without claiming (T-552).

    Status stays TO DO; no wait-turns. Master/planner/optimizer may --for;
    anyone may --drop. atm next --steal <id> and assign --owner override.
    """
    t = load(board, a.id)
    who = whoami(getattr(a, "owner", "") or "")
    drop = bool(getattr(a, "drop", False))
    target = (getattr(a, "for_agent", None) or "").strip()
    if drop and target:
        sys.exit("atm reserve: use --for <agent> or --drop, not both")
    if not drop and not target:
        sys.exit("atm reserve <id> --for <agent>  (or --drop)")
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
        sys.exit("atm reserve --for is master/planner/optimizer only (anyone may --drop)")
    if t.get("status") != "open":
        sys.exit("%s is %s; reserve only open tickets" % (t["id"], t.get("status") or "?"))
    _refuse_unreleased_deps(t, load_all(board), only_done=True)
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
        print("no epics (atm epic create \"title\")")
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
        print("no sprints (atm sprint create \"goal\" --activate)")
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
   `atm plan` graph (real `--after` edges), not a flat list.

I will not spawn workers or create tickets until you answer.
CLI: `atm` (the `tickets` command is an alias).
Run `atm harness available` to probe every catalog row (missing is a row).
It auto-checks usage; unsupported or missing remaining/reset is unknown, not exhausted.
When they name tasks, use `atm plan` so deps are real `--after` edges.
Unattended persist ends at a reviewable SHA; human review is the gate.
"""


def print_onboarding_startup():
    sys.stdout.write(ONBOARDING_STARTUP)
    if not ONBOARDING_STARTUP.endswith("\n"):
        sys.stdout.write("\n")
    sys.stdout.write("\n")


CEO_ONBOARDING_STARTUP = """**You are onboarding as Atman CEO.**

Connecting here is joining **Atman**, not Claude, Cursor, Codex, or any
other provider. Board identity is `atman-<seat>` (example: `atman-ceo`).
"""


def board_is_atman_operator(board):
    """True only for the Atman team's own board (T-1007 / T-1093)."""
    if (os.environ.get("ATMAN_OPERATOR_BOARD") or "").strip() == "1":
        return True
    if not board or not os.path.isdir(board):
        return False
    names = list((load_workforce(board) or {}).keys())
    names += [(r or {}).get("owner") or "" for r in (load_agents(board) or [])]
    names += list((load_aliases(board) or {}).keys())
    return any(str(n).strip().lower().startswith("atman-") for n in names)


def board_is_living(board):
    if not board or not os.path.isdir(board):
        return False
    obj_path = os.path.join(board, "objective.json")
    try:
        with open(obj_path) as f:
            obj = json.load(f)
        if str(obj.get("text") or "").strip():
            return True
    except (IOError, ValueError):
        pass
    try:
        return bool(load_all(board))
    except Exception:
        return False


def atman_seat_name(seat="ceo"):
    raw = (seat or "ceo").strip().lower()
    if raw.startswith("atman-"):
        raw = raw[len("atman-"):]
    raw = "".join(ch if (ch.isalnum() or ch == "-") else "-" for ch in raw).strip("-") or "ceo"
    if raw in ("master", "everyone", "cursor"):
        raw = "ceo"
    return "atman-%s" % raw


MASTER_TEMPLATE = """**You are onboarding.**

# MASTER -- coordination node for this board

Any agent can become master: run `atm master take`, then `atm master`
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

Run `atm harness available`. It probes `command -v` for every catalog
entry (Cursor `agent`/`cursor-agent`, `agy`, `claude`, `codex`, `devin`,
`gemini`). Missing is a row, not a skip. Auto-checks usage; missing
remaining/reset is FAIL.

Ask: **Which of these do you want to use?** Do not spawn until they answer.
Codex stays in the catalog even with **unknown usage**. Gemini dispatch records
harness=gemini; persist/hooks is the wake.

### Step 3 — Announce that name on the board

After they pick a name and integrations:

```
atm msg --to everyone "<name> is onboarding. Integrating: <list>. Objective and tasks next. @everyone"
atm master log "onboarding: name=<name> integrations=<list>"
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
`atm create` per title. Do not invent extra tickets. Do not leave
blockers only in the ticket body.

```
atm objective "<their sentence>"
atm plan <<'EOF'
[{"key":"api","title":"Build REST API","role":"backend","deps":[]},
 {"key":"ui","title":"Build login UI","role":"frontend","deps":["api"]}]
EOF
atm graph
atm map
```

Mid-run (same loop — not a second planner product):

```
atm dep T-004 --after T-003
atm create "DB migration" --blocks T-002
atm create "Add rate limiting" --deps T-002
```

Follow-up every wake (master or CoS): `atm update` / `atm here`;
reopen silent >90m claims (`atm reopen`); `atm drive` toward the
objective; drain the review queue. Show `atm graph` / `atm map`.
If HEALTH flags prose-only deps, wire `atm dep` instead of leaving
them in the body.

Do not implement those tasks in this session. Spawning children when a
parent is done is a separate success-trigger, not this onboarding step.
Spawn seats only from the integrations they confirmed, one ticket each.

## COS ONBOARDING — future (do not run on a living board unless asked)

**You are onboarding as chief of staff.** Master plans and scopes. CoS
reviews, unblocks, merges, and staffs. Same integration catalog as master.
After the board has an objective and a `atm plan` graph:

1. `atm graph` / `atm map` — statuses and `--after` edges, not prose.
2. Follow-up: `atm update` / `here`; `atm reopen` silent >90m claims;
   `atm drive` toward the objective; review queue.
3. Mid-run graph edits: `atm dep` / `atm create --blocks`.
4. Announce with `atm master cos <name>` and `atm msg --to everyone`.

Do not dump a live-board plan. Do not invent a second planner.

## CEO ONBOARDING — living board (product flow)

**You are onboarding as Atman CEO.** Connecting is joining Atman, not a
provider. Identity is `atman-<seat>` (example `atman-ceo`). The current
CoS holder staffs (or no CoS yet). CEO does not claim worker tickets.

Run `atm connect` (or `atm connect --ceo`). It executes, in order:

1. Catalog + usage (`atm harness available` + recorded limits)
2. Attach the living board / objective — do not invent a new team
3. `atm join atman-<seat> --roles master ...`
4. Announce the Atman role (`atm msg --to everyone`)
5. Ask the operator for feedback
6. `atm graph` / `atm map` — tasks they can actually run

Do not `atm init` or `atm clear`. Do not one `atm create` per
title — `atm plan` with real deps if they add work. Spawn only the
harnesses they confirm. Mail hooks are not Claude-only:
`atm hooks cursor|codex|remote|claude --agent atman-<seat>`.

HANDOVER dated 2026-09-08 is historical, not live authority. Live:
`atm master`, `atm role list`, the message board, this section.

## Mission
(what we are building, one paragraph)

## Workforce
| agent (TICKET_AGENT) | tool | default roles | worktree |
|---|---|---|---|
| example | Claude Code | backend | .worktrees/example |

## Rules for every agent
1. Claim before you work (`atm next`). Never work without a ticket; never
   edit `.tickets/` by hand.
2. Finish what you claim. If you cannot, `atm block` with a reason or
   `atm reopen` -- do not go silent. Hold one ticket at a time.
3. You may create, split, re-wire and assign tickets (`create --blocks`,
   `dep`, `assign`, `plan`). Extending the graph is expected, not exceptional.
4. Work on your own git worktree and branch, never on main. Commit as you go.
   `atm done` refuses from main or with uncommitted files.
5. Post `atm update <id> "..."` at least every 45 minutes and at each
   milestone. Silence longer than that is treated as a timeout.
6. `done --notes` must include branch@sha (added automatically), the paths
   you touched, and every decision a dependent ticket must match. Then merge
   (or open the PR) before claiming the next ticket.
7. Set `TICKET_AGENT` to your own name so the board can tell agents apart.

## Sprint plan
(goals per sprint; `atm sprint list` has the live numbers)

## Decision log
(append with `atm master log "..."`)
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
    if sub == "take":
        owner = whoami(a.owner)
        prev = current_master(board)
        with open(master_state_path(board), "w") as f:
            json.dump({"owner": owner, "since": now()}, f)
        _master_log(board, "%s took over as master%s" % (
            owner, (" from %s" % prev["owner"]) if prev and prev.get("owner") != owner else ""))
        print("%s is master now. Run `atm master` for the briefing." % owner)
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
    me = whoami()
    if m:
        stale = hours_since(m["since"])
        print("current master: %s (for %s)%s" % (m["owner"], fmt_hours(stale),
              "  <- that is you" if m["owner"] == me else ""))
        if m["owner"] != me:
            print("TAKING OVER? Masters die on usage limits. Run `atm master take`, then in order: "
                  "`atm limits` (who is out), REVIEW QUEUE below (merge with `atm merge`), "
                  "HEALTH below, `atm who`. Everything decided so far is in the Decision log.")
    else:
        print("current master: nobody -- `atm master take` to become it, then follow HANDOVER in MASTER.md")
    print("=" * 72)
    if os.path.exists(path):
        with open(path) as f:
            print(f.read().rstrip())
    else:
        print("(no MASTER.md yet -- `atm master init`)")
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
        print("REVIEW QUEUE (%d) -- coordinator close is three distinct steps: "
              "`atm accept <id> --sha <exact>`, then `atm merge`, then `atm done <id>`:" % len(queue))
        for t in queue:
            print("  %s @%-12s %-46s %s  waiting %s%s" % (
                t["id"], t.get("owner", "?"), t["title"][:46],
                _rv.review_queue_pin(t),
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


def health(board, tickets):
    """Things a master must act on. Returns [(severity, message, fix)]."""
    out = []
    by_id = dict((t["id"], t) for t in tickets)
    done = set(t["id"] for t in tickets if t["status"] == "done")
    cyc = find_cycle(tickets)
    if cyc:
        out.append(("CRIT", "dependency cycle %s" % " -> ".join(cyc),
                    "atm dep %s --drop %s" % (cyc[0], cyc[1])))
    for tid, miss in dangling(tickets).items():
        out.append(("CRIT", "%s depends on non-existent %s" % (tid, ",".join(miss)),
                    "atm dep %s --drop %s" % (tid, ",".join(miss))))
    for t in tickets:
        if t["status"] != "claimed":
            continue
        tm = timing(t)
        su = tm["since_update"]
        if su is not None and su * 60 > UPDATE_EVERY_MIN * 2:
            out.append(("WARN", "%s (@%s) silent for %s -- likely timed out" % (
                t["id"], t.get("owner"), fmt_hours(su)),
                "atm reopen %s   # or ping the agent" % t["id"]))
        elif su is not None and su * 60 > UPDATE_EVERY_MIN:
            out.append(("INFO", "%s (@%s) no update for %s" % (t["id"], t.get("owner"), fmt_hours(su)),
                        "ask for `atm update %s`" % t["id"]))
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
                "atm dep %s --after %s && atm reopen %s" % (t["id"], ",".join(unwired), t["id"])
                if ready else "atm dep %s --after %s" % (t["id"], ",".join(unwired))))
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
                    "atm join <agent> --can %s   # e.g. grok, it has its own machine" % ",".join(missing)))
    for r in load_agents(board):
        lim = r.get("limit") or r.get("expired_limit")
        if lim and _route_headroom().limit_expiry(lim)[0]:
            out.append(("WARN", "%s stale usage limit no longer blocks: %s; check seat resumed" % (
                r["owner"], _route_headroom().limit_expiry(lim)[2]),
                "atm pending --agent %s" % r["owner"]))
            continue
        if r.get("limit"):
            held = [t["id"] for t in tickets if t["status"] == "claimed" and t.get("owner") == r["owner"]]
            out.append(("WARN", "%s hit a usage limit %s ago%s%s" % (
                r["owner"], fmt_hours(hours_since(r["limit"]["at"])),
                (", back %s" % r["limit"]["until"]) if r["limit"].get("until") else "",
                (" -- still holds %s" % ",".join(held)) if held else ""),
                ("atm reopen %s   # hand to someone else" % held[0]) if held else "atm limit %s --clear when back" % r["owner"]))
    for r in load_agents(board):
        if r.get("branch") in ("main", "master") and hours_since(r.get("seen", "")) < 24:
            out.append(("WARN", "%s is working on %s (rule 4)" % (r["owner"], r["branch"]),
                        "git worktree add .worktrees/%s -b %s-work" % (r["owner"], r["owner"])))
    ready_ids = set(t["id"] for t in unblocked(board, tickets))
    agents_by = dict((r.get("owner"), r) for r in load_agents(board))
    for t in tickets:
        who = _reserved_agent(t)
        if not who or t.get("status") != "open" or t["id"] not in ready_ids:
            continue
        rec = agents_by.get(who) or {}
        reason = ""
        if _route_headroom().seat_limit(rec):
            reason = "limited"
        elif not rec.get("seen"):
            reason = "no heartbeat"
        elif hours_since(rec.get("seen", "")) > 1.5:
            reason = "silent %s" % fmt_hours(hours_since(rec["seen"]))
        if reason:
            out.append(("WARN", "%s is READY and reserved for %s who is down/limited (%s)" % (
                t["id"], who, reason),
                "atm reserve %s --drop" % t["id"]))
    if not active_sprint(board):
        out.append(("INFO", "no active sprint", "atm sprint create \"goal\" --activate"))
    loose = [t["id"] for t in tickets if not t.get("epic") and t["status"] != "done"]
    if loose and load_epics(board):
        out.append(("INFO", "%d open tickets have no epic" % len(loose),
                    "atm assign <id> --epic E-xxx"))
    if not os.path.exists(master_path(board)):
        out.append(("WARN", "no MASTER.md -- nobody can take over cleanly", "atm master init"))
    return out


def cmd_reopen(a, board):
    t = load(board, a.id)
    _refuse_unreleased_deps(t, load_all(board), only_done=True)
    notes = getattr(a, "notes", "") or ""
    revision = bool(getattr(a, "revision", False))
    # T-394: silent reopen of IN REVIEW (or review_at leftover) returns the
    # ticket to `next` while notes still read as REVIEW. Claimed work that
    # never entered review stays reopenable without notes (T-246).
    if (t["status"] == "review" or t.get("review_at") or revision) and not notes.strip():
        sys.exit(
            'reopen of IN REVIEW work needs --notes "why" '
            "(silent reopen returns it to next and looks like a next-reissue bug)"
        )
    # T-1460: explicit revise request -- return to the author as claimed,
    # distinct from release-to-pool reopen (which clears owner → open).
    if revision:
        if t.get("status") != "review":
            sys.exit("--revision only applies while the ticket is IN REVIEW")
        prev_owner = (t.get("owner") or "").strip()
        if not prev_owner:
            sys.exit("%s has no owner to return the revision to" % a.id)
        actor = whoami(getattr(a, "by", ""))
        before = t["status"]
        _work_view().supersede_release_evidence(t)
        author = _rv.return_to_author_for_revision(
            t, actor=actor, reason=notes, sha="", kind="revision")
        t["owner"] = prev_owner
        tc = _recovery()
        if tc is not None:
            tc.issue_owner_lease(
                t, prev_owner, harness=_lease_harness(board, prev_owner),
                reason="revision-request", previous_owner=prev_owner)
            tc.rewrite_claim_lock(board, t["id"], prev_owner)
        save(board, t)
        traj_event(board, "reopen", agent=actor, ticket=t,
                   state_before=before, state_after="claimed",
                   outcome="revision", prev_owner=prev_owner,
                   notes_len=len(notes), **_traj_git())
        _bind_agent_ticket(board, prev_owner, t["id"])
        post_message(
            board, actor,
            "%s returned for revision: %s" % (t["id"], notes[:160]),
            to=prev_owner, re=t["id"], task=True)
        print("%s returned to %s as claimed for revision" % (a.id, author or prev_owner))
        return
    if notes:
        t["notes"].append({"by": whoami(getattr(a, "by", "")), "at": now(), "text": notes})
    before = t["status"]
    prev_owner = t.get("owner", "")
    _work_view().supersede_release_evidence(t)
    t["status"] = "open"
    t["owner"] = ""
    save(board, t)
    traj_event(board, "reopen", agent=whoami(getattr(a, "by", "")), ticket=t,
               state_before=before, state_after="open", outcome="reopened",
               prev_owner=prev_owner,
               notes_len=len(getattr(a, "notes", "") or ""), **_traj_git())
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
            "REFUSED: atm clear will not wipe a live board (missing .fixture-board).\n"
            "This command only deletes tickets on disposable fixture boards.\n"
            "Use a disposable fixture board for tests, or:\n"
            "  atm board-backup --out /tmp/board.tgz\n"
            "  atm board-restore --archive /tmp/board.tgz --dest /tmp/fixture\n"
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

    try:
        from .board_backup import backup
    except ImportError:
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

    try:
        from .board_backup import restore
    except ImportError:
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


def _read_jsonl(path):
    out = []
    try:
        with open(path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    out.append(json.loads(line))
                except ValueError:
                    continue
    except OSError:
        pass
    return out


def _shadow_board_summary(path):
    summary = {
        "path": path,
        "ticket_count": len(glob.glob(os.path.join(path, "T-*.json"))),
        "message_count": 0,
        "recipients": [],
        "agent_count": 0,
        "since": None,
        "until": None,
    }
    messages = _read_jsonl(os.path.join(path, "messages.jsonl"))
    summary["message_count"] = len(messages)
    recipients = set()
    times = []
    for m in messages:
        to = (m.get("to") or "").strip() if isinstance(m, dict) else ""
        if to:
            recipients.add(to)
        at = m.get("at") if isinstance(m, dict) else None
        if at:
            times.append(at)
    summary["recipients"] = sorted(recipients)
    if times:
        times.sort()
        summary["since"] = times[0]
        summary["until"] = times[-1]
    agents_dir = os.path.join(path, "agents")
    if os.path.isdir(agents_dir):
        try:
            summary["agent_count"] = len([n for n in os.listdir(agents_dir) if n.endswith(".json")])
        except OSError:
            pass
    return summary


def cmd_doctor(a):
    """Diagnose board resolution: what board is in effect, and any shadow
    boards nearby (T-959). Read-only."""
    env = os.environ.get("TICKETS_DIR")
    root = _repo_root()
    configured = _configured_shared_board(root) if root else None
    candidate = _board_dir_uncached()

    print("board resolution order: TICKETS_DIR > configured shared board > cwd .tickets (if primary or empty)")
    if env:
        print("  TICKETS_DIR is set: %s  <- in effect" % os.path.abspath(os.path.expanduser(env)))
    else:
        print("  TICKETS_DIR is not set")
    print("  repo root: %s" % (root or "(not inside a git worktree)"))
    print("  configured shared board: %s" % (configured or "none registered for this repo"))

    effective = candidate
    if not env and configured and os.path.realpath(configured) != os.path.realpath(candidate):
        if _is_marked_primary(candidate):
            print("  cwd .tickets is marked primary -- overrides the configured board")
        elif not _board_has_content(candidate):
            effective = configured
            print("  cwd .tickets is empty -- configured shared board is in effect")
        else:
            effective = configured
            print("  ** shadow board detected at %s -- NOT the configured board and NOT marked "
                  "primary. Normal commands refuse until this is resolved. **" % candidate)
    print("effective board: %s" % effective)

    shadows = []
    if root:
        local = os.path.join(root, ".tickets")
        if os.path.isdir(local) and os.path.realpath(local) != os.path.realpath(effective):
            shadows.append(os.path.abspath(local))
    for kid in child_boards(os.getcwd()):
        real_kid = os.path.realpath(kid)
        if real_kid != os.path.realpath(effective) and kid not in shadows:
            shadows.append(kid)

    if not shadows:
        print("\nno shadow boards found")
        return
    print("\nshadow boards found (read-only summary -- nothing modified):")
    for shadow in shadows:
        s = _shadow_board_summary(shadow)
        print("  %s" % shadow)
        print("    tickets: %d, messages: %d, agents: %d" % (
            s["ticket_count"], s["message_count"], s["agent_count"]))
        if s["since"]:
            print("    span: %s .. %s" % (s["since"], s["until"]))
        if s["recipients"]:
            shown = s["recipients"][:20]
            more = "" if len(s["recipients"]) <= 20 else " (+%d more)" % (len(s["recipients"]) - 20)
            print("    messages addressed to: %s%s" % (", ".join(shown), more))
        print("    fix: atm board-archive-shadow %s --yes   (moves it aside; never deletes)" % shadow)


def cmd_board_mark_primary(a):
    root = _repo_root()
    board = os.path.join(root, ".tickets") if root else os.path.join(os.getcwd(), ".tickets")
    if not os.path.isdir(board):
        sys.exit("no .tickets directory at %s -- nothing to mark" % board)
    marker = os.path.join(board, PRIMARY_BOARD_MARKER)
    with open(marker, "w", encoding="utf-8") as f:
        f.write("marked primary %s\n" % datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"))
    print("marked %s primary -- it now wins over any configured shared board for this repo" % board)


def _board_in_effect_for_archive():
    """Live board for archive-shadow, without refusing an unmarked shadow.

    Same order as board_dir: TICKETS_DIR, then a local .primary, then the
    configured shared board, then cwd .tickets.
    """
    env = os.environ.get("TICKETS_DIR")
    if env:
        return os.path.abspath(os.path.expanduser(env))
    candidate = _board_dir_uncached()
    if _is_marked_primary(candidate):
        return candidate
    root = _repo_root()
    configured = _configured_shared_board(root) if root else None
    if configured:
        return configured
    return candidate


def cmd_board_archive_shadow(a):
    path = os.path.abspath(os.path.expanduser(a.path))
    if not os.path.isdir(path):
        sys.exit("no such directory: %s" % path)
    effective = _board_in_effect_for_archive()
    if os.path.realpath(path) == os.path.realpath(effective):
        sys.exit(
            "REFUSING: %s IS the board currently in effect -- archiving it would "
            "archive the live board, not a shadow. Nothing was moved." % path
        )
    s = _shadow_board_summary(path)
    dest = "%s.archived-%s" % (path, datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ"))
    if not getattr(a, "yes", False):
        sys.exit(
            "DRY RUN -- pass --yes to actually move this aside (nothing changed):\n"
            "  shadow:  %s\n"
            "  tickets: %d, messages: %d, agents: %d\n"
            "  would move to: %s\n"
            % (path, s["ticket_count"], s["message_count"], s["agent_count"], dest)
        )
    os.rename(path, dest)
    print("archived shadow board %s -> %s (nothing deleted)" % (path, dest))



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
    print("%-14s %-8s %-34s %-22s %s" % ("agent", "seen", "branch@sha", "ticket", "worktree"))
    for r in sorted(agents, key=lambda r: r.get("seen", ""), reverse=True):
        tid = r.get("ticket") or ""
        t = tickets.get(tid)
        tdesc = ("%s %s" % (tid, MARK.get(t["status"], "")) if t else (tid or "-"))
        b = "%s@%s" % (r.get("branch") or "?", r.get("sha") or "?")
        if r.get("dirty"):
            b += " +%d" % r["dirty"]
        wt = r.get("worktree") or r.get("cwd") or ""
        home = os.path.expanduser("~")
        if wt.startswith(home):
            wt = "~" + wt[len(home):]
        print("%-14s %-8s %-34s %-22s %s" % (
            r["owner"][:14], fmt_hours(hours_since(r.get("seen"))) + " ago", b[:34], tdesc[:22], wt))
        if r.get("limit"):
            lim = r["limit"]
            print("%-14s !! USAGE LIMIT hit %s ago%s" % ("", fmt_hours(hours_since(lim["at"])),
                                                        (", back %s" % lim["until"]) if lim.get("until") else ""))
        if r.get("note"):
            print("%-14s %s" % ("", "\"%s\"" % r["note"][:90]))
    # collisions
    by_branch = {}
    for r in agents:
        if r.get("branch") and r["branch"] not in ("main", "master"):
            by_branch.setdefault(r["branch"], []).append(r["owner"])
    for b, os_ in by_branch.items():
        if len(set(os_)) > 1:
            print("!! %s share branch %s -- they will clobber each other" % (", ".join(sorted(set(os_))), b))
    on_main = [r["owner"] for r in agents if r.get("branch") in ("main", "master")]
    if on_main:
        print("!! on main/master: %s -- rule 4, move to a worktree" % ", ".join(on_main))


# ---- message board ------------------------------------------------------

def messages_path(board):
    return os.path.join(board, "messages.jsonl")


WEAK_SENDER_VIA = ("TICKET_AGENT", "flat", "pid")


def _sender_via(board, explicit=None):
    if explicit:
        return "explicit"
    if (os.environ.get("TICKET_SEAT") or "").strip():
        return "TICKET_SEAT"
    keyed = bool(agent_session_key())
    recorded = None
    if board:
        try:
            recorded = read_identity(board)
        except Exception:
            recorded = None
    if keyed and recorded:
        return "session-keyed"
    if (os.environ.get("TICKET_AGENT") or "").strip():
        return "TICKET_AGENT"
    if recorded:
        return "flat"
    return "pid"


def message_provenance_state(m):
    if not m or "via" not in m:
        return "absent"
    if m.get("unverified"):
        return "unverified"
    return "verified"


def post_message(board, sender, text, to="", re="", kind="", task=False, explicit=None):
    rec = {"at": now(), "from": sender, "to": to, "re": re, "text": text}
    via = _sender_via(board, explicit)
    rec["session"] = agent_session_key() or ""
    rec["via"] = via
    rec["endpoint_pid"] = ""
    rec["unverified"] = via in WEAK_SENDER_VIA
    if task or kind == "task":
        rec["kind"] = "task"
    line_ = json.dumps(rec) + "\n"
    # O_APPEND writes under PIPE_BUF are atomic, so concurrent posters never interleave
    fd = os.open(messages_path(board), os.O_CREAT | os.O_WRONLY | os.O_APPEND, 0o644)
    try:
        os.write(fd, line_.encode())
    finally:
        os.close(fd)
    # ids and a length, never the body (T-311 privacy rule). Best-effort: a
    # message that reached the board must not be undone by instrumentation.
    traj_event(board, "msg", agent=sender, ticket=re or None,
               to=to or "", text_len=len(text or ""))
    return rec


def load_messages(board):
    out = []
    try:
        with open(messages_path(board)) as f:
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


def _agent_rec(board, owner):
    path = os.path.join(agents_dir(board), owner + ".json")
    try:
        with open(path) as f:
            return json.load(f)
    except (IOError, ValueError):
        return {}


def _agent_set(board, owner, **fields):
    """Update fields on an agent record without touching the rest of it.

    T-428: must not invent cwd/branch/sha from the caller. Those belong on the
    caller's own record via checkin().
    """
    rec = _agent_rec(board, owner) or {"owner": owner}
    rec.update(fields)
    os.makedirs(agents_dir(board), exist_ok=True)
    path = os.path.join(agents_dir(board), owner + ".json")
    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        json.dump(rec, f, indent=2)
    os.replace(tmp, path)
    return rec


def _mark_inbox_read(board, owner, scan=None):
    rec = _agent_rec(board, owner)
    if not rec:
        rec = checkin(board, owner)
    if scan is None:
        scan = _inbox_scan(board, owner)
    rec["inbox_seen"] = scan[1]
    if scan[2]:
        rec["inbox_seen_ids"] = scan[2]
    else:
        rec.pop("inbox_seen_ids", None)
    os.makedirs(agents_dir(board), exist_ok=True)
    path = os.path.join(agents_dir(board), owner + ".json")
    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        json.dump(rec, f, indent=2)
    os.replace(tmp, path)


def _visible_after_join(msgs, owner, joined):
    """Hide BROADCAST history from before this agent existed -- never directed mail.

    Kept byte-identical to the root tickets.py copy on purpose: this file is the
    packaged entry point (pyproject: tickets = ticket_board.cli:main) and the two
    copies of the delivery path drifting apart is precisely how T-228 shipped a
    half-ported fix. If you change one, change both.
    """
    if not joined:
        return msgs  # every pre-existing agent: unchanged, by construction
    return [m for m in msgs if m.get("to") == owner or m.get("at", "") >= joined]


def _msg_id(m):
    """Stable, content-derived identity for a message (see T-228 in the root
    tickets.py, which carries the full rationale). Derived rather than stored
    so nothing already written to disk has to be migrated."""
    raw = json.dumps([m.get("at", ""), m.get("from", ""), m.get("to", ""),
                      m.get("re", ""), m.get("text", "")], sort_keys=True)
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:16]


def _seen_counts(seen_ids):
    counts = {}
    for k in seen_ids or []:
        counts[k] = counts.get(k, 0) + 1
    return counts


def _addressed(m, owner):
    """Would this message ever be shown to `owner`? (Own mail is never echoed.)"""
    return (m.get("from") != owner
            and (not m.get("to") or m.get("to") == owner or m.get("to") == "all"))


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
    very second an agent read its inbox. The obvious repair, `>=`, redelivers
    on every poll forever (a wake storm), so the ambiguous region is
    disambiguated by IDENTITY instead: anything at or ahead of the watermark is
    unread unless this agent has already been shown that specific message.

    "At or ahead" rather than "exactly at" is what makes a future-stamped
    record safe: it sits above the clamped watermark for the length of the
    skew, is delivered once, and is not redelivered on the next poll.
    """
    at = m.get("at", "")
    if not at or at < since:
        return False
    k = _msg_id(m)
    if remaining.get(k):
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

    Kept deliberately in step with the root tickets.py copy: this file is the
    packaged entry point (pyproject: tickets = ticket_board.cli:main), and
    T-228 clamped the root only, which is the drift T-329 exists to close.
    """
    rec = _agent_rec(board, owner)
    # T-329 union resolution across the T-522 orphan: new main brought T-327's
    # pre-join suppression into this function, and this branch brings T-228's
    # same-second clamp. Neither side is droppable, and the root tickets.py on
    # new main carries BOTH -- which is the shape this file exists to match.
    since = _seen_since(rec)
    # T-327's pre-join broadcast suppression is applied inside the scan, not
    # over its result, so the boundary-identity set below is drawn from the
    # same list the agent is actually shown. Kept in step with the root
    # tickets.py copy: the two delivery paths drifting apart is how T-228
    # shipped a half-ported fix in the first place.
    joined = rec.get("joined_at", "")
    seen_ids = rec.get("inbox_seen_ids") or []
    msgs = load_messages(board)
    remaining = _seen_counts(seen_ids)
    visible = _visible_after_join(
        [m for m in msgs if _addressed(m, owner)], owner, joined)
    out = [m for m in visible if _is_unread(m, since, remaining)]
    watermark = max([m.get("at", "") for m in msgs] or [""])
    ceiling = now()
    if watermark > ceiling:
        watermark = ceiling    # a future stamp is not something anyone observed
    if watermark < since:
        watermark = since      # nothing newer than the agent already knew
    if not watermark:
        watermark = now()
    # Identities are needed only where the timestamp alone cannot settle the
    # question -- at the watermark second, and above it while a future-stamped
    # record is waiting for the clock. Rebuilt from the file rather than
    # carried forward, because a message delivered on an EARLIER poll is no
    # longer in `out` and would otherwise lose its identity and be redelivered.
    retained = [_msg_id(m) for m in msgs
                if _addressed(m, owner) and m.get("at", "") >= watermark]
    return out, watermark, retained


def unread(board, owner):
    return _inbox_scan(board, owner)[0]


def fmt_msg(m):
    to = (" -> %s" % m["to"]) if m.get("to") and m["to"] != "all" else ""
    re_ = (" [%s]" % m["re"]) if m.get("re") else ""
    mark = ""
    if message_provenance_state(m) == "unverified":
        mark = " [unverified:%s]" % (m.get("via") or "?")
    if m.get("leadership_flag"):
        mark += " [leadership-flag:%s]" % m["leadership_flag"]
    return "%s  %s%s%s%s: %s" % (
        m["at"][5:16].replace("T", " "), m.get("from", "?"), mark, to, re_, m.get("text", ""))


def cmd_msg(a, board):
    sender = session_seat(board, a.owner)
    if a.re:
        load(board, a.re)  # validate the ticket exists
    m = post_message(board, sender, a.text, a.to or "", a.re or "",
                     explicit=a.owner or None)
    print("posted: " + fmt_msg(m))


def cmd_inbox(a, board):
    owner = session_seat(board, a.owner)
    if getattr(a, "quiet_if_unidentified", False) and not a.owner:
        if not seat_confirmed(board):
            return
    scan = None
    if a.all:
        msgs = load_messages(board)[-a.limit:]
        if not msgs:
            print("no messages yet (atm msg \"text\" [--to agent] [--re T-001])")
            return
        for m in msgs:
            print(fmt_msg(m))
    else:
        scan = _inbox_scan(board, owner)
        msgs = scan[0]
        if not msgs:
            print("inbox empty for %s (atm inbox --all for history)" % owner)
        else:
            print("%d unread for %s:" % (len(msgs), owner))
            for m in msgs:
                print("  " + fmt_msg(m))
    if not a.keep:
        _mark_inbox_read(board, owner, scan)


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


def _traj_summary(events):
    """The numbers this log exists for: turns-to-done per ticket."""
    from ticket_board.prices import (
        load_price_table, traj_summary_add_event, traj_summary_bucket,
    )
    table = load_price_table()
    by_ticket = {}
    for e in events:
        tid = e.get("ticket")
        if not tid:
            continue
        s = by_ticket.setdefault(tid, traj_summary_bucket())
        traj_summary_add_event(s, e, table=table)
    return by_ticket


def cmd_trajectories(a, board):
    """Read, export or backfill the trajectory log (packaged CLI)."""
    sub = getattr(a, "traj_cmd", "list") or "list"
    if sub == "backfill":
        return _traj_backfill(a, board)
    events = _traj_safe(lambda: _traj.load(board)) or []
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
        print("no trajectory events yet (%s)" % _traj.trajectories_path(board))
        print("the log fills as agents claim, update, review and run; "
              "`atm trajectories backfill` synthesises the history already on the board")
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
            from ticket_board.prices import traj_summary_cost_cell
            cost = traj_summary_cost_cell(s)
            print("%-8s %5d %8s %5d %5d %8d %10s  %s %s" % (
                tid, s["runs"], (s["turns"] or "-"), s["updates"], s["msgs"],
                s["reopens"], cost, ",".join(sorted(s["agents"])) or "-",
                ("-> " + s["outcome"]) if s["outcome"] else ""))
        print("")
        print("runs = watch runs that reached run_end (the board's own turn count).  "
              "turns = turns the harness itself reported, '-' when it reported none "
              "-- an unreported count is never estimated from the run.")


def _traj_backfill(a, board):
    """Same synthesis rules as tickets.py -- packaged CLI must not skip this."""
    ONCE_PER_TICKET = ("claim", "review", "done")
    existing = _traj_safe(lambda: _traj.load(board)) or []
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
                continue
            plan(t, "update", n.get("at", ""), n.get("by", "") or owner,
                 notes_len=len(text))
        plan(t, "review", t.get("review_at", ""), owner,
             state_before="claimed", state_after="review", outcome="review",
             pin=t.get("commit", ""))
        if t.get("done_at"):
            plan(t, "done", t["done_at"], owner,
                 state_before="review" if t.get("review_at") else "claimed",
                 state_after="done", outcome="done", pin=t.get("commit", ""))
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
        rec = traj_event(board, kind, agent=agent, ticket=t, at=at,
                         src="backfill", backfilled_at=ran_at,
                         bf_key="%s:%s:%s" % (t["id"], kind, at), **fields)
        if rec:
            written += 1
    print("backfill wrote %d event(s) into %s" % (written, _traj.trajectories_path(board)))
    if not planned:
        print("nothing to synthesise: every claim/update/review/done on this board "
              "is already in the log")


def _turns_cmd():
    try:
        from ticket_board.turns import cmd_turns as impl
        return impl
    except ImportError:
        from turns import cmd_turns as impl
        return impl


def cmd_turns(a, board):
    """T-312: table / --json of watch-run turns per ticket. See docs/turns.md."""
    return _turns_cmd()(a, board, load_all, load_workforce, load_messages)


def _trace_cmd():
    try:
        from .trace import cmd_trace as impl
        return impl
    except ImportError:
        from trace import cmd_trace as impl
        return impl


def cmd_trace(a, board):
    """T-1051: one greppable timeline for an objective or ticket."""
    return _trace_cmd()(a, board)


def _scheduler_cmd():
    try:
        from ticket_board.scheduler import cmd_route_shadow as impl
        return impl
    except ImportError:
        from scheduler import cmd_route_shadow as impl
        return impl


# ---- routing: which agent should take which open ticket -----------------

def _route_headroom():
    try:
        from ticket_board import route_headroom as m
        return m
    except ImportError:
        import route_headroom as m
        return m


def _route_seat_limit(board, name):
    """Shared T-1041 limit check (route_headroom.seat_limit)."""
    return _route_headroom().seat_limit(_agent_rec(board, name))


def _refuse_limited_seat(board, seat, verb):
    lim = _route_seat_limit(board, seat)
    if not lim:
        return
    sys.exit("%s: %s -- not dispatching" % (
        verb, _route_headroom().limit_label(lim)))


# Same harnesses tickets.py `_auth_gates_spawn` preflights.
_ROUTE_AUTH_GATED = ("claude", "codex", "cursor", "cursor+claude")


def _route_logged_out(board, wf, name):
    """T-1043 route_skip for one seat: confirmed logged-out reason, or empty.

    The packaged CLI has no auth probe, so it honours the auth_check that
    tickets.py stored (`atm harness auth`, dispatch/route preflight) for the
    seat's harness. A fresh positive preflight wins; no record keeps the seat.
    """
    try:
        from ticket_board import preflight as pf
    except ImportError:
        import preflight as pf
    e = wf.get(name, {}) or {}
    hid = (e.get("harness") or e.get("tool") or "").strip()
    if hid not in _ROUTE_AUTH_GATED:
        return ""
    rec = _agent_rec(board, name) or {}
    if pf.cached_positive(rec, now(), harness=hid):
        return ""
    auth = rec.get("auth_check")
    if not isinstance(auth, dict) or (auth.get("harness") or "") != hid:
        return ""
    return pf.route_skip(auth, hid)


def _write_route_pick(board, t, rows, deps_done):
    """Apply pick_seat: suggest or reserve. All-limited is reported, never stored."""
    best, why = _route_headroom().pick_seat(rows)
    if not best:
        return None, why
    if deps_done:
        t["suggested"] = best
    else:
        t["reserved_for"] = best
        why = ("reserved  " + why).strip()
    t.setdefault("notes", []).append({"by": whoami(), "at": now(), "text": why})
    save(board, t)
    return best, why


def _cmd_next_dispatch(a, board):
    from ticket_board.scheduler import (
        DEFAULT_ALIVE_WITHIN_MIN, filter_eligible, _candidate_names)
    from ticket_board.sounding import ticket_lane
    tickets = load_all(board)
    wf = load_workforce(board)
    roles = load_roles(board)
    agents = dict((r["owner"], r) for r in load_agents(board))
    done = set(t["id"] for t in tickets if t["status"] == "done")
    load_ = {}
    for t in tickets:
        if t["status"] == "claimed":
            load_[t.get("owner")] = load_.get(t.get("owner"), 0) + 1
    raw_names = _candidate_names(wf, roles, only=getattr(a, "only", None))
    limited, agents = _route_headroom().split_limited(
        raw_names, agents, lambda n: _route_seat_limit(board, n))
    names, _excluded = filter_eligible(
        raw_names, wf, roles, agents, load_, DEFAULT_ROLES,
        alive_within_min=DEFAULT_ALIVE_WITHIN_MIN)
    limited = _route_headroom().eligible_limited(
        limited, agents, lambda ns, ag: filter_eligible(
            ns, wf, roles, ag, load_, DEFAULT_ROLES,
            alive_within_min=DEFAULT_ALIVE_WITHIN_MIN))
    names, limited, logged_out = _route_headroom().drop_logged_out(
        names, limited, lambda n: _route_logged_out(board, wf, n))
    if logged_out:
        print(_route_headroom().format_logged_out(logged_out))

    def _deps_done(t):
        return all(d in done for d in t.get("deps", []))

    ready = [t for t in tickets
             if t["status"] == "open"
             and not _ticket_on_hold(t)
             and ticket_lane(t) == "ready"
             and _deps_done(t)
             and not _reserved_agent(t)]
    ready.sort(key=lambda t: (t.get("priority", 2), t["id"]))
    if not ready:
        print("next --dispatch: no ready ticket")
        sys.exit(1)
    rank_load = _route_headroom().seat_load(tickets, load_)
    held = False
    for t in ready:
        # An all-limited or nobody-fits ticket is reported, never stored, and
        # must not block the tickets behind it.
        rows = _route_headroom().candidate_rows(
            board, t, names, limited, wf, roles, rank_load, score_agent)
        best, why = _write_route_pick(board, t, rows, False)
        if best:
            print("%s reserved for %s (%s)" % (t["id"], best, why))
            return
        held = held or why.startswith("HOLD:")
        print("%s %s" % (t["id"], why or "(nobody fits)"))
    sys.exit(0 if held else 1)


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
    capabilities and cost. Writes `suggested`; `atm next` honours it.
    Agents still pull -- this is a hint, not a lock -- unless --claim.

    `--shadow` (T-315) is print-only plus one `shadow_decision` event per
    ready ticket. `--apply` is unimplemented.
    """
    if getattr(a, "apply", False) or getattr(a, "shadow", False) or getattr(a, "report", False) or getattr(a, "score", False):
        return _scheduler_cmd()(
            a, board, load_all, load_workforce, load_roles, load_agents,
            score_agent, traj_event, DEFAULT_ROLES)
    from ticket_board.scheduler import (
        DEFAULT_ALIVE_WITHIN_MIN, filter_eligible, format_excluded, _candidate_names)
    from ticket_board.sounding import ticket_lane
    tickets = load_all(board)
    wf = load_workforce(board)
    roles = load_roles(board)
    agents = dict((r["owner"], r) for r in load_agents(board))
    alive_within = int(getattr(a, "alive_within", None) or DEFAULT_ALIVE_WITHIN_MIN)
    released = _work_view().released_ids(tickets)
    load_ = {}
    for t in tickets:
        if t["status"] == "claimed":
            load_[t.get("owner")] = load_.get(t.get("owner"), 0) + 1
    raw_names = _candidate_names(wf, roles, only=a.only)
    limited, agents = _route_headroom().split_limited(
        raw_names, agents, lambda n: _route_seat_limit(board, n))
    names, excluded = filter_eligible(
        raw_names, wf, roles, agents, load_, DEFAULT_ROLES,
        alive_within_min=alive_within)
    limited = _route_headroom().eligible_limited(
        limited, agents, lambda ns, ag: filter_eligible(
            ns, wf, roles, ag, load_, DEFAULT_ROLES, alive_within_min=alive_within))
    names, limited, logged_out = _route_headroom().drop_logged_out(
        names, limited, lambda n: _route_logged_out(board, wf, n))
    print(format_excluded(excluded))
    if logged_out:
        print(_route_headroom().format_logged_out(logged_out))
    def _deps_done(t):
        return all(d in released for d in t.get("deps", []))

    def _needs_route(t):
        if _work_view().unreleased_dep_id(t, tickets, only_done=True):
            return False
        if t["status"] != "open":
            return False
        if _ticket_on_hold(t):
            return False
        if ticket_lane(t) != "ready":
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
    rank_load = _route_headroom().seat_load(tickets, load_)
    for t in ready_first:
        own_load = _route_headroom().without_own_reservation(rank_load, t)
        rows = _route_headroom().candidate_rows(
            board, t, names, limited, wf, roles, own_load, score_agent)
        best, why = _write_route_pick(board, t, rows, _deps_done(t))
        if best:
            changed += 1
            if not _deps_done(t):
                rank_load = dict(own_load)  # the new reservation replaces the old one
            rank_load[best] = rank_load.get(best, 0) + 0.5  # soft-count suggestions too
            if a.claim and t["status"] == "open" and _deps_done(t):
                got = try_claim(board, t["id"], best)
                if got:
                    t = got
                    why += "  CLAIMED"
        label = best or ("(all limited)" if why.startswith("HOLD:") else "(nobody fits)")
        print("%-6s %-3s %-44s %-14s %s" % (t["id"], t.get("priority", 2), t["title"][:44],
                                          label, why))
    if changed:
        _master_log(board, "route: suggested owners for %d tickets%s" % (changed, " and claimed ready ones" if a.claim else ""))
    print("\nAgents pull with `atm next`; their suggested tickets come first. "
          "Dep-blocked tickets get reserved_for instead of a note. "
          "`atm route --claim` hard-assigns the ready ones.")


# ---- onboarding ---------------------------------------------------------

CONNECT = """## Connecting an agent to this board

Paste this at the start of ANY agent session (Claude Code, Codex, Cursor,
Grok, a human shell). Replace the name and roles.

    export TICKET_AGENT=claude-opus          # unique per agent, never reuse
    cd {root}
    atm join $TICKET_AGENT --roles backend   # registers you, prints the loop
    atm master                            # read the briefing first
    atm inbox                             # anything addressed to you
    atm next                              # claim work; prints handoffs

`join` without `--harness` prints `harness=claude (default)`: a label on the
agent record, not a running process. Claude is not started until `atm watch`
or `atm spawn`. Dry BYOA is join then `atm next` in your own shell;
`--harness custom --cmd '...'` is for when a watcher should run your harness
(docs/byoa.md).

Then the loop, until `atm next` says nothing is ready:

    # work on your own worktree (join prints the exact command)
    atm update <id> "what changed, what is next"     # every {every} min
    atm msg "..." --to <agent> --re <id>             # questions, blockers
    git add -A && git commit -m "..."                    # commit as you go
    atm review <id> --notes "paths, tests, decisions" # reviewable SHA; refuses on main / dirty
    # human review is the gate; then:
    atm next

Plan dependent work with `atm plan` so JSON `deps` become real `--after`
edges (`atm graph` to inspect). Unattended persist ends at that reviewable
SHA. Merge is not silent auto-promote.

Tool-specific:
- Claude Code: `TICKET_AGENT=claude-opus claude` -- the global SessionStart hook
  shows the board automatically; `.tickets/CONTEXT.md` is offered on each claim.
- Codex: reads AGENTS.md in the repo root (installed by `atm init`);
  launch with `TICKET_AGENT=codex codex`.
- Cursor: reads AGENTS.md and .cursor/rules/tickets.mdc; set TICKET_AGENT in
  the terminal you start it from, or pass `--owner cursor` on each command.
- Anything else that can run a shell: the same commands work; `tickets` is one
  stdlib Python file at ~/.claude/tools/tickets.py.

To take coordination: `atm master take`, then `atm master` and act on
the HEALTH section.
"""


def _identity_harness_key(spec):
    spec = (spec or "").strip()
    if spec.startswith("custom:"):
        return "custom"
    return spec


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
    return (
        "refusing: %s is bound to %s (auth/session/runner). "
        "Join a unique provider-specific agent id and bind it with --alias ceo|cos, "
        "or pass --transfer to audit handover of this name. "
        "Historical aliases: atm retire <name>."
        % (owner, prev)
    )


def _strip_identity_bound_state(board, owner):
    rec = _agent_rec(board, owner)
    if rec:
        for key in ("auth_check", "runner_context", "limit", "adapter_failure",
                    "auth_resume_at", "harness_check"):
            rec.pop(key, None)
        rec["ticket"] = ""
        os.makedirs(agents_dir(board), exist_ok=True)
        path = os.path.join(agents_dir(board), owner + ".json")
        tmp = path + ".tmp"
        with open(tmp, "w") as f:
            json.dump(rec, f, indent=2)
        os.replace(tmp, path)
    try:
        from session_adapters import remove_endpoint
        remove_endpoint(board, owner)
    except Exception:
        pass


def _guard_seat_identity(board, owner, incoming_harness, transfer=False, alias=""):
    alias = (alias or "").strip().lower()
    if alias and alias not in ("ceo", "cos"):
        sys.exit("--alias must be one of: ceo, cos")
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


def cmd_join(a, board):
    _refuse_join_tickets_dir_shadow(board)
    owner = a.name or whoami()
    if owner.startswith("agent-"):
        sys.exit("give yourself a real name: atm join <name> --roles ...")
    incoming = (getattr(a, "harness", "") or a.tool or "").strip()
    _guard_seat_identity(
        board, owner, incoming,
        transfer=bool(getattr(a, "transfer", False)),
        alias=(getattr(a, "alias", "") or "").strip())
    if not getattr(a, "transfer", False) and owner != whoami():
        m = current_master(board) or {}
        leaders = set(n for n in (m.get("owner"), m.get("cos")) if n)
        for alias, holder in (load_aliases(board) or {}).items():
            if (alias or "").strip().lower() in ("ceo", "cos") and holder:
                leaders.add(holder)
        if owner in leaders:
            sys.exit("refusing: %s holds or held a leadership role; pass --transfer "
                     "for an audited handover" % owner)
    # AFTER the guard, never before it: a refused join must leave this session
    # answering as whoever it already was. Stamping first made `join alpha` --
    # refused for provider reuse or a bound alias -- still turn this session
    # into alpha, so a bare `atm inbox` read alpha's private mail and a
    # bare `atm msg` posted as alpha. Nothing below can sys.exit.
    #
    # T-954: join on behalf of another seat must not write THIS session.
    on_behalf = bool(getattr(a, "on_behalf", False))
    if _join_binds_this_session(board, owner, on_behalf=on_behalf):
        write_identity(board, owner)
    else:
        seat, why = identity_resolution(board)
        print("session identity unchanged (%s via %s); joined %s on behalf" % (
            seat, why, owner))
    # Before checkin(), which creates the record: only a genuinely new agent is
    # stamped, so a re-join never moves the watermark over unread mail.
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
    harness = _identity_harness_key(getattr(a, "harness", "") or a.tool)
    if harness:
        entry["harness"] = harness
        entry["tool"] = harness
        entry["provider"] = harness
    elif a.tool:
        entry["tool"] = a.tool
    if a.model:
        entry["model"] = a.model
    if a.can is not None:
        entry["can"] = sorted(set([c.strip() for c in a.can.split(",") if c.strip()]))
    if a.cost:
        entry["cost"] = a.cost
    if a.best_for:
        entry["best_for"] = a.best_for
    entry.setdefault("can", [])
    entry.setdefault("cost", "medium")
    entry["agent_id"] = owner
    alias = (getattr(a, "alias", "") or "").strip().lower()
    if alias:
        aliases = load_aliases(board)
        aliases[alias] = owner
        save_aliases(board, aliases)
        entry["role_alias"] = alias
    wf[owner] = entry
    save_workforce(board, wf)
    rec = checkin(board, owner, None, "joined" + (" (%s)" % a.tool if a.tool else ""),
                  cwd=os.path.abspath(getattr(a, "worktree", "") or "") or None)
    if first_join:
        jrec = _agent_rec(board, owner)
        jrec.setdefault("joined_at", now())
        os.makedirs(agents_dir(board), exist_ok=True)
        jpath = os.path.join(agents_dir(board), owner + ".json")
        with open(jpath + ".tmp", "w") as f:
            json.dump(jrec, f, indent=2)
        os.replace(jpath + ".tmp", jpath)
    post_message(board, owner, "joined the board%s; roles=%s; at %s [%s]" % (
        (" via %s" % a.tool) if a.tool else "", roles.get(owner, DEFAULT_ROLES.get(owner, [])),
        rec["worktree"] or rec["cwd"], rec["branch"] or "?"))
    root = os.path.dirname(board)
    print("joined as %s  roles=%s  can=%s  cost=%s" % (
        owner, roles.get(owner, DEFAULT_ROLES.get(owner, "any")), entry["can"] or "-", entry["cost"]))
    print("board: %s" % board)
    m = current_master(board)
    print("master: %s" % (m["owner"] if m else "nobody -- `atm master take` if you are it"))
    n = len(unread(board, owner))
    if n:
        print("inbox: %d unread (atm inbox)" % n)
    warn = worktree_warning(owner)
    print("")
    if warn:
        print(warn)
    else:
        g = git_state()
        if g:
            print("working tree OK: %s @ %s" % (g["branch"], g["top"]))
    print("")
    print("Loop:  atm master  ->  atm next  ->  work + commit  ->  "
          "atm update <id> \"...\" (every %d min)  ->  atm review <id> --notes \"<exact artifact>\"  "
          "->  atm next" % UPDATE_EVERY_MIN)
    print("Workers stop at `atm review` with an exact artifact. Coordinator close is `atm accept`, then `atm merge`, then `atm done`.")
    print("Full instructions: atm connect")
    if not os.path.exists(os.path.join(root, "AGENTS.md")):
        print("(no AGENTS.md here -- run `atm init` once so Codex/Cursor see the rules)")


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
    if ceo or (board_is_atman_operator(board) and not worker):
        name = atman_seat_name(seat)
        sys.stdout.write(CEO_ONBOARDING_STARTUP)
        if not CEO_ONBOARDING_STARTUP.endswith("\n"):
            sys.stdout.write("\n")
        print("")
        print("Product flow: catalog + usage → living board → %s → announce → feedback → graph/map" % name)
        print("Do not invent a new team. CEO does not claim worker tickets. The current CoS holder staffs (or no CoS yet).")
        print("Then probe: `atm harness available`")
        print("Join: atm join %s --roles master --persistent --wake-mode continuous" % name)
        print("Announce Atman role, ask for feedback, then atm graph / atm map.")
        return
    print_onboarding_startup()
    print("Then probe integrations: `atm harness available`")
    print("It auto-checks usage; unsupported or missing remaining/reset is unknown, not exhausted.")
    print("Ask which to integrate; do not spawn until they answer.")
    print("Announce the board/team name with `atm msg --to everyone`, then ask")
    print("for the objective and tasks. Turn tasks into a graph with `atm plan`")
    print("(JSON keys + deps), then `atm graph` / `atm map`. Follow up with")
    print("`atm update` / `here`, reopen silent >90m claims, `atm drive`.")
    print("Unattended persist ends at a reviewable SHA; human `atm review` is the gate.")
    print("")
    print(CONNECT.format(root=os.path.dirname(board), every=UPDATE_EVERY_MIN))


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
        print("bound: `atm` run from %s resolves to this board "
              "(`tickets` is a compatibility alias)." % os.getcwd())
    elif explicit:
        print("\nNOT BOUND: you asked for --board %s, but `tickets` run from %s "
              "still resolves to %s.\n  To use the board you just wrote:  "
              "export TICKETS_DIR=%s" % (board, os.getcwd(), bound, board))
    else:
        sys.exit(
            "INIT WROTE THE BOARD BUT IT IS NOT BOUND: wrote %s, yet `tickets` "
            "from %s still resolves to %s. Refusing to report success (T-263)."
            % (board, os.getcwd(), bound))
    print("\nClaude Code picks this up from its global SessionStart hook.")
    print("Codex and Cursor read AGENTS.md; Cursor also gets .cursor/rules/tickets.mdc.")


PROTOCOL = """## Shared ticket board

Work here is coordinated through a ticket board that Claude Code, Codex and
Cursor all share. It lives in `.tickets/` and is driven only through the
Atman CLI -- never edit files in `.tickets/` by hand, or atomic claiming
breaks and two agents will do the same work. The primary public name is `atm`;
`tickets` is a compatibility alias for the same implementation, arguments,
exit codes, and board.

Run `atm board` for the current state, or `atm graph` to see the whole
dependency tree with each node's status and owner.

**Connect first** (once per session; `atm connect` prints the long form):

    export TICKET_AGENT=<your-unique-name>     # claude-opus, codex, grok ...
    atm join $TICKET_AGENT --roles backend --can docker,browser --cost high
    # omit --harness: prints harness=claude (default); label only until watch/spawn
    atm master                             # briefing + health
    atm inbox                              # messages addressed to you

**Rules for every agent**

1. Claim before you work (`atm next`). Never work without a ticket; never
   edit `.tickets/` by hand. Hold one ticket at a time.
2. Finish what you claim. If you cannot, `atm block --reason` or
   `atm reopen` -- never go silent.
3. You may create, split, re-wire and assign tickets. Extending the graph is
   expected. Anyone can become master with `atm master take`.
4. Work on your own git worktree and branch, never on main. Commit as you go.
   `atm review` and `atm done` refuse from main or with uncommitted files.
5. Post `atm update <id> "..."` at least every 45 minutes and at every
   milestone. Longer silence is treated as a timeout and the ticket may be
   reopened for someone else.
6. When finished, submit -- do not close: `atm review <id> --notes "paths
   touched, tests run, decisions dependents must match"` (branch@sha is added
   automatically; `--pr N` if you opened one). Another seat then reviews it and
   records `atm accept <id> --sha <exact sha>`; that accept -- not `atm done` --
   is what releases the dependents. Claim your next ticket right away.
7. Tickets can declare `needs` (docker, browser, own-machine, gpu ...). You only
   receive tickets whose needs you registered with `--can`. Expensive agents
   are steered to priority-1 work, cheap agents to routine work.

Statuses: TO DO -> IN PROGRESS -> IN REVIEW -> DONE, or BLOCKED. Set them with
`atm status <id> todo|in-progress|review|blocked|done` or the dedicated
commands below; `atm list` shows the label on every line.

**The loop**

    atm next                          # claims -> IN PROGRESS; prints handoffs + timing
    atm update T-002 "..."            # progress, every 45 min
    atm msg "question" --to claude-opus --re T-002
    atm review T-002 --notes "..."    # -> IN REVIEW; master is messaged
    atm who                           # where everyone is: worktree, branch, ticket

`atm next` prints the ticket body, briefing paths, every note on direct
dependencies, and the latest note on earlier ancestors. Only one agent can
ever hold a ticket.

**Epics and sprints.** Tickets carry `epic` (E-001) and `sprint` (S-01).
`atm next` prefers the active sprint. `atm epic list` / `atm sprint show`
give progress bars; `atm sprint close S-01 --carry S-02`
rolls unfinished work forward.

The `--notes` text on `review`/`accept`/`done` is shown to whoever picks up a
dependent ticket, together with the accepted sha.
Write what the next agent needs -- file paths, names, decisions they must match
-- not a summary of your effort.

**As the planner**, create the whole dependency graph in one shot. `deps` may
reference a `key` from the same plan or an existing `T-` id:

    atm plan <<'EOF'
    [{"key":"api","title":"Build REST API","role":"backend","body":"details","deps":[]},
     {"key":"ui","title":"Build login UI","role":"frontend","deps":["api"]}]
    EOF

Tickets whose dependencies are unfinished stay invisible to `atm next`, and a
finished dependency stays withheld until a DIFFERENT seat accepts its exact sha
(`atm accept <id> --sha <sha>`), so nobody -- human or agent -- can release
their own work. Checkout `atm quickstart --gate` demonstrates that (this packaged copy has no quickstart).

**Adding work to a graph that already exists.** Any agent can extend the graph
mid-run -- this is normal, not a last resort:

    atm create "Add rate limiting" --deps T-002
    atm create "DB migration" --blocks T-002
    atm dep T-004 --after T-003
    atm dep T-004 --drop T-003
"""

CURSOR_RULE = """---
description: Shared ticket board used to coordinate work with other AI agents
alwaysApply: true
---

""" + PROTOCOL


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
        files = release["files"]
        if "tickets.py" not in files:
            return ""
        if any(name.startswith("src/") for name in files):
            for required in (
                "src/ticket_board/ticket_coordination.py",
                "src/ticket_board/board_backup.py",
            ):
                if required not in files:
                    return ""
        for name in files:
            rel = name.replace("\\", "/")
            if (not rel or rel.startswith("/") or ".." in rel.split("/")
                    or os.path.normpath(rel) != rel):
                return ""
            path = os.path.join(root, rel)
            recorded = files[name]
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


def main():
    p = argparse.ArgumentParser(prog=cli_prog(), description=__doc__.split("\n")[0])
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
    x = ms.add_parser("init"); x.add_argument("--force", action="store_true")
    c.set_defaults(fn=cmd_master, master_cmd="brief", owner=None, force=False)

    c = sub.add_parser("join", help="register this agent: name, roles, capabilities, cost")
    c.add_argument("name", nargs="?", default="")
    c.add_argument("--roles", default=None, help="backend,console")
    c.add_argument("--can", default=None, help="docker,browser,own-machine,gpu")
    c.add_argument("--cost", choices=("low", "medium", "high"), default=None)
    c.add_argument("--tool", default="", help="claude|codex|cursor|grok")
    c.add_argument("--harness", default="", help="same field as --tool")
    c.add_argument("--model", default="", help="e.g. opus, sonnet, gpt-5, grok-4")
    c.add_argument("--best-for", default="", help="free text; keywords are matched against ticket titles by `route`")
    c.add_argument("--alias", default="", help="stable role alias (ceo or cos) pointing at this unique runtime identity")
    c.add_argument("--transfer", action="store_true",
                   help="audited handover when this name's provider/session/runner identity changes")
    c.set_defaults(fn=cmd_join)

    c = sub.add_parser("retire", help="remove a seat from the board (inverse of join)")
    c.add_argument("name", nargs="?", default="")
    c.add_argument("--owner", "-o", default="", help="who is performing the retire (default: TICKET_AGENT)")
    c.set_defaults(fn=cmd_retire)

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
                   help="worker claim loop (prints atm next)")
    c.add_argument("--seat", default="ceo",
                   help="board identity suffix; join as atman-<seat> (default: ceo)")
    c.set_defaults(fn=cmd_connect)

    c = sub.add_parser("here", help="check in: record my worktree, branch and ticket")
    c.add_argument("--owner", "-o")
    c.add_argument("--note", default="")
    c.set_defaults(fn=cmd_here)

    c = sub.add_parser("who", help="where every agent is working")
    c.set_defaults(fn=cmd_who)

    c = sub.add_parser("msg", help="post to the message board")
    c.add_argument("text")
    c.add_argument("--to", default="", help="agent name, or omit for everyone")
    c.add_argument("--re", default="", help="ticket id this is about")
    c.add_argument("--owner", "-o")
    c.set_defaults(fn=cmd_msg)

    c = sub.add_parser("inbox", help="unread messages for me")
    c.add_argument("--all", action="store_true", help="full history")
    c.add_argument("--limit", type=int, default=40)
    c.add_argument("--keep", action="store_true", help="do not mark as read")
    c.add_argument("--quiet-if-unidentified", action="store_true",
                   help="print nothing when this session has no recorded seat")
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

    c = sub.add_parser("trace",
                       help="one greppable timeline for an objective or ticket")
    c.add_argument("target", nargs="?", default="",
                   help="ticket id (T-001) or objective text/id; omit for the standing objective")
    c.add_argument("--json", action="store_true", dest="json",
                   help="machine-readable event list")
    c.set_defaults(fn=cmd_trace)

    c = sub.add_parser("review", help="submit finished work for the master to review + merge")
    c.add_argument("id")
    c.add_argument("--notes", "-n", default="", help="what to look at: paths, tests run, decisions")
    c.add_argument("--pr", default="", help="PR number or URL if you opened one")
    c.add_argument("--owner", "-o")
    c.add_argument("--force", action="store_true")
    c.set_defaults(fn=cmd_review)

    c = sub.add_parser("accept", help="record a structured accept of the exact submitted SHA")
    c.add_argument("id")
    c.add_argument("--sha", required=True, help="full 40-character git SHA of the submitted review head")
    c.add_argument("--notes", "-n", required=True, help="why this artifact is accepted")
    c.set_defaults(fn=cmd_accept)

    c = sub.add_parser("reject", help="reject the submitted SHA and return the ticket to its author as claimed")
    c.add_argument("id")
    c.add_argument("--sha", required=True, help="git SHA of the submitted review head")
    c.add_argument("--reason", required=True, help="why this artifact is rejected")
    c.set_defaults(fn=cmd_reject)

    c = sub.add_parser("sync", help="agent: merge main into my branch now (do this before review)")
    c.add_argument("--force", action="store_true")
    c.set_defaults(fn=cmd_sync)

    c = sub.add_parser("merge", help="master: integrate pinned review SHAs -> test -> FF main -> close by ancestry")
    c.add_argument("branches", nargs="*", help="branches to merge; default = branches in the review queue")
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

    c = sub.add_parser("limits", help="who is limited: records, silence, and local tool logs")
    c.add_argument("--hours", type=int, default=24)
    c.set_defaults(fn=cmd_limits)

    c = sub.add_parser("status", help="set status: todo | in-progress | review | blocked | done")
    c.add_argument("id")
    c.add_argument("status")
    c.add_argument("--notes", "-n", default="")
    c.add_argument("--owner", "-o")
    c.add_argument("--pr", default="")
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

    c = sub.add_parser("plan", help="bulk-create tickets from JSON on stdin")
    c.set_defaults(fn=cmd_plan)

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
    c.add_argument("--dispatch", action="store_true",
                   help="CoS: reserve the next ready ticket for the best non-limited seat")
    c.set_defaults(fn=cmd_next)

    c = sub.add_parser("claim", help="atomically claim a specific ticket")
    c.add_argument("id")
    c.add_argument("--owner", "-o")
    c.add_argument("--another", action="store_true", help="claim even though I already hold one")
    c.set_defaults(fn=cmd_claim)

    c = sub.add_parser("done", help="mark a ticket done")
    c.add_argument("id")
    c.add_argument("--notes", "-n", default="", help="handoff text for dependent tickets")
    c.add_argument("--no-notes", action="store_true", help="allow empty handoff")
    c.add_argument("--force", action="store_true", help="skip the branch/clean-tree rule")
    c.add_argument("--release-unverified", action="store_true",
                   help="operator override: release dependents without ACCEPT (recorded, never silent)")
    c.set_defaults(fn=cmd_done)

    c = sub.add_parser("block", help="mark a ticket blocked")
    c.add_argument("id")
    c.add_argument("--reason", "-n", required=True)
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
    c.add_argument("--revision", action="store_true",
                   help="T-1460: return IN REVIEW work to its owner as claimed for revision "
                        "(instead of releasing to open)")
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

    c = sub.add_parser("doctor", help="diagnose board resolution and detect shadow boards (T-959)")
    c.set_defaults(fn=cmd_doctor)

    c = sub.add_parser("board-mark-primary", help="opt this repo's local .tickets in as its board of record")
    c.set_defaults(fn=cmd_board_mark_primary)

    c = sub.add_parser("board-archive-shadow", help="move a shadow board aside (never deletes); requires --yes")
    c.add_argument("path")
    c.add_argument("--yes", action="store_true", help="actually move it (default is a dry run)")
    c.set_defaults(fn=cmd_board_archive_shadow)

    c = sub.add_parser("context", help="print the shared briefing file")
    c.set_defaults(fn=cmd_context)

    c = sub.add_parser("mine", help="list tickets claimed by this agent")
    c.add_argument("--owner", "-o")
    c.set_defaults(fn=cmd_mine)

    c = sub.add_parser("init", help="install the board protocol into this project")
    c.add_argument("--track", action="store_true", help="commit the board to git instead of gitignoring it")
    c.add_argument("--board", metavar="DIR", help="write the board HERE instead of the "
                   "directory init resolves from cwd -- the explicit escape hatch for "
                   "the case init would otherwise refuse (T-263)")
    c.set_defaults(fn=cmd_init)

    try:
        from .ticket_coordination import register
    except ImportError:
        # `python src/ticket_board/cli.py` puts this directory on sys.path[0],
        # so the same module is importable as a top-level name. pip-install
        # (`ticket_board.cli:main`) takes the relative branch above.
        from ticket_coordination import register
    register(sub, globals())

    a = p.parse_args()
    if not a.cmd:
        p.print_help()
        return
    if a.cmd in ("doctor", "board-mark-primary", "board-archive-shadow"):
        # These diagnose/repair board resolution itself, so they must not go
        # through board_dir() -- a shadow board is exactly the case they are
        # for, and board_dir() would refuse before they ever ran (T-959).
        a.fn(a)
        return
    discover = a.cmd != "board"
    board = board_dir(discover_children=discover)
    if a.cmd not in (
        "create",
        "plan",
        "where",
        "init",
        "join",
        "epic",
        "sprint",
        "master",
        "connect",
        "board-restore",
    ):
        if not os.path.isdir(board):
            if a.cmd == "board":
                return
            sys.exit("no board at %s (create a ticket first)" % board)
    # board-restore uses --dest, not the discovered board
    if a.cmd == "board-restore":
        a.fn(a, board)
        return
    a.fn(a, board)


if __name__ == "__main__":
    main()
