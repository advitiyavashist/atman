# Splitting the shared board into one board per project

Implements spec §4.11 / Phase 1S (T-1118). The shared working board
(`steer/.tickets`) holds tickets for four repos. This migration copies it into
one board per project and freezes the original as a read-only archive.

Nothing is moved and nothing is deleted. The shared board is written to
exactly once, at the very end, to add a `.split` marker.

## The four steps

```
atm project split --propose --out split-plan.json     # read-only
$EDITOR split-plan.json                               # the operator decides
atm project split --plan split-plan.json --dry-run --manifest-out m.json
atm project split --plan split-plan.json --apply --expect-manifest m.json
```

`--propose` attributes a ticket by its `repo` field and nothing else. A ticket
with no `repo` is listed as `unassigned` with `branch`, `artifact_dir` and
epic-majority **hints**; it is never auto-attributed, and the dry run refuses
while any *non-done* unassigned ticket still has no project. A done one may
stay in `archive`.

A target directory that already exists and is not empty is reported as a
**shadow**, with the tickets, messages and agent records it holds and the
`atm board-archive-shadow` line that moves it aside. That is the existing
local `atman/.tickets` case §4.11 names: the split never writes a project
board on top of a board something else may still read.

`--apply` builds each board in a temp directory, re-hashes every file it
wrote, compares the whole manifest against the dry run's, and only then
renames the boards into place. A refusal at any point leaves the shared board
exactly as it was.

## Why the manifest has no timestamp

The manifest is deterministic: given the same source bytes and the same plan
file it is byte-identical. (The *plan* does carry a `generated` stamp, written
once at `--propose` time; reproducing a manifest means reusing that plan file,
not re-proposing.) That is what makes "the manifest the dry run printed is
the manifest apply wrote" a real equality check rather than a field-by-field
comparison that could miss the field that mattered. The wall clock lives in
the `.split` marker and in each new board's `MASTER.md` header, which uses the
plan's own `generated` stamp.

The manifest records, for every source line of `messages*.jsonl` and
`trajectories*.jsonl`, which projects it went to — as an index into a legend
of distinct routes, so 46,069 lines cost 11 legend entries. A line routed
nowhere stays readable on the archive. `source_digests` records the sha256 of
every stable source file so `undo` can prove the archive stayed frozen.

## Cross-project dependencies

A dependency whose parent lands on another board leaves `deps` and becomes an
`external_deps` snapshot on the child:

```json
"external_deps": [{"project": "steer", "id": "T-807",
                   "released": {"kind": "accept", "sha": "…", "at": "…"}}]
```

The child's gate (`work_view.unreleased_dep_id`) reads the snapshot and never
the other board, so the boards stay independent. An unreleased parent is
recorded as `"released": false` with a reason (`done-unaccepted` or
`pending`), which shows as a `dep_unaccepted` / `dep_open` chip naming
`steer:T-807`. A parent the operator left on the archive is named
`shared-archive:T-180`, not `unassigned:T-180` — the archive is a registered,
readable project, so the reference resolves. **The migration never turns unaccepted into accepted.** The
only thing that flips a snapshot is `atm project refresh-external <ticket>`,
which reads the other board once and releases only on a real recorded accept.

A *not done* parent in another project is refused by default; the plan should
put both tickets in one project. `--allow-pending-external` records an
unreleased snapshot instead.

## Ids

`_alloc` gained a floor, read from `_alloc.json` in the directory it
allocates in. Each new board gets a **disjoint block** above the shared
board's highest id (block size 10,000). A shared floor would stop new ids
colliding with old ones but not with each other — two boards seeded the same
would both mint the next id.

## Reversing it

```
atm project split --undo <board>/split-manifest.json            # dry run
atm project split --undo <board>/split-manifest.json --apply
```

Undo removes `.split`, restores the machine registry, and renames each new
board to `.tickets.split-undone-<ts>`. It never deletes one. A board written
to since the split refuses unless `--merge-back` is passed, which appends the
new lines back into the shared board (deduplicated by message id, so one
message copied into two projects and appended in both comes back once) and
copies back tickets whose `updated` is newer. Any conflict refuses and merges
nothing.

## Not handled yet

`decisions.jsonl` (spec §5.1, Phase 2A) does not exist on any board today, so
nothing routes it and nothing is lost. When 2A creates it, it needs a routing
rule here. A decision is required to name a ticket, so the rule is the same
one messages already use — route by `ticket`, fall back to the author's home
projects — and `undo --merge-back` already folds back *any* `.jsonl` the
split copied, so only the routing rule is missing.

## Measured on a snapshot of the real shared board

A private snapshot taken **2026-09-21 21:03 +08** (1,043 tickets, 111 MB),
copied again before use. Counts are *as of that snapshot*; the live board
moves. `--propose` and `--dry-run` write nothing, which was checked by
`diff -rq` against the untouched snapshot afterwards.

| | tickets | messages | trajectory events | agent records |
|---|---|---|---|---|
| atman | 422 (258 done) | 10,170 | 14,708 | 137 |
| steer | 282 (258 done) | 7,403 | 8,749 | 81 |
| ati | 34 (32 done) | 2,241 | 2,465 | 21 |
| tickets | 8 (8 done) | 512 | 256 | 8 |
| shared-archive | 297 (172 done) | 4,324 | 5,546 | — |

Source lines routed: 46,069 (18,950 message + 27,119 trajectory). Source
files: 1,911 copied, 1,227 left on the archive, 319 live-state files not
copied (`.run` receipts, `.watch.pid`, `.identities/`, the board's own
`.git/`).

Cross-project edges after attribution by `repo` alone: **264** —
`done-unaccepted` 199, `pending` 64, `released` 1. 41 seats are homed in more
than one project.

Refusals on the plan exactly as proposed: **125 `unassigned-live`** (the
operator must attribute every non-done ticket with no `repo`) and **7
`pending-external-dep`**. 64 of the 264 edges have a not-done parent, but 57
of those have a child that stays on the archive: that child is not copied, so
it gets no snapshot and keeps the dep it always had, which is not a decision
anyone has to make. The 7 that remain are real. Live-state refusals that `--apply` would stop on: 5
active `.run` receipts and 2 live watchers — the operator runs `atm spawn
--stop` first.

One ticket on the real board has `repo` set to the board's own
`.tickets/.git`. Stripping `.git` leaves a dot-directory, which is not a repo
name, so it is unassigned rather than silently folded into the `tickets`
project.

### Scale exercise of `--apply` and `--undo`

On a second copy, with every live unassigned ticket attributed mechanically
(87 by epic-majority hint, 38 to atman) purely so the write path could be
exercised at real size — **this is not an attribution recommendation**:

- `--apply`: 4.0 s wall clock (n=1, one machine, so treat it as an order of
  magnitude and not a benchmark). 871 ticket files placed, 0 duplicated, 0
  missing; all 1,043 still on the archive. 785 byte-identical, 86 rewritten
  for `external_deps` and verified unchanged in every other field. 0 tickets
  had `review_events` or `review_head` altered. 0 log lines missing from a
  routed target and 0 invented.
- The frozen board named both of a two-home seat's boards in its refusal.
- `--undo --apply`: 1.4 s (n=1). Four boards moved aside, none deleted,
  registry restored, the archive took writes again.
