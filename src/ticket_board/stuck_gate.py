"""T-014: 'stuck' is earned over N rounds of evidence, then escalates.

T-976 sat in review six weeks while its seat posted 'stuck' four times in
prose and nobody acted. Prose is not a state: nothing counted the reports,
nothing routed them, nothing noticed they went unanswered. This module is the
state machine that replaces it.

A seat reports one *round* per `atm stuck <id> --obstacle K --evidence E`.
Rounds count toward escalation only while they are consecutive reports of the
SAME obstacle, each with new evidence, each at least a round gap after the
last. At N rounds the ticket escalates: it goes BLOCKED, the planner and CEO
are messaged, and it stays on the operator view until someone resolves it.
An escalation nobody acknowledges within the SLA turns into a CRIT health
item, which wakes the master on every watch poll until it is answered.

Pure functions over the ticket's ``stuck`` record; tickets.py does the I/O.
"""
from __future__ import annotations

import os
import re
from datetime import datetime, timezone

DEFAULT_ROUNDS = 2
DEFAULT_ROUND_GAP_S = 600.0
DEFAULT_ACK_HOURS = 24.0
MIN_EVIDENCE_CHARS = 20
HISTORY_KEEP = 5

# reporting: rounds accumulating, ticket status untouched
# escalated: N rounds reached, planner/CEO messaged, nobody has answered
# acknowledged: a different seat answered; still on the operator view
# resolved: closed out; off every view (kept in history)
OPEN_STATES = ("escalated", "acknowledged")


def _env_num(environ, key, default, floor):
    env = environ if environ is not None else os.environ
    raw = (env.get(key) or "").strip()
    try:
        val = float(raw) if raw else float(default)
    except ValueError:
        val = float(default)
    return max(floor, val)


def rounds_required(environ=None):
    return int(_env_num(environ, "TICKETS_STUCK_ROUNDS", DEFAULT_ROUNDS, 1))


def round_gap_s(environ=None):
    return _env_num(environ, "TICKETS_STUCK_ROUND_SECS", DEFAULT_ROUND_GAP_S, 0)


def ack_hours(environ=None):
    return _env_num(environ, "TICKETS_STUCK_ACK_HOURS", DEFAULT_ACK_HOURS, 0)


def _parse(at):
    try:
        d = datetime.fromisoformat(str(at).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    return d if d.tzinfo else d.replace(tzinfo=timezone.utc)


def _iso(d):
    return d.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def obstacle_key(text):
    """Same obstacle, however it is capitalised or spaced."""
    return re.sub(r"\s+", " ", (text or "").strip().lower())


def _evidence_key(text):
    return re.sub(r"\W+", " ", (text or "").lower()).strip()


def is_open(stuck):
    return bool(stuck) and stuck.get("state") in OPEN_STATES


class Refused(Exception):
    """A report that does not count as a round. The message says why."""


def _archive(stuck):
    """Close the current cycle into history; return a fresh record."""
    hist = list(stuck.get("history") or [])
    if stuck.get("rounds"):
        row = {k: stuck.get(k) for k in (
            "state", "obstacle", "escalated_at", "acked_by", "resolved_by",
            "resolved_at") if stuck.get(k)}
        row["rounds"] = len(stuck["rounds"])
        hist.append(row)
    return {"state": "reporting", "rounds": [], "history": hist[-HISTORY_KEEP:]}


def record_round(stuck, by, obstacle, evidence, now_dt, environ=None):
    """Apply one stuck report. Returns (new_record, verdict).

    verdict = {"round": k, "needed": N, "escalate": bool, "reset": str,
               "followup": bool}. Raises Refused when the report cannot count.
    """
    need = rounds_required(environ)
    obstacle = (obstacle or "").strip()
    evidence = (evidence or "").strip()
    if not obstacle:
        raise Refused("stuck needs --obstacle: a short name for what blocks you, "
                      "the same words each round")
    if len(evidence) < MIN_EVIDENCE_CHARS:
        raise Refused("stuck needs --evidence of at least %d characters: what you "
                      "tried THIS round and what it returned" % MIN_EVIDENCE_CHARS)
    cur = dict(stuck or {})
    cur.setdefault("rounds", [])
    cur.setdefault("history", [])
    if cur.get("state") == "resolved":
        cur = _archive(cur)
    rounds = cur["rounds"]
    reset = ""
    if rounds and obstacle_key(rounds[-1].get("obstacle")) != obstacle_key(obstacle):
        if is_open(cur):
            raise Refused("%s is already escalated on %r; resolve that first "
                          "(atm stuck <id> --resolve --notes ...) before reporting "
                          "a different obstacle" % (cur.get("ticket") or "this ticket",
                                                    rounds[-1].get("obstacle")))
        reset = "obstacle changed from %r; counting restarts at round 1" % (
            rounds[-1].get("obstacle"))
        cur = _archive(cur)
        rounds = cur["rounds"]
    if rounds:
        last = rounds[-1]
        if _evidence_key(last.get("evidence")) == _evidence_key(evidence):
            raise Refused("round %d evidence repeats round %d word for word; a round "
                          "needs new evidence (what you tried since)" % (
                              len(rounds) + 1, len(rounds)))
        gap = round_gap_s(environ)
        prev = _parse(last.get("at"))
        if prev is not None and (now_dt - prev).total_seconds() < gap:
            wait = gap - (now_dt - prev).total_seconds()
            raise Refused("round %d is %ds after round %d; a round is a fresh attempt, "
                          "at least %ds apart -- try again in %ds" % (
                              len(rounds) + 1, int((now_dt - prev).total_seconds()),
                              len(rounds), int(gap), int(wait) + 1))
    rounds.append({"at": _iso(now_dt), "by": by, "obstacle": obstacle,
                   "evidence": evidence[:2000]})
    cur["rounds"] = rounds
    cur["obstacle"] = obstacle
    followup = is_open(cur)
    escalate = False
    if not followup:
        cur["state"] = "reporting"
        if len(rounds) >= need:
            escalate = True
            cur["state"] = "escalated"
            cur["escalated_at"] = _iso(now_dt)
    return cur, {"round": len(rounds), "needed": need, "escalate": escalate,
                 "reset": reset, "followup": followup}


def acknowledge(stuck, by, note, now_dt):
    if not is_open(stuck):
        raise Refused("nothing to acknowledge: no open stuck escalation")
    reporters = set(r.get("by") for r in stuck.get("rounds") or [])
    if by in reporters:
        raise Refused("%s reported this obstacle; the acknowledgement must come "
                      "from a different seat (planner, CEO or CoS)" % by)
    if not (note or "").strip():
        raise Refused('ack needs --notes "what you will do about it"')
    cur = dict(stuck)
    cur["state"] = "acknowledged"
    cur["acked_by"] = by
    cur["acked_at"] = _iso(now_dt)
    cur["ack_note"] = note.strip()[:1000]
    return cur


def resolve(stuck, by, note, now_dt):
    if not stuck or stuck.get("state") in (None, "resolved") or not stuck.get("rounds"):
        raise Refused("nothing to resolve: no stuck report on this ticket")
    if not (note or "").strip():
        raise Refused('resolve needs --notes "what cleared the obstacle"')
    cur = dict(stuck)
    cur["was"] = cur.get("state")
    cur["state"] = "resolved"
    cur["resolved_by"] = by
    cur["resolved_at"] = _iso(now_dt)
    cur["resolve_note"] = note.strip()[:1000]
    return cur


def escalation_view(ticket, now_dt, environ=None):
    """What an operator view shows for one ticket, or None.

    Done tickets drop off: the work closed, the obstacle no longer holds it.
    """
    stuck = (ticket or {}).get("stuck") or {}
    if not is_open(stuck) or ticket.get("status") == "done":
        return None
    at = _parse(stuck.get("escalated_at"))
    age_h = (now_dt - at).total_seconds() / 3600.0 if at else 0.0
    sla = ack_hours(environ)
    acked = stuck.get("state") == "acknowledged"
    rounds = stuck.get("rounds") or []
    last = rounds[-1] if rounds else {}
    return {"id": ticket.get("id"), "state": stuck.get("state"),
            "obstacle": stuck.get("obstacle") or "",
            "rounds": len(rounds), "escalated_at": stuck.get("escalated_at") or "",
            "age_h": round(age_h, 2), "sla_h": sla,
            "overdue": (not acked) and age_h > sla,
            "acked_by": stuck.get("acked_by") or "",
            "reporter": last.get("by") or "", "evidence": (last.get("evidence") or "")[:400],
            "escalated_to": list(stuck.get("escalated_to") or [])}


def escalation_targets(master, aliases, reporter):
    """Planner (the master), CEO and CoS -- deduplicated, never the reporter."""
    m = master or {}
    planner = (m.get("owner") or "").strip()
    ceo = ((aliases or {}).get("ceo") or planner or "").strip()
    out = []
    for who in (planner, ceo, (m.get("cos") or "").strip()):
        if who and who != reporter and who not in out:
            out.append(who)
    return out


def escalation_body(ticket, stuck, rounds, evidence, environ=None):
    return ("stuck: %s escalated after %d rounds on %r -- %s. Latest evidence: %s. "
            "Answer with `atm stuck %s --ack --notes \"...\"`; unacknowledged after "
            "%gh it goes CRIT." % (ticket["id"], rounds, stuck.get("obstacle"),
                                    (ticket.get("title") or "")[:80], evidence.strip()[:300],
                                    ticket["id"], ack_hours(environ)))


def _fmt_h(h):
    return "%dm" % int(round(h * 60)) if h < 1 else "%.1fh" % h


def health_items(tickets, now_dt, environ=None):
    """[(severity, message, fix)] for health(). CRIT once unacknowledged past
    the SLA -- CRIT health is a master wake gate, so it re-wakes until answered."""
    out = []
    for t in tickets:
        sv = escalation_view(t, now_dt, environ)
        if not sv:
            continue
        ack = 'atm stuck %s --ack --notes "what you will do"' % t["id"]
        if sv["overdue"]:
            out.append(("CRIT", "%s stuck on %r escalated %s ago and nobody acknowledged it" % (
                t["id"], sv["obstacle"], _fmt_h(sv["age_h"])), ack))
        else:
            out.append(("WARN", "%s stuck on %r (%s, %d rounds%s)" % (
                t["id"], sv["obstacle"], sv["state"], sv["rounds"],
                (", acked by %s" % sv["acked_by"]) if sv["acked_by"] else ""),
                ('atm stuck %s --resolve --notes "what cleared it"' % t["id"])
                if sv["acked_by"] else ack))
    return out


def apply(ticket, by, args, now_dt, environ=None):
    """Run one `atm stuck` invocation against a ticket dict, in place.

    Returns {"print": [lines], "messages": [(to, text, task)]}; the caller
    saves the ticket and posts the messages. Raises Refused. Shared by the
    root tickets.py and the packaged CLI so the two copies cannot drift.
    `master`/`aliases` ride on args as plain dicts.
    """
    t = ticket
    cur = t.get("stuck") or {}
    stamp = _iso(now_dt)
    out = {"print": [], "messages": []}
    if getattr(args, "ack", False):
        nxt = acknowledge(cur, by, getattr(args, "notes", ""), now_dt)
        t["stuck"] = nxt
        t.setdefault("notes", []).append({"by": by, "at": stamp, "text": "STUCK ACK (%s): %s" % (
            nxt.get("obstacle"), nxt["ack_note"])})
        for r in sorted(set(x.get("by") for x in nxt.get("rounds") or [] if x.get("by"))):
            out["messages"].append((r, "%s stuck on %r acknowledged by %s: %s" % (
                t["id"], nxt.get("obstacle"), by, nxt["ack_note"]), False))
        out["print"].append("%s stuck escalation acknowledged; it stays on the operator view "
                            "until `atm stuck %s --resolve --notes ...`" % (t["id"], t["id"]))
        return out
    if getattr(args, "resolve", False):
        nxt = resolve(cur, by, getattr(args, "notes", ""), now_dt)
        t["stuck"] = nxt
        back = ""
        if t.get("status") == "blocked" and nxt.get("was") in OPEN_STATES:
            back = nxt.get("status_before") or ("claimed" if t.get("owner") else "open")
            t["status"] = back
        t.setdefault("notes", []).append({"by": by, "at": stamp, "text": "STUCK RESOLVED (%s): %s" % (
            nxt.get("obstacle"), nxt["resolve_note"])})
        out["print"].append("%s stuck resolved%s" % (t["id"], (" -> %s" % back) if back else ""))
        out["back"] = back
        return out
    obstacle = getattr(args, "obstacle", "") or ""
    evidence = getattr(args, "evidence", "") or ""
    nxt, v = record_round(cur, by, obstacle, evidence, now_dt, environ)
    nxt["ticket"] = t["id"]
    t.setdefault("notes", []).append({"by": by, "at": stamp, "text": "STUCK round %d/%d (%s): %s" % (
        v["round"], v["needed"], obstacle.strip(), evidence.strip()[:600])})
    if v["reset"]:
        out["print"].append(v["reset"])
    out["verdict"] = v
    if v["escalate"]:
        targets = escalation_targets(getattr(args, "master", None),
                                     getattr(args, "aliases", None), by)
        nxt["escalated_to"] = targets
        nxt["status_before"] = t["status"] if t.get("status") != "blocked" else (
            "claimed" if t.get("owner") else "open")
        t["status"] = "blocked"
        body = escalation_body(t, nxt, v["round"], evidence, environ)
        if targets:
            out["messages"].extend((r, body, True) for r in targets)
        else:
            out["messages"].append(("", body + " (no planner/CEO seat on this board)", True))
        out["print"].append("%s ESCALATED after %d rounds -> BLOCKED; told %s" % (
            t["id"], v["round"], ", ".join(targets) or "the whole board (no planner/CEO seat)"))
    elif v["followup"]:
        out["print"].append("%s: evidence added to the open escalation (round %d)" % (
            t["id"], v["round"]))
    else:
        out["print"].append("%s: stuck round %d of %d recorded -- keep working it; if the same "
                            "obstacle still holds next round, report again with new evidence"
                            % (t["id"], v["round"], v["needed"]))
    t["stuck"] = nxt
    return out


def status_word_refusal(tid, environ=None):
    return ("RULE: stuck is earned, not set: `atm stuck %s --obstacle \"...\" "
            "--evidence \"what you tried this round\"` once per round; after %d "
            "rounds on the same obstacle it escalates. (`atm block` is for a hard "
            "external blocker.)" % (tid, rounds_required(environ)))
