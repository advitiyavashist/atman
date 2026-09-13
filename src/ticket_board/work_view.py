"""T-889: the Work view -- objective above a real dependency graph, node detail.

Pure functions over ticket and message dicts, plus the CSS/HTML/JS that the
shell splices in through three named placeholders (``tickets.py`` ``_ui_page``).
No board I/O lives here: ``tickets.py`` loads tickets, the T-791 graph payload
and the message log and hands them in, so there is exactly one source for what
the Work view says about a node.

Evidence states (``phase``) are deliberately distinct:

* ``ready``       unblocked, nobody has been told; ``tickets next`` claims it
* ``dispatched``  a task DM about the ticket reached a seat (T-781 success
                  trigger, ``tickets assign``, a reservation) but nobody claimed
* ``working``     claimed; the evidence is the claim and the last update age
* ``waiting``     open with unfinished ``--after`` deps
* ``capture``     open but not sounded (``tickets sound`` promotes it)
* ``hold``        open but parked (``tickets hold``)
* ``blocked`` / ``review`` / ``done`` / ``discarded`` follow the ticket status
"""
from __future__ import annotations

from datetime import datetime, timezone

from .sounding import ticket_lane, ticket_on_hold

PHASES = ("working", "review", "blocked", "dispatched", "ready", "waiting",
          "capture", "hold", "done", "discarded")
STALE_H = 1.5      # same amber/red thresholds the shell's cards use
WARM_H = 0.75
HANDOFF_CHARS = 320
MSG_WINDOW = 2000  # newest task messages considered for dispatch evidence


def _hours_since(stamp, now=None):
    try:
        d = datetime.strptime(stamp, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
    except (ValueError, TypeError):
        return None
    now = now or datetime.now(timezone.utc)
    return (now - d).total_seconds() / 3600.0


def _last_note(t):
    notes = t.get("notes") or []
    return notes[-1] if notes else {}


def _note_view(note, limit=HANDOFF_CHARS):
    text = (note.get("text") or "").strip()
    return {"by": note.get("by") or "", "at": note.get("at") or "",
            "text": text[:limit], "truncated": len(text) > limit}


def verdict_of(t):
    """Accepted / awaiting review / the reviewer's last verdict note / ''."""
    st = t.get("status")
    if st == "done":
        return "accepted"
    if st == "review":
        return "awaiting review"
    for n in reversed(t.get("notes") or []):
        text = (n.get("text") or "").strip()
        low = text.lower()
        if low.startswith(("review:", "verdict:")) or "request fix" in low:
            return text[:200]
    return ""


def _task_messages_by_ticket(messages):
    out = {}
    for m in (messages or [])[-MSG_WINDOW:]:
        if (m.get("kind") or "") != "task":
            continue
        re_ = (m.get("re") or "").strip()
        if re_:
            out.setdefault(re_, []).append(m)
    return out


def _dispatch_of(tid, task_msgs, acked, now):
    """Newest task DM about this ticket addressed to a seat, with ack evidence."""
    for m in reversed(task_msgs.get(tid) or []):
        to = (m.get("to") or "").strip()
        if not to or to.lower() in ("all", "everyone"):
            continue
        seen = None
        if acked is not None:
            try:
                seen = bool(acked(to, m))
            except Exception:  # noqa: BLE001 - evidence stays honest as unknown
                seen = None
        return {"to": to, "from": m.get("from") or "", "at": m.get("at") or "",
                "age_h": _hours_since(m.get("at") or "", now), "msg_id": m.get("id") or "",
                "seen": seen, "text": (m.get("text") or "").strip()[:160]}
    return None


def phase_of(t, waiting, dispatch):
    st = t.get("status")
    if st == "done":
        return "done"
    if st == "review":
        return "review"
    if st == "claimed":
        return "working"
    if st == "blocked":
        return "blocked"
    lane = ticket_lane(t)
    if lane == "capture":
        return "capture"
    if lane == "discarded":
        return "discarded"
    if ticket_on_hold(t):
        return "hold"
    if waiting:
        return "waiting"
    if dispatch or (t.get("reserved_for") or "").strip():
        return "dispatched"
    return "ready"


def _fmt_age(h):
    if h is None:
        return "unknown time"
    if h < 1.0 / 60:
        return "just now"
    if h < 1:
        return "%dm ago" % round(h * 60)
    if h < 48:
        return "%.1fh ago" % h if h < 10 else "%dh ago" % round(h)
    return "%dd ago" % round(h / 24)


def wait_of(t, phase, waiting):
    tid = t.get("id", "")
    if phase == "waiting":
        return {"kind": "deps", "on": list(waiting),
                "text": "waits on " + ", ".join(waiting),
                "cmd": "tickets show %s" % waiting[0]}
    if phase == "capture":
        return {"kind": "capture", "on": [],
                "text": "waits in capture: run sound",
                "cmd": "tickets sound %s" % tid}
    if phase == "hold":
        reason = (t.get("hold_reason") or "").strip()
        return {"kind": "hold", "on": [],
                "text": "HOLD" + (": " + reason if reason else ""),
                "cmd": "tickets hold %s --clear" % tid}
    if phase == "blocked":
        reason = (_last_note(t).get("text") or "").strip()
        return {"kind": "blocked", "on": [],
                "text": "blocked" + (": " + reason[:160] if reason else ""),
                "cmd": "tickets reopen %s" % tid}
    return {"kind": "", "on": [], "text": "", "cmd": ""}


def evidence_of(t, phase, dispatch, freed_by, now):
    owner = (t.get("owner") or "").strip()
    reserved = (t.get("reserved_for") or "").strip()
    if phase == "working":
        claimed = _hours_since(t.get("claimed_at") or "", now)
        stamps = [n.get("at") for n in (t.get("notes") or []) if n.get("at")]
        last = max(stamps) if stamps else (t.get("claimed_at") or "")
        upd = _hours_since(last, now)
        text = "claimed by @%s %s" % (owner or "?", _fmt_age(claimed))
        text += " · last update %s" % _fmt_age(upd)
        if upd is not None and upd > STALE_H:
            text += " · silent >%sh" % ("%g" % STALE_H)
        return text
    if phase == "review":
        bits = ["submitted by @%s" % (owner or "?")]
        if t.get("commit"):
            bits.append(t["commit"])
        if t.get("pr"):
            bits.append("PR %s" % t["pr"])
        return " · ".join(bits)
    if phase == "done":
        return "done %s by @%s" % (_fmt_age(_hours_since(t.get("done_at") or "", now)), owner or "?")
    if phase == "blocked":
        reason = (_last_note(t).get("text") or "").strip()
        return "blocked by @%s" % (_last_note(t).get("by") or owner or "?") + (": " + reason[:160] if reason else "")
    if phase == "dispatched":
        if dispatch:
            seen = dispatch.get("seen")
            seen_txt = "seen" if seen else ("not yet seen" if seen is False else "delivery unknown")
            text = "task posted to @%s by %s %s · %s · not claimed" % (
                dispatch["to"], dispatch.get("from") or "?", _fmt_age(dispatch.get("age_h")), seen_txt)
            if reserved and reserved != dispatch["to"]:
                text += " · reserved for @%s" % reserved
            return text
        return "reserved for @%s · no task posted yet · not claimed" % reserved
    if phase == "ready":
        text = "unblocked · nobody dispatched · tickets next claims it"
        if freed_by:
            text = "freed when %s finished %s · nobody dispatched · tickets next claims it" % (
                freed_by["parent"], _fmt_age(freed_by.get("age_h")))
        return text
    if phase == "waiting":
        return "not claimable until its --after deps finish"
    if phase == "capture":
        return "captured, not sounded · invisible to tickets next until tickets sound"
    if phase == "hold":
        return "parked · tickets next skips it"
    if phase == "discarded":
        return "discarded · stays on the graph for history"
    return ""


def _started_by(t, by_id, done_ids, task_msgs, acked, now):
    """Which finished parent freed this ticket, whether anyone was told, and
    whether it began -- the completed-parent story from the child's side."""
    done_deps = [d for d in (t.get("deps") or []) if d in done_ids and d in by_id]
    if not done_deps:
        return None
    parent = max(done_deps, key=lambda d: by_id[d].get("done_at") or "")
    pt = by_id[parent]
    at = pt.get("done_at") or ""
    trigger = None
    for m in reversed(task_msgs.get(t.get("id", "")) or []):
        text = (m.get("text") or "")
        if "success trigger" in text or ("unblocked" in text and parent in text):
            to = (m.get("to") or "").strip()
            seen = None
            if to and acked is not None:
                try:
                    seen = bool(acked(to, m))
                except Exception:  # noqa: BLE001
                    seen = None
            trigger = {"to": to, "from": m.get("from") or "", "at": m.get("at") or "",
                       "age_h": _hours_since(m.get("at") or "", now), "seen": seen}
            break
    began_at = t.get("claimed_at") or ""
    return {"parent": parent, "parent_title": pt.get("title") or "", "at": at,
            "age_h": _hours_since(at, now), "trigger": trigger,
            "began": bool(began_at), "began_at": began_at,
            "began_age_h": _hours_since(began_at, now) if began_at else None,
            "began_by": (t.get("owner") or "").strip()}


def _starts_of(t, kids, by_id, done_ids, phases, dispatches):
    """For a finished parent: each child it freed, and whether that child began."""
    out = []
    tid = t.get("id", "")
    for cid in kids.get(tid, []):
        child = by_id.get(cid)
        if not child:
            continue
        leftover = [d for d in (child.get("deps") or []) if d not in done_ids and d != tid]
        if leftover:
            out.append({"id": cid, "phase": phases.get(cid, ""), "freed": False,
                        "still_waiting_on": leftover, "began": False, "who": ""})
            continue
        ph = phases.get(cid, "")
        disp = dispatches.get(cid)
        who = (child.get("owner") or "").strip() or (disp["to"] if disp else "") \
            or (child.get("reserved_for") or "").strip()
        out.append({"id": cid, "phase": ph, "freed": True, "still_waiting_on": [],
                    "began": bool(child.get("claimed_at")), "who": who,
                    "began_at": child.get("claimed_at") or ""})
    return out


def _depths(node_ids, deps_of):
    memo = {}

    def depth(tid, trail=()):
        if tid in memo:
            return memo[tid]
        if tid in trail:  # cycle guard: check_graph forbids cycles, stay safe anyway
            return 0
        ds = [d for d in deps_of.get(tid, []) if d in node_ids]
        val = 0 if not ds else 1 + max(depth(d, trail + (tid,)) for d in ds)
        memo[tid] = val
        return val

    for tid in node_ids:
        depth(tid)
    return memo


def empty_state(tickets, nodes, edges):
    if not tickets:
        return {"kind": "no_tickets",
                "lead": "No work yet.",
                "detail": "Plan the first tasks as a real dependency graph; edges are --after links, not prose.",
                "cmd": "tickets plan '{\"tasks\":[{\"key\":\"a\",\"title\":\"First task\"},{\"key\":\"b\",\"title\":\"Second task\",\"deps\":[\"a\"]}]}'"}
    if nodes and not edges:
        ids = [n["id"] for n in nodes]
        a, b = (ids + ["T-001", "T-002"])[:2] if len(ids) < 2 else ids[:2]
        return {"kind": "no_edges",
                "lead": "Tickets exist, but nothing waits on anything.",
                "detail": "Add the order the work must happen in so success can start the next ticket.",
                "cmd": "tickets dep %s --after %s" % (b, a)}
    if not nodes:
        return {"kind": "all_done",
                "lead": "Everything on the board is done.",
                "detail": "Plan the next slice against the objective.",
                "cmd": "tickets plan"}
    return None


def work_payload(tickets, graph, messages, objective=None, acked=None, now=None):
    """Everything the Work view renders, as plain data.

    ``graph`` is the T-791 ``workflow_graph`` payload (nodes, edges, forest);
    ``messages`` the raw message log (newest last); ``acked(agent, msg)`` the
    shell's inbox-seen check for delivery evidence (None -> unknown).
    """
    graph = graph or {}
    now = now or datetime.now(timezone.utc)
    by_id = dict((t["id"], t) for t in tickets)
    done_ids = set(t["id"] for t in tickets if t.get("status") == "done")
    kids = {}
    for t in tickets:
        for d in t.get("deps") or []:
            kids.setdefault(d, []).append(t["id"])
    task_msgs = _task_messages_by_ticket(messages)
    keep = [n["id"] for n in graph.get("nodes") or [] if n["id"] in by_id]
    keep_set = set(keep)

    dispatches, phases, waits = {}, {}, {}
    for tid in keep:
        t = by_id[tid]
        waiting = [d for d in (t.get("deps") or []) if d not in done_ids]
        waits[tid] = waiting
        disp = _dispatch_of(tid, task_msgs, acked, now) if t.get("status") == "open" else None
        dispatches[tid] = disp
        phases[tid] = phase_of(t, waiting, disp)

    deps_of = dict((tid, list(by_id[tid].get("deps") or [])) for tid in keep)
    depths = _depths(keep_set, deps_of)

    nodes = []
    for tid in keep:
        t = by_id[tid]
        ph = phases[tid]
        waiting = waits[tid]
        disp = dispatches[tid]
        started = _started_by(t, by_id, done_ids, task_msgs, acked, now)
        freed_by = started if (started and ph == "ready") else None
        handoff = []
        for d in t.get("deps") or []:
            dep = by_id.get(d)
            if not dep or d not in done_ids:
                continue
            note = _last_note(dep)
            if note:
                row = _note_view(note)
                row["from"] = d
                row["from_title"] = dep.get("title") or ""
                handoff.append(row)
        last = _last_note(t)
        since_update = None
        if t.get("status") == "claimed":
            stamps = [n.get("at") for n in (t.get("notes") or []) if n.get("at")]
            since_update = _hours_since(max(stamps) if stamps else (t.get("claimed_at") or ""), now)
        qs = t.get("open_questions") or []
        if not isinstance(qs, list):
            qs = [str(qs)]
        nodes.append({
            "id": tid,
            "title": t.get("title") or "",
            "status": t.get("status") or "",
            "phase": ph,
            "evidence": evidence_of(t, ph, disp, freed_by, now),
            "owner": (t.get("owner") or "").strip(),
            "reserved_for": (t.get("reserved_for") or "").strip(),
            "suggested": (t.get("suggested") or "").strip(),
            "role": t.get("role") or "",
            "priority": t.get("priority", 2),
            "epic": t.get("epic") or "",
            "sprint": t.get("sprint") or "",
            "lane": ticket_lane(t),
            "hold": bool(ticket_on_hold(t)),
            "deps": list(t.get("deps") or []),
            "waiting": waiting,
            "children": [c for c in kids.get(tid, []) if c in keep_set],
            "depth": depths.get(tid, 0),
            "wait": wait_of(t, ph, waiting),
            "dispatch": disp,
            "started_by": started,
            "starts": _starts_of(t, kids, by_id, done_ids, phases, dispatches) if ph == "done" else [],
            "acceptance": {
                "proof": (t.get("proof") or "").strip(),
                "cause": (t.get("cause") or "").strip(),
                "change": (t.get("change") or "").strip(),
                "open_questions": [str(q) for q in qs],
                "sounded_at": t.get("sounded_at") or "",
                "sounded_by": t.get("sounded_by") or "",
            },
            "handoff": handoff,
            "last_note": _note_view(last) if last else None,
            "artifact": {"commit": t.get("commit") or "", "branch": t.get("branch") or "",
                         "pr": str(t.get("pr") or "")},
            "verdict": verdict_of(t),
            "since_update_h": since_update,
            "stale": bool(since_update is not None and since_update > STALE_H),
            "claimed_at": t.get("claimed_at") or "",
            "done_at": t.get("done_at") or "",
            "created": t.get("created") or "",
        })

    node_by = dict((n["id"], n) for n in nodes)
    edges = [e for e in (graph.get("edges") or []) if e.get("from") in node_by and e.get("to") in node_by]
    order = sorted(keep, key=lambda i: (depths.get(i, 0), i))
    layers = []
    for tid in order:
        d = depths.get(tid, 0)
        while len(layers) <= d:
            layers.append([])
        layers[d].append(tid)
    counts = dict((p, 0) for p in PHASES)
    for n in nodes:
        counts[n["phase"]] = counts.get(n["phase"], 0) + 1

    def _pick(ph):
        return [n for n in nodes if n["phase"] == ph]

    working = sorted(_pick("working"), key=lambda n: (-(n["since_update_h"] or 0), n["id"]))
    candidates = sorted(_pick("dispatched") + _pick("ready"), key=lambda n: (n["priority"], n["id"]))
    nxt = None
    if candidates:
        c = candidates[0]
        who = c["owner"] or (c["dispatch"]["to"] if c["dispatch"] else "") or c["reserved_for"] or c["suggested"]
        nxt = {"id": c["id"], "title": c["title"], "phase": c["phase"], "who": who}
    obj = objective or {}
    return {
        "objective": {
            "text": (obj.get("text") or "").strip(),
            "state": obj.get("state") or "",
            "exit_criterion": (obj.get("exit_criterion") or "").strip(),
            "exit_missing": bool(obj.get("exit_missing")),
        },
        "summary": {
            "finishing": [{"id": n["id"], "owner": n["owner"], "stale": n["stale"]} for n in working],
            "blocked": [{"id": n["id"], "text": n["wait"]["text"]} for n in _pick("blocked")],
            "waiting": counts.get("waiting", 0),
            "ready": [n["id"] for n in _pick("ready")],
            "dispatched": [n["id"] for n in _pick("dispatched")],
            "next": nxt,
        },
        "counts": counts,
        "nodes": nodes,
        "edges": edges,
        "layers": layers,
        "order": order,
        "roots": [r for r in (graph.get("roots") or []) if r in node_by],
        "empty": empty_state(tickets, nodes, edges),
    }


# --------------------------------------------------------------------------
# Presentation. Spliced into UI_HTML by tickets.py `_ui_page` at three named
# placeholders. Tokens (--bg, --fg, --acc, ...) come from the shell.

WORK_CSS = r"""
/* T-889 Work view */
.wv{display:flex;flex-direction:column;gap:12px}
.wv-objective{display:grid;grid-template-columns:minmax(0,1fr) auto;gap:6px 16px;align-items:start;padding:10px 12px;border:1px solid var(--line);border-radius:10px;background:var(--surface)}
.wv-objective .k{font-size:11px;letter-spacing:.08em;text-transform:uppercase;color:var(--mute);font-weight:650}
.wv-objective .v{font-size:15px;font-weight:650;margin-top:2px}
.wv-objective .exit{grid-column:1/-1;font-size:13px;color:var(--fg)}
.wv-objective .exit b{color:var(--mute);font-weight:650}
.wv-objective .exit.missing{color:var(--warn)}
.wv-objective .state{font:12px/1.3 ui-monospace,Menlo,monospace;color:var(--mute);padding:3px 8px;border:1px solid var(--line);border-radius:999px;white-space:nowrap}
.wv-bar{display:flex;flex-wrap:wrap;gap:8px 14px;align-items:center;font-size:12px;color:var(--mute)}
.wv-bar .legend{display:flex;flex-wrap:wrap;gap:4px 10px}
.wv-bar .legend span::before{content:"";display:inline-block;width:8px;height:8px;border-radius:50%;margin-right:5px;background:var(--wv-c,var(--mute));vertical-align:0}
.wv-modes{display:inline-flex;gap:4px;margin-left:auto}
.wv-modes button{appearance:none;background:transparent;border:1px solid var(--line);color:var(--mute);padding:3px 9px;font:12px/1.2 inherit;font-weight:650;border-radius:6px;cursor:pointer}
.wv-modes button[aria-pressed=true]{color:var(--fg);border-color:var(--acc);background:var(--chip)}
.wv-modes button:focus-visible,.wv-node:focus-visible,.wv-cmd-btn:focus-visible{outline:2px solid var(--acc);outline-offset:2px}
.wv-body{display:grid;grid-template-columns:minmax(0,1fr);gap:12px;align-items:start}
.wv-body.has-detail{grid-template-columns:minmax(0,1fr) minmax(280px,360px)}
@media(max-width:900px){.wv-body.has-detail{grid-template-columns:minmax(0,1fr)}}
.wv-canvas{position:relative;overflow:auto;max-width:100%;border:1px solid var(--line);border-radius:10px;background:var(--surface)}
.wv-inner{position:relative;min-width:max-content;padding:14px}
.wv-layers{display:flex;gap:34px;align-items:flex-start}
.wv-layer{display:flex;flex-direction:column;gap:10px;min-width:222px}
.wv-layer-h{font-size:10px;letter-spacing:.1em;text-transform:uppercase;color:var(--mute);font-weight:650;padding:0 2px}
.wv-edges{position:absolute;inset:0;width:100%;height:100%;pointer-events:none;overflow:visible}
.wv-edges path{fill:none;stroke:var(--mute);stroke-opacity:.6;stroke-width:1.5}
.wv-edges path.waiting{stroke:var(--warn);stroke-opacity:.9;stroke-dasharray:4 3}
.wv-edges path.sel{stroke:var(--acc);stroke-opacity:1;stroke-width:2}
.wv-node{position:relative;z-index:1;appearance:none;text-align:left;width:222px;padding:8px 10px 8px 12px;border:1px solid var(--line);border-left:3px solid var(--wv-c,var(--mute));border-radius:8px;background:var(--card);color:var(--fg);font:inherit;cursor:pointer;display:flex;flex-direction:column;gap:3px}
.wv-node[aria-pressed=true]{border-color:var(--acc);border-left-color:var(--wv-c,var(--acc));box-shadow:0 0 0 1px var(--acc) inset}
.wv-node .top{display:flex;gap:8px;align-items:baseline;justify-content:space-between}
.wv-node .id{font:600 12px/1.3 ui-monospace,Menlo,monospace;color:var(--mute)}
.wv-node .ph{font-size:10px;letter-spacing:.06em;text-transform:uppercase;font-weight:700;color:var(--wv-c,var(--mute))}
.wv-node .t{font-weight:600;font-size:13px;line-height:1.3;display:-webkit-box;-webkit-line-clamp:2;-webkit-box-orient:vertical;overflow:hidden}
.wv-node .who,.wv-node .w{font-size:11.5px;color:var(--mute);line-height:1.3}
.wv-node .w{display:-webkit-box;-webkit-line-clamp:2;-webkit-box-orient:vertical;overflow:hidden}
.wv-list .wv-node .w{display:inline;-webkit-line-clamp:unset}
.wv-node .w.warn{color:var(--warn)}
.wv-node .who .stale{color:var(--bad)}
.wv-node.ph-done{opacity:.72}
.wv-node.ph-discarded{opacity:.5;text-decoration:line-through}
.ph-working{--wv-c:var(--flight)}.ph-review{--wv-c:var(--review)}.ph-blocked{--wv-c:var(--blocked)}
.ph-dispatched{--wv-c:var(--acc)}.ph-ready{--wv-c:var(--ok)}.ph-waiting{--wv-c:var(--mute)}
.ph-capture,.ph-hold{--wv-c:var(--warn)}.ph-done{--wv-c:var(--progress)}.ph-discarded{--wv-c:var(--mute)}
.wv-list{list-style:none;margin:0;padding:0;display:flex;flex-direction:column;gap:6px}
.wv-list .wv-node{width:100%;flex-direction:row;flex-wrap:wrap;align-items:baseline;gap:6px 12px}
.wv-list .wv-node .t{display:block;-webkit-line-clamp:unset;flex:1 1 240px}
.wv-list .wv-node .top{gap:8px}
.wv-detail{border:1px solid var(--line);border-radius:10px;background:var(--card);padding:12px 14px;font-size:13px;position:sticky;top:64px}
.wv-detail h3{margin:0 0 2px;font-size:15px;line-height:1.3}
.wv-detail .hd{display:flex;gap:8px;align-items:baseline;flex-wrap:wrap;margin-bottom:8px}
.wv-detail .hd .id{font:600 12px/1.3 ui-monospace,Menlo,monospace;color:var(--mute)}
.wv-detail .pill{font-size:10px;letter-spacing:.06em;text-transform:uppercase;font-weight:700;color:var(--wv-c,var(--mute));border:1px solid var(--wv-c,var(--line));border-radius:999px;padding:2px 7px}
.wv-detail dl{margin:0;display:grid;grid-template-columns:96px minmax(0,1fr);gap:6px 10px}
.wv-detail dt{color:var(--mute);font-size:11px;letter-spacing:.06em;text-transform:uppercase;font-weight:650;padding-top:2px}
.wv-detail dd{margin:0;overflow-wrap:anywhere}
.wv-detail dd.mute{color:var(--mute)}
.wv-detail dd.warn{color:var(--warn)}
.wv-detail dd.bad{color:var(--bad)}
.wv-detail ul{margin:0;padding-left:16px}
.wv-detail .close{float:right;appearance:none;background:transparent;border:1px solid var(--line);color:var(--mute);border-radius:6px;padding:2px 8px;font:12px/1.2 inherit;cursor:pointer}
.wv-cmd{font:12px/1.4 ui-monospace,Menlo,monospace;background:var(--chip);border:1px solid var(--line);border-radius:5px;padding:1px 5px;color:var(--fg);white-space:nowrap}
.wv-cmds{display:flex;flex-wrap:wrap;gap:6px;margin-top:10px}
.wv-handoff li{margin-bottom:4px}
.wv-handoff .from{font:600 12px/1.3 ui-monospace,Menlo,monospace}
.wv-handoff .by{color:var(--mute);font-size:11.5px}
.wv-empty{padding:22px 18px;border:1px dashed var(--line);border-radius:10px;background:var(--surface)}
.wv-empty .lead{font-size:16px;font-weight:650;margin:0 0 4px}
.wv-empty p{margin:0 0 8px;color:var(--mute);font-size:13px}
.wv-empty .wv-cmd{white-space:pre-wrap}
.wv-sr{position:absolute;width:1px;height:1px;overflow:hidden;clip:rect(0 0 0 0);white-space:nowrap}
@media(prefers-reduced-motion:reduce){.wv *{transition:none!important;animation:none!important;scroll-behavior:auto!important}}
"""

WORK_HTML = r"""<div class="wv" id="workView"><p class="graph-empty" id="workViewLoading">Loading work…</p></div>"""

WORK_JS = r"""
window.AtmanWork=(function(){
  const LS_MODE='tickets-ui-work-mode',LS_SEL='tickets-ui-work-selected';
  let MODE='graph',SEL='',W=null,HOST=null,SIG='';
  try{MODE=localStorage.getItem(LS_MODE)==='list'?'list':'graph';SEL=localStorage.getItem(LS_SEL)||''}catch(e){}
  // deep links: /?work=T-012 opens that node; /?mode=list opens the list fallback
  try{const q=new URLSearchParams(location.search);if(q.get('mode'))MODE=q.get('mode')==='list'?'list':'graph';if(/^T-\d+$/.test(q.get('work')||''))SEL=q.get('work')}catch(e){}
  const esc=s=>String(s==null?'':s).replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  const PH={working:'Working',review:'In review',blocked:'Blocked',dispatched:'Dispatched',ready:'Ready',waiting:'Waiting',capture:'Capture',hold:'Hold',done:'Done',discarded:'Discarded'};
  const LEGEND=['ready','dispatched','working','review','blocked','waiting','capture','hold','done'];
  function ago(h){if(h==null)return '—';if(h<1/60)return 'just now';if(h<1)return Math.round(h*60)+'m ago';if(h<48)return (h<10?h.toFixed(1):Math.round(h))+'h ago';return Math.round(h/24)+'d ago'}
  function cmd(s){return '<code class="wv-cmd">'+esc(s)+'</code>'}
  function who(n){
    if(n.phase==='working'||n.phase==='review'||n.phase==='done')return n.owner?'@'+n.owner:'unowned';
    if(n.phase==='dispatched')return n.dispatch?'told @'+n.dispatch.to+(n.dispatch.seen===false?' · unseen':n.dispatch.seen?' · seen':''):(n.reserved_for?'reserved @'+n.reserved_for:'');
    if(n.phase==='blocked')return n.owner?'@'+n.owner:'';
    return n.suggested?'suggested @'+n.suggested:'';
  }
  function objective(o){
    // T-810 shell may carry its own standing-objective strip above the graph; defer to it.
    if(document.getElementById('workObjective'))return '';
    const has=o&&o.text;
    const ex=has?(o.exit_criterion?'<div class="exit"><b>Done when</b> '+esc(o.exit_criterion)+'</div>':'<div class="exit missing"><b>Done when</b> no exit criterion yet — '+cmd('tickets objective --set "…" --exit "…"')+'</div>'):'';
    return '<section class="wv-objective" aria-label="Objective"><div><div class="k">Objective</div><div class="v">'+(has?esc(o.text):'No standing objective yet.')+'</div>'+(has?'':'<div class="exit mute">'+cmd('tickets objective --set "what we are finishing"')+'</div>')+'</div>'+(has&&o.state?'<span class="state">'+esc(o.state)+'</span>':'')+ex+'</section>';
  }
  function nodeBtn(n){
    const w=n.wait&&n.wait.text?'<span class="w'+(n.wait.kind==='deps'?'':' warn')+'">'+esc(n.wait.text)+'</span>':'';
    const stale=n.stale?' <span class="stale">silent '+esc(ago(n.since_update_h))+'</span>':'';
    const wh=who(n);
    return '<button type="button" class="wv-node ph-'+esc(n.phase)+'" data-id="'+esc(n.id)+'" aria-pressed="'+(SEL===n.id?'true':'false')+'" aria-label="'+esc(n.id+' '+n.title+', '+(PH[n.phase]||n.phase)+(wh?', '+wh:'')+(n.wait&&n.wait.text?', '+n.wait.text:''))+'">'+
      '<span class="top"><span class="id">'+esc(n.id)+'</span><span class="ph">'+esc(PH[n.phase]||n.phase)+'</span></span>'+
      '<span class="t">'+esc(n.title)+'</span>'+
      (wh?'<span class="who">'+esc(wh)+stale+'</span>':(stale?'<span class="who">'+stale+'</span>':''))+w+'</button>';
  }
  function graphHtml(w){
    const by={};w.nodes.forEach(n=>{by[n.id]=n});
    const cols=(w.layers||[]).map((ids,i)=>'<div class="wv-layer" data-layer="'+i+'"><div class="wv-layer-h">'+(i===0?'starts':'after '+i+(i===1?' step':' steps'))+'</div>'+ids.map(id=>nodeBtn(by[id])).join('')+'</div>').join('');
    return '<div class="wv-canvas"><div class="wv-inner"><svg class="wv-edges" aria-hidden="true"></svg><div class="wv-layers" role="group" aria-label="Dependency graph, layered by how many steps must finish first">'+cols+'</div></div></div>';
  }
  function listHtml(w){
    const by={};w.nodes.forEach(n=>{by[n.id]=n});
    return '<ol class="wv-list" aria-label="Work in dependency order">'+(w.order||[]).map(id=>'<li>'+nodeBtn(by[id])+'</li>').join('')+'</ol>';
  }
  function emptyHtml(e){
    return '<div class="wv-empty" role="status"><p class="lead">'+esc(e.lead)+'</p><p>'+esc(e.detail)+'</p>'+cmd(e.cmd)+'</div>';
  }
  function row(k,v,cls){return v?'<dt>'+k+'</dt><dd'+(cls?' class="'+cls+'"':'')+'>'+v+'</dd>':''}
  function detailHtml(n){
    const wh=who(n);
    let why='';
    const s=n.started_by;
    if(s){
      why='Freed when '+esc(s.parent)+' finished '+esc(ago(s.age_h))+'.';
      if(s.trigger)why+=' Success trigger told @'+esc(s.trigger.to||'?')+' '+esc(ago(s.trigger.age_h))+(s.trigger.seen===false?' (not yet seen)':s.trigger.seen?' (seen)':'')+'.';
      else why+=' No trigger message reached a seat.';
      why+=s.began?' Began '+esc(ago(s.began_age_h))+(s.began_by?' by @'+esc(s.began_by):'')+'.':' <span class="warn">Has not begun.</span>';
    }
    let starts='';
    if(n.phase==='done'&&(n.starts||[]).length){
      starts='<ul>'+n.starts.map(c=>'<li>'+esc(c.id)+' · '+esc(PH[c.phase]||c.phase)+(c.freed?(c.began?' · began'+(c.who?' by @'+esc(c.who):''):(c.who?' · told @'+esc(c.who)+', not begun':' · not begun, nobody told')):' · still waits on '+esc(c.still_waiting_on.join(', ')))+'</li>').join('')+'</ul>';
    }
    const a=n.acceptance||{};
    const acc=a.proof?esc(a.proof):(n.lane==='capture'?'<span class="warn">not sounded yet — '+cmd('tickets sound '+n.id)+'</span>':'<span class="mute">none recorded</span>');
    const qs=(a.open_questions||[]).length?'<ul>'+a.open_questions.map(q=>'<li>'+esc(q)+'</li>').join('')+'</ul>':'';
    const hand=(n.handoff||[]).length?'<ul class="wv-handoff">'+n.handoff.map(h=>'<li><span class="from">'+esc(h.from)+'</span> <span class="by">'+esc(h.by||'?')+(h.at?' · '+esc(h.at):'')+'</span><br>'+esc(h.text)+(h.truncated?'…':'')+'</li>').join('')+'</ul>':'';
    const art=n.artifact||{};
    const artTxt=[art.commit?esc(art.commit):'',art.pr?'PR '+esc(art.pr):'',art.branch&&!(art.commit||'').startsWith(art.branch)?esc(art.branch):''].filter(Boolean).join(' · ');
    const cmds=['tickets show '+n.id];
    if(n.phase==='ready')cmds.push('tickets next');
    if(n.phase==='capture')cmds.push('tickets sound '+n.id);
    if(n.phase==='hold')cmds.push('tickets hold '+n.id+' --clear');
    if(n.phase==='review')cmds.push('tickets merge '+n.id);
    if(n.owner)cmds.push('tickets msg --to '+n.owner+' --re '+n.id+' "…"');
    else if(n.dispatch)cmds.push('tickets msg --to '+n.dispatch.to+' --re '+n.id+' "…"');
    return '<button type="button" class="close" data-wv-close aria-label="Close detail">Close</button>'+
      '<div class="hd ph-'+esc(n.phase)+'"><span class="id">'+esc(n.id)+'</span><span class="pill">'+esc(PH[n.phase]||n.phase)+'</span>'+(n.role?'<span class="mute">'+esc(n.role)+'</span>':'')+'<span class="mute">P'+esc(n.priority)+'</span></div>'+
      '<h3>'+esc(n.title)+'</h3><dl>'+
      row('Status',esc(n.evidence),n.stale?'bad':'')+
      row('Owner',wh?esc(wh):'<span class="mute">nobody</span>')+
      row('Waiting',n.wait&&n.wait.text?esc(n.wait.text)+(n.wait.cmd?' · '+cmd(n.wait.cmd):''):'',n.wait&&n.wait.kind&&n.wait.kind!=='deps'?'warn':'')+
      row('Why now',why)+
      row('Started',starts)+
      row('Acceptance',acc)+
      row('Cause',a.cause?esc(a.cause):'')+row('Change',a.change?esc(a.change):'')+row('Open',qs)+
      row('Handoff',hand||(n.deps&&n.deps.length?'<span class="mute">no notes from finished deps yet</span>':''))+
      row('Artifact',artTxt||'<span class="mute">—</span>')+
      row('Verdict',n.verdict?esc(n.verdict):'<span class="mute">—</span>')+
      row('Last note',n.last_note?'<span class="mute">'+esc(n.last_note.by||'?')+'</span> '+esc(n.last_note.text)+(n.last_note.truncated?'…':''):'')+
      '</dl><div class="wv-cmds">'+cmds.map(cmd).join('')+'</div>';
  }
  function drawEdges(){
    if(!HOST||!W||MODE!=='graph')return;
    const svg=HOST.querySelector('.wv-edges'),inner=HOST.querySelector('.wv-inner');
    if(!svg||!inner)return;
    const ir=inner.getBoundingClientRect();
    const pos={};
    HOST.querySelectorAll('.wv-node').forEach(b=>{const r=b.getBoundingClientRect();pos[b.dataset.id]={l:r.left-ir.left,r:r.right-ir.left,t:r.top-ir.top,b:r.bottom-ir.top}});
    svg.setAttribute('width',inner.scrollWidth);svg.setAttribute('height',inner.scrollHeight);
    svg.setAttribute('viewBox','0 0 '+inner.scrollWidth+' '+inner.scrollHeight);
    svg.innerHTML=(W.edges||[]).map(e=>{
      const a=pos[e.from],b=pos[e.to];if(!a||!b)return '';
      const x1=a.r,y1=(a.t+a.b)/2,x2=b.l,y2=(b.t+b.b)/2,dx=Math.max(24,(x2-x1)/2);
      const sel=SEL&&(e.from===SEL||e.to===SEL);
      return '<path class="'+(e.waiting?'waiting':'')+(sel?' sel':'')+'" d="M'+x1+' '+y1+' C'+(x1+dx)+' '+y1+' '+(x2-dx)+' '+y2+' '+x2+' '+y2+'"/>';
    }).join('');
  }
  function nodeOf(id){return W&&W.nodes.find(n=>n.id===id)}
  function renderDetail(){
    const body=HOST.querySelector('.wv-body'),el=HOST.querySelector('.wv-detail');
    const n=SEL?nodeOf(SEL):null;
    if(!body||!el)return;
    if(!n){el.hidden=true;el.innerHTML='';body.classList.remove('has-detail');return}
    el.hidden=false;el.innerHTML=detailHtml(n);body.classList.add('has-detail');
  }
  function select(id,opts){
    opts=opts||{};
    SEL=(id&&nodeOf(id))?id:'';
    try{localStorage.setItem(LS_SEL,SEL)}catch(e){}
    HOST.querySelectorAll('.wv-node').forEach(b=>b.setAttribute('aria-pressed',b.dataset.id===SEL?'true':'false'));
    renderDetail();drawEdges();
    try{document.dispatchEvent(new CustomEvent('atman:work-select',{detail:{id:SEL}}))}catch(e){}
    if(opts.focus&&SEL){const b=HOST.querySelector('.wv-node[data-id="'+SEL+'"]');if(b)b.focus()}
  }
  function setMode(m,persist){
    MODE=m==='list'?'list':'graph';
    if(persist){try{localStorage.setItem(LS_MODE,MODE)}catch(e){}}
    SIG='';
    if(W)render({work:W},HOST);
  }
  function render(d,host){
    HOST=host||document.getElementById('workflowGraph');
    if(!HOST||!d||!d.work)return;
    const w=d.work;
    const sig=MODE+'|'+JSON.stringify(w);
    if(sig===SIG)return;
    SIG=sig;W=w;
    const active=document.activeElement,focusId=(active&&HOST.contains(active)&&active.dataset)?active.dataset.id:'';
    const focusClose=active&&HOST.contains(active)&&active.hasAttribute('data-wv-close');
    const legend='<span class="legend" aria-label="Phases">'+LEGEND.map(p=>'<span class="ph-'+p+'">'+esc(PH[p])+(w.counts&&w.counts[p]?' '+w.counts[p]:'')+'</span>').join('')+'</span>';
    const modes='<span class="wv-modes" role="group" aria-label="Layout"><button type="button" data-wv-mode="graph" aria-pressed="'+(MODE==='graph')+'">Graph</button><button type="button" data-wv-mode="list" aria-pressed="'+(MODE==='list')+'">List</button></span>';
    let main;
    if(w.empty&&!(w.nodes||[]).length)main=emptyHtml(w.empty);
    else main=(MODE==='list'?listHtml(w):graphHtml(w))+(w.empty&&w.empty.kind==='no_edges'?'<div class="wv-empty" role="status" style="margin-top:10px"><p class="lead">'+esc(w.empty.lead)+'</p><p>'+esc(w.empty.detail)+'</p>'+cmd(w.empty.cmd)+'</div>':'');
    HOST.innerHTML='<div class="wv">'+objective(w.objective)+'<div class="wv-bar">'+legend+modes+'</div><div class="wv-body"><div class="wv-main">'+main+'</div><aside class="wv-detail" aria-label="Ticket detail" aria-live="polite" hidden></aside></div></div>';
    if(SEL&&!nodeOf(SEL))SEL='';
    renderDetail();drawEdges();
    if(focusId){const b=HOST.querySelector('.wv-node[data-id="'+focusId+'"]');if(b)b.focus()}
    else if(focusClose){const c=HOST.querySelector('[data-wv-close]');if(c)c.focus()}
  }
  function move(from,dx,dy){
    const layers=[...HOST.querySelectorAll('.wv-layer')];
    if(!layers.length){
      const all=[...HOST.querySelectorAll('.wv-node')],i=all.indexOf(from);
      const nx=all[i+(dy||dx)];if(nx)nx.focus();return;
    }
    const col=from.closest('.wv-layer'),ci=layers.indexOf(col);
    const inCol=[...col.querySelectorAll('.wv-node')],ri=inCol.indexOf(from);
    if(dy){const nx=inCol[ri+dy];if(nx)nx.focus();return}
    const tgt=layers[ci+dx];if(!tgt)return;
    const t=[...tgt.querySelectorAll('.wv-node')];
    const fr=from.getBoundingClientRect(),mid=(fr.top+fr.bottom)/2;
    let best=t[0],bd=Infinity;
    t.forEach(b=>{const r=b.getBoundingClientRect(),d=Math.abs((r.top+r.bottom)/2-mid);if(d<bd){bd=d;best=b}});
    if(best)best.focus();
  }
  document.addEventListener('click',e=>{
    if(!HOST||!HOST.contains(e.target))return;
    const m=e.target.closest('[data-wv-mode]');if(m){setMode(m.dataset.wvMode,true);return}
    if(e.target.closest('[data-wv-close]')){const id=SEL;select('');const b=id&&HOST.querySelector('.wv-node[data-id="'+id+'"]');if(b)b.focus();return}
    const n=e.target.closest('.wv-node');if(n)select(n.dataset.id===SEL?'':n.dataset.id);
  });
  document.addEventListener('keydown',e=>{
    if(!HOST||!HOST.contains(e.target))return;
    if(e.key==='Escape'&&SEL){e.preventDefault();const id=SEL;select('');const b=HOST.querySelector('.wv-node[data-id="'+id+'"]');if(b)b.focus();return}
    const n=e.target.closest('.wv-node');if(!n)return;
    const map={ArrowLeft:[-1,0],ArrowRight:[1,0],ArrowUp:[0,-1],ArrowDown:[0,1]};
    if(!map[e.key])return;
    e.preventDefault();move(n,map[e.key][0],map[e.key][1]);
  });
  window.addEventListener('resize',drawEdges);
  return {render,select,setMode,drawEdges};
})();
"""
