"""T-480: read-time cost estimates from tokens x a versioned public price table.

Estimates are computed at display/read time only — never written to
trajectories.jsonl. Harness-reported cost_usd is never overwritten.
"""

from __future__ import annotations

import json
import os
from functools import lru_cache

_PRICES_PATH = os.path.join(os.path.dirname(__file__), "prices.json")


class PriceTableError(Exception):
    pass


@lru_cache(maxsize=1)
def load_price_table(path=_PRICES_PATH):
    with open(path) as f:
        doc = json.load(f)
    models = doc.get("models") or {}
    for name, row in models.items():
        for req in ("input_per_mtok", "output_per_mtok", "source", "as_of"):
            if req not in row:
                raise PriceTableError("model %r missing %s in %s" % (name, req, path))
    return doc


def _num(v):
    if isinstance(v, bool) or v is None:
        return None
    if isinstance(v, (int, float)):
        return float(v)
    return None


def _int_token(v):
    if isinstance(v, int) and not isinstance(v, bool):
        return v
    return None


def _price_row(model, table=None):
    if not model:
        return None
    table = table or load_price_table()
    return (table.get("models") or {}).get(model)


def estimate_run_end_cost(event, table=None):
    """Return (cost_usd, cost_source, price_as_of) or (None, None, None).

    Only when cost_usd is absent, tokens exist, and the model has a price row.
    """
    if event.get("kind") != "run_end":
        return None, None, None
    if isinstance(event.get("cost_usd"), (int, float)) and not isinstance(
            event.get("cost_usd"), bool):
        return None, None, None
    model = event.get("model")
    row = _price_row(model, table)
    if not row:
        return None, None, None
    tin = _int_token(event.get("tokens_in"))
    tout = _int_token(event.get("tokens_out"))
    tcr = _int_token(event.get("tokens_cache_read")) or 0
    tcw = _int_token(event.get("tokens_cache_write")) or 0
    if tin is None and tout is None and tcr == 0 and tcw == 0:
        return None, None, None
    tin = tin or 0
    tout = tout or 0
    if tin == 0 and tout == 0 and tcr == 0 and tcw == 0:
        return None, None, None
    cost = (
        tin * row["input_per_mtok"]
        + tout * row["output_per_mtok"]
        + tcr * row.get("cache_read_per_mtok", 0.0)
        + tcw * row.get("cache_write_per_mtok", 0.0)
    ) / 1_000_000.0
    if cost <= 0.0:
        return None, None, None
    return round(cost, 6), "estimate", row.get("as_of")


def _accumulate_estimates(evs, table=None):
    total, known, as_of = 0.0, False, None
    for e in evs:
        if e.get("kind") != "run_end":
            continue
        est, _, row_as_of = estimate_run_end_cost(e, table=table)
        if est is None:
            continue
        total += est
        known = True
        if row_as_of:
            as_of = row_as_of
    return (round(total, 6), as_of) if known else (None, None)


def ticket_cost_fields(evs, harness_cost, table=None):
    """Return cost_usd, cost_usd_est, cost_source for a ticket row."""
    est_total, price_as_of = _accumulate_estimates(evs, table=table)
    if harness_cost is not None:
        source = "harness"
    elif est_total is not None:
        source = "estimate"
    else:
        source = None
    return harness_cost, est_total, source, price_as_of


def fmt_cost_cell(harness_usd, est_usd=None):
    """Table cell: measured, estimated (labelled), or UNMEASURED ('-')."""
    if harness_usd is not None:
        if est_usd is not None:
            return "$%.4f + $%.4f est" % (harness_usd, est_usd)
        return "$%.4f" % harness_usd
    if est_usd is not None:
        return "$%.4f est" % est_usd
    return "-"


def traj_summary_bucket():
    return {
        "runs": 0, "updates": 0, "msgs": 0, "reopens": 0, "agents": set(),
        "outcome": "", "turns": 0,
        "cost_usd": 0.0, "cost_known": False,
        "cost_est_usd": 0.0, "cost_est_known": False,
    }


def traj_summary_add_event(s, event, table=None):
    k = event.get("kind")
    if k == "run_end":
        s["runs"] += 1
        if isinstance(event.get("turns"), int):
            s["turns"] += event["turns"]
        if isinstance(event.get("cost_usd"), (int, float)):
            s["cost_usd"] += event["cost_usd"]
            s["cost_known"] = True
        else:
            est, _, _ = estimate_run_end_cost(event, table=table)
            if est is not None:
                s["cost_est_usd"] += est
                s["cost_est_known"] = True
    elif k == "update":
        s["updates"] += 1
    elif k == "msg":
        s["msgs"] += 1
    elif k == "reopen":
        s["reopens"] += 1
    if k in ("done", "merge", "review", "block"):
        s["outcome"] = event.get("outcome") or k
    if event.get("agent"):
        s["agents"].add(event["agent"])


def traj_summary_cost_cell(s):
    harness = s["cost_usd"] if s["cost_known"] else None
    est = round(s["cost_est_usd"], 6) if s["cost_est_known"] else None
    return fmt_cost_cell(harness, est)
