"""T-1041: skip LIMITED providers; prefer headroom, then cheaper cost."""
from __future__ import annotations

import json
import os

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


def seat_limit(rec):
    """Active T-1022 limit dict or None. Caller expires stale resets."""
    lim = (rec or {}).get("limit")
    if not isinstance(lim, dict):
        return None
    if not lim.get("at") and not lim.get("note"):
        return None
    return lim


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


def rank_key(limited, remaining, cost, score):
    """Lower is better. Limited last. Unknown sits between available and zero."""
    if remaining is None:
        avail, headroom = 1, 0
    elif remaining > 0:
        avail, headroom = 0, -remaining
    else:
        avail, headroom = 2, 0
    return (1 if limited else 0, avail, headroom, cost, -float(score or 0))


def pick_seat(candidates):
    """Pick the best seat.

    candidates: iterable of dicts with keys
      name, limited (bool), remaining (float|None), cost (int), score (float),
      limit_label (str).
    Returns (name, reason) or (None, hold_reason).
    """
    rows = list(candidates)
    if not rows:
        return None, "no candidates"
    open_seats = [r for r in rows if not r.get("limited")]
    if not open_seats:
        blocked = [(r["name"], r.get("limit_label") or "limited") for r in rows]
        return None, all_limited_hold_reason(blocked)
    best = sorted(open_seats, key=lambda r: rank_key(
        False, r.get("remaining"), r.get("cost", 1), r.get("score", 0)))[0]
    bits = []
    if best.get("remaining") is not None:
        bits.append("headroom %g%%" % best["remaining"])
    else:
        bits.append("headroom unknown")
    bits.append("cost %s" % ({0: "low", 1: "medium", 2: "high"}.get(best.get("cost", 1), "medium")))
    if best.get("score") is not None:
        bits.append("fit %s" % best["score"])
    skipped = [r for r in rows if r.get("limited")]
    if skipped:
        bits.append("skipped limited: " + ", ".join(
            "%s (%s)" % (r["name"], r.get("limit_label") or "limited") for r in skipped))
    return best["name"], "route: " + "; ".join(bits)
