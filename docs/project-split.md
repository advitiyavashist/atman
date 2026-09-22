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

Two things bracket those four steps on a live fleet, and both were measured on
a copy of the real board rather than guessed. **Before `--apply`:** stop the
seats. The 2026-09-21 21:03 snapshot carried seven live-state receipts — five
seats with an active run and two with a live watcher — and `--apply` refuses on
every one of them until `atm spawn --stop` has run. **After `--apply`:** the
command prints one `TICKETS_DIR=<board> atm spawn <seat> --persist` line per
seat per home project, because a seat that is homed in two projects needs a run
on each board and its old session is pointed at the archive, which now takes no
new records.

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

## What the frozen archive still allows

The archive stays readable forever and takes no new records. "Takes no new
records" is enforced per *invocation*, not per command name, because a command
name is not the unit that decides whether something writes. **Six** different
shapes got past a name-only check while this was being built -- four found by
the author, two by the independent reviewer -- and each one is now refused:

| shape | example | what it would have done |
|---|---|---|
| sub-command | `atm trajectories backfill` | synthesised events into the archive's own append-only log |
| sub-command + path | `atm trajectories export --out <board>/agents/<seat>.json` | replaced a seat record outright |
| flag | `atm plan-status --write-master` | wrote a Plan section into the archive's `MASTER.md` |
| dispatch order | `atm board-mark-primary` | wrote `.primary`, making the archive **win** board resolution and route every seat back onto it |
| entry point | the packaged `atm` (`ticket_board.cli:main`) | every write, from `note` to `clear`, on the CLI a pip install provides |
| report path | `atm project split --propose --out <board>/T-100.json` | replaced that ticket with the plan, exit 0, printing "nothing was written to the board" |

`atm board-mark-primary` never reaches the refusal at all: it is dispatched
before `board_dir()` so it can repair board resolution on a board resolution
itself refuses, so it is guarded at the command instead. `atm
board-archive-shadow` is left alone deliberately — it takes an explicit
operator-named path.

Two commands stay allowed with a condition rather than outright, and the
condition is the same one in both cases: a path the operator names must not
resolve inside the board. `atm trajectories export` is allowed when `--out`
points *outside* the board, because an archive you cannot read data out of is
not an archive. `atm project` is allowed in every form, writing ones included,
because `atm project split --undo --apply` has to run on the board it is
undoing — except when its `--out` or `--manifest-out` points back inside it.

The fifth is not a command at all. `pyproject.toml` maps **both** console
scripts — `atm` and `tickets` — to `ticket_board.cli:main`, a second
implementation with its own parser, and the guard first existed only in
`tickets.py`: on a board with a `.split` marker, `python3 tickets.py note T-1 x`
refused while the packaged `atm note T-1 x` wrote the note. So the block lives
in both files. Sharing it through an import was the obvious fix and the wrong
one: a gate that every write passes through must not be switchable off by an
ImportError, and `tickets.py` also ships as a single file whose `src/` is found
relative to itself. `_shadow_board_refusal` (T-959) is duplicated for the same
reason. What keeps the copies honest is not an import but
`test_the_packaged_entry_point_refuses_the_same_writes`, which drives one
invocation matrix through both entry points and fails if either decides
differently, plus a comparison of the two allow-lists as sets.

For the same reason the marker probe reads `.split` by a literal name instead
of importing the split engine for the constant. It used to call
`_project_split().SPLIT_MARKER` and swallow `ImportError` — which returns "no
marker", i.e. turns the whole refusal off, on any install where the engine is
not importable. The documented install is a symlink, where `realpath` finds
`src/`, so that was defensive rather than a live hole; a bare *copy* of
`tickets.py` on `PATH` is the case the test covers. It also charged an engine
import (measured 18.8 ms) to every invocation that is not on the allow-list.

One honest asymmetry: the packaged entry point does not carry `atm project`
(nor `plan-status`, `dash`, `util`, `guide`, `self`), so its refusal points at
`python3 tickets.py project split --undo` rather than printing an `atm`
invocation the operator cannot run. The split engine ships with the checkout.

One refusal is deliberate and worth naming, because it looks like a mistake:
`atm knowledge` / `atm kb` are refused on the archive even though the knowledge
graph is never part of a board — `knowledge_root()` is documented as "never
place it below `.tickets/`". It was measured both ways: every reading form
(`list`, `query`, `show`, `validate`) leaves the board byte-identical, and so
does `knowledge add`, which writes only into the knowledge directory. It stays
off the allow-list anyway. The list is conservative on purpose — a read left
off costs one confusing refusal, a write left off strands records on a board
nothing reads again — and a seat's `knowledge_dir` can be configured *from the
board*, so "outside the board" is an operator's choice rather than a guarantee.
A future change would need the same path predicate `trajectories export` uses.
The refusal is pinned in the parity matrix so the next person makes that
decision rather than inheriting it.

The sixth shape was found by this ticket's independent reviewer, in the one
command the allow-list used to wave through whole. `project` was allowed
entire because `--undo --apply` has to run on the board it is undoing — but
`--out` and
`--manifest-out` are operator-named paths, exactly like `trajectories export
--out`, and nothing checked them. Pointed at a ticket on the archive,
`--propose --out` wrote the plan over it and the ticket's own `id` disappeared.
Two guards now: the allow-list entry for `project` is a predicate, so on a
frozen board this is the archive refusal; and `cmd_project` refuses an output
path inside the board on **any** board, frozen or not, because §4.11 says the
shared board is not edited except for the one `.split` marker and a plan
written into a live board destroys a record just the same.

A seventh finding from the same review is not about the refusal at all but
about the same *placement* mistake one layer down: `_alloc.json` files are
useless if an entry point never reads them. The floor check lived only in
`tickets.py`, so the packaged CLI allocated from local maximum + 1 — on a
board whose floor was 11,000 it minted `T-302`, and on another it re-minted a
`T-102` that already existed in a sibling project, which is precisely what the
disjoint blocks exist to prevent. `_alloc_floor` is now in both entry points,
and the test is parametrised per entry point with a fresh board each time: one
board cannot test two allocators, because whichever runner goes first leaves a
local maximum that drags the second one above the floor by accident.

Because "both entry points carry it" is a claim about how many entry points
exist, they are enumerated and pinned too. `pyproject.toml` installs exactly
two console scripts, both `ticket_board.cli:main`; the self-runnable modules
under `src/` are `cli.py`, `__main__.py` (which delegates to it),
`board_backup.py`, the two Claude adapters and the runner. Only the first two
touch a resolved board. The adapters and the runner talk to a server and never
name a board directory — asserted, not assumed. `board_backup.py` writes only
to an operator-named `--dest`, and it needs a `.fixture-board` marker or an
explicit `--i-understand-live` to write anywhere real: that is the same carve
out as `atm board-archive-shadow` and the same shape as `trajectories export`,
which is refused only when `--out` lands *inside* the board.
`test_the_write_surface_is_enumerated_not_assumed` fails when a new entry
point appears, so the decision has to be made rather than inherited.

One write path onto a frozen board is deliberately **not** guarded here, and
it is named rather than left to be found: `storage/legacy.py`'s
`take_ownership()` writes `.server-owned.json` into a legacy board directory
when `import_legacy_board(..., take_over=True)` runs. Pointed at an archive it
would add a record to the board that takes no new records, and it would make
the archive's `source_digests` no longer match what `undo` compares. Nothing
ships that reaches it — no CLI command and no API route calls
`import_legacy_board` today, only tests — so it is latent, and it belongs to
the Phase 2A storage layer rather than to the 4.11 CLI. Guarding it there,
when a route does exist, is the fix; guessing at it from this side is not.

`tests/test_t1118_project_split.py` pins the whole command-line surface the
allow-list exposes — every flag and sub-command of every allow-listed command,
compared against `--help` — so a flag added later to a read-only command fails
the test until somebody decides whether it writes.

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

Three things about that floor are worth knowing, because two of them were
wrong once:

- **Both entry points read it.** It lived only in `tickets.py` at first, so the
  packaged `atm` allocated from local maximum + 1 and immediately re-minted an
  id that existed in a sibling project. Correct `_alloc.json` files prove
  nothing on their own.
- **Only `T-` ids are seeded.** §4.11 is about ticket ids, so `E-` and `S-`
  allocation is untouched: two boards descended from one shared board can mint
  the same epic id for different epics. That is why `undo --merge-back` does
  not fold epics or sprints by id — see *Reversing it*. Whether the spec should
  be read as covering every prefix is an open question this migration does not
  decide.
- **The floor is a file on the board.** Delete `_alloc.json` and that board
  allocates from its local maximum again, which re-opens the collision the
  block exists to prevent. `_alloc_floor` treats a missing or unreadable file
  as "no floor" on purpose — a board that never had one must keep allocating
  as before — so the failure is silent by design, and the manifest is where the
  floors are recorded if one has to be rebuilt.

## Reversing it

```
atm project split --undo <board>/split-manifest.json            # dry run
atm project split --undo <board>/split-manifest.json --apply
```

Undo removes `.split`, restores the machine registry, and renames each new
board to `.tickets.split-undone-<ts>`. It never deletes one. A board written
to since the split refuses unless `--merge-back` is passed, which appends the
new lines back into the shared board and copies back tickets whose `updated`
is newer. Any conflict refuses and merges nothing.

**What merge-back folds, exactly.** Root-level `T-*.json`, and lines appended
to any root-level `.jsonl` — including a log the split never wrote for that
project, such as one created by the board's first `note` or by a rotation
after the split. Two defects here were found by following the reviewer's
question about allocator floors, and both are worth naming because both were
silent:

- Dedup used to hash `at`/`from`/`to`/`re`/`text` for records without an `id`
  — tickets' own identity for message lines that predate message ids. A
  trajectory event carries none of those fields, so four `note` calls
  produced four events that collapsed to two and merge-back **dropped two real
  events while reporting success**. Content is not identity in an event
  stream: three identical "update T-100 by ann" lines are three things that
  happened. Dedup now runs only on records that carry a real `id` — the case
  §4.11 means, one message appended to two project boards with a recipient
  homed in each — and a line with no id cannot collide, because
  `_appended_lines` slices strictly past the bytes the split wrote.
- Only files the manifest recorded for that board were scanned, so a project
  that received no `trajectories.jsonl` at split time and created one with its
  first write had every one of those lines dropped, with `lines none` printed
  over it.

**What merge-back does not fold, and now says so.** Epics, sprints, agent
records, briefs — anything that is not a root-level ticket or log, whether it
is new or a file the split wrote that a seat then *modified*. A check-in
rewrites `agents/<seat>.json`, and that showed up in the drift count and
nowhere else until the reviewer pointed at it; the count is not the name. They
stay on the board `undo` moves aside (nothing is deleted), and the output
names them per project:

```
NOT merged back (kept on the set-aside board, nothing deleted):
  atman            2 file(s): epics/E-002.json, sprints/S-01.json
```

**A deleted ticket is ignored; a missing log refuses.** Two different answers,
and the second one was wrong until the reviewer defeated it. merge-back only
appends and copies newer forward, so a *ticket* deleted on a project board
changes nothing on the shared board — the shared board holds the original, not
a replica. A *log* that is gone is the dangerous case: missing cannot be told
from renamed, and `mv trajectories.jsonl trajectories.operator-rotated.jsonl`
made the recorded name read as "gone, nothing appended" while the new name read
as "a file the split never wrote, so all of it is new" — two defensible rules
with a duplicated history between them. A missing recorded log is now a
conflict, and a log the split never wrote contributes only its **excess** over
what the source already holds — a multiset comparison, not set membership,
because a fourth copy of a line the source holds three times is a real fourth
event. Fixing duplication by dropping real lines would have been the same
mistake content dedup made, one layer along. Both halves are pinned, including
that a genuinely new line in such a file still comes back.

**An operator-named report is refused when it is a second NAME for a board
record, and written through a temp file and `os.replace` regardless.** The
target can be a *hard link* to a file inside the board, and
`os.path.realpath` does not see through a hard link the way it sees through a
symlink — `os.link(<archive>/T-100.json, outside.json)` then `--propose
--out outside.json` passed the containment check and destroyed the ticket
through its other name. Two layers now, because either one alone leaves
something wrong:

- the **write** replaces a directory entry rather than a file's contents, so
  the inode — and with it the board's own copy — survives even if the refusal
  is ever bypassed. That alone was the first fix, and it is not enough: the
  command still exited 0 and printed "nothing was written to the board" while
  the operator's other name for an archived ticket stopped being that ticket.
- the **refusal** resolves identity as `(st_dev, st_ino)`, not as a string,
  and names which board record the target is a twin of. It holds on any
  board, frozen or not, and in both entry points. The board is walked only
  when the target already exists as a regular file whose link count is above
  one, which is the only way it can be a second name; the ordinary case costs
  one `stat`. A hard link between two files that are both outside the board
  is not this rule and is not refused.

**Dedup by id is board-wide, not per log name.** The surviving half of the
same rename attack, and it survived two rules that each looked sufficient. A
log still under its recorded name is protected by slicing past the bytes the
split wrote; a log under an unrecognised name is protected by the excess
multiset — but only while the bytes match, and re-serialising a record
(`json.dumps(rec, sort_keys=True)`: same event, different bytes) breaks that.
The id check that should have caught it was keyed by basename, so for
`messages.2026-09-20.jsonl` — a name the shared board does not carry — the set
of ids already present was empty, and the pre-split `msg_c` was appended to
the restored shared board a second time. Measured, then fixed: the set is now
every `id` in every root-level `*.jsonl` on the shared board, so a rename or a
date stamp cannot hide one, and it is mutated as lines merge, so one message
appended to two project boards still arrives once. A genuinely new id in the
same rotated file still comes back. Lines with no `id` — every trajectory
event on the real board carries none — stay governed by the slice and the
multiset, because content is not identity in an event stream.

Epics and sprints are not folded *by id* on purpose, and the reason is
concrete: only `T-` ids get a per-project floor, so a project board minting a
new epic can land on an `E-` id the shared board already uses for a different
epic — merging that back by id would overwrite the original. Reporting it
beats guessing, and `merged back: 1 ticket(s)` printed over a board that also
grew an epic is a true sentence that leaves a false impression.

## The live-state preconditions, and what "held" means

`--apply` refuses on three live-state conditions, all read from the board's own
receipts rather than from a process table: an `agents/*.run` receipt marked
active, an `agents/*.watch.pid` whose pid is **alive**, and a `merge.lock` that
is **held** — an exclusive `flock`, not merely a file that exists, so a stale
lock from a crashed merge cannot block a migration forever. Both distinctions
are tested with the real thing: the watcher probe uses the test process's own
pid, because a made-up number would pass by being dead, and the lock probe
takes an actual `flock` after checking that the unlocked file alone applies
cleanly.

My own suite pinned only the first of the three until the independent reviewer
probed all of them; two of the rules were covered by someone else's evidence
and not by mine. They are pinned here now.

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
