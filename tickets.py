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
import json
import os
import sys
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


def _repo_root():
    """Root of the MAIN worktree, so every linked worktree shares one board."""
    import subprocess
    try:
        out = subprocess.run(["git", "rev-parse", "--git-common-dir"],
                             capture_output=True, text=True, timeout=5)
    except (OSError, subprocess.TimeoutExpired):
        return None
    if out.returncode != 0:
        return None
    common = os.path.abspath(out.stdout.strip())
    if os.path.basename(common) == ".git":
        return os.path.dirname(common)
    return None  # bare repo or unusual layout


def board_dir(discover_children=True):
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

def git(*args):
    import subprocess
    try:
        out = subprocess.run(["git"] + list(args), capture_output=True, text=True, timeout=10)
    except (OSError, subprocess.TimeoutExpired):
        return None
    if out.returncode != 0:
        return None
    return out.stdout.strip()


def git_state():
    """Branch, short sha, dirty-file count, and whether cwd is the main worktree."""
    top = git("rev-parse", "--show-toplevel")
    if not top:
        return None
    branch = git("rev-parse", "--abbrev-ref", "HEAD") or "?"
    sha = git("rev-parse", "--short", "HEAD") or "?"
    dirty = git("status", "--porcelain")
    common = git("rev-parse", "--git-common-dir") or ""
    gitdir = git("rev-parse", "--git-dir") or ""
    is_main_tree = os.path.abspath(os.path.join(top, common)) == os.path.abspath(os.path.join(top, gitdir))
    return {
        "top": top,
        "branch": branch,
        "sha": sha,
        "dirty": len(dirty.splitlines()) if dirty else 0,
        "main_tree": is_main_tree,
    }


def agents_dir(board):
    return os.path.join(board, "agents")


def checkin(board, owner, ticket=None, note=""):
    """Record where this agent is working: cwd, worktree root, branch, sha."""
    g = git_state() or {}
    rec = {
        "owner": owner,
        "cwd": os.getcwd(),
        "worktree": g.get("top", ""),
        "branch": g.get("branch", ""),
        "sha": g.get("sha", ""),
        "dirty": g.get("dirty", 0),
        "ticket": ticket if ticket is not None else _current_ticket(board, owner),
        "note": note,
        "seen": now(),
    }
    os.makedirs(agents_dir(board), exist_ok=True)
    path = os.path.join(agents_dir(board), owner + ".json")
    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        json.dump(rec, f, indent=2)
    os.replace(tmp, path)
    return rec


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
    owner = t.get("owner") or whoami(a.owner)
    t["status"] = "review"
    t["owner"] = owner
    t["review_at"] = now()
    text = a.notes
    if g:
        stamp = "%s@%s" % (g["branch"], g["sha"])
        text = "%s -- %s" % (stamp, text)
        t["commit"] = stamp
        t["branch"] = g["branch"]
    if a.pr:
        t["pr"] = a.pr
        text += " (PR %s)" % a.pr
    t["notes"].append({"by": owner, "at": now(), "text": "REVIEW: " + text})
    save(board, t)
    checkin(board, owner, t["id"], "submitted %s for review" % t["id"])
    m = current_master(board)
    post_message(board, owner, "%s ready for review: %s" % (t["id"], text),
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
    r = subprocess.run(["git", "merge", "--no-edit", "-m", "Sync %s into %s" % (trunk, g["branch"]), trunk],
                       capture_output=True, text=True)
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
    if m["owner"] != owner and not force_master:
        sys.exit("rejected owner: %s is not the designated integrator (%s); "
                 "pass --force-master only for break-glass recovery" % (owner, m["owner"]))
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
    """
    import subprocess
    root = os.path.dirname(board)
    cfg = merge_config(board)
    trunk = _trunk()
    owner = whoami(a.owner)
    require_integrator(board, owner, force_master=getattr(a, "force_master", False))

    def sh(*args, cwd=root):
        return subprocess.run(list(args), cwd=cwd, capture_output=True, text=True)

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
                continue
            got = sh("git", "rev-parse", pin)
            if got.returncode != 0:
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


# ---- usage limits -------------------------------------------------------

LIMIT_PATTERNS = ("usage limit", "rate limit", "rate_limit", "hit your limit", "limit reached",
                  "out of credits", "quota exceeded", "429", "resets at", "try again in")


def _scan_logs(paths, hours):
    """Return [(path, hits, last_mtime)] for files touched within `hours` containing limit text."""
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
        try:
            with open(p, "r", errors="ignore") as f:
                for ln in f:
                    low = ln.lower()
                    if any(k in low for k in LIMIT_PATTERNS) and ("limit" in low or "429" in low or "credit" in low or "quota" in low):
                        hits += 1
        except OSError:
            continue
        if hits:
            out.append((p, hits, st.st_mtime))
    return out


def cmd_limit(a, board):
    """Record (or clear) that an agent hit a usage limit; shown in who/master."""
    owner = a.agent or whoami()
    rec = _agent_rec(board, owner) or checkin(board, owner)
    if a.clear:
        rec.pop("limit", None)
        msg = "%s is back (limit cleared)" % owner
    else:
        rec["limit"] = {"at": now(), "until": a.until or "", "note": a.note or ""}
        msg = "%s hit a usage limit%s%s" % (owner, (" until %s" % a.until) if a.until else "",
                                            (": %s" % a.note) if a.note else "")
    os.makedirs(agents_dir(board), exist_ok=True)
    path = os.path.join(agents_dir(board), owner + ".json")
    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        json.dump(rec, f, indent=2)
    os.replace(tmp, path)
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
    sources = {
        "claude": glob.glob(os.path.join(home, ".claude", "projects", "*", "*.jsonl")),
        "codex": glob.glob(os.path.join(home, ".codex", "sessions", "**", "*.jsonl"), recursive=True)
                 + glob.glob(os.path.join(home, ".codex", "log", "*")),
        "cursor": glob.glob(os.path.join(home, ".cursor", "chats", "**", "*.json*"), recursive=True)
                  + glob.glob(os.path.join(home, ".cursor", "*.log")),
    }
    print("Local tool logs mentioning limits (last %dh):" % a.hours)
    found = False
    for tool, paths in sources.items():
        hits = _scan_logs(paths, a.hours)
        hits.sort(key=lambda x: -x[2])
        for p, n, mt in hits[:5]:
            found = True
            short = p.replace(home, "~")
            print("  %-7s %3d hits  %s ago  %s" % (tool, n, fmt_hours((datetime.now(timezone.utc).timestamp() - mt) / 3600.0), short[-90:]))
    if not found:
        print("  none found (grok/other tools: record manually with `tickets limit`)")


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
        t["commit"] = stamp
    if text:
        t["notes"].append({"by": t.get("owner") or "agent", "at": now(), "text": text})
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
    t["notes"].append({"by": t.get("owner") or "agent", "at": now(), "text": a.reason})
    save(board, t)
    print("%s blocked: %s" % (a.id, a.reason))


def cmd_note(a, board):
    t = load(board, a.id)
    t["notes"].append({"by": a.by or t.get("owner") or "agent", "at": now(), "text": a.text})
    save(board, t)
    who = a.by or t.get("owner")
    if who and t["status"] == "claimed":
        checkin(board, who, t["id"], a.text[:80])
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
    t["notes"].append({"by": whoami(a.by), "at": now(), "text": "assign: " + ", ".join(changed)})
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
    if sub == "take":
        owner = whoami(a.owner)
        prev = current_master(board)
        with open(master_state_path(board), "w") as f:
            json.dump({"owner": owner, "since": now()}, f)
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
    print("%-14s %-8s %-34s %-22s %s" % ("agent", "seen", "branch@sha", "ticket", "worktree"))
    for r in sorted(agents, key=lambda r: r.get("seen", ""), reverse=True):
        tid = r.get("ticket") or ""
        t = tickets.get(tid)
        tdesc = ("%s %s" % (tid, MARK.get(t["status"], "")) if t else (tid or "-"))
        b = "%s@%s" % (r.get("branch") or "?", r.get("sha") or "?")
        if r.get("dirty"):
            b += " +%d" % r["dirty"]
        wt = r.get("worktree") or r.get("cwd") or ""
        home = os.path.expanduser("~")
        if wt.startswith(home):
            wt = "~" + wt[len(home):]
        print("%-14s %-8s %-34s %-22s %s" % (
            r["owner"][:14], fmt_hours(hours_since(r.get("seen"))) + " ago", b[:34], tdesc[:22], wt))
        if r.get("limit"):
            lim = r["limit"]
            print("%-14s !! USAGE LIMIT hit %s ago%s" % ("", fmt_hours(hours_since(lim["at"])),
                                                        (", back %s" % lim["until"]) if lim.get("until") else ""))
        if r.get("note"):
            print("%-14s %s" % ("", "\"%s\"" % r["note"][:90]))
    # collisions
    by_branch = {}
    for r in agents:
        if r.get("branch") and r["branch"] not in ("main", "master"):
            by_branch.setdefault(r["branch"], []).append(r["owner"])
    for b, os_ in by_branch.items():
        if len(set(os_)) > 1:
            print("!! %s share branch %s -- they will clobber each other" % (", ".join(sorted(set(os_))), b))
    on_main = [r["owner"] for r in agents if r.get("branch") in ("main", "master")]
    if on_main:
        print("!! on main/master: %s -- rule 4, move to a worktree" % ", ".join(on_main))


# ---- message board ------------------------------------------------------

def messages_path(board):
    return os.path.join(board, "messages.jsonl")


def post_message(board, sender, text, to="", re=""):
    rec = {"at": now(), "from": sender, "to": to, "re": re, "text": text}
    line_ = json.dumps(rec) + "\n"
    # O_APPEND writes under PIPE_BUF are atomic, so concurrent posters never interleave
    fd = os.open(messages_path(board), os.O_CREAT | os.O_WRONLY | os.O_APPEND, 0o644)
    try:
        os.write(fd, line_.encode())
    finally:
        os.close(fd)
    return rec


def load_messages(board):
    out = []
    try:
        with open(messages_path(board)) as f:
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


def _agent_rec(board, owner):
    path = os.path.join(agents_dir(board), owner + ".json")
    try:
        with open(path) as f:
            return json.load(f)
    except (IOError, ValueError):
        return {}


def _mark_inbox_read(board, owner):
    rec = _agent_rec(board, owner)
    if not rec:
        rec = checkin(board, owner)
    rec["inbox_seen"] = now()
    os.makedirs(agents_dir(board), exist_ok=True)
    path = os.path.join(agents_dir(board), owner + ".json")
    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        json.dump(rec, f, indent=2)
    os.replace(tmp, path)


def unread(board, owner):
    since = _agent_rec(board, owner).get("inbox_seen", "")
    msgs = load_messages(board)
    return [m for m in msgs
            if m.get("from") != owner
            and (not m.get("to") or m.get("to") == owner or m.get("to") == "all")
            and m.get("at", "") > since]


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
    if a.all:
        msgs = load_messages(board)[-a.limit:]
        if not msgs:
            print("no messages yet (tickets msg \"text\" [--to agent] [--re T-001])")
            return
        for m in msgs:
            print(fmt_msg(m))
    else:
        msgs = unread(board, owner)
        if not msgs:
            print("inbox empty for %s (tickets inbox --all for history)" % owner)
        else:
            print("%d unread for %s:" % (len(msgs), owner))
            for m in msgs:
                print("  " + fmt_msg(m))
    if not a.keep:
        _mark_inbox_read(board, owner)


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


def main():
    p = argparse.ArgumentParser(prog="tickets", description=__doc__.split("\n")[0])
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

    c = sub.add_parser("here", help="check in: record my worktree, branch and ticket")
    c.add_argument("--owner", "-o")
    c.add_argument("--note", default="")
    c.set_defaults(fn=cmd_here)

    c = sub.add_parser("who", help="where every agent is working")
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

    c = sub.add_parser("limits", help="who is limited: records, silence, and local tool logs")
    c.add_argument("--hours", type=int, default=24)
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
    c.add_argument("--reason", "-n", required=True)
    c.set_defaults(fn=cmd_block)

    c = sub.add_parser("note", help="add a note to a ticket")
    c.add_argument("id")
    c.add_argument("text")
    c.add_argument("--by", default="")
    c.set_defaults(fn=cmd_note)

    c = sub.add_parser("reopen", help="release a claimed ticket back to open")
    c.add_argument("id")
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

    from ticket_coordination import register
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
