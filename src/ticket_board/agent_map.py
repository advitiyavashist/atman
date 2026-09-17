"""T-1072: the agent map -- one row per seat run, grouped by ticket.

Read-only. Every field comes from a record the board already keeps:

* runs     -- trajectory `run_start` / `run_end` events, plus the
              agents/<seat>.run receipt for a run the log does not have yet;
* role     -- `review_events` (reviewer), the owner / `reserved_for` /
              `dispatch: reserved for` note / `claim` events (author, or fixer
              once a REJECT predates the run), the `review_queue` wake trigger;
* verdict  -- the reviewer's own `review_events` entry inside its run window;
* tokens   -- the harness-reported counts on `run_end`. Absent is `None`
              ("unknown"), never a guess and never 0.

Live state for an open run is the caller's `agent_liveness` answer mapped
onto the map's words; this module never probes a process or a transcript.
"""
from __future__ import annotations

import re
import time
from datetime import datetime, timezone

STATES = ("running", "done", "stalled", "limited", "dead")
ROLES = ("author", "reviewer", "fixer", "unknown")
RECENT_SECS = 24 * 3600
TITLE_MAX = 48
# A verdict typed just after the run's end stamp still belongs to that run.
VERDICT_SLACK_SECS = 120
_DISPATCH_RE = re.compile(r"^dispatch: reserved for (\S+)")
_PR_RE = re.compile(r"(\d+)\s*/?\s*$")


def _epoch(stamp):
    try:
        dt = datetime.fromisoformat(str(stamp).replace("Z", "+00:00"))
    except (ValueError, TypeError):
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.timestamp()


def _int(v):
    return v if isinstance(v, int) and not isinstance(v, bool) else None


def pr_label(t):
    m = _PR_RE.search(str((t or {}).get("pr") or ""))
    return "#" + m.group(1) if m else ""


def clip(text, n=TITLE_MAX):
    t = " ".join((text or "").split())
    return t if len(t) <= n else t[:n - 3].rstrip() + "..."


def _runs(events, receipts):
    """Pair run_start/run_end by run_id (agent#run_no when absent)."""
    runs, order = {}, []

    def slot(seat, run_id, run_no):
        key = run_id or "%s#%s" % (seat, run_no)
        if key not in runs:
            runs[key] = {"seat": seat, "run_id": run_id or "", "run_no": run_no,
                         "ticket": "", "harness": "", "started": "", "ended": "",
                         "open": True, "trigger": []}
            order.append(key)
        return runs[key]

    for e in events:
        kind = e.get("kind")
        if kind not in ("run_start", "run_end") or e.get("dry_run") or not e.get("agent"):
            continue
        r = slot(e["agent"], e.get("run_id"), e.get("run_no"))
        r["ticket"] = r["ticket"] or e.get("ticket") or ""
        r["harness"] = r["harness"] or e.get("harness") or ""
        r["trigger"] = r["trigger"] or list(e.get("trigger") or [])
        r["started"] = r["started"] or e.get("started_at") or (e.get("at") if kind == "run_start" else "")
        if kind == "run_end":
            r.update(open=False, ended=e.get("ended_at") or e.get("at") or "",
                     exit=e.get("exit"), outcome=e.get("outcome"),
                     interrupted=bool(e.get("interrupted") or e.get("timed_out")),
                     duration_s=e.get("duration_s"),
                     # remote bridges report input_/output_tokens
                     tokens_in=_int(e.get("tokens_in", e.get("input_tokens"))),
                     tokens_out=_int(e.get("tokens_out", e.get("output_tokens"))))
    for seat, rec in sorted((receipts or {}).items()):
        if not rec or not rec.get("started"):
            continue
        if not rec.get("run_id"):
            # An older receipt has no run_id: same seat, run number and start.
            t0 = _epoch(rec["started"]) or 0
            same = [k for k in order if runs[k]["seat"] == seat and runs[k]["run_no"] == rec.get("run")
                    and abs((_epoch(runs[k]["started"]) or 0) - t0) <= 5]
            rec = dict(rec, run_id=same[-1]) if same else rec
        r = slot(seat, rec.get("run_id"), rec.get("run"))
        r["ticket"] = r["ticket"] or rec.get("ticket") or ""
        r["started"] = r["started"] or rec["started"]
        r["receipt"] = rec
        if r["open"] and not rec.get("active"):
            r.update(open=False, ended=rec.get("ended") or "", exit=rec.get("rc"),
                     interrupted=bool(rec.get("interrupted")))
    return [runs[k] for k in order]


def _state(run, live, superseded):
    if not run["open"]:
        if run.get("outcome") == "limit":
            return "limited"
        if run.get("interrupted") or run.get("exit") not in (0, None):
            return "dead"
        return "done"
    if superseded:
        return "dead"  # the seat started another run; this one never closed
    lv = live or {}
    st = lv.get("state")
    if st == "limited":
        return "limited"
    if st == "dead" or ("receipt" in run and lv.get("pid_alive") is False):
        return "dead"
    if st == "working" or lv.get("source") == "heartbeat":
        return "running"
    return "stalled"


def _verdicts(t):
    return [e for e in (t or {}).get("review_events") or []
            if isinstance(e, dict) and e.get("kind") in ("accept", "reject")]


def _author_seats(t, claims):
    seats = {(t.get("owner") or "").strip(), (t.get("reserved_for") or "").strip()}
    for n in t.get("notes") or []:
        m = _DISPATCH_RE.match((n or {}).get("text") or "")
        if m:
            seats.add(m.group(1))
    seats.update(claims.get(t.get("id"), ()))
    seats.discard("")
    return seats


def _role(run, t, claims, start):
    if not t:
        return "unknown"
    seat = run["seat"]
    verdicts = _verdicts(t)
    if any(e.get("by") == seat for e in verdicts):
        return "reviewer"
    authors = _author_seats(t, claims)
    if seat in authors:
        rejected = any(e["kind"] == "reject" and (_epoch(e.get("at")) or 0) < (start or 0)
                       for e in verdicts)
        return "fixer" if rejected else "author"
    if "review_queue" in run["trigger"]:
        return "reviewer"
    return "unknown"


def _verdict(run, t, start, end):
    hit = None
    for e in _verdicts(t):
        at = _epoch(e.get("at"))
        if e.get("by") != run["seat"] or at is None or start is None:
            continue
        if start <= at and (end is None or at <= end + VERDICT_SLACK_SECS):
            hit = e
    return {"kind": hit["kind"].upper(), "sha": (hit.get("sha") or "")[:7]} if hit else None


def build(tickets, events, receipts, live, workforce=None, show_all=False, now=None):
    """The map as plain data: `atm agents --json` and the UI panel share it."""
    now = time.time() if now is None else now
    by_id = {t.get("id"): t for t in tickets or [] if t.get("id")}
    claims = {}
    for e in events or []:
        if e.get("kind") == "claim" and e.get("ticket") and e.get("agent"):
            claims.setdefault(e["ticket"], set()).add(e["agent"])
    runs = _runs(events or [], receipts)
    last_start = {}
    for r in runs:
        s = _epoch(r["started"]) or 0
        last_start[r["seat"]] = max(last_start.get(r["seat"], 0), s)
    groups, order = {}, []
    for r in runs:
        start = _epoch(r["started"])
        end = _epoch(r["ended"]) if not r["open"] else None
        superseded = r["open"] and (start or 0) < last_start.get(r["seat"], 0)
        state = _state(r, (live or {}).get(r["seat"]), superseded)
        recent = (end or start or 0) >= now - RECENT_SECS
        if not (show_all or recent or (r["open"] and state in ("running", "stalled", "limited"))):
            continue
        t = by_id.get(r["ticket"]) or {}
        elapsed = r.get("duration_s")
        if not isinstance(elapsed, (int, float)) or isinstance(elapsed, bool):
            elapsed = ((end if end is not None else now) - start) if start else None
        tin, tout = r.get("tokens_in"), r.get("tokens_out")
        known = [n for n in (tin, tout) if n is not None]
        lv = (live or {}).get(r["seat"]) if r["open"] and not superseded else None
        row = {
            "seat": r["seat"],
            "harness": r["harness"] or ((workforce or {}).get(r["seat"]) or {}).get("harness") or "unknown",
            "ticket": r["ticket"], "title": t.get("title", ""), "pr": pr_label(t),
            "role": _role(r, t, claims, start),
            "state": state,
            "liveness": ({"state": lv.get("state", "unknown"), "detail": lv.get("detail", ""),
                          "source": lv.get("source", "none")} if lv else None),
            "verdict": _verdict(r, t, start, end),
            "run_id": r["run_id"], "run_no": r["run_no"],
            "started": r["started"], "ended": r["ended"],
            "elapsed_s": int(max(0, elapsed)) if elapsed is not None else None,
            "tokens": sum(known) if known else None,
            "tokens_in": tin, "tokens_out": tout,
        }
        key = r["ticket"] or ""
        if key not in groups:
            groups[key] = {"ticket": key, "title": t.get("title", ""), "pr": row["pr"],
                           "status": t.get("status", ""), "rows": []}
            order.append(key)
        groups[key]["rows"].append(row)
    out = []
    for key in order:
        g = groups[key]
        rows = sorted(g["rows"], key=lambda x: _epoch(x["started"]) or 0)
        toks = [x["tokens"] for x in rows if x["tokens"] is not None]
        g.update(rows=rows, runs=len(rows),
                 running=sum(1 for x in rows if x["state"] == "running"),
                 elapsed_s=sum(x["elapsed_s"] or 0 for x in rows),
                 tokens=sum(toks) if toks else None,
                 tokens_unknown=len(rows) - len(toks))
        out.append(g)
    out.sort(key=lambda g: (-g["running"], -max(_epoch(x["started"]) or 0 for x in g["rows"])))
    return {"v": 1, "scope": "all" if show_all else "recent",
            "running": sum(g["running"] for g in out),
            "runs": sum(g["runs"] for g in out), "groups": out}


def fmt_elapsed(secs):
    if secs is None:
        return "unknown"
    secs = int(secs)
    if secs < 60:
        return "%ds" % secs
    if secs < 3600:
        return "%dm" % (secs // 60)
    if secs < 86400:
        return "%dh%02dm" % (secs // 3600, secs % 3600 // 60)
    return "%dd%02dh" % (secs // 86400, secs % 86400 // 3600)


def fmt_tokens(n):
    return "unknown" if n is None else "{:,}".format(n)


def render_text(data):
    scope = "all runs" if data["scope"] == "all" else "running + last 24h; --all for history"
    lines = ["%d agents running  (%s)" % (data["running"], scope)]
    if not data["groups"]:
        lines.append("no seat runs recorded")
        return "\n".join(lines)
    for g in data["groups"]:
        head = "%s  %s" % (g["ticket"] or "(no ticket)", clip(g["title"]))
        if g["pr"]:
            head += "  PR %s" % g["pr"]
        toks = fmt_tokens(g["tokens"]) + (" (+%d unknown)" % g["tokens_unknown"]
                                           if g["tokens"] is not None and g["tokens_unknown"] else "")
        lines += ["", head.rstrip(),
                  "  %d run%s · %s · tokens %s" % (g["runs"], "" if g["runs"] == 1 else "s",
                                                 fmt_elapsed(g["elapsed_s"]), toks),
                  "  %-14s %-8s %-8s %-8s %-14s %-8s %s" % (
                      "seat", "harness", "role", "state", "verdict", "elapsed", "tokens")]
        for x in g["rows"]:
            v = x["verdict"]
            lines.append("  %-14s %-8s %-8s %-8s %-14s %-8s %s" % (
                x["seat"][:14], x["harness"][:8], x["role"], x["state"],
                ("%s %s" % (v["kind"], v["sha"])) if v else "-",
                fmt_elapsed(x["elapsed_s"]), fmt_tokens(x["tokens"])))
    return "\n".join(lines)
