# Safe clear and board backup

The `tickets clear` command never wipes a live board. This page is the
operator note for fixture-only clear and for backup/restore.

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
python3 scripts/install.py
# or: python3 scripts/install.py ~/.local/share/tickets/tickets.py

# optional editable package
pip install -e .
```

## Develop / test

```bash
pytest -q tests/
# Always use fixture boards — never point TICKETS_DIR at a live board for clear tests
```

## Commands

| Command | Behavior |
|---|---|
| `tickets clear --yes` | Fixture boards only |
| `tickets board-backup --out …` | Tar backup (redacts notes; excludes messages/secrets) |
| `tickets board-restore --archive … --dest …` | Restore into fixture dest |
