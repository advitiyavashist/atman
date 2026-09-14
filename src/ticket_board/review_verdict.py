"""T-944: structured review verdicts bound to an exact artifact SHA.

`atm accept` / `atm reject` write `review_events` on the ticket. Work view and
any success-trigger reader use only those events as ACCEPT/REJECT. A note or
message whose text starts with accept/approved is an unstructured note, never
a verdict. `atm done` is a separate close and is unchanged.
"""
from __future__ import annotations

import re
from datetime import datetime, timezone

FULL_SHA_RE = re.compile(r"^[0-9a-f]{40}$")
SHA_RE = re.compile(r"^[0-9a-f]{7,40}$")
PIN_SHA_RE = re.compile(r"^[0-9a-f]{7,40}$")
KINDS = ("accept", "reject")
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
    """The review-head SHA recorded on the ticket (`branch@sha` or bare)."""
    commit = (t.get("commit") or "").strip()
    if "@" in commit:
        commit = commit.rsplit("@", 1)[1]
    commit = commit.lower().strip()
    return commit if PIN_SHA_RE.fullmatch(commit) else ""


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
    if st != "review":
        return "%s is %s; only IN REVIEW work can be %sed" % (
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
