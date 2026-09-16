"""T-1051: one readable timeline for an objective or ticket.

Reads existing board sources. Does not write a new log. A missing transcript
is labelled missing -- never an empty success.
"""
from __future__ import annotations

import glob
import hashlib
import json
import os
import re
import sys

TICKET_RE = re.compile(r"^[Tt]-(\d+)$")
TEST_RE = re.compile(
    r"(?i)\b(pytest|unittest|vitest|npm test|tests?:)\b|\b\d+\s+passed\b"
)

# Stable order when timestamps collide.
KIND_RANK = {
    "objective": 0,
    "seat": 1,
    "claim": 2,
    "run_start": 3,
    "update": 4,
    "test": 5,
    "message": 6,
    "steer": 7,
    "stall": 8,
    "limit": 9,
    "review": 10,
    "accept": 11,
    "reject": 12,
    "handoff": 13,
    "done": 14,
    "run_end": 15,
    "transcript": 16,
    "exit": 17,
}


def _read_json(path, default=None):
    try:
        with open(path) as f:
            return json.load(f)
    except (OSError, ValueError):
        return default


def _read_jsonl(path):
    out = []
    try:
        with open(path) as f:
            for ln in f:
                ln = ln.strip()
                if not ln:
                    continue
                try:
                    rec = json.loads(ln)
                except ValueError:
                    continue
                if isinstance(rec, dict):
                    out.append(rec)
    except OSError:
        pass
    return out


def load_tickets(board):
    out = []
    for path in sorted(glob.glob(os.path.join(board, "T-*.json"))):
        rec = _read_json(path)
        if isinstance(rec, dict) and rec.get("id"):
            out.append(rec)
    return out


def load_objective(board):
    rec = _read_json(os.path.join(board, "objective.json"), {})
    return rec if isinstance(rec, dict) else {}


def load_workforce(board):
    rec = _read_json(os.path.join(board, "workforce.json"), {})
    return rec if isinstance(rec, dict) else {}


def load_messages(board):
    out = _read_jsonl(os.path.join(board, "messages.jsonl"))
    for path in sorted(glob.glob(os.path.join(board, "messages.*.jsonl"))):
        out.extend(_read_jsonl(path))
    return out


def load_trajectories(board):
    paths = sorted(glob.glob(os.path.join(board, "trajectories.*.jsonl")))
    paths.append(os.path.join(board, "trajectories.jsonl"))
    out = []
    for path in paths:
        out.extend(_read_jsonl(path))
    return out


def load_agents(board):
    out = []
    for path in sorted(glob.glob(os.path.join(board, "agents", "*.json"))):
        rec = _read_json(path)
        if isinstance(rec, dict):
            if not rec.get("owner"):
                rec["owner"] = os.path.splitext(os.path.basename(path))[0]
            out.append(rec)
    return out


def objective_id(obj):
    if not isinstance(obj, dict) or not obj.get("text"):
        return ""
    h = hashlib.sha1(
        ("%s|%s" % (obj["text"], obj.get("at", ""))).encode("utf-8", "replace")
    )
    return "obj-" + h.hexdigest()[:8]


def norm_tid(raw):
    m = TICKET_RE.match((raw or "").strip())
    if not m:
        return (raw or "").strip()
    return "T-%03d" % int(m.group(1))


def looks_like_ticket(raw):
    return bool(TICKET_RE.match((raw or "").strip()))


def connected_tickets(tickets, tid):
    """Ticket plus every ancestor and descendant on --after / deps edges."""
    by_id = dict((t["id"], t) for t in tickets)
    kids = {}
    for t in tickets:
        for dep in t.get("deps") or []:
            kids.setdefault(dep, []).append(t["id"])
    seen = set()
    stack = [tid]
    while stack:
        cur = stack.pop()
        if cur in seen:
            continue
        seen.add(cur)
        t = by_id.get(cur)
        if not t:
            continue
        stack.extend(t.get("deps") or [])
        stack.extend(kids.get(cur) or [])
    return [t for t in tickets if t["id"] in seen]


def resolve_scope(tickets, target, objective):
    """Return (selected_tickets, error). Empty target = standing objective."""
    target = (target or "").strip()
    if not target:
        return list(tickets), ""
    tid = norm_tid(target)
    by_id = dict((t["id"], t) for t in tickets)
    if tid in by_id:
        return connected_tickets(tickets, tid), ""
    if looks_like_ticket(target):
        return [], "trace: no ticket %s" % tid
    obj_text = (objective.get("text") or "").strip()
    oid = objective_id(objective)
    if target == oid or (obj_text and target.lower() == obj_text.lower()):
        return list(tickets), ""
    if obj_text and target.lower() in obj_text.lower():
        return list(tickets), ""
    return [], "trace: no ticket or objective matching %r" % target


def current_accept(t):
    for ev in reversed(t.get("review_events") or []):
        if not isinstance(ev, dict):
            continue
        if (ev.get("kind") or "").strip().lower() != "accept":
            continue
        if ev.get("superseded"):
            continue
        return ev
    return None


def pin_sha(t):
    head = (t.get("review_head") or "").strip()
    if head:
        return head
    commit = (t.get("commit") or "").strip()
    if "@" in commit:
        return commit.rsplit("@", 1)[1]
    return commit


def harness_of(workforce, seat):
    entry = (workforce or {}).get(seat) or {}
    if not isinstance(entry, dict):
        return "", ""
    return (entry.get("tool") or entry.get("harness") or "",
            entry.get("model") or "")


def claude_project_dir(cwd, home=""):
    root = home or os.path.expanduser("~")
    key = re.sub(r"[/._]", "-", cwd or "")
    return os.path.join(root, ".claude", "projects", key)


def newest_jsonl(directory):
    newest = ""
    newest_mt = -1
    for path in glob.glob(os.path.join(directory, "*.jsonl")):
        try:
            mt = os.stat(path).st_mtime
        except OSError:
            continue
        if mt >= newest_mt:
            newest, newest_mt = path, mt
    return newest


def transcript_pointer(cwd, harness="", home=""):
    """(path, status). status is present or missing -- never an empty success."""
    if not cwd:
        return "", "missing"
    home = home or os.path.expanduser("~")
    name = (harness or "").strip().lower()
    candidates = []
    if name in ("", "claude", "cursor+claude"):
        found = newest_jsonl(claude_project_dir(cwd, home))
        if found:
            candidates.append(found)
    if name in ("", "codex"):
        needles = ('"cwd":"%s"' % cwd, '"cwd": "%s"' % cwd)
        sessions = os.path.join(home, ".codex", "sessions")
        for path in glob.glob(os.path.join(sessions, "**", "*.jsonl"), recursive=True):
            try:
                with open(path, "rb") as f:
                    head = f.read(8192).decode("utf-8", "replace")
            except OSError:
                continue
            if any(n in head for n in needles):
                candidates.append(path)
                break
    if candidates:
        return candidates[0], "present"
    return "", "missing"


def _event(at, kind, seat="", ticket="", **fields):
    rec = {"at": at or "", "kind": kind, "seat": seat or "", "ticket": ticket or ""}
    for key, value in fields.items():
        if value is None:
            continue
        rec[key] = value
    return rec


def _add(events, rec):
    if rec is not None:
        events.append(rec)


def collect(board, target="", home=""):
    """Ordered events for one objective or ticket. Read-only."""
    tickets = load_tickets(board)
    objective = load_objective(board)
    selected, err = resolve_scope(tickets, target, objective)
    if err:
        return [], err
    wanted = set(t["id"] for t in selected)
    workforce = load_workforce(board)
    messages = [m for m in load_messages(board) if (m.get("re") or "") in wanted]
    traj = [e for e in load_trajectories(board)
            if (e.get("ticket") or "") in wanted or (
                not e.get("ticket") and e.get("objective_id") == objective_id(objective)
            )]
    agents = load_agents(board)
    by_id = dict((t["id"], t) for t in selected)
    events = []

    if objective.get("text") and selected:
        _add(events, _event(
            objective.get("at", ""), "objective",
            seat=objective.get("set_by") or "",
            text=objective.get("text"),
            state=objective.get("state") or ("achieved" if objective.get("done") else "active"),
            objective_id=objective_id(objective) or None,
        ))

    have_run_start = set()
    for e in traj:
        kind = e.get("kind") or ""
        if kind not in ("run_start", "run_end"):
            continue
        seat = e.get("agent") or ""
        ticket = e.get("ticket") or ""
        extra = {}
        for key in ("harness", "model", "effort", "exit", "duration_s", "turns"):
            if key in e:
                extra[key] = e[key]
        if kind == "run_start":
            have_run_start.add((seat, ticket))
            _add(events, _event(e.get("at", ""), "seat", seat, ticket, **extra))
            _add(events, _event(e.get("at", ""), "run_start", seat, ticket, **extra))
        else:
            _add(events, _event(e.get("at", ""), "run_end", seat, ticket, **extra))

    for t in selected:
        tid = t["id"]
        owner = t.get("owner") or ""
        harness, model = harness_of(workforce, owner)
        if t.get("claimed_at") and (owner, tid) not in have_run_start:
            extra = {}
            if harness:
                extra["harness"] = harness
            if model:
                extra["model"] = model
            _add(events, _event(t["claimed_at"], "seat", owner, tid, **extra))
        if t.get("claimed_at"):
            _add(events, _event(t["claimed_at"], "claim", owner, tid))
        if t.get("review_at"):
            _add(events, _event(
                t["review_at"], "review", owner, tid, sha=pin_sha(t) or None,
            ))
        for ev in t.get("review_events") or []:
            if not isinstance(ev, dict):
                continue
            kind = (ev.get("kind") or "").strip().lower()
            if kind not in ("accept", "reject"):
                continue
            extra = {"sha": ev.get("sha") or pin_sha(t) or None}
            if ev.get("superseded"):
                extra["superseded"] = True
            if kind == "accept" and ev.get("notes"):
                extra["notes"] = ev["notes"]
            if kind == "reject" and (ev.get("reason") or ev.get("notes")):
                extra["reason"] = ev.get("reason") or ev.get("notes")
            _add(events, _event(ev.get("at", ""), kind, ev.get("by") or "", tid, **extra))
        if t.get("done_at"):
            _add(events, _event(
                t["done_at"], "done", owner, tid,
                sha=pin_sha(t) or None, status=t.get("status") or "done",
            ))
        structured_steers = [
            rec for rec in (t.get("steers") or []) if isinstance(rec, dict)
        ]
        for n in t.get("notes") or []:
            if not isinstance(n, dict):
                continue
            text = str(n.get("text") or "")
            at = n.get("at") or ""
            by = n.get("by") or owner
            if ((n.get("kind") or "") == "steer" or text.startswith("STEER ")):
                if structured_steers:
                    continue
                _add(events, _event(at, "steer", by, tid, text=text[:240]))
                continue
            if TEST_RE.search(text):
                _add(events, _event(at, "test", by, tid, text=text[:240]))
            elif text.startswith("REVIEW: "):
                continue
            elif text:
                _add(events, _event(at, "update", by, tid, text=text[:240]))
        for rec in structured_steers:
            if not isinstance(rec, dict):
                continue
            extra = {
                "steer_id": rec.get("id") or None,
                "receipt": rec.get("receipt") or None,
                "delivered": rec.get("delivered"),
                "text": (rec.get("text") or "")[:240] or None,
            }
            _add(events, _event(
                rec.get("at", ""), "steer", rec.get("from") or "", tid,
                to=rec.get("to") or None, **extra,
            ))
        for dep_id in t.get("deps") or []:
            pred = by_id.get(dep_id)
            if not pred:
                continue
            acc = current_accept(pred)
            if not acc:
                continue
            when = t.get("claimed_at") or t.get("created") or acc.get("at") or ""
            _add(events, _event(
                when, "handoff", t.get("owner") or "", tid,
                from_ticket=dep_id,
                sha=acc.get("sha") or pin_sha(pred) or None,
                notes=acc.get("notes") or None,
                by=acc.get("by") or None,
            ))

    for m in messages:
        _add(events, _event(
            m.get("at", ""), "message", m.get("from") or "", m.get("re") or "",
            to=m.get("to") or None, text=(m.get("text") or "")[:240] or None,
        ))

    for rec in agents:
        seat = rec.get("owner") or ""
        tid = rec.get("ticket") or ""
        if tid and tid not in wanted:
            continue
        if not tid and wanted and not any(
            t.get("owner") == seat for t in selected
        ):
            continue
        if not tid:
            for t in selected:
                if t.get("owner") == seat:
                    tid = t["id"]
                    break
        stall = rec.get("stall") or {}
        if isinstance(stall, dict) and (stall.get("measured_s") is not None
                                        or stall.get("last_output_at")):
            _add(events, _event(
                stall.get("at") or stall.get("last_output_at") or "",
                "stall", seat, tid,
                measured_s=stall.get("measured_s"),
                last_output_at=stall.get("last_output_at") or None,
                source=stall.get("source") or None,
            ))
        limit = rec.get("limit") or {}
        if isinstance(limit, dict) and (limit.get("at") or limit.get("note")
                                        or limit.get("source")):
            _add(events, _event(
                limit.get("at", ""), "limit", seat, tid,
                source=limit.get("source") or None,
                until=limit.get("until") or None,
                reset_at=limit.get("reset_at") or None,
                note=(limit.get("note") or "")[:240] or None,
            ))

    seats = {}
    for t in selected:
        if t.get("owner"):
            seats.setdefault(t["owner"], t["id"])
    for rec in agents:
        if rec.get("owner"):
            seats.setdefault(rec["owner"], rec.get("ticket") or "")
    for seat, tid in seats.items():
        if tid and tid not in wanted:
            continue
        rec = next((a for a in agents if a.get("owner") == seat), {})
        cwd = rec.get("cwd") or rec.get("worktree") or ""
        harness, _model = harness_of(workforce, seat)
        if not harness:
            harness = rec.get("harness") or rec.get("tool") or ""
        path, status = transcript_pointer(cwd, harness, home=home)
        watch = os.path.join(board, "agents", seat + ".watch.log")
        watch_ptr = watch if os.path.isfile(watch) else ""
        at = rec.get("updated") or rec.get("at") or ""
        extra = {"status": status, "cwd": cwd or None}
        if path:
            extra["path"] = path
        if watch_ptr:
            extra["watch_log"] = watch_ptr
        _add(events, _event(at, "transcript", seat, tid, **extra))

    if objective.get("done_at") or objective.get("state") in (
            "achieved", "blocked", "replaced"):
        _add(events, _event(
            objective.get("done_at") or objective.get("at") or "",
            "exit",
            seat=objective.get("set_by") or "",
            state=objective.get("state") or ("achieved" if objective.get("done") else ""),
            evidence=objective.get("evidence") or None,
        ))

    events.sort(key=lambda e: (
        e.get("at") or "",
        KIND_RANK.get(e.get("kind"), 50),
        e.get("ticket") or "",
        e.get("seat") or "",
        e.get("kind") or "",
    ))
    return events, ""


def render_text(events):
    lines = []
    for e in events:
        bits = [
            e.get("at") or "-",
            (e.get("seat") or "-"),
            (e.get("ticket") or "-"),
            (e.get("kind") or "?"),
        ]
        extras = []
        for key in (
            "status", "sha", "harness", "model", "measured_s",
            "last_output_at", "from_ticket", "notes", "reason", "receipt",
            "delivered", "path", "watch_log", "source", "until", "reset_at",
            "exit", "state", "evidence", "to", "steer_id", "text", "cwd",
        ):
            if key not in e:
                continue
            value = e[key]
            if value is True:
                extras.append("%s=true" % key)
            elif value is False:
                extras.append("%s=false" % key)
            else:
                extras.append("%s=%s" % (key, value))
        line = "  ".join(bits)
        if extras:
            line += "  " + " ".join(extras)
        lines.append(line)
    return lines


def cmd_trace(a, board):
    target = (getattr(a, "target", "") or "").strip()
    home = os.environ.get("HOME") or ""
    events, err = collect(board, target, home=home)
    if err:
        sys.exit(err)
    if getattr(a, "json", False):
        print(json.dumps(events, indent=2))
        return
    if not events:
        if target:
            print("no events for %s" % target)
        else:
            print("no events on this board (set an objective or pass a ticket id)")
        return
    for line in render_text(events):
        print(line)
