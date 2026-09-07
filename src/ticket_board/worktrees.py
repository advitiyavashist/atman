"""Worktree isolation: does this checkout already belong to somebody? (T-192)

The 19:15Z acceptance note on T-192 established the gap by execution, not by
argument. The Claude adapter's enrollment guard protects the board's checkouts
by git repository IDENTITY plus a path-containment floor, and opus-backend-2
probed it with four real cases: origin_repo REFUSED, origin_wt REFUSED,
clone_repo ALLOWED, clone_wt ALLOWED. So an agent working in a worktree of a
CLONE of a board checkout, parked outside the protected roots, passes both
checks.

That cannot be fixed in the adapter and the planner's reasoning is right: a
clone is a genuinely distinct repository on disk, so no git-identity test can
separate "a clone someone is working in" from "an ordinary project the user
legitimately wants to enrol" -- and the latter is the whole feature.
Origin-URL matching would refuse every fork.

The answer is a REGISTRY, which is a signal only the board has: it already
records `worktree` per agent. Enrolment consults that registry and refuses a
directory that is, contains, or sits inside a checkout another agent already
occupies. Both directions matter -- an ordinary project, and a clone nobody
is working in, must still enrol.

CONTAINMENT IS LEXICAL, DELIBERATELY, AND THAT IS A REAL LIMIT. These paths
are operator-supplied strings for directories the server does not own and
which frequently do not exist yet (the isolated worktree is about to be
created). Resolving them would mean touching the filesystem on behalf of a
caller, and would still answer the wrong question for a path that is not
there. So the comparison normalises separators and `.`/`..` segments and then
compares path SEGMENTS -- never raw string prefixes, which would let
`/w/agent-2` look like it sits inside `/w/agent`. The limit this leaves is
symlinks: two different paths can name one directory and this will not see
it. Stated here rather than discovered later; closing it needs a resolver
that runs where the checkouts actually live, which is the adapter, not the
server.
"""

import posixpath


def normalize(path):
    """A comparable form of a worktree path, or "" if there is nothing to compare.

    Backslashes fold to forward slashes so a Windows-style path and a POSIX
    one for the same location do not silently miss each other. Trailing
    separators are dropped so `/w/a/` and `/w/a` are one directory.
    """
    if not isinstance(path, str):
        return ""
    text = path.strip().replace("\\", "/")
    if not text:
        return ""
    collapsed = posixpath.normpath(text)
    if collapsed in (".", "/"):
        return collapsed
    return collapsed.rstrip("/")


def _segments(path):
    return [part for part in normalize(path).split("/") if part not in ("", ".")]


def overlaps(a, b):
    """True when two worktree paths cannot be occupied independently.

    That is the real question -- not "are these equal". It is true when the
    paths are the same directory, and when either contains the other, because
    an agent working in a parent checkout is working in every child of it.
    Compared segment by segment so a shared name PREFIX (`/w/agent` vs
    `/w/agent-2`) is correctly two different directories.
    """
    left, right = _segments(a), _segments(b)
    if not left or not right:
        return False
    shorter, longer = (left, right) if len(left) <= len(right) else (right, left)
    return longer[:len(shorter)] == shorter


def contains(parent, child):
    """Is `child` the same directory as `parent`, or inside it?

    DIRECTIONAL, and the direction is the whole point. `overlaps` is
    symmetric, which is right for "is this checkout free?" but wrong for "may
    this runner run here?": a runner allowlisted for `/w/agent-1` that asks
    for `/` overlaps its approved path and must still be refused, because it
    asked for MORE. Containment answers the second question; equality is the
    boundary case and is allowed.
    """
    outer, inner = _segments(parent), _segments(child)
    if not outer or not inner:
        return False
    return inner[:len(outer)] == outer
