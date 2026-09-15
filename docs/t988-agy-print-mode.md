# T-988: Antigravity (`agy`) print mode — what was actually wrong

Measured against `agy` 1.2.2 on 2026-09-15. Every command line and every
envelope below is a verbatim capture, not a reconstruction.

## The short answer

**Antigravity print mode is not interactive-only.** It answers a one-line
prompt in about two seconds, with stdin closed, in a plain git worktree, with
no `--project` and no `--new-project`. The CEO probe that returned only

```
[agy] print timeout after 5m0s with turn in progress; returning partial output
```

was not a timeout, a missing project, a missing TTY or a missing login. It was
an **HTTP 429 that the output format threw away**.

## Evidence

`agy models` answered in 2.7s, so credentials and network were never the
problem. Re-running the identical hanging prompt with a machine-readable
output format printed the reason the text format had dropped:

```
$ agy --output-format json --print-timeout 55s -p "Reply with exactly: AGY OK"
[agy] print timeout after 55s with turn in progress; returning partial output
{"conversation_id":"a14abd1e-...","status":"ERROR","response":"",
 "error":"API error (attempt 5): RESOURCE_EXHAUSTED (code 429): Individual quota
 reached. Please upgrade your subscription to increase your limits.
 Resets in 76h1m55s.","duration_seconds":45.085081,"num_turns":1,
 "usage":{"input_tokens":0,...,"total_tokens":0}}
```

**Quota is per model family, not per account.** Same account, same flags, same
prompt, same second:

| model | result | wall time |
|---|---|---|
| (default) | `ERROR` — 429, resets in 76h | 5m (the print timeout) |
| `gemini-3.8-flash-low` | `ERROR` — 429, resets in 76h | 5m (the print timeout) |
| `claude-sonnet-4-6` | `SUCCESS` — `AGY OK` | **2.0s** |
| `gpt-oss-120b-medium` | `SUCCESS` — `AGY OK` | **1.0s** |

The default model is a Gemini one, so every Agy seat launched during the
exhausted window failed, and the failure was invisible. This is the same
exhaustion T-890 recorded on 2026-09-13 ("Individual quota reached,
reset 10m1s"), so it recurs; it is not a one-off.

## The working recipe

```
agy -p "$(tickets prompt)" --dangerously-skip-permissions \
    --output-format stream-json --print-timeout 60m [--model <model>]
```

Verified end to end: 6.9s in a throwaway directory, 5.1s inside a real git
worktree, stdin closed both times. Flags after the prompt ARE parsed — agy
does not stop at the first positional — so the argument order T-890 hit is not
a problem in this shape.

## Three defects this fixes

1. **`--print-timeout` was never set**, so agy used its own 5m default. Real
   ticket work is cut off mid-turn and returns partial output. The watcher's
   `--run-timeout` is in **minutes** (default 90), so `AGY_PRINT_TIMEOUT`
   is 60m and the watcher stays the outer bound.

   *Correcting the ticket's premise:* `--run-timeout 90` is 90 minutes, not 90
   seconds (`tickets.py`, the `watch` parser). The watcher never capped an Agy
   run at 90 seconds.

2. **The text output format drops an errored turn entirely.** It prints no
   status, no error and no 429 — and `agy` **exits 0 either way**. A
   quota-exhausted run therefore reached the run log as a clean empty success,
   which is why a seat could hold a ticket and produce no artifact with nothing
   in the log to say why (agy-docs-t969). `stream-json` carries the real
   `{"event":"result","result":{"status":"ERROR","error":...}}`, and its
   per-step lines keep the log advancing while the turn runs, which the
   one-blob-at-the-end `json` format would not.

   Because agy exits 0 on failure, nothing downstream may read its exit code
   as evidence that the run did anything:
   - `_structured_limit_signal` now recognises agy's errored result when the
     error string is limit-shaped, so `run_end.outcome` is `limit` at exit 0.
     Status `ERROR` alone is never enough — that is also how an ordinary tool
     failure ends.
   - the watch loop's `auth_check` no longer forces `state: ready` from
     `rc == 0`; a definite failure classification wins over the exit code.
     This was a general false-Ready, not an agy-only one.

3. **agy was missing from the V2 auth contract's `PROFILE_KINDS`**, so
   `validate_auth_check` called every agy record an "unknown harness" and
   `merge_auth_check` silently dropped it: a healthy agy run recorded **no**
   auth state at all. agy's kind is `adapter`, like `remote` and `custom` —
   the credential lives in the Antigravity app and there is no `agy` CLI login
   to hold it, which is why its recovery command is empty rather than invented.

`agy models` is now agy's credential probe. It reaches the provider without
starting a turn, and it proves **credentials and network only** — it listed
models in 2.7s while every Gemini model on the same account was 429. A `ready`
from it must never be read as "has quota".

Usage is now read off agy's own result (tokens, turns, duration), so Agy runs
stop landing in the trajectory log with no counts. No cost is recorded: agy
reports none, and a made-up price is worse than an absent one. `thinking_tokens`
is deliberately not mapped — the board has no field that prices or reads it.

## Quota visibility is runtime-observed, and says so

`INTEGRATION_CATALOG` used to mark agy `quota: "supported"`, with `usage_args`
of `("help",)` — and `agy help` can never contain the `{"quota": {...}}` shape
`quota_adapters.parse_agy_quota` expects. agy 1.2.2 has **no** status, usage or
quota subcommand at all (`agent`, `agents`, `changelog`, `help`, `install`,
`mcp`, `mic-serve`, `models`, `plugin`, `plugins`, `remote-control`, `update`),
so the only quota signal we ever get is a 429 from a run that already spent the
attempt.

Per the CEO ruling on T-862, that is now labelled for what it is:

- a fourth quota label, `runtime-observed`, alongside `supported`,
  `unsupported` and `optional-admin`; agy moves out of
  `quota_adapters.SUPPORTED_QUOTA` into `RUNTIME_OBSERVED_QUOTA`.
- `classify_catalog_usage` reports `unknown` with
  *"quota observed only at runtime (a 429 arrives after a run attempt)"*. Three
  things stay distinct: we never look (unsupported), we looked and could not
  read it (supported but incomplete), and there is nothing to look at before
  spending a run (runtime-observed).
- `usage_args` becomes `("models",)`. `agy help` proved only that the binary
  runs; `agy models` proves credentials and network reach the provider. Neither
  reports quota, and nothing may present either as proactive quota discovery.

**Onboarding must not claim proactive quota discovery for Antigravity.**

## Launch posture (CEO ruling, 2026-09-15)

- Antigravity stays **experimental** for the macOS preview — not supported.
- **No global product-default model is pinned.** Quota is transient and
  choosing a model is a routing decision, not an adapter default.
- While the Gemini family is exhausted (until roughly 2026-09-18), a spawned
  Agy seat must be given a currently working model *per seat*, e.g.
  `tickets spawn <seat> --harness agy --model claude-sonnet-4-6`. Without that
  the run burns on a 429 — visibly now, but still uselessly.
