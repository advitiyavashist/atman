# Scheduler (V0–V4)

The agent-facing objective is **task completion in the fewest turns**.
`tickets route` is the control plane; the intelligence behind it is allowed to
improve without changing how agents pull work.

This document is the evolution map. V0 is what T-315 ships. Later levels
unlock only when the trajectory log can actually support them.

## Command (V0)

```sh
tickets route --shadow            # print rule pick vs learned/prior; write shadow_decision
tickets route --shadow --report   # agreement rate + realized turns where observed
tickets route --apply             # unimplemented; exits non-zero
```

`--shadow` does **not** assign, note, or message. The only write is one
`shadow_decision` event per currently ready ticket (deps met, status open)
into `.tickets/trajectories.jsonl`, through the same T-311 writers both CLI
copies already share.

## V0 — rules, with a named prior when data is thin

Today's `tickets route` still scores agents by role, cost and load. Shadow
mode prints that **rule pick** next to a **learned pick**:

- Group finished tickets by `(role, priority band)` × `(agent, model)`.
- Estimate median turns-to-done, Laplace-smoothed reopen rate and success
  rate. A TURN is a `run_end` (see `docs/turns.md`). `turns=null` (backfill /
  no `run_end`) is **not** 0: those tickets are `n_unmeasured` and excluded
  from the median.
- Support **n ≥ 5** → use the learned ranking (fewest median turns) and say
  `learned`.
- Support **n < 5** or **n = 0** → fall back to a **named tier prior** and
  say `prior` in the output. Never invent a measured median from an empty
  cell. The n=0 path prints `no measured trajectories` and still shows the
  prior — that is the designed behaviour, not a missing feature.

Tier prior:

| ticket | prior |
|---|---|
| P1 or contract work | opus / high |
| routine (default) | sonnet / medium |
| docs + tests | cursor or codex / low |

`--apply` is out of scope for V0. A live flip would change who takes work;
shadow is how we measure whether the learned side would have been better
before anyone trusts it.

Malformed `trajectories.jsonl` lines **error** (T-350). They are not skipped.

## What data unlocks each level

| level | what `tickets route` does | data that unlocks it |
|---|---|---|
| **V0** | Rule-based assign. Shadow compares rule vs learned/prior. | Ticket records + `trajectories.jsonl` v1. Works at n=0 (priors). Learned cells need n≥5 measured `run_end`s. |
| **V1** | Capability + cost + task → agent (still a published rule, richer features). | Stable `needs` / `can`, cost, role; enough finished tickets that a feature ablation is not noise. |
| **V2** | Learned router: the model behind `route` is fit on trajectories, not a median table. | Organic `run_end` (not backfill) across several (role, band, agent, model) cells; `shadow_decision` + realized turns so agreement/regret is measurable. |
| **V3** | Router also picks **model** and **reasoning effort**. | `model` and `effort` present on events (from `tickets join`), not omitted; enough contrast between effort levels on the same ticket shape. |
| **V4** | Router picks agent, model, compute budget, and text vs latent comms. | Budget/latency/token fields on `run_end` when the harness reports them; a comms-mode field that does not exist yet — do not default it. |

A field the writer does not know stays omitted (`docs/trajectories.md`).
Optimisers must not train on `n_unmeasured`.

## Event: `shadow_decision`

Appended only by `--shadow` (not `--report`). Notable fields: `source`
(`prior` or `learned`), `rule_agent`, `learned_agent`, `learned_model`, `n`,
`n_unmeasured`, `expected_turns` (omitted when unknown), `runner_up`,
`prior` (the named tier, when source is prior). No ticket bodies.
