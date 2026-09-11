# Atman competitor and category matrix

**Snapshot:** 2026-09-11 (Asia/Singapore)

**Question:** Where does Atman sit among coding-agent suites, local multi-agent coordinators, workflow frameworks, and agent control planes?

**Source policy:** Product capabilities come from current first-party documentation, repositories, or pricing pages. Community posts appear only in the pain-evidence section and carry retrieval grades. A missing documented capability is marked **N/E** (no first-party evidence found), rather than asserted to be impossible.

## Decision

Atman is a **repo-delivery control plane for a team of existing coding agents**. Its direct category is narrower than “multi-agent framework”: it owns a durable objective and dependency graph across model turns, assigns one owner atomically, moves messages and dependency handoffs between seats, recovers abandoned work, and holds a pinned branch and commit for review before merge.

The closest current competitors are:

1. **Medley**, which assigns a dependency-aware mission DAG across locally installed Claude Code, Codex, Cursor, OpenCode, Kimi, and Pi workers, persists local state, and exposes approvals, steering, history, receipts, and a live dashboard. Its compiled mission engine is proprietary, currently requires Apple-Silicon macOS and the Claude CLI, and the product boundary is unusually close to Atman's.
2. **Gas Town**, which now covers a broad local operations surface including Beads work, persistent identities, agent health, recovery, worktrees, and a bisecting verification merge queue.
3. **Claude Code agent teams**, which have closed much of the primitive coordination gap inside Claude: dependent shared tasks, atomic self-claim, direct mail, automatic delivery, and completion/idle hooks. The feature remains experimental and does not resume in-process teammates.
4. **NTM**, which combines many coding CLIs with dependency-aware assignment, Agent Mail reservations, file conflict handling, resumable pipelines, and durable approvals.
5. **GitHub coding agents**, whose distribution, heterogeneous hosted agents, issue-to-PR lifecycle, security checks, and audit surface make it the strongest platform threat even though its public docs do not show a cross-agent delivery DAG or agent-to-agent wake contract.
6. **Pentagon Studio**, which provides persistent local Claude/Codex agent teams, identity, direct/group communication, knowledge, delegation and status reporting, though no dependency DAG or autonomous task router is documented.
7. **Cursor**, which has polished parallel cloud execution, durable run events, artifacts, triggers, worktrees, and self-hosted execution pools, but no documented shared task graph or cross-agent coordination protocol.

Spine Canvas is a cloud DAG/model-routing product, while HarnessRouter, dari, Conifer and Not Diamond operate primarily at the harness/model routing layer. OpenHands, CrewAI, LangGraph, and Microsoft Agent Framework are adjacent runtimes or workflow substrates. Sondera is an adjacent reference monitor. They can run underneath or beside Atman; none of their reviewed first-party material establishes ownership of the repo-delivery lifecycle across arbitrary pre-existing coding harnesses.

This means Atman should not lead with “task graph,” “agent messages,” “parallel agents,” or “local orchestration” alone. Those are parity requirements. The defensible claim is the combined boundary:

> Give a mixed team of coding agents one durable delivery graph, recover work across sessions and providers, and accept only pinned, verified results—without rewriting the agents into a framework.

That claim is credible only if Atman closes the measurement, persistence-proof, packaging, and verification gaps listed below.

## How to read the matrices

| Mark | Meaning |
|---|---|
| **Y** | Current first-party source directly documents the capability. |
| **P** | Partial coverage or materially different scope; read the row note. |
| **N/E** | No first-party evidence found in the reviewed sources. This is not proof of absence. |
| **—** | The dimension does not fit the product's declared layer. |

Two distinctions prevent false parity:

- A **delivery graph** persists tickets and dependencies across agent runs and review. An **in-run workflow graph** connects model/tool nodes inside an application. Both can be useful, but they solve different ownership problems.
- **BYOA** here means coordinating an existing Claude Code, Codex, Cursor, local-model, or custom harness process. Choosing among models within a product SDK or hosted service earns partial credit.

## Matrix A — delivery coordination

| Product | Category | Objective / task graph | Atomic ownership | Messages / wakes | Persistent sessions | BYOA harnesses | Recovery | Verification / merge gate |
|---|---|---:|---:|---:|---:|---:|---:|---:|
| **Atman** | Repo-delivery control plane | **Y** durable objective, tickets, deps | **Y** exclusive claim; one active ticket default | **Y** durable DM/mention, dependency notes, queued offline wake | **P** lifecycle and native adapters exist; live proof gaps remain | **Y** prompt file + cwd + identity; built-ins and arbitrary command/remote | **Y** leases, stale/blocked states, explicit recovery | **Y** review queue; branch/SHA-pinned merge and tests |
| **Claude Code agent teams** | Native coding-agent suite | **Y** shared dependent task list | **Y** claim protected by file locking | **Y** direct mailbox, automatic delivery, idle notification | **P** tasks are local; in-process teammate resumption unsupported | **N/E** Claude instances only | **P** state is visible; replacement is manual after teammate errors | **P** completion hooks can gate tasks; worktrees and code review exist on separate surfaces |
| **Codex** | Native coding-agent suite and cloud service | **P** parent/child delegated task tree, not a documented durable delivery DAG | **N/E** | **P** parent/child send, wait, interrupt, follow-up | **Y** local sessions; cloud/app tasks are durable | **P** OSS CLI, MCP and tools; no arbitrary coding-harness team contract | **P** resume/retry at session or task level | **P** isolated worktrees, diffs, test logs and PR review; no documented team merge gate |
| **Cursor** | IDE and hosted coding-agent suite | **N/E** shared delivery DAG | **N/E** | **P** queue/follow-up to an active agent; external triggers | **Y** agents and per-prompt runs are durable | **P** model selection and MCP; agent loop stays Cursor-owned | **P** checkpoints and durable run state | **P** worktrees, commits/PRs, artifacts and diagnostics; no shared merge gate documented |
| **GitHub coding agents** | Hosted issue-to-PR platform | **P** unified agent-session/task view; issue and PR are the work unit | **N/E** | **P** prompt, review request and PR-comment iteration | **Y** multiple isolated agent sessions | **P** Copilot, Claude, Codex and custom agents within GitHub's hosted contract | **P** session history and comment iteration | **Y** PR boundary, security validation, tests/logs and human review |
| **Gas Town** | Local coding-agent workspace manager | **Y** Beads/molecules and convoys | **Y** Beads ownership and isolated polecat worktrees | **Y** mail, nudge/notification and daemon lifecycle | **Y** persistent Hook/crew identities; session handoff/seance | **Y** many presets plus custom command | **Y** witness/deacon health, escalation, release/repair/orphan flows | **Y** Refinery runs gates, batches and bisects merge failures |
| **NTM** | Local multi-CLI process and coordination manager | **Y** work graph and dependency-aware pipelines | **Y** atomic assignment plus Agent Mail file reservations | **Y** Agent Mail, broadcasts, notifications, coordinator | **Y** tmux sessions, saved profiles, pipeline state | **Y** broad CLI list including Ollama/local tools | **Y** pipeline resume, stale lock release, resilience state | **P** review-gate workflow and safety approvals; no equivalent bisecting repo merge queue evidenced |
| **OpenHands** | Coding-agent runtime, SDK and service | **P** SDK can compose agents; no external delivery ticket graph evidenced | **N/E** | **P** application-defined multi-agent communication | **Y** conversation history/resume | **P** BYO model and SDK agents, not arbitrary existing CLI harnesses | **P** persisted conversation/runtime state | **P** agent tests/PR work; no independent team acceptance queue evidenced |
| **CrewAI** | General agent workflow framework | **P** Tasks, Crews, Processes and Flows | **P** application-defined | **P** flow events and human feedback, not external seat wake | **Y** flow persistence/resume | **P** providers/tools through the framework | **Y** persisted flow state and resume | **P** task guardrails and human review, not repo merge ownership |
| **LangGraph / LangSmith** | Stateful agent workflow runtime and observability | **P** graph nodes inside an application | **P** application-defined | **P** graph interrupts/events, not harness mail | **Y** checkpointed threads | **P** provider-agnostic models/tools through SDK | **Y** durable execution/checkpoints | **P** evaluators/HITL; repo merge is external |
| **Microsoft Agent Framework** | General multi-agent workflow SDK | **P** sequential/concurrent/handoff/group-chat graphs | **P** application-defined | **P** workflow events/handoffs | **Y** checkpoint and durable session integrations | **P** providers/agents through Python or .NET SDK | **Y** checkpoint recovery and durable extension | **P** HITL/telemetry; repo merge is external |
| **Sondera Coding Agent Hooks** | Coding-agent reference monitor | **—** | **—** | **P** can steer/block/escalate the current harness event, not coordinate seats | **P** trajectory-aware decisions; local persistence differs by implementation | **Y** adapters span Claude, Cursor, Copilot, Gemini, Codex, OpenHands and others | **P** fail-closed hook enforcement; not work/session recovery | **P** pre/post model/tool policy gates; no delivery merge ownership |

## Matrix B — data, routing, economics, deployment

| Product | Trajectory data | Routing | Turns / cost measurement | Local / self-hosted | Setup | Price and source status |
|---|---:|---:|---:|---:|---|---|
| **Atman** | **P** run events tie objective/ticket/seat/harness/model/outcome; payloads intentionally bounded | **P** live eligibility rules; learned/prior comparison is shadow only | **P** turns exist; token/cost coverage is currently too sparse for claims | **Y** local file board; local/custom/HTTP models supported | Python 3.9+, Git; live CLI is one stdlib file | MIT; free source, user pays harness/model; no hosted plan published |
| **Claude Code** | **Y** local JSONL transcripts, histories, analytics | **P** lead delegates; `/batch` and subagent/agent-team choices; no learned team router evidenced | **Y** token/cost reporting; agent teams scale cost with active teammates | **P** local CLI, model service hosted by Anthropic | Installer/package plus account or API key; experimental flag for teams | Proprietary service; consumer plans and API usage pricing published |
| **Codex** | **Y** transcripts, diffs, commands, logs and task artifacts | **P** parent delegates; app supports parallel tasks; no outcome-learned team router evidenced | **Y** usage is metered; no task-turn objective score evidenced | **Y** Apache-2.0 local CLI; hosted app/cloud optional | Script, npm or Homebrew; ChatGPT/API auth | CLI Apache-2.0; service proprietary and plan/credit metered |
| **Cursor** | **Y** transcripts, run events, environment/setup logs and artifacts | **P** user selects model; pool routes to hardware class; `/best-of-n` | **Y** usage/model API-price accounting; no team-turn optimizer evidenced | **P** self-hosted runners execute tools, while Cursor retains the loop and inference control | Desktop/CLI or managed cloud; runners require pool setup | Proprietary; Individual Pro $20, Pro+ $60, Ultra $200; Teams from $40/user at retrieval |
| **GitHub coding agents** | **Y** session history, PR record, audit/actions logs | **P** agent/model choice and Copilot Auto model-by-complexity | **Y** AI credits plus Actions minutes; no delivery-turn optimizer evidenced | **P** local Copilot CLI exists; cloud coding-agent execution is hosted | Enable agent/app and repository permissions | Proprietary service; Free $0, Pro $10, Pro+ $39, Max $100 shown at retrieval; org plans differ |
| **Gas Town** | **Y** event logs, activity feed and OTEL data model | **P** capacity scheduler and role/provider selection; no learned outcome router evidenced | **P** telemetry exists; no documented fewest-turn/cost-to-outcome learner found | **Y** local workspace manager | Binary/Homebrew/npm; Git plus Beads/Dolt-era workspace dependencies documented by version | MIT; free source, user pays harness/model |
| **NTM** | **Y** timeline/metrics, pane state, assignments, reservations | **Y** capability/dependency strategies; coordinator automation | **P** rate/token telemetry exists; no outcome-linked turn learner evidenced | **Y** local tmux-based manager and local model support | Binary/Homebrew/source; tmux and optional coordination stack | MIT repository; free source, user pays harness/model |
| **OpenHands** | **Y** conversation and event history | **P** application/SDK-defined agent selection | **P** model usage available; no delivery-turn metric evidenced | **Y** CLI/local GUI/Docker and local LLM endpoints | CLI binary/uv or Docker; model/provider configuration | Core current repos expose OSS licenses; cloud service is separate; exact hosted price not relied upon here |
| **CrewAI** | **Y** enterprise tracing covers LLM/tool/memory events and costs | **P** application-defined agents/process | **Y** trace/cost data; no external delivery-turn objective | **Y** OSS framework; enterprise control plane optional | `uv`/pip and project scaffold | MIT framework; enterprise pricing by contact |
| **LangGraph / LangSmith** | **Y** traces, datasets, evaluators and cost tracking | **P** application-defined graph/router | **Y** token/cost tracking; no repo-delivery turn target | **Y** MIT runtime; LangSmith can be hosted or enterprise deployed | Python/JS package plus optional LangSmith | LangGraph MIT; LangSmith Developer $0, Plus $39/seat, Enterprise custom at retrieval, usage extra |
| **Microsoft Agent Framework** | **Y** spans, metrics and workflow events | **P** application-defined handoff/orchestration | **P** telemetry supports measurement; no delivery-turn optimizer evidenced | **Y** MIT Python/.NET SDK; self-hosted and Azure options | Python/.NET package; provider credentials | MIT framework; infrastructure/model charges vary |
| **Sondera Coding Agent Hooks** | **Y** adjudicated trajectory events; every action/decision/reason is a product claim | **—** policy routes allow/deny/escalate/steer, not task-to-agent | **N/E** delivery turns/cost | **Y** deterministic signatures/Cedar run locally without provider key; platform optional | Release binary or Rust build, start `serve`, install harness adapter | MIT coding-agent hooks; open 110+ Cedar policies; managed console price not published in reviewed sources |

## Evidence notes by competitor

### Atman baseline

The baseline comes from this repository's [README](../../README.md), [BYOA contract](../byoa.md), [message and runner design](../messages-and-runners.md), [native-session adapter evidence](../t683-native-session-adapters.md), [managed runner contract](../managed-runner.md), [trajectory schema](../trajectories.md), [turn metric](../turns.md), [organic routing scorecard](../turns-scorecard-organic.md), and [scheduler](../scheduler.md).

The distinguishing interface is intentionally small: a harness receives a prompt file, working directory, and identity, then uses the same board as every other seat. The runner does not parse the harness's reasoning or require an agent SDK. The local board has durable files, exclusive-create claims, delivery dependencies, messages, reviews, and pinned merge evidence.

The qualification is material. “Persistent” can describe a retained but offline seat; the native-adapter document explicitly does not claim a real idle Claude Code end-to-end resume proof and lists Cursor live-path proof gaps. The managed runner carries a ten-turn budget but cannot enforce it because the underlying Claude CLI has no matching flag; only time is enforced. The organic scorecard found `cost_usd` measured on **0/27** relevant rows and absent on **all 463** captured `run_end` rows, while learned expected-turn estimates were available on **0/27** routes. The live router is therefore a rules router, and outcome/cost learning remains an unproved direction.

The root/live installer also exposes `watch`, `spawn`, `hooks`, `ui`, wake modes, and remote adapters that the smaller packaged console entry point does not yet match. That packaging split raises setup and support risk.

### Claude Code

[Agent teams](https://code.claude.com/docs/en/agent-teams) are experimental and disabled by default, but the current implementation is a real parity threat: a lead and independent teammates share a local dependent task list and mailbox; teammates self-claim; file locking prevents simultaneous claims; messages deliver automatically; completion unblocks dependencies. `TeammateIdle`, `TaskCreated`, and `TaskCompleted` hooks can enforce deterministic checks by returning exit code 2. The same page documents the boundaries: teammates cannot be resumed in-process, task state can lag, a session can manage only one team, teams cannot nest, and same-file work can overwrite another teammate's changes.

[Session management](https://code.claude.com/docs/en/sessions), [subagents and `/batch`](https://code.claude.com/docs/en/agents), [worktrees](https://code.claude.com/docs/en/worktrees), and [code review](https://code.claude.com/docs/en/code-review) provide strong adjacent persistence, isolation and verification. [Cost guidance](https://code.claude.com/docs/en/costs) now explicitly warns that agent-team cost scales with team size and reports token/cost controls. [Claude Code pricing](https://claude.com/product/claude-code) is proprietary and plan/API metered.

Atman remains different where the work team crosses provider or harness boundaries, persists after the native team is removed, needs explicit lease recovery, or must merge through one external acceptance queue.

### OpenAI Codex

The [Codex repository](https://github.com/openai/codex) is an Apache-2.0 local CLI. Its current [multi-agent tool specification](https://github.com/openai/codex/blob/main/codex-rs/core/src/tools/handlers/multi_agents_spec.rs) exposes child spawning, messages, follow-up tasks, interrupts, waits, and a task tree. That is strong in-session delegation evidence, but it is not first-party evidence for a durable cross-run delivery DAG with atomic ticket ownership.

The [Codex app launch](https://openai.com/index/introducing-the-codex-app/) describes a multi-agent command center, parallel long-running tasks, worktrees, diffs, and automations. [Codex upgrades](https://openai.com/index/introducing-upgrades-to-codex/) documents terminal logs, tests, citations, and review. [Plan availability](https://help.openai.com/en/articles/11369540-using-codex-with-your-chatgpt-plan) changes often; the matrix relies only on the current metered-plan framing, not a durable dollar figure.

### Cursor

[Cloud agents](https://cursor.com/docs/cloud-agent) run many agents in isolated managed VMs and accept web, app, Slack, GitHub, Linear and API triggers. The [cloud-agent API](https://prod.cursor.com/docs/cloud-agent/api/endpoints) models a durable agent with individual prompt runs. [Worktrees and best-of-n](https://cursor.com/docs/configuration/worktrees) isolate results for review, commit and PR. [Agent diagnostics](https://cursor.com/docs/cloud-agent) expose transcripts, events, environment logs, setup logs, and artifacts.

[Self-hosted runners](https://cursor.com/docs/cloud-agent/self-hosted) move tool execution to customer machines, but Cursor retains the agent loop, inference, planning, and transfers the needed contents and results. That is not a fully self-hosted control plane. [Current model and plan pricing](https://prod.cursor.com/docs/models-and-pricing) supports the prices captured in the matrix.

### GitHub coding agents

[Third-party coding agents](https://docs.github.com/en/copilot/concepts/agents/about-third-party-coding-agents) place Claude and Codex beside Copilot cloud agent. A user assigns an issue or prompt, the agent works asynchronously, opens a PR, and responds to reviews and comments; sessions consume Actions minutes and AI credits. [Managing agent sessions](https://docs.github.com/en/copilot/how-tos/github-copilot-app/agent-sessions) documents isolated sessions/branches, local and cloud surfaces, model/reasoning selection, history, and security review. The [agents product page](https://github.com/features/copilot/agents) presents the unified task view and partner/custom-agent surface. [Current plan prices](https://github.com/features/copilot/plans) support the retrieved consumer figures.

GitHub has the strongest ready-made system of record and human review boundary. The reviewed docs do not establish atomic claims on dependent subtasks, agent-to-agent mail, or offline wakes. Marking those **N/E** avoids confusing the issue/PR lifecycle with an external team scheduler.

### Gas Town

The [Gas Town repository](https://github.com/gastownhall/gastown) and [overview](https://github.com/gastownhall/gastown/blob/main/docs/overview.md) document a Mayor coordinator, Beads work, convoys, isolated polecats, persistent hooks/crew, Witness and Deacon health management, and a Refinery. The [command reference](https://github.com/gastownhall/gastown/blob/main/docs/reference.md) lists built-in Claude, Gemini, Codex, Kiro, Cursor, Auggie, Amp, OpenCode and Copilot providers plus custom commands.

Refinery is stronger than Atman's current generic gate in one important respect: it batches merge requests, tests the merged stack, and bisects a red batch so good work can land. Gas Town also exposes repair/release/orphan and previous-session “seance” operations. Its operational model, vocabulary and dependency stack are heavier, and its public positioning favors much larger fleets than the 3–7-seat pain supported below. No reviewed source shows learned harness/model selection from completed delivery outcomes.

### NTM

The [NTM repository and reference](https://github.com/Dicklesworthstone/ntm) document a local tmux manager that now exposes work graph/triage, dependency-aware assignment, Agent Mail, reservations, lock renewal and force-release, coordinator conflict handling, safety policy and approvals, and reusable pipelines with persisted resume. The [current changelog](https://github.com/Dicklesworthstone/ntm/blob/main/CHANGELOG.md) supports capability profiles, atomic assignment, conflict detection, resume, resilience hardening and multi-channel events. Its [agent skill](https://github.com/Dicklesworthstone/ntm/blob/main/SKILL.md) shows the actual operator commands.

NTM's harness breadth and local operations are direct competition. Its reviewed first-party sources do not show a single independent acceptance queue that pins each agent branch/SHA, tests the integrated result and owns the merge in the same way as Gas Town Refinery or Atman.

### OpenHands, CrewAI, LangGraph and Microsoft Agent Framework

[OpenHands](https://github.com/All-Hands-AI/OpenHands) provides a coding-agent runtime, CLI, SDK and service. Its [quickstart](https://docs.openhands.dev/overview/quickstart), [CLI history/resume](https://docs.openhands.dev/openhands/usage/cli/quick-start), [local LLM support](https://docs.openhands.dev/openhands/usage/llms/local-llms), and [SDK](https://docs.openhands.dev/sdk/index) demonstrate local/cloud execution and programmable multi-agent applications. It is an agent implementation surface, not evidence of a durable external delivery board over arbitrary installed CLIs.

[CrewAI](https://github.com/crewAIInc/crewAI) is an MIT agent framework. [Crews, tasks, processes and Flows](https://docs.crewai.com/en/introduction) provide application-level orchestration, persistence, human feedback and guardrails. Its enterprise control-plane page claims traces and cost visibility; that is useful observability, but the app author still owns repo work assignment and merge.

[LangGraph](https://github.com/langchain-ai/langgraph) is an MIT durable workflow runtime with checkpointed state and human-in-the-loop control. [Subgraphs](https://docs.langchain.com/oss/python/langgraph/use-subgraphs) compose multi-agent applications. [LangSmith cost tracking](https://docs.langchain.com/langsmith/cost-tracking) and [pricing](https://www.langchain.com/pricing) cover observability/evaluation. The graph is inside the application rather than a delivery DAG across existing coding tools.

[Microsoft Agent Framework](https://github.com/microsoft/agent-framework) is the current MIT successor to AutoGen for Python and .NET. Its [workflow documentation](https://learn.microsoft.com/en-us/agent-framework/workflows/) covers sequential, concurrent, handoff, group-chat and Magentic orchestration with events and telemetry; [checkpoints](https://learn.microsoft.com/en-us/agent-framework/workflows/checkpoints) and the [durable extension](https://learn.microsoft.com/en-us/agent-framework/integrations/durable-extension) support recovery and self-hosted/Azure execution. The official [AutoGen repository](https://github.com/microsoft/autogen) now describes AutoGen as community-maintained and directs new users to Agent Framework, so AutoGen is not treated as a separate current leader.

### Sondera Coding Agent Hooks

The MIT [Sondera Coding Agent Hooks repository](https://github.com/sondera-ai/sondera-coding-agent-hooks) is a reference monitor around coding-agent actions. Local hook adapters normalize events and send them over gRPC to `sondera serve`. Deterministic YARA/signature findings and Cedar policies return Allow, Deny or Escalate; optional LLM classifiers label sensitivity or secure-code properties, while the symbolic policy layer owns the verdict. This implements Sondera's concise boundary: **neural reads; symbolic decides**.

Its documented adapter matrix reaches Claude Code, Cursor, GitHub Copilot, Gemini CLI, Antigravity, Codex, Hermes, OpenCode, OpenHands and VS Code. Depending on host hooks, it can observe, block, request approval, steer with context, redact tool output, or terminate at pre-model, post-model, pre-tool, and post-tool stages. Enforcement hooks fail closed when the harness is unavailable. The [architecture article](https://blog.sondera.ai/p/hooking-coding-agents-with-the-cedar) explains the normalized trajectory event model, local reference monitor, information-flow state and Cedar authorization. The project runs locally without a provider key and publishes more than 110 Cedar policies.

Sondera's [platform documentation](https://docs.sondera.ai/) and [trajectory model](https://docs.sondera.ai/concepts/trajectories/) claim an action-level record of inputs, outputs, tool calls and policy decisions. Scope matters: the Python documentation says its local `CedarPolicyHarness` does not persist trajectories or resume them, and a denied decision does not stop an arbitrary embedded agent unless the integration honors it. The coding-agent hooks provide stronger host-specific enforcement and fail-closed behavior; the managed console/persistent store is a separate platform surface. No reviewed price is published.

Atman is complementary on objective decomposition, ticket ownership, context delivery, cross-seat messages, liveness/recovery and review/merge. Sondera can inspect and constrain each selected worker. For the related Steer product, the overlap is direct: policy evaluation, runtime action choice, pre/post model and tool interception, redaction/steering/blocking, and auditable receipts. Steer should distinguish its customer-output/compliance rule packs and `allow/redact/revise/review/block` decision contract from Sondera's coding-agent action reference monitor; it should not claim the general hook-policy-receipt pattern as unique.

## Previously named systems

Most names in the epic briefing can be resolved to current first-party products. Their layer matters more than a flat “competitor” label. Medley and Pentagon are team runtimes; Spine Canvas is a cloud DAG and model orchestrator; Mosaic and Wato center shared sessions/context; HarnessRouter, dari, Conifer and Not Diamond route harnesses or models. “Orchestra” remains too ambiguous to score.

### Named-system matrix A — coordination

| Product | Category | Objective / task graph | Atomic ownership | Messages / wakes | Persistence | BYOA | Recovery | Verification / merge |
|---|---|---:|---:|---:|---:|---:|---:|---:|
| **Spine Canvas / Swarm** | Cloud agent DAG/model orchestrator | **Y** adaptive DAG/task tree | **P** system assigns tasks; no atomic claim primitive evidenced | **P** structured inter-task handoffs | **Y** blocks, context and memory | **P** MCP/API context, not arbitrary worker harnesses | **P** adaptive orchestration; no delivery lease recovery evidenced | **N/E** independent repo merge gate |
| **Medley** | Local coding-agent mission runtime | **Y** dependency-aware mission DAG | **P** supervisor assignment and edit-conflict hook; no atomic self-claim proof found | **P** supervisor-mediated handoffs, approvals and steering; no general A2A mailbox/wake evidenced | **Y** local SQLite missions/history and background daemon | **Y** installed Claude Code, Codex, Cursor, OpenCode, Kimi and Pi; inherited auth/config | **P** supervision/history, but no lease/fenced uncertain-effect contract evidenced | **P** execution receipts, approvals and edit-conflict gate; no independent merge queue evidenced |
| **Pentagon Studio** | Persistent coding-agent team workspace | **P** tasks/managers/delegation; no dependency DAG evidenced | **P** manager assignment | **Y** DMs, channels, handoffs and status reports | **Y** identity, memory, history and knowledge | **P** locally installed Claude Code/Codex with per-agent runtime | **P** persistent history; no explicit abandoned-work lease recovery found | **P** human status/review flows; no merge gate evidenced |
| **Mosaic** | Shared coding-session layer | **N/E** | **N/E** | **P** handoff, rooms, fork and human takeover; no A2A mail evidenced | **Y** synchronized sessions plus background local replica | **Y** existing terminal coding agents | **P** synchronized replica and takeover | **N/E** repo acceptance/merge gate |
| **Wato** | Shared context/skill/session platform | **P** visual workflows, not a task-dependency planner | **N/E** | **P** collaborative sessions/triggers; no A2A mailbox evidenced | **Y** versioned memory, skills, artifacts and cloud sessions | **Y** MCP connects Claude, Codex, Cursor and others | **P** version history and persisted sessions | **P** evaluation/feedback loop, not repo merge ownership |
| **HarnessRouter** | Agent-harness runtime/router | **N/E** | **N/E** | **N/E** | **Y** sessions, files and durable records | **Y** configured Codex, Claude, Hermes, Pi, DSH and other harnesses | **Y** cancellation, durable sessions and route failover | **N/E** delivery verification/merge |
| **dari** | Model router/gateway | **N/E** | **N/E** | **N/E** | **P** routing/activity state, not agent-session delivery state | **Y** base-URL replacement, BYOK and custom/self-hosted executors | **Y** request retry and fallback | **N/E** |
| **Conifer** | Model/provider/agent router | **N/E** | **N/E** | **N/E** | **P** deferred jobs and receipts | **Y** provider keys, endpoints, agents and local hardware | **Y** provider failover and budget caps | **N/E** |
| **Not Diamond** | Model/router optimization service | **N/E** | **N/E** | **N/E** | **P** session IDs retain recommendations/feedback | **Y** custom endpoints and agent workflows as candidates | **N/E** caller executes the recommendation; no retry or delivery recovery evidenced | **N/E** |
| **Orchestra** | Unresolved | **N/E** | **N/E** | **N/E** | **N/E** | **N/E** | **N/E** | **N/E** |

### Named-system matrix B — data, economics and deployment

| Product | Trajectory data | Routing | Turns / cost | Local / self-hosted | Setup | Pricing / OSS |
|---|---:|---:|---:|---:|---|---|
| **Spine Canvas / Swarm** | **Y** inspect DAG, task tree and intermediary blocks | **Y** chooses models/ensembles from 300+ | **Y** API observability and credit consumption; no delivery-turn target evidenced | **N/E** cloud product | Web app or API key | Pay-as-you-go; $5 initial credit and 1,000 credits/$1 documented; proprietary |
| **Medley** | **Y** mission history and execution receipts | **Y** assigns model/runtime per task | **P** receipts; no public fewest-turn/cost learner evidenced | **P** local state/localhost dashboard; proprietary compiled engine | Apple Silicon macOS; Claude CLI required, optional worker CLIs discovered | Free with existing subscriptions; MIT plugin shim, proprietary engine |
| **Pentagon Studio** | **P** conversations, documents, metadata and status reports | **P** manual per-agent runtime/model choice | **N/E** cost/budget meter | **P** code/execution local; collaboration metadata syncs to cloud; enterprise on-prem option | Proprietary macOS download plus sign-in | Studio free; Enterprise custom; closed source |
| **Mosaic** | **P** shared session history | **N/E** | **N/E** | **P** local replica requires hosted synchronization/internet | macOS client installs agent skills | Price not found; GPL-3.0 public client, hosted sync service not evidenced OSS |
| **Wato** | **Y** traces, audit logs, run collections, tool access and outputs | **N/E** | **P** run telemetry; public price undisclosed | **P** self-hosted/pilot domains mentioned, no standard self-host package evidenced | Cloud workspace plus OAuth MCP | Signup/demo pricing; proprietary |
| **HarnessRouter** | **Y** full User/Agent/Tool/Result timeline | **Y** feature/request-to-harness mapping | **Y** tracing and cost optimization | **Y** one-container Community Edition with durable volumes/no vendor telemetry | Docker for CE or hosted API | CE Apache-2.0/free; Cloud $0/$20/$200/$1,000 tiers, Enterprise custom at retrieval |
| **dari** | **P** request/activity/conversation telemetry | **Y** quality/cost policy, speculative routes, retries/fallbacks | **Y** request cost policy and telemetry | **Y** Apache-2.0 TypeScript router; self-hosted executors | Install CLI, create router, change model base URL | Managed price not found; router framework Apache-2.0, service proprietary |
| **Conifer** | **P** routing receipts, not full agent action trajectory | **Y** exact/auto/balanced/best across models/providers/agents/caches | **Y** detailed cost components and hard budgets | **Y** local runtime/models can run offline | Desktop/runtime/CLI or Apache SDK | Local/BYOK free; prepaid cloud, $10 default balance, top-ups from $5; SDK Apache-2.0, core distributed binaries |
| **Not Diamond** | **P** recommendation/feedback/debug session context | **Y** quality/cost/latency router, including coding-agent router | **P** cost-aware selection and request quotas; no per-route cost receipt evidenced | **P** custom endpoints/client execution; production router hosted, VPC on top tier | SDK/API/gateway integration | Free to 100k requests; $100/mo tier then $0.001/request; enterprise custom; SDKs OSS, production service not evidenced OSS |
| **Orchestra** | **N/E** | **N/E** | **N/E** | **N/E** | Exact owner/repository required | Unscored |

### Named-system source notes

- **Spine Canvas / Swarm:** [product](https://www.getspine.ai/), [Canvas](https://docs.getspine.ai/app/canvas), [agent architecture](https://docs.getspine.ai/app/advanced/agent-architecture), [context and memory](https://docs.getspine.ai/app/advanced/context-management), [API observability](https://docs.getspine.ai/api-reference/overview), [quickstart/pricing](https://docs.getspine.ai/api-reference/quickstart), and [MCP](https://docs.getspine.ai/app/advanced/mcp). Canvas is the cloud product under Spine; it is distinct from Medley.
- **Medley:** [product](https://www.medley.sh/) and [official repository/setup](https://github.com/Spine-AI/medley). `/mission` interviews the user, builds a DAG and supervises local workers across six currently listed harnesses. Its host-specific hooks include an edit-conflict gate and supervision backstop. The public repository is a MIT plugin shim that downloads a proprietary engine; “open source” would overstate its license.
- **Pentagon Studio:** [product/pricing](https://www.pentagon.run/), [introduction](https://docs.pentagon.run/introduction), [runtimes](https://docs.pentagon.run/agents/runtimes), [A2A messaging](https://docs.pentagon.run/communication/agent-to-agent), [knowledge](https://docs.pentagon.run/agents/knowledge), [identity](https://docs.pentagon.run/agents/identity), [installation](https://docs.pentagon.run/installation), and [status reports](https://docs.pentagon.run/workflow/status-reports). Local code execution does not mean fully local collaboration data: current docs say conversations, documents and metadata sync to Pentagon's cloud.
- **Mosaic:** [product](https://mosaic.inc/), [installation/local replica](https://mosaic.inc/install), [architecture memo](https://mosaic.inc/blog/memo), and [public client repository](https://github.com/emergent-inc/mosaic). This identity is conditional because “Mosaic” is highly ambiguous; it is the shared coding-session product at `mosaic.inc`.
- **Wato:** [product](https://www.watolabs.com/), [client connections](https://docs.watolabs.com/docs/connect-clients), [skills](https://docs.watolabs.com/docs/skills), [artifacts/history](https://docs.watolabs.com/docs/artifacts), and [evaluation/trace feedback loop](https://blog.watolabs.com/ai-agents-feedback-loop).
- **HarnessRouter:** [repository](https://github.com/HarnessRouter/harnessrouter), [docs](https://www.harnessrouter.ai/docs), [feature-to-harness mapping](https://harnessrouter.ai/docs/feature-key-to-harness-mapping), [tracing/optimization](https://www.harnessrouter.ai/docs/tracing-and-optimization), [pricing](https://harnessrouter.ai/pricing), and [Docker configuration](https://github.com/HarnessRouter/harnessrouter/blob/main/docker-compose.yml). It is a credible substrate for Atman's BYOA runner layer, not an objective/task owner.
- **dari:** [product](https://www.dari.dev/), [docs](https://docs.dari.dev/), [documentation index](https://docs.dari.dev/llms.txt), [Apache-2.0 router](https://github.com/mupt-ai/dari-router), and [CLI](https://github.com/mupt-ai/dari-cli).
- **Conifer:** [product](https://www.conifer.build/), [setup/docs](https://www.conifer.build/docs/), [routing receipts](https://www.conifer.build/docs/cloud/routing/), [billing](https://www.conifer.build/docs/setup/account/), [SDK/budgets](https://www.conifer.build/docs/sdk/conifer/), and [Apache-2.0 SDK](https://github.com/ConiferKit/use-conifer).
- **Not Diamond:** [product](https://www.notdiamond.ai/), [coding-agent router](https://docs.notdiamond.ai/docs/pre-trained-router-code), [routing quickstart](https://docs.notdiamond.ai/docs/quickstart-routing), [sessions](https://docs.notdiamond.ai/docs/key-concepts), [custom endpoints/workflows](https://docs.notdiamond.ai/docs/routing-between-custom-models), [pricing](https://www.notdiamond.ai/pricing), and [official GitHub organization](https://github.com/Not-Diamond).
- **Orchestra:** the name currently resolves to unrelated first-party projects including [confusionstudios/orchestra](https://github.com/confusionstudios/orchestra), [carloluisito/orchestra](https://github.com/carloluisito/orchestra), [DrSeedon/orchestra](https://github.com/DrSeedon/orchestra), and the separate [orchestra.org framework](https://docs.orchestra.org/orchestra/orchestration). Do not populate its cells until the original owner or URL is identified.

## Pain evidence: what buyers actually report

These observations are demand evidence, not competitor feature evidence. Grades inherit the T-722/T-727 method:

- **DIRECT** — the native page or HN API item was fetched.
- **INDEXED** — a current search index exposed a live Reddit URL/snippet, but Reddit origin was not fetched.
- **SECONDARY** — a public stand-in rather than the named native network.
- **INFERENCE** — synthesis, not a quotation.

| Grade | Evidence | Product implication |
|---|---|---|
| **DIRECT — HN** | In [Ask HN: Who's using Claude Code agents?](https://news.ycombinator.com/item?id=47467922), `scuff3d` reports that keeping six or seven agents busy means constant context switching. `PeterStuer` describes a three-hour agentic stint as mentally exhausting. `bayareapsycho` rejects a 100-agent target and says speeding up two to four workers is enough. | Sell the 3–7-seat coordination problem. Do not make “run a swarm” the primary promise. |
| **DIRECT — HN** | In [Ask HN: How are you managing multiple coding agents?](https://news.ycombinator.com/item?id=48839984), `dpc_01234` says the human remains the bottleneck and unblocker; `treefry` reports switching among three to five agent windows; `intended` and `UncleOxidant` describe verification/review as the bottleneck. | Measure human interventions, unblock time, review latency, duplicate work and escaped bad changes before selling raw parallelism. |
| **INDEXED — Reddit** | A live [r/AI_Agents thread](https://www.reddit.com/r/AI_Agents/comments/1s8gf6f/google_tested_180_agent_setups_multiagent_made/) summarizes a Google scaling study with the phrase “Every agent-to-agent handoff is a lossy compression of intent.” Origin was not fetched. The [official Google study](https://research.google/blog/towards-a-science-of-scaling-agent-systems-when-and-why-agent-systems-work/) is the direct source: parallel agents improved a suitable finance task by 81%, while sequential tasks degraded 39–70% and independent agents amplified errors 17.2× in the studied settings. | Route only wide, separable graph regions in parallel. Preserve explicit artifacts at handoffs. Never generalize that multi-agent is always better or always worse. |
| **INDEXED — Reddit** | A live [six-month production retrospective](https://www.reddit.com/r/AI_Agents/comments/1tlgz6o/after_6_months_of_running_ai_agents_in_production/) says framework debate was a distraction and that loops/budgets caused failures. Origin was not fetched. | Demonstrate bounded recovery and completed delivery outcomes; avoid framework-first positioning. |
| **No native X evidence** | T-727 could not authenticate the X connector from the CLI. Native X posts read: **0**. Web or indexed snippets were intentionally not substituted. | Make no X-backed demand claim until an authenticated native pass is completed. |

The evidence supports a narrow wedge: a few concurrent coding agents become difficult to coordinate and review. It does not support a mass-swarm launch narrative or a claim that customers want a new agent framework.

## Atman parity gaps to close

1. **Prove persistent wake on real harnesses.** Record end-to-end receipts for an idle Claude Code session, Codex app-server thread, Cursor persistent session, and one remote/custom harness. Show delivery, acknowledgement, dedupe and recovery after the process or machine disappears.
2. **Beat Medley's mission path on a concrete dimension.** Medley now offers a one-command DAG across six installed harnesses with routing, approvals, a live dashboard and persistent local missions. Atman's public proof should make its differences visible: arbitrary custom/local/remote workers, atomic ticket ownership, explicit lease recovery, pinned merge acceptance, cross-platform install, and inspectable MIT control-plane code.
3. **Unify the installed product.** Remove the documented split between the root/live CLI and the packaged `ticket_board.cli` entry point before public installation claims. One install must expose the same wake, runner, remote, UI, review and recovery contracts used in demos.
4. **Make “fewest turns” measurable before making it a headline.** Populate tokens and cost from each supported harness, enforce or honestly rename the turn budget, define a delivery outcome, and collect enough routes for expected-turn estimates. Today the relevant scorecard is 0/27 for both measured cost and learned expected turns.
5. **Strengthen integrated verification.** Gas Town already documents a bisecting merge queue; GitHub and Cursor expose rich logs/artifacts, and GitHub supplies security validation. Atman should make required checks, review evidence, merge-base freshness, conflicts and rollback receipts one coherent gate.
6. **Match local-operator recovery breadth.** Gas Town and NTM expose health views, stale locks, orphans, pipelines and repair actions. Atman's fail-closed semantics are a good foundation, but the common recoveries need proof and a short operator path.
7. **Ship a credible collaborative surface.** The current dashboard is local/read-only and the checked-in capture is not a hosted demo. GitHub, Cursor and Claude offer stronger remote, mobile, issue, review and notification surfaces.
8. **Publish a commercial boundary.** MIT source plus user-paid agents is clear; hosted, team, support and enterprise responsibilities are not. A buyer cannot compare total setup and operating cost without them.
9. **Integrate policy enforcement rather than rebuilding it inside scheduling.** Sondera already supplies a deterministic coding-agent reference monitor. Atman should carry policy context and policy receipts with tickets and review, while allowing Sondera or Steer to own the action decision.
10. **Close the graph on success (2026-09-11).** Stripe [Minions](https://stripe.dev/blog/minions-stripes-one-shot-end-to-end-coding-agents) one-shot unattended work to a reviewable PR; Ramp [Inspect](https://builders.ramp.com/post/why-we-built-our-background-agent) background agents verify with the same tools a human has. **Steal:** unattended persist + skip-all; success of A starts B without a human `tickets next` (T-781); review remains a gate. **Ignore:** Stripe/Ramp hosted VMs as the product, Slack-as-control-plane, Modal sandboxes, forking goose/OpenCode, 400-tool MCP sheds. Atman stays the local board: claim, deps, mail, review — not a multi-agent framework or shared-memory brain.

## Defensible wedge and launch boundaries

Atman has a credible wedge if it combines four properties in one small control plane:

- **Existing harnesses remain themselves.** The prompt-file + cwd + identity contract avoids an SDK migration and supports installed commercial CLIs, local models, HTTP agents, and custom scripts.
- **Delivery state outlives a model turn.** The objective, graph, ownership, messages, handoffs, recovery state, review and pinned merge evidence remain inspectable even when a provider session or machine is gone.
- **Uncertain effects fail closed.** Lease fencing, at-least-once wake dedupe and recovery-required states are a stronger operational contract than silently retrying a tool run that may already have changed the world.
- **The unit of learning is the completed team outcome.** Atman can eventually connect task shape and graph position to harness/model/effort, interventions, review, turns, tokens, cost and accepted outcome. Competitors mostly expose either provider telemetry or in-app traces. This is future defensibility, not a current performance claim.

The first ideal customer is a repository team already using at least two coding-agent harnesses and manually juggling three to seven concurrent workstreams. The first demo should show one narrow, genuinely parallel objective: atomic claims, dependency handoff, one interrupted worker recovered by a different provider, human review, and a pinned merge with a complete receipt. Measure operator switches, duplicate claims, orphan recovery time, time-to-review, override/reopen rate, and escaped unreviewed changes. Report token/cost only when captured rather than substituting zero.

The expansion path is then:

1. local board and mixed-agent handoffs;
2. persistent wakes and remote runners;
3. team policy and verification adapters, including Sondera/Steer receipts;
4. outcome-complete trajectories;
5. shadow route recommendations;
6. only after prospective evidence, automated agent/model/effort/budget selection.

Do not launch with “100 agents,” “learned cheapest routing,” “persistent everywhere,” “complete cost telemetry,” or “the only agent task graph.” Current evidence contradicts or does not yet prove each claim.

## Research limits

- Capability pages change quickly; this is a retrieval-date snapshot, not a permanent certification.
- **N/E** records the reviewed documentation boundary. It is not proof that a private beta or unpublished API lacks the feature.
- Pricing is the public list price visible on the retrieval date and excludes negotiated enterprise terms, taxes, Actions/compute overages and model/API consumption.
- No native X evidence was available. Reddit findings are indexed snippets because origin fetches were blocked; only HN is direct community evidence.
- Orchestra remains unscored because several unrelated current projects use that name; Mosaic is explicitly tied to the conditional `mosaic.inc` identity. Marketing comparisons should omit an ambiguous name rather than guess.
- Atman's organic scorecard is a small internal sample and demonstrates missing instrumentation, not a statistically sound ranking of agents.
