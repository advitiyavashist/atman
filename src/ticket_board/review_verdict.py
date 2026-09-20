"""T-944: structured review verdicts bound to an exact artifact SHA.

`atm accept` / `atm reject` write `review_events` on the ticket. Work view and
any success-trigger reader use only those events as ACCEPT/REJECT. A note or
message whose text starts with accept/approved is an unstructured note, never
a verdict. `atm done` is a separate close. T-1031: dependents release only after
ACCEPT (or a recorded override); accept may bind a done ticket that still
has a submitted review head so "accept it or reopen" is actionable.

`atm review --pr N` must pin a *verified* head: the SHA is on origin, the PR
head equals or contains that SHA in the same repository, and the worktree is
clean. Accept/reject then bind to `review_head` (full 40), not a prose note.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
from datetime import datetime, timezone

FULL_SHA_RE = re.compile(r"^[0-9a-f]{40}$")
SHA_RE = re.compile(r"^[0-9a-f]{7,40}$")
PIN_SHA_RE = re.compile(r"^[0-9a-f]{7,40}$")
KINDS = ("accept", "reject")
_GITHUB_ORIGIN = re.compile(
    r"(?:https://github\.com/|git@github\.com:|ssh://git@github\.com/)"
    r"(?P<owner>[^/]+)/(?P<repo>[^/.]+?)(?:\.git)?$",
    re.I,
)
_PR_URL_RE = re.compile(
    r"https://github\.com/(?P<owner>[^/]+)/(?P<repo>[^/]+)/pull/(?P<num>\d+)",
    re.I,
)
STATUS_LABEL = {
    "open": "TO DO",
    "claimed": "IN PROGRESS",
    "review": "IN REVIEW",
    "blocked": "BLOCKED",
    "done": "DONE",
}


def now():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def submitted_sha(t):
    """The review-head SHA recorded on the ticket.

    Prefer the verified full SHA from `review --pr` (`review_head`). Fall back
    to `branch@sha` / bare `commit` so older pins still bind.
    """
    head = normalize_sha(t.get("review_head"))
    if PIN_SHA_RE.fullmatch(head):
        return head
    commit = (t.get("commit") or "").strip()
    if "@" in commit:
        commit = commit.rsplit("@", 1)[1]
    commit = commit.lower().strip()
    return commit if PIN_SHA_RE.fullmatch(commit) else ""


def displayed_review_head(t):
    """Full 40-char review head for CLI output, or '' if it was never recorded."""
    head = normalize_sha(t.get("review_head"))
    if FULL_SHA_RE.fullmatch(head):
        return head
    head = submitted_sha(t)
    return head if FULL_SHA_RE.fullmatch(head) else ""


def review_queue_pin(t):
    """`atm master` REVIEW QUEUE pin: branch@full40 so accept --sha is copyable."""
    head = displayed_review_head(t)
    commit = (t.get("commit") or "").strip()
    if head:
        if "@" in commit:
            branch = commit.rsplit("@", 1)[0].strip()
            if branch:
                return "%s@%s" % (branch, head)
        branch = (t.get("branch") or "").strip()
        if branch:
            return "%s@%s" % (branch, head)
        return head
    return commit or "?"


def sha_match(a, b):
    a, b = (a or "").lower(), (b or "").lower()
    if not a or not b:
        return False
    n = min(len(a), len(b), 40)
    return n >= 7 and a[:n] == b[:n]


def normalize_sha(raw):
    return (raw or "").strip().lower()


def iter_structured(t):
    for ev in t.get("review_events") or []:
        if not isinstance(ev, dict):
            continue
        kind = (ev.get("kind") or "").strip().lower()
        if kind not in KINDS:
            continue
        yield ev


def refuse(t, reviewer, sha, kind, require_full=False, notes="", reason=""):
    """Return a refusal string, or None if the verdict may be recorded."""
    tid = t.get("id") or "?"
    st = t.get("status")
    if st not in ("review", "done"):
        return "%s is %s; only IN REVIEW (or done-unverified) work can be %sed" % (
            tid, STATUS_LABEL.get(st, st or "?"), kind)
    author = (t.get("owner") or "").strip()
    reviewer = (reviewer or "").strip()
    if not reviewer:
        return "reviewer identity is required (set TICKET_AGENT)"
    if author and reviewer == author:
        return "%s: the ticket author (%s) cannot %s their own work" % (
            tid, author, kind)
    sha = normalize_sha(sha)
    if require_full and not FULL_SHA_RE.fullmatch(sha):
        head = displayed_review_head(t)
        if head:
            return ("%s: --sha must be the full 40-character submitted review head (%s)"
                    % (tid, head))
        return "%s: --sha must be the full 40-character submitted review head" % tid
    if not SHA_RE.fullmatch(sha):
        return "%s: --sha must be a git object name (7-40 hex chars)" % tid
    head = submitted_sha(t)
    if not head:
        return "%s: no submitted review head to bind --sha to" % tid
    if not sha_match(sha, head):
        return "%s: sha %s is not the submitted review head (%s)" % (tid, sha, head)
    if kind == "accept" and not (notes or "").strip():
        return 'accept needs --notes "why this artifact is accepted"'
    if kind == "reject" and not (reason or "").strip():
        return "reject needs --reason"
    return None


def make_event(kind, reviewer, sha, notes="", reason="", at=None):
    ev = {
        "kind": kind,
        "by": reviewer,
        "at": at or now(),
        "sha": normalize_sha(sha),
    }
    if kind == "accept":
        ev["notes"] = (notes or "").strip()
    else:
        ev["reason"] = (reason or "").strip()
    return ev


def append_event(t, event):
    events = [e for e in (t.get("review_events") or []) if isinstance(e, dict)]
    events.append(event)
    t["review_events"] = events
    return t


def apply(t, reviewer, sha, kind, notes="", reason="", at=None, require_full=False):
    """Record a structured verdict. Returns (event, None) or (None, error)."""
    err = refuse(t, reviewer, sha, kind, require_full=require_full,
                 notes=notes, reason=reason)
    if err:
        return None, err
    ev = make_event(kind, reviewer, sha, notes=notes, reason=reason, at=at)
    append_event(t, ev)
    return ev, None


def format_detail(t):
    lines = []
    for ev in t.get("review_events") or []:
        if not isinstance(ev, dict):
            continue
        extra = ev.get("notes") or ev.get("reason") or ""
        lines.append("  - [%s] %s %s%s" % (
            ev.get("by") or "?",
            ev.get("kind") or "?",
            ev.get("sha") or "",
            (" -- " + extra) if extra else "",
        ))
    return lines


def pr_num(pr):
    s = str(pr or "").strip()
    if not s:
        return ""
    m = _PR_URL_RE.search(s)
    if m:
        return m.group("num")
    if s.startswith("http"):
        s = s.rstrip("/").split("/")[-1]
    return s


def normalize_origin(url):
    text = (url or "").strip().rstrip("/")
    if not text:
        return ""
    m = _GITHUB_ORIGIN.match(text)
    if m:
        return "%s/%s" % (m.group("owner").lower(), m.group("repo").lower())
    m = _PR_URL_RE.search(text)
    if m:
        return "%s/%s" % (m.group("owner").lower(), m.group("repo").lower())
    if "/" in text and "://" not in text and not text.startswith("/") and not text.startswith("."):
        parts = text.split("/")
        if len(parts) == 2:
            return text.lower()
    return os.path.realpath(os.path.expanduser(text)).lower() if os.path.isabs(text) or text.startswith(".") else text.lower()


def origins_match(a, b):
    na, nb = normalize_origin(a), normalize_origin(b)
    return bool(na) and na == nb


def injected_pr_view(pr):
    raw = os.environ.get("TICKETS_PR_VIEW") or ""
    if not raw:
        return None
    try:
        table = json.loads(raw)
    except ValueError:
        return None
    if not isinstance(table, dict):
        return None
    key = pr_num(pr)
    for candidate in (pr, key, str(pr), int(key) if key.isdigit() else None):
        if candidate is None:
            continue
        row = table.get(candidate)
        if row is None:
            row = table.get(str(candidate))
        if isinstance(row, dict):
            return row
    return None


def _gh_pr_view(num, repo, cwd=None):
    cmd = ["gh", "pr", "view", str(num), "--json",
           "headRefOid,headRefName,url,headRepository"]
    if repo:
        cmd.extend(["--repo", repo])
    try:
        r = subprocess.run(
            cmd, capture_output=True, text=True, timeout=20,
            cwd=cwd or None)
    except (OSError, subprocess.TimeoutExpired) as exc:
        return None, "gh pr view failed (%s)" % exc
    if r.returncode != 0:
        err = (r.stderr or r.stdout or "gh pr view exited %s" % r.returncode).strip()
        return None, err.splitlines()[0] if err else "gh pr view failed"
    try:
        data = json.loads(r.stdout or "{}")
    except ValueError:
        return None, "gh pr view returned non-JSON"
    if not isinstance(data, dict):
        return None, "gh pr view returned non-object JSON"
    return data, ""


def load_pr_view(pr, origin_url, cwd=None):
    """Return (view_dict, error). Fail closed when the PR cannot be loaded."""
    injected = injected_pr_view(pr)
    if injected is not None:
        return injected, ""
    num = pr_num(pr)
    if not num:
        return None, "review --pr: missing PR number"
    repo = normalize_origin(origin_url)
    github_repo = repo if repo.count("/") == 1 and not repo.startswith("/") else ""
    if not github_repo:
        return None, (
            "review --pr: could not load PR %s in %s: not a GitHub origin "
            "(use a GitHub remote or TICKETS_PR_VIEW)" % (num, origin_url or "?"))
    data, err = _gh_pr_view(num, github_repo, cwd=cwd)
    if err:
        return None, "review --pr: could not load PR %s in %s: %s" % (
            num, github_repo, err)
    return data, ""


def pr_view_fields(view):
    sha = normalize_sha(
        view.get("headRefOid") or view.get("head_sha") or view.get("sha"))
    name = (view.get("headRefName") or view.get("head") or view.get("branch") or "").strip()
    repo = ""
    hr = view.get("headRepository") or view.get("repository") or {}
    if isinstance(hr, dict):
        repo = (hr.get("nameWithOwner") or hr.get("name") or "").strip()
    if not repo:
        repo = (view.get("repo") or view.get("url") or "").strip()
    return sha, name, repo


_GIT_LOCATION_KEYS = (
    "GIT_DIR", "GIT_WORK_TREE", "GIT_COMMON_DIR", "GIT_INDEX_FILE",
    "GIT_OBJECT_DIRECTORY", "GIT_ALTERNATE_OBJECT_DIRECTORIES",
    "GIT_PREFIX", "GIT_CEILING_DIRECTORIES",
)


def _clean_git_env():
    env = os.environ.copy()
    for key in _GIT_LOCATION_KEYS:
        env.pop(key, None)
    env["GIT_TERMINAL_PROMPT"] = "0"
    return env


def _run_git(cwd, *args, timeout=8):
    """Ask git about `cwd` only; inherited GIT_DIR must not redirect (T-243)."""
    try:
        r = subprocess.run(
            ["git", *args], cwd=cwd or None, capture_output=True, text=True,
            timeout=timeout, env=_clean_git_env())
    except (OSError, subprocess.TimeoutExpired):
        return None
    return r.stdout.strip() if r.returncode == 0 else None


def _remote_sha_for(cwd, sha, branch):
    """Look up origin's tip for `branch` and whether `sha` exists on origin."""
    sha = normalize_sha(sha)
    listed = _run_git(cwd, "ls-remote", "--exit-code", "origin", sha)
    on_origin = bool(listed and sha[:7] in listed.lower())
    tip = ""
    if branch:
        refs = _run_git(cwd, "ls-remote", "origin", "refs/heads/%s" % branch)
        if refs:
            tip = normalize_sha(refs.split()[0])
    return on_origin, tip


def origin_contains_sha(git_fn, sha, branch, cwd=None):
    """Return (remote_sha, error). remote_sha is origin/<branch> when it contains sha."""
    del git_fn  # cwd + clean env; do not inherit the caller's GIT_DIR
    sha = normalize_sha(sha)
    if not sha:
        return "", "review --pr: no local HEAD SHA to verify"
    here = cwd or os.getcwd()
    origin = _run_git(here, "config", "--get", "remote.origin.url")
    if not origin:
        return "", "review --pr: no origin remote; cannot verify SHA %s is pushed" % sha
    on_origin, remote_sha = _remote_sha_for(here, sha, branch)
    if remote_sha and sha_match(sha, remote_sha):
        return remote_sha, ""
    if on_origin:
        return remote_sha or sha, ""
    if remote_sha:
        if _run_git(here, "merge-base", "--is-ancestor", sha, remote_sha) is not None:
            return remote_sha, ""
        return "", (
            "review --pr: SHA %s is not on origin/%s (origin/%s is %s). "
            "Push %s first." % (sha, branch, branch, remote_sha, sha))
    return "", (
        "review --pr: SHA %s is not on origin/%s (origin/%s does not exist). "
        "Push %s first." % (sha, branch, branch, branch))


def pr_contains_sha(git_fn, sha, pr_sha, cwd=None):
    """True when PR head equals sha, or local git can prove PR head contains sha."""
    del git_fn
    sha, pr_sha = normalize_sha(sha), normalize_sha(pr_sha)
    if not sha or not pr_sha:
        return False
    if sha_match(sha, pr_sha):
        return True
    here = cwd or os.getcwd()
    if _run_git(here, "cat-file", "-e", pr_sha + "^{commit}") is None:
        _run_git(here, "fetch", "origin", pr_sha)
    return _run_git(here, "merge-base", "--is-ancestor", sha, pr_sha) is not None


def verify_submit(git_fn, *, sha_full, branch, origin_url, pr, dirty=0, cwd=None):
    """Refuse review --pr unless SHA/origin/PR/repo all match.

    Dirty-tree refusal stays in `cmd_review` (honors --force). This gate is
    the T-811 bind: pushed SHA, PR head equals/contains it, same repository.
    """
    del dirty
    sha_full = normalize_sha(sha_full)
    branch = (branch or "").strip()
    if not sha_full or not PIN_SHA_RE.fullmatch(sha_full):
        return "review --pr: local HEAD is not a git SHA"
    if not branch or branch in ("main", "master", "HEAD"):
        return "review --pr: submit from your own worktree branch, not %r" % (branch or "?")
    if not (pr or "").strip():
        return "review --pr: missing PR number"
    here = cwd or os.getcwd()
    _remote_sha, err = origin_contains_sha(git_fn, sha_full, branch, cwd=here)
    if err:
        return err
    view, err = load_pr_view(pr, origin_url, cwd=here)
    if err:
        return err
    pr_sha, pr_branch, pr_repo = pr_view_fields(view)
    num = pr_num(pr)
    if not pr_sha:
        return "review --pr: PR %s has no head SHA" % num
    if not origins_match(origin_url, pr_repo):
        return (
            "review --pr: PR #%s is %s@%s but this artifact is %s@%s -- wrong repository"
            % (num, normalize_origin(pr_repo) or (pr_repo or "?"), pr_sha,
               normalize_origin(origin_url) or (origin_url or "?"), sha_full))
    if not pr_contains_sha(git_fn, sha_full, pr_sha, cwd=here):
        return (
            "review --pr: PR #%s head %s@%s does not equal or contain submitted SHA %s"
            % (num, pr_branch or "?", pr_sha, sha_full))
    return None


def record_verified_head(t, sha_full, pr=""):
    """Stamp the verified full SHA that accept/reject must bind to."""
    sha_full = normalize_sha(sha_full)
    if PIN_SHA_RE.fullmatch(sha_full):
        t["review_head"] = sha_full
    if pr:
        t["pr"] = pr
    return t
