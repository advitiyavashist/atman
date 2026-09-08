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

ABSOLUTENESS IS PART OF THE COMPARISON (T-485, cos-opus A3). The first cut of
this module segmented `/w/a` and `w/a` identically, because `_segments` drops
empty parts and a leading `/` is an empty first part. That made a RELATIVE
path pass the containment floor against an ABSOLUTE approved one -- and a
relative worktree is not a near-miss of the approved directory, it is a
different directory entirely: `launcher.ClaudeLauncher.start` does
`Path(spec.worktree)` and passes it as the child's `cwd`, so a relative value
resolves against whatever directory the supervisor process happens to be in.
That is cheaper than the symlink hole above -- it needs no filesystem access
at all and the runner supplies the string. So `contains` now refuses when the
two paths disagree about being absolute, and refuses either side containing a
`..` segment that `posixpath.normpath` could not fold away (which only
survives at the front of a relative path). Both refusals are NARROWINGS: they
turn a silent pass into a 403 that names the reason.

`overlaps` deliberately does NOT get the same strictness. It answers "is this
checkout free?", where the loose answer is the REFUSING answer: if `/w/a` and
`w/a` might name one directory, the registry should decline to hand the second
one out. Tightening it there would turn a refusal into a grant, which is the
wrong direction for that question.
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


def is_absolute(path):
    """Does this path start from the filesystem root?

    Asked of the NORMALISED form so `//w/a`, `/w/a/` and `\\w\\a` all answer the
    same. This is the distinction `_segments` cannot make on its own: a leading
    separator is an empty segment and empty segments are dropped, which is
    exactly why `/w/a` and `w/a` used to compare equal.
    """
    return normalize(path).startswith("/")


def unusable_reason(path):
    """Why a worktree path cannot stand on its own, or None when it can.

    A path is usable here when it names something, carries no unresolved `..`,
    and is absolute. The last is the load-bearing one: a relative path is not
    an under-specified directory, it is a directory chosen by the supervisor
    process's working directory at launch time, which is not a thing any
    operator approved. Returned as a phrase rather than a bool so the 403 can
    say which of the three it was -- a refusal an operator cannot diagnose
    gets worked around instead of fixed.
    """
    segments = _segments(path)
    if not segments:
        return "names no directory"
    if ".." in segments:
        return ("contains a '..' segment this server will not resolve, because"
                " resolving it would mean answering for a directory the server"
                " does not own")
    if not is_absolute(path):
        return ("is a relative path; the runtime resolves it against the"
                " supervisor process's own working directory, so it names a"
                " directory nobody approved")
    return None


def refusal_reason(parent, child):
    """Why `child` may not be run in given approval for `parent`, or None.

    The companion to `contains` for callers that have to explain themselves.
    Order matters: the specific complaints about `child` come first, so a
    relative path is reported AS a relative path rather than as the much less
    useful "is not inside it".
    """
    reason = unusable_reason(child)
    if reason is not None:
        return reason
    if not _segments(parent):
        return "cannot be checked: no approved directory to check it against"
    if ".." in _segments(parent):
        return "cannot be checked: the approved directory contains a '..' segment"
    if not is_absolute(parent):
        return ("is absolute while the approved directory is relative, so the"
                " two cannot be compared")
    if not contains(parent, child):
        return "is not that directory and does not sit inside it"
    return None


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
    # T-485/A3. Segments alone cannot tell `/w/a` from `w/a`, and the second
    # one is resolved by the runtime against the supervisor's cwd -- an
    # arbitrary directory that was never inside the approved one. Two paths
    # that disagree about being absolute are not comparable, so the answer is
    # "no", never "close enough".
    if is_absolute(parent) != is_absolute(child):
        return False
    # A `..` that survived normpath is a leading one on a relative path. It
    # points somewhere only the caller's cwd can decide, so it is refused
    # rather than guessed at.
    if ".." in outer or ".." in inner:
        return False
    return inner[:len(outer)] == outer
