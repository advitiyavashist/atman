"""The team trajectory log, written from the packaged CLI.

T-311 instrumented the root `tickets.py` -- the file the live shim and
`install.sh` both exec. It is not the only delivery path: `pyproject.toml`
declares `tickets = "ticket_board.cli:main"`, so a `pip install` of this
package produces a `tickets` command that goes through `ticket_board/cli.py`
instead. Left uninstrumented, that entry point writes no events at all, and a
board driven by both entry points -- a watcher on the root script, an operator
on the installed console script -- yields a log with silent holes rather than
an honest absence. `tickets turns` (T-312) cannot tell the two apart, so the
metric would read as fact and be wrong.

This module is the writer both call sites should agree on. `cli.py` imports
it. The root `tickets.py` must stay a standalone single file (the live release
ships it alone, with no package to import), so it keeps its own copy of the
same logic -- and `tests/test_trajectories_entrypoints.py` fails the moment the
two disagree on version, kinds, or emitted shape. A second copy is only safe
while something goes red when it drifts.

Everything here is deliberately dependency-free: it reads the board's own
`objective.json` and `workforce.json` and needs nothing from `cli.py`, which
keeps the import one-directional and lets the API server use it later.

PRIVACY, identical to the root writer: ids, counts, outcomes and timings only.
No prompt text, no tool input or output, no note or message bodies -- their
LENGTH is recorded (notes_len, text_len) and nothing else.
"""

import glob
import hashlib
import json
import os
import sys
from datetime import datetime, timezone

TRAJ_VERSION = 1
TRAJ_MAX_BYTES = int(os.environ.get("TICKETS_TRAJECTORIES_MAX_BYTES", 50 * 1024 * 1024))
TRAJ_KINDS = ("run_start", "run_end", "claim", "update", "review", "done",
              "reopen", "block", "msg", "merge", "shadow_decision")


def _now():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def trajectories_path(board):
    return os.path.join(board, "trajectories.jsonl")


def _absolute_uncollapsed(path):
    """Make absolute without collapsing ``..`` (keeps symlink parents visible)."""
    path = os.path.expanduser(path)
    if os.path.isabs(path):
        return path
    return os.path.join(os.getcwd(), path)


def _nearest_existing_ancestor(path):
    """Walk parents until a lexically-existing path is found (or the root).

    Does not use abspath/normpath: those collapse ``..`` by string and miss
    symlink escapes into the board (``dlink/../agents/alice.json``).
    """
    cur = _absolute_uncollapsed(path)
    while not os.path.lexists(cur):
        parent = os.path.dirname(cur)
        if parent == cur:
            break
        cur = parent
    return cur


def _resolved_path_for_board_guard(path):
    """realpath(nearest existing ancestor) + remaining path components.

    Resolves directory symlinks before collapsing ``..``, so a path that
    string-abspaths outside the board but opens inside it is still detected.
    """
    abs_path = _absolute_uncollapsed(path)
    ancestor = _nearest_existing_ancestor(abs_path)
    if not os.path.lexists(ancestor):
        return os.path.normpath(abs_path)
    real_anc = os.path.realpath(ancestor)
    if abs_path == ancestor:
        return real_anc
    if abs_path.startswith(ancestor):
        rest = abs_path[len(ancestor):].lstrip(os.sep)
    else:
        rest = os.path.basename(abs_path)
    if not rest:
        return real_anc
    return os.path.normpath(os.path.join(real_anc, rest))


def _path_is_under_board_samefile(path, board):
    """True when `path` is the board or a descendant, compared by inode.

    Resolves symlink ancestors first, then samefile-walks so case aliases on
    macOS APFS/HFS+ (``.TICKETS`` vs ``.tickets``) still match by dev/inode.
    """
    board_root = os.path.realpath(board)
    cur = _resolved_path_for_board_guard(path)
    while True:
        if os.path.lexists(cur):
            try:
                if os.path.samefile(cur, board_root):
                    return True
            except OSError:
                pass
        parent = os.path.dirname(cur)
        if parent == cur:
            return False
        cur = parent


def export_path_inside_board(out, board):
    """True when `trajectories export --out` would land under the active board.

    Export writes ``<out>.tmp`` then os.replace onto --out. Pointing either at
    agents/<seat>.json (or any other board file) silently replaces the record
    with JSONL -- exit 0, no warning. Both CLI entry points must refuse before
    writing (T-1140). Symlink ``--out`` / ``<out>.tmp`` are refused: open()
    would follow them into the board. Path checks resolve symlink ancestors
    before ``..`` collapse, then samefile/dev-inode for case aliases.
    """
    if not out or not board:
        return False
    try:
        if os.path.islink(out) or os.path.islink(out + ".tmp"):
            return True
        return (
            _path_is_under_board_samefile(out, board)
            or _path_is_under_board_samefile(out + ".tmp", board)
        )
    except (OSError, ValueError):
        return True


def refuse_export_inside_board(out, board):
    """Exit if --out is under the board. Shared by the packaged CLI entry point."""
    if export_path_inside_board(out, board):
        sys.exit(
            "REFUSING: trajectories export --out must live outside the ticket "
            "board (would replace board records): %s" % out
        )


def open_export_tmp(tmp):
    """Create ``<out>.tmp`` with O_CREAT|O_EXCL|O_NOFOLLOW (no symlink follow)."""
    if os.path.islink(tmp):
        sys.exit(
            "REFUSING: trajectories export temp path must not be a symlink "
            "(would follow into the board): %s" % tmp
        )
    if os.path.lexists(tmp):
        if os.path.isdir(tmp) and not os.path.islink(tmp):
            sys.exit(
                "REFUSING: trajectories export temp path is a directory: %s" % tmp
            )
        os.unlink(tmp)
    flags = os.O_CREAT | os.O_EXCL | os.O_WRONLY
    nofollow = getattr(os, "O_NOFOLLOW", 0)
    if nofollow:
        flags |= nofollow
    try:
        return os.open(tmp, flags, 0o644)
    except OSError as exc:
        sys.exit(
            "REFUSING: cannot create trajectories export temp file safely: "
            "%s (%s)" % (tmp, exc)
        )


def write_export_file(out, events):
    """Write JSONL events to --out via a nofollow temp file + os.replace."""
    if os.path.islink(out):
        sys.exit(
            "REFUSING: trajectories export --out must not be a symlink: %s" % out
        )
    tmp = out + ".tmp"
    fd = open_export_tmp(tmp)
    try:
        with os.fdopen(fd, "w") as f:
            for e in events:
                f.write(json.dumps(e) + "\n")
        os.replace(tmp, out)
    except Exception:
        try:
            if os.path.lexists(tmp) and not os.path.islink(tmp):
                os.unlink(tmp)
        except OSError:
            pass
        raise


def rotate_if_big(board):
    """Same swap-under-a-lock as the root writer, and the same honest caveat:
    a writer already inside an append during the swap can land its line in the
    archive. Every reader of this log reads archives too, so a line in the
    archive is not a lost line."""
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
        archive = os.path.join(board, "trajectories.%s.jsonl" % _now()[:10])
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


def objective_id(board):
    """A stable id for the standing objective, DERIVED from its text and
    set-time rather than stored: `tickets objective` writes no id, and adding
    one would rewrite a file other seats read. Same objective -> same id in
    every process; editing it starts a new id, which is what a reader grouping
    trajectories by objective wants."""
    try:
        with open(os.path.join(board, "objective.json")) as f:
            obj = json.load(f)
    except (IOError, ValueError, OSError):
        return ""
    if not isinstance(obj, dict) or not obj.get("text"):
        return ""
    h = hashlib.sha1(("%s|%s" % (obj["text"], obj.get("at", ""))).encode("utf-8", "replace"))
    return "obj-" + h.hexdigest()[:8]


def agent_harness(board, owner):
    """(harness, model, effort) from what the agent registered with `tickets
    join`. Unregistered -> empty strings, and the caller omits the fields
    rather than writing a plausible-looking default."""
    try:
        with open(os.path.join(board, "workforce.json")) as f:
            w = json.load(f)
        entry = (w if isinstance(w, dict) else {}).get(owner) or {}
    except (IOError, ValueError, OSError):
        entry = {}
    if not isinstance(entry, dict):
        entry = {}
    return (entry.get("tool", "") or "", entry.get("model", "") or "",
            entry.get("effort", "") or "")


def write(board, rec):
    """Append one event. Best-effort by design: instrumentation that can fail
    a `tickets done` is worse than a missing line. Every caller is on a
    command's success path, so a raised exception would abort work that
    already happened."""
    try:
        rotate_if_big(board)
        line_ = json.dumps(rec) + "\n"
        fd = os.open(trajectories_path(board), os.O_CREAT | os.O_WRONLY | os.O_APPEND, 0o644)
        try:
            os.write(fd, line_.encode())
        finally:
            os.close(fd)
    except (OSError, ValueError, TypeError):
        return None
    return rec


def build(board, kind, agent="", ticket=None, **fields):
    """The event dict, without writing it -- the half `test_trajectories_
    entrypoints.py` compares against the root script's.

    `ticket` may be a ticket dict (epic/sprint/repo are read off it) or an id
    string. Optional fields are dropped when empty so a reader can distinguish
    "not known" from a real zero; `exit` and the token counts are the
    exception -- 0 is meaningful there, so only None is dropped.
    """
    if kind not in TRAJ_KINDS:
        return None
    rec = {"v": TRAJ_VERSION, "at": _now(), "kind": kind}
    tid = ""
    if isinstance(ticket, dict):
        tid = ticket.get("id", "")
        for src, dst in (("epic", "epic"), ("sprint", "sprint")):
            if ticket.get(src):
                rec[dst] = ticket[src]
        # NOT the ticket's pinned branch/sha: `branch`/`sha` on an event mean
        # "the tree this event was recorded from", and a review pin can name a
        # different repo entirely (T-272). Only the repo id, which is
        # unambiguous, is taken from the ticket.
        if ticket.get("repo"):
            rec.setdefault("repo", ticket["repo"])
    elif ticket:
        tid = str(ticket)
    if tid:
        rec["ticket"] = tid
    if agent:
        rec["agent"] = agent
        harness, model, effort = agent_harness(board, agent)
        if harness:
            rec["harness"] = harness
        if model:
            rec["model"] = model
        if effort:
            rec["effort"] = effort
    oid = objective_id(board)
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
    return rec


def event(board, kind, agent="", ticket=None, **fields):
    """Build and append one trajectory event."""
    rec = build(board, kind, agent=agent, ticket=ticket, **fields)
    if rec is None:
        return None
    return write(board, rec)


def load(board, include_archives=True):
    """Read events oldest-first. Archives are ON by default: every reader of
    this file is analytical (a metric, an export, a backfill dedup check) and a
    silently truncated history would corrupt the answer."""
    paths = [trajectories_path(board)]
    if include_archives:
        paths = sorted(glob.glob(os.path.join(board, "trajectories.*.jsonl"))) + paths
    out = []
    for p in paths:
        try:
            with open(p) as f:
                for line_ in f:
                    line_ = line_.strip()
                    if not line_:
                        continue
                    try:
                        rec = json.loads(line_)
                    except ValueError:
                        continue
                    if isinstance(rec, dict):
                        out.append(rec)
        except (IOError, OSError):
            continue
    return out
