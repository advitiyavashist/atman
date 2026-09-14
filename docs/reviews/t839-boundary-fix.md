# T-839 supported identity boundaries

Base: Atman `3679053`, integrating the accepted explicit-seat, packaged
session identity, Cursor hook pinning and target-worktree behavior from
`feca2f5`. This repair closes the two T-844 failures without editing a
canonical seat or the prior author's worktree.

## Changes

`ticket_board.session_boundary` ships in the wheel and owns the remote
wrapper renderer and Claude hook filter. Root `hooks remote` uses the same
renderer as the installed-wheel normal-command tests.

The wrapper overwrites `TICKET_AGENT` and `TICKET_SEAT`, removes inherited
provider session IDs and installs a unique `wrapper:<owner>:<id>` session
key. That key belongs to this generated wrapper and remains stable across its
commands so a worker join can retain continuity without adopting its parent.

Claude permission/settings inheritance filters individual Atman commands out
of `settings.local.json`, preserving custom sibling hooks and permissions.
Worker pinning also sanitizes an already-present local settings file before
installing the worker's own hooks in `settings.json`. Parent settings remain
unchanged. Hook installation uses the same filter, preserving custom commands
that shared an entry with an older Atman command.

The packaged checkin import supports both package execution and the direct
source script path. Both still use the T-836 canonical checkin implementation.

## Evidence

System Python **3.9.6**, pip **21.2.4**. The adapter test builds a real wheel
without build isolation, installs it into a new virtual environment using
`--no-index --no-deps`, and runs normal commands from a disposable board with
no checkout `PYTHONPATH`.

```sh
PYTHONPATH=src:. python3 -m pytest -q -p no:cacheprovider \
  tests/test_t839_boundary_adapters.py \
  --basetemp=/private/tmp/atman-t839-boundary-tests
```

**5 passed.** Root-generated remote wrappers and the wheel's shared renderer
both show worker-only mail, post as the worker and join under their own session
despite inherited parent seat/session variables. Tests assert byte-identical
parent agent/session records and unchanged parent workforce, roles and aliases.
Claude tests cover inherited and pre-existing worker local settings, preserve
custom hooks/permissions and execute the pinned worker inbox command without
revealing parent mail.

The launch regression selection covers T-839, identity precedence/session
scope, T-804, T-808, T-633, Claude inheritance, T-327 and T-836. It produced
**61 passes** initially, plus two baseline plain-source import failures and one
sandbox `ps` denial. After the direct import repair, the targeted package and
boundary selection passed **9/9**. The sandbox-denied persistent-watcher case
passed **1/1** with host process visibility.

```sh
PYTHONPATH=src:. python3 -m pytest -q -p no:cacheprovider \
  tests/test_t839_boundary_adapters.py tests/test_t836_packaged_checkin.py \
  tests/test_t804_identity_isolation.py \
  -k 'boundary or cli_imports or tickets_help or cli.py' \
  --basetemp=/private/tmp/atman-t839-boundary-final

PYTHONPATH=src:. python3 -m pytest -q -p no:cacheprovider \
  tests/test_t808_persist_delivery.py::test_live_persist_watch_survives_queued_offline_poke \
  --basetemp=/private/tmp/atman-t839-live-poke-host
```

This evidence covers generated adapters and installed-wheel normal commands.
The wheel did not gain an unsupported `hook-run` CLI, and no live Cursor model
or native session wake was executed. A separate verifier must approve the exact
submitted SHA before merge.
