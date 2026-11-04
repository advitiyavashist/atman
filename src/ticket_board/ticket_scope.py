"""T-020: a ticket's write_scope -- the path prefixes it expects to touch.

Claiming a ticket whose scope overlaps another in-progress ticket's scope
prints a warning naming both tickets, owners and paths; `atm review` warns
about committed paths outside the declared scope. Warnings only: scope is a
coordination hint, never a gate. (The other T-020 half, compare-and-set
ticket revisions, lives in ticket_coordination beside the generation CAS.)

Pure functions over ticket dicts and path strings; no board globals, so the
root `tickets.py` and the packaged CLI share one implementation.
"""
from __future__ import annotations

import posixpath

# --- write_scope -----------------------------------------------------------


class ScopeError(ValueError):
    """A path prefix that cannot name a place inside the repo."""


def normalize(prefix):
    """Repo-relative POSIX prefix. '' / '.' mean the whole repo."""
    raw = str(prefix or "").strip().replace("\\", "/")
    if raw.startswith("/") or (len(raw) > 1 and raw[1] == ":"):
        raise ScopeError("write_scope %r must be relative to the repo root" % prefix)
    if any(part == ".." for part in raw.split("/")):
        raise ScopeError("write_scope %r may not contain '..'" % prefix)
    dir_only = raw.endswith("/")
    norm = posixpath.normpath(raw) if raw else "."
    if norm == ".":
        return ""
    return norm + ("/" if dir_only else "")


def normalize_all(prefixes):
    out = []
    for p in prefixes or []:
        n = normalize(p)
        if n not in out:
            out.append(n)
    return out


def scope_of(ticket):
    raw = (ticket or {}).get("write_scope") or []
    if isinstance(raw, str):
        raw = [raw]
    try:
        return normalize_all(raw)
    except ScopeError:
        return [str(p) for p in raw]


def covers(prefix, path):
    """True when `path` lies at or under `prefix` (component boundary)."""
    prefix, path = prefix.rstrip("/"), path.rstrip("/")
    if not prefix:
        return True
    return path == prefix or path.startswith(prefix + "/")


def overlap(a, b):
    """Two prefixes overlap when either covers the other."""
    return covers(a, b) or covers(b, a)


def _label(prefix):
    return prefix or "(whole repo)"


ACTIVE = ("claimed",)


def conflicts(ticket, tickets):
    """[(other_ticket, [(mine, theirs), ...])] for other in-progress tickets."""
    mine = scope_of(ticket)
    if not mine:
        return []
    out = []
    for other in tickets or []:
        if other.get("id") == ticket.get("id") or other.get("status") not in ACTIVE:
            continue
        pairs = [(m, o) for m in mine for o in scope_of(other) if overlap(m, o)]
        if pairs:
            out.append((other, pairs))
    return out


def conflict_lines(ticket, tickets):
    """Human warning lines; [] when nothing overlaps."""
    lines = []
    for other, pairs in conflicts(ticket, tickets):
        shown = ", ".join("%s ~ %s" % (_label(m), _label(o)) for m, o in pairs[:4])
        if len(pairs) > 4:
            shown += ", +%d more" % (len(pairs) - 4)
        lines.append("warning: write_scope of %s overlaps %s (in progress, owner=%s): %s"
                     % (ticket.get("id"), other.get("id"), other.get("owner") or "nobody", shown))
    return lines


def all_overlaps(tickets):
    """Every overlapping pair among in-progress tickets, each pair once."""
    active = [t for t in tickets or [] if t.get("status") in ACTIVE and scope_of(t)]
    out = []
    for i, t in enumerate(active):
        for other, pairs in conflicts(t, active[i + 1:]):
            out.append((t, other, pairs))
    return out


def outside(scope, paths):
    """Paths no declared prefix covers. An empty scope declares nothing."""
    if not scope:
        return []
    return [p for p in paths if p and not any(covers(s, p) for s in scope)]
