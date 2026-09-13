# Codex global hook identity repair

T-873 live configuration repair · evidence for T-856

Removed four planner-pinned commands from the user-global Codex hooks, preserving an exact local backup and unrelated configuration. The unsafe ancestor-directory scope matched worker worktrees. A project hook does not replace global hooks: [official Codex documentation](https://developers.openai.com/codex/hooks) confirms sources are additive and changed project hooks require trust review.

Replacement registrations live only in the planner worktree. A local helper requires both the exact planner session ID and a cwd inside that worktree before performing any board check-in or context injection. The helper and its identity manifest are ignored personal files. Global configuration, native transport credentials and local identity data are not published.

Validation:

- Matching planner session/worktree emits planner context, exit 0.
- Foreign worktree, foreign session, and missing session ID return silently, exit 0.
- Fresh authenticated Codex exec in an isolated worktree runs one ticket identity command. The command resolves the explicitly assigned worker identity, exits 0, and receives no planner impersonation instruction. Global hooks were not disabled or bypassed.
- Worker check-in state is confined to a disposable board. The planner's inbox works and its native wake endpoint was rearmed with the current thread and existing roles.
- Three earlier probes were insufficient: invalid command punctuation; installation-only status; then a coordination lock denied by a read-only sandbox. They are excluded from acceptance. The final test allows the identity command's normal check-in within its isolated worktree and disposable board.

Limits and next steps:

The already-attached interactive thread does not establish automatic discovery/trust of new worktree hooks. Future attaches should use the planner worktree and review its hooks. Explicit planner CLI access and the registered native endpoint remain available. A zero-keystroke native ACK probe is a separate acceptance condition; it was requested, not claimed passed. Internal Codex subagents can share a parent session ID, whereas this test covers independently spawned CLI workers.

The T-856 installer owner still needs to prevent identity-pinned global targets, sanitize inherited provider/session variables, and fail closed on a worker startup identity mismatch before enabling watchers or claims. This local repair does not close the framework ticket. CEO decides whether to resume bounded Codex staffing. Full local evidence and backup location were reported privately through the shared board.
