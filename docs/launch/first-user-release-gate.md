# Atman preview: prove one dependable product experience

Decision adopted 15 September 2026. Target: 18 September preview.

The operator subsequently appointed Sol interim CEO and froze new feature
work. Consolidation includes the existing Fable app and landing changes.
T-894 orchestration experiments, T-933 automatic train tooling, T-898 Homebrew
publication and T-771 expanded knowledge work are on hold; Homebrew is removed
from the preview acceptance dependency. Preserve their branches and evidence.
Leadership return after Claude becomes available requires an explicit handoff.

Atman lets you run the coding agents you already use as one team. Give a
coordinator an objective, inspect the work, review the result, and continue
interrupted work from saved artifacts and a handoff. Coordination state is
local; model requests still go to the selected providers.

The release endpoint is an unfamiliar tester completing that workflow without
the author explaining it. A demonstration by the author is useful evidence,
but does not satisfy this gate.

## Priority and scope

1. Complete the existing local app and its matching first-session guide.
2. Fix installation, ownership, review and recovery defects on that path.
3. Integrate and test the resulting candidate once, with limitations visible.

Use the existing app, onboarding and recovery branches. Do not introduce a
replacement dashboard, desktop wrapper, hosted runtime or documentation system.
A complete language migration, published Homebrew tap, animated demo and
metrics dashboard are not prerequisites for this source-installed preview.
Security and state-integrity failures on the supported path remain blockers.

## Supported runtime decision

The proposed preview path is a pinned source checkout with `./install.sh
--prefix <chosen-prefix>` and that prefix on PATH. The app and both CLI names
must reach that checkout's implementation. `atm` is primary; `tickets` remains
a compatibility alias. The guide must show how to identify the running version
and board before creating work.

The reduced installed Python console package is not a second supported preview
path until it passes the same advertised workflow. Keep its limitations in the
support matrix and remove it from primary setup instructions. This is a scope
decision, not evidence that source installation already passes every gate.

Test a fresh isolated prefix and an existing tickets-only installation. Never
silently overwrite another launcher or redirect a new project to a previous
board. A non-destructive refusal with an exact supported next step is valid;
an installer reporting success when `atm` is absent from PATH is not.

## Reuse the work already in flight

The following is a board snapshot, not a merge order or an acceptance verdict.
Recheck actual heads, base and integration evidence before landing changes.

| Work | Existing ticket / candidate | Next action |
| --- | --- | --- |
| Coherent local app | T-810, PR #156 `033cb73` | Finish the first-user controls and honest states on this branch. Reuse the Work view; avoid more administrative tabs. |
| First-session docs | T-972, PR #171 | Keep one entry path. Its stub walkthrough proves command mechanics, not provider support or cross-provider recovery. |
| README promise | T-969 draft → T-819 | Explain the user's workflow and local/provider boundary. Do not wait for a polished recording to make setup understandable. |
| Capability evidence | T-975 | One table maps README, landing and app claims to exact-SHA tests/manual evidence and limitations. |
| Harness support evidence | T-1025, [`docs/launch/harness-support-matrix.md`](harness-support-matrix.md) | Per-harness discovery/auth/launch/wake/completion/recovery, each cell an evidence citation. Feeds T-975. |
| PR overlap and integration | T-974 triage; T-968 train verification | Existing triage covers 17 stale PRs; do not call it a completed audit of every open PR. Preserve candidate heads during testing. |
| Install / alias correctness | T-809 and independent FIX T-970 | Resolve source PATH-upgrade and invoked-name findings with a narrow successor; separately exclude unsupported wheel operations. |
| Claimable dependent plan | T-879 / PR #121, T-917 verification | Test the final guide verbatim. Tasks lacking real cause/change/proof stay capture with an actionable explanation. |
| Board selection | T-878 / PR #143 `1aaa7e2` | Keep `unset TICKETS_DIR`, then `atm where` in the new-project path; intentional shared overrides remain supported. |
| Safe recovery | T-977 | Reuse existing handoff/ownership mechanisms and show the tested recovery operation or exact terminal steps. |
| One active hold | T-979 | Cursor fixes the reproduced second-active-assignment defect with focused disposable-board tests. |
| Final user journeys | T-976 | Independent normal, interrupted, cross-harness continuation and adverse-setup checks. Initial probes may identify missing work; they do not certify an unmerged final candidate. |
| Public/static and local/app boundary | T-832 | Record URL-to-SHA and local runtime evidence separately. A static website is not a hosted live team. |

## What the app must make clear

The first screen answers: what are we finishing, who is working, what is
blocked, and what needs me? A user can select a project, connect an available
agent, set an objective with a completion condition, inspect a task and its
handoff, and reach the actual review/recovery operation.

Keep these states separate:

| Observation | Honest UI state |
| --- | --- |
| Executable exists | Tool found; authentication/connection not yet proven |
| Authentication was checked | Auth state, enrolled host and checked time |
| A message was posted | Queued or transport receipt; execution not established |
| A worker responded or began a turn | Acknowledged or working, with corresponding evidence |
| Worker submitted an artifact | Ready for review; not accepted |
| Independent exact-artifact verdict recorded | Accepted or needs changes, with reviewer and SHA |
| App misses events or disconnects | Stale/reconnecting; resynchronize authoritative state |
| Worker stops | Interrupted; show saved artifacts, ownership and next action |

Mutations show pending, confirmed or failed state, prevent duplicate clicks,
and preserve input on failure. Keyboard access, readable contrast and narrow
layouts are required on the core journey. Recovery controls must perform a
tested operation; terminal-only recovery must say so.

## Acceptance contract

Use disposable projects, isolated prefixes/configuration and deterministic
harnesses for repeatable checks. Record live supported-provider checks
separately. Never put test objectives, agents or messages on the live board.

| Journey | Required result | Failure / limitation handling |
| --- | --- | --- |
| Normal | Fresh install → correct project/board → coordinator objective → A then B → correct persisted artifacts → submitted review → independent exact-artifact acceptance. Inspect the actual app along the way. | A successful command, HTTP 200 or completion post alone is insufficient. Record every author intervention. |
| Interrupted | Stop a worker after an intermediate artifact; reload the app; recover/reassign safely; replacement receives saved state and finishes; independent review verifies output. | Artifacts remain intact; at most one active owner; stale owner cannot publish an accepted result. Unsafe recovery blocks the promise. |
| Cross-harness continuation | Repeat continuation using a second explicitly supported harness; verify the replacement's actual task outcome and relevant handoff. | A stub or same-provider restart does not certify this claim. Narrow public copy if live evidence is missing. |
| Adverse setup/state | Missing binary/auth, exhausted or unknown quota, stale version, repeated operation, failed check, competing claims, stopped coordinator and app reconnect produce honest states with the tested next step. | No fabricated connection, allowance, execution, successful retry or lost-state claim. |

Provider discovery, login, launch, wake, completion and recovery each need
their own support cell. Initially claim only tested macOS/version/harness
combinations. Supervised watcher wake must be labelled supervised; queued mail
must not be labelled a native wake. Personal remote access is not evidence of
a supported public remote-control product.

The reviewer must not be the artifact's author. Verify the final artifact
identity and actual acceptance/merge boundary, not an unstructured approval
caption. A retry or browser refresh must not create duplicate acceptance.

## Recovery scope: enforce what the runtime owns

A saved handoff identifies the objective/task, completion criteria, observed
progress, decisions, outstanding questions, artifact/worktree references,
verified checks and next action. Flag missing files and separate assumptions
from observations. Include only relevant context within a stated budget.

Fence stale claims, state mutations and accepted results with the existing
ownership/generation mechanism. A board lease by itself cannot prevent an
arbitrary process with filesystem permissions from making a Git commit. The
recovery proof must name the process-stop/owned-worktree isolation boundary
actually exercised; do not promise universal control of uncooperative tools.
If safe automatic recovery is unavailable, document tested terminal recovery
and its ownership checks rather than adding a decorative app button.

## Evidence and minimal metrics

Collect these in an ordinary per-run receipt; no dashboard project is needed.

| Metric | Definition |
| --- | --- |
| First reviewable result | Sessions reaching a correct submitted artifact / all eligible started onboarding sessions, with success and failure counts |
| Accepted result | Independent exact-artifact acceptance, with integration identity if merged; report separately from submission |
| Author intervention | Count each instruction/configuration/repair the tester needed from the author; target zero on the documented supported path |
| Recovery success | Verified completed continuations / attempted recoveries, split same-harness and cross-harness |
| Duplicate work | Count duplicate active claims, repeated dispatched work and duplicate accepted outcomes separately |
| Completion time | Time from objective submission to independently accepted outcome, plus failed/unfinished counts and observation cutoff |
| Cost | Observed provider spend including coordination/review/failed attempts; missing spend remains unknown with coverage, not zero |

Every receipt names the source SHA, runtime/install identity, environment,
guide revision, screenshots and actual test commands. Distinguish not run,
blocked, failed and passed. Branch tests do not certify a different merged
tree. Do not rerun sealed baselines or start a second full suite to improve a
summary; preserve the current verifier's custody and wait for its result.

## Integration and date cuts

Use one integration owner and one immutable candidate. If main advances while
a train is being verified, record that divergence and verify the actual
resulting candidate before claiming it has the train's results. Never move a
PR head under a running verification job.

The release graph is:

```text
app + canonical guide + source install fixes + ownership/recovery/review fixes
                              ↓
                  one integrated candidate
                              ↓
              T-976 first-user + recovery checks
                              ↓
        T-975 claim checklist + README/public/local consistency
                              ↓
                  independent release verdict
```

T-976 can start with diagnostic probes now; its final verdict must be repinned
after integration. Candidate tests and documentation edits can proceed in
parallel without waiting for a remote transport certification or demo GIF.

Protect the date by deferring optional metrics dashboards, cost-based routing
experiments, a wholesale backend migration, additional untested harnesses,
public remote access and Homebrew publication. Keep already reviewed work;
defer its release commitment without deleting foreign branches or labelling
unfinished implementations accepted.

Release the preview only when the supported guide, app journey and core
ownership/review/recovery gates pass for an unfamiliar tester. Otherwise cut
an unsupported claim or delay; more explanation and coordination messages do
not substitute for that result.
