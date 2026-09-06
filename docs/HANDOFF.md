# Ticket Board V1 — implementation handoff

Design repository: `/Users/kavana/Downloads/tickets-design`, branch `codex-interface-design` of `advitiyavashist/tickets`.
Read `docs/interface-v1.md`, open `docs/prototype.html`, and use `docs/implementation-plan.json` for acceptance and path boundaries.
Delivery tracker: existing Steer board, E-010. Queue assignments below do not claim work on behalf of active workers. Start after current claimed work and dependencies finish. No changes to current master ownership.

| Ticket | Assigned lane | Dependencies | Deliverable |
|---|---|---|---|
| T-178 | claude-opus | none | Define board service contract and fixture pack |
| T-179 | claude-opus | T-178 | Implement transactional board state and legacy import |
| T-180 | claude-opus | T-179 | Build board API and scoped access |
| T-181 | claude-sonnet | T-178 | Connect Claude sessions with hook adapter and doctor |
| T-182 | claude-fable | T-180 | Implement master lease and assignment loop |
| T-183 | codex | T-178 | Build dashboard screens against fixtures |
| T-184 | codex | T-180, T-181, T-182, T-183 | Wire dashboard, enrollment and review to live API |
| T-185 | cursor-2 | T-184 | Verify connection, concurrency and agent recovery |

Implementation branches and PRs belong to **tickets**, not **steer**. Cursor coordinates availability and reviews. Do not use Steer's automatic merge command to integrate a tickets branch. Submit repository URL, branch, exact SHA, checks, and handoff notes; master reviews in the corresponding repository. Keep one claimed ticket per worker. Master may queue a replacement owner if a named agent is unavailable.

First task: T-178 freezes API/schema fixtures. T-181 adapter and T-183 dashboard then run alongside T-179 storage. T-180 API enables T-182 master; T-184 connects the full product, then T-185 verifies it.

