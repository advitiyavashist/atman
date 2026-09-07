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
import shlex
import sys
import threading
from datetime import datetime, timezone

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

    Scope, deliberately narrow (T-259 defect 3 / cos-opus's ruling): only the
    location family is removed. GIT_AUTHOR_*/GIT_COMMITTER_* must survive --
    this env is also handed to `cmd_watch`/`cmd_spawn` as the FLEET-LAUNCH
    environment, and stripping identity there is the same class of attribution
    loss as T-238. GIT_SSH_COMMAND/GIT_ASKPASS/GIT_TERMINAL_PROMPT likewise
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


def _refuse_board_outside_pytest_tmp(path):
    """T-256: a test suite created a real ticket on the LIVE steer board.
    Root cause -- board_dir() prefers $TICKETS_DIR unconditionally, and every
    real agent session exports TICKETS_DIR pointing at its live board so
    plain `tickets ...` just works; a subprocess a test forgets to sandbox
    (test_wakeup.py's shell=True call for the injection regression, e.g.)
    inherits that ambient value straight through. pytest sets
    PYTEST_CURRENT_TEST for the life of every test, and pytest's own
    tmp_path/tmpdir fixtures always live under the system temp dir, so that
    combination is a reliable signal a board resolution is about to escape
    its sandbox. Fail loud instead of writing -- a silently-wrong resolution
    here is indistinguishable from a real board write after the fact."""
    if not os.environ.get("PYTEST_CURRENT_TEST"):
        return
    import tempfile
    tmp_root = os.path.realpath(tempfile.gettempdir())
    real = os.path.realpath(path)
    if real == tmp_root or real.startswith(tmp_root + os.sep):
        return
    sys.exit(
        "REFUSING TO USE BOARD %r: running under pytest (PYTEST_CURRENT_TEST "
        "is set) but this board resolved outside the system temp dir (%r). "
        "This looks like a test about to read or write a real board instead "
        "of an isolated tmp_path fixture -- see T-257. Make sure TICKETS_DIR "
        "points at a tmp_path (and that any subprocess.run() call passes "
        "env= explicitly rather than inheriting the ambient environment)."
        % (real, tmp_root)
    )


def board_dir(discover_children=True):
    result = _board_dir_uncached(discover_children)
    _refuse_board_outside_pytest_tmp(result)
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
    import subprocess
    try:
        # cwd + scrubbed env (T-243): cwd alone does NOT stop an inherited
        # GIT_DIR/GIT_COMMON_DIR from overriding repo discovery.
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


def _git_state_raw():
    """(state, mismatch). See git_state() -- this keeps the mismatch signal
    that git_state() deliberately throws away, for callers that record it."""
    here = os.getcwd()
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


def git_state():
    """Branch, short sha, dirty-file count, and whether cwd is the main worktree.

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
    state, _mismatch = _git_state_raw()
    return state


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
        if "inbox_seen" not in rec:
            # T-244: an agent's record starts existing right here, at its first
            # ever check-in -- so this is also where inbox_seen must start
            # existing. Leaving it unset makes unread()'s since="" branch
            # ("live file only") apply to the one agent that most needs the
            # opposite. Guard on the key's absence, not on is_new_agent: a
            # pre-T-244 on-disk record is not "new" but still has since="".
            #
            # Reading the key inside the lock is stricter than T-244 was on its
            # own branch: there the presence test ran against a pre-image read
            # outside any lock, so two concurrent check-ins could both see the
            # key absent and the loser's stamp would win by overwrite.
            rec["inbox_seen"] = now()

    # Other commands keep their own state in this record (inbox_seen, limit,
    # stop_blocks); a check-in must not erase it or every watch poll re-wakes
    # the agent. rec.update preserves them against the ORDERING hazard; the
    # lock in _agent_update is what preserves them against the CONCURRENCY one.
    return _agent_update(board, owner, _apply)


def _current_ticket(board, owner):
    for t in load_all(board):
        if t["status"] == "claimed" and t.get("owner") == owner:
            return t["id"]
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
    t["status"] = "claimed"
    t["owner"] = owner
    t["claimed_at"] = now()
    t["done_at"] = ""
    return save(board, t)


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
    """Open tickets whose dependencies are all done."""
    done = set(t["id"] for t in tickets if t["status"] == "done")
    return [
        t
        for t in tickets
        if t["status"] == "open" and all(d in done for d in t.get("deps", []))
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
    for t in made:
        print("created %s  %s" % (t["id"], t["title"]))


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
    ready_all = unblocked(board, tickets)
    ready = [t for t in _filter_ready(ready_all, roles) if can_do(board, owner, t)]
    cur = active_sprint(board)
    cur_id = cur["id"] if cur else None
    rank = cost_rank(board, owner)

    def order(t):
        p = t.get("priority", 2)
        # expensive agents go to hard/critical work first; cheap ones to the
        # routine tickets first, so the master's budget stretches further
        cost_key = p if rank == 2 else (-p if rank == 0 else 0)
        needs_key = 0 if t.get("needs") else 1  # a ticket only I can do comes first
        mine_key = 0 if t.get("suggested") == owner else (2 if t.get("suggested") else 1)
        return (mine_key, 0 if cur_id and t.get("sprint") == cur_id else 1, needs_key, cost_key, p, t["id"])

    ready.sort(key=order)
    for t in ready:
        got = try_claim(board, t["id"], owner)
        if got:
            checkin(board, owner, got["id"])
            print(detail(board, got, load_all(board)))
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
    others = [t for t in ready_all if t not in ready]
    if roles is not None and others:
        print(
            "no ticket for roles %s; %d ready for other roles: %s"
            % (roles, len(others), ", ".join(t["id"] for t in others))
        )
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
    g = git_state()
    if g and g["branch"] in ("main", "master") and not a.force:
        sys.exit("RULE: submit from your own worktree branch, not %r (or --force)" % g["branch"])
    if g and g["dirty"] and not a.force:
        sys.exit("RULE: %d uncommitted files -- commit before submitting for review (or --force)" % g["dirty"])
    if g and not a.force:
        trunk = _trunk()
        if git("merge-base", "--is-ancestor", trunk, "HEAD") is None:
            sys.exit("RULE: your branch is behind %s. Run `tickets sync` (merges %s in, so conflicts "
                     "are yours to fix now, not the master's later), then submit again." % (trunk, trunk))
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
        stamp = "%s@%s" % (g["branch"], g["sha"])
        text = "%s -- %s" % (stamp, text)
        t["commit"] = stamp
        t["branch"] = g["branch"]
        # T-215: record which repo this pin belongs to, so `tickets merge`
        # can refuse to close it from an unrelated repo's ancestry. Stable
        # across worktrees -- see repo_identity().
        t["repo"] = g["repo"]
    if a.pr:
        t["pr"] = a.pr
        text += " (PR %s)" % a.pr
    t["notes"].append({"by": author, "at": now(), "text": "REVIEW: " + text})
    save(board, t)
    checkin(board, author, t["id"], "submitted %s for review" % t["id"])
    m = current_master(board)
    post_message(board, author, "%s ready for review: %s" % (t["id"], text),
                 to=(m["owner"] if m else ""), re=t["id"])
    tm = timing(t)
    print("%s -> IN REVIEW after %s of work; master%s notified. Claim your next ticket." % (
        t["id"], fmt_hours(tm["active"]), (" (%s)" % m["owner"]) if m else ""))


def _trunk():
    for b in ("main", "master"):
        if git("rev-parse", "--verify", "-q", b) is not None:
            return b
    return "main"


def cmd_sync(a, board):
    """Agent side: bring main into my branch now, so the master's merge is trivial."""
    g = git_state()
    if not g:
        sys.exit("not in a git repo")
    trunk = _trunk()
    if g["branch"] in ("main", "master"):
        sys.exit("you are on %s; sync is for your own worktree branch" % g["branch"])
    if g["dirty"] and not a.force:
        sys.exit("%d uncommitted files; commit first (sync merges %s into your branch)" % (g["dirty"], trunk))
    if git("merge-base", "--is-ancestor", trunk, "HEAD") is not None:
        print("%s already contains %s; nothing to do" % (g["branch"], trunk))
        return
    import subprocess
    # T-243: `tickets sync` runs from the agent's own worktree cwd with no -C,
    # so it is exactly as exposed to an ambient GIT_DIR as git_state() was.
    r = subprocess.run(["git", "merge", "--no-edit", "-m", "Sync %s into %s" % (trunk, g["branch"]), trunk],
                       cwd=os.getcwd(), env=_clean_git_env(), capture_output=True, text=True)
    if r.returncode == 0:
        print("merged %s into %s -> %s" % (trunk, g["branch"], git("rev-parse", "--short", "HEAD")))
        checkin(board, whoami(), None, "synced with %s" % trunk)
        return
    conflicted = (git("diff", "--name-only", "--diff-filter=U") or "").splitlines()
    print("CONFLICTS merging %s into %s -- these files need you:" % (trunk, g["branch"]))
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
    root = os.path.dirname(board)
    cfg = merge_config(board)
    trunk = _trunk()
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
                       "Integrate %s@%s into %s (master %s via tickets merge%s)" % (
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
        sha = git("rev-parse", "--short", trunk)
        full_trunk = sh("git", "rev-parse", trunk).stdout.strip()
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
            t2["status"] = "done"
            t2["done_at"] = now()
            t2["notes"].append({"by": owner, "at": now(),
                                "text": "merged into %s as %s (tickets merge; pinned %s)" % (
                                    trunk, sha, pin)})
            save(board, t2)
            closed.append(t2["id"])
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
CLI_LIMIT_STRINGS = ("session limit", "usage limit", "hit your limit", "limit reached",
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


def _looks_auth(text):
    low = (text or "").lower()
    return any(k in low for k in CLI_AUTH_STRINGS)


# ---- in-run heartbeat ---------------------------------------------------

def _run_file(board, owner):
    return os.path.join(agents_dir(board), owner + ".run")


_RUN_BEAT_LOCK = threading.Lock()


def _run_beat(board, owner, **fields):
    """Write the in-run heartbeat.

    Deliberately its OWN file rather than a field on agents/<name>.json: the
    child session runs `tickets` commands that read-modify-write that record,
    and a heartbeat thread rewriting it every few seconds would race them and
    silently drop whatever the child had just written (inbox_seen, limit,
    ticket). One watcher per agent holds the pid lock, so this file has a
    single writer.
    """
    try:
        os.makedirs(agents_dir(board), exist_ok=True)
        path = _run_file(board, owner)
        # The beat thread and the run-end write are both in this process and
        # can overlap: the lock keeps a read-modify-write whole, and the tmp
        # name is per-writer so two overlapping writers cannot truncate each
        # other's scratch file and leave a spliced record on disk.
        with _RUN_BEAT_LOCK:
            rec = _read_run(board, owner)
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


def _run_begin(board, owner, run_no, cwd):
    _run_beat(board, owner, pid=os.getpid(), run=run_no, cwd=cwd,
              started=now(), active=True, rc=None, ended="")


def _run_end(board, owner, run_no, rc):
    _run_beat(board, owner, run=run_no, active=False, rc=rc, ended=now())


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

def _watcher_pid(board, owner):
    try:
        with open(os.path.join(agents_dir(board), owner + ".watch.pid")) as f:
            return int((f.read() or "0").strip() or 0)
    except (IOError, ValueError):
        return 0


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
    # gate: gpt-cursor sat hard-limited until October with 37 failures in a
    # row, and gating on speed dropped it the moment one of those failures
    # happened to take 97 seconds instead of 7.
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
    activity it is. That is not hypothetical: three records on this board
    (claude-fable, cursor, cursor-2) point at one worktree, and reading the
    transcript naively reports all three as working when at most one is.
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
    # epic that is routinely a second repo -- every E-010 agent runs `tickets
    # review` from advitiyavashist/tickets while its session runs in a steer
    # worktree. The WATCHER's cwd, recorded in the run file, is the session's
    # own directory and does not move when a command is run elsewhere, so it
    # is tried first. Both are kept: an agent running by hand has no run file.
    cwds = _dedup([run.get("cwd") or "",
                   (rec or {}).get("cwd") or "",
                   (rec or {}).get("worktree") or ""])
    cwd = cwds[0] if cwds else ""
    beat_age = _age_secs(run.get("beat"))
    out = {"state": "unknown", "detail": "", "source": "none", "heuristic": True,
           "watcher": bool(pid and _pid_alive(pid)),
           "run": run if run.get("active") else {},
           "seen_age": _age_secs((rec or {}).get("seen"))}

    # 1. A human asserting a state always wins: `tickets limit` is a person
    #    saying "I read the log". Keep it, but it is no longer the ONLY path
    #    to a limited render -- that is how gpt-cursor sat on this board for a
    #    day looking healthy while hard-limited until October.
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


def cmd_done(a, board):
    t = load(board, a.id)
    if not a.notes and not a.no_notes:
        sys.exit(
            'done needs --notes "paths, names, decisions the next agent must match" '
            "(or --no-notes if there is truly nothing to hand off)"
        )
    g = git_state()
    # A branch/SHA match is not proof of repository identity (T-254).
    # Check before mutating the ticket; --force only bypasses worktree rules.
    recorded_repo = t.get("repo")
    current_repo = g.get("repo") if g else None
    if (g or recorded_repo) and not current_repo:
        sys.exit("RULE: %s cannot be closed without a verifiable repository; "
                 "run done from the deliverable's checkout." % a.id)
    if recorded_repo and recorded_repo != current_repo:
        sys.exit("RULE: %s recorded repository %r does not match current repository %r; "
                 "run done from the recorded repository. --force cannot override "
                 "repository evidence." % (a.id, recorded_repo, current_repo))
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
        t["commit"] = stamp
        t["repo"] = current_repo
    if text:
        # by=whoami(), not t["owner"]: the note records who wrote it, which is
        # not always who the ticket is filed under (T-238 -- see cmd_note).
        t["notes"].append({"by": whoami(), "at": now(), "text": text})
    save(board, t)
    if t.get("owner"):
        checkin(board, t["owner"], "", "finished %s" % a.id)
    tm = timing(t)
    print("%s done in %s (waited %s before claim)" % (
        a.id, fmt_hours(tm["active"]), fmt_hours(tm["wait"])))
    if g:
        print("recorded %s" % t["commit"])
    tickets = load_all(board)
    freed = [x["id"] for x in unblocked(board, tickets) if a.id in x.get("deps", [])]
    if freed:
        print("unblocked: %s" % ", ".join(freed))


def cmd_block(a, board):
    t = load(board, a.id)
    t["status"] = "blocked"
    t["notes"].append({"by": whoami(), "at": now(), "text": a.reason})
    save(board, t)
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
        if t["status"] == "open" and a.owner:
            got = try_claim(board, t["id"], a.owner)
            if not got:
                sys.exit("%s was claimed by someone else while assigning" % t["id"])
            t = got
            changed.append("claimed for %s" % a.owner)
        elif t["status"] == "claimed":
            t["owner"] = a.owner
            changed.append("owner=%s" % a.owner)
    if not changed:
        sys.exit("nothing to change; see tickets assign --help")
    note_text = "assign: " + ", ".join(changed)
    if getattr(a, "notes", ""):
        note_text += " -- " + a.notes
    t["notes"].append({"by": whoami(a.by), "at": now(), "text": note_text})
    save(board, t)
    print("%s: %s" % (t["id"], ", ".join(changed)))


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

MASTER_TEMPLATE = """# MASTER -- coordination node for this board

Any agent can become master: run `tickets master take`, then `tickets master`
to get the full briefing. Keep this file current; it is the memory that
survives agent restarts and timeouts.

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
   (or open the PR) before claiming the next ticket.
7. Set `TICKET_AGENT` to your own name so the board can tell agents apart.

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
    if getattr(a, "notes", ""):
        # Attribute to the acting agent, not the ticket's outgoing owner --
        # reopen is very often one agent (a reviewer, the master) sending
        # BACK another agent's ticket, and stamping the reason as though the
        # outgoing owner wrote it is the same misattribution class as T-238.
        t["notes"].append({"by": whoami(getattr(a, "by", "")), "at": now(), "text": a.notes})
    t["status"] = "open"
    t["owner"] = ""
    save(board, t)
    lock = os.path.join(board, a.id + ".lock")
    if os.path.exists(lock):
        os.unlink(lock)
    print("%s reopened" % a.id)


def is_fixture_board(board):
    """True only when the board is explicitly marked disposable."""
    return os.path.isfile(os.path.join(board, ".fixture-board"))


def cmd_clear(a, board):
    """Delete ticket files — FIXTURE BOARDS ONLY (incident 2026-09-06)."""
    if not is_fixture_board(board):
        sys.exit(
            "REFUSED: tickets clear will not wipe a live board (missing .fixture-board).\n"
            "Incident 2026-09-06: clear deleted all T-*.json on the Steer board.\n"
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
    rec = checkin(board, owner, None, a.note or "")
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
    live = {} if getattr(a, "no_liveness", False) else dict(
        (r["owner"], _safe(lambda r=r: agent_liveness(board, r, agents),
                           {"state": "unknown", "detail": "liveness read failed",
                            "source": "none", "heuristic": True}))
        for r in agents)
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


def post_message(board, sender, text, to="", re=""):
    _rotate_messages_if_big(board)
    rec = {"at": now(), "from": sender, "to": to, "re": re, "text": text}
    line_ = json.dumps(rec) + "\n"
    # O_APPEND writes under PIPE_BUF are atomic, so concurrent posters never interleave
    fd = os.open(messages_path(board), os.O_CREAT | os.O_WRONLY | os.O_APPEND, 0o644)
    try:
        os.write(fd, line_.encode())
    finally:
        os.close(fd)
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

    Messages carry no id on the wire, and adding one would change a format
    that T-213's legacy import round-trips verbatim, so identity is derived
    from content instead: the same record hashes the same whether it is read
    from the live file or from a rotated archive, and nothing already on disk
    has to be migrated. Two byte-identical messages in the same second do
    share an identity, which is exactly why the boundary list is consumed as
    a multiset below -- one delivery per occurrence, not per distinct value.
    """
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
    seen_ids = rec.get("inbox_seen_ids") or []
    msgs = load_messages(board)
    # An agent that slept through a rotation has its unread mail sitting in an
    # archive the fast path never reads, so its inbox would come back silently
    # empty -- the one failure this whole board is built to prevent. Pay for
    # the archives only when `since` predates what is left in the live file.
    if since and (not msgs or since < msgs[0].get("at", "")):
        if glob.glob(os.path.join(board, "messages.*.jsonl")):
            msgs = load_messages(board, include_archives=True)
    remaining = _seen_counts(seen_ids)
    out = [m for m in msgs
           if _addressed(m, owner) and _is_unread(m, since, remaining)]
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
    retained = [_msg_id(m) for m in msgs
                if _addressed(m, owner) and m.get("at", "") >= watermark]
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


def fmt_msg(m):
    to = (" -> %s" % m["to"]) if m.get("to") and m["to"] != "all" else ""
    re_ = (" [%s]" % m["re"]) if m.get("re") else ""
    return "%s  %s%s%s: %s" % (m["at"][5:16].replace("T", " "), m.get("from", "?"), to, re_, m.get("text", ""))


def cmd_msg(a, board):
    sender = whoami(a.owner)
    if a.re:
        load(board, a.re)  # validate the ticket exists
    m = post_message(board, sender, a.text, a.to or "", a.re or "")
    print("posted: " + fmt_msg(m))


def cmd_inbox(a, board):
    owner = whoami(a.owner)
    scan = None
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
    Agents still pull -- this is a hint, not a lock -- unless --claim."""
    tickets = load_all(board)
    wf = load_workforce(board)
    roles = load_roles(board)
    agents = dict((r["owner"], r) for r in load_agents(board))
    names = sorted(set(list(wf) + [n for n in roles if roles[n]]))
    if a.only:
        names = [n for n in names if n in a.only]
    # exclude agents that are out on a limit
    names = [n for n in names if not agents.get(n, {}).get("limit")]
    done = set(t["id"] for t in tickets if t["status"] == "done")
    load_ = {}
    for t in tickets:
        if t["status"] == "claimed":
            load_[t.get("owner")] = load_.get(t.get("owner"), 0) + 1
    ready_first = sorted(
        [t for t in tickets if t["status"] == "open" and (not t.get("suggested") or a.redo)],
        key=lambda t: (0 if all(d in done for d in t.get("deps", [])) else 1, t.get("priority", 2), t["id"]))
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
            t["suggested"] = best
            save(board, t)
            changed += 1
            load_[best] = load_.get(best, 0) + 0.5  # soft-count suggestions too
            if a.claim and t["status"] == "open" and all(d in done for d in t.get("deps", [])):
                got = try_claim(board, t["id"], best)
                if got:
                    t = got
                    why += "  CLAIMED"
        print("%-6s %-3s %-44s %-14s %s" % (t["id"], t.get("priority", 2), t["title"][:44],
                                          best or "(nobody fits)", why))
    if changed:
        _master_log(board, "route: suggested owners for %d tickets%s" % (changed, " and claimed ready ones" if a.claim else ""))
    print("\nAgents pull with `tickets next`; their suggested tickets come first. "
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

Then the loop, until `tickets next` says nothing is ready:

    # work on your own worktree (join prints the exact command)
    tickets update <id> "what changed, what is next"     # every {every} min
    tickets msg "..." --to <agent> --re <id>             # questions, blockers
    git add -A && git commit -m "..."                    # commit as you go
    tickets done <id> --notes "paths, decisions"         # refuses on main / dirty
    # merge or open a PR, then:
    tickets next

Tool-specific:
- Claude Code: `TICKET_AGENT=claude-opus claude` -- the global SessionStart hook
  shows the board automatically; `.tickets/CONTEXT.md` is offered on each claim.
- Codex: reads AGENTS.md in the repo root (installed by `tickets init`);
  launch with `TICKET_AGENT=codex codex`.
- Cursor: reads AGENTS.md and .cursor/rules/tickets.mdc; set TICKET_AGENT in
  the terminal you start it from, or pass `--owner cursor` on each command.
- Anything else that can run a shell: the same commands work; `tickets` is one
  stdlib Python file at ~/.claude/tools/tickets.py.

To take coordination: `tickets master take`, then `tickets master` and act on
the HEALTH section.

## Making agents start on their own

A session cannot be woken by a hook once its turn has ended, so use both:

- Keep going while there is work (Claude Code): `tickets hooks claude` installs a
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


def cmd_join(a, board):
    owner = a.name or whoami()
    if owner.startswith("agent-"):
        sys.exit("give yourself a real name: tickets join <name> --roles ...")
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
    if a.tool:
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
    wf[owner] = entry
    save_workforce(board, wf)
    rec = checkin(board, owner, None, "joined" + (" (%s)" % a.tool if a.tool else ""))
    post_message(board, owner, "joined the board%s; roles=%s; at %s [%s]" % (
        (" via %s" % a.tool) if a.tool else "", roles.get(owner, DEFAULT_ROLES.get(owner, [])),
        rec["worktree"] or rec["cwd"], rec["branch"] or "?"))
    root = os.path.dirname(board)
    print("joined as %s  roles=%s  can=%s  cost=%s" % (
        owner, roles.get(owner, DEFAULT_ROLES.get(owner, "any")), entry["can"] or "-", entry["cost"]))
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
    print("Loop:  tickets master  ->  tickets next  ->  work + commit  ->  "
          "tickets update <id> \"...\" (every %d min)  ->  tickets done <id> --notes \"...\"  "
          "->  merge  ->  tickets next" % UPDATE_EVERY_MIN)
    print("Full instructions: tickets connect")
    if not os.path.exists(os.path.join(root, "AGENTS.md")):
        print("(no AGENTS.md here -- run `tickets init` once so Codex/Cursor see the rules)")


def cmd_connect(a, board):
    print(CONNECT.format(root=os.path.dirname(board), every=UPDATE_EVERY_MIN))


# ---- wake-up: is there work for this agent, and how to start it ----------
#
# Two mechanisms, because a session cannot be woken by a hook once its turn
# has ended: `stop-hook` keeps a Claude Code turn alive while board work
# remains; `watch` starts a headless run when work appears. Both are built on
# `pending_work`, which never raises and never mutates the board.

STOP_HOOK_MAX_PER_HOUR = 4
WATCH_MIN_INTERVAL = 5
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
    """
    out = {}
    if not owner or not os.path.isdir(board):
        return out
    rec = _safe(lambda: _agent_rec(board, owner), {}) or {}
    if rec.get("limit"):
        out["limited"] = rec["limit"].get("until") or rec["limit"].get("at") or "yes"
        return out
    msgs = _safe(lambda: unread(board, owner), [])
    direct = [m for m in msgs if m.get("to") == owner]
    if direct:
        out["messages_to_me"] = [fmt_msg(m) for m in direct[-5:]]
    elif msgs:
        out["broadcasts"] = len(msgs)
    tickets = _safe(lambda: load_all(board), [])
    held = [t for t in tickets if t.get("status") == "claimed" and t.get("owner") == owner]
    if held:
        out["holding"] = [t["id"] + " " + t.get("title", "")[:60] for t in held]
    roles = _safe(lambda: roles_for(board, owner, None), None)
    ready = _safe(lambda: [t for t in _filter_ready(unblocked(board, tickets), roles)
                           if can_do(board, owner, t)], [])
    mine_first = [t for t in ready if t.get("suggested") == owner]
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
        stuck = [fmt_msg(x) for x in _safe(lambda: load_messages(board), [])
                 if x.get("from") != owner and _is_unread(x, since, _rem)
                 and str(x.get("text", "")).lower().startswith(("stuck", "blocked"))]
        if stuck:
            out["stuck_messages"] = stuck[-5:]
        crit = [i for i in _safe(lambda: health(board, tickets), []) if i[0] == "CRIT"]
        if crit:
            out["health_crit"] = [i[1][:80] for i in crit[:3]]
        # The objective heartbeat: the master seat is woken every `drive_every`
        # minutes (set by `watch --heartbeat`) even when nothing else is pending,
        # so it keeps planning toward the objective instead of going quiet.
        obj = _safe(lambda: load_objective(board), {})
        every = int(rec.get("drive_every") or 0)
        if obj and not obj.get("done") and every > 0 and owner == m.get("owner"):
            last = rec.get("drive_at", "")
            if not last or hours_since(last) * 60 >= every:
                out["drive"] = {"objective": obj.get("text", "")[:100], "last": last or "never"}
    return out


def actionable(pending):
    """Broadcast-only noise or a recorded usage limit should not start a run."""
    return bool(pending) and not (set(pending) <= {"broadcasts", "limited"})


WORKER_PROMPT = """You are {agent}, a worker on the shared ticket board at {board} (repo {root}).
TICKET_AGENT is already set in your environment; run `tickets ...` commands plainly (no env prefix).
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
TICKET_AGENT is set; run `tickets ...` plainly. You do not take feature tickets. Your three jobs, every wake-up:
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
Stop when the inbox is empty, the review queue is empty and no health item needs action.
{extra}"""

PLANNER_PROMPT = """You are {agent}, the MASTER PLANNER of the shared ticket board at {board} (repo {root}).
TICKET_AGENT is set; run `tickets ...` plainly. A chief of staff ({cos}) handles review, merge and day-to-day
unblocking; you do not take feature tickets and you do not merge unless the cos is silent.
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
Stop when there is nothing addressed to you, the sprint matches the vision, and every ready ticket has an owner.
{extra}"""


DRIVE_PROMPT = """OBJECTIVE (set by {set_by}; `tickets objective` to read it in full):
{objective}

DRIVE STATUS now:
{status}

5. DRIVE THE OBJECTIVE (this wake-up may have been a heartbeat with nothing else pending -- that is the point):
   compare the status above with the objective. If the sprint is done, lanes are empty, or ready work is
   unowned, plan the next slice toward the objective NOW: `tickets plan`/`tickets create` the missing tickets,
   `tickets route` + `tickets assign`, `tickets brief` the context, `tickets spawn` where a lane has no live
   worker, and log the reasoning with `tickets master log`. If the objective is met, run
   `tickets objective --done "<evidence>"` and post it. If it cannot be met without the user (spend, credentials,
   a decision they reserved), post exactly what you need with `tickets msg` and log it; do not guess.
   Never end a heartbeat without either advancing the plan or logging why nothing needed to change."""


def cmd_objective(a, board):
    """Set, show or close the standing objective the master drives toward."""
    path = objective_path(board)
    cur = load_objective(board)
    if a.done is not None:
        if not cur:
            sys.exit("no objective set")
        cur["done"] = True
        cur["done_at"] = now()
        cur["evidence"] = a.done
        with open(path, "w") as f:
            json.dump(cur, f, indent=2)
        post_message(board, whoami(a.by), "objective met: %s -- %s" % (cur.get("text", "")[:120], a.done))
        _master_log(board, "objective met: %s" % a.done, by=whoami(a.by))
        print("objective marked met")
        return
    if a.text:
        rec = {"text": a.text, "set_by": whoami(a.by), "at": now(), "done": False}
        with open(path, "w") as f:
            json.dump(rec, f, indent=2)
        post_message(board, whoami(a.by), "objective set: %s" % a.text[:200])
        _master_log(board, "objective set: %s" % a.text, by=whoami(a.by))
        print("objective set")
        return
    if not cur:
        print("no objective set; `tickets objective \"<what done looks like>\"`")
        return
    print("OBJECTIVE%s (set by %s, %s)" % (" -- MET" if cur.get("done") else "", cur.get("set_by", "?"), cur.get("at", "")))
    print(cur.get("text", ""))
    if cur.get("done"):
        print("evidence: %s" % cur.get("evidence", ""))
    print()
    print(drive_status(board))


def cmd_drive(a, board):
    """Set the objective and spawn the master seat with a heartbeat, in one go:
    `tickets drive "<objective>" --as claude-fable --tool cursor+claude --heartbeat 30`."""
    owner = whoami(a.by)
    if a.text:
        ns = argparse.Namespace(text=a.text, done=None, by=owner)
        cmd_objective(ns, board)
    elif not load_objective(board):
        sys.exit("give an objective: tickets drive \"<what done looks like>\"")
    argv = [sys.executable, os.path.realpath(__file__), "spawn", owner, "--master",
            "--heartbeat", str(a.heartbeat), "--every", str(a.every), "--tool", a.tool]
    if a.model:
        argv += ["--model", a.model]
    import subprocess
    if a.restart:
        subprocess.call([sys.executable, os.path.realpath(__file__), "spawn", owner, "--stop"])
    sys.exit(subprocess.call(argv))


def cos_prompt_text(agent, board, root, extra):
    return MASTER_PROMPT.replace("the MASTER of", "the CHIEF OF STAFF of").replace(
        "Your three jobs, every wake-up:",
        "The master planner sets scope and routes by complexity; you review, unblock and merge. "
        "Escalate scope or vision questions to the planner with `tickets msg --to <master>`. "
        "Your three jobs, every wake-up:").format(agent=agent, board=board, root=root, extra=extra)


def cmd_pending(a, board):
    owner = whoami(a.agent)
    p = pending_work(board, owner)
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


def ticket_context(board, owner):
    """Context notes attached to the agent's held ticket(s)."""
    out = []
    for t in load_all(board):
        if t.get("status") == "claimed" and t.get("owner") == owner:
            ctx = [n["text"] for n in t.get("notes", []) if n.get("kind") == "context"]
            if ctx:
                out.append("%s %s:\n  - %s" % (t["id"], t.get("title", "")[:60], "\n  - ".join(ctx)))
    return "\n".join(out)


def cmd_prompt(a, board):
    owner = whoami(a.agent)
    m = current_master(board)
    master = (m["owner"] if m else "the master")
    cos = (m or {}).get("cos") or ""
    if getattr(a, "cos", False) or (cos and owner == cos and not getattr(a, "master", False)):
        print(cos_prompt_text(owner, board, os.path.dirname(board), a.extra or ""))
        return
    if getattr(a, "master", False):
        extra = a.extra or ""
        obj = _safe(lambda: load_objective(board), {})
        if obj and not obj.get("done"):
            _safe(lambda: _agent_set(board, owner, drive_at=now()), None)
            extra = DRIVE_PROMPT.format(objective=obj.get("text", ""), set_by=obj.get("set_by", "?"),
                                        status=_safe(lambda: drive_status(board), "")) + ("\n" + extra if extra else "")
        if cos and owner != cos:
            print(PLANNER_PROMPT.format(agent=owner, board=board, root=os.path.dirname(board), cos=cos,
                                        extra=extra))
        else:
            print(MASTER_PROMPT.format(agent=owner, board=board, root=os.path.dirname(board),
                                       extra=extra))
        return
    parts = []
    brief = agent_brief(board, owner)
    if brief:
        parts.append("Your standing brief (%s):\n%s" % (brief_path(board, owner), brief))
    tctx = ticket_context(board, owner)
    if tctx:
        parts.append("Context attached to your ticket(s):\n" + tctx)
    if a.extra:
        parts.append(a.extra)
    print(WORKER_PROMPT.format(agent=owner, board=board, root=os.path.dirname(board), master=master,
                               extra="\n\n".join(parts)))


def cmd_brief(a, board):
    """Give an agent (or a ticket) context. Shown on every claim, in `tickets
    prompt`, and in `boot`. Appends with a timestamp; --file replaces from a file;
    --show prints; --ticket attaches to a ticket instead of an agent."""
    who = whoami(a.by)
    if a.ticket and not a.text and a.agent:
        a.text, a.agent = a.agent, ""  # `brief --ticket T-1 "text"`: first positional is the text
    if a.ticket:
        t = load(board, a.ticket)
        if a.show:
            for n in t.get("notes", []):
                if n.get("kind") == "context":
                    print("- [%s] %s" % (n.get("by", "?"), n["text"]))
            return
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
        sys.exit("brief needs an agent name or --ticket")
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
            lines.append("  %-13s %-8s%s %-9s %s" % (
                nme[:13], state, mark,
                ("seen " + fmt_hours(hours_since(r["seen"]))) if r.get("seen") else "never",
                ("pending: " + ", ".join(keys)) if keys else (lv.get("detail") or "")[:40]))
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
                return None
            try:
                os.unlink(path)  # stale lock from a dead watcher
            except OSError:
                return None
    return None


def _watch_run_capped(cmd, cwd, env, log_path, timeout_s, cap_bytes,
                      on_beat=None, beat_secs=None):
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
    if on_beat:
        interval = beat_secs or RUN_HEARTBEAT_SECS

        def beat():
            while not beat_stop.wait(interval):
                _safe(on_beat, None)

        _safe(on_beat, None)  # stamp the start of the run, do not wait a tick
        beat_thread = threading.Thread(target=beat, daemon=True)
        beat_thread.start()

    try:
        rc = proc.wait(timeout=timeout_s)
        timed_out = False
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait()
        rc = 124
        timed_out = True
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


def cmd_watch(a, board):
    """Poll the board; when there is work for the agent, launch a worker command.

    One watcher per agent (pid lock), one run at a time, per-run timeout,
    exponential backoff after failed runs, clean exit on SIGTERM/Ctrl-C.
    """
    import signal
    import time as _time

    owner = whoami(a.agent)
    if owner.startswith("agent-"):
        sys.exit("set --agent or TICKET_AGENT to a real name")
    root = os.path.dirname(board)
    cwd = os.path.abspath(a.cwd or root)
    if not os.path.isdir(cwd):
        sys.exit("--cwd %s does not exist" % cwd)
    cmd = a.exec or (
        'claude -p "$(tickets prompt)" --permission-mode %s%s'
        % (a.permission_mode, (" --allowedTools %s" % a.allowed_tools) if a.allowed_tools else "")
    )
    every = max(WATCH_MIN_INTERVAL, int(a.every))
    lock = None
    if not a.once:
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
               PATH=os.path.expanduser("~/.local/bin") + ":/opt/homebrew/bin:" + os.environ.get("PATH", ""))
    stop = {"now": False}

    def _term(signum, frame):
        stop["now"] = True

    signal.signal(signal.SIGTERM, _term)

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
            print("watching %s for %s every %ds; cwd=%s; cmd=%s" % (board, owner, every, cwd, cmd))
            _safe(lambda: checkin(board, owner, None, "watch loop online (every %ds)" % every), None)
        _safe(lambda: _agent_set(board, owner, drive_every=int(getattr(a, "heartbeat", 0) or 0)), None)
        while not stop["now"]:
            if os.path.exists(_stop_file(board, owner)):
                try:
                    os.unlink(_stop_file(board, owner))
                except OSError:
                    pass
                print("stop requested via tickets spawn --stop")
                break
            p = _safe(lambda: pending_work(board, owner), {})
            if actionable(p):
                runs += 1
                log("%s run %d trigger=%s" % (now(), runs, json.dumps(p)[:400]))
                print("%s work found (%s) -> run %d" % (now(), ", ".join(p), runs))
                if a.dry_run:
                    print("  dry-run; would execute: %s" % cmd)
                    rc = 0
                else:
                    # The whole point of T-237: something must record that this
                    # agent is alive WHILE the child runs. checkin() cannot --
                    # the next call to it is on the far side of this line.
                    _safe(lambda: _run_begin(board, owner, runs, cwd), None)
                    rc, timed_out = _watch_run_capped(
                        cmd, cwd, env, log_path,
                        a.run_timeout * 60 if a.run_timeout else None,
                        WATCH_LOG_MAX_BYTES,
                        on_beat=lambda: _run_beat(board, owner, pid=os.getpid(), run=runs,
                                                  cwd=cwd, active=True),
                        beat_secs=int(getattr(a, "beat_every", 0) or RUN_HEARTBEAT_SECS),
                    )
                    _safe(lambda: _run_end(board, owner, runs, rc), None)
                    if timed_out:
                        with open(log_path, "a") as lf:
                            lf.write("%s run %d TIMEOUT after %d min\n" % (now(), runs, a.run_timeout))
                    log("%s run %d exit %s" % (now(), runs, rc))
                    print("  run %d finished exit=%s (log: %s)" % (runs, rc, log_path))
                failures = failures + 1 if rc not in (0, None) else 0
                if a.max_runs and runs >= a.max_runs:
                    print("max-runs reached")
                    break
            elif a.verbose:
                print("%s nothing pending%s" % (now(), " (limited)" if p.get("limited") else ""))
            if a.once:
                sys.exit(0 if actionable(p) else 1)
            wait = min(every * (2 ** min(failures, 5)), 900) if failures else every
            _safe(lambda: checkin(board, owner, None, "watching (%d runs, %d failed in a row)" % (runs, failures)), None)
            try:
                _time.sleep(wait)
            except KeyboardInterrupt:
                break
    finally:
        if lock:
            try:
                os.unlink(lock)
            except OSError:
                pass
        if not a.once:
            print("watch stopped")


# ---- native Codex hook (SessionStart / UserPromptSubmit context) ----------

def cmd_codex_hook(a, board):
    """Codex hook body: print board context as hookSpecificOutput.additionalContext.

    Scoped to --worktree via the event cwd so one install is silent elsewhere.
    Read-only: does not acknowledge messages or touch tickets.
    """
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
    owner = a.agent
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
    if owner not in wf or (a.roles is not None):
        ns = argparse.Namespace(name=owner, roles=a.roles, can=a.can, cost=a.cost, tool=a.tool,
                                model=a.model, best_for="")
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
    nxt = ("tickets mine" if p.get("holding") else "tickets next") if actionable(p) else "tickets msg \"idle\""
    print("NEXT: TICKET_AGENT=%s %s" % (owner, nxt))
    if a.watch:
        wn = argparse.Namespace(agent=owner, every=a.every, exec=a.exec, cwd=a.cwd or root,
                                permission_mode="acceptEdits", allowed_tools="", max_runs=0,
                                once=False, dry_run=False, verbose=False, run_timeout=a.run_timeout)
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

One command does every step (join, hooks, check-in, briefing):

    export TICKET_AGENT=<unique-name>          # claude-opus, claude-sonnet, codex, cursor-2 ...
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
  Claude Code (interactive):  TICKET_AGENT=<name> claude      -- hooks do the rest each turn
  Claude Code (unattended):   tickets boot --tool claude --watch    (or: tickets watch --every 60)
                              runs `claude -p "$(tickets prompt)"` only when `tickets pending` says there is work
  Codex:                      TICKET_AGENT=<name> codex        -- hook injects board context; AGENTS.md carries the rules
  Cursor:                     open the repo/worktree; enable Hooks in settings; set TICKET_AGENT in the launching shell
  Anything else:              `tickets prompt` prints the worker instructions; `tickets watch --exec '<your cli>'`

Safety rails (all on by default):
  - Stop hook: at most one extra continuation per user turn (stop_hook_active) and 4 per hour;
    off with TICKETS_STOP_HOOK=off; never fires without TICKET_AGENT; never for broadcasts only.
  - watch: one watcher per agent name (pid lock), one run at a time, --run-timeout, backoff on failures,
    logs in .tickets/agents/<name>.watch.log, stops on SIGTERM.
  - An agent that recorded `tickets limit` is never woken until `tickets limit --clear`.

Spawning a team from a master session (models per agent):
  tickets spawn scribe --model sonnet --roles docs --brief "house style: ..."     # cheap worker
  tickets spawn core   --model opus   --roles backend --cost high                # hard tickets
  tickets spawn boss   --master --model sonnet                                   # coordinate / unblock / review / merge
  tickets drive "<what done looks like>" --as boss --heartbeat 30               # objective + master seat that wakes
                                                                                #   every 30 min to plan toward it
  tickets spawn --list | tickets spawn <name> --stop
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


def _worker_cmd(board, owner, model="", permission_mode="bypassPermissions", tool="claude", master=False):
    """The headless command a spawned worker runs. Model comes from --model or
    the workforce record (`tickets join --model`). Spawned workers run without
    permission prompts by default: nobody is there to answer them, and the
    blast radius is the agent's own worktree and branch (--safe for acceptEdits).
    """
    # The workforce record is writable by any agent, so the model name is quoted
    # before it reaches `watch`, which runs this string through the shell.
    model = shlex.quote(model or load_workforce(board).get(owner, {}).get("model", "") or "")
    model = "" if model == "''" else model
    prompt = {"master": "tickets prompt --master", "cos": "tickets prompt --cos"}.get(
        master if isinstance(master, str) else ("master" if master else ""), "tickets prompt")
    if tool == "claude":
        flag = ("--dangerously-skip-permissions" if permission_mode == "bypassPermissions"
                else "--permission-mode %s" % permission_mode)
        return 'claude -p "$(%s)" %s%s' % (prompt, flag, (" --model %s" % model) if model else "")
    if tool == "codex":
        # Codex CLI headless: exec mode; unattended needs the bypass flag (no one
        # can answer approvals), --safe keeps the workspace-write sandbox instead.
        mode = ("--dangerously-bypass-approvals-and-sandbox" if permission_mode == "bypassPermissions"
                else "-s workspace-write")
        return 'codex exec --skip-git-repo-check %s%s "$(%s)"' % (mode, (" -m %s" % model) if model else "", prompt)
    if tool == "cursor":
        # Cursor CLI (`agent`): runs any model Cursor offers (gpt-5.5-high, claude-fable-5-1-thinking-high, ...)
        force = "--force" if permission_mode == "bypassPermissions" else ""
        return 'agent -p --output-format text %s%s "$(%s)"' % (force, (" --model %s" % model) if model else "", prompt)
    if tool == "cursor+claude":
        # Fable through Cursor first; if that run errors, the same prompt through the
        # Claude CLI (opus). One identity, two engines -- the master never goes dark.
        first = _worker_cmd(board, owner, model or "claude-fable-5-1-thinking-high", permission_mode, "cursor", master)
        second = _worker_cmd(board, owner, "opus", permission_mode, "claude", master)
        return "%s || %s" % (first, second)
    return tool  # any other executable that reads the prompt itself


def _inherit_settings(root, wt):
    """Copy the project's .claude settings into a new worktree so permission
    allow-lists and hooks are the same there (a worktree does not inherit the
    root checkout's .claude/ directory)."""
    import shutil
    src = os.path.join(root, ".claude")
    dst = os.path.join(wt, ".claude")
    if not os.path.isdir(src) or os.path.abspath(src) == os.path.abspath(dst):
        return []
    copied = []
    os.makedirs(dst, exist_ok=True)
    for name in ("settings.json", "settings.local.json"):
        s, d = os.path.join(src, name), os.path.join(dst, name)
        if os.path.isfile(s) and not os.path.exists(d):
            shutil.copy2(s, d)
            copied.append(name)
    return copied


def _watcher_pid(board, owner):
    try:
        with open(os.path.join(agents_dir(board), owner + ".watch.pid")) as f:
            pid = int(f.read().strip() or "0")
    except (OSError, ValueError):
        return 0
    return pid if pid and _pid_alive(pid) else 0


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
        print("%-14s %-9s %-8s %-8s %s" % ("agent", "watcher", "model", "seen", "worktree"))
        for r in sorted(load_agents(board), key=lambda r: r["owner"]):
            pid = _watcher_pid(board, r["owner"])
            print("%-14s %-9s %-8s %-8s %s" % (
                r["owner"][:14], ("pid %d" % pid) if pid else "-", (wf.get(r["owner"], {}).get("model") or "-")[:8],
                (fmt_hours(hours_since(r["seen"])) + " ago") if r.get("seen") else "never",
                (r.get("worktree") or "").replace(os.path.expanduser("~"), "~")))
        return
    if not a.name:
        sys.exit("spawn needs a name (or --list)")
    owner = a.name
    if a.stop:
        pid = _watcher_pid(board, owner)
        if not pid:
            print("no running watcher for %s" % owner)
            return
        with open(_stop_file(board, owner), "w") as f:
            f.write(now())
        print("asked watcher %s (pid %d) to stop at its next poll" % (owner, pid))
        post_message(board, whoami(), "%s watcher asked to stop" % owner)
        return
    ns = argparse.Namespace(name=owner, roles=a.roles, can=a.can, cost=a.cost, tool=a.tool,
                            model=a.model, best_for=a.best_for or "")
    _silent(lambda: cmd_join(ns, board))
    wt = os.path.abspath(a.worktree) if a.worktree else os.path.join(root, ".worktrees", owner)
    if not os.path.isdir(wt):
        base = a.base or _trunk()
        r = subprocess.run(["git", "-C", root, "worktree", "add", "-q", wt, "-b", owner, base],
                           capture_output=True, text=True)
        if r.returncode != 0:
            r = subprocess.run(["git", "-C", root, "worktree", "add", "-q", wt, owner], capture_output=True, text=True)
        if r.returncode != 0:
            sys.exit("could not create worktree %s: %s" % (wt, (r.stderr or r.stdout).strip()))
        print("worktree %s (branch %s)" % (wt, owner))
    if a.brief:
        bn = argparse.Namespace(agent=owner, text=a.brief, ticket="", file="", show=False, by=whoami())
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
    if _watcher_pid(board, owner):
        print("watcher for %s already running (pid %d); --stop first" % (owner, _watcher_pid(board, owner)))
        return
    try:
        os.unlink(_stop_file(board, owner))
    except OSError:
        pass
    mode = "acceptEdits" if a.safe else "bypassPermissions"
    kind = "cos" if a.cos else ("master" if a.master else "")
    cmd = a.exec or _worker_cmd(board, owner, a.model, mode, a.tool or "claude", master=kind)
    argv = [sys.executable, os.path.realpath(__file__), "watch", "--agent", owner, "--every", str(a.every),
            "--cwd", wt, "--exec", cmd, "--run-timeout", str(a.run_timeout),
            "--heartbeat", str(int(getattr(a, "heartbeat", 0) or 0))]
    # T-243: strip Git's LOCATION vars before handing the parent's environment
    # to a spawned/exec'd child, or an ambient GIT_DIR in *this* process
    # cascades into every agent this launches. _clean_git_env is deliberately
    # narrow: GIT_AUTHOR_*/GIT_COMMITTER_* survive, because this is the
    # fleet-launch env and stripping identity here would be a T-238-class
    # attribution loss (T-259 defect 3).
    env = dict(_clean_git_env(), TICKET_AGENT=owner, TICKETS_DIR=board,
               PATH=os.path.expanduser("~/.local/bin") + ":/opt/homebrew/bin:" + os.environ.get("PATH", ""))
    log_path = os.path.join(agents_dir(board), owner + ".watch.log")
    with open(log_path, "a") as lf:
        subprocess.Popen(argv, cwd=wt, env=env, stdout=lf, stderr=subprocess.STDOUT,
                         stdin=subprocess.DEVNULL, start_new_session=True)
    import time as _time
    _time.sleep(1.0)
    pid = _watcher_pid(board, owner)
    model = a.model or load_workforce(board).get(owner, {}).get("model") or "default"
    print("watcher for %s started%s; model=%s; log %s" % (owner, (" (pid %d)" % pid) if pid else "", model, log_path))
    print("cmd: %s" % cmd)
    post_message(board, whoami(), "%s spawned as a persistent worker (%s, model %s); it wakes whenever the board has work for it"
                 % (owner, a.tool or "claude", model))


UI_HTML = r"""<!doctype html><html><head><meta charset="utf-8"><title>Ticket board</title>
<meta name="viewport" content="width=device-width,initial-scale=1">
<style>
:root{--bg:#f7f7f5;--fg:#1c1c1a;--mute:#6b6b66;--line:#e2e2dd;--card:#fff;--ok:#2f8f4e;--warn:#c27a00;--bad:#c23b2b;--acc:#2b5fd9}
@media(prefers-color-scheme:dark){:root{--bg:#141413;--fg:#ececea;--mute:#9a9a94;--line:#2c2c2a;--card:#1d1d1b;--ok:#5ec27f;--warn:#e0a53d;--bad:#e5645a;--acc:#7aa2ff}}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--fg);font:14px/1.45 -apple-system,Segoe UI,Helvetica,Arial,sans-serif}
header{display:flex;gap:16px;align-items:baseline;padding:14px 20px;border-bottom:1px solid var(--line);position:sticky;top:0;background:var(--bg)}
h1{font-size:16px;margin:0}small{color:var(--mute)}main{padding:16px 20px;display:grid;gap:16px;grid-template-columns:repeat(auto-fit,minmax(340px,1fr))}
section{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:12px 14px;min-width:0}
section h2{font-size:12px;letter-spacing:.06em;text-transform:uppercase;color:var(--mute);margin:0 0 8px}
table{width:100%;border-collapse:collapse}td,th{text-align:left;padding:4px 6px;border-top:1px solid var(--line);vertical-align:top;font-size:13px}th{color:var(--mute);font-weight:500;border-top:0}
.bar{height:8px;background:var(--line);border-radius:4px;overflow:hidden}.bar i{display:block;height:100%;background:var(--acc)}
.tag{display:inline-block;padding:0 6px;border-radius:4px;font-size:11px;border:1px solid var(--line);color:var(--mute)}
.ok{color:var(--ok)}.warn{color:var(--warn)}.bad{color:var(--bad)}.mono{font-family:ui-monospace,Menlo,monospace;font-size:12px}
.msgs div{padding:4px 0;border-top:1px solid var(--line);font-size:13px}.wide{grid-column:1/-1}.num{text-align:right}
</style></head><body>
<header><h1 id="title">Ticket board</h1><small id="meta"></small><small id="clock" style="margin-left:auto"></small></header>
<main>
<section class="wide"><h2>Goals</h2><div id="goals" style="white-space:pre-wrap;font-size:13px"></div></section>
<section class="wide"><h2>Sprint</h2><div id="sprint"></div></section>
<section class="wide"><h2>Utilization (24h)</h2><table id="util"></table></section>
<section><h2>In flight</h2><table id="flight"></table></section>
<section><h2>Review queue</h2><table id="review"></table></section>
<section><h2>Agents</h2><table id="agents"></table></section>
<section><h2>Health</h2><table id="health"></table></section>
<section class="wide"><h2>Open tickets</h2><table id="open"></table></section>
<section class="wide"><h2>Messages</h2><div class="msgs" id="msgs"></div></section>
</main>
<script>
const esc=s=>String(s??'').replace(/[&<>]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;'}[c]));
const h=x=>x==null?'-':(x<1?Math.round(x*60)+'m':x<48?x.toFixed(1)+'h':(x/24).toFixed(1)+'d');
function row(cells,cls){return '<tr class="'+(cls||'')+'">'+cells.map(c=>'<td>'+c+'</td>').join('')+'</tr>'}
async function load(){const r=await fetch('/board.json?'+Date.now());const d=await r.json();
document.getElementById('title').textContent='Ticket board · '+d.project;
document.getElementById('meta').textContent='master '+(d.master||'nobody')+(d.cos?' · cos '+d.cos:'')+' · '+d.counts.done+' done / '+d.counts.total+' tickets';
document.getElementById('clock').textContent='updated '+new Date().toLocaleTimeString();
const s=d.sprint;document.getElementById('sprint').innerHTML=s?('<b>'+esc(s.id)+'</b> '+esc(s.goal)+'<div class="bar" style="margin:6px 0"><i style="width:'+(100*s.done/Math.max(1,s.total))+'%"></i></div><small>'+s.done+'/'+s.total+' done · '+s.in_flight+' in flight · '+s.blocked+' blocked · '+s.review+' in review'+(d.burn&&d.burn.done_per_day?' · '+d.burn.done_per_day.toFixed(1)+'/day, ETA '+(d.burn.eta_days?d.burn.eta_days.toFixed(1)+'d':'-'):'')+'</small>'):'no active sprint';
document.getElementById('goals').innerHTML=esc(d.goals||'(no MASTER.md yet -- tickets master init)');
document.getElementById('util').innerHTML='<tr><th>agent</th><th>state</th><th class="num">done</th><th>avg cycle</th><th>active</th><th>util</th><th class="num">wip</th><th class="num">review</th></tr>'+d.util.map(u=>row([esc(u.agent),'<span class="'+(u.state=='DOWN'?'bad':u.state=='busy'?'ok':'')+'">'+u.state+'</span>','<span class="num">'+u.done+'</span>',h(u.avg_cycle_h),h(u.active_h),'<div class="bar" style="width:120px;display:inline-block;vertical-align:middle"><i style="width:'+u.util_pct+'%"></i></div> '+Math.round(u.util_pct)+'%','<span class="num">'+u.in_flight+'</span>','<span class="num">'+u.in_review+'</span>'])).join('');
document.getElementById('flight').innerHTML='<tr><th>id</th><th>owner</th><th>title</th><th>last update</th></tr>'+d.in_flight.map(t=>row([t.id,esc(t.owner),esc(t.title),'<span class="'+(t.since_update>1.5?'bad':t.since_update>0.75?'warn':'ok')+'">'+h(t.since_update)+'</span>'])).join('')||row(['—','','',''] );
document.getElementById('review').innerHTML='<tr><th>id</th><th>owner</th><th>title</th><th>branch</th></tr>'+d.review.map(t=>row([t.id,esc(t.owner),esc(t.title),'<span class="mono">'+esc(t.commit)+'</span>'+(t.pr?' PR '+esc(t.pr):'')])).join('')||row(['empty','','','']);
document.getElementById('agents').innerHTML='<tr><th>agent</th><th>state</th><th>model</th><th class="num">done 24h</th><th>seen</th><th>ticket</th></tr>'+d.agents.map(a=>row([esc(a.name),'<span class="'+(a.state=='DOWN'?'bad':a.state=='busy'?'ok':'')+'">'+a.state+(a.watcher?' ●':'')+'</span>',esc(a.model||'-'),'<span class="num">'+a.done+'</span>',h(a.seen_h)+' ago',esc(a.ticket||'')])).join('');
document.getElementById('health').innerHTML=d.health.length?d.health.map(x=>row(['<span class="'+(x.sev=='CRIT'?'bad':x.sev=='WARN'?'warn':'')+'">'+x.sev+'</span>',esc(x.msg)])).join(''):row(['<span class="ok">clean</span>','']);
document.getElementById('open').innerHTML='<tr><th>id</th><th>status</th><th>pri</th><th>title</th><th>role</th><th>waits on</th></tr>'+d.open.map(t=>row([t.id,'<span class="tag">'+t.status+'</span>',t.priority,esc(t.title),esc(t.role),esc((t.waiting||[]).join(','))])).join('');
document.getElementById('msgs').innerHTML=d.messages.map(m=>'<div><small>'+esc(m.at)+'</small> <b>'+esc(m.from)+'</b>'+(m.to?' → '+esc(m.to):'')+(m.re?' <span class="tag">'+esc(m.re)+'</span>':'')+' '+esc(m.text)+'</div>').join('');}
load();setInterval(load,5000);
</script></body></html>"""


def board_snapshot(board, messages=40):
    """Everything the UI shows, as plain data. Read-only."""
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
    rows, burn = utilization(board, tickets, hours=24)
    agents = {r["owner"]: r for r in load_agents(board)}
    wf = load_workforce(board)
    out_agents = []
    for r in rows:
        rec = agents.get(r["agent"], {})
        out_agents.append({"name": r["agent"], "state": r["state"], "model": wf.get(r["agent"], {}).get("model", ""),
                           "done": r["done"], "seen_h": r["seen_h"], "ticket": rec.get("ticket", ""),
                           "watcher": bool(_watcher_pid(board, r["agent"]))})
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
        goals = "OBJECTIVE%s\n%s\n\n%s" % (" (met)" if obj.get("done") else "", obj.get("text", ""), goals)
    util_rows = [r for r in rows if r["state"] != "DOWN"]
    return {
        "project": os.path.basename(os.path.dirname(board)), "generated": now(),
        "master": m.get("owner", ""), "cos": m.get("cos", ""), "counts": counts, "sprint": sprint, "burn": burn,
        "goals": goals,
        "util": sorted(util_rows, key=lambda r: (-r["done"], r["agent"])),
        "in_flight": [{"id": t["id"], "owner": t.get("owner", ""), "title": t["title"], "since_update": timing(t)["since_update"]}
                      for t in tickets if t["status"] == "claimed"],
        "review": [{"id": t["id"], "owner": t.get("owner", ""), "title": t["title"], "commit": t.get("commit", ""), "pr": t.get("pr", "")}
                   for t in tickets if t["status"] == "review"],
        "open": [{"id": t["id"], "status": LABEL.get(t["status"], t["status"]), "priority": t.get("priority", 2), "title": t["title"],
                  "role": t.get("role", ""), "waiting": [d for d in t.get("deps", []) if d not in done]}
                 for t in tickets if t["status"] in ("open", "blocked")],
        "agents": out_agents,
        "health": [{"sev": s, "msg": msg} for s, msg, _fix in health(board, tickets) if s in ("CRIT", "WARN")][:12],
        "messages": [{"at": x.get("at", "")[5:16].replace("T", " "), "from": x.get("from", ""), "to": x.get("to", ""),
                      "re": x.get("re", ""), "text": x.get("text", "")} for x in load_messages(board)[-messages:]][::-1],
    }


def cmd_ui(a, board):
    """Local status UI: serves an auto-refreshing page and /board.json (read-only)."""
    from http.server import BaseHTTPRequestHandler, HTTPServer

    if a.json:
        print(json.dumps(board_snapshot(board), indent=2))
        return

    class H(BaseHTTPRequestHandler):
        def do_GET(self):
            if self.path.startswith("/board.json"):
                body = json.dumps(_safe(lambda: board_snapshot(board), {"error": "snapshot failed"})).encode()
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

        def log_message(self, *args):
            pass

    srv = HTTPServer((a.host, a.port), H)
    print("board UI: http://%s:%d  (Ctrl-C to stop; read-only)" % (a.host, a.port))
    if a.open:
        import subprocess
        subprocess.Popen(["open", "http://%s:%d" % (a.host, a.port)])
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass


def cmd_guide(a, board):
    print(GUIDE)


# ---- hooks: wire a tool so the board reaches the agent every turn ----------

INBOX_HOOK_CMD = (
    'PATH="$HOME/.local/bin:/opt/homebrew/bin:$PATH"; '
    '[ -n "$TICKET_AGENT" ] && tickets inbox --keep --limit 8 2>/dev/null | sed "s/^/[board] /" || true'
)

CURSOR_HOOK = r'''#!/usr/bin/env python3
"""Cursor hook installed by `tickets hooks cursor`: surface new board messages.
Reads the hook event JSON on stdin, writes {additional_context, user_message}
on stdout. Keeps its own watermark so `tickets inbox` read-state is untouched.
Identity comes from $TICKET_AGENT (defaults to the name given at install)."""
import json, os, sys
from pathlib import Path

AGENT = os.environ.get("TICKET_AGENT", "%(agent)s")
BOARD = Path(os.environ.get("TICKETS_DIR", %(board)r))
STATE = Path(__file__).resolve().parent / "state" / ("board-%%s.json" %% AGENT)

def main():
    try:
        event = json.loads(sys.stdin.read() or "{}")
    except ValueError:
        event = {}
    path = BOARD / "messages.jsonl"
    if not path.is_file():
        print("{}"); return 0
    STATE.parent.mkdir(parents=True, exist_ok=True)
    last = ""
    if STATE.exists():
        try: last = json.loads(STATE.read_text()).get("last_at", "")
        except ValueError: pass
    new = []
    for ln in path.read_text().splitlines():
        try: m = json.loads(ln)
        except ValueError: continue
        if (m.get("at") or "") <= last: continue
        to = m.get("to") or ""
        if to not in ("", "all", "everyone", AGENT): continue
        if m.get("from") == AGENT and to in ("", "all", "everyone"): continue
        new.append(m)
    out = {}
    if new:
        lines = ["Ticket board: %%d new message(s) for %%s. Reply with `tickets msg`." %% (len(new), AGENT)]
        for m in new[-12:]:
            lines.append("- %%s %%s -> %%s%%s: %%s" %% (m.get("at","?")[5:16], m.get("from","?"), m.get("to") or "everyone",
                         (" [%%s]" %% m["re"]) if m.get("re") else "", (m.get("text") or "")[:220]))
        out["additional_context"] = "\n".join(lines)[:3500]
        out["user_message"] = "%%d new ticket-board message(s) for %%s" %% (len(new), AGENT)
        STATE.write_text(json.dumps({"last_at": new[-1].get("at", last)}))
    print(json.dumps(out)); return 0

if __name__ == "__main__":
    raise SystemExit(main())
'''


def cmd_hooks(a, board):
    """Install board hooks for a tool: claude (global settings.json) or cursor
    (project .cursor/hooks). Codex has no hooks; AGENTS.md carries the protocol."""
    root = os.path.dirname(board)
    script = os.path.realpath(__file__)
    def ours(entry):
        for hk in entry.get("hooks", []) if isinstance(entry, dict) else []:
            cmd = str(hk.get("command", ""))
            if script in cmd or "tickets.py" in cmd or cmd == INBOX_HOOK_CMD or "tickets inbox --keep" in cmd:
                return True
        return False
    if a.tool == "claude":
        path = os.path.expanduser(getattr(a, "settings", "") or "~/.claude/settings.json")
        try:
            with open(path) as f:
                s = json.load(f)
        except (IOError, ValueError):
            s = {}
        hooks = s.setdefault("hooks", {})
        ss = hooks.setdefault("SessionStart", [])
        ss[:] = [h for h in ss if not ours(h)]
        ss.append({"matcher": "startup|resume|clear|compact",
                   "hooks": [{"type": "command", "command": "%s board" % script, "timeout": 10}]})
        ups = hooks.setdefault("UserPromptSubmit", [])
        ups[:] = [h for h in ups if not ours(h)]
        ups.append({"hooks": [{"type": "command", "command": INBOX_HOOK_CMD, "timeout": 10}]})
        # Keep a turn alive while the agent still has board work. Loop-guarded
        # (stop_hook_active) and rate-capped; --no-stop removes it.
        st = hooks.setdefault("Stop", [])
        st[:] = [h for h in st if not ours(h)]
        if getattr(a, "stop", True):
            st.append({"hooks": [{"type": "command", "command": "%s stop-hook" % script, "timeout": 15}]})
        if not st:
            hooks.pop("Stop", None)
        allow = s.setdefault("permissions", {}).setdefault("allow", [])
        for p in ("Bash(tickets:*)", "Bash(%s:*)" % script):
            if p not in allow:
                allow.append(p)
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        tmp = path + ".tmp"
        with open(tmp, "w") as f:
            json.dump(s, f, indent=2)
        os.replace(tmp, path)
        print("Claude Code hooks in %s: SessionStart -> board; UserPromptSubmit -> inbox; Stop -> %s."
              % (path, "keep working while board work remains" if getattr(a, "stop", True) else "off"))
        print("Launch sessions as: TICKET_AGENT=<name> claude")
        return
    if a.tool == "codex":
        hp = os.path.expanduser(getattr(a, "hooks_file", "") or "~/.codex/hooks.json")
        agent = a.agent or whoami()
        if agent.startswith("agent-"):
            sys.exit("codex hooks need --agent <name> (or TICKET_AGENT)")
        wt = os.path.abspath(a.worktree) if getattr(a, "worktree", "") else ""
        try:
            with open(hp) as f:
                cfg = json.load(f)
        except (IOError, ValueError):
            cfg = {}
        hooks = cfg.setdefault("hooks", {})
        cmd = "%s codex-hook --agent %s%s" % (script, agent, (" --worktree %s" % wt) if wt else "")
        for ev in ("SessionStart", "UserPromptSubmit"):
            lst = hooks.setdefault(ev, [])
            # replace only our own earlier entry for this agent; keep everything else
            lst[:] = [e for e in lst if not ("codex-hook --agent %s" % agent) in json.dumps(e)]
            lst.append({"hooks": [{"type": "command", "command": cmd, "timeout": 5,
                                   "statusMessage": "Checking the ticket board",
                                   "additionalContextLimit": 2000}]})
        os.makedirs(os.path.dirname(hp) or ".", exist_ok=True)
        tmp = hp + ".tmp"
        with open(tmp, "w") as f:
            json.dump(cfg, f, indent=2)
        os.replace(tmp, hp)
        print("Codex hooks in %s for %s%s: SessionStart + UserPromptSubmit -> board context. "
              "Trust it with /hooks in Codex; AGENTS.md carries the rules." % (hp, agent, (" (scoped to %s)" % wt) if wt else ""))
        return
    if a.tool == "cursor":
        hdir = os.path.join(root, ".cursor", "hooks")
        os.makedirs(hdir, exist_ok=True)
        sp = os.path.join(hdir, "tickets-board.py")
        if os.path.exists(sp) and not a.force:
            print("%s exists; --force to overwrite" % sp)
        else:
            with open(sp, "w") as f:
                f.write(CURSOR_HOOK % {"agent": a.agent or "cursor", "board": board})
            os.chmod(sp, 0o755)
        hp = os.path.join(root, ".cursor", "hooks.json")
        try:
            with open(hp) as f:
                cfg = json.load(f)
        except (IOError, ValueError):
            cfg = {"version": 1, "hooks": {}}
        entry = {"command": ".cursor/hooks/tickets-board.py", "timeout": 15}
        for ev in ("sessionStart", "beforeSubmitPrompt", "stop"):
            lst = cfg.setdefault("hooks", {}).setdefault(ev, [])
            if not any("tickets-board" in json.dumps(x) for x in lst):
                lst.append(dict(entry, **({"loop_limit": 2} if ev == "stop" else {})))
        with open(hp, "w") as f:
            json.dump(cfg, f, indent=2)
        print("Cursor: %s + %s (sessionStart, beforeSubmitPrompt, stop). Enable Hooks in Cursor settings; "
              "set TICKET_AGENT in the shell Cursor starts from." % (os.path.relpath(hp, root), os.path.relpath(sp, root)))
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

    print("board: %s" % board)
    for w in written:
        print("wrote: %s" % w)
    print("\nClaude Code picks this up from its global SessionStart hook.")
    print("Codex and Cursor read AGENTS.md; Cursor also gets .cursor/rules/tickets.mdc.")


PROTOCOL = """## Shared ticket board

Work here is coordinated through a ticket board that Claude Code, Codex and
Cursor all share. It lives in `.tickets/` and is driven only through the
`tickets` CLI -- never edit files in `.tickets/` by hand, or atomic claiming
breaks and two agents will do the same work.

Run `tickets board` for the current state, or `tickets graph` to see the whole
dependency tree with each node's status and owner.

**Connect first** (once per session; `tickets connect` prints the long form):

    export TICKET_AGENT=<your-unique-name>     # claude-opus, codex, grok ...
    tickets join $TICKET_AGENT --roles backend --can docker,browser --cost high
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


def main():
    p = _LoudArgumentParser(prog="tickets", description=__doc__.split("\n")[0])
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
    c.add_argument("--tool", default="", help="claude|codex|cursor|grok")
    c.add_argument("--model", default="", help="e.g. opus, sonnet, gpt-5, grok-4")
    c.add_argument("--best-for", default="", help="free text; keywords are matched against ticket titles by `route`")
    c.set_defaults(fn=cmd_join)

    c = sub.add_parser("route", help="master: suggest an owner for every open ticket by model/roles/capabilities/cost")
    c.add_argument("--claim", action="store_true", help="hard-assign the ready ones (claims on their behalf)")
    c.add_argument("--redo", action="store_true", help="recompute tickets that already have a suggestion")
    c.add_argument("--only", nargs="*", help="restrict to these agents")
    c.set_defaults(fn=cmd_route)

    c = sub.add_parser("connect", help="print how any agent connects to this board")
    c.set_defaults(fn=cmd_connect)

    c = sub.add_parser("pending", help="exit 0 if there is work for the agent (messages, held or ready ticket)")
    c.add_argument("--agent", default="")
    c.add_argument("--json", action="store_true")
    c.set_defaults(fn=cmd_pending)

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
    c.add_argument("--done", default=None, metavar="EVIDENCE", help="mark the objective met, with evidence")
    c.add_argument("--by", default="")
    c.set_defaults(fn=cmd_objective)

    c = sub.add_parser("drive", help="set the objective and spawn the master seat with a heartbeat")
    c.add_argument("text", nargs="?", default="")
    c.add_argument("--by", "--as", dest="by", default="", help="agent name for the master seat (default TICKET_AGENT)")
    c.add_argument("--heartbeat", type=int, default=30, help="minutes between objective wake-ups")
    c.add_argument("--every", type=int, default=60, help="seconds between board polls")
    c.add_argument("--tool", default="claude", help="claude | codex | cursor | cursor+claude")
    c.add_argument("--model", default="")
    c.add_argument("--restart", action="store_true", help="stop an existing watcher for this seat first")
    c.set_defaults(fn=cmd_drive)

    c = sub.add_parser("watch", help="poll the board and launch a worker when there is work for the agent")
    c.add_argument("--agent", default="")
    c.add_argument("--every", type=int, default=60, help="seconds between polls")
    c.add_argument("--heartbeat", type=int, default=0,
                   help="master seat only: also wake every N minutes to drive the objective (0 = off)")
    c.add_argument("--exec", default="", help="command to run (default: headless claude with `tickets prompt`)")
    c.add_argument("--cwd", default="", help="directory to run in (default: repo root; use the agent's worktree)")
    c.add_argument("--permission-mode", default="acceptEdits", help="for the default claude command")
    c.add_argument("--allowed-tools", default="", help='e.g. "Bash Edit Write Read"')
    c.add_argument("--max-runs", type=int, default=0)
    c.add_argument("--run-timeout", type=int, default=90, help="minutes per run before it is killed (0 = none)")
    c.add_argument("--beat-every", type=int, default=0,
                   help="seconds between in-run heartbeats (0 = TICKETS_RUN_HEARTBEAT_SECS, default 30)")
    c.add_argument("--once", action="store_true", help="check once; exit 0 if work, 1 if not")
    c.add_argument("--dry-run", action="store_true")
    c.add_argument("--verbose", action="store_true")
    c.set_defaults(fn=cmd_watch)

    c = sub.add_parser("codex-hook", help="(hook body) Codex SessionStart/UserPromptSubmit board context")
    c.add_argument("--agent", required=True)
    c.add_argument("--worktree", default="")
    c.set_defaults(fn=cmd_codex_hook)

    c = sub.add_parser("boot", help="every startup step: join, hooks, check-in, briefing (idempotent)")
    c.add_argument("--agent", default="")
    c.add_argument("--tool", default="", help="claude | codex | cursor (installs that tool's hooks)")
    c.add_argument("--roles", default=None)
    c.add_argument("--can", default=None)
    c.add_argument("--cost", choices=("low", "medium", "high"), default=None)
    c.add_argument("--model", default="")
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
    c.add_argument("--tool", default="claude", help="claude | codex | <executable>")
    c.add_argument("--roles", default=None)
    c.add_argument("--can", default=None)
    c.add_argument("--cost", choices=("low", "medium", "high"), default=None)
    c.add_argument("--best-for", default="")
    c.add_argument("--brief", default="", help="standing context for this worker")
    c.add_argument("--worktree", default="", help="default .worktrees/<name>")
    c.add_argument("--base", default="", help="branch/ref to create the worktree from (default main)")
    c.add_argument("--every", type=int, default=60)
    c.add_argument("--run-timeout", type=int, default=90)
    c.add_argument("--heartbeat", type=int, default=0,
                   help="with --master: also wake every N minutes to drive the objective (0 = off)")
    c.add_argument("--safe", action="store_true", help="worker confirms edits instead of running unattended")
    c.add_argument("--master", action="store_true",
                   help="spawn the board master/planner (scope, routing by complexity, escalations)")
    c.add_argument("--cos", action="store_true",
                   help="spawn the chief of staff (review, unblock, merge) under the current master")
    c.add_argument("--exec", default="", help="override the worker command entirely")
    c.add_argument("--stop", action="store_true", help="ask the watcher to exit at its next poll")
    c.add_argument("--list", action="store_true")
    c.set_defaults(fn=cmd_spawn)

    c = sub.add_parser("ui", help="local status page: http://localhost:8765 (read-only, auto-refresh)")
    c.add_argument("--port", type=int, default=8765)
    c.add_argument("--host", default="127.0.0.1")
    c.add_argument("--open", action="store_true", help="open it in the browser")
    c.add_argument("--json", action="store_true", help="print the snapshot instead of serving")
    c.set_defaults(fn=cmd_ui)

    c = sub.add_parser("guide", help="print the startup guide for claude / codex / cursor")
    c.set_defaults(fn=cmd_guide)

    c = sub.add_parser("brief", help="give an agent or a ticket context (shown on claim, in prompt, in boot)")
    c.add_argument("agent", nargs="?", default="")
    c.add_argument("text", nargs="?", default="")
    c.add_argument("--ticket", default="", help="attach to a ticket instead (owner is messaged)")
    c.add_argument("--file", default="", help="replace the agent brief from a file")
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

    c = sub.add_parser("hooks", help="wire a tool to the board: claude | cursor | codex")
    c.add_argument("tool", choices=("claude", "cursor", "codex"))
    c.add_argument("--agent", default="", help="agent name baked into the hook (codex/cursor)")
    c.add_argument("--worktree", default="", help="codex: only fire inside this worktree")
    c.add_argument("--settings", default="", help="claude: settings.json path (default ~/.claude/settings.json)")
    c.add_argument("--hooks-file", default="", help="codex: hooks.json path (default ~/.codex/hooks.json)")
    c.add_argument("--no-stop", dest="stop", action="store_false", help="claude: do not install the Stop hook")
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
    c.add_argument("--owner", "-o")
    c.set_defaults(fn=cmd_inbox)

    c = sub.add_parser("review", help="submit finished work for the master to review + merge")
    c.add_argument("id")
    c.add_argument("--notes", "-n", default="", help="what to look at: paths, tests run, decisions")
    c.add_argument("--pr", default="", help="PR number or URL if you opened one")
    c.add_argument("--owner", "-o")
    c.add_argument("--force", action="store_true")
    c.set_defaults(fn=cmd_review)

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
    c.set_defaults(fn=cmd_next)

    c = sub.add_parser("claim", help="atomically claim a specific ticket")
    c.add_argument("id")
    c.add_argument("--owner", "-o")
    c.set_defaults(fn=cmd_claim)

    c = sub.add_parser("done", help="mark a ticket done")
    c.add_argument("id")
    c.add_argument("--notes", "-n", default="", help="handoff text for dependent tickets")
    c.add_argument("--no-notes", action="store_true", help="allow empty handoff")
    c.add_argument("--force", action="store_true", help="skip the branch/clean-tree rule")
    c.set_defaults(fn=cmd_done)

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
    c.add_argument("--notes", "-n", default="", help="why it's being reopened (recorded as a note)")
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

    c = sub.add_parser("mine", help="list tickets claimed by this agent")
    c.add_argument("--owner", "-o")
    c.set_defaults(fn=cmd_mine)

    c = sub.add_parser("init", help="install the board protocol into this project")
    c.add_argument("--track", action="store_true", help="commit the board to git instead of gitignoring it")
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
    if not a.cmd:
        p.print_help()
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
