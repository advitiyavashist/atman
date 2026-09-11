# Stripe Minions + Ramp Inspect vs Atman graph execution

**Snapshot:** 2026-09-11 (Asia/Singapore)

**Question:** What should Atman steal and ignore from Stripe’s unattended coding agents and Ramp’s background agent, relative to Atman’s local delivery graph?

**Source policy:** First-party product posts only. This is a research receipt, not a performance claim and not a change to public landing copy.

- Stripe [Minions: one-shot end-to-end coding agents](https://stripe.dev/blog/minions-stripes-one-shot-end-to-end-coding-agents) (fetched 2026-09-11).
- Ramp [Why we built our background agent](https://builders.ramp.com/post/why-we-built-our-background-agent) (Inspect; fetched 2026-09-11).

Category matrix one-liner (merge with T-781): `docs/research/atman-competitor-category-matrix.md` gap 10. Implementation of “success starts the next node”: T-781. Do not treat this file as permission to rewrite `landing/`.

## Decision

Steal the **graph-execution contract**, not the hosted factory:

| Steal | Ignore |
|---|---|
| Unattended persist until a reviewable artifact exists (Stripe skip-permission isolated run; Atman `skip-all` / `tickets done --force` only as the unattended persist analogue, never as a merge). | Slack (or any chat product) as the control plane. |
| One-shot to a human-reviewable PR/branch, with CI/local checks in the loop. | Hosted VM fleets as the product (Stripe pre-warmed **devbox**; Ramp **Modal** sandboxes). |
| Success of node A starts B without a human `tickets next` (T-781). Failure / BLOCKED / HOLD does not start children. | Forking **goose** (Stripe) or **OpenCode** (Ramp) as Atman’s agent loop. |
| Human review stays a gate. Stripe: minion PRs are human-reviewed; Ramp: GitHub auth so the app cannot self-merge unreviewed code. | A 400-tool MCP shed (Stripe Toolshed). Atman does not become a tool catalog. |
| Isolate concurrent work. Stripe/Ramp use remote machines because worktrees “wouldn’t scale” / “don’t ration checkouts.” Atman isolates with **git worktrees** on the operator machine. | Claiming Atman is a multi-agent framework, a shared-memory brain, or “Stripe/Ramp but local.” |

Atman remains the **local board**: claim, deps, mail, review, pinned merge. Harnesses stay themselves.

## What the sources actually say

### Stripe Minions

Documented, first-party:

- Fully **unattended**, built to **one-shot** tasks. A typical run **starts in Slack** and **ends in a pull request that passes CI**, ready for human review, with **no interaction in between**.
- **>1000 PRs merged per week** are described as completely minion-produced; they are **human-reviewed** and contain no human-written code (Stripe’s claim; not independently measured here).
- Isolated **devboxes** (same class of machine humans use), pre-warmed (~10s), **isolated from production and the internet**, so minions run **without human permission checks**. Stripe explicitly prefers this over git worktrees at their scale.
- Core loop is a **fork of Block goose**, mixed with deterministic git/lint/test steps. **At most two CI rounds** after local checks.
- Context via **MCP**; internal **Toolshed** hosts **>400 MCP tools**. Minions get curated subsets.
- Other entry points exist (CLI, web, internal docs/flags/tickets). Slack is the most frequent, not the only one.

### Ramp Inspect

Documented, first-party:

- Background agent that **writes code and closes the verification loop** with the same tools a Ramp engineer would have (tests, telemetry, feature flags; frontend screenshots / live preview).
- Each session is a **sandboxed VM on Modal** with a full local-like stack (Vite, Postgres, Temporal, …), wired to Sentry, Datadog, LaunchDarkly, Braintrust, GitHub, Slack, Buildkite.
- Fast/cheap sessions → many concurrent attempts; laptop need not stay on. Kick off at night, **check the PR in the morning**.
- Clients: Slack, web, Chrome extension, PR, hosted VS Code; **multiplayer** sessions; state synced across clients.
- Auth: prefer the **user’s GitHub token** to open PRs so the bot cannot be a path for **unreviewed** self-approval.
- Recommend replicators **fork OpenCode** (server-first). Spec is “paste this post into a coding agent.”
- Internal adoption claim: **~30%** of merged PRs on frontend and backend repos written by Inspect (Ramp’s claim; not independently measured here).

Neither post documents a **cross-harness delivery DAG** (Claude + Codex + Cursor as first-class seats), atomic multi-vendor claim, or Atman-style `tickets review` merge pin. They document **one-company hosted agents** that emit GitHub PRs.

## Mapping onto Atman (do / do not)

### Do (steal)

1. **Unattended persist.** A claimed ticket should be able to run to a reviewable branch/SHA without a human `tickets next` in the middle. Stripe’s analogue is skip-permission on an isolated box; Atman’s analogue is persist through approval prompts (`skip-all`) and, when the worker is submitting from a dirty/main-check exception path, `tickets done --force` / `tickets review` rules already in the CLI. `--force` is not “skip human review of the code.”
2. **One-shot to a reviewable artifact.** Success means `tickets review` (or `done` when the node does not require review), with branch + SHA — the same “PR that passes CI” shape, locally.
3. **Success starts the next graph node.** T-781: `done` (or merge when the node requires review) posts `kind=task` to reserved/suggested children; HOLD tickets are skipped by `next`. Grandchildren do not start. Live Steer graph must not auto-fire from this research.
4. **Review remains a gate.** `tickets review` / master merge. Stripe and Ramp both keep a human on the merge. Do not auto-`tickets done` production work because a child woke.
5. **Isolate with worktrees.** Parallel seats get their own worktree and branch. Do not take Stripe’s “worktrees wouldn’t scale” as a reason to stand up a VM product.

### Do not (ignore)

1. **Slack-as-bus.** Mail stays `tickets msg`. Slack may notify humans; it must not own claim, deps, or wake.
2. **Hosted VM fleets / Modal / Stripe devbox** as Atman itself. Remote runners can exist later as a `--can` / harness detail; they are not the control plane.
3. **Fork goose or OpenCode** into Atman. BYOA: bring the agent you already use.
4. **Toolshed-scale MCP.** Atman is not 400 tools. Context stays ticket + briefing + bounded knowledge.
5. **Framework / shared-memory positioning.** Minions and Inspect are internal coding agents. Atman is a **repo-delivery control plane** for existing harnesses. Do not launch as “our Minions” or “our Inspect.”

## What is already landed vs leftover polish

| Surface | Status at T-782 write |
|---|---|
| Steer `.tickets/MASTER.md` staffing bullet (graph execution steal/ignore) | Landed in board notes 2026-09-11. Not duplicated here. |
| Matrix gap 10 (steal/ignore one-liner) | On `cursor/t781-success-trigger` (docs commit `2a1031a`). This receipt is the durable expansion. After T-781 merges, keep gap 10 pointing here; do not fork a second steal/ignore story. |
| T-781 HOLD skip + done→task-wake | IN REVIEW at `cursor/t781-success-trigger@2a1031a` (code `6593c64`). Do not reimplement. |
| Public `landing/` copy | Unchanged. No Minions/Inspect, no “unattended fleet,” no Slack-control-plane claim. |

## Research limits

- Stripe and Ramp numbers are **vendor blog claims**, not Atman metrics. Do not copy them onto README, landing, or scorecards.
- Posts describe **internal** systems. There is no public Minions/Inspect product to buy; “parity” is pattern-level only.
- Retrieval date 2026-09-11. Implementation details (goose fork, Modal, Durable Objects) may change.
- T-781 may still be unmerged when this lands; dependents should match T-781’s tests (`tests/test_t781_success_trigger.py`), not this prose.
