# tickets

Standalone multi-agent ticket board CLI. File-backed (`T-*.json` under `.tickets/`), no database, no daemon.

Extracted from [steer](https://github.com/advitiyavashist/steer) so the CLI can be hardened **outside** the product tree. This package does **not** vendor the Steer app.

```bash
pip install -e .
tickets --help
```

## Why this repo exists (2026-09-06 incident)

On a live Steer board, an agent ran `tickets clear` believing it would **unclaim one ticket**.

Upstream `clear` deleted **every** `T-*.json` on the board. Notes history was lost. The board had to be stub-restored.

**In this package, `tickets clear` cannot wipe a live board.**

| Command | What it does |
| --- | --- |
| `tickets clear` | Unclaim **this agent's** ticket(s). Ticket files and notes stay. |
| `tickets clear --ticket T-123` | Unclaim that ticket only (must be yours, unless `--force`). |
| `tickets board-reset --i-understand-destroy-all-tickets --confirm DESTROY-ALL-TICKETS` | Nuclear wipe of `T-*.json` only, after an automatic backup. |

`clear --all`, `clear --wipe`, and similar wipe-shaped flags are **rejected** and do not delete files.

## Install

Python 3.10+.

```bash
git clone https://github.com/advitiyavashist/tickets.git
cd tickets
python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
tickets --version
```

`pyproject.toml` registers the `tickets` console entrypoint.

## Board path

The board is a directory of ticket JSON plus coordination files:

```
.tickets/
  T-1.json
  T-2.json
  board.json
  here
  inbox.jsonl
  presence/
  secrets/          # never copied into backups
```

Resolution order:

1. `--board PATH`
2. `TICKETS_BOARD` or `TICKETS_DIR`
3. Nearest `.tickets/` walking up from the current working directory
4. `./.tickets` (created on first write)

Agent identity:

1. `--as NAME`
2. `TICKETS_AGENT`
3. `.tickets/here` (written by `tickets here`)
4. `$USER`

Backups are written to a **sibling** `.tickets-backups/` so a wipe never deletes recovery snapshots.

## Safe workflows

```bash
# Identity
tickets here cos
export TICKETS_AGENT=cos
export TICKETS_BOARD=/path/to/.tickets

# Snapshot before anything scary
tickets board-backup --reason pre-change

# Everyday agent loop
tickets pulse
tickets next
tickets claim            # or: tickets claim T-12
tickets note T-12 "blocked on API shape"
tickets done T-12 "landed in main"

# Wrong ticket? Unclaim. Do not invent a wipe.
tickets clear
tickets clear --ticket T-12

# Recover
tickets board-restore .tickets-backups/board-….tar.gz
```

Destructive commands (`board-reset`, `board-restore`) take a secret-excluding snapshot first.

**Agents: never run `board-reset`.** If you think the board is stale, `pulse`, `list`, and `board-backup`. Ask a human before any wipe.

## Commands

Production surface used on the Steer board:

| Command | Purpose |
| --- | --- |
| `create` | New ticket (`T-N.json`) |
| `assign` | Set assignee without claiming |
| `claim` | Claim a ticket or the next unblocked open one |
| `done` | Mark done (notes kept) |
| `review` | Submit for review, or `--pass` / `--fail` |
| `note` | Append a timestamped note |
| `msg` | Message an agent or a ticket |
| `who` | Active holders + presence |
| `pulse` | Counts, what you hold, next claimable |
| `here` | Register this agent |
| `update` | Title / body / status / priority / epic / sprint |
| `list` / `show` / `next` | Read the board |
| `dep add\|remove\|list` | Dependencies (claim waits on `done`) |
| `epic` / `sprint` | Grouping |
| `join` | Add yourself as collaborator |
| `clear` | **Unclaim only** |
| `board-backup` / `board-restore` | Snapshot / rollback (secrets excluded) |
| `board-reset` | Nuclear wipe with dual confirmation |

`--json` is available on the root parser for machine-readable output.

## Tests

```bash
pip install -e ".[dev]"
pytest
```

Required coverage:

- `clear` does not delete `T-*.json` and preserves notes
- wipe-shaped `clear` flags fail closed
- `board-reset` without both confirmations refuses
- backup / restore on the fixture board (secrets stay out of the archive)
- claim / done, including dependency blocking

## Adoption path (CoS)

1. **Install once** in the environment every agent shares: `pip install -e /path/to/tickets`.
2. **Point at the live board:** `export TICKETS_BOARD=/path/to/steer/.tickets` (or the real board root). Do not copy tickets into this repo.
3. **Snapshot immediately:** `tickets board-backup --reason cos-cutover`.
4. **Smoke-test on a scratch board** (`--board /tmp/tickets-smoke`) — create, claim, clear, confirm files remain, restore.
5. **Rewrite agent instructions:** `clear` = unclaim; `board-reset` is forbidden unless a human types both confirmations.
6. **Cut over PATH** so `tickets` is this package, not `tools/tickets/tickets.py` from Steer.
7. **Optional later:** in a Steer checkout, replace the in-tree launcher with a thin shim that calls the installed console script. Do not vendor Steer here.

Unknown JSON keys on existing `T-*.json` files are preserved on save so a live Steer board can be pointed at without stripping extra fields.

## Upstream note

The intended source of truth was `advitiyavashist/steer` (`tools/tickets/`, `scripts/board_backup.py`). That repository is private and was **not readable** from the extraction environment (GitHub 404). Behavior here follows the production command list, the 2026-09-06 incident, and the safety contract above. Re-diff against Steer `main` when credentials allow, especially ticket schema extras.

## License

MIT. See [LICENSE](LICENSE).
