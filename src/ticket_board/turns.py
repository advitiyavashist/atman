"""T-312: turns-to-done aggregator.

A TURN is one completed watch run (`run_start` -> `run_end`) that wrote
the bound ticket. T-425/T-481: a run increments `turns` only when THAT run
recorded a bound-ticket write (claim, update, review, done, block, reopen,
or msg --re that ticket). Pairing key is `run_id` if present, else
`(agent, run_no)`. Fail-exit, timeout, and session-limit with no write stay
in jsonl but do not count. Only events with neither `run_id` nor `run_no`
(pre-T-425) still count every `run_end`. A `run_no`-only `run_end` is unpairable — and takes the same
legacy count path — only when no `run_no`-bearing write in that
trajectory is at or before that `run_end`'s `at` (T-500: not
trajectory-wide). Decided from the log, not a date. Backfill never
recorded runs, so `turns` is JSON null — never 0.

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

from ticket_board.prices import estimate_run_end_cost, fmt_cost_cell, ticket_cost_fields

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


def _filter_events(events, ticket="", agent="", since="", until="",
                   epic="", ticket_index=None):
    """Filter trajectory events. --agent/--since/--until/--epic narrow sel;
    --epic excludes unbound runs (no ticket => no epic). --model is applied
    separately: ticket rows use the resolved ticket model; per-run cost
    aggregates use each run_end's own model."""
    idx = ticket_index or {}
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
        if epic:
            tid = e.get("ticket")
            if not tid:
                continue
            if (idx.get(tid) or {}).get("epic") != epic:
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


# Bound-ticket writes credited to a run_id. Broadcast msg (no ticket) is not.
_BOUND_WRITE_KINDS = frozenset(
    ("claim", "update", "review", "done", "block", "reopen"))


def _is_bound_write(e, ticket):
    if not ticket or e.get("ticket") != ticket:
        return False
    kind = e.get("kind")
    return kind in _BOUND_WRITE_KINDS or kind == "msg"


def _as_run_no(value):
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _end_flag_key(end):
    """T-481: run_id if present, else (agent, run_no). None = pre-T-425."""
    rid = end.get("run_id")
    if rid:
        return ("id", rid)
    run_no = _as_run_no(end.get("run_no"))
    agent = end.get("agent")
    if agent and run_no is not None:
        return ("no", agent, run_no)
    return None


def _event_flag_keys(e):
    """Index a write under every key a run_end might use to find it."""
    keys = []
    rid = e.get("run_id")
    if rid:
        keys.append(("id", rid))
    run_no = _as_run_no(e.get("run_no"))
    agent = e.get("agent")
    if agent and run_no is not None:
        keys.append(("no", agent, run_no))
    return keys


def _run_no_write_at_or_before(end, evs):
    """True if some bound write bearing run_no is at or before this run_end.

    T-500: the FLAG's unpairable test is time-aware, not trajectory-wide.
    A later stamped write must not pull earlier run_no-only ends into FLAG.
    Missing stamps fall back to log order (oldest-first): a write after this
    run_end in the list does not qualify. Do not require the write to bear
    this run_end's own (agent, run_no) — an idle run has none (T-425).
    """
    end_at = end.get("at")
    seen_end = False
    tid = end.get("ticket")
    for e in evs:
        if e is end:
            seen_end = True
        if not _is_bound_write(e, tid):
            continue
        if _as_run_no(e.get("run_no")) is None:
            continue
        write_at = e.get("at")
        if end_at and write_at:
            if write_at <= end_at:
                return True
            continue
        if not seen_end:
            return True
    return False


def _run_is_productive(end, writes_by_key, evs):
    """FLAG: increment iff THAT run wrote the bound ticket.

    Exit=1 / timed_out / session-limit with zero writes is idle (HB87/HB88).
    No content grep. Broadcasts without --re never increment.
    Missing bound_write must not default to "count" when a pairing key exists
    and that key type appears on writes *at or before this run_end*. A
    run_no-only run_end is unpairable when no run_no-bearing write is at or
    before its `at` (historical writers never stamped it yet) — then take
    the pre-T-425 count path, not idle.
    """
    if end.get("bound_write"):
        return True
    key = _end_flag_key(end)
    if not key:
        return True  # pre-T-425 jsonl: neither run_id nor run_no
    if key[0] == "no" and not _run_no_write_at_or_before(end, evs):
        return True  # unpairable era at this timestamp: key cannot match
    tid = end.get("ticket")
    return any(_is_bound_write(w, tid) for w in writes_by_key.get(key) or [])


def _measured_turns(evs):
    """Count productive completed watch runs. Unknown -> None, never 0.

    Idle and fail-exit `run_end` rows stay in jsonl. If none of a ticket's
    runs wrote the bound ticket, `turns` stays null so n_measured does not
    rise.
    """
    writes_by_key = {}
    saw_run = False
    n = 0
    for e in evs:
        if _is_bound_write(e, e.get("ticket")):
            for key in _event_flag_keys(e):
                writes_by_key.setdefault(key, []).append(e)
    for e in evs:
        if e.get("kind") != "run_end":
            continue
        saw_run = True
        if _run_is_productive(e, writes_by_key, evs):
            n += 1
    if not saw_run or n == 0:
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
    sel = _filter_events(
        events, ticket=ticket, agent=agent, since=since, until=until,
        epic=epic, ticket_index=idx)
    rows = []
    for tid, evs in sorted(_group(sel).items()):
        t = idx.get(tid) or {}
        owner = _owner(evs, t)
        mdl = _model(evs, owner, workforce)
        if model and mdl != model:
            continue
        harness_cost = _measured_cost(evs)
        cost_usd, cost_usd_est, cost_source, cost_price_as_of = ticket_cost_fields(
            evs, harness_cost)
        row = {
            "ticket": tid,
            "owner": owner,
            "model": mdl,
            "turns": _measured_turns(evs),
            "wall_clock_s": _wall_clock_s(evs),
            "reopens": sum(1 for e in evs if e.get("kind") == "reopen"),
            "stuck": _stuck_count(messages, tid),
            "outcome": _outcome(evs, t),
            "cost_usd": cost_usd,
            "cost_usd_est": cost_usd_est,
            "cost_source": cost_source,
            "cost_price_as_of": cost_price_as_of,
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
    ested = [r["cost_usd_est"] for r in rows if r.get("cost_usd_est") is not None]
    cost_est_agg = _agg(ested)
    cost_est_agg["total"] = round(sum(ested), 6) if ested else None
    cost_est_agg["n_unmeasured"] = sum(
        1 for r in rows if r.get("cost_usd_est") is None and r.get("cost_usd") is None)

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

    def cost_group(keyfn, label, field="cost_usd"):
        """Same shape as group_agg, over cost instead of turns. Kept separate
        because a row can be measured for one and unmeasured for the other."""
        buckets = {}
        for r in rows:
            if r.get(field) is None:
                continue
            k = keyfn(r)
            if k is None or k == "":
                continue
            buckets.setdefault(k, []).append(r[field])
        out = []
        for k in sorted(buckets, key=lambda x: (str(type(x)), str(x))):
            rec = _agg(buckets[k])
            rec["total"] = round(sum(buckets[k]), 6)
            rec[label] = k
            out.append(rec)
        return out

    def cost_est_by_run_model(events, run_model=""):
        """Per-run estimates grouped by the run_end model, not the ticket row."""
        buckets = {}
        for e in events:
            if e.get("kind") != "run_end":
                continue
            m = e.get("model")
            if run_model and m != run_model:
                continue
            est, _, _ = estimate_run_end_cost(e)
            if est is None:
                continue
            if not m:
                continue
            buckets.setdefault(m, []).append(est)
        out = []
        for k in sorted(buckets, key=lambda x: (str(type(x)), str(x))):
            rec = _agg(buckets[k])
            rec["total"] = round(sum(buckets[k]), 6)
            rec["model"] = k
            out.append(rec)
        return out

    def cost_est_unbound(events, run_model=""):
        """Token spend on run_ends not bound to any ticket (e.g. review-lane runs)."""
        by_agent = {}
        all_ests = []
        for e in events:
            if e.get("kind") != "run_end":
                continue
            if e.get("ticket"):
                continue
            m = e.get("model")
            if run_model and m != run_model:
                continue
            est, _, _ = estimate_run_end_cost(e)
            if est is None:
                continue
            all_ests.append(est)
            key = (e.get("agent"), e.get("model"))
            by_agent.setdefault(key, []).append(est)
        out_by_agent = []
        for key in sorted(by_agent, key=lambda x: (str(x[0] or ""), str(x[1] or ""))):
            vals = by_agent[key]
            agent, model = key
            out_by_agent.append({
                "agent": agent,
                "model": model,
                "n": len(vals),
                "total": round(sum(vals), 6),
            })
        out = {
            "n": len(all_ests),
            "total": round(sum(all_ests), 6) if all_ests else None,
            "by_agent": out_by_agent,
        }
        if epic:
            out["excluded"] = "unbound runs carry no epic"
        return out

    def _report_scope():
        filters = {k: v for k, v in (
            ("ticket", ticket), ("agent", agent), ("model", model),
            ("epic", epic), ("since", since), ("until", until),
        ) if v}
        fields = {
            "tickets": {
                "denominator": "ticket",
                "model_axis": "resolved ticket model (events/workforce)",
            },
            "cost_est": {
                "denominator": "ticket",
                "model_axis": "resolved ticket model",
            },
            "cost_est_by_run_model": {
                "denominator": "run",
                "model_axis": "run_end model",
            },
            "cost_est_unbound": {
                "denominator": "run",
                "model_axis": "run_end model",
            },
        }
        if epic:
            fields["cost_est_unbound"]["excluded"] = (
                "unbound runs carry no epic")
        return {"filters": filters, "fields": fields}

    public_rows = []
    for r in rows:
        pub = {k: r[k] for k in ROW_KEYS}
        if r.get("cost_usd_est") is not None:
            pub["cost_usd_est"] = r["cost_usd_est"]
        if r.get("cost_source"):
            pub["cost_source"] = r["cost_source"]
        if r.get("cost_price_as_of"):
            pub["cost_price_as_of"] = r["cost_price_as_of"]
        public_rows.append(pub)
    by_run_model = cost_est_by_run_model(sel, run_model=model)
    unbound = cost_est_unbound(sel, run_model=model)
    return {
        "v": TURNS_JSON_V,
        "scope": _report_scope(),
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
            "cost_est": cost_est_agg,
            "cost_est_by_agent": cost_group(
                lambda r: r.get("owner"), "agent", field="cost_usd_est"),
            "cost_est_by_run_model": by_run_model,
            "cost_est_unbound": unbound,
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


def _fmt_cost(usd, est_usd=None):
    """'-' means UNMEASURED, and it is not $0.00. See _measured_cost."""
    return fmt_cost_cell(usd, est_usd)


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
            _fmt_cost(r.get("cost_usd"), r.get("cost_usd_est")),
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
    lines.append("'-' is UNMEASURED, not $0.00. Harness cost needs a JSON output format; "
                 "'est' is a list-price token estimate (T-480), never written to jsonl.")
    cost_est = agg.get("cost_est") or {}
    if (cost_est.get("n") or 0) > 0:
        lines.append("cost est (list price) measured %d ticket(s); unmeasured %d  total %s" % (
            cost_est.get("n") or 0, cost_est.get("n_unmeasured") or 0,
            _fmt_cost(None, cost_est.get("total"))))

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
