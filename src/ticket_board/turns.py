"""T-312: turns-to-done aggregator.

A TURN is one completed watch run (`run_start` -> `run_end`). A ticket's
turns are those runs from the first claim to the final done; reopens add
their runs. Backfill never recorded runs, so `turns` is JSON null — never 0.

`--json` shape is frozen here and in docs/turns.md. The optimizer (T-313)
reads it; do not rename keys.
"""

from __future__ import annotations

import glob
import json
import os
import sys
from datetime import datetime, timezone
from statistics import mean, median

TURNS_JSON_V = 1


class TrajectoryParseError(Exception):
    """Malformed trajectories.jsonl line — must not be silently skipped."""

    def __init__(self, path, line_no, detail="invalid JSON"):
        self.path = path
        self.line_no = line_no
        self.detail = detail
        super().__init__("%s:%d: %s" % (path, line_no, detail))

ROW_KEYS = ("ticket", "owner", "model", "turns", "wall_clock_s", "reopens",
            "stuck", "outcome", "cost_usd", "tokens_in", "tokens_out")


def trajectories_path(board):
    return os.path.join(board, "trajectories.jsonl")


def load_trajectory_events(board, include_archives=True):
    """Oldest-first. Archives included: a truncated history would corrupt turns."""
    paths = [trajectories_path(board)]
    if include_archives:
        paths = sorted(glob.glob(os.path.join(board, "trajectories.*.jsonl"))) + paths
    out = []
    for p in paths:
        try:
            with open(p) as f:
                for line_no, ln in enumerate(f, 1):
                    ln = ln.strip()
                    if not ln:
                        continue
                    try:
                        rec = json.loads(ln)
                    except ValueError as exc:
                        raise TrajectoryParseError(p, line_no, str(exc) or "invalid JSON") from exc
                    if isinstance(rec, dict):
                        out.append(rec)
        except IOError:
            pass
    return out


def _parse_at(stamp):
    if not stamp:
        return None
    try:
        return datetime.strptime(stamp, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
    except (ValueError, TypeError):
        return None


def _seconds_between(a, b):
    da, db = _parse_at(a), _parse_at(b)
    if not da or not db:
        return None
    return max(0.0, (db - da).total_seconds())


def _stuck_count(messages, ticket):
    n = 0
    for m in messages or []:
        if m.get("re") != ticket:
            continue
        text = str(m.get("text") or "").lstrip().lower()
        if text.startswith("stuck"):
            n += 1
    return n


def _filter_events(events, ticket="", agent="", since="", until=""):
    out = []
    for e in events:
        if ticket and e.get("ticket") != ticket:
            continue
        if agent and e.get("agent") != agent:
            continue
        at = e.get("at", "")
        if since and at < since:
            continue
        if until and at > until:
            continue
        out.append(e)
    return out


def _group(events):
    by = {}
    for e in events:
        tid = e.get("ticket")
        if not tid:
            continue
        by.setdefault(tid, []).append(e)
    return by


def _ticket_index(tickets):
    return {t.get("id"): t for t in (tickets or []) if t.get("id")}


def _outcome(evs, ticket):
    last = ""
    for e in evs:
        k = e.get("kind")
        if k in ("done", "merge", "review", "block", "reopen"):
            last = e.get("outcome") or k
    if last:
        return last
    st = (ticket or {}).get("status") or ""
    return {
        "done": "done",
        "review": "review",
        "blocked": "blocked",
        "claimed": "in-progress",
        "open": "open",
    }.get(st, st or None)


def _owner(evs, ticket):
    if ticket and ticket.get("owner"):
        return ticket["owner"]
    last = None
    for e in evs:
        if e.get("kind") in ("claim", "run_end", "review", "done") and e.get("agent"):
            last = e["agent"]
    return last


def _model(evs, owner, workforce):
    last = None
    for e in evs:
        if e.get("model"):
            last = e["model"]
    if last:
        return last
    if owner and workforce:
        m = (workforce.get(owner) or {}).get("model")
        return m or None
    return None


def _measured_turns(evs):
    """Count completed watch runs. Unknown (no run_end) -> None, never 0."""
    n = 0
    saw_run = False
    for e in evs:
        if e.get("kind") == "run_end":
            saw_run = True
            n += 1
    if not saw_run:
        return None
    return n


def _measured_cost(evs):
    """Total harness-reported cost for a ticket. Unknown -> None, never 0.

    A ticket whose runs reported no cost is UNMEASURED, not free: on this
    board that is the normal case, because the default watch commands do not
    ask the harness for a JSON output format (T-396). Rendering it as $0.00
    would make the cheapest-looking agent the one we know least about.
    """
    total, known = 0.0, False
    for e in evs:
        if e.get("kind") != "run_end":
            continue
        c = e.get("cost_usd")
        if isinstance(c, (int, float)) and not isinstance(c, bool):
            total += float(c)
            known = True
    return round(total, 6) if known else None


def _measured_tokens(evs, field):
    """Summed token count for a ticket, or None when no run reported one."""
    total, known = 0, False
    for e in evs:
        if e.get("kind") != "run_end":
            continue
        n = e.get(field)
        if isinstance(n, int) and not isinstance(n, bool):
            total += n
            known = True
    return total if known else None


def _wall_clock_s(evs):
    claims = [e.get("at") for e in evs if e.get("kind") == "claim" and e.get("at")]
    dones = [e.get("at") for e in evs if e.get("kind") in ("done", "merge") and e.get("at")]
    ats = [e.get("at") for e in evs if e.get("at")]
    start = min(claims) if claims else (min(ats) if ats else None)
    if not start:
        return None
    end = max(dones) if dones else (max(ats) if ats else None)
    return _seconds_between(start, end)


def _agg(values):
    nums = [v for v in values if isinstance(v, (int, float))]
    if not nums:
        return {"n": 0, "mean": None, "median": None}
    return {
        "n": len(nums),
        "mean": round(mean(nums), 4),
        "median": round(median(nums), 4),
    }


def build_turns_report(events, tickets=None, workforce=None, messages=None,
                       ticket="", agent="", model="", epic="", since="", until=""):
    """Return the frozen --json object."""
    tickets = tickets or []
    workforce = workforce or {}
    messages = messages or []
    idx = _ticket_index(tickets)
    sel = _filter_events(events, ticket=ticket, agent=agent, since=since, until=until)
    rows = []
    for tid, evs in sorted(_group(sel).items()):
        t = idx.get(tid) or {}
        if epic and t.get("epic") != epic:
            continue
        owner = _owner(evs, t)
        mdl = _model(evs, owner, workforce)
        if model and mdl != model:
            continue
        row = {
            "ticket": tid,
            "owner": owner,
            "model": mdl,
            "turns": _measured_turns(evs),
            "wall_clock_s": _wall_clock_s(evs),
            "reopens": sum(1 for e in evs if e.get("kind") == "reopen"),
            "stuck": _stuck_count(messages, tid),
            "outcome": _outcome(evs, t),
            "cost_usd": _measured_cost(evs),
            "tokens_in": _measured_tokens(evs, "tokens_in"),
            "tokens_out": _measured_tokens(evs, "tokens_out"),
        }
        # Extra fields used only for aggregates; stripped from --json rows.
        row["_role"] = t.get("role") or None
        row["_priority"] = t.get("priority") if t.get("priority") is not None else None
        rows.append(row)

    measured = [r["turns"] for r in rows if r["turns"] is not None]
    overall = _agg(measured)
    overall["n_unmeasured"] = sum(1 for r in rows if r["turns"] is None)
    costed = [r["cost_usd"] for r in rows if r["cost_usd"] is not None]
    cost_agg = _agg(costed)
    cost_agg["total"] = round(sum(costed), 6) if costed else None
    cost_agg["n_unmeasured"] = sum(1 for r in rows if r["cost_usd"] is None)

    def group_agg(keyfn, label):
        buckets = {}
        for r in rows:
            if r["turns"] is None:
                continue
            k = keyfn(r)
            if k is None or k == "":
                continue
            buckets.setdefault(k, []).append(r["turns"])
        out = []
        for k in sorted(buckets, key=lambda x: (str(type(x)), str(x))):
            rec = _agg(buckets[k])
            rec[label] = k
            out.append(rec)
        return out

    def cost_group(keyfn, label):
        """Same shape as group_agg, over cost instead of turns. Kept separate
        because a row can be measured for one and unmeasured for the other."""
        buckets = {}
        for r in rows:
            if r["cost_usd"] is None:
                continue
            k = keyfn(r)
            if k is None or k == "":
                continue
            buckets.setdefault(k, []).append(r["cost_usd"])
        out = []
        for k in sorted(buckets, key=lambda x: (str(type(x)), str(x))):
            rec = _agg(buckets[k])
            rec["total"] = round(sum(buckets[k]), 6)
            rec[label] = k
            out.append(rec)
        return out

    public_rows = [{k: r[k] for k in ROW_KEYS} for r in rows]
    return {
        "v": TURNS_JSON_V,
        "tickets": public_rows,
        "aggregates": {
            "mean": overall["mean"],
            "median": overall["median"],
            "n": overall["n"],
            "n_unmeasured": overall["n_unmeasured"],
            "by_agent": group_agg(lambda r: r.get("owner"), "agent"),
            "by_model": group_agg(lambda r: r.get("model"), "model"),
            "by_role": group_agg(lambda r: r.get("_role"), "role"),
            "by_priority": group_agg(lambda r: r.get("_priority"), "priority"),
            "cost": cost_agg,
            "cost_by_agent": cost_group(lambda r: r.get("owner"), "agent"),
            "cost_by_model": cost_group(lambda r: r.get("model"), "model"),
        },
    }


def _fmt_wall(seconds):
    if seconds is None:
        return "-"
    h = seconds / 3600.0
    if h < 1:
        return "%dm" % int(round(h * 60))
    if h < 48:
        return "%.1fh" % h
    return "%.1fd" % (h / 24.0)


def _fmt_cost(usd):
    """'-' means UNMEASURED, and it is not $0.00. See _measured_cost."""
    return "-" if usd is None else ("$%.4f" % usd)


def _fmt_tokens(n):
    if n is None:
        return "-"
    if n >= 1000000:
        return "%.1fM" % (n / 1000000.0)
    if n >= 1000:
        return "%.1fk" % (n / 1000.0)
    return str(n)


def render_turns_table(report):
    lines = []
    lines.append("%-8s %-16s %-12s %5s %8s %10s %8s %8s %7s %5s  %s" % (
        "ticket", "owner", "model", "turns", "wall", "cost", "tok in",
        "tok out", "reopens", "stuck", "outcome"))
    for r in report.get("tickets") or []:
        turns = r.get("turns")
        lines.append("%-8s %-16s %-12s %5s %8s %10s %8s %8s %7d %5d  %s" % (
            r.get("ticket") or "-",
            (r.get("owner") or "-")[:16],
            (r.get("model") or "-")[:12],
            "-" if turns is None else str(turns),
            _fmt_wall(r.get("wall_clock_s")),
            _fmt_cost(r.get("cost_usd")),
            _fmt_tokens(r.get("tokens_in")),
            _fmt_tokens(r.get("tokens_out")),
            int(r.get("reopens") or 0),
            int(r.get("stuck") or 0),
            r.get("outcome") or "-",
        ))
    agg = report.get("aggregates") or {}
    lines.append("")
    lines.append("measured %d ticket(s); unmeasured (no run_end, typically backfill) %d" % (
        agg.get("n") or 0, agg.get("n_unmeasured") or 0))
    mean_v, med_v = agg.get("mean"), agg.get("median")
    lines.append("turns  mean %s  median %s" % (
        "-" if mean_v is None else ("%.2f" % mean_v),
        "-" if med_v is None else ("%.2f" % med_v)))

    def dump(title, rows, key):
        if not rows:
            return
        lines.append("%s:" % title)
        for rec in rows:
            lines.append("  %-16s n=%d  mean %s  median %s" % (
                str(rec.get(key) or "-")[:16], rec.get("n") or 0,
                "-" if rec.get("mean") is None else ("%.2f" % rec["mean"]),
                "-" if rec.get("median") is None else ("%.2f" % rec["median"])))

    dump("per agent", agg.get("by_agent"), "agent")
    dump("per model", agg.get("by_model"), "model")
    dump("per role", agg.get("by_role"), "role")
    dump("per priority", agg.get("by_priority"), "priority")

    cost = agg.get("cost") or {}
    lines.append("")
    lines.append("cost   measured %d ticket(s); unmeasured %d  total %s  mean %s  median %s" % (
        cost.get("n") or 0, cost.get("n_unmeasured") or 0,
        _fmt_cost(cost.get("total")), _fmt_cost(cost.get("mean")),
        _fmt_cost(cost.get("median"))))
    lines.append("'-' is UNMEASURED, not $0.00: a harness reports a cost only when it was "
                 "asked for a JSON output format, and no cost is ever estimated.")

    def dump_cost(title, rows, key):
        if not rows:
            return
        lines.append("%s:" % title)
        for rec in rows:
            lines.append("  %-16s n=%d  total %s  mean %s" % (
                str(rec.get(key) or "-")[:16], rec.get("n") or 0,
                _fmt_cost(rec.get("total")), _fmt_cost(rec.get("mean"))))

    dump_cost("cost per agent", agg.get("cost_by_agent"), "agent")
    dump_cost("cost per model", agg.get("cost_by_model"), "model")
    return "\n".join(lines)


def cmd_turns(a, board, load_all, load_workforce, load_messages):
    try:
        events = load_trajectory_events(board)
    except TrajectoryParseError as exc:
        sys.exit(str(exc))
    report = build_turns_report(
        events,
        tickets=load_all(board),
        workforce=load_workforce(board),
        messages=load_messages(board) if load_messages else [],
        ticket=getattr(a, "ticket", "") or "",
        agent=getattr(a, "agent", "") or "",
        model=getattr(a, "model", "") or "",
        epic=getattr(a, "epic", "") or "",
        since=getattr(a, "since", "") or "",
        until=getattr(a, "until", "") or "",
    )
    if getattr(a, "json", False):
        print(json.dumps(report, indent=2))
        return
    if not report["tickets"]:
        print("no turn data yet (%s)" % trajectories_path(board))
        print("a TURN is one watch run (run_start -> run_end); backfill has no runs, so those tickets show turns as '-' / null")
        return
    print(render_turns_table(report))
