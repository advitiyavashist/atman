# Shared seat context

Role files are markdown context for this seat. They are **not** a shared-memory
product and they do not persist conversation.

On `tickets watch` / `tickets spawn` the runtime injects `roles/_shared.md`
plus `roles/<role>.md` for each role on the seat. Edit a file here; the next
wake-up picks it up. A project-local `roles/` directory, or `$TICKETS_ROLES_DIR`,
overrides these framework defaults file-by-file.

- One ticket at a time. Own worktree. Board-only comms (`tickets msg`).
- If blocked: say so early (`stuck:`). Do not wait silently.
- BYO harnesses are fine. This file is context, not a backend.
