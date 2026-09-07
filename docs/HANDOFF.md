# Developer handoff

Public-facing implementation notes for the ticket board / Atman runtime CLI.

## Start here

| Doc | Purpose |
|---|---|
| [interface-v1.md](interface-v1.md) | Product contract and screen model |
| [prototype.html](prototype.html) | UI reference (fixtures) |
| [contracts/](contracts/) | Frozen OpenAPI + JSON fixtures |
| [connect-claude.md](connect-claude.md) | Claude Code hook enrollment |
| [LIVE_CLI.md](LIVE_CLI.md) | Staging and activating the shared CLI entrypoint |

## Repo layout

- `tickets.py` — flat install entrypoint (symlinked to `~/.local/bin/tickets`)
- `src/ticket_board/` — package implementation (server, storage, adapters)
- `tests/` — pytest suite; always use fixture boards, never a live `.tickets/`
- `ui/` — dashboard (Vite + React)

## Before you hand over a board

From a checkout that contains `.tickets/`:

```sh
python3 scripts/handoff-check.py
```

Commit tracked handoff docs before clearing or compacting a shared board.

## Public release hygiene

Before making this repository public, run:

```sh
python3 scripts/public_scrub_check.py
```

See [public-release-scrub.md](public-release-scrub.md) for the checklist and evidence template.
