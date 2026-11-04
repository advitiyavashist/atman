"""T-023: tokens per TICKET, metered from the harness's own session log.

Per-run usage (`run_end.tokens_*`) cannot answer "what did T-022 cost": one
watch run claims, submits and claims again, so its `ticket` field is the
ticket held at one instant -- on the live board every multi-ticket run
recorded `ticket: None` and billed nothing to any ticket.

The deterministic answer splits the session log, not the run:

  * every assistant request the harness logged carries a timestamp and its
    own usage (Claude Code transcripts), or a cumulative total (codex
    rollouts, turned into per-event deltas here);
  * the trajectory log says when this seat held which ticket: a `claim`
    opens a window, its `review`/`done`/`block`/`reopen` closes it, and a
    claim of another ticket closes the previous one (one ticket at a time);
  * each request lands in the window that contains its timestamp, or in
    `unattributed` -- work done holding nothing (a review of someone else's
    ticket, idle triage). Unattributed is reported, never spread.

Claude Code writes an assistant message once per content block, repeating
the SAME usage on each line; counting lines counts a two-block reply twice.
Requests are therefore keyed by message id and counted once.

Budgets: a ticket's `token_budget` is compared with `total` (fresh input +
output + cache write + cache read: everything the model processed for that
ticket). The four fields are disjoint for both harnesses: codex's
`input_tokens` includes its cached input, so the cached part is subtracted.

Pure: readers take file paths, `attribute` takes samples and windows.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone

FIELDS = ("tokens_in", "tokens_out", "tokens_cache_write", "tokens_cache_read")
CLAUDE_FIELDS = (("input_tokens", "tokens_in"), ("output_tokens", "tokens_out"),
                 ("cache_creation_input_tokens", "tokens_cache_write"),
                 ("cache_read_input_tokens", "tokens_cache_read"))
OPEN_KINDS = ("claim",)
CLOSE_KINDS = ("review", "done", "block", "reopen")
UNATTRIBUTED = ""


def epoch(stamp):
    if isinstance(stamp, (int, float)) and not isinstance(stamp, bool):
        return float(stamp)
    try:
        dt = datetime.fromisoformat(str(stamp).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.timestamp()


def _int(v):
    return int(v) if isinstance(v, (int, float)) and not isinstance(v, bool) and v >= 0 else 0


def _lines(path):
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            for raw in f:
                try:
                    rec = json.loads(raw)
                except ValueError:
                    continue
                if isinstance(rec, dict):
                    yield rec
    except OSError:
        return


def claude_samples(paths, since=None):
    """[(ts, usage)] once per assistant request across Claude transcripts."""
    seen, out = set(), []
    for path in paths:
        for rec in _lines(path):
            msg = rec.get("message")
            u = msg.get("usage") if isinstance(msg, dict) else None
            if not isinstance(u, dict):
                continue
            ts = epoch(rec.get("timestamp"))
            if ts is None or (since is not None and ts < since):
                continue
            key = (msg.get("id") or rec.get("requestId") or rec.get("uuid")
                   or "%s:%s" % (path, ts))
            if key in seen:
                continue
            seen.add(key)
            out.append((ts, {dst: _int(u.get(src)) for src, dst in CLAUDE_FIELDS}))
    return sorted(out, key=lambda s: s[0])


def codex_samples(paths, want_cwd, cumulative_of, since=None):
    """[(ts, usage delta)] from codex rollouts whose session cwd is `want_cwd`.

    `cumulative_of(rec)` is tickets.py's `_usage_from_codex_event`: the
    rollout's running total, or None. A total that goes DOWN is a new
    session counter, so its whole value is the delta.
    """
    out = []
    for path in paths:
        cwd, prev = None, None
        for rec in _lines(path):
            pay = rec.get("payload") if isinstance(rec.get("payload"), dict) else {}
            if isinstance(pay.get("cwd"), str):
                cwd = pay["cwd"]
            tot = cumulative_of(rec)
            if not tot:
                continue
            cur = {k: _int(tot.get(k)) for k in FIELDS}
            # OpenAI counts cached input INSIDE input_tokens (total_tokens =
            # input + output). Claude's four fields are disjoint; make codex's
            # disjoint too, or `total` counts every cached token twice.
            cur["tokens_in"] = max(0, cur["tokens_in"] - cur["tokens_cache_read"])
            if prev is None or any(cur[k] < prev[k] for k in FIELDS):
                delta = dict(cur)
            else:
                delta = {k: cur[k] - prev[k] for k in FIELDS}
            prev = cur
            ts = epoch(rec.get("timestamp"))
            if ts is None or cwd != want_cwd or (since is not None and ts < since):
                continue
            if any(delta.values()):
                out.append((ts, delta))
    return sorted(out, key=lambda s: s[0])


def windows(events, agent):
    """[(ticket, t0, t1)] when `agent` held each ticket; t1 None = still held."""
    rows = sorted((e for e in events if e.get("agent") == agent and e.get("ticket")),
                  key=lambda e: str(e.get("at") or ""))
    out, cur = [], None          # cur = [ticket, t0]
    for e in rows:
        kind, tid, ts = e.get("kind"), e["ticket"], epoch(e.get("at"))
        if ts is None:
            continue
        if kind in OPEN_KINDS or (kind == "update" and cur is None):
            # An update with nothing open is a held ticket whose claim the log
            # never saw (a reject returned it, an assign handed it over).
            if cur and cur[0] != tid:
                out.append((cur[0], cur[1], ts))
                cur = None
            if cur is None:
                cur = [tid, ts]
        elif kind in CLOSE_KINDS and cur and cur[0] == tid:
            out.append((tid, cur[1], ts))
            cur = None
    if cur:
        out.append((cur[0], cur[1], None))
    return out


def attribute(samples, wins):
    """{ticket: totals} with UNATTRIBUTED for requests outside every window."""
    out = {}
    for ts, usage in samples:
        tid = UNATTRIBUTED
        for w_tid, t0, t1 in wins:
            if t0 <= ts and (t1 is None or ts <= t1):
                tid = w_tid
                break
        row = out.setdefault(tid, dict({k: 0 for k in FIELDS}, requests=0))
        for k in FIELDS:
            row[k] += usage.get(k, 0)
        row["requests"] += 1
    for row in out.values():
        row["total"] = sum(row[k] for k in FIELDS)
    return out


def merge(into, more):
    for tid, row in more.items():
        dst = into.setdefault(tid, dict({k: 0 for k in FIELDS}, requests=0, total=0))
        for k in FIELDS + ("requests", "total"):
            dst[k] += row.get(k, 0)
    return into


def budget_state(total, budget):
    """'' (no budget) | 'ok' | 'warn' (>= 80%) | 'over'."""
    if not budget:
        return ""
    if total > budget:
        return "over"
    return "warn" if total >= 0.8 * budget else "ok"


def human(n):
    n = int(n or 0)
    for unit, size in (("M", 1_000_000), ("k", 1_000)):
        if n >= size:
            return "%.1f%s" % (n / size, unit)
    return str(n)
