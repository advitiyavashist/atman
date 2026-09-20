"""T-946: worktree cleanup as an automated graph node plus a safe sweep.

Two node kinds live on the board: ``agent`` (a model turn) and ``automated``
(Atman runs a deterministic action, no model). When an implementation ticket
is created with a worktree, Atman adds a child ``cleanup worktree for T-x``
with ``--after T-x``. The success trigger runs that child; it never posts a
task to a seat on the happy path.

Removal happens only when every hold is true. Anything that needs judgment
converts the node to an agent ticket (owner or CoS) with keep/remove choices.
``git worktree remove`` + ``prune`` only. Branches are never deleted (T-542).
"""
from __future__ import annotations

import errno
import json
import os
import re
import subprocess
from datetime import datetime, timezone

ACTION = "cleanup_worktree"
KIND_AGENT = "agent"
KIND_AUTOMATED = "automated"

DISPOSABLE_NAMES = {
    ".venv", "venv", "__pycache__", ".pytest_cache",
    "node_modules", "build", "dist",
}
DISPOSABLE_SUFFIXES = (".egg-info",)

ESCALATE_REASONS = (
    "unmerged", "dirty", "custody", "open_pr", "live_cwd",
    "runtime_symlink", "nested",
)

RUNTIME_LINK_NAMES = ("tickets", "atm")
RUNTIME_DIR_NAMES = ("atman-runtime-current",)


def utcnow():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def ticket_kind(t):
    if (t.get("kind") or "").strip() == KIND_AUTOMATED:
        return KIND_AUTOMATED
    return KIND_AGENT


def is_automated(t):
    return ticket_kind(t) == KIND_AUTOMATED


def cleanup_target(t):
    auto = t.get("automated") or {}
    if auto.get("action") == ACTION:
        return (auto.get("target") or "").strip()
    return ""


def find_cleanup_child(tickets, parent_id):
    for t in tickets:
        if cleanup_target(t) == parent_id:
            return t
    return None


def _run(args, cwd=None, env=None, timeout=None):
    clean = os.environ.copy()
    for key in ("GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE", "GIT_COMMON_DIR"):
        clean.pop(key, None)
    if env:
        clean.update(env)
    return subprocess.run(list(args), cwd=cwd, capture_output=True, text=True,
                          env=clean, timeout=timeout)


def default_origin_ref(cwd, git_fn=None):
    git_fn = git_fn or (lambda *a, **k: _run(["git", *a], cwd=k.get("cwd", cwd)))
    for ref in ("origin/HEAD", "origin/main", "origin/master"):
        r = git_fn("rev-parse", "--verify", "-q", ref, cwd=cwd)
        if r.returncode == 0 and (r.stdout or "").strip():
            if ref == "origin/HEAD":
                # origin/HEAD -> origin/main
                name = git_fn("rev-parse", "--abbrev-ref", "origin/HEAD", cwd=cwd)
                got = (name.stdout or "").strip()
                if got:
                    return got
            return ref
    return "origin/main"


def worktree_size_mb(path):
    total = 0
    for root, dirs, files in os.walk(path):
        # skip nested .git objects; they live in the common dir for linked trees
        if os.path.basename(root) == ".git" and os.path.isdir(os.path.join(root)):
            dirs[:] = []
            continue
        for name in files:
            try:
                total += os.path.getsize(os.path.join(root, name))
            except OSError:
                continue
    return round(total / (1024.0 * 1024.0), 3)


def _porcelain(cwd, ignored=False, git_fn=None):
    git_fn = git_fn or (lambda *a, **k: _run(["git", *a], cwd=k.get("cwd", cwd)))
    args = ["status", "--porcelain", "-u"]
    if ignored:
        args.append("--ignored")
    r = git_fn(*args, cwd=cwd)
    return [ln for ln in (r.stdout or "").splitlines() if ln.strip()]


def _ignored_paths(cwd, git_fn=None):
    out = []
    for ln in _porcelain(cwd, ignored=True, git_fn=git_fn):
        if ln.startswith("!! "):
            out.append(ln[3:].rstrip("/"))
    return out


def _is_disposable(rel):
    rel = rel.replace("\\", "/").strip("/")
    if not rel:
        return True
    first = rel.split("/", 1)[0]
    if first in DISPOSABLE_NAMES:
        return True
    if any(part.endswith(DISPOSABLE_SUFFIXES) for part in rel.split("/")):
        return True
    return False


def is_linked_worktree(path):
    gitfile = os.path.join(path, ".git")
    if os.path.isfile(gitfile):
        try:
            text = open(gitfile).read()
        except OSError:
            return False
        return text.startswith("gitdir:")
    return False


def is_nested_or_unusual(path, repo_root=None):
    """Main checkouts, repo roots, and nested-git oddities are not sweep targets."""
    if not path or not os.path.isdir(path):
        return "missing"
    real = os.path.realpath(path)
    if real in ("/", os.path.expanduser("~")):
        return "nested"
    if repo_root and os.path.realpath(repo_root) == real:
        return "nested"
    if not is_linked_worktree(path):
        return "nested"
    # a second .git directory inside the tree (not the worktree gitfile) is unusual
    for root, dirs, _files in os.walk(path):
        if root == path:
            if ".git" in dirs:
                return "nested"
            continue
        if ".git" in dirs or ".git" in _files:
            return "nested"
        # do not descend forever
        if root.count(os.sep) - path.count(os.sep) > 4:
            dirs[:] = []
    return ""


def runtime_symlink_targets(extra=None):
    found = []
    home_bin = os.path.join(os.path.expanduser("~"), ".local", "bin")
    for name in RUNTIME_LINK_NAMES:
        p = os.path.join(home_bin, name)
        if os.path.islink(p) or os.path.isfile(p):
            try:
                found.append(os.path.realpath(p))
            except OSError:
                continue
    env = (os.environ.get("ATMAN_RUNTIME_CURRENT") or "").strip()
    if env:
        found.append(os.path.realpath(env))
    for extra_path in extra or ():
        if extra_path:
            found.append(os.path.realpath(extra_path))
    # directory named atman-runtime-current that a bin link may resolve through
    out = []
    for p in found:
        out.append(p)
        parent = os.path.dirname(p)
        if os.path.basename(parent) in RUNTIME_DIR_NAMES:
            out.append(parent)
        if os.path.basename(p) in RUNTIME_DIR_NAMES:
            out.append(p)
    return out


def is_runtime_symlink_target(path, extra=None):
    real = os.path.realpath(path)
    for target in runtime_symlink_targets(extra):
        if real == target or real == os.path.dirname(target):
            return True
        # bin link -> .../atman-runtime-current/tickets.py
        if target.startswith(real + os.sep):
            return True
    return False


def _find_lsof():
    for cand in (os.environ.get("TICKETS_GC_LSOF") or "",
                 "lsof", "/usr/sbin/lsof", "/usr/bin/lsof", "/bin/lsof"):
        if not cand:
            continue
        if os.path.sep in cand:
            if os.path.isfile(cand) and os.access(cand, os.X_OK):
                return cand
        else:
            # bare name: let exec fail later if missing; prefer which
            for d in (os.environ.get("PATH") or "").split(os.pathsep):
                p = os.path.join(d, cand)
                if os.path.isfile(p) and os.access(p, os.X_OK):
                    return p
    return ""


def _proc_live_cwds(real):
    """Walk /proc for cwds under ``real``. None if /proc is unavailable.

    Fail-closed: any unreadable cwd (except a pid that vanished) returns a
    single unknown row. Callers may try a targeted lsof probe of this
    worktree before treating that unknown as final. Own-uid EACCES is not
    skipped -- a same-uid helper whose cwd cannot be read still blocks GC
    unless a targeted probe proves the worktree is unused.
    """
    proc = "/proc"
    if not os.path.isdir(proc):
        return None
    try:
        names = os.listdir(proc)
    except OSError as exc:
        return [{"pid": 0, "cmd": "proc scan failed: %s" % exc, "unknown": True}]
    hits = []
    for name in names:
        if not name.isdigit():
            continue
        try:
            cwd = os.path.realpath(os.readlink(os.path.join(proc, name, "cwd")))
        except OSError as exc:
            # Processes can exit during enumeration. Other errors leave
            # their cwd unknown and must not authorize removal.
            if exc.errno in (errno.ENOENT, errno.ESRCH):
                continue
            return [{"pid": int(name), "cmd": "proc cwd failed: %s" % exc,
                     "unknown": True}]
        if cwd == real or cwd.startswith(real + os.sep):
            cmd = name
            try:
                cmd = open(os.path.join(proc, name, "comm")).read().strip() or name
            except OSError:
                pass
            hits.append({"pid": int(name), "cmd": cmd})
    return hits


def _lsof_live_cwds(real):
    """Targeted ``lsof +D`` probe of one worktree. May contain unknown=True."""
    hits = []
    lsof = _find_lsof()
    if not lsof:
        return [{"pid": 0, "cmd": "lsof-missing", "unknown": True}]

    def unknown(detail):
        return [{"pid": 0, "cmd": detail, "unknown": True}]

    try:
        r = _run([lsof, "-nP", "-a", "-d", "cwd", "+D", real, "-Fpcfn"])
    except (OSError, subprocess.SubprocessError, UnicodeError) as exc:
        return unknown("lsof failed: %s" % exc)
    # Status 1 with no output or diagnostics is lsof's no-match result.
    # Never retry an error into a success: incomplete evidence must preserve.
    if r.returncode == 1 and not r.stdout.strip() and not r.stderr.strip():
        return []
    if r.returncode != 0 or r.stderr.strip():
        return unknown("lsof failed (exit %s): %s" % (r.returncode, r.stderr.strip()))
    pid, cmd, fd = None, None, None
    complete = False
    for line in r.stdout.splitlines():
        tag, value = line[:1], line[1:]
        if tag == "p" and value.isdigit() and int(value) > 0:
            if pid is not None and not complete:
                return unknown("unparseable lsof output")
            pid, cmd, fd, complete = int(value), None, None, False
        elif tag == "c" and pid is not None and value:
            cmd = value
        elif tag == "f" and pid is not None and value == "cwd":
            fd = value
        elif tag == "n" and pid and cmd and fd == "cwd" and os.path.isabs(value):
            name_real = os.path.realpath(value)
            complete = True
            if name_real == real or name_real.startswith(real + os.sep):
                hits.append({"pid": pid, "cmd": cmd})
        else:
            return unknown("unparseable lsof output")
    if not complete:
        return unknown("empty or incomplete lsof output")
    return hits


def live_cwds(path, lsof_fn=None):
    """Processes whose cwd is this worktree (including interactive shells)."""
    real = os.path.realpath(path)
    if lsof_fn:
        return lsof_fn(real)
    proc_rows = _proc_live_cwds(real)
    if proc_rows is not None and not any(x.get("unknown") for x in proc_rows):
        return proc_rows
    # Incomplete /proc (EACCES on an unrelated helper is common on GHA): a
    # targeted lsof of THIS worktree can still prove it unused. If lsof is
    # also inconclusive, keep the /proc unknown -- fail closed.
    lsof_rows = _lsof_live_cwds(real)
    if not any(x.get("unknown") for x in lsof_rows):
        return lsof_rows
    if proc_rows is not None:
        return proc_rows
    return lsof_rows


def open_prs_for_branch(branch, cwd, gh_fn=None):
    """Return matching PRs, or an unknown marker unless absence is proven."""
    def unknown(detail):
        return [{"unknown": True, "error": "gh pr list: %s" % detail}]

    if not branch:
        return unknown("branch is unknown")
    try:
        if gh_fn:
            rows = gh_fn(branch, cwd)
        else:
            r = _run(["gh", "pr", "list", "--head", branch, "--state", "open",
                      "--json", "number,url,headRefName"], cwd=cwd, timeout=30)
            if r.returncode != 0 or (r.stderr or "").strip():
                err = (r.stderr or r.stdout or "").strip()
                return unknown("exit %s: %s" % (r.returncode, err or "lookup failed"))
            rows = json.loads(r.stdout)
    except (OSError, subprocess.SubprocessError, UnicodeError, ValueError, TypeError) as exc:
        return unknown("%s: %s" % (type(exc).__name__, exc))
    if not isinstance(rows, list):
        return unknown("expected a JSON list")
    # Validate the entire response before filtering: an invalid unrelated row
    # also leaves the result uncertain and must never authorize removal.
    for index, row in enumerate(rows):
        if (not isinstance(row, dict)
                or type(row.get("number")) is not int or row["number"] <= 0
                or not isinstance(row.get("url"), str) or not row["url"].strip()
                or not isinstance(row.get("headRefName"), str)
                or not row["headRefName"].strip()):
            return unknown("entry %s requires number (positive integer), url and "
                           "headRefName (non-empty strings)" % index)
    return [row for row in rows if row["headRefName"] == branch]


def head_contained_in_origin(cwd, git_fn=None, origin_ref=None):
    git_fn = git_fn or (lambda *a, **k: _run(["git", *a], cwd=k.get("cwd", cwd)))
    ref = origin_ref or default_origin_ref(cwd, git_fn=git_fn)
    head = git_fn("rev-parse", "HEAD", cwd=cwd)
    if head.returncode != 0:
        return False, "", ref
    sha = (head.stdout or "").strip()
    chk = git_fn("merge-base", "--is-ancestor", sha, ref, cwd=cwd)
    return chk.returncode == 0, sha, ref


def current_branch(cwd, git_fn=None):
    git_fn = git_fn or (lambda *a, **k: _run(["git", *a], cwd=k.get("cwd", cwd)))
    r = git_fn("rev-parse", "--abbrev-ref", "HEAD", cwd=cwd)
    return (r.stdout or "").strip()


class Probes:
    """Injectable filesystem/git/gh/process oracles for tests."""

    def __init__(self, git_fn=None, gh_fn=None, lsof_fn=None, runtime_extra=None,
                 origin_ref=None):
        self.git_fn = git_fn
        self.gh_fn = gh_fn
        self.lsof_fn = lsof_fn
        self.runtime_extra = runtime_extra
        self.origin_ref = origin_ref

    def git(self, *args, cwd=None):
        if self.git_fn:
            return self.git_fn(*args, cwd=cwd)
        return _run(["git", *args], cwd=cwd)


def evaluate_cleanup(path, parent=None, repo_root=None, probes=None):
    """Return a decision dict. ``action`` is remove | wait | escalate | skip."""
    probes = probes or Probes()
    parent = parent or {}
    path = os.path.abspath(path) if path else ""
    decision = {
        "action": "skip",
        "reason": "",
        "path": path,
        "mb": 0.0,
        "branch": "",
        "sha": "",
        "origin_ref": "",
        "detail": "",
        "escalate": False,
    }
    if not path:
        decision["reason"] = "no_path"
        decision["detail"] = "ticket has no worktree path"
        return decision
    if not os.path.isdir(path):
        if (parent.get("status") or "") == "done":
            decision["action"] = "remove"
            decision["reason"] = "already_gone"
            decision["detail"] = "worktree already absent"
            return decision
        decision["reason"] = "missing"
        decision["detail"] = "worktree path does not exist yet"
        decision["action"] = "wait"
        return decision

    decision["mb"] = worktree_size_mb(path)
    decision["branch"] = current_branch(path, git_fn=probes.git)
    unusual = is_nested_or_unusual(path, repo_root=repo_root)
    if unusual == "nested":
        decision.update(action="escalate", escalate=True, reason="nested",
                        detail="not a linked worktree, or structurally unusual")
        return decision

    if is_runtime_symlink_target(path, extra=probes.runtime_extra):
        decision.update(action="escalate", escalate=True, reason="runtime_symlink",
                        detail="path is a runtime/CLI symlink target")
        return decision

    status = parent.get("status") or ""
    if status != "done":
        decision.update(action="wait", reason="not_done",
                        detail="parent ticket is %s" % (status or "unknown"))
        return decision

    contained, sha, ref = head_contained_in_origin(
        path, git_fn=probes.git, origin_ref=probes.origin_ref)
    decision["sha"] = sha
    decision["origin_ref"] = ref
    if not contained:
        decision.update(action="escalate", escalate=True, reason="unmerged",
                        detail="HEAD %s is not contained in %s" % (sha[:12] if sha else "?", ref))
        return decision

    dirty = _porcelain(path, ignored=False, git_fn=probes.git)
    if dirty:
        decision.update(action="escalate", escalate=True, reason="dirty",
                        detail="git status not clean: %s" % "; ".join(dirty[:6]))
        return decision

    ignored = _ignored_paths(path, git_fn=probes.git)
    custody = [p for p in ignored if not _is_disposable(p)]
    if custody:
        decision.update(action="escalate", escalate=True, reason="custody",
                        detail="non-disposable ignored paths: %s" % ", ".join(custody[:8]))
        return decision

    prs = open_prs_for_branch(decision["branch"], path, gh_fn=probes.gh_fn)
    if prs:
        if any(p.get("unknown") for p in prs):
            decision.update(action="escalate", escalate=True, reason="open_pr",
                            detail="cannot prove no open PR (%s)" % (prs[0].get("error") or "unknown"))
            return decision
        decision.update(action="escalate", escalate=True, reason="open_pr",
                        detail="open PR backs %s: %s" % (
                            decision["branch"],
                            ", ".join(str(p.get("url") or p.get("number")) for p in prs[:4])))
        return decision

    here = os.path.realpath(os.getcwd())
    if here == os.path.realpath(path) or here.startswith(os.path.realpath(path) + os.sep):
        decision.update(action="wait", reason="self_cwd",
                        detail="this process is using the worktree; gc after leaving")
        return decision

    lives = live_cwds(path, lsof_fn=probes.lsof_fn)
    if lives:
        if any(x.get("unknown") for x in lives):
            decision.update(action="escalate", escalate=True, reason="live_cwd",
                            detail="cannot prove no live process cwd (%s)" % lives[0].get("cmd", "unknown"))
            return decision
        decision.update(action="escalate", escalate=True, reason="live_cwd",
                        detail="live process cwd: %s" % ", ".join(
                            "%s:%s" % (x.get("cmd"), x.get("pid")) for x in lives[:6]))
        return decision

    decision.update(action="remove", reason="merged")
    decision["detail"] = "merged into %s; safe to remove checkout" % ref
    return decision


def remove_worktree(path, repo_root=None, probes=None):
    """Remove the checkout only. Never delete the branch. Returns (ok, detail)."""
    probes = probes or Probes()
    path = os.path.abspath(path)
    branch = current_branch(path, git_fn=probes.git)
    root = repo_root or path
    # worktree remove must run from a remaining checkout
    r = probes.git("worktree", "remove", path, cwd=root)
    if r.returncode != 0:
        # try from common git dir parent
        r = probes.git("worktree", "remove", path, cwd=os.path.dirname(path) or root)
    if r.returncode != 0:
        return False, (r.stderr or r.stdout or "worktree remove failed").strip(), branch
    probes.git("worktree", "prune", cwd=root)
    return True, "removed", branch


def branch_still_exists(repo_root, branch, probes=None):
    if not branch:
        return False
    probes = probes or Probes()
    r = probes.git("show-ref", "--verify", "--quiet", "refs/heads/%s" % branch,
                   cwd=repo_root)
    return r.returncode == 0


def escalate_body(parent_id, decision):
    return (
        "ESCALATION: worktree cleanup for %s needs judgment.\n"
        "Reason: %s\n"
        "Path: %s\n"
        "Evidence: %s\n"
        "\n"
        "Choices (do not guess):\n"
        "  keep -- this worktree must stay; say why\n"
        "  remove -- accept the risk; Atman may remove the checkout (branch stays)\n"
        "\n"
        "Reply on this ticket with `tickets update` starting with keep: or remove:."
        % (parent_id, decision.get("reason") or "unknown",
           decision.get("path") or "", decision.get("detail") or "")
    )


def ensure_cleanup_node(board, parent, worktree, hooks):
    """Create the automated child if missing. Idempotent."""
    tickets = hooks.load_all(board)
    existing = find_cleanup_child(tickets, parent["id"])
    path = os.path.abspath(worktree) if worktree else (parent.get("worktree") or "")
    if existing:
        auto = dict(existing.get("automated") or {})
        if path and auto.get("worktree") != path:
            auto["worktree"] = path
            existing["automated"] = auto
            hooks.save(board, existing)
        return existing
    if not path:
        return None
    body = (
        "Automated follow-up: when %s is DONE and its HEAD is contained in "
        "origin/<default>, remove the worktree checkout and prune. Never delete "
        "the branch. If any safety rule needs judgment, this node becomes an "
        "agent task for the owner or CoS."
        % parent["id"]
    )
    child = hooks.create(
        board,
        "cleanup worktree for %s" % parent["id"],
        body=body,
        role="ops",
        deps=[parent["id"]],
        priority=3,
        epic=parent.get("epic") or "",
        sprint=parent.get("sprint") or "",
    )
    child["kind"] = KIND_AUTOMATED
    child["automated"] = {
        "action": ACTION,
        "target": parent["id"],
        "worktree": path,
        "escalated": False,
    }
    child["notes"] = list(child.get("notes") or [])
    child["notes"].append({
        "by": hooks.who(),
        "at": hooks.now(),
        "text": "automated node: cleanup_worktree for %s at %s" % (parent["id"], path),
    })
    hooks.save(board, child)
    parent = hooks.load(board, parent["id"])
    if (parent.get("worktree") or "") != path:
        parent["worktree"] = path
        hooks.save(board, parent)
    return child


def attach_worktree(board, ticket, worktree, hooks):
    """Record the path and ensure the cleanup child exists."""
    path = os.path.abspath(worktree) if worktree else ""
    if not path:
        return None
    if ticket.get("kind") == KIND_AUTOMATED:
        return None
    ticket["worktree"] = path
    hooks.save(board, ticket)
    return ensure_cleanup_node(board, ticket, path, hooks)


def _digest_path(board):
    return os.path.join(board, "gc-digest.jsonl")


def append_digest(board, row):
    path = _digest_path(board)
    os.makedirs(board, exist_ok=True)
    with open(path, "a") as f:
        f.write(json.dumps(row, sort_keys=True) + "\n")


def _close_automated(hooks, board, child, text, extra=None):
    child["status"] = "done"
    child["done_at"] = hooks.now()
    if not child.get("claimed_at"):
        child["claimed_at"] = child.get("updated") or child.get("created") or hooks.now()
    child["owner"] = child.get("owner") or "atman"
    child["notes"] = list(child.get("notes") or [])
    child["notes"].append({"by": hooks.who(), "at": hooks.now(), "text": text})
    if extra:
        child["automated"] = dict(child.get("automated") or {}, **extra)
    hooks.save(board, child)
    return child


def escalate_node(board, child, parent, decision, hooks):
    """Convert automated -> agent. No guess. Wait for keep/remove."""
    parent_id = (parent or {}).get("id") or cleanup_target(child) or "?"
    owner = ((parent or {}).get("owner") or "").strip()
    cos = (hooks.cos(board) or "").strip() if hasattr(hooks, "cos") else ""
    routed = owner or cos or ""
    child["kind"] = KIND_AGENT
    auto = dict(child.get("automated") or {})
    auto["escalated"] = True
    auto["escalate_reason"] = decision.get("reason") or ""
    auto["worktree"] = decision.get("path") or auto.get("worktree") or ""
    child["automated"] = auto
    child["status"] = "open"
    child["owner"] = ""
    if routed:
        child["reserved_for"] = routed
    child["role"] = child.get("role") or (parent or {}).get("role") or "ops"
    child["body"] = escalate_body(parent_id, decision)
    child["notes"] = list(child.get("notes") or [])
    child["notes"].append({
        "by": hooks.who(),
        "at": hooks.now(),
        "text": "escalated: %s -- %s" % (decision.get("reason"), decision.get("detail")),
    })
    hooks.save(board, child)
    row = {
        "at": hooks.now(),
        "action": "escalate",
        "reason": decision.get("reason"),
        "path": decision.get("path"),
        "ticket": child["id"],
        "target": parent_id,
        "mb": decision.get("mb") or 0,
        "detail": decision.get("detail"),
    }
    append_digest(board, row)
    return child, row


def run_cleanup_node(board, child, hooks, probes=None, repo_root=None, apply=False):
    """Execute or escalate one automated cleanup node. Never posts a model task."""
    probes = probes or Probes()
    auto = child.get("automated") or {}
    parent_id = auto.get("target") or ""
    parent = hooks.load(board, parent_id) if parent_id else {}
    path = auto.get("worktree") or (parent or {}).get("worktree") or ""
    repo_root = repo_root or (os.path.dirname(board) if board else None)
    decision = evaluate_cleanup(path, parent=parent, repo_root=repo_root, probes=probes)
    if not apply:
        return {"status": "would remove" if decision["action"] == "remove" else "keep",
                "decision": decision, "ticket": child["id"]}
    if decision["action"] not in ("remove", "escalate", "wait"):
        return {"status": "keep", "decision": decision, "ticket": child["id"]}
    if decision["action"] == "wait":
        return {"status": "wait", "decision": decision, "ticket": child["id"]}
    if decision["action"] == "escalate":
        child, row = escalate_node(board, child, parent, decision, hooks)
        return {"status": "escalate", "decision": decision, "ticket": child["id"],
                "digest": row}
    # remove (including already_gone)
    mb = decision.get("mb") or 0
    branch = decision.get("branch") or ""
    if decision.get("reason") != "already_gone" and os.path.isdir(path):
        ok, detail, branch = remove_worktree(path, repo_root=repo_root, probes=probes)
        if not ok:
            decision = dict(decision, action="escalate", escalate=True,
                            reason="nested", detail="worktree remove failed: %s" % detail)
            child, row = escalate_node(board, child, parent, decision, hooks)
            return {"status": "escalate", "decision": decision, "ticket": child["id"],
                    "digest": row}
        if branch and repo_root and not branch_still_exists(repo_root, branch, probes=probes):
            # we must never have deleted it; surface if something else did
            decision = dict(decision, detail="branch %s missing after remove (not deleted by gc)" % branch)
    text = (
        "removed worktree %s (%s MB freed); branch %s intact; reason=%s"
        % (path, mb, branch or "(none)", decision.get("reason"))
    )
    if decision.get("reason") == "already_gone":
        text = "worktree already absent at %s; branch left intact" % (path or "(none)")
    _close_automated(hooks, board, child, text, extra={
        "freed_mb": mb,
        "branch": branch,
        "result": "removed",
    })
    row = {
        "at": hooks.now(),
        "action": "remove",
        "reason": decision.get("reason"),
        "path": path,
        "ticket": child["id"],
        "target": parent_id,
        "mb": mb,
        "branch": branch,
        "detail": text,
    }
    append_digest(board, row)
    return {"status": "removed", "decision": decision, "ticket": child["id"],
            "digest": row, "branch": branch, "mb": mb}


def list_linked_worktrees(repo_root, probes=None):
    probes = probes or Probes()
    r = probes.git("worktree", "list", "--porcelain", cwd=repo_root)
    rows, cur = [], {}
    for ln in (r.stdout or "").splitlines():
        if not ln.strip():
            if cur.get("path"):
                rows.append(cur)
            cur = {}
            continue
        if ln.startswith("worktree "):
            cur["path"] = ln[9:]
        elif ln.startswith("branch refs/heads/"):
            cur["branch"] = ln[len("branch refs/heads/"):]
        elif ln.startswith("HEAD "):
            cur["sha"] = ln[5:]
        elif ln == "bare":
            cur["bare"] = True
    if cur.get("path"):
        rows.append(cur)
    return rows


def sweep(board, hooks, probes=None, repo_root=None, attach_missing=True, apply=False):
    """Preview all linked worktrees; apply only with explicit authorization."""
    probes = probes or Probes()
    repo_root = repo_root or os.path.dirname(board)
    tickets = hooks.load_all(board)
    if not apply:
        results = []
        for tree in list_linked_worktrees(repo_root, probes=probes):
            path = tree.get("path") or ""
            parents = [t for t in tickets if not t.get("automated") and (
                os.path.realpath(t.get("worktree") or t.get("artifact_dir") or "/")
                == os.path.realpath(path) or
                (tree.get("branch") and t.get("branch") == tree["branch"]))]
            parent = parents[0] if parents else {}
            decision = evaluate_cleanup(path, parent=parent, repo_root=repo_root, probes=probes)
            child = find_cleanup_child(tickets, parent.get("id")) if parent else None
            if child and (not is_automated(child) or child.get("status") != "open"):
                decision.update(action="wait", reason="cleanup_held",
                                detail="cleanup ticket requires agent review or is already closed")
            results.append({"status": "would remove" if decision["action"] == "remove" else "keep",
                            "decision": decision, "ticket": parent.get("id", "")})
        return results
    by_id = dict((t["id"], t) for t in tickets)
    results = []

    # 1. Ready automated cleanup nodes (deps all done).
    done = set(t["id"] for t in tickets if t.get("status") == "done")
    for t in tickets:
        if not is_automated(t) or cleanup_target(t) == "":
            continue
        if t.get("status") != "open":
            continue
        deps = t.get("deps") or []
        if deps and not all(d in done for d in deps):
            continue
        results.append(run_cleanup_node(board, t, hooks, probes=probes,
                                        repo_root=repo_root, apply=True))

    # 2. Done tickets that still record a worktree but have no child.
    tickets = hooks.load_all(board)
    if attach_missing:
        for t in tickets:
            if t.get("kind") == KIND_AUTOMATED:
                continue
            path = (t.get("worktree") or t.get("artifact_dir") or "").strip()
            if not path:
                continue
            if find_cleanup_child(tickets, t["id"]):
                continue
            child = ensure_cleanup_node(board, t, path, hooks)
            if child and t.get("status") == "done":
                results.append(run_cleanup_node(board, child, hooks, probes=probes,
                                                repo_root=repo_root, apply=True))

    # 3. Linked worktrees whose branch matches a done ticket.
    tickets = hooks.load_all(board)
    by_branch = {}
    for t in tickets:
        if t.get("kind") == KIND_AUTOMATED:
            continue
        b = (t.get("branch") or "").strip()
        if b and t.get("status") == "done":
            by_branch.setdefault(b, []).append(t)
    try:
        trees = list_linked_worktrees(repo_root, probes=probes)
    except Exception:
        trees = []
    for wt in trees:
        path = wt.get("path") or ""
        branch = wt.get("branch") or ""
        if not path or not branch:
            continue
        if os.path.realpath(path) == os.path.realpath(repo_root):
            continue
        parents = by_branch.get(branch) or []
        if not parents:
            continue
        parent = parents[0]
        tickets = hooks.load_all(board)
        child = find_cleanup_child(tickets, parent["id"])
        if child is None:
            child = ensure_cleanup_node(board, parent, path, hooks)
        if child and child.get("status") == "open" and is_automated(child):
            # avoid double-running the same child in this sweep
            if any(r.get("ticket") == child["id"] for r in results):
                continue
            results.append(run_cleanup_node(board, child, hooks, probes=probes,
                                            repo_root=repo_root, apply=True))

    return results


def format_digest(results):
    lines = []
    for r in results:
        d = r.get("decision") or {}
        lines.append("%s %s %s  %s  %s MB  %s" % (
            r.get("status") or "?",
            r.get("ticket") or "-",
            d.get("reason") or "",
            d.get("path") or "",
            r.get("mb") if r.get("mb") is not None else d.get("mb") or 0,
            d.get("detail") or "",
        ))
    return "\n".join(lines)


class CliHooks:
    """Wire worktree_gc to tickets.py without importing it at module load."""

    def __init__(self, api):
        self.api = api

    def load_all(self, board):
        return self.api.load_all(board)

    def load(self, board, tid):
        return self.api.load(board, tid) if tid else {}

    def save(self, board, t):
        return self.api.save(board, t)

    def create(self, board, title, body="", role="", deps=None, priority=3,
               epic="", sprint="", needs=None):
        return self.api.create(board, title, body, role, deps, priority, epic,
                               sprint, needs)

    def now(self):
        return self.api.now()

    def who(self):
        return self.api.whoami()

    def cos(self, board):
        fn = getattr(self.api, "current_master", None)
        rec = fn(board) if fn else None
        if isinstance(rec, dict):
            return (rec.get("cos") or rec.get("owner") or "").strip()
        return ""


# used by a couple of unit tests / grep-friendly export
_SHA_RE = re.compile(r"^[0-9a-f]{7,40}$")
