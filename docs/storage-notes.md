# Storage handoff (T-179) — what T-180 and T-182 need to know

Written for someone who has not read `src/ticket_board/storage/`. The layer
implements the records behind the frozen T-178 contract; routes, auth and SSE
are **not** here and are T-180's job.

```python
from ticket_board.storage import BoardStore

store = BoardStore("board.sqlite3")          # creates and migrates on open
project = store.create_project("Demo")
agent = store.create_agent(project["id"], "backend-1", role="backend")
ticket = store.create_ticket(project["id"], "DEMO-1", "Add the widget endpoint")
store.claim_ticket(project["id"], "DEMO-1", agent["id"],
                   expected_version=ticket["version"])
```

Run `python -m pytest tests/storage/ -q` to see every behaviour below asserted.

## The five things most likely to bite you

1. **Errors are already contract-shaped. Do not re-map them.**
   Every failure is a `BoardError` subclass carrying the frozen `code`, the
   HTTP `status`, and the `details` the fixture for that code publishes. Render
   with `err.to_error_response(request_id)` and return `err.status`:

   ```python
   except BoardError as err:
       return jsonify(err.to_error_response(request_id)), err.status
   ```

   Inventing a second mapping in the route layer is how the published error
   fixtures stop matching what the server actually sends.

2. **Null versus omitted is load-bearing.** The contract sets
   `additionalProperties: false`, and its optional fields come in two kinds:
   `anyOf [..., null]` (send `null` — `owner`, `blocked_reason`,
   `last_progress_at`) and a plain type (**omit entirely** — `outcome`, `role`,
   `notes`, `subject_type`). The store already omits the second kind. If you
   rebuild response bodies field-by-field instead of passing records through,
   re-read `_omit_none` in `board.py` first — emitting `"outcome": null` fails
   schema validation, and it is not obvious from reading the spec.

3. **`expected_version` is required on every mutation, and the 409 tells the
   caller everything.** `TicketVersionConflict.details` carries
   `expected_version` and `actual_version`, so the client re-reads in one round
   trip. Pass the client's value straight through; do not read-then-write on
   the client's behalf, which silently reintroduces last-write-wins.

4. **`request_id` idempotency is handled here, not in the route.** Pass it into
   the store call. A replay with an identical body returns the original result
   without re-applying or re-auditing; a replay with a different body raises
   `RequestIdReused` (409). It is scoped per project.

5. **`dependency_blocked` is computed on read.** There is no column and no such
   state — `TicketState` is `open|claimed|review|done|blocked`. Do not add it to
   a filter as if it were a state; filter on the boolean in the serialized
   record.

## Concurrency, honestly stated

`claim_ticket` is atomic across processes and threads: the state check,
capacity check and write happen in one `BEGIN IMMEDIATE` transaction.
`tests/storage/test_claim_race.py` races eight OS processes over five rounds
and asserts exactly one winner per round.

What `BEGIN IMMEDIATE` does **not** do is prevent the double write on its own —
SQLite's snapshot isolation already does that. Measured with deferred
transactions, the losers still fail, but they fail with
`OperationalError: database is locked` instead of a clean 409. So the immediate
transaction is what makes the failure *contract-shaped*, not what makes it
safe. If you add a write path, use `write_txn` from `db.py`; do not open your
own deferred transaction.

Each thread gets its own connection (`BoardStore.conn` is a thread-local
property), so a threaded server can share one `BoardStore`. Correctness lives
in the database, not in a mutex in the object.

## Audit and the event stream

`audit_events` is append-only, enforced by SQLite triggers — `UPDATE` and
`DELETE` raise. Every mutation writes one row.

For SSE (T-180): `store.audit_trail(project_id, after_seq=N)` is your cursor
read. Each row carries `event_id` in the frozen `evt_<12 digits>_<6>` form,
zero-padded so **lexicographic order matches sequence order**. That is what
makes `Last-Event-ID` reconnect correct; if you generate stream ids anywhere
else, preserve that property.

## Legacy import and the ownership lock (operator-facing)

`import_legacy_board(store, path)` reads a directory of `T-NNN.json` files and
returns an `ImportReport`. Verified against the real 119-ticket Steer board:
119/119 tickets, 42/42 dependency edges, nothing skipped, no unmapped states.

- **Originals are never modified or deleted.** Rollback is
  `release_ownership(path)`; the files are untouched, so the old CLI resumes.
- **Legacy ids are re-prefixed.** `T-179` becomes `LEG-179`, because the
  contract's `TicketId` pattern needs at least two characters before the dash.
  The number is preserved. If you surface these to operators who cite "T-179",
  say so in the UI.
- **A dependency on a ticket not in the source board is reported**, in
  `report.dropped_dependencies`, never silently dropped. Same for unmapped
  legacy states (`report.unmapped_states`) — they import as `open` and are
  counted, not guessed.
- **Ownership is a visible sentinel file** (`.server-owned.json`) in the legacy
  directory. `assert_writable(path)` raises `LegacyWriterActive` (409) while it
  exists. Call it from any legacy write path; an operator who sees their CLI
  refuse can open the file and read why.

## Backup and rollback

`backup_database(conn, dest)` uses SQLite's online backup API, not a file copy,
so a snapshot taken during a concurrent write is still consistent.
`restore_database(snapshot, db_path)` keeps a copy of what it replaced and
**deletes the `-wal`/`-shm` sidecars**. That last part is not tidiness: after an
unclean shutdown the WAL holds the post-snapshot writes, and copying a snapshot
over the main file alone lets SQLite replay them, so the restore silently does
nothing. `tests/storage/test_backup_rollback.py` demonstrates both halves.

## What this lane deliberately does not do

No HTTP, auth, sessions-as-credentials, SSE transport, rate limiting or CSRF —
all T-180. No master lease acquisition loop; the `master_lease` table and
`Assignment` records exist and `store.assign(...)` queues one, but the routing
policy is T-182. Messaging records (`Member`, `Channel`, `Message`, `Delivery`,
`WakeJob`, `Run`, `RunnerLease`) are **not** implemented — they are T-187's,
and their contracts are already frozen in `openapi.yaml`.

Nothing here has been served over a network or measured under load. The
concurrency claims above are from the tests in this repo on one machine, and
they are claims about correctness, not throughput.
