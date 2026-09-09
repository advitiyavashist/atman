# T-631 live release activation

Activated the reviewed T-623 release on 2026-09-09 from Atman commit
`3a641e9e2d9435cfb49c79a3287981f45d4fb17c`.

## Provenance and rollback

- Previous release: `0dbd18ccc63852c28280ec77eaec04e1f82a4d30`
- Previous launcher SHA-256: `b6e101f58ebd4fde87ec048b3ecffb69953ceddf304c5951f7b65607f17c5823`
- Active release: `3a641e9e2d9435cfb49c79a3287981f45d4fb17c`
- Active launcher SHA-256: `86569dbf42fb11f81dcd5cb9e0825bb5df32b5bd16e446b714f1da5fe03aa160`
- Launcher: `/Users/kavana/.claude/tools/tickets.py`
- Global command: `/Users/kavana/.local/bin/tickets`, a symlink to the launcher
- Automatic backup: `/Users/kavana/.claude/tools/tickets-releases/previous-b6e101f58ebd4fde87ec048b3ecffb69953ceddf304c5951f7b65607f17c5823`

The reviewed rollback command is:

```sh
python3 scripts/install_live.py --ref 0dbd18ccc63852c28280ec77eaec04e1f82a4d30 --activate --expected-live-sha256 86569dbf42fb11f81dcd5cb9e0825bb5df32b5bd16e446b714f1da5fe03aa160
```

The rollback commit is present in the Atman repository. The rollback was not
executed because the active release passed its checks.

## Verification

All commands were run from `/private/tmp`, with `PYTHONPATH` removed and the
board and agent supplied explicitly. The global CLI passed:

- `tickets --version`: exact active commit and `verified release`
- `tickets turns --since 2026-09-06 --json`: valid JSON
- `tickets util --json`: valid JSON
- `tickets route`: completed
- `tickets ui --json`: valid JSON
- `tickets prompt --master`: emitted the objective-driven master prompt

The installed `--version` path invokes `release_status()`, which hashes every
manifest entry and rejects unexpected package paths before reporting the
release as verified.

Claude's global hooks still resolve through the live launcher:

- `SessionStart`: `/Users/kavana/.claude/tools/tickets.py board`
- `Stop`: `/Users/kavana/.claude/tools/tickets.py stop-hook`

The focused release suite passed 13 tests, including disposable staged-copy
checks for equal-length byte tampering and unmanifested package-file drift:

```text
13 passed in 65.16s
```

No live release bytes were modified for the adversarial checks.

## Command latency

Five runs per command from `/private/tmp`; values include Python startup,
manifest hashing, board loading, and JSON rendering.

| Command | p50 | max | output bytes |
|---|---:|---:|---:|
| `--version` | 116.43 ms | 139.57 ms | 75 |
| `prompt --master` | 153.18 ms | 154.35 ms | 3,723 |
| `route` | 163.39 ms | 164.94 ms | 385 |
| `turns --json` | 448.59 ms | 570.87 ms | 103,895 |
| `ui --json` | 635.30 ms | 662.47 ms | 255,853 |
| `util --json` | 751.00 ms | 810.52 ms | 20,213 |

These measurements are a local operational baseline. The large report
commands scale with the current board and should not be treated as pure CLI
startup measurements.
