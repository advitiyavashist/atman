# Shared seat context

This file is a **template only**. Watch/spawn inject and `tickets brief --role`
share one board-canonical store:

- `.tickets/briefs/_shared.md`
- `.tickets/briefs/roles/<role>.md`

Repo-root `roles/` and `$TICKETS_ROLES_DIR` are **not** read on inject.

Role files are markdown context for a seat. They are **not** a shared-memory
product and they do not persist conversation.

- One ticket at a time. Own worktree. Board-only comms (`tickets msg`).
- If blocked: say so early (`stuck:`). Do not wait silently.
- BYO harnesses are fine. This file is context, not a backend.
