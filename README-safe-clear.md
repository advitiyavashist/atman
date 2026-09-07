# ticket-board (tickets CLI)

Standalone multi-agent ticket board CLI for coordinating agent teams on one codebase.

## Why safe clear exists

An early `tickets clear` implementation could wipe every `T-*.json` on a live board.
This package hardens that command and adds backup/restore.

## Safe clear

`tickets clear` **never** wipes a live board.

- Requires a `.fixture-board` marker file in the board directory
- Requires `--yes`
- There is **no** flag that clears a live board

```bash
echo fixture > /tmp/my-fixture/.fixture-board
TICKETS_DIR=/tmp/my-fixture tickets clear --yes
```

## Backup / restore

```bash
tickets board-backup --out /tmp/board.tgz          # fixture boards by default
tickets board-restore --archive /tmp/board.tgz --dest /tmp/restored-fixture
```

Live backups need `--i-understand-live` (note text is still redacted). Prefer fixtures for drills.

## Install

```bash
# into the historic flat layout used by agent fleets
python3 scripts/install.py
# or: python3 scripts/install.py ~/.claude/tools/tickets.py

# optional editable package
pip install -e .
```

`~/.local/bin/tickets` should symlink to `~/.claude/tools/tickets.py`.

## Develop / test

```bash
pytest -q tests/
# Always use fixture boards — never point TICKETS_DIR at a live shared board for clear tests
```

## Commands added/changed (T-108)

| Command | Behavior |
|---|---|
| `tickets clear --yes` | Fixture boards only |
| `tickets board-backup --out …` | Tar backup (redacts notes; excludes messages/secrets) |
| `tickets board-restore --archive … --dest …` | Restore into fixture dest |
