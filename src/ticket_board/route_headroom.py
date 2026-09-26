"""T-1041: skip LIMITED providers; prefer headroom, then cheaper cost."""
from __future__ import annotations

import json
import os
from datetime import datetime, timedelta, timezone
import re

COST_RANK = {"low": 0, "medium": 1, "high": 2}
LEDGER_NAME = "provider_usage.json"


def cost_rank(entry):
    return COST_RANK.get((entry or {}).get("cost") or "medium", 1)


def remaining_percent(text):
    if text is None:
        return None
    raw = str(text).strip().rstrip("%")
    if not raw:
        return None
    try:
        val = float(raw)
    except ValueError:
        return None
    if val < 0 or val > 100:
        return None
    return val


# Retry policy, not a claim that quota is available. Explicit provider resets win.
UNKNOWN_LIMIT_HOURS = 5


def _stamp(value):
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return dt if dt.tzinfo is not None else None
    except (ValueError, TypeError, AttributeError):
        return None


def provider_reset_at(text, observed_at):
    """Resolve provider text once at capture; bare clocks use host local time."""
    from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
    value = (text or "").strip()
    observed = _stamp(observed_at)
    if observed is None:
        return ""
    value = re.sub(r"^.*?resets?\s+(?:at\s+)?", "", value, flags=re.I)
    reset = _stamp(value)
    if reset is None:
        relative = re.search(r"(?:back|retry|try again|resets?)\s+in\s+(\d+)\s*(minutes?|mins?|m|hours?|hrs?|h|seconds?|secs?|s)\b", text or "", re.I)
        if relative:
            count, unit = relative.groups()
            seconds = int(count) * (3600 if unit.lower().startswith('h') else 60 if unit.lower().startswith('m') else 1)
            try:
                reset = observed + timedelta(seconds=seconds)
            except OverflowError:
                return ""
        else:
            match = re.fullmatch(r"(\d{1,2})(?::(\d{2}))?\s*(am|pm)(?:\s*\(([^()]+)\))?", value, re.I)
            if not match:
                return ""
            hour, minute, meridiem, zone = match.groups()
            if not 1 <= int(hour) <= 12 or not 0 <= int(minute or 0) < 60:
                return ""
            try:
                local = observed.astimezone(ZoneInfo(zone)) if zone else observed.astimezone()
            except (ValueError, ZoneInfoNotFoundError):
                return ""
            reset = local.replace(hour=int(hour) % 12 + (12 if meridiem.lower() == "pm" else 0),
                                  minute=int(minute or 0), second=0, microsecond=0)
            if reset <= local:
                reset += timedelta(days=1)
            if not zone:
                # Re-resolve local DST for the target day rather than retaining
                # the observation's fixed UTC offset across a transition.
                reset = reset.replace(tzinfo=None).astimezone()
    return reset.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def limit_expiry(lim, now=None):
    """(expired, deadline, reason), shared by read-only and mutating callers."""
    now = now or datetime.now(timezone.utc)
    reset = _stamp(lim.get("reset_at"))
    if reset is not None:
        return reset <= now, reset, "provider reset elapsed"
    # Legacy records may have a provider time in until/note but no reset_at.
    parsed = (provider_reset_at(lim.get("until"), lim.get("at"))
              or provider_reset_at(lim.get("note"), lim.get("at")))
    reset = _stamp(parsed)
    if reset is not None:
        return reset <= now, reset, "recorded reset elapsed"
    observed = _stamp(lim.get("at"))
    if observed is None:
        return True, now, "limit has no valid observation time; retry permitted"
    # Only an explicitly named weekly window gets a seven-day hold.
    weekly = bool(re.search(r"\bweekly\s+(?:usage\s+)?(?:limit|quota)\b", lim.get("note") or "", re.I))
    hours = 168 if weekly else UNKNOWN_LIMIT_HOURS
    deadline = observed + timedelta(hours=hours)
    return deadline <= now, deadline, "no valid reset; %sh retry window elapsed" % hours


def seat_limit(rec, now=None):
    """Active limit or None; unknown resets have a bounded retry window."""
    lim = (rec or {}).get("limit")
    if not isinstance(lim, dict) or not lim:
        return None
    return None if limit_expiry(lim, now)[0] else lim


def split_limited(names, agents, limit_of):
    """(limited, agents): limited maps seat -> active limit.

    Seats whose stored limit is no longer active lose it in the returned
    agents copy, so filter_eligible and seat_limit agree on who is limited.
    """
    limited = {}
    out = dict(agents or {})
    for n in names:
        lim = limit_of(n)
        if lim:
            limited[n] = lim
        elif (out.get(n) or {}).get("limit"):
            rec = dict(out[n])
            rec.pop("limit", None)
            out[n] = rec
    return limited, out


def limit_label(lim):
    if not lim:
        return ""
    hid = (lim.get("harness") or lim.get("provider") or "provider").strip() or "provider"
    reset = (lim.get("reset_at") or lim.get("until") or "").strip() or "reset unknown"
    return "%s limited until %s" % (hid, reset)


def all_limited_hold_reason(blocked):
    """blocked: list of (seat, label)."""
    if not blocked:
        return "every candidate provider is limited"
    parts = ["%s (%s)" % (seat, label) for seat, label in blocked]
    return "HOLD: every candidate provider is limited -- " + "; ".join(parts)


def seat_headroom(board, entry):
    """Observed remaining percent, or None when unknown. Never invents zero."""
    hid = ((entry or {}).get("harness") or (entry or {}).get("tool") or "").strip().lower()
    if not hid:
        return None
    reading = _usage_reading(board, hid)
    return remaining_percent((reading or {}).get("remaining"))


def _usage_reading(board, hid):
    try:
        from ticket_board import provider_usage as pu
        return pu.get_reading(board, hid)
    except ImportError:
        pass
    try:
        import provider_usage as pu
        return pu.get_reading(board, hid)
    except ImportError:
        pass
    return _ledger_reading(board, hid)


def _ledger_reading(board, hid):
    path = os.path.join(str(board), LEDGER_NAME)
    try:
        with open(path) as f:
            data = json.load(f)
    except (OSError, ValueError):
        return None
    if not isinstance(data, dict):
        return None
    rec = (data.get("providers") or {}).get(hid)
    return rec if isinstance(rec, dict) else None


def eligible_limited(limited, agents, eligible_fn):
    """Limited seats that would pass eligibility apart from the limit.

    eligible_fn(names, agents) -> (eligible, counts), i.e. filter_eligible
    with the caller's workforce, roles and load. A limited seat that is also
    dormant, busy or unregistered is dropped, so it can never turn a ticket
    nobody fits into a permanent HOLD.
    """
    if not limited:
        return {}
    bare = dict(agents or {})
    for n in limited:
        rec = dict(bare.get(n) or {})
        rec.pop("limit", None)
        bare[n] = rec
    names, _counts = eligible_fn(sorted(limited), bare)
    return dict((n, limited[n]) for n in names)


def drop_logged_out(names, limited, skip_of):
    """(names, limited, skipped): confirmed logged-out seats removed from both.

    skip_of(name) -> reason, or empty to keep (T-1043 preflight route_skip).
    Runs after eligibility, so dormant/busy seats are never probed, and a
    logged-out seat is neither a candidate nor a limited row / HOLD reason.
    """
    skipped = {}
    for n in list(names or []) + sorted(limited or {}):
        if n in skipped:
            continue
        reason = skip_of(n)
        if reason:
            skipped[n] = reason
    kept = [n for n in (names or []) if n not in skipped]
    still = dict((n, lim) for n, lim in (limited or {}).items() if n not in skipped)
    return kept, still, skipped


def format_logged_out(skipped):
    return "\n".join("skipped %s: %s" % (n, skipped[n]) for n in sorted(skipped or {}))


def rank_key(limited, remaining, cost, score, load=0):
    """Lower is better. Limited last, confirmed-zero headroom next to last.

    Load (claims, reservations and suggestions made earlier in the same pass)
    ranks ahead of the known/unknown headroom split, so work spreads across
    seats with and without a reading. Unknown still beats confirmed zero.
    """
    if remaining is None:
        avail, headroom = 1, 0
    elif remaining > 0:
        avail, headroom = 0, -remaining
    else:
        avail, headroom = 2, 0
    # Fit is deliberately the last tiebreaker: headroom and cost decide before
    # score, so a priority-1 ticket no longer leans to a high-cost seat.
    return (1 if limited else 0, 1 if avail == 2 else 0, float(load or 0),
            avail, headroom, cost, -float(score or 0))


def seat_load(tickets, claims):
    """Ranking load: claims plus open reservations (reserved_for), 1 each."""
    load = dict(claims or {})
    for t in tickets or []:
        if t.get("status") != "open":
            continue
        who = (t.get("reserved_for") or "").strip()
        if who:
            load[who] = load.get(who, 0) + 1
    return load


def without_own_reservation(load, ticket):
    """Load as seen when re-routing `ticket`: its own reservation is not load."""
    own = ((ticket or {}).get("reserved_for") or "").strip()
    if not own or not (load or {}).get(own):
        return load
    out = dict(load)
    out[own] -= 1
    return out


def candidate_rows(board, ticket, eligible, limited, workforce, roles, load_, score_agent):
    """Rows for pick_seat.

    Only seats that passed filter_eligible are ranked. Limited seats that fit
    the ticket are carried as limited rows, used only for the skipped note and
    the HOLD reason; they are never picked.
    """
    rows = []
    for n in eligible or []:
        e = (workforce or {}).get(n, {})
        s = score_agent(board, n, e, roles, ticket)
        if s is None:
            continue
        rows.append({
            "name": n, "limited": False, "remaining": seat_headroom(board, e),
            "cost": cost_rank(e), "score": s, "load": (load_ or {}).get(n, 0),
            "limit_label": "",
        })
    for n in sorted(limited or {}):
        if n in (eligible or []):
            continue
        e = (workforce or {}).get(n, {})
        if score_agent(board, n, e, roles, ticket) is None:
            continue
        rows.append({
            "name": n, "limited": True, "remaining": None, "cost": cost_rank(e),
            "score": 0, "load": 0, "limit_label": limit_label(limited[n]),
        })
    return rows


def pick_seat(candidates):
    """Pick the best seat.

    candidates: iterable of dicts with keys
      name, limited (bool), remaining (float|None), cost (int), score (float),
      load (float, optional), limit_label (str).
    Returns (name, reason) or (None, hold_reason).
    """
    rows = list(candidates)
    if not rows:
        return None, ""
    open_seats = [r for r in rows if not r.get("limited")]
    if not open_seats:
        blocked = [(r["name"], r.get("limit_label") or "limited") for r in rows]
        return None, all_limited_hold_reason(blocked)
    best = sorted(open_seats, key=lambda r: rank_key(
        False, r.get("remaining"), r.get("cost", 1), r.get("score", 0),
        r.get("load", 0)))[0]
    bits = []
    if best.get("remaining") is not None:
        bits.append("headroom %g%%" % best["remaining"])
    else:
        bits.append("headroom unknown")
    bits.append("cost %s" % ({0: "low", 1: "medium", 2: "high"}.get(best.get("cost", 1), "medium")))
    if best.get("load"):
        bits.append("load %g" % best["load"])
    if best.get("score") is not None:
        bits.append("fit %s" % best["score"])
    skipped = [r for r in rows if r.get("limited")]
    if skipped:
        bits.append("skipped limited: " + ", ".join(
            "%s (%s)" % (r["name"], r.get("limit_label") or "limited") for r in skipped))
    return best["name"], "route: " + "; ".join(bits)
