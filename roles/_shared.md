# Shared seat context

This file is a **template only**. Watch/spawn inject and `tickets brief --role`
share one board-canonical store:

- `.tickets/briefs/_shared.md`
- `.tickets/briefs/roles/<role>.md`

Repo-root `roles/` and `$TICKETS_ROLES_DIR` are **not** read on inject.

Role files are markdown context for a seat. They are **not** a shared-memory
product and they do not persist conversation. KB v0 is board docs +
tracked `docs/knowledge/` + this brief store (E-013). Do not auto-sync
the catalog here.

- One ticket at a time. Own worktree. Board-only comms (`tickets msg`).
- If blocked: say so early (`stuck:`). Do not wait silently.
- BYO harnesses are fine. This file is context, not a backend.
- **steer.md** is a separate runtime policy product. Do not mix Steer claims into Atman chrome.

Full operator path: [docs/onboarding/role-context.md](../docs/onboarding/role-context.md) · [docs/byoa.md](../docs/byoa.md)
