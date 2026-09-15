# tickets.py vs `src/ticket_board` ownership (T-981)

Input for the consolidation decision on T-974 / T-975. Measured on
`origin/main` @ e201436 after this hygiene change. Do not split
`tickets.py` here.

## Two delivery paths

| Path | Entry | Who uses it |
|---|---|---|
| Live monolith | repo-root `tickets.py` (`~/.local/bin/tickets` via `scripts/install_live.py`) | Seats on this machine; `tickets self` "verified release" |
| Packaged wheel | `ticket_board.cli:main` (`atm` / `tickets` console scripts) | `pip install`; `scripts/install.py` copies `cli.py` flat |

Both paths must keep board behavior identical for the commands they share.
T-981 does not merge them.

## Line counts at this SHA

| Surface | Lines |
|---| ---:|
| Root `tickets.py` | 18,089 |
| Packaged `src/ticket_board/cli.py` | 5,374 |
| Packaged tree (`src/ticket_board/**/*.py`) | 57 files |

The CEO note of 9,597 lines on `tickets.py` is stale; do not use it for
sizing the split.

## Commands `tickets.py` owns that `cli.py` does not

These `cmd_*` handlers exist only on the live monolith:

`boot`, `brief`, `capture`, `codex_hook`, `dash`, `discard`, `dispatch`,
`drive`, `guide`, `harness` (+ `harness_auth` / `harness_available` /
`harness_usage`), `hook_run`, `hooks`, `knowledge`, `objective`,
`pending`, `plan_status`, `pr_sync`, `prompt`, `quickstart`, `remote`,
`repin`, `retro`, `schedule`, `self`, `sound`, `spawn`, `stop_hook`,
`ui`, `util`, `watch`

A pip-only install therefore cannot run connect-adjacent product verbs
(`spawn`, `watch`, `hooks`, `prompt`, `ui`, `knowledge`, …). That gap is
the consolidation question, not a T-981 defect.

## Commands both copies implement

45 shared `cmd_*` names: board CRUD and status (`create`, `show`, `next`,
`claim`, `done`, `review`, `accept`, `reject`, `sync`, `merge`, …),
workforce (`join`, `retire`, `route`, `connect`), messaging (`msg`,
`inbox`), graph (`dep`, `graph`, `map`, `plan`), and the T-108 fixture
`clear` / `board-backup` / `board-restore` pair.

## What the package already owns (single implementation)

After T-981, these modules exist only under `src/ticket_board/`:

- `ticket_coordination.py` — identity, role, handover, pulse, leases
- `board_backup.py` — backup / restore

`tickets.py` imports them via `src/` on `sys.path`. Older flat installs
that still ship sibling copies remain a fallback, not a second source.

Other package modules the monolith already shims into (do not duplicate
again): `agent_checkin`, `onboard_roles`, `work_view`, `sounding`,
`seat_schedule`, `review_verdict`, `prices`, `turns`, `scheduler`,
`trajectories`.

Package-only subtrees the monolith does not reimplement: `storage/`,
`server/`, `adapters/`, `runners/`, `orchestrator/`.

## Root files still beside `tickets.py`

Not in this ticket: `session_adapters.py`, `quota_adapters.py`,
`auth_v2_contract.py`. Treat those as the next hygiene candidates if
consolidation wants one implementation per operation.

## Recommended consolidation order (not done here)

1. Keep `src/ticket_board/` as the only Python implementation.
2. Shrink root `tickets.py` to a launcher that prepends `src/` and calls
   `ticket_board.cli.main()` once `cli.py` has gained the monolith-only
   verbs above (or those verbs move into package modules).
3. Point `scripts/install_live.py` at the launcher + package tree only.
4. Retire two-entry-point tests in favor of one black-box corpus.

See `docs/architecture/core-migration-and-benchmarks.md` stage 0–1.
