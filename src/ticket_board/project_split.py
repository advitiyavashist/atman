"""Split one shared ticket board into one board per project (spec §4.11).

The shape of this migration, and the reason every step below is a copy:

* **Copy, never move.** The source board is read-only for the whole run. The
  one exception is the ``.split`` marker written at the very end of ``apply``,
  which freezes it. Nothing on it is ever deleted or rewritten.
* **Ticket ids are kept.** No renumbering, so ``re`` fields, notes, commit
  subjects (``T-809: ...``) and PR titles stay valid. Each new board gets a
  disjoint id block above the shared board's highest id (``_alloc.json``), so
  two boards can never mint the same id.
* **Records are copied byte for byte.** Ticket JSON travels unparsed, so
  ``review_events``, ``review_head``, ``steers``, notes and ``owner_lease``
  arrive unchanged. Message and trajectory lines keep their own bytes, so a
  message copied into two projects has the same ``id`` in both and receipts
  keyed by message id cannot fork.
* **Append-only logs are partitioned, never rewritten.** Every source line is
  routed to zero or more target files unchanged; a line routed nowhere stays
  readable on the frozen archive. The manifest records where every line went,
  so "no line was lost" is checkable rather than asserted.

The manifest is **deterministic**: given the same source bytes and the same
plan it is identical, with no wall-clock field anywhere in it. That is what
lets ``--dry-run`` print the manifest ``--apply`` will write and lets the
audit test compare the two for exact equality. The "when" of a split lives in
the ``.split`` marker and in each new board's ``MASTER.md`` header line, which
is taken from the plan's own ``generated`` stamp so it too stays fixed.

Nothing here calls ``sys.exit`` or prints: refusals are returned as data so
the CLI, the tests and a future API surface all see the same answer.
"""
from __future__ import annotations

import glob
import hashlib
import json
import os
import re
import shutil

try:
    from . import work_view as _wv
except ImportError:  # run as a plain module path, not as a package
    import work_view as _wv


SPLIT_MARKER = ".split"
MANIFEST_NAME = "split-manifest.json"
ARCHIVE_SLUG = "shared-archive"
ARCHIVE = "archive"
UNASSIGNED = "unassigned"
PLAN_VERSION = 1

# New ids must not collide across the boards this migration creates. A shared
# floor above the highest existing id only protects against the *old* ids; two
# new boards seeded to the same floor would both mint the next one. So each
# project gets its own disjoint block.
ID_BLOCK = 10000
RECENT_OWNER_DAYS = 30

_SLUG_RE = re.compile(r"[^a-z0-9._-]+")


# --------------------------------------------------------------------------
# small helpers
# --------------------------------------------------------------------------

def repo_slug(repo):
    """Project slug for a ticket's ``repo`` value, or '' when it has none.

    ``repo`` on the real board is a git remote URL
    (``https://github.com/<org>/steer.git``), but a local path and a bare name
    both appear too, so all three reduce to the same basename-minus-``.git``.
    """
    raw = str(repo or "").strip()
    if not raw:
        return ""
    raw = raw.rstrip("/")
    if raw.endswith(".git"):
        raw = raw[: -len(".git")]
    base = raw.replace("\\", "/").rstrip("/").split("/")[-1]
    if ":" in base:  # scp-style git@host:org/name
        base = base.split(":")[-1]
    if base.startswith("."):
        # A dot-directory is not a repo name. The real board has one ticket
        # whose `repo` is the board's own `.tickets/.git`, and folding that
        # into the `tickets` project would be a silent wrong attribution --
        # exactly what "a ticket with no repo is never auto-attributed"
        # forbids. It becomes unassigned, and the operator decides.
        return ""
    return _SLUG_RE.sub("-", base.lower()).strip("-.")


def sha256_bytes(data):
    return hashlib.sha256(data).hexdigest()


def _read_bytes(path):
    with open(path, "rb") as f:
        return f.read()


def _split_lines(data):
    """Source lines as exact bytes, each including its trailing newline.

    Blank lines are preserved as lines so a routing table stays index-aligned
    with the file the operator can open and count.
    """
    if not data:
        return []
    out = data.split(b"\n")
    if out and out[-1] == b"":
        out.pop()
        return [ln + b"\n" for ln in out]
    return [ln + b"\n" for ln in out[:-1]] + [out[-1]]


def _json_line(raw):
    try:
        rec = json.loads(raw.decode("utf-8"))
    except (ValueError, UnicodeDecodeError):
        return None
    return rec if isinstance(rec, dict) else None


def _line_id(rec):
    """Merge-back dedup identity, or None when a line must not be deduped.

    Dedup exists for one case: the same MESSAGE, which carries a real `id`,
    is appended to two project boards after the split -- one recipient homed
    in each -- and must reach the restored shared board once. That is what
    §4.11's "dedup by id" means and what the acceptance test checks.

    Everything else must not be deduped, and the first spelling of this
    function did it anyway: it fell back to tickets._msg_id's at/from/to/re/
    text tuple, which a trajectory event does not carry. Four `note` calls
    produced four events whose records were byte-identical apart from a
    one-second gap, so they collapsed to two and merge-back dropped two real
    events while reporting success. Content is not identity in an event
    stream: three identical "update T-100 by ann" lines are three things that
    happened. A line with no id can only have been appended after the split,
    on exactly one board -- `_appended_lines` slices strictly past the bytes
    the split wrote -- so there is nothing for it to collide with.
    """
    return str(rec["id"]) if rec.get("id") else None


def _to_tokens(value):
    return [t.strip() for t in str(value or "").replace(";", ",").split(",") if t.strip()]


def _recipients(msg):
    tokens = _to_tokens(msg.get("to"))
    if tokens:
        return tokens
    mentions = msg.get("mentions") or []
    return [str(m) for m in mentions if m]


def _days_between(later, earlier):
    """Whole days between two board timestamps, or None when unreadable."""
    from datetime import datetime

    def parse(s):
        s = str(s or "").strip()
        if not s:
            return None
        for fmt in ("%Y-%m-%dT%H:%M:%SZ", "%Y-%m-%dT%H:%M:%S%z", "%Y-%m-%d"):
            try:
                return datetime.strptime(s, fmt).replace(tzinfo=None)
            except ValueError:
                continue
        return None

    a, b = parse(later), parse(earlier)
    if a is None or b is None:
        return None
    return (a - b).days


# --------------------------------------------------------------------------
# reading the source board
# --------------------------------------------------------------------------

class SourceBoard(object):
    """Everything the split reads, read once, never written."""

    def __init__(self, board):
        self.board = os.path.realpath(board)
        self.tickets = {}          # id -> parsed record
        self.ticket_bytes = {}     # id -> exact file bytes
        for path in sorted(glob.glob(os.path.join(self.board, "T-*.json"))):
            tid = os.path.basename(path)[:-len(".json")]
            try:
                raw = _read_bytes(path)
                rec = json.loads(raw.decode("utf-8"))
            except (OSError, ValueError, UnicodeDecodeError):
                continue
            if not isinstance(rec, dict):
                continue
            rec.setdefault("id", tid)
            self.tickets[tid] = rec
            self.ticket_bytes[tid] = raw

    # ---- log files -------------------------------------------------------
    def message_files(self):
        live = os.path.join(self.board, "messages.jsonl")
        names = sorted(os.path.basename(p) for p in
                       glob.glob(os.path.join(self.board, "messages.*.jsonl")))
        out = [n for n in names]
        if os.path.isfile(live):
            out.append("messages.jsonl")
        return out

    def trajectory_files(self):
        live = os.path.join(self.board, "trajectories.jsonl")
        names = sorted(os.path.basename(p) for p in
                       glob.glob(os.path.join(self.board, "trajectories.*.jsonl")))
        out = [n for n in names]
        if os.path.isfile(live):
            out.append("trajectories.jsonl")
        return out

    def lines(self, name):
        path = os.path.join(self.board, name)
        try:
            return _split_lines(_read_bytes(path))
        except OSError:
            return []

    # ---- json side files -------------------------------------------------
    def read_json(self, name, default=None):
        path = os.path.join(self.board, name)
        try:
            rec = json.loads(_read_bytes(path).decode("utf-8"))
        except (OSError, ValueError, UnicodeDecodeError):
            return default
        return rec

    def agent_names(self):
        out = []
        for path in sorted(glob.glob(os.path.join(self.board, "agents", "*.json"))):
            name = os.path.basename(path)[:-len(".json")]
            if name and not name.startswith("."):
                out.append(name)
        return out

    def highest_ticket_number(self):
        best = 0
        for tid in self.tickets:
            stem = tid.split("-", 1)[-1]
            if stem.isdigit():
                best = max(best, int(stem))
        return best


# --------------------------------------------------------------------------
# step 1: propose
# --------------------------------------------------------------------------

def _epic_majority(src, assigned):
    """slug most common among the attributed tickets sharing each epic."""
    counts = {}
    for tid, slug in assigned.items():
        epic = (src.tickets[tid].get("epic") or "").strip()
        if not epic or slug in (UNASSIGNED, ARCHIVE):
            continue
        counts.setdefault(epic, {}).setdefault(slug, 0)
        counts[epic][slug] += 1
    out = {}
    for epic, by_slug in counts.items():
        best = sorted(by_slug.items(), key=lambda kv: (-kv[1], kv[0]))
        out[epic] = best[0][0] if best else ""
    return out


def _default_board_path(src, slug):
    """Where a project's board would most plausibly live.

    Sibling of the repo that holds the shared board, which is where this
    fleet's checkouts already sit. Deliberately **empty** when that lands on
    the shared board itself: the shared board becomes the frozen archive, so
    a project cannot also be it, and picking a path for the operator here
    would hide a real decision (spec step 2: the operator edits the plan).
    """
    repo_root = os.path.dirname(src.board)
    candidate = os.path.join(os.path.dirname(repo_root), slug, ".tickets")
    if os.path.realpath(candidate) == src.board:
        return ""
    return candidate


def _seat_homes(src, assigned, plan_projects, generated):
    """Home rule (§4.11): own/reserved non-done work, named lead, or recent."""
    homes = {}

    def add(seat, slug):
        seat = (seat or "").strip()
        if not seat or slug in ("", UNASSIGNED, ARCHIVE):
            return
        homes.setdefault(seat, set()).add(slug)

    for tid, t in src.tickets.items():
        slug = assigned.get(tid, UNASSIGNED)
        done = (t.get("status") or "") == "done"
        if not done:
            add(t.get("owner"), slug)
            add(t.get("reserved_for"), slug)
        else:
            age = _days_between(generated, t.get("updated"))
            if age is not None and age <= RECENT_OWNER_DAYS:
                add(t.get("owner"), slug)
    for slug, proj in (plan_projects or {}).items():
        add(proj.get("master"), slug)
        add(proj.get("cos"), slug)
    return dict((k, sorted(v)) for k, v in sorted(homes.items()))


def _edge_kind(parent):
    if _wv.dep_released(parent):
        return "released"
    if (parent.get("status") or "") == "done":
        return "done-unaccepted"
    return "pending"


def cross_project_edges(src, assigned):
    """Every dependency edge whose two ends land in different projects.

    Unattributed tickets are their own bucket rather than being quietly
    folded into a neighbour: the real count is only known after the operator
    attributes them, and saying so is more useful than a confident wrong
    number.
    """
    out = []
    for tid in sorted(src.tickets):
        child = src.tickets[tid]
        child_slug = assigned.get(tid, UNASSIGNED)
        for dep in child.get("deps") or []:
            parent = src.tickets.get(dep)
            parent_slug = assigned.get(dep, UNASSIGNED) if parent else ""
            if parent is None:
                continue  # dangling dep: not a cross-project edge
            if parent_slug == child_slug:
                continue
            out.append({
                "child": tid, "child_project": child_slug,
                "parent": dep, "parent_project": parent_slug,
                "kind": _edge_kind(parent),
                "child_done": (child.get("status") or "") == "done",
                "involves_unassigned": UNASSIGNED in (child_slug, parent_slug),
            })
    return out



def _repo_roots_for(src, slug, registry):
    out = []
    boards = (registry or {}).get("boards") if isinstance(registry, dict) else None
    if isinstance(boards, dict):
        for root in boards:
            try:
                real = os.path.realpath(os.path.expanduser(str(root)))
            except (TypeError, ValueError, OSError):
                continue
            if os.path.basename(real) == slug:
                out.append(real)
    guess = os.path.join(os.path.dirname(os.path.dirname(src.board)), slug)
    if os.path.isdir(guess) and os.path.realpath(guess) not in out:
        out.append(os.path.realpath(guess))
    return sorted(set(out))


def propose(board, generated="", registry=None):
    """Read-only plan. Nothing is auto-attributed; hints are labelled hints."""
    src = SourceBoard(board)
    assigned = {}
    hints = {}
    for tid, t in src.tickets.items():
        slug = repo_slug(t.get("repo"))
        assigned[tid] = slug or UNASSIGNED
    majority = _epic_majority(src, assigned)
    for tid, slug in assigned.items():
        if slug != UNASSIGNED:
            continue
        t = src.tickets[tid]
        hint = {
            "branch": (t.get("branch") or "").strip(),
            "artifact_dir": (t.get("artifact_dir") or "").strip(),
            "epic": (t.get("epic") or "").strip(),
            "epic_majority": majority.get((t.get("epic") or "").strip(), ""),
            "status": t.get("status") or "",
            "title": (t.get("title") or "")[:90],
        }
        hints[tid] = hint

    slugs = sorted(set(s for s in assigned.values() if s != UNASSIGNED))
    master = src.read_json("master.json", {}) or {}
    projects = {}
    for i, slug in enumerate(slugs):
        repos = sorted(set(str(src.tickets[t].get("repo") or "") for t in src.tickets
                           if repo_slug(src.tickets[t].get("repo")) == slug))
        projects[slug] = {
            "board": _default_board_path(src, slug),
            "repos": [r for r in repos if r],
            # Local checkout roots, so `apply` can repoint the machine
            # registry. Filled from the registry when it already knows one,
            # otherwise a sibling of the repo holding the shared board if it
            # exists on disk. The operator edits this like any other field.
            "repo_roots": _repo_roots_for(src, slug, registry),
            # Carried, not invented: the shared board's own master and CoS are
            # the only recorded leadership there is. `lead` is left unset on
            # purpose -- decision 2 says the user picks it per project.
            "master": (master.get("owner") or ""),
            "cos": (master.get("cos") or ""),
            "objective": True,
        }
    base = ((src.highest_ticket_number() // 1000) + 1) * 1000
    id_floor = dict((slug, base + i * ID_BLOCK) for i, slug in enumerate(slugs))

    seats = _seat_homes(src, assigned, projects, generated)
    plan = {
        "version": PLAN_VERSION,
        "generated": generated,
        "source_board": src.board,
        "archive": {"slug": ARCHIVE_SLUG, "board": src.board},
        "highest_ticket_number": src.highest_ticket_number(),
        "id_floor": id_floor,
        "projects": projects,
        "tickets": dict(sorted(assigned.items())),
        "hints": dict(sorted(hints.items())),
        "seats": seats,
        "cross_project_edges": cross_project_edges(src, assigned),
        "allow_pending_external": False,
    }
    plan["summary"] = summarize(src, plan)
    if registry is not None:
        plan["registry_before"] = registry
    return plan


def summarize(src, plan):
    """Counts the operator needs: per project, per gap, per edge kind.

    Edges are recomputed from the plan's current attribution rather than read
    from `plan["cross_project_edges"]`, so an edited plan reports its own
    edges and not the proposal's.
    """
    assigned = plan.get("tickets") or {}
    per = {}
    for tid, slug in assigned.items():
        per.setdefault(slug, {"tickets": 0, "open": 0, "done": 0})
        per[slug]["tickets"] += 1
        st = (src.tickets.get(tid, {}).get("status") or "")
        per[slug]["done" if st == "done" else "open"] += 1
    edges = cross_project_edges(src, assigned)
    kinds = {}
    for e in edges:
        kinds[e["kind"]] = kinds.get(e["kind"], 0) + 1
    unassigned_live = [t for t, s in assigned.items()
                       if s == UNASSIGNED
                       and (src.tickets.get(t, {}).get("status") or "") != "done"]
    return {
        "per_project": dict(sorted(per.items())),
        "cross_project_edges_by_kind": dict(sorted(kinds.items())),
        "cross_project_edges": len(edges),
        "unassigned_total": sum(1 for s in assigned.values() if s == UNASSIGNED),
        "unassigned_live": sorted(unassigned_live),
        "seats_multi_project": sorted(s for s, h in (plan.get("seats") or {}).items()
                                      if len(h) > 1),
    }


# --------------------------------------------------------------------------
# step 2/3: validate the edited plan
# --------------------------------------------------------------------------

def _refusal(kind, text, **extra):
    rec = {"kind": kind, "text": text}
    rec.update(extra)
    return rec


def project_slugs(plan):
    return sorted((plan.get("projects") or {}).keys())



def shadow_summary(path):
    """What a non-empty target board already holds. Read-only.

    Deliberately self-contained rather than importing `tickets._shadow_board_summary`:
    this module is a pure engine the CLI calls, not the other way round.
    """
    out = {"path": path, "tickets": 0, "messages": 0, "agents": 0}
    out["tickets"] = len(glob.glob(os.path.join(path, "T-*.json")))
    try:
        with open(os.path.join(path, "messages.jsonl"), "rb") as f:
            out["messages"] = sum(1 for ln in f if ln.strip())
    except OSError:
        pass
    out["agents"] = len([p for p in glob.glob(os.path.join(path, "agents", "*.json"))])
    return out


def validate(src, plan, allow_pending_external=None):
    """Every reason this plan cannot be applied, as data. Never raises.

    Ordered so the operator sees structural problems (a project with nowhere
    to live) before per-ticket ones.
    """
    out = []
    projects = plan.get("projects") or {}
    if not projects:
        out.append(_refusal("no-projects", "the plan names no projects"))
    if plan.get("version") != PLAN_VERSION:
        out.append(_refusal("plan-version", "plan version %r is not %d"
                            % (plan.get("version"), PLAN_VERSION)))
    if os.path.realpath(plan.get("source_board") or "") != src.board:
        out.append(_refusal("wrong-source",
                            "plan was proposed for %s, not %s"
                            % (plan.get("source_board"), src.board)))

    seen_paths = {}
    for slug in sorted(projects):
        proj = projects[slug] or {}
        path = (proj.get("board") or "").strip()
        if not path:
            out.append(_refusal(
                "project-board-unset",
                "project %r has no board path. The shared board becomes the "
                "frozen archive, so no project may live there -- pick a path "
                "and set it with: atm project add %s --board <path>" % (slug, slug),
                project=slug))
            continue
        real = os.path.realpath(os.path.expanduser(path))
        if real == src.board:
            out.append(_refusal("project-board-is-source",
                                "project %r points at the shared board itself" % slug,
                                project=slug))
            continue
        if real.startswith(src.board + os.sep):
            out.append(_refusal("project-board-inside-source",
                                "project %r would live inside the shared board (%s)"
                                % (slug, real), project=slug))
            continue
        if real in seen_paths:
            out.append(_refusal("project-board-shared",
                                "projects %r and %r both point at %s"
                                % (seen_paths[real], slug, real), project=slug))
            continue
        seen_paths[real] = slug
        if os.path.exists(real) and os.listdir(real):
            # §4.11 names this case directly: the local atman/.tickets already
            # holds a few tickets and messages, and the split must report it
            # as a shadow rather than write a project board on top of it.
            sh = shadow_summary(real)
            out.append(_refusal(
                "project-board-shadow",
                "%s already exists and is not empty: %d ticket(s), %d message(s), "
                "%d agent record(s). Building %r here would write a project board "
                "on top of a board something else may still read. Move it aside "
                "first (it is never deleted): atm board-archive-shadow %s --yes"
                % (real, sh["tickets"], sh["messages"], sh["agents"], slug, real),
                project=slug, shadow=sh))

    assigned = plan.get("tickets") or {}
    known = set(projects) | {UNASSIGNED, ARCHIVE}
    for tid in sorted(src.tickets):
        slug = assigned.get(tid)
        if slug is None:
            out.append(_refusal("ticket-unplanned",
                                "%s is on the board but not in the plan" % tid,
                                ticket=tid))
            continue
        if slug not in known:
            out.append(_refusal("ticket-unknown-project",
                                "%s is assigned to unknown project %r" % (tid, slug),
                                ticket=tid, project=slug))
            continue
        if slug != UNASSIGNED:
            continue
        status = (src.tickets[tid].get("status") or "")
        if status != "done":
            # Step 2 of the spec: every non-done unassigned ticket must be
            # given a project. A done one may stay in `archive`.
            out.append(_refusal(
                "unassigned-live",
                "%s is %s and has no project. Nothing is auto-attributed: "
                "assign it in the plan (hints are in plan.hints[%s])."
                % (tid, status or "open", tid),
                ticket=tid, status=status))

    allow = (plan.get("allow_pending_external") if allow_pending_external is None
             else allow_pending_external)
    # Recomputed, never read from the plan: `cross_project_edges` there is the
    # propose-time snapshot, and the operator's edits are exactly what changes
    # which edges cross a boundary.
    slugs = set(projects)
    for e in cross_project_edges(src, assigned):
        if e["kind"] != "pending":
            continue
        if allow:
            continue
        if e["child_project"] not in slugs:
            # The child stays on the archive, so it is not copied, gets no
            # snapshot, and keeps the dep it always had. Nothing to decide.
            continue
        out.append(_refusal(
            "pending-external-dep",
            "%s depends on %s, which is not done, and the two land in "
            "different projects. Put both in one project, or pass "
            "--allow-pending-external to record an unreleased snapshot."
            % (e["child"], e["parent"]),
            ticket=e["child"], parent=e["parent"]))
    return out


def preconditions(src):
    """Live-state refusals for --apply only (§4.11 step 4).

    Read from the board's own receipts, not from a process table: a receipt
    is what the rest of atm treats as authoritative for "a run is in flight",
    and disagreeing with it here would make the refusal unexplainable.
    """
    out = []
    agents = os.path.join(src.board, "agents")
    for path in sorted(glob.glob(os.path.join(agents, "*.run"))):
        seat = os.path.basename(path)[:-len(".run")]
        try:
            rec = json.loads(_read_bytes(path).decode("utf-8"))
        except (OSError, ValueError, UnicodeDecodeError):
            rec = {}
        if isinstance(rec, dict) and rec.get("active"):
            out.append(_refusal("active-run",
                                "seat %s has an active run receipt (%s). Stop "
                                "seats first: atm spawn --stop" % (seat, path),
                                seat=seat))
    for path in sorted(glob.glob(os.path.join(agents, "*.watch.pid"))):
        seat = os.path.basename(path)[:-len(".watch.pid")]
        try:
            pid = int(_read_bytes(path).decode("utf-8").strip().split()[0])
        except (OSError, ValueError, IndexError, UnicodeDecodeError):
            continue
        if _pid_alive(pid):
            out.append(_refusal("watcher-alive",
                                "seat %s has a live watcher (pid %d). Stop it "
                                "first: atm spawn --stop" % (seat, pid),
                                seat=seat, pid=pid))
    lock = os.path.join(src.board, "merge.lock")
    if os.path.isfile(lock) and _flock_held(lock):
        out.append(_refusal("merge-lock",
                            "merge.lock is held: a merge is in flight"))
    if os.path.exists(os.path.join(src.board, SPLIT_MARKER)):
        out.append(_refusal("already-split",
                            "%s is already split (%s present)"
                            % (src.board, SPLIT_MARKER)))
    return out


def _pid_alive(pid):
    try:
        os.kill(pid, 0)
    except OSError as e:
        import errno
        return e.errno == errno.EPERM
    return True


def _flock_held(path):
    """True when another process holds an exclusive flock on `path`."""
    try:
        import fcntl
    except ImportError:  # pragma: no cover - non-POSIX
        return False
    try:
        fd = os.open(path, os.O_RDWR)
    except OSError:
        return False
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        fcntl.flock(fd, fcntl.LOCK_UN)
        return False
    except OSError:
        return True
    finally:
        os.close(fd)


# --------------------------------------------------------------------------
# emitting: one code path for --dry-run and --apply
# --------------------------------------------------------------------------

class Emitter(object):
    """Accumulates the manifest; writes bytes only when given roots.

    Dry-run and apply run the *same* traversal through this object. The
    manifest therefore cannot drift between them by construction, which is
    what ``test_split_dry_run_writes_nothing`` checks rather than assumes.
    """

    def __init__(self, roots=None):
        self.roots = roots or {}
        self.files = {}   # slug -> relpath -> {"sha256": h, "bytes": n, "lines": n}
        self._hash = {}   # (slug, relpath) -> hashlib object
        self._open = {}   # (slug, relpath) -> file object

    def emit(self, slug, relpath, data, lines=0):
        key = (slug, relpath)
        h = self._hash.get(key)
        if h is None:
            h = self._hash[key] = hashlib.sha256()
            self.files.setdefault(slug, {})[relpath] = {
                "sha256": "", "bytes": 0, "lines": 0}
        h.update(data)
        row = self.files[slug][relpath]
        row["bytes"] += len(data)
        row["lines"] += lines
        row["sha256"] = h.hexdigest()
        root = self.roots.get(slug)
        if root:
            f = self._open.get(key)
            if f is None:
                path = os.path.join(root, relpath)
                d = os.path.dirname(path)
                if d:
                    os.makedirs(d, exist_ok=True)
                f = self._open[key] = open(path, "wb")
            f.write(data)

    def close(self):
        for f in self._open.values():
            f.close()
        self._open = {}

    def manifest_files(self):
        return dict((slug, dict(sorted(rows.items())))
                    for slug, rows in sorted(self.files.items()))


def _dumps(rec):
    """Stable JSON for the records the split rewrites rather than copies."""
    return (json.dumps(rec, indent=2) + "\n").encode("utf-8")


class Routing(object):
    """Where each append-only line went, recorded compactly but readably."""

    def __init__(self):
        self.legend = []
        self._by_key = {}
        self.files = {}

    def record(self, name, slugs):
        key = tuple(sorted(slugs))
        idx = self._by_key.get(key)
        if idx is None:
            idx = self._by_key[key] = len(self.legend)
            self.legend.append(list(key))
        self.files.setdefault(name, []).append(idx)

    def as_manifest(self):
        return {"legend": [list(x) for x in self.legend],
                "files": dict(sorted(self.files.items()))}

    def counts(self):
        """Per-file {slug or 'archive': lines} -- the operator-facing summary."""
        out = {}
        for name, rows in self.files.items():
            per = {}
            for idx in rows:
                targets = self.legend[idx] or [ARCHIVE]
                for slug in targets:
                    per[slug] = per.get(slug, 0) + 1
            out[name] = dict(sorted(per.items()))
        return out


# --------------------------------------------------------------------------
# the traversal: what goes where
# --------------------------------------------------------------------------

# Live process state and repository plumbing, not board records. Copying a run
# receipt would resurrect a run that is not running; copying an identity would
# let a session answer as a seat on the wrong board; and `.git` inside a board
# directory belongs to whatever repo the operator keeps there, not to the
# board -- it also churns on its own, which would make the frozen-board check
# in `undo` report conflicts that are not conflicts.
NEVER_COPIED_SUFFIXES = (".run", ".run.lock", ".watch.pid", ".pid")
NEVER_COPIED_DIRS = (".identities", ".git")
NEVER_COPIED_NAMES = (SPLIT_MARKER, MANIFEST_NAME, "merge.lock", ".agent-identity")


def _homes(plan, seat):
    return list((plan.get("seats") or {}).get(seat) or [])


def _ticket_project(plan, tid):
    slug = (plan.get("tickets") or {}).get(tid, UNASSIGNED)
    return "" if slug in (UNASSIGNED, ARCHIVE) else slug


def _message_targets(plan, msg, slugs):
    """Routing rules for one message line (§4.11 'What goes where')."""
    re_id = str(msg.get("re") or "").strip()
    if re_id:
        slug = _ticket_project(plan, re_id)
        return [slug] if slug else []
    sender_homes = _homes(plan, msg.get("from"))
    recips = _recipients(msg)
    if recips:
        out = set(sender_homes)
        for r in recips:
            out.update(_homes(plan, r))
        return sorted(out & set(slugs))
    return sorted(set(sender_homes) & set(slugs))


def _trajectory_targets(plan, ev, slugs):
    tid = str(ev.get("ticket") or "").strip()
    if tid:
        slug = _ticket_project(plan, tid)
        return [slug] if slug else []
    return sorted(set(_homes(plan, ev.get("agent"))) & set(slugs))


def _filtered_epic(rec, local_ids, source_board):
    out = dict(rec)
    for key in ("tickets", "members"):
        if isinstance(out.get(key), list):
            out[key] = [x for x in out[key] if x in local_ids]
    out["split_from"] = source_board
    return out



def external_snapshot(parent, parent_slug):
    """The child-side record of a dependency that now lives on another board.

    A released parent is frozen into a snapshot the child's own gate can read;
    an unreleased one is recorded as unreleased **with its reason**. The
    migration never turns an unaccepted parent into an accepted one, so
    ``released`` is false whenever ``dep_released`` is false, no matter how
    finished the parent looks.
    """
    # A parent the operator left on the archive is named by the archive's own
    # slug, not by the pseudo-value `unassigned`: `shared-archive:T-180` is a
    # reference `refresh-external` can actually resolve, and it says plainly
    # where the parent went.
    if parent_slug in ("", None, UNASSIGNED, ARCHIVE):
        parent_slug = ARCHIVE_SLUG
    rec = {"project": parent_slug, "id": parent.get("id") or ""}
    if _wv.dep_released(parent):
        kind = "accept" if _wv.structured_accept(parent) else (
            "merge" if _wv.structured_merge(parent) else "override")
        at = ""
        for ev in reversed(parent.get("review_events") or []):
            if isinstance(ev, dict) and (ev.get("kind") or "").lower() == "accept":
                at = str(ev.get("at") or "")
                break
        if not at:
            at = str((parent.get("release_override") or {}).get("at") or "")
        rec["released"] = {"kind": kind,
                           "sha": _wv.accepted_release_sha(parent) or "",
                           "at": at}
    else:
        rec["released"] = False
        rec["reason"] = ("done-unaccepted"
                         if (parent.get("status") or "") == "done" else "pending")
    return rec


def external_deps_by_child(src, plan):
    """{child id: [snapshot, ...]} for every edge that crosses a project."""
    assigned = plan.get("tickets") or {}
    slugs = set(project_slugs(plan))
    out = {}
    for tid in sorted(src.tickets):
        child_slug = assigned.get(tid)
        if child_slug not in slugs:
            continue
        for dep in src.tickets[tid].get("deps") or []:
            parent = src.tickets.get(dep)
            if parent is None:
                continue
            parent_slug = assigned.get(dep)
            if parent_slug == child_slug:
                continue
            out.setdefault(tid, []).append(external_snapshot(parent, parent_slug))
    return out


def build(src, plan, emitter, routing=None):
    """Walk every source file once, emitting each target's bytes.

    Returns the per-file routing table. The traversal is the single source of
    truth for both the dry run and the apply, and it never reads a target.
    """
    routing = routing if routing is not None else Routing()
    slugs = project_slugs(plan)
    slug_set = set(slugs)
    assigned = plan.get("tickets") or {}
    generated = plan.get("generated") or ""

    local_ids = dict((s, set()) for s in slugs)
    for tid, slug in assigned.items():
        if slug in slug_set:
            local_ids[slug].add(tid)

    # ---- tickets ---------------------------------------------------------
    # Byte for byte, except the one change §4.11 requires: a dependency whose
    # parent lands in another project leaves `deps` and becomes an
    # `external_deps` snapshot, so each board's release gate reads its own
    # records and never the other board.
    externals = external_deps_by_child(src, plan)
    for tid in sorted(src.tickets):
        slug = assigned.get(tid)
        if slug not in slug_set:
            continue
        if tid in externals:
            rec = dict(src.tickets[tid])
            cut = set(x["id"] for x in externals[tid])
            rec["deps"] = [d for d in (rec.get("deps") or []) if d not in cut]
            rec["external_deps"] = externals[tid]
            emitter.emit(slug, tid + ".json", _dumps(rec))
        else:
            emitter.emit(slug, tid + ".json", src.ticket_bytes[tid])
        # Claim locks travel with their ticket: a lock is live state, but
        # dropping one would make a ticket that someone holds look free.
        for lock in (tid + ".lock", tid + ".json.lock"):
            lp = os.path.join(src.board, lock)
            if not os.path.isfile(lp):
                continue
            try:
                emitter.emit(slug, lock, _read_bytes(lp))
            except OSError:
                continue

    # ---- epics and sprints: one copy per project that has a member -------
    for sub, prefix, field in (("epics", "E", "epic"), ("sprints", "S", "sprint")):
        for path in sorted(glob.glob(os.path.join(src.board, sub, prefix + "-*.json"))):
            name = os.path.basename(path)
            rid = name[:-len(".json")]
            try:
                rec = json.loads(_read_bytes(path).decode("utf-8"))
            except (OSError, ValueError, UnicodeDecodeError):
                continue
            if not isinstance(rec, dict):
                continue
            for slug in slugs:
                members = set(t for t in local_ids[slug]
                              if (src.tickets[t].get(field) or "").strip() == rid)
                if not members:
                    continue
                emitter.emit(slug, os.path.join(sub, name),
                             _dumps(_filtered_epic(rec, members, src.board)))

    # ---- append-only logs -------------------------------------------------
    for name in src.message_files():
        for raw in src.lines(name):
            rec = _json_line(raw)
            targets = _message_targets(plan, rec, slugs) if rec else []
            for slug in targets:
                emitter.emit(slug, name, raw, lines=1)
            routing.record(name, targets)
    for name in src.trajectory_files():
        for raw in src.lines(name):
            rec = _json_line(raw)
            targets = _trajectory_targets(plan, rec, slugs) if rec else []
            for slug in targets:
                emitter.emit(slug, name, raw, lines=1)
            routing.record(name, targets)

    # ---- seats -----------------------------------------------------------
    workforce = src.read_json("workforce.json", {}) or {}
    roles = src.read_json("roles.json", {}) or {}
    aliases = src.read_json("aliases.json", {}) or {}
    retired = src.read_json("retired.json", {}) or {}
    coord = src.read_json(os.path.join("coordination", "state.json"), None)

    homed = dict((s, set()) for s in slugs)
    for seat, hs in (plan.get("seats") or {}).items():
        for slug in hs:
            if slug in homed:
                homed[slug].add(seat)

    for seat in src.agent_names():
        slugs_for_seat = [x for x in _homes(plan, seat) if x in slug_set]
        if not slugs_for_seat:
            continue  # no home: the seat stays on the archive
        try:
            raw = _read_bytes(os.path.join(src.board, "agents", seat + ".json"))
        except OSError:
            continue
        # Unchanged, so inbox_seen / seen ids / wake_delivery / limit /
        # auth_check travel with the seat: nothing is redelivered and no
        # receipt changes its label.
        for slug in slugs_for_seat:
            emitter.emit(slug, os.path.join("agents", seat + ".json"), raw)

    for slug in slugs:
        mine = homed[slug]
        sub_wf = dict((k, v) for k, v in workforce.items() if k in mine)
        if sub_wf:
            emitter.emit(slug, "workforce.json", _dumps(sub_wf))
        sub_roles = dict((k, v) for k, v in roles.items() if k in mine)
        if sub_roles:
            emitter.emit(slug, "roles.json", _dumps(sub_roles))
        sub_alias = dict((k, v) for k, v in aliases.items() if v in mine)
        if sub_alias:
            emitter.emit(slug, "aliases.json", _dumps(sub_alias))
        sub_retired = dict(
            (k, v) for k, v in retired.items()
            if k in mine or (isinstance(v, dict) and v.get("alias") in mine)
            or any((src.tickets[t].get("owner") or "") == k
                   or (src.tickets[t].get("reserved_for") or "") == k
                   for t in local_ids[slug]))
        if sub_retired:
            emitter.emit(slug, "retired.json", _dumps(sub_retired))
        if isinstance(coord, dict):
            sub = dict(coord)
            agents = coord.get("agents")
            if isinstance(agents, dict):
                sub["agents"] = dict((k, v) for k, v in agents.items() if k in mine)
            emitter.emit(slug, os.path.join("coordination", "state.json"), _dumps(sub))

    # ---- leadership, objective, decision log ------------------------------
    objective = src.read_json("objective.json", None)
    master_md = None
    p = os.path.join(src.board, "MASTER.md")
    if os.path.isfile(p):
        master_md = _read_bytes(p)
    for slug in slugs:
        proj = (plan.get("projects") or {})[slug] or {}
        rec = {"owner": proj.get("master") or "", "since": generated,
               "cos": proj.get("cos") or "", "split_from": src.board}
        # `lead` is deliberately NOT set: decision 2 says the user picks who
        # they talk to per project, and the app shows a picker until they do.
        for key in ("owner", "cos"):
            if not rec[key]:
                rec.pop(key)
        emitter.emit(slug, "master.json", _dumps(rec))
        if objective is not None and proj.get("objective", True):
            emitter.emit(slug, "objective.json", _dumps(objective))
        if master_md is not None:
            header = ("forked from the shared board %s at %s\n\n"
                      % (src.board, generated or "unknown")).encode("utf-8")
            emitter.emit(slug, "MASTER.md", header + master_md)

    # ---- copied to every project verbatim ---------------------------------
    for name in ("CONTEXT.md", "provider_usage.json", "merge.json"):
        p = os.path.join(src.board, name)
        if not os.path.isfile(p):
            continue
        raw = _read_bytes(p)
        for slug in slugs:
            emitter.emit(slug, name, raw)
    briefs = os.path.join(src.board, "briefs")
    if os.path.isdir(briefs):
        for path in sorted(glob.glob(os.path.join(briefs, "*"))):
            if not os.path.isfile(path):
                continue
            raw = _read_bytes(path)
            rel = os.path.join("briefs", os.path.basename(path))
            for slug in slugs:
                emitter.emit(slug, rel, raw)

    # ---- id allocator floor ----------------------------------------------
    floors = plan.get("id_floor") or {}
    for slug in slugs:
        floor = int(floors.get(slug) or 0)
        if floor:
            emitter.emit(slug, "_alloc.json", _dumps({"T": floor}))

    return routing


# --------------------------------------------------------------------------
# manifest, dry run, apply
# --------------------------------------------------------------------------

def plan_digest(plan):
    return sha256_bytes(json.dumps(plan, sort_keys=True,
                                   separators=(",", ":")).encode("utf-8"))


def _inventory(src, plan, emitter):
    """Every source file, and what the split did with it.

    Lets "nothing was lost" be *checked* rather than asserted: a file that is
    neither copied nor deliberately left on the archive shows up here.
    """
    copied, never = set(), []
    for rows in emitter.files.values():
        copied.update(rows)
    out_copied, out_archive = [], []
    for root, dirs, names in os.walk(src.board):
        dirs[:] = [d for d in sorted(dirs) if d not in NEVER_COPIED_DIRS]
        for name in sorted(names):
            rel = os.path.relpath(os.path.join(root, name), src.board)
            if name.endswith(NEVER_COPIED_SUFFIXES) or name in NEVER_COPIED_NAMES:
                never.append(rel)
            elif rel in copied:
                out_copied.append(rel)
            else:
                out_archive.append(rel)
    for d in sorted(NEVER_COPIED_DIRS):
        if os.path.isdir(os.path.join(src.board, d)):
            never.append(d + "/")
    return {"copied": sorted(out_copied),
            "archive_only": sorted(out_archive),
            "not_copied_live_state": sorted(never)}



def source_digests(src):
    """sha256 of every stable source file, so `undo` can prove the shared
    board stayed frozen. Live-state files are excluded: they move on their
    own and a changed run receipt is not a conflict."""
    out = {}
    for root, dirs, names in os.walk(src.board):
        dirs[:] = [d for d in sorted(dirs) if d not in NEVER_COPIED_DIRS]
        for name in sorted(names):
            if name.endswith(NEVER_COPIED_SUFFIXES) or name in NEVER_COPIED_NAMES:
                continue
            path = os.path.join(root, name)
            rel = os.path.relpath(path, src.board)
            try:
                data = _read_bytes(path)
            except OSError:
                continue
            out[rel] = {"sha256": sha256_bytes(data), "bytes": len(data)}
    return dict(sorted(out.items()))


def manifest(src, plan, emitter, routing, registry_before=None, registry_after=None):
    """The audit record. Deterministic: no wall clock anywhere in it."""
    projects = {}
    for slug in project_slugs(plan):
        proj = (plan.get("projects") or {})[slug] or {}
        projects[slug] = {
            "board": os.path.realpath(os.path.expanduser(proj.get("board") or "")),
            "id_floor": int((plan.get("id_floor") or {}).get(slug) or 0),
            "repos": list(proj.get("repos") or []),
            "repo_roots": list(proj.get("repo_roots") or []),
        }
    return {
        "version": PLAN_VERSION,
        "plan_digest": plan_digest(plan),
        "source_board": src.board,
        "archive": {"slug": ARCHIVE_SLUG, "board": src.board},
        "generated": plan.get("generated") or "",
        "projects": projects,
        "files": emitter.manifest_files(),
        "routing": routing.as_manifest(),
        "line_counts": routing.counts(),
        "source_inventory": _inventory(src, plan, emitter),
        "source_digests": source_digests(src),
        "seats": dict(sorted((plan.get("seats") or {}).items())),
        "external_deps": external_deps_by_child(src, plan),
        "registry_before": registry_before if registry_before is not None else {},
        "registry_after": registry_after if registry_after is not None else {},
    }


def registry_after(plan, registry_before):
    """The machine registry as `apply` would leave it. Pure."""
    reg = json.loads(json.dumps(registry_before or {}))
    if not isinstance(reg, dict):
        reg = {}
    boards = reg.get("boards")
    boards = dict(boards) if isinstance(boards, dict) else {}
    projects = reg.get("projects")
    projects = dict(projects) if isinstance(projects, dict) else {}
    source = os.path.realpath(plan.get("source_board") or "")
    for slug in project_slugs(plan):
        proj = (plan.get("projects") or {})[slug] or {}
        board = os.path.realpath(os.path.expanduser(proj.get("board") or ""))
        projects[slug] = {"board": board, "repos": list(proj.get("repos") or [])}
        for root in proj.get("repo_roots") or []:
            boards[os.path.realpath(os.path.expanduser(root))] = board
    projects[ARCHIVE_SLUG] = {"board": source, "repos": [], "read_only": True}
    reg["boards"] = dict(sorted(boards.items()))
    reg["projects"] = dict(sorted(projects.items()))
    return reg


def dry_run(src, plan, registry_before=None, allow_pending_external=None):
    """Writes nothing. Returns {refusals, summary, manifest}."""
    refusals = validate(src, plan, allow_pending_external=allow_pending_external)
    emitter = Emitter()
    routing = build(src, plan, emitter)
    emitter.close()
    after = registry_after(plan, registry_before or {})
    man = manifest(src, plan, emitter, routing,
                   registry_before=registry_before or {}, registry_after=after)
    return {
        "refusals": refusals,
        "preconditions": preconditions(src),
        "summary": summarize(src, plan),
        "manifest": man,
    }


def _verify_on_disk(root, rows):
    """Re-hash what actually landed. Returns the list of mismatches."""
    bad = []
    for rel, row in sorted(rows.items()):
        path = os.path.join(root, rel)
        try:
            data = _read_bytes(path)
        except OSError:
            bad.append({"file": rel, "problem": "missing"})
            continue
        if len(data) != row["bytes"] or sha256_bytes(data) != row["sha256"]:
            bad.append({"file": rel, "problem": "digest",
                        "expected": row["sha256"], "got": sha256_bytes(data)})
    return bad


def apply_split(src, plan, registry_before=None, expected_manifest=None,
                allow_pending_external=None, write_registry=None, at=""):
    """Build in temp dirs, verify, then rename into place.

    Returns {"ok": bool, "refusals": [...], "manifest": {...}, "boards": {...}}.
    Nothing outside the temp dirs is touched until every digest matches, so a
    refusal here leaves the shared board exactly as it was.
    """
    refusals = validate(src, plan, allow_pending_external=allow_pending_external)
    refusals += preconditions(src)
    if refusals:
        return {"ok": False, "refusals": refusals, "manifest": None, "boards": {}}

    slugs = project_slugs(plan)
    targets, temps = {}, {}
    digest8 = plan_digest(plan)[:8]
    for slug in slugs:
        real = os.path.realpath(os.path.expanduser(
            ((plan.get("projects") or {})[slug] or {}).get("board") or ""))
        parent = os.path.dirname(real)
        os.makedirs(parent, exist_ok=True)
        tmp = os.path.join(parent, ".%s.split-%s-%s"
                           % (os.path.basename(real), slug, digest8))
        if os.path.exists(tmp):
            shutil.rmtree(tmp)
        os.makedirs(tmp)
        targets[slug], temps[slug] = real, tmp

    emitter = Emitter(roots=temps)
    try:
        routing = build(src, plan, emitter)
    finally:
        emitter.close()
    after = registry_after(plan, registry_before or {})
    man = manifest(src, plan, emitter, routing,
                   registry_before=registry_before or {}, registry_after=after)

    problems = []
    if expected_manifest is not None and expected_manifest != man:
        problems.append(_refusal(
            "manifest-drift",
            "the board changed between the dry run and this apply: the "
            "manifest no longer matches. Re-run --dry-run and compare."))
    for slug in slugs:
        for bad in _verify_on_disk(temps[slug], man["files"].get(slug, {})):
            problems.append(_refusal("verify-failed", "%s: %s %s"
                                     % (slug, bad["file"], bad["problem"]),
                                     project=slug))
    if problems:
        for tmp in temps.values():
            shutil.rmtree(tmp, ignore_errors=True)
        return {"ok": False, "refusals": problems, "manifest": man, "boards": {}}

    # The manifest goes in before the rename, so a board never exists without
    # the record `undo` needs to find it. Each temp dir is a sibling of its
    # target, so the rename is same-filesystem and effectively atomic. If one
    # still failed, earlier boards would be in place and the shared board
    # would be unmarked (the marker is written last) -- a retry then refuses
    # with `project-board-occupied` naming the boards to move aside, which is
    # recoverable, rather than a half-frozen board, which is not.
    for slug in slugs:
        real, tmp = targets[slug], temps[slug]
        with open(os.path.join(tmp, MANIFEST_NAME), "w", encoding="utf-8") as f:
            json.dump(man, f, indent=2, sort_keys=True)
        if os.path.isdir(real) and not os.listdir(real):
            os.rmdir(real)
        os.rename(tmp, real)

    marker = {"at": at, "source_board": src.board,
              "manifest": os.path.join(targets[slugs[0]], MANIFEST_NAME) if slugs else "",
              "archive_slug": ARCHIVE_SLUG,
              "projects": dict((s, targets[s]) for s in slugs),
              "seats": dict(sorted((plan.get("seats") or {}).items()))}
    with open(os.path.join(src.board, SPLIT_MARKER), "w", encoding="utf-8") as f:
        json.dump(marker, f, indent=2, sort_keys=True)

    if write_registry is not None:
        write_registry(after)
    return {"ok": True, "refusals": [], "manifest": man, "boards": targets,
            "marker": marker}


def restart_lines(plan, targets):
    """The exact command each seat's session restarts with, per home."""
    out = []
    for seat, homes in sorted((plan.get("seats") or {}).items()):
        for slug in homes:
            board = targets.get(slug)
            if board:
                out.append("TICKETS_DIR=%s atm spawn %s --persist" % (board, seat))
    return out


# --------------------------------------------------------------------------
# undo
# --------------------------------------------------------------------------

def _board_drift(board, rows):
    """What a new board has that its manifest does not account for."""
    extra, changed = [], []
    expected = set(rows) | {MANIFEST_NAME}
    for root, dirs, names in os.walk(board):
        dirs[:] = sorted(dirs)
        for name in sorted(names):
            rel = os.path.relpath(os.path.join(root, name), board)
            if rel not in expected:
                extra.append(rel)
    for rel, row in sorted(rows.items()):
        try:
            data = _read_bytes(os.path.join(board, rel))
        except OSError:
            changed.append({"file": rel, "problem": "missing"})
            continue
        if sha256_bytes(data) != row["sha256"]:
            changed.append({"file": rel, "problem": "modified"})
    return {"extra": sorted(extra), "changed": changed}


def _appended_lines(board, rel, row):
    """(lines gained after the split, prefix_intact).

    Append-only means the bytes the split wrote are still the file's prefix.
    That is checked, not assumed: if the file was rewritten rather than
    appended to, slicing at the recorded length would hand back arbitrary
    bytes as "new lines", so the caller is told instead and refuses.
    """
    try:
        data = _read_bytes(os.path.join(board, rel))
    except OSError:
        # Gone. That used to read as "intact, nothing appended", which is the
        # benign case for a ticket -- the shared board holds the original --
        # but for a LOG it cannot be told apart from a rename, and a renamed
        # log is the dangerous one: its pre-split lines then look like a brand
        # new file and get appended to the shared board a second time. The
        # reviewer defeated the merge that way. An undo that cannot tell must
        # stop, not guess.
        return [], False
    n = row["bytes"]
    if len(data) < n or sha256_bytes(data[:n]) != row["sha256"]:
        return [], False
    return _split_lines(data[n:]), True


def undo(man, merge_back=False, do_apply=False, at="", write_registry=None):
    """Reverse a split. Boards are moved aside, never deleted.

    Two cases, exactly as §4.11 specifies. Without ``merge_back`` a board that
    has been written to since the split refuses, because silently dropping
    those writes is the one thing an undo must never do.
    """
    source = os.path.realpath(man.get("source_board") or "")
    projects = man.get("projects") or {}
    files = man.get("files") or {}
    report = {"source_board": source, "merge_back": bool(merge_back),
              "boards": {}, "refusals": [], "conflicts": [],
              "moved_aside": {}, "merged": {}, "would_merge": {}}

    drifted = {}
    for slug in sorted(projects):
        board = projects[slug].get("board") or ""
        if not os.path.isdir(board):
            report["refusals"].append(_refusal(
                "missing-board", "%s: %s is not a directory" % (slug, board),
                project=slug))
            continue
        drift = _board_drift(board, files.get(slug, {}))
        report["boards"][slug] = {"board": board, "drift": drift}
        if drift["extra"] or drift["changed"]:
            drifted[slug] = drift

    if drifted and not merge_back:
        for slug, drift in sorted(drifted.items()):
            report["refusals"].append(_refusal(
                "post-split-writes",
                "%s has %d file(s) added and %d changed since the split. "
                "Re-run with --merge-back to fold them into the shared board."
                % (slug, len(drift["extra"]), len(drift["changed"])),
                project=slug))

    if merge_back:
        # The shared board is frozen, so any change to it is a conflict. This
        # should be impossible; it is checked because an undo that guesses is
        # worse than one that stops.
        for rel, row in sorted((man.get("source_digests") or {}).items()):
            path = os.path.join(source, rel)
            try:
                data = _read_bytes(path)
            except OSError:
                report["conflicts"].append({"file": rel, "problem": "removed from the shared board"})
                continue
            if sha256_bytes(data) != row["sha256"]:
                report["conflicts"].append({"file": rel, "problem": "changed on the frozen shared board"})
        plan_merge = _plan_merge_back(man, source)
        report["would_merge"] = plan_merge["summary"]
        report["left_behind"] = _merge_back_leftovers(man, report["boards"])
        report["conflicts"] += plan_merge["conflicts"]
        if report["conflicts"]:
            report["refusals"].append(_refusal(
                "merge-back-conflict",
                "%d conflict(s): the boards are not in the state the "
                "manifest recorded. Nothing was merged."
                % len(report["conflicts"])))

    if report["refusals"] or not do_apply:
        report["ok"] = not report["refusals"]
        return report

    if merge_back:
        report["merged"] = _do_merge_back(man, source)
    marker = os.path.join(source, SPLIT_MARKER)
    if os.path.exists(marker):
        os.remove(marker)
    for slug in sorted(projects):
        board = projects[slug].get("board") or ""
        if not os.path.isdir(board):
            continue
        aside = "%s.split-undone-%s" % (board, at or "undo")
        n = 2
        while os.path.exists(aside):
            aside, n = "%s.split-undone-%s-%d" % (board, at or "undo", n), n + 1
        os.rename(board, aside)
        report["moved_aside"][slug] = aside
    if write_registry is not None:
        write_registry(man.get("registry_before") or {})
    report["ok"] = True
    return report


def _merge_back_folds(rel):
    """True when `--merge-back` folds this new file into the shared board."""
    if rel.endswith(".lock"):
        return True          # transient; deliberately not carried back
    if "/" in rel or os.sep in rel:
        return False         # epics/, sprints/, agents/, briefs/ are not folded
    return rel.endswith(".jsonl") or (
        rel.startswith("T-") and rel.endswith(".json"))


def _merge_back_leftovers(man, boards):
    """New files on a project board that `--merge-back` does NOT fold.

    It folds root-level `T-*.json` and lines appended to the append-only logs
    the manifest recorded. Anything else a seat created after the split -- an
    epic, a sprint, a new agent record, a brief, a newly rotated log file --
    stays on the board `undo` moves aside. Nothing is deleted, but "merged
    back: 1 ticket(s)" printed over a board that also grew an epic is a true
    sentence that leaves a false impression, so the leftovers are named.

    Epics and sprints are not folded by id on purpose. Only `T-` ids get a
    seeded floor per project (§4.11), so two boards descended from one shared
    board can mint the same `E-` id for different records -- merging those by
    id would be a guess, and a guess is the one thing an undo must not make.
    """
    out = {}
    rows = man.get("files") or {}
    for slug in sorted(boards):
        drift = boards[slug].get("drift") or {}
        known = set(rows.get(slug, {}))
        left = [rel for rel in drift.get("extra") or []
                if rel not in known and not _merge_back_folds(rel)]
        # A file the split DID write and a seat then modified is not folded
        # either unless it is a ticket or a log -- a check-in rewrites
        # agents/<seat>.json, which appeared in the drift count and nowhere
        # else. Reported for the same reason: the count is not the name.
        left += [row["file"] for row in drift.get("changed") or []
                 if isinstance(row, dict) and row.get("file")
                 and not _merge_back_folds(row["file"])]
        if left:
            out[slug] = sorted(left)
    return out


def _merge_back_items(man, source):
    """(log lines to append, tickets to copy back) with dedup already applied."""
    projects = man.get("projects") or {}
    files = man.get("files") or {}
    src_tickets = SourceBoard(source).tickets
    # Tickets the split rewrote had their cross-project deps moved into an
    # external_deps snapshot. Merging one back unchanged would leave the
    # restored shared board with a truncated `deps` and a snapshot that means
    # nothing there, so the original edge is put back.
    externals = man.get("external_deps") or {}
    lines, tickets, conflicts = {}, {}, []

    def source_lines(_source, _cache={}):
        """How many times the shared board already holds each log line.

        A COUNT, not a set, and the difference matters. Set membership would
        drop a genuinely new line that happens to be byte-identical to a
        pre-split one -- three identical `update T-100 by ann` events are
        three things that happened, which is the same mistake content-based
        dedup made. Counting means an unrecognised log contributes its
        EXCESS: a copy of a log the split handed out contributes nothing, and
        a fourth copy of a line the source holds three times comes back.

        Built once per process and keyed by board path.
        """
        key = os.path.realpath(_source)
        if key not in _cache:
            got = {}
            src = SourceBoard(_source)
            for name in list(src.message_files()) + list(src.trajectory_files()):
                for raw in src.lines(name):
                    k = raw.strip()
                    got[k] = got.get(k, 0) + 1
            _cache[key] = got
        return _cache[key]

    def board_line_ids(_source):
        """Every line id the shared board already holds, in ANY of its logs.

        Board-wide, not per-basename, and that is the whole fix for the
        reviewer's second falsifier in its surviving form. Keyed by basename,
        this set was empty for `messages.2026-09-20.jsonl` -- a name the
        shared board does not carry -- so a pre-split `msg_c` copied into a
        rotated or dated log came back a second time. The byte multiset in
        `source_lines` did not catch it either: re-serialising the record
        (different key order, different separators) changes the bytes while
        the event stays the same event. An id is the one identity that
        survives both a rename and a re-serialisation, which is why §4.11
        dedups by it.

        Ids collected from every root-level `*.jsonl`, not just the two known
        families: a log the shared board rotated to a third name is exactly
        the case being defended against, so the sweep must not depend on
        recognising the name. Mutated as lines are merged, so the same id
        appended to two project boards still reaches the shared board once.
        """
        got = set()
        for path in sorted(glob.glob(os.path.join(_source, "*.jsonl"))):
            try:
                raws = _split_lines(_read_bytes(path))
            except OSError:
                continue
            for raw in raws:
                rec = _json_line(raw)
                mid = _line_id(rec) if rec else None
                if mid is not None:
                    got.add(mid)
        return got

    # Read once per call, not cached across calls: `_do_merge_back` appends to
    # these same logs, so a set kept between the plan and the apply would call
    # its own writes duplicates on the next undo.
    shared_ids = board_line_ids(source)
    for slug in sorted(projects):
        board = projects[slug].get("board") or ""
        rows = files.get(slug, {})
        # A log the split never wrote for this project -- the board had no
        # trajectories.jsonl at all, and the first `note` created one -- is
        # not in `rows`, so the loop below never looked at it and its lines
        # were dropped while the report said "lines none". Such a file is
        # folded, but NOT as "all new": see the excess rule below, which is
        # what stops a log copied to a second basename from replaying the
        # shared board's own history into it.
        pending = dict(rows)
        try:
            for name in sorted(os.listdir(board)):
                if name.endswith(".jsonl") and name not in pending:
                    pending[name] = None
        except OSError:
            pass
        for rel, row in sorted(pending.items()):
            if rel.endswith(".jsonl"):
                if row is None:
                    try:
                        fresh = _split_lines(_read_bytes(os.path.join(board, rel)))
                    except OSError:
                        fresh = []
                    # "Every line in a file the split never wrote is new" is
                    # false the moment someone copies a log to a second
                    # basename: those lines are the shared board's own, and
                    # appending them back duplicates history. So this file
                    # contributes only its EXCESS over what the source
                    # already holds, counted per line rather than by set
                    # membership -- a real fourth copy of a line the source
                    # holds three times is still a new event.
                    budget = dict(source_lines(source))
                    kept = []
                    for raw in fresh:
                        k = raw.strip()
                        if budget.get(k):
                            budget[k] -= 1
                            continue
                        kept.append(raw)
                    fresh = kept
                    intact = True
                else:
                    fresh, intact = _appended_lines(board, rel, row)
                if not intact:
                    gone = not os.path.exists(os.path.join(board, rel))
                    conflicts.append({
                        "file": "%s:%s" % (slug, rel),
                        "problem": ("moved or removed since the split, so its "
                                    "lines cannot be told from new ones"
                                    if gone else
                                    "rewritten, not appended to, since the "
                                    "split")})
                    continue
                for raw in fresh:
                    rec = _json_line(raw)
                    if rec is None:
                        continue
                    mid = _line_id(rec)
                    if mid is not None:
                        if mid in shared_ids:
                            continue
                        shared_ids.add(mid)
                    lines.setdefault(rel, []).append(raw)
        for root, dirs, names in os.walk(board):
            dirs[:] = sorted(dirs)
            for name in sorted(names):
                if not (name.startswith("T-") and name.endswith(".json")):
                    continue
                rel = os.path.relpath(os.path.join(root, name), board)
                if rel != name:
                    continue
                tid = name[:-len(".json")]
                try:
                    rec = json.loads(_read_bytes(os.path.join(board, name)).decode("utf-8"))
                except (OSError, ValueError, UnicodeDecodeError):
                    continue
                if tid in externals:
                    back = [str(d.get("id")) for d in externals[tid]
                            if isinstance(d, dict) and d.get("id")]
                    rec["deps"] = list(rec.get("deps") or []) + [
                        d for d in back if d not in (rec.get("deps") or [])]
                    rec.pop("external_deps", None)
                old = src_tickets.get(tid)
                if old is None:
                    tickets[tid] = (slug, rec)
                    continue
                if str(rec.get("updated") or "") > str(old.get("updated") or ""):
                    prev = tickets.get(tid)
                    if prev and prev[1].get("updated") != rec.get("updated"):
                        conflicts.append({"ticket": tid,
                                          "problem": "changed on both %s and %s"
                                                     % (prev[0], slug)})
                    tickets[tid] = (slug, rec)
    return lines, tickets, conflicts


def _plan_merge_back(man, source):
    lines, tickets, conflicts = _merge_back_items(man, source)
    return {"summary": {"lines": dict((k, len(v)) for k, v in sorted(lines.items())),
                        "tickets": sorted(tickets)},
            "conflicts": conflicts}


def _do_merge_back(man, source):
    lines, tickets, _ = _merge_back_items(man, source)
    for rel, raws in sorted(lines.items()):
        path = os.path.join(source, rel)
        with open(path, "ab") as f:
            for raw in raws:
                if not raw.endswith(b"\n"):
                    raw += b"\n"
                f.write(raw)
    for tid, (_slug, rec) in sorted(tickets.items()):
        with open(os.path.join(source, tid + ".json"), "w", encoding="utf-8") as f:
            json.dump(rec, f, indent=2)
    return {"lines": dict((k, len(v)) for k, v in sorted(lines.items())),
            "tickets": sorted(tickets)}


# --------------------------------------------------------------------------
# refresh-external
# --------------------------------------------------------------------------

def refresh_external(board, ticket_id, boards_by_slug, apply=True):
    """Re-read the other board once and flip released snapshots that are now
    real accepts. Never the other way: an accept that was withdrawn is a
    decision for the other board's own gate, and a migration tool downgrading
    a release would be doing review by side effect.
    """
    path = os.path.join(board, ticket_id + ".json")
    try:
        rec = json.loads(_read_bytes(path).decode("utf-8"))
    except (OSError, ValueError, UnicodeDecodeError):
        return {"ok": False, "reason": "no such ticket: %s" % ticket_id,
                "changed": [], "unchanged": []}
    deps = rec.get("external_deps")
    if not isinstance(deps, list) or not deps:
        return {"ok": True, "reason": "%s has no external_deps" % ticket_id,
                "changed": [], "unchanged": []}
    changed, unchanged = [], []
    out = []
    for dep in deps:
        if not isinstance(dep, dict):
            out.append(dep)
            continue
        if dep.get("released"):
            out.append(dep)
            unchanged.append({"id": dep.get("id"), "why": "already released"})
            continue
        other = boards_by_slug.get(dep.get("project") or "")
        if not other:
            out.append(dep)
            unchanged.append({"id": dep.get("id"),
                              "why": "no board registered for project %r"
                                     % dep.get("project")})
            continue
        try:
            parent = json.loads(_read_bytes(
                os.path.join(other, str(dep.get("id")) + ".json")).decode("utf-8"))
        except (OSError, ValueError, UnicodeDecodeError):
            out.append(dep)
            unchanged.append({"id": dep.get("id"),
                              "why": "not found on %s" % other})
            continue
        if not _wv.dep_released(parent):
            fresh = dict(dep)
            fresh["reason"] = ("done-unaccepted"
                               if (parent.get("status") or "") == "done" else "pending")
            out.append(fresh)
            unchanged.append({"id": dep.get("id"), "why": fresh["reason"]})
            continue
        fresh = external_snapshot(parent, dep.get("project") or "")
        out.append(fresh)
        changed.append(fresh)
    if changed and apply:
        rec["external_deps"] = out
        with open(path, "w", encoding="utf-8") as f:
            json.dump(rec, f, indent=2)
    return {"ok": True, "reason": "", "changed": changed, "unchanged": unchanged,
            "external_deps": out}
