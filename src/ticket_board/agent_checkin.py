"""Canonical flocked agent checkin (T-836).

Lives in the packaged wheel so `tickets` does not import repo-root tickets.py.
Root tickets.py and ticket_board.cli both call this one implementation.
"""
from __future__ import annotations

import glob
import json
import os
import subprocess
import sys
from datetime import datetime, timezone


def now():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def agents_dir(board):
    return os.path.join(board, "agents")


def _clean_git_env():
    env = {k: v for k, v in os.environ.items()
           if not k.startswith("GIT_")}
    return env


def _git(args, cwd=None):
    try:
        out = subprocess.run(
            ["git"] + list(args), capture_output=True, text=True, timeout=10,
            cwd=cwd or os.getcwd(), env=_clean_git_env())
    except (OSError, subprocess.TimeoutExpired):
        return None
    if out.returncode != 0:
        return None
    return (out.stdout or "").strip()


def _git_state_raw(cwd=None):
    here = cwd or os.getcwd()
    top = _git(["rev-parse", "--show-toplevel"], cwd=here)
    if not top:
        return None, False
    real_top = os.path.realpath(top)
    real_here = os.path.realpath(here)
    if os.path.commonpath([real_top, real_here]) != real_top:
        sys.stderr.write(
            "tickets: git resolved toplevel %s which does not contain cwd %s -- "
            "treating as unresolved rather than trusting it (T-243)\n" % (real_top, real_here))
        return None, True
    branch = _git(["rev-parse", "--abbrev-ref", "HEAD"], cwd=here) or "?"
    sha = _git(["rev-parse", "--short", "HEAD"], cwd=here) or "?"
    dirty = _git(["status", "--porcelain"], cwd=here)
    return {
        "top": top,
        "branch": branch,
        "sha": sha,
        "dirty": len(dirty.splitlines()) if dirty else 0,
    }, False


def _load_tickets(board):
    out = []
    for path in sorted(glob.glob(os.path.join(board, "T-*.json"))):
        try:
            with open(path) as f:
                out.append(json.load(f))
        except (ValueError, IOError):
            continue
    return out


def _current_ticket(board, owner):
    mine = [t for t in _load_tickets(board) if t.get("owner") == owner]
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


class _AgentLock:
    """Serialize read-modify-write on one agents/<name>.json across processes."""

    def __init__(self, board, owner):
        os.makedirs(agents_dir(board), exist_ok=True)
        self.path = os.path.join(agents_dir(board), owner + ".json.lock")
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


def _agent_rec(board, owner):
    path = os.path.join(agents_dir(board), owner + ".json")
    try:
        with open(path) as f:
            return json.load(f)
    except (IOError, ValueError):
        return {}


def _agent_update(board, owner, mutate):
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


def checkin(board, owner, ticket=None, note="", cwd=None):
    """Record where this agent is working: cwd, worktree root, branch, sha.

    `cwd` is the tree to stamp. Spawn must pass the target --worktree here:
    the launcher process cwd is a different checkout (T-839 live restart).
    """
    here = os.path.abspath(cwd) if cwd else os.getcwd()
    _state, _mismatch = _git_state_raw(here)
    g = _state or {}
    top = g.get("top", "")
    fields = {
        "owner": owner,
        "cwd": here,
        "worktree": top or (here if cwd else ""),
        "branch": g.get("branch", ""),
        "sha": g.get("sha", ""),
        "dirty": g.get("dirty", 0),
        "git_mismatch": bool(_mismatch),
        "ticket": ticket if ticket is not None else _current_ticket(board, owner),
        "note": note,
        "seen": now(),
    }

    def _apply(rec):
        rec.update(fields)

    return _agent_update(board, owner, _apply)
