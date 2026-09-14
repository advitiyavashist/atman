"""T-889: the Work view -- objective above a real dependency graph, node detail.

Pure functions over ticket, message and agent dicts, plus the CSS/HTML/JS that
the shell splices in through three named placeholders (``tickets.py``
``_ui_page``). No board I/O lives here: ``tickets.py`` loads tickets, the T-791
graph payload, the message log and the agent records and hands them in, so
there is exactly one source for what the Work view says about a node.

Evidence states (``phase``) are distinct and never overstate (T-892 review):

* ``ready``     unblocked; no reservation, no task posted; ``tickets next`` claims it
* ``reserved``  a reservation names a seat; no task has been posted to it
* ``posted``    a task message about the ticket was posted to a seat. Inbox read,
                wake receipt and claim are separate facts; none is implied
* ``working``   claimed; the evidence is the claim and the last update age
* ``waiting``   open with unfinished ``--after`` deps
* ``capture``   open but not sounded (``tickets sound`` promotes it)
* ``hold``      open but parked (``tickets hold``)
* ``blocked`` / ``review`` / ``done`` / ``discarded`` follow the ticket status

Review evidence is separate from ticket status: ``review_of`` reports the
latest *structured* verdict (``review_events`` from ``atm accept`` /
``atm reject``) with reviewer and artifact SHA, marks verdicts on an older
SHA as superseded, and never turns a done flag or a prose note into
"accepted". Legacy text that looks like accept/approved is an unstructured
note.
"""
from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime, timezone

from .sounding import ticket_lane, ticket_on_hold
from .review_verdict import iter_structured, sha_match as _event_sha_match

PHASES = ("working", "review", "blocked", "posted", "reserved", "ready", "waiting",
          "capture", "hold", "done", "discarded")
STALE_H = 1.5      # same amber/red thresholds the shell's cards use
WARM_H = 0.75
HANDOFF_CHARS = 320
MSG_WINDOW = 2000  # newest task messages considered for dispatch evidence
WAKE_CONFIRMED = ("woken", "deduped")   # tickets.py _AUTONOMOUS_WAKE_LABELS minus watch-poke
SHA_RE = re.compile(r"\b[0-9a-f]{7,40}\b")
FIX_RE = re.compile(r"(?i)\b(request(?:ed)?\s+fix|fix\s+requested|needs?\s+fix|verdict:?\s*\W*\s*fix|\*\*fix\*\*)\b")
ACCEPT_RE = re.compile(r"(?i)\b(accept(?:ed)?|approved|lgtm)\b")
NEG_ACCEPT_RE = re.compile(r"(?i)\b(not|no|never|nothing|nor)\s+(?:\w+\s+){0,2}(accept(?:ed)?|approved)\b")
REJECT_RE = re.compile(r"(?i)\breject(?:ed)?\b")
MERGED_RE = re.compile(r"(?i)^merged into (\S+) as (\S+)")


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


def _msg_id(m):
    """Same identity rule as tickets.py _msg_id (id, else content hash)."""
    if m.get("id"):
        return str(m["id"])
    raw = json.dumps([m.get("at", ""), m.get("from", ""), m.get("to", ""),
                      m.get("re", ""), m.get("text", "")], sort_keys=True)
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:16]


def _epoch_of(t):
    """Messages older than this belong to the ticket's previous life (reopen)."""
    return (t.get("reopened_at") or "").strip()


# --- review evidence --------------------------------------------------------

def artifact_sha(t):
    head = (t.get("review_head") or "").strip().lower()
    if SHA_RE.fullmatch(head):
        return head
    commit = (t.get("commit") or "").strip()
    if "@" in commit:
        commit = commit.rsplit("@", 1)[1]
    return commit if SHA_RE.fullmatch(commit) else ""


def _sha_match(a, b):
    a, b = (a or "").lower(), (b or "").lower()
    if not a or not b:
        return False
    n = min(len(a), len(b), 40)
    return n >= 7 and a[:n] == b[:n]


def _verdict_kind(text):
    low = text.lower()
    m = MERGED_RE.match(text.strip())
    if m:
        return "MERGED"
    if low.startswith("review:"):
        return ""          # the author's own submission, not a verdict
    if FIX_RE.search(text):
        return "FIX"
    # T-944: accept/approved/reject prose is not a verdict. Only review_events
    # written by atm accept / atm reject count as ACCEPT/REJECT.
    if REJECT_RE.search(text) and ("verdict" in low or low.startswith("reject")):
        return "UNSTRUCTURED"
    if ACCEPT_RE.search(text) and not NEG_ACCEPT_RE.search(text):
        if "verdict" in low or low.startswith(("accept", "approved", "lgtm")):
            return "UNSTRUCTURED"
    return ""


def _applies(sha, art):
    if not art:
        return "unknown"
    if not sha:
        return "unknown"
    if _sha_match(sha, art) or _event_sha_match(sha, art):
        return "exact"
    return "superseded"


def _verdict_entries(t, msgs_re):
    """Every recorded verdict about this ticket, oldest first.

    ACCEPT/REJECT come only from structured ``review_events``. Notes and
    messages may still contribute FIX/MERGED, or UNSTRUCTURED lookalikes.
    """
    art = artifact_sha(t)
    seen = set()
    entries = []
    rows = [("event", ev) for ev in iter_structured(t)]
    rows += [("note", n) for n in (t.get("notes") or [])]
    rows += [("msg", m) for m in (msgs_re or [])]
    rows.sort(key=lambda r: r[1].get("at") or "")
    for src, r in rows:
        if src == "event":
            kind = (r.get("kind") or "").strip().upper()
            sha = (r.get("sha") or "").strip()
            by = r.get("by") or ""
            key = ("event", kind, by, sha)
            if key in seen:
                continue
            seen.add(key)
            extra = r.get("notes") or r.get("reason") or ""
            entries.append({"kind": kind, "by": by, "at": r.get("at") or "",
                            "sha": sha, "applies": _applies(sha, art),
                            "source": "event", "text": extra[:200]})
            continue
        text = (r.get("text") or "").strip()
        if not text:
            continue
        kind = _verdict_kind(text)
        if not kind:
            continue
        sha = ""
        if kind == "MERGED":
            m = MERGED_RE.match(text)
            pin = re.search(r"pinned\s+([0-9a-f]{7,40})", text)
            sha = (pin.group(1) if pin else m.group(2)).strip("():,.")
        else:
            for cand in SHA_RE.findall(text):
                sha = cand
                break
        by = r.get("by") or r.get("from") or ""
        key = (kind, by, sha) if sha else (kind, by, text[:120])
        if key in seen:
            continue          # the same verdict recorded as a note and echoed as a message
        seen.add(key)
        entries.append({"kind": kind, "by": by, "at": r.get("at") or "",
                        "sha": sha, "applies": _applies(sha, art),
                        "source": src, "text": text[:200]})
    return entries


def review_of(t, msgs_re=None):
    """Ticket status stays separate from review evidence.

    ``label`` is what the UI shows next to the artifact; ``latest`` the newest
    verdict that applies to the recorded artifact (or the newest of unknown
    applicability); ``history`` everything else, newest first.
    """
    st = t.get("status")
    art = artifact_sha(t)
    entries = _verdict_entries(t, msgs_re)
    unstruct = [e for e in entries if e["kind"] == "UNSTRUCTURED"]
    verdicts = [e for e in entries if e["kind"] != "UNSTRUCTURED"]
    exact = [e for e in verdicts if e["applies"] == "exact"]
    unknown = [e for e in verdicts if e["applies"] == "unknown"]
    merged = [e for e in verdicts if e["kind"] == "MERGED"]
    latest = (exact or unknown or [None])[-1]
    history = [e for e in reversed(entries) if e is not latest]
    short = (art[:7] if art else "")
    if merged and (not art or any(_sha_match(m["sha"], art) for m in merged) or not merged[-1]["sha"]):
        m = merged[-1]
        label = "Merged into main as %s" % (m["sha"][:7] or short or "?")
        latest = m
        history = [e for e in reversed(entries) if e is not m]
    elif latest and latest["kind"] == "ACCEPT":
        label = "Accepted by @%s on %s" % (latest["by"] or "?", latest["sha"][:7] or short or "unrecorded artifact")
        if latest["applies"] == "unknown":
            label += " (artifact not identified in the note)"
    elif latest and latest["kind"] in ("FIX", "REJECT"):
        label = "%s by @%s on %s" % ("FIX requested" if latest["kind"] == "FIX" else "Rejected",
                                     latest["by"] or "?", latest["sha"][:7] or short or "unrecorded artifact")
        if latest["applies"] == "unknown":
            label += " (artifact not identified in the note)"
        if st == "done":
            label += " · marked done afterwards; acceptance not recorded"
    elif st == "done":
        label = "Marked done; verification not recorded"
    elif st == "review":
        label = "Awaiting review of %s · no verdict recorded" % (short or "unrecorded artifact")
        if unstruct:
            label += " · unstructured note"
        sup = [e for e in history if e["applies"] == "superseded" and e["kind"] not in ("MERGED", "UNSTRUCTURED")]
        if sup:
            label += " · earlier %s by @%s on %s superseded" % (sup[0]["kind"], sup[0]["by"] or "?", sup[0]["sha"][:7])
    else:
        label = ""
        sup = [e for e in history if e["kind"] in ("FIX", "REJECT")]
        if sup:
            label = "earlier %s by @%s on %s (before resubmission)" % (sup[0]["kind"], sup[0]["by"] or "?", sup[0]["sha"][:7] or "?")
    return {"artifact": art, "label": label, "latest": latest, "history": history,
            "verified": bool(merged) or bool(latest and latest["kind"] == "ACCEPT" and latest["applies"] == "exact")}


def verdict_of(t, msgs_re=None):
    """Backward-compatible string: the review label."""
    return review_of(t, msgs_re)["label"]


# --- routing / delivery evidence --------------------------------------------

def _task_messages_by_ticket(messages):
    out = {}
    for m in (messages or [])[-MSG_WINDOW:]:
        if (m.get("kind") or "") != "task":
            continue
        re_ = (m.get("re") or "").strip()
        if re_:
            out.setdefault(re_, []).append(m)
    return out


def _messages_by_ticket(messages):
    out = {}
    for m in (messages or [])[-MSG_WINDOW:]:
        re_ = (m.get("re") or "").strip()
        if re_:
            out.setdefault(re_, []).append(m)
    return out


def wake_receipt(rec, msg_id):
    """The seat's last-wake receipt if it names this message (tickets.py
    _note_wake_delivery); None when no receipt exists for it."""
    if not rec or not msg_id:
        return None
    wd = rec.get("wake_delivery") if isinstance(rec, dict) else None
    if not isinstance(wd, dict):
        wd = {}
    if str(wd.get("message_id") or "") == str(msg_id):
        label = str(wd.get("label") or "")
        return {"label": label, "at": wd.get("at") or "", "confirmed": label in WAKE_CONFIRMED}
    if str(msg_id) in [str(x) for x in (rec.get("wake_delivery_ids") or [])]:
        return {"label": "woken", "at": "", "confirmed": True}
    return None


def _receipts(to, m, acked, agents, now):
    seen = None
    if acked is not None:
        try:
            seen = bool(acked(to, m))
        except Exception:  # noqa: BLE001 - evidence stays honest as unknown
            seen = None
    wake = wake_receipt((agents or {}).get(to), _msg_id(m))
    if wake and wake.get("at"):
        wake["age_h"] = _hours_since(wake["at"], now)
    return {"seen": seen, "wake": wake}


def delivery_text(d):
    """One honest phrase for a task message's receipts."""
    wake = d.get("wake") if d else None
    if wake and wake.get("confirmed"):
        return "wake confirmed" + (" " + _fmt_age(wake.get("age_h")) if wake.get("at") else "")
    if wake and wake.get("label"):
        return "wake: %s" % wake["label"]
    seen = d.get("seen") if d else None
    if seen is True:
        return "inbox read, not acknowledged"
    if seen is False:
        return "not read, wake unconfirmed"
    return "delivery unknown"


def _dispatch_of(t, task_msgs, acked, agents, now):
    """Newest task message about this ticket that is current: after any reopen,
    and addressed to the reserved seat when a reservation exists. Older or
    other-recipient posts are counted as ``ignored`` history, never as intent."""
    tid = t.get("id", "")
    reserved = (t.get("reserved_for") or "").strip()
    epoch = _epoch_of(t)
    ignored = 0
    for m in reversed(task_msgs.get(tid) or []):
        to = (m.get("to") or "").strip()
        if not to or to.lower() in ("all", "everyone"):
            continue
        if epoch and (m.get("at") or "") < epoch:
            ignored += 1
            continue
        if reserved and to != reserved:
            ignored += 1
            continue
        d = _receipts(to, m, acked, agents, now)
        d.update({"to": to, "from": m.get("from") or "", "at": m.get("at") or "",
                  "age_h": _hours_since(m.get("at") or "", now), "msg_id": _msg_id(m),
                  "text": (m.get("text") or "").strip()[:160], "ignored": ignored,
                  "unverified": bool(m.get("unverified")),
                  "via": m.get("via") or "",
                  "provenance": "absent" if "via" not in m else (
                      "unverified" if m.get("unverified") else "verified")})
        return d
    return None if not ignored else {"ignored": ignored, "to": "", "msg_id": "", "seen": None, "wake": None}


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
    if dispatch and dispatch.get("to"):
        return "posted"
    if (t.get("reserved_for") or "").strip():
        return "reserved"
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


def evidence_of(t, phase, dispatch, progress, now):
    owner = (t.get("owner") or "").strip()
    reserved = (t.get("reserved_for") or "").strip()
    ignored = (dispatch or {}).get("ignored") or 0
    hist = " · %d earlier task post%s ignored (before reopen or to another seat)" % (
        ignored, "" if ignored == 1 else "s") if ignored else ""
    if phase == "working":
        claimed = _hours_since(t.get("claimed_at") or "", now)
        stamps = [n.get("at") for n in (t.get("notes") or []) if n.get("at")]
        last = max(stamps) if stamps else (t.get("claimed_at") or "")
        upd = _hours_since(last, now)
        text = "Claimed by @%s %s" % (owner or "?", _fmt_age(claimed))
        text += " · last update %s" % _fmt_age(upd)
        if upd is not None and upd > STALE_H:
            text += " · silent >%sh" % ("%g" % STALE_H)
        return text
    if phase == "review":
        bits = ["Submitted by @%s" % (owner or "?")]
        if t.get("commit"):
            bits.append(t["commit"])
        if t.get("pr"):
            bits.append("PR %s" % t["pr"])
        return " · ".join(bits)
    if phase == "done":
        return "Marked done %s by @%s" % (_fmt_age(_hours_since(t.get("done_at") or "", now)), owner or "?")
    if phase == "blocked":
        reason = (_last_note(t).get("text") or "").strip()
        return "Blocked by @%s" % (_last_note(t).get("by") or owner or "?") + (": " + reason[:160] if reason else "")
    if phase == "posted":
        text = "Task posted to @%s by %s %s · %s · not claimed" % (
            dispatch["to"], dispatch.get("from") or "?", _fmt_age(dispatch.get("age_h")), delivery_text(dispatch))
        if reserved and reserved != dispatch["to"]:
            text += " · reserved for @%s" % reserved
        return text + hist
    if phase == "reserved":
        return "Reserved for @%s · no task posted · not claimed" % reserved + hist
    if phase == "ready":
        text = "Unblocked · no reservation, no task posted · tickets next claims it"
        if progress and progress.get("became_ready"):
            text = "Became ready when %s finished %s · no reservation, no task posted · tickets next claims it" % (
                progress["parent"], _fmt_age(progress.get("age_h")))
        return text + hist
    if phase == "waiting":
        if progress:
            return "Dependency %s completed %s; still waiting on %s" % (
                progress["parent"], _fmt_age(progress.get("age_h")), ", ".join(progress["pending"]))
        return "not claimable until its --after deps finish"
    if phase == "capture":
        return "Captured, not sounded · invisible to tickets next until tickets sound"
    if phase == "hold":
        return "Parked · tickets next skips it"
    if phase == "discarded":
        return "Discarded · stays on the graph for history"
    return ""


# --- success-to-next story --------------------------------------------------

def _trigger_text_matches(text, child, parent):
    return ("unblocked %s after %s" % (child, parent)) in text and "success trigger" in text


def progress_of(t, by_id, done_ids, task_msgs, acked, agents, now):
    """The completed-parent story from the child's side, with each fact kept
    separate: which deps finished, which still wait, whether readiness is
    evidenced, whether a trigger was posted and received, and whether a claim
    followed. ``None`` when no dependency has finished yet."""
    tid = t.get("id", "")
    deps = list(t.get("deps") or [])
    done_deps = [d for d in deps if d in done_ids and d in by_id]
    if not done_deps:
        return None
    pending = [d for d in deps if d not in done_ids]
    parent = max(done_deps, key=lambda d: by_id[d].get("done_at") or "")
    pt = by_id[parent]
    at = pt.get("done_at") or ""
    all_done = not pending
    lane = ticket_lane(t)
    st = t.get("status")
    gate = ""
    if lane == "capture":
        gate = "capture"
    elif lane == "discarded":
        gate = "discarded"
    elif ticket_on_hold(t):
        gate = "hold"
    became_ready = bool(all_done and not gate and st in ("open", "claimed", "review", "done"))
    epoch = _epoch_of(t)
    trigger = None
    for m in reversed(task_msgs.get(tid) or []):
        text = m.get("text") or ""
        if not _trigger_text_matches(text, tid, parent):
            continue
        m_at = m.get("at") or ""
        if at and m_at < at:
            continue          # a trigger from an earlier completion of the same parent
        if epoch and m_at < epoch:
            continue          # posted before the ticket was reopened
        to = (m.get("to") or "").strip()
        trigger = {"to": to, "from": m.get("from") or "", "at": m_at,
                   "age_h": _hours_since(m_at, now), "msg_id": _msg_id(m)}
        trigger.update(_receipts(to, m, acked, agents, now) if to else {"seen": None, "wake": None})
        break
    claimed_at = t.get("claimed_at") or ""
    claim = None
    if claimed_at:
        current = st in ("claimed", "review", "done")
        if not current:
            causality = "stale"          # reopen keeps the old stamp; the ticket is open again
        elif at and claimed_at < at:
            causality = "predates"       # claimed before this parent finished
        elif trigger and claimed_at >= trigger["at"]:
            causality = "after_trigger"
        elif trigger:
            causality = "before_trigger"
        else:
            causality = "unverified"     # claim after completion, no trigger record
        claim = {"at": claimed_at, "age_h": _hours_since(claimed_at, now),
                 "by": (t.get("owner") or "").strip(), "current": current, "causality": causality}
    return {"parent": parent, "parent_title": pt.get("title") or "", "at": at,
            "age_h": _hours_since(at, now),
            "done_deps": [{"id": d, "title": by_id[d].get("title") or "", "at": by_id[d].get("done_at") or ""}
                          for d in sorted(done_deps, key=lambda d: by_id[d].get("done_at") or "")],
            "pending": pending, "all_done": all_done, "gate": gate,
            "became_ready": became_ready, "trigger": trigger, "claim": claim}


def _starts_of(t, kids, by_id, done_ids, phases, dispatches):
    """For a finished parent: each child, whether this completion left it
    eligible, who (if anyone) is named for it and on what evidence, and
    whether a claim followed this completion."""
    out = []
    tid = t.get("id", "")
    at = t.get("done_at") or ""
    for cid in kids.get(tid, []):
        child = by_id.get(cid)
        if not child:
            continue
        leftover = [d for d in (child.get("deps") or []) if d not in done_ids and d != tid]
        lane = ticket_lane(child)
        gate = "capture" if lane == "capture" else ("discarded" if lane == "discarded"
                                                    else ("hold" if ticket_on_hold(child) else ""))
        st = child.get("status")
        disp = dispatches.get(cid)
        owner = (child.get("owner") or "").strip()
        reserved = (child.get("reserved_for") or "").strip()
        if st in ("claimed", "review", "done") and owner:
            who, kind = owner, "claimed"
        elif st == "blocked" and owner:
            who, kind = owner, "claimed"
        elif disp and disp.get("to"):
            who, kind = disp["to"], "posted"
        elif reserved:
            who, kind = reserved, "reserved"
        else:
            who, kind = "", ""
        claimed_at = child.get("claimed_at") or ""
        current = st in ("claimed", "review", "done", "blocked")
        began = bool(current and claimed_at and (not at or claimed_at >= at))
        row = {"id": cid, "title": child.get("title") or "", "phase": phases.get(cid, ""),
               "freed": bool(not leftover and not gate), "still_waiting_on": leftover, "gate": gate,
               "who": who, "who_kind": kind, "began": began, "began_at": claimed_at if began else "",
               "claim_predates": bool(current and claimed_at and at and claimed_at < at),
               "delivery": delivery_text(disp) if (kind == "posted" and disp) else ""}
        out.append(row)
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
                "detail": "Plan the first tasks; add --after links only where one task really must finish before another.",
                "cmd": "tickets plan '{\"tasks\":[{\"key\":\"a\",\"title\":\"First task\"},{\"key\":\"b\",\"title\":\"Second task\",\"deps\":[\"a\"]}]}'"}
    if nodes and not edges:
        ids = sorted(set(n["id"] for n in nodes))
        out = {"kind": "no_edges",
               "lead": "No dependencies yet.",
               "detail": "A board of independent tasks is valid. If one ticket must finish before another, record that order so success can start the next one.",
               "cmd": "tickets plan"}
        if len(ids) >= 2:
            out["example"] = "tickets dep %s --after %s" % (ids[1], ids[0])
        return out
    if not nodes:
        return {"kind": "all_done",
                "lead": "Everything on the board is done.",
                "detail": "Plan the next slice against the objective.",
                "cmd": "tickets plan"}
    return None


def _who_of(n):
    """Who is named for a node and on what evidence -- never 'told' from a reservation."""
    ph = n["phase"]
    if ph in ("working", "review", "done", "blocked") and n["owner"]:
        return n["owner"], "claimed"
    if ph == "posted" and n["dispatch"] and n["dispatch"].get("to"):
        return n["dispatch"]["to"], "posted"
    if n["reserved_for"]:
        return n["reserved_for"], "reserved"
    if n["suggested"]:
        return n["suggested"], "suggested"
    return "", ""


def work_payload(tickets, graph, messages, objective=None, acked=None, agents=None, now=None):
    """Everything the Work view renders, as plain data.

    ``graph`` is the T-791 ``workflow_graph`` payload (nodes, edges, forest);
    ``messages`` the raw message log (newest last); ``acked(agent, msg)`` the
    shell's inbox-seen check (None -> unknown); ``agents`` the agent records by
    name, read only for their wake receipts (``wake_delivery``).
    """
    graph = graph or {}
    now = now or datetime.now(timezone.utc)
    agents = agents or {}
    by_id = dict((t["id"], t) for t in tickets)
    done_ids = set(t["id"] for t in tickets if t.get("status") == "done")
    kids = {}
    for t in tickets:
        for d in t.get("deps") or []:
            kids.setdefault(d, []).append(t["id"])
    task_msgs = _task_messages_by_ticket(messages)
    msgs_re = _messages_by_ticket(messages)
    keep = [n["id"] for n in graph.get("nodes") or [] if n["id"] in by_id]
    keep_set = set(keep)

    dispatches, phases, waits = {}, {}, {}
    for tid in keep:
        t = by_id[tid]
        waiting = [d for d in (t.get("deps") or []) if d not in done_ids]
        waits[tid] = waiting
        disp = _dispatch_of(t, task_msgs, acked, agents, now) if t.get("status") == "open" else None
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
        progress = progress_of(t, by_id, done_ids, task_msgs, acked, agents, now)
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
        review = review_of(t, msgs_re.get(tid))
        node = {
            "id": tid,
            "title": t.get("title") or "",
            "status": t.get("status") or "",
            "phase": ph,
            "evidence": evidence_of(t, ph, disp, progress, now),
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
            "dispatch": disp if (disp and disp.get("to")) else None,
            "stale_posts": (disp or {}).get("ignored") or 0,
            "progress": progress,
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
                         "pr": str(t.get("pr") or ""), "sha": review["artifact"]},
            "review": review,
            "verdict": review["label"],
            "since_update_h": since_update,
            "stale": bool(since_update is not None and since_update > STALE_H),
            "claimed_at": t.get("claimed_at") or "",
            "done_at": t.get("done_at") or "",
            "reopened_at": t.get("reopened_at") or "",
            "created": t.get("created") or "",
        }
        node["who"], node["who_kind"] = _who_of(node)
        nodes.append(node)

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

    def _brief(n):
        return {"id": n["id"], "title": n["title"], "phase": n["phase"],
                "who": n["who"], "who_kind": n["who_kind"]}

    working = sorted(_pick("working"), key=lambda n: (-(n["since_update_h"] or 0), n["id"]))
    candidates = sorted(_pick("posted") + _pick("reserved") + _pick("ready"),
                        key=lambda n: (n["priority"], n["id"]))
    nxt = None
    if candidates:
        c = candidates[0]
        nxt = _brief(c)
        nxt["evidence"] = c["evidence"]
        nxt["of"] = len(candidates)
    blockers = []
    for n in _pick("blocked") + _pick("hold") + _pick("capture"):
        blockers.append({"id": n["id"], "title": n["title"], "phase": n["phase"],
                         "kind": n["wait"]["kind"], "text": n["wait"]["text"], "cmd": n["wait"]["cmd"]})
    obj = objective or {}
    return {
        "objective": {
            "text": (obj.get("text") or "").strip(),
            "state": obj.get("state") or "",
            "exit_criterion": (obj.get("exit_criterion") or "").strip(),
            "exit_missing": bool(obj.get("exit_missing")),
        },
        "summary": {
            "finishing": [dict(_brief(n), owner=n["owner"], stale=n["stale"]) for n in working],
            "blocked": blockers,
            "waiting": counts.get("waiting", 0),
            "waiting_on": [{"id": n["id"], "title": n["title"], "on": n["waiting"]} for n in _pick("waiting")],
            "ready": [n["id"] for n in _pick("ready")],
            "reserved": [n["id"] for n in _pick("reserved")],
            "posted": [n["id"] for n in _pick("posted")],
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
# placeholders. Tokens (--bg, --fg, --acc, ...) come from the shell; the
# module adds --wv-acc/--wv-focus so light mode gets a readable action colour
# (shell brass #c4b49a is 1.95:1 on the light card) and status colours are
# semantic, never brass.

WORK_CSS = r"""
/* T-889 Work view */
.wv{display:flex;flex-direction:column;gap:12px;--wv-acc:var(--acc);--wv-focus:var(--acc)}
body[data-theme=light] .wv{--wv-acc:#6b4f14;--wv-focus:#6b4f14}
.wv-objective{display:grid;grid-template-columns:minmax(0,1fr) auto;gap:6px 16px;align-items:start;padding:10px 12px;border:1px solid var(--line);border-radius:10px;background:var(--surface)}
.wv-objective .k{font-size:11px;letter-spacing:.08em;text-transform:uppercase;color:var(--mute);font-weight:650}
.wv-objective .v{font-size:15px;font-weight:650;margin-top:2px}
.wv-objective .exit{grid-column:1/-1;font-size:13px;color:var(--fg)}
.wv-objective .exit b{color:var(--mute);font-weight:650}
.wv-objective .exit.missing{color:var(--warn)}
.wv-objective .state{font:12px/1.3 ui-monospace,Menlo,monospace;color:var(--mute);padding:3px 8px;border:1px solid var(--line);border-radius:999px;white-space:nowrap}
.wv-lead{margin:0;font-size:13px;color:var(--mute)}
.wv-bar{display:flex;flex-wrap:wrap;gap:8px 14px;align-items:center;font-size:12px;color:var(--mute)}
.wv-bar .legend{display:flex;flex-wrap:wrap;gap:4px 10px}
.wv-bar .legend span::before{content:"";display:inline-block;width:8px;height:8px;border-radius:50%;margin-right:5px;background:var(--wv-c,var(--mute));vertical-align:0}
.wv-modes{display:inline-flex;gap:4px;margin-left:auto}
.wv-modes button{appearance:none;background:transparent;border:1px solid var(--line);color:var(--mute);padding:3px 9px;font:12px/1.2 inherit;font-weight:650;border-radius:6px;cursor:pointer}
.wv-modes button[aria-pressed=true]{color:var(--fg);border-color:var(--wv-acc);background:var(--chip)}
.wv-modes button:focus-visible,.wv-node:focus-visible,.wv-cmd-btn:focus-visible,.wv-detail .close:focus-visible,.wv-detail .back:focus-visible{outline:2px solid var(--wv-focus);outline-offset:2px}
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
.wv-edges path.sel{stroke:var(--wv-acc);stroke-opacity:1;stroke-width:2}
.wv-node{position:relative;z-index:1;appearance:none;text-align:left;width:222px;padding:8px 10px 8px 12px;border:1px solid var(--line);border-left:3px solid var(--wv-c,var(--mute));border-radius:8px;background:var(--card);color:var(--fg);font:inherit;cursor:pointer;display:flex;flex-direction:column;gap:3px}
.wv-node[aria-pressed=true]{border-color:var(--wv-acc);border-left-color:var(--wv-c,var(--wv-acc));box-shadow:0 0 0 1px var(--wv-acc) inset}
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
/* status colours are semantic tokens; brass (--acc) is reserved for actions and focus */
.ph-working{--wv-c:var(--flight)}.ph-review{--wv-c:var(--review)}.ph-blocked{--wv-c:var(--blocked)}
.ph-posted{--wv-c:var(--progress)}.ph-reserved{--wv-c:color-mix(in srgb,var(--ok) 45%,var(--mute))}.ph-ready{--wv-c:var(--ok)}.ph-waiting{--wv-c:var(--mute)}
.ph-capture,.ph-hold{--wv-c:var(--warn)}.ph-done{--wv-c:var(--mute)}.ph-discarded{--wv-c:var(--mute)}
.wv-list{list-style:none;margin:0;padding:0;display:flex;flex-direction:column;gap:6px}
.wv-list .wv-node{width:100%;flex-direction:row;flex-wrap:wrap;align-items:baseline;gap:6px 12px}
.wv-list .wv-node .t{display:block;-webkit-line-clamp:unset;flex:1 1 240px}
.wv-list .wv-node .top{gap:8px}
.wv-detail{border:1px solid var(--line);border-radius:10px;background:var(--card);padding:12px 14px;font-size:13px;position:sticky;top:64px}
@media(max-width:900px){.wv-detail{position:static}}
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
.wv-detail .close,.wv-detail .back{appearance:none;background:transparent;border:1px solid var(--line);color:var(--mute);border-radius:6px;padding:2px 8px;font:12px/1.2 inherit;cursor:pointer}
.wv-detail .close{float:right}
.wv-detail .back{display:none;margin-top:10px}
@media(max-width:900px){.wv-detail .back{display:inline-block}}
.wv-detail .hist{color:var(--mute);font-size:12px}
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
  const PH={working:'Working',review:'In review',blocked:'Blocked',posted:'Task posted',reserved:'Reserved',ready:'Ready',waiting:'Waiting',capture:'Capture',hold:'Hold',done:'Done',discarded:'Discarded'};
  const LEGEND=['ready','reserved','posted','working','review','blocked','waiting','capture','hold','done'];
  const reduced=()=>{try{return window.matchMedia('(prefers-reduced-motion: reduce)').matches}catch(e){return true}};
  const stacked=()=>{try{return window.matchMedia('(max-width: 900px)').matches}catch(e){return false}};
  function ago(h){if(h==null)return '—';if(h<1/60)return 'just now';if(h<1)return Math.round(h*60)+'m ago';if(h<48)return (h<10?h.toFixed(1):Math.round(h))+'h ago';return Math.round(h/24)+'d ago'}
  function cmd(s){return '<code class="wv-cmd">'+esc(s)+'</code>'}
  // receipts are separate facts: wake receipt > inbox read > nothing; none of them is a claim
  function delivery(d){
    if(!d)return 'delivery unknown';
    if(d.wake&&d.wake.confirmed)return 'wake confirmed'+(d.wake.at?' '+ago(d.wake.age_h):'');
    if(d.wake&&d.wake.label)return 'wake: '+d.wake.label;
    if(d.seen===true)return 'inbox read, not acknowledged';
    if(d.seen===false)return 'not read, wake unconfirmed';
    return 'delivery unknown';
  }
  function shortDelivery(d){
    if(!d)return '';
    if(d.wake&&d.wake.confirmed)return ' · woken';
    if(d.wake&&d.wake.label)return ' · wake: '+d.wake.label;
    if(d.seen===true)return ' · read';
    if(d.seen===false)return ' · unread';
    return '';
  }
  function who(n,full){
    if(!n.who)return n.phase==='working'||n.phase==='review'||n.phase==='done'?'unowned':'';
    if(n.who_kind==='claimed')return '@'+n.who;
    if(n.who_kind==='posted')return full?'task posted to @'+n.who+' · '+delivery(n.dispatch):'posted to @'+n.who+shortDelivery(n.dispatch)+' · not claimed';
    if(n.who_kind==='reserved')return full?'reserved for @'+n.who+' · no task posted':'reserved for @'+n.who+' · no task';
    if(n.who_kind==='suggested')return 'suggested @'+n.who;
    return '@'+n.who;
  }
  function exitLine(o){
    if(!o||!o.text)return '';
    return o.exit_criterion?'<div class="exit"><b>Done when</b> '+esc(o.exit_criterion)+'</div>':'<div class="exit missing"><b>Done when</b> no exit criterion yet — '+cmd('tickets objective --set "…" --exit "…"')+'</div>';
  }
  function objective(o){
    // T-810 shell may carry its own standing-objective strip above the graph. Defer the
    // objective text to it, but never lose the terminal criterion: render Done when here
    // unless the shell strip marks its own with [data-done-when].
    const shell=document.getElementById('workObjective');
    if(shell){
      if(!o||!o.text||shell.querySelector('[data-done-when]'))return '';
      return '<section class="wv-objective" aria-label="Done when">'+exitLine(o)+'</section>';
    }
    const has=o&&o.text;
    return '<section class="wv-objective" aria-label="Objective"><div><div class="k">Objective</div><div class="v">'+(has?esc(o.text):'No standing objective yet.')+'</div>'+(has?'':'<div class="exit mute">'+cmd('tickets objective --set "what we are finishing" --exit "how we know it is done"')+'</div>')+'</div>'+(has&&o.state?'<span class="state">'+esc(o.state)+'</span>':'')+exitLine(o)+'</section>';
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
    return '<div class="wv-empty" role="status"><p class="lead">'+esc(e.lead)+'</p><p>'+esc(e.detail)+'</p>'+cmd(e.cmd)+(e.example?'<p style="margin-top:8px">If one really must wait for the other: '+cmd(e.example)+'</p>':'')+'</div>';
  }
  function row(k,v,cls){return v?'<dt>'+k+'</dt><dd'+(cls?' class="'+cls+'"':'')+'>'+v+'</dd>':''}
  function parentRef(p){return esc(p.parent)+(p.parent_title?' '+esc(p.parent_title):'')}
  // the success-to-next story, one fact per sentence; a child with an unfinished dep is still waiting
  function progressHtml(n){
    const p=n.progress;if(!p)return '';
    const parent=parentRef(p),when=esc(ago(p.age_h));
    let s='';
    if(!p.all_done)s='Dependency '+parent+' completed '+when+'; still waiting on '+esc(p.pending.join(', '))+'.';
    else if(p.gate)s='All dependencies finished ('+parent+' last, '+when+'); '+(p.gate==='capture'?'held in capture until sounded':p.gate==='hold'?'parked on hold':'discarded')+', so not ready.';
    else if(p.became_ready)s='Became ready when '+parent+' finished '+when+' (all '+p.done_deps.length+' '+(p.done_deps.length===1?'dependency':'dependencies')+' done).';
    else s='Dependencies finished ('+parent+' last, '+when+'); readiness not evidenced.';
    if(p.trigger)s+=' Success trigger posted to @'+esc(p.trigger.to||'?')+' '+esc(ago(p.trigger.age_h))+' · '+esc(delivery(p.trigger))+'.';
    else if(p.all_done&&!p.gate)s+=' No trigger message posted.';
    const c=p.claim;
    if(c){
      const by=c.by?' by @'+esc(c.by):'';
      if(!c.current)s+=' Earlier claim recorded '+esc(ago(c.age_h))+'; the ticket is open again, so that claim is history, not current work.';
      else if(c.causality==='after_trigger')s+=' Claimed'+by+' '+esc(ago(c.age_h))+', after the trigger.';
      else if(c.causality==='predates')s+=' Claim recorded '+esc(ago(c.age_h))+', before '+esc(p.parent)+' finished; trigger relationship unverified.';
      else if(c.causality==='before_trigger')s+=' Claimed'+by+' '+esc(ago(c.age_h))+', before the trigger was posted; trigger relationship unverified.';
      else s+=' Claimed'+by+' '+esc(ago(c.age_h))+'; trigger relationship unverified.';
    }else if(p.all_done&&!p.gate&&(n.phase==='ready'||n.phase==='reserved'||n.phase==='posted'))s+=' <span class="warn">Has not begun.</span>';
    return s;
  }
  function startsHtml(n){
    if(n.phase!=='done'||!(n.starts||[]).length)return '';
    return '<ul>'+n.starts.map(c=>{
      let t=esc(c.id)+(c.title?' '+esc(c.title):'')+' · '+esc(PH[c.phase]||c.phase);
      if(!c.freed)t+=c.gate?' · all deps done but '+esc(c.gate==='capture'?'waits in capture':c.gate==='hold'?'on hold':c.gate):' · still waits on '+esc(c.still_waiting_on.join(', '));
      else if(c.began)t+=' · claimed by @'+esc(c.who)+' after this finished';
      else if(c.claim_predates)t+=' · claim by @'+esc(c.who)+' recorded before this finished; relationship unverified';
      else if(c.who_kind==='posted')t+=' · task posted to @'+esc(c.who)+' · '+esc(c.delivery||'delivery unknown')+' · not claimed';
      else if(c.who_kind==='reserved')t+=' · reserved for @'+esc(c.who)+' · no task posted · not claimed';
      else t+=' · nobody reserved or posted to · not claimed';
      return '<li>'+t+'</li>'}).join('')+'</ul>';
  }
  function reviewHtml(n){
    const r=n.review||{};
    let s=r.label?esc(r.label):'<span class="mute">no verdict recorded</span>';
    const hist=(r.history||[]).filter(h=>h.kind!=='MERGED'||h.applies!=='exact');
    if(hist.length)s+='<div class="hist">earlier: '+hist.slice(0,3).map(h=>esc(h.kind==='UNSTRUCTURED'?'unstructured note':h.kind)+' by @'+esc(h.by||'?')+(h.sha?' on '+esc(h.sha.slice(0,7)):'')+(h.applies==='superseded'?' (superseded)':h.applies==='unknown'?' (artifact unknown)':'')).join('; ')+'</div>';
    return s;
  }
  function detailHtml(n){
    const wh=who(n,true);
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
    if(n.who&&n.who_kind!=='suggested')cmds.push('tickets msg --to '+n.who+' --re '+n.id+' "…"');
    const stat=esc(n.evidence);
    return '<button type="button" class="close" data-wv-close aria-label="Close detail">Close</button>'+
      '<div class="hd ph-'+esc(n.phase)+'"><span class="id">'+esc(n.id)+'</span><span class="pill">'+esc(PH[n.phase]||n.phase)+'</span>'+(n.role?'<span class="mute">'+esc(n.role)+'</span>':'')+'<span class="mute">P'+esc(n.priority)+'</span></div>'+
      '<h3>'+esc(n.title)+'</h3><dl>'+
      row('Status',stat,n.stale?'bad':'')+
      row('Who',wh?esc(wh):'<span class="mute">nobody</span>')+
      row('Waiting',n.wait&&n.wait.text?esc(n.wait.text)+(n.wait.cmd?' · '+cmd(n.wait.cmd):''):'',n.wait&&n.wait.kind&&n.wait.kind!=='deps'?'warn':'')+
      row('Why now',progressHtml(n))+
      row('Started',startsHtml(n))+
      row('Acceptance',acc)+
      row('Cause',a.cause?esc(a.cause):'')+row('Change',a.change?esc(a.change):'')+row('Open',qs)+
      row('Handoff',hand||(n.deps&&n.deps.length?'<span class="mute">no notes from finished deps yet</span>':''))+
      row('Artifact',artTxt||'<span class="mute">—</span>')+
      row('Review',reviewHtml(n))+
      row('Last note',n.last_note?'<span class="mute">'+esc(n.last_note.by||'?')+'</span> '+esc(n.last_note.text)+(n.last_note.truncated?'…':''):'')+
      '</dl><div class="wv-cmds">'+cmds.map(cmd).join('')+'</div>'+
      '<button type="button" class="back" data-wv-back>Back to '+esc(n.id)+' in the '+(MODE==='list'?'list':'graph')+'</button>';
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
  // one short announcement per selection change; polls never re-announce the detail
  function announce(text){const live=HOST&&HOST.querySelector('[data-wv-live]');if(live)live.textContent=text}
  function backToNode(id){const b=id&&HOST.querySelector('.wv-node[data-id="'+id+'"]');if(b){b.focus();if(stacked())b.scrollIntoView({block:'center',behavior:reduced()?'auto':'smooth'})}}
  function select(id,opts){
    opts=opts||{};
    const prev=SEL;
    SEL=(id&&nodeOf(id))?id:'';
    try{localStorage.setItem(LS_SEL,SEL)}catch(e){}
    HOST.querySelectorAll('.wv-node').forEach(b=>b.setAttribute('aria-pressed',b.dataset.id===SEL?'true':'false'));
    renderDetail();drawEdges();
    const n=SEL?nodeOf(SEL):null;
    if(SEL!==prev)announce(n?n.id+' detail open: '+n.title+'. '+(PH[n.phase]||n.phase)+'.':'Detail closed');
    // the shell's composer follows the selection: who to address and about what
    try{document.dispatchEvent(new CustomEvent('atman:work-select',{detail:{id:SEL,title:n?n.title:'',phase:n?n.phase:'',to:n?(n.who_kind==='suggested'?'':n.who):'',who_kind:n?n.who_kind:''}}))}catch(e){}
    if(opts.focus&&SEL){const b=HOST.querySelector('.wv-node[data-id="'+SEL+'"]');if(b)b.focus()}
    // stacked layout (≤900px): the detail sits below the graph; bring it into view, Back returns
    if(SEL&&SEL!==prev&&stacked()&&!opts.noScroll){const el=HOST.querySelector('.wv-detail');if(el)el.scrollIntoView({block:'start',behavior:reduced()?'auto':'smooth'})}
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
    // polls replace the subtree: keep focus on the node, the close button, or the Graph/List control
    const active=document.activeElement,inside=active&&HOST.contains(active);
    const focusId=(inside&&active.dataset)?active.dataset.id:'';
    const focusClose=inside&&active.hasAttribute('data-wv-close');
    const focusBack=inside&&active.hasAttribute('data-wv-back');
    const focusMode=(inside&&active.dataset)?active.dataset.wvMode:'';
    const legend='<span class="legend" aria-label="Phases">'+LEGEND.map(p=>'<span class="ph-'+p+'">'+esc(PH[p])+(w.counts&&w.counts[p]?' '+w.counts[p]:'')+'</span>').join('')+'</span>';
    const modes='<span class="wv-modes" role="group" aria-label="Layout"><button type="button" data-wv-mode="graph" aria-pressed="'+(MODE==='graph')+'">Graph</button><button type="button" data-wv-mode="list" aria-pressed="'+(MODE==='list')+'">List</button></span>';
    let main;
    if(w.empty&&!(w.nodes||[]).length)main=emptyHtml(w.empty);
    else main=(MODE==='list'?listHtml(w):graphHtml(w))+(w.empty&&w.empty.kind==='no_edges'?'<div style="margin-top:10px">'+emptyHtml(w.empty)+'</div>':'');
    HOST.innerHTML='<div class="wv">'+objective(w.objective)+'<p class="wv-lead">Follow the work. Select a ticket for its blockers, handoff, and review.</p><div class="wv-bar">'+legend+modes+'</div><div class="wv-body"><div class="wv-main">'+main+'</div><aside class="wv-detail" aria-label="Ticket detail" hidden></aside></div><div class="wv-sr" aria-live="polite" data-wv-live></div></div>';
    if(SEL&&!nodeOf(SEL))SEL='';
    renderDetail();drawEdges();
    if(focusId){const b=HOST.querySelector('.wv-node[data-id="'+focusId+'"]');if(b)b.focus()}
    else if(focusClose){const c=HOST.querySelector('[data-wv-close]');if(c)c.focus()}
    else if(focusBack){const c=HOST.querySelector('[data-wv-back]');if(c)c.focus()}
    else if(focusMode){const c=HOST.querySelector('[data-wv-mode="'+focusMode+'"]');if(c)c.focus()}
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
    const m=e.target.closest('[data-wv-mode]');if(m){setMode(m.dataset.wvMode,true);const c=HOST.querySelector('[data-wv-mode="'+m.dataset.wvMode+'"]');if(c)c.focus();return}
    if(e.target.closest('[data-wv-back]')){backToNode(SEL);return}
    if(e.target.closest('[data-wv-close]')){const id=SEL;select('');backToNode(id);return}
    const n=e.target.closest('.wv-node');if(n)select(n.dataset.id===SEL?'':n.dataset.id);
  });
  document.addEventListener('keydown',e=>{
    if(!HOST||!HOST.contains(e.target))return;
    if(e.key==='Escape'&&SEL){e.preventDefault();const id=SEL;select('');backToNode(id);return}
    const n=e.target.closest('.wv-node');if(!n)return;
    const map={ArrowLeft:[-1,0],ArrowRight:[1,0],ArrowUp:[0,-1],ArrowDown:[0,1]};
    if(!map[e.key])return;
    e.preventDefault();move(n,map[e.key][0],map[e.key][1]);
  });
  window.addEventListener('resize',drawEdges);
  return {render,select,setMode,drawEdges};
})();
"""
