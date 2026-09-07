"""T-315: shadow scheduler v0.

`tickets route --shadow` compares the existing rule-based owner pick with a
learned pick (median turns-to-done, n>=5) or a named tier prior. It does not
assign, note, or message. The only write is a `shadow_decision` trajectory
event per ready ticket.

turns=null (no run_end / backfill) is never treated as 0: those tickets are
counted as n_unmeasured and excluded from the estimate.
"""

from __future__ import annotations

import json
import sys
from collections import defaultdict
from statistics import median

from ticket_board.turns import (
    TrajectoryParseError,
    _measured_turns,
    _model,
    _owner,
    load_trajectory_events,
)

MIN_SUPPORT = 5
MIN_COMPARE = 3
APPLY_MSG = "tickets route --apply is unimplemented (T-315: shadow only; nothing is assigned)"

DOCS_TEST_ROLES = ("docs", "documentation", "qa", "verification", "acceptance",
                   "test", "tests")


def priority_band(ticket):
    p = ticket.get("priority", 2)
    try:
        p = int(p)
    except (TypeError, ValueError):
        p = 2
    if p <= 1:
        return "p1"
    if p >= 3:
        return "p3"
    return "p2"


def _role(ticket):
    return (ticket.get("role") or "") or ""


def _ticket_index(tickets):
    return {t.get("id"): t for t in (tickets or []) if t.get("id")}


def _group_events(events):
    by = {}
    for e in events:
        tid = e.get("ticket")
        if not tid:
            continue
        by.setdefault(tid, []).append(e)
    return by


def _finished(evs, ticket):
    if (ticket or {}).get("status") == "done":
        return True
    for e in evs:
        if e.get("kind") in ("done", "merge"):
            return True
    return False


def _reopened(evs):
    return any(e.get("kind") == "reopen" for e in evs)


def _success(evs, ticket):
    last = ""
    for e in evs:
        k = e.get("kind")
        if k in ("done", "merge", "review", "block", "reopen"):
            last = k
    if last in ("done", "merge"):
        return True
    return (ticket or {}).get("status") == "done"


def prior_spec(ticket):
    """Named tier prior. Returned as (label, model_hint, cost_hint)."""
    role = _role(ticket).lower()
    band = priority_band(ticket)
    blob = ((ticket.get("title") or "") + " " + (ticket.get("body") or "")).lower()
    if band == "p1" or role == "contract" or "contract" in blob:
        return ("opus/high", "opus", "high")
    if role in DOCS_TEST_ROLES:
        return ("codex/cursor-low", "cursor", "low")
    return ("sonnet/medium", "sonnet", "medium")


def _laplace(k, n):
    return (k + 1.0) / (n + 2.0)


def build_estimates(events, tickets=None, workforce=None):
    """Per (role, band) x (agent, model) stats. Null turns excluded."""
    tickets = tickets or []
    workforce = workforce or {}
    idx = _ticket_index(tickets)
    cells = defaultdict(lambda: {
        "turns": [], "n_unmeasured": 0, "reopens": 0, "successes": 0, "n_finished": 0,
    })
    n_measured = 0
    n_unmeasured = 0
    for tid, evs in _group_events(events).items():
        t = idx.get(tid) or {}
        if not _finished(evs, t):
            continue
        owner = _owner(evs, t)
        mdl = _model(evs, owner, workforce)
        key = (_role(t), priority_band(t), owner or "", mdl or "")
        cell = cells[key]
        cell["n_finished"] += 1
        turns = _measured_turns(evs)
        if turns is None:
            cell["n_unmeasured"] += 1
            n_unmeasured += 1
            continue
        n_measured += 1
        cell["turns"].append(turns)
        if _reopened(evs):
            cell["reopens"] += 1
        if _success(evs, t):
            cell["successes"] += 1
    out = {}
    for key, cell in cells.items():
        nums = cell["turns"]
        n = len(nums)
        rec = {
            "role": key[0],
            "band": key[1],
            "agent": key[2],
            "model": key[3],
            "n": n,
            "n_unmeasured": cell["n_unmeasured"],
            "median_turns": median(nums) if nums else None,
            "reopen_rate": _laplace(cell["reopens"], n) if n else None,
            "success_rate": _laplace(cell["successes"], n) if n else None,
            "source": "learned" if n >= MIN_SUPPORT else "prior",
        }
        out[key] = rec
    return {
        "cells": out,
        "n_measured": n_measured,
        "n_unmeasured": n_unmeasured,
    }


def learned_ranking(estimates, role, band):
    """(agent, model) cells with n>=5, best (fewest median turns) first."""
    ranked = []
    for rec in estimates["cells"].values():
        if rec["role"] != role or rec["band"] != band:
            continue
        if rec["n"] < MIN_SUPPORT or rec["median_turns"] is None:
            continue
        ranked.append(rec)
    ranked.sort(key=lambda r: (r["median_turns"], -r["n"], r["agent"] or "", r["model"] or ""))
    return ranked


def _agent_names(workforce, roles, only=None, agents=None):
    names = sorted(set(list(workforce or {}) + [n for n in (roles or {}) if roles[n]]))
    if only:
        names = [n for n in names if n in only]
    agents = agents or {}
    return [n for n in names if not (agents.get(n) or {}).get("limit")]


def rule_pick(board, ticket, names, workforce, roles, score_agent, load_):
    best, best_s, why = None, None, ""
    runner, runner_s = None, None
    for n in names:
        e = (workforce or {}).get(n, {})
        s = score_agent(board, n, e, roles, ticket)
        if s is None:
            continue
        s -= 1.5 * (load_ or {}).get(n, 0)
        if best_s is None or s > best_s:
            runner, runner_s = best, best_s
            best, best_s = n, s
            why = "%s %s" % (e.get("model") or e.get("tool") or "", e.get("cost", ""))
        elif runner_s is None or s > runner_s:
            runner, runner_s = n, s
    return best, why, runner


def _matches_prior(entry, model_hint, cost_hint):
    model = (entry.get("model") or "").lower()
    cost = entry.get("cost", "medium")
    tool = (entry.get("tool") or entry.get("harness") or "").lower()
    if cost_hint == "high":
        return cost == "high" or "opus" in model
    if cost_hint == "low":
        return cost == "low" or tool in ("codex", "cursor") or "codex" in model or "cursor" in model
    return cost == "medium" or "sonnet" in model


def prior_pick(ticket, names, workforce, roles, board, score_agent):
    label, model_hint, cost_hint = prior_spec(ticket)
    scored = []
    for n in names:
        e = (workforce or {}).get(n, {})
        if not _matches_prior(e, model_hint, cost_hint):
            continue
        s = score_agent(board, n, e, roles, ticket)
        if s is None:
            continue
        scored.append((s, n, e))
    scored.sort(key=lambda x: (-x[0], x[1]))
    if scored:
        best = scored[0][1]
        runner = scored[1][1] if len(scored) > 1 else None
        return best, label, runner
    # named prior even when no registered agent matches — never invent a score
    return None, label, None


def decide_ticket(board, ticket, estimates, names, workforce, roles, score_agent, load_):
    role, band = _role(ticket), priority_band(ticket)
    rule_agent, rule_why, rule_runner = rule_pick(
        board, ticket, names, workforce, roles, score_agent, load_)
    ranked = learned_ranking(estimates, role, band)
    if ranked:
        best = ranked[0]
        runner = ranked[1] if len(ranked) > 1 else None
        return {
            "ticket": ticket.get("id"),
            "priority": ticket.get("priority", 2),
            "title": ticket.get("title") or "",
            "role": role,
            "band": band,
            "rule_agent": rule_agent,
            "rule_why": rule_why,
            "rule_runner": rule_runner,
            "learned_agent": best["agent"] or None,
            "learned_model": best["model"] or None,
            "expected_turns": best["median_turns"],
            "n": best["n"],
            "n_unmeasured": best["n_unmeasured"],
            "runner_up": (runner or {}).get("agent") if runner else None,
            "source": "learned",
        }
    agent, label, runner = prior_pick(
        ticket, names, workforce, roles, board, score_agent)
    wf = (workforce or {}).get(agent or "", {}) if agent else {}
    return {
        "ticket": ticket.get("id"),
        "priority": ticket.get("priority", 2),
        "title": ticket.get("title") or "",
        "role": role,
        "band": band,
        "rule_agent": rule_agent,
        "rule_why": rule_why,
        "rule_runner": rule_runner,
        "learned_agent": agent,
        "learned_model": (wf.get("model") or None) if agent else None,
        "expected_turns": None,
        "n": 0,
        "n_unmeasured": 0,
        "runner_up": runner,
        "source": "prior",
        "prior": label,
    }


def ready_tickets(tickets):
    done = set(t["id"] for t in tickets if t.get("status") == "done")
    ready = [t for t in tickets
             if t.get("status") == "open" and all(d in done for d in t.get("deps") or [])]
    ready.sort(key=lambda t: (t.get("priority", 2), t.get("id") or ""))
    return ready


def claimed_load(tickets):
    load_ = {}
    for t in tickets:
        if t.get("status") == "claimed":
            load_[t.get("owner")] = load_.get(t.get("owner"), 0) + 1
    return load_


def render_shadow_table(decisions, estimates):
    lines = []
    if estimates["n_measured"] == 0:
        lines.append("no measured trajectories")
    lines.append("%-8s %-3s %-8s %-14s %-14s %-8s %5s %8s %-14s %s" % (
        "ticket", "pri", "source", "rule", "learned", "n", "exp", "unmeas", "runner-up", "why"))
    for d in decisions:
        exp = d.get("expected_turns")
        src = d.get("source") or "prior"
        why = d.get("prior") if src == "prior" else (d.get("rule_why") or "")
        if src == "prior":
            why = "prior (%s)" % (d.get("prior") or why)
        lines.append("%-8s %-3s %-8s %-14s %-14s %5s %8s %8s %-14s %s" % (
            d.get("ticket") or "-",
            str(d.get("priority", 2)),
            src,
            (d.get("rule_agent") or "-")[:14],
            (d.get("learned_agent") or "-")[:14],
            str(d.get("n") or 0),
            "-" if exp is None else ("%.1f" % exp),
            str(d.get("n_unmeasured") or 0),
            (d.get("runner_up") or "-")[:14],
            why,
        ))
    lines.append("measured tickets used for estimates: %d; unmeasured (null turns, not 0): %d" % (
        estimates["n_measured"], estimates["n_unmeasured"]))
    return "\n".join(lines)


def _write_shadow_events(board, decisions, traj_event):
    if not traj_event:
        return 0
    n = 0
    for d in decisions:
        fields = {
            "src": "shadow",
            "source": d.get("source"),
            "rule_agent": d.get("rule_agent"),
            "learned_agent": d.get("learned_agent"),
            "learned_model": d.get("learned_model"),
            "n": d.get("n"),
            "n_unmeasured": d.get("n_unmeasured"),
            "runner_up": d.get("runner_up"),
            "band": d.get("band"),
            "role": d.get("role") or None,
        }
        if d.get("expected_turns") is not None:
            fields["expected_turns"] = d["expected_turns"]
        if d.get("prior"):
            fields["prior"] = d["prior"]
        rec = traj_event(board, "shadow_decision", ticket=d.get("ticket"), **fields)
        if rec:
            n += 1
    return n


def report_shadow(events, tickets=None, workforce=None):
    tickets = tickets or []
    workforce = workforce or {}
    idx = _ticket_index(tickets)
    grouped = _group_events(events)
    shadows = [e for e in events if e.get("kind") == "shadow_decision"]
    agree = 0
    compared = 0
    rule_turns = []
    learned_turns = []
    for e in shadows:
        tid = e.get("ticket")
        if not tid:
            continue
        evs = grouped.get(tid) or []
        t = idx.get(tid) or {}
        if not _finished(evs, t):
            continue
        turns = _measured_turns(evs)
        if turns is None:
            continue
        owner = _owner(evs, t)
        rule_a = e.get("rule_agent")
        learned_a = e.get("learned_agent")
        compared += 1
        if rule_a and learned_a and rule_a == learned_a:
            agree += 1
        if owner and rule_a and owner == rule_a:
            rule_turns.append(turns)
        if owner and learned_a and owner == learned_a:
            learned_turns.append(turns)
    return {
        "n_shadow": len(shadows),
        "n_observed": compared,
        "agreement_rate": (agree / compared) if compared else None,
        "rule_realized_median": median(rule_turns) if rule_turns else None,
        "learned_realized_median": median(learned_turns) if learned_turns else None,
        "n_rule_realized": len(rule_turns),
        "n_learned_realized": len(learned_turns),
    }


def render_report(rep):
    lines = ["shadow report"]
    lines.append("shadow_decision events: %d" % (rep.get("n_shadow") or 0))
    n = rep.get("n_observed") or 0
    rate = rep.get("agreement_rate")
    lines.append("observed (finished + measured turns): %d" % n)
    lines.append("agreement rate: %s" % ("-" if rate is None else ("%.2f" % rate)))
    rt, lt = rep.get("rule_realized_median"), rep.get("learned_realized_median")
    lines.append("realized turns  rule median %s (n=%d)  learned median %s (n=%d)" % (
        "-" if rt is None else ("%.1f" % rt), rep.get("n_rule_realized") or 0,
        "-" if lt is None else ("%.1f" % lt), rep.get("n_learned_realized") or 0,
    ))
    return "\n".join(lines)


def _events_before(events, at):
    """Strictly before `at` — post-claim records must not leak into the pick."""
    if not at:
        return list(events or [])
    return [e for e in (events or []) if (e.get("at") or "") < at]


def _first_claim_at(evs):
    claims = [e.get("at") for e in evs if e.get("kind") == "claim" and e.get("at")]
    return min(claims) if claims else None


def _has_bound_run_start(evs):
    return any(e.get("kind") == "run_start" and e.get("ticket") for e in evs)


def _historical_claimed_load(events, before_at):
    load_ = {}
    for tid, evs in _group_events(events).items():
        claimed = False
        owner = None
        done_before = False
        for e in evs:
            at = e.get("at") or ""
            if at >= before_at:
                continue
            if e.get("kind") == "claim":
                claimed = True
                owner = e.get("agent") or owner
            if e.get("kind") in ("done", "merge"):
                done_before = True
        if claimed and not done_before and owner:
            load_[owner] = load_.get(owner, 0) + 1
    return load_


def _comparable_turns(events, tickets, workforce, agent, role, band, before_at):
    """Measured turns for `agent` on finished (role, band) tickets before cutoff."""
    idx = _ticket_index(tickets)
    nums = []
    for tid, evs in _group_events(_events_before(events, before_at)).items():
        t = idx.get(tid) or {}
        if _role(t) != role or priority_band(t) != band:
            continue
        if not _finished(evs, t):
            continue
        owner = _owner(evs, t)
        if owner != agent:
            continue
        turns = _measured_turns(evs)
        if turns is None:
            continue
        nums.append(turns)
    return nums


def _median_or_null(nums):
    if len(nums) < MIN_COMPARE:
        return None, len(nums)
    return median(nums), len(nums)


def shadow_pick_at_claim(board, ticket, events, before_at, names, workforce, roles,
                         score_agent, tickets):
    """Shadow learned/prior pick using only records strictly before claim."""
    pre = _events_before(events, before_at)
    estimates = build_estimates(pre, tickets=tickets, workforce=workforce)
    load_ = _historical_claimed_load(pre, before_at)
    d = decide_ticket(board, ticket, estimates, names, workforce, roles,
                      score_agent, load_)
    return d.get("learned_agent"), d


def score_shadow(events, tickets, workforce, roles, board, score_agent, names=None,
                 agents=None):
    """Retrospective shadow-vs-actual scorecard (T-416). Observational only."""
    tickets = tickets or []
    workforce = workforce or {}
    roles = roles or {}
    agents = agents or {}
    names = names or _agent_names(workforce, roles, agents=agents)
    idx = _ticket_index(tickets)
    grouped = _group_events(events)
    rows = []
    for tid, evs in sorted(grouped.items(), key=lambda kv: (_first_claim_at(kv[1]) or "", kv[0])):
        t = idx.get(tid) or {}
        if not _finished(evs, t):
            continue
        if not _has_bound_run_start(evs):
            continue
        claim_at = _first_claim_at(evs)
        if not claim_at:
            continue
        actual = _owner(evs, t)
        shadow, decision = shadow_pick_at_claim(
            board, t, events, claim_at, names, workforce, roles, score_agent, tickets)
        agree = bool(actual and shadow and actual == shadow)
        role, band = _role(t), priority_band(t)
        pre = _events_before(events, claim_at)
        actual_nums = _comparable_turns(pre, tickets, workforce, actual, role, band, claim_at)
        shadow_nums = _comparable_turns(pre, tickets, workforce, shadow, role, band, claim_at)
        actual_med, actual_n = _median_or_null(actual_nums)
        shadow_med, shadow_n = _median_or_null(shadow_nums)
        realized = _measured_turns(evs)
        rows.append({
            "ticket": tid,
            "role": role,
            "band": band,
            "claim_at": claim_at,
            "actual": actual,
            "shadow": shadow,
            "source": decision.get("source"),
            "agree": agree,
            "realized_turns": realized,
            "actual_median": actual_med,
            "actual_n": actual_n,
            "shadow_median": shadow_med,
            "shadow_n": shadow_n,
        })
    compared = len(rows)
    agree_n = sum(1 for r in rows if r["agree"])
    rate = (agree_n / compared) if compared else None
    return {
        "rows": rows,
        "n": compared,
        "agreement_rate": rate,
        "n_agree": agree_n,
    }


def render_score_table(rep):
    lines = ["shadow-vs-actual scorecard (observational; not a counterfactual)"]
    rate = rep.get("agreement_rate")
    lines.append("agreement rate: %s (%d/%d)" % (
        "-" if rate is None else ("%.2f" % rate),
        rep.get("n_agree") or 0, rep.get("n") or 0))
    lines.append("%-8s %-8s %-14s %-14s %-5s %5s %12s %12s %5s" % (
        "ticket", "role", "actual", "shadow", "agree", "turns",
        "actual_med", "shadow_med", "src"))
    for r in rep.get("rows") or []:
        am = r.get("actual_median")
        sm = r.get("shadow_median")
        lines.append("%-8s %-8s %-14s %-14s %-5s %5s %12s %12s %5s" % (
            r.get("ticket") or "-",
            (r.get("role") or "-")[:8],
            (r.get("actual") or "-")[:14],
            (r.get("shadow") or "-")[:14],
            "yes" if r.get("agree") else "no",
            "-" if r.get("realized_turns") is None else str(r.get("realized_turns")),
            "-" if am is None else ("%.1f(n=%d)" % (am, r.get("actual_n") or 0)),
            "-" if sm is None else ("%.1f(n=%d)" % (sm, r.get("shadow_n") or 0)),
            (r.get("source") or "-")[:5],
        ))
    return "\n".join(lines)


def render_scorecard_doc(rep, generated_at):
    """Markdown scorecard with dated header (T-281 / T-416)."""
    lines = [
        "# Shadow-vs-actual turns scorecard",
        "",
        "**Generated:** %s" % generated_at,
        "",
        "Observational evidence only — not a counterfactual claim about what would",
        "have happened if the shadow pick had been assigned.",
        "",
        render_score_table(rep),
        "",
        "## Limits",
        "",
        "Survivorship: only tickets that reached `done` with a bound `run_start`",
        "(post T-352/T-388 recut) enter the table. Small *n* on disagreement",
        "medians is reported as `-` when either side has fewer than %d comparable" % MIN_COMPARE,
        "finished tickets. No cost axis until T-403 lands.",
        "",
    ]
    return "\n".join(lines)


def cmd_route_shadow(a, board, load_all, load_workforce, load_roles, load_agents,
                     score_agent, traj_event):
    """Print-only shadow route. `--apply` exits non-zero."""
    if getattr(a, "apply", False):
        print(APPLY_MSG, file=sys.stderr)
        sys.exit(1)
    try:
        events = load_trajectory_events(board)
    except TrajectoryParseError as exc:
        sys.exit(str(exc))
    tickets = load_all(board)
    workforce = load_workforce(board)
    roles = load_roles(board)
    agents = dict((r["owner"], r) for r in load_agents(board))
    names = _agent_names(workforce, roles, only=getattr(a, "only", None), agents=agents)
    if getattr(a, "score", False):
        rep = score_shadow(events, tickets, workforce, roles, board, score_agent,
                           names=names, agents=agents)
        text = render_score_table(rep)
        print(text)
        doc_path = getattr(a, "write_scorecard", None)
        if doc_path is not None:
            a.scorecard_doc = doc_path
            from datetime import datetime, timezone
            stamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
            with open(doc_path, "w") as f:
                f.write(render_scorecard_doc(rep, stamp))
            print("wrote %s" % doc_path)
        return
    estimates = build_estimates(events, tickets=tickets, workforce=workforce)
    if getattr(a, "report", False):
        print(render_report(report_shadow(events, tickets=tickets, workforce=workforce)))
        return
    ready = ready_tickets(tickets)
    load_ = claimed_load(tickets)
    decisions = [decide_ticket(board, t, estimates, names, workforce, roles,
                               score_agent, load_) for t in ready]
    print(render_shadow_table(decisions, estimates))
    _write_shadow_events(board, decisions, traj_event)
