#!/usr/bin/env python3
"""T-1509: resume-context size before and after a working STATE file.

Read-only over a board. For each ticket it reports what a resuming seat reads
today (`atm show <id>`: the ticket plus its whole note history) next to what
it reads with STATE (the prompt block: protected Goal plus the STATE body),
in characters and in estimated tokens (chars / 4; no tokenizer is assumed).

A ticket nobody kept a STATE for reports `after: null` -- that row is the
baseline to compare against once seats use `atm state`, not a measurement of
the change. Time to resume and inherited-handoff success need live seats and
are not measured here.

    python3 scripts/t1509_state_measure.py [--board DIR] [--top 10] [T-1 T-2 ...]
"""
from __future__ import annotations

import argparse
import contextlib
import importlib.util
import io
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _tickets():
    spec = importlib.util.spec_from_file_location("tickets_t1509_measure", os.path.join(ROOT, "tickets.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _tokens(chars):
    return (chars + 3) // 4


def measure(board, ids=None, top=10):
    tk = _tickets()
    ss = tk._seat_state()
    tickets = tk.load_all(board)
    by_id = {t["id"]: t for t in tickets}
    if ids:
        chosen = [by_id[i] for i in ids if i in by_id]
    else:
        chosen = sorted(tickets, key=lambda t: len(t.get("notes") or []), reverse=True)[:top]
    rows = []
    for t in chosen:
        with contextlib.redirect_stderr(io.StringIO()):
            before = tk.detail(board, t, tickets)
        owner = t.get("owner") or ""
        st = ss.load(board, owner, t) if owner else None
        after = ss.prompt_block(t, st) if st else None
        rows.append({
            "id": t["id"], "status": t.get("status"), "notes": len(t.get("notes") or []),
            "before_chars": len(before), "before_tokens_est": _tokens(len(before)),
            "after_chars": len(after) if after else None,
            "after_tokens_est": _tokens(len(after)) if after else None,
            "state_owner": (st or {}).get("inherited_from") or (owner if st else None),
        })
    return rows


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("ids", nargs="*")
    p.add_argument("--board", default=os.environ.get("TICKETS_DIR") or "")
    p.add_argument("--top", type=int, default=10)
    a = p.parse_args(argv)
    if not a.board:
        sys.exit("give --board or set TICKETS_DIR")
    print(json.dumps(measure(os.path.abspath(a.board), a.ids, a.top), indent=2))


if __name__ == "__main__":
    main()
