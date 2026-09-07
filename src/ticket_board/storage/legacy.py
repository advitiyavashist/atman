"""Legacy board import and server ownership.

The legacy board is a directory of `T-NNN.json` files plus `agents/`, `epics/`,
`sprints/`, `briefs/`, `coordination/` and `messages.jsonl`, written directly by
every agent's CLI. Importing it into SQLite creates a second writer for the same
data, which is the actual risk here -- not the import itself but the window
afterwards where half the fleet still writes files and the server writes rows.

So the import is deliberately conservative:

- **Originals are never modified or deleted.** Rollback is "stop the server and
  delete the sentinel"; the files are still exactly as they were.
- **Nothing is dropped silently.** Every key of every source ticket either maps
  onto a contract field or is archived verbatim, and the report says which.
  A key the importer has never seen is archived *and* counted, so a legacy
  field added after this code was written cannot disappear quietly.
- **The whole import is one transaction.** A dependency cycle, a duplicate id
  or a disk error two thirds of the way through leaves the database exactly as
  it was -- not a half-imported project that a re-run would duplicate.
- **Server ownership is explicit and visible in the directory itself.** A
  sentinel file says the board is owned; `assert_writable()` refuses legacy
  writes while it exists, so a stale CLI fails loudly with
  `legacy_writer_active` instead of writing into a board nobody reads.

## Where the legacy fields go

| legacy key            | destination                                        |
|-----------------------|----------------------------------------------------|
| `title`               | `tickets.title` (truncated at 200, full archived)  |
| `body`                | `tickets.outcome` (truncated at 4000, full archived)|
| `role`, `status`      | `tickets.role`, `tickets.state` via `STATUS_MAP`   |
| `deps`                | `ticket_dependencies`                              |
| `owner`               | `tickets.owner` -> a real `agents` row             |
| `created`/`updated`   | `tickets.created_at` / `updated_at`                |
| `claimed_at`          | `tickets.claimed_at`                               |
| `notes[]`             | `ticket_updates`, each keeping the note's own `at` |
| everything else       | `legacy_ticket_fields` (queryable, one row/field)  |

`priority`, `epic`, `sprint`, `needs`, `suggested`, `done_at`, `review_at`,
`commit`, `branch` and `pr` have no field in the frozen T-178 contract. They are
archived rather than dropped, and every one of them is listed in
`report.archived_fields` with a count. That list is the input to T-211's
contract-amendment map; this lane does not amend a frozen contract.

## Why review evidence does not become `GitEvidence`

The contract's `Sha` is `^[0-9a-f]{40}$` and `GitEvidence.pr_url` is a URI. On
the board this was written against, **none** of the 106 `commit` values match --
they are `branch@shortsha` (`alice@af27511`) -- and `pr` is a bare number
(`"104"`). Writing those into `tickets.evidence` would produce records that
fail the published schema the moment T-180 serves them, and inventing a 40-hex
sha would be worse. So evidence is archived verbatim, the mismatch is reported
in `report.contract_mismatches` with counts, and `evidence`/`reviews` rows are
written only for values that genuinely satisfy the contract. Pass a
`sha_resolver` if you can expand short shas against a real repository.

T-224 promoted `GitEvidence.repository` from optional to required. The legacy
record has no repository concept at all -- not even a real 40-hex `commit`
carries one -- so today `_evidence()` withholds evidence unconditionally,
`sha_resolver` included, and counts a `"no repository identity"` mismatch
instead. A `sha_resolver` alone no longer produces contract-valid evidence;
supplying real repository identity is T-215's work.
"""

import datetime as _dt
import json
import os
import re
import sqlite3
from pathlib import Path

from . import ids
from .board import SYSTEM_ACTOR, _json
from .db import write_txn
from .errors import DependencyCycle, LegacyWriterActive, MalformedRequest

SENTINEL_NAME = ".server-owned.json"

# Legacy status -> contract TicketState. The legacy board has no 'review'
# distinct from an in-progress claim for some statuses; anything unrecognised
# is imported as 'open' and counted in `unmapped_states` rather than guessed.
STATUS_MAP = {
    "open": "open",
    "todo": "open",
    "to do": "open",
    "claimed": "claimed",
    "in progress": "claimed",
    "in_progress": "claimed",
    "doing": "claimed",
    "review": "review",
    "in review": "review",
    "in_review": "review",
    "done": "done",
    "closed": "done",
    "blocked": "blocked",
}

# Contract limits we have to respect when filling a real column. Exceeding one
# is not a reason to drop the value: the column takes the truncated form the
# API can legally serve, and `legacy_ticket_fields` keeps the original.
TITLE_MAX = 200
OUTCOME_MAX = 4000
UPDATE_BODY_MAX = 4000

# `legacy_documents` has no contract column to bound it -- it is a resource
# bound, not a schema limit, so it is env-overridable rather than fixed like
# the ones above. Default is generously above the largest real file on the
# board this was tuned against (briefs/*, MASTER.md) while still keeping a
# runaway file from landing whole in one SQLite row inside the same atomic
# transaction as the rest of the board (T-231).
LEGACY_DOCUMENT_MAX_BYTES = int(
    os.environ.get("TICKET_BOARD_LEGACY_DOCUMENT_MAX_BYTES", 1_000_000)
)

SHA_RE = re.compile(r"^[0-9a-f]{40}$")
# Full RFC 3339, not just the `...Z` form the CLI usually writes. Two notes on
# the real board carry `2026-09-05T18:11:02.960071+00:00` -- fractional seconds
# and a numeric zero offset. That is valid RFC 3339 and it is UTC, so rejecting
# it and falling back to the ticket's `updated` silently moved those two notes
# by 83 minutes. A zero offset is kept verbatim; a non-zero one is converted to
# UTC and counted, because the contract says timestamps are always UTC.
TIMESTAMP_RE = re.compile(
    r"^(\d{4}-\d{2}-\d{2})[Tt](\d{2}:\d{2}:\d{2})(\.\d+)?"
    r"([Zz]|[+-]\d{2}:\d{2})$"
)
ZERO_OFFSETS = ("Z", "z", "+00:00", "-00:00")

# Keys the importer understands. Anything outside this set is archived *and*
# counted in `report.unknown_fields` -- that counter is the whole point, it is
# what makes "the importer silently ignored a new field" impossible.
MAPPED_TICKET_FIELDS = frozenset({
    "id", "title", "body", "role", "status", "deps", "dependencies",
    "owner", "created", "updated", "claimed_at", "notes",
})
ARCHIVED_TICKET_FIELDS = frozenset({
    "priority", "epic", "sprint", "needs", "suggested", "done_at",
    "review_at", "commit", "branch", "pr", "messages",
})
KNOWN_TICKET_FIELDS = MAPPED_TICKET_FIELDS | ARCHIVED_TICKET_FIELDS

# The archive. These tables are owned by the importer, not by the contract:
# nothing here is ever served as part of a `Ticket`, so `additionalProperties:
# false` is not at risk. They exist so an operator can answer "what was the
# priority of LEG-95 on the old board" without going back to the JSON files.
ARCHIVE_DDL = (
    """
    CREATE TABLE IF NOT EXISTS legacy_ticket_fields (
        project_id TEXT NOT NULL,
        ticket_id  TEXT NOT NULL,
        field      TEXT NOT NULL,
        value      TEXT NOT NULL,
        PRIMARY KEY (project_id, ticket_id, field),
        FOREIGN KEY (project_id, ticket_id)
            REFERENCES tickets(project_id, id) ON DELETE CASCADE
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS legacy_documents (
        project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
        kind       TEXT NOT NULL,
        name       TEXT NOT NULL,
        content    TEXT NOT NULL,
        PRIMARY KEY (project_id, kind, name)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS legacy_messages (
        project_id  TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
        seq         INTEGER NOT NULL,
        sent_at     TEXT,
        sender      TEXT,
        recipient   TEXT,
        re_ticket   TEXT,
        -- `re_ticket` is the legacy id the message actually cited ("T-213");
        -- `re_ticket_id` is the same ticket under its imported key ("LEG-213"),
        -- so the archive joins to `tickets` without the caller re-deriving the
        -- prefix rule. Null when the message cites a ticket that is not on the
        -- board.
        re_ticket_id TEXT,
        body        TEXT,
        raw         TEXT NOT NULL,
        PRIMARY KEY (project_id, seq)
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_legacy_messages_re"
    " ON legacy_messages(project_id, re_ticket_id)",
    # One row per source note, in source order, naming the ticket_updates rows
    # it became. Needed for two reasons that are easy to miss: 59 notes on the
    # real board share an `at` with a sibling, so `ORDER BY created_at, id`
    # cannot recover source order on its own; and a note longer than the
    # contract's 4000-char update body is stored as several rows.
    """
    CREATE TABLE IF NOT EXISTS legacy_note_map (
        project_id TEXT NOT NULL,
        ticket_id  TEXT NOT NULL,
        ordinal    INTEGER NOT NULL,
        update_ids TEXT NOT NULL,
        extra      TEXT NOT NULL DEFAULT '{}',
        PRIMARY KEY (project_id, ticket_id, ordinal),
        FOREIGN KEY (project_id, ticket_id)
            REFERENCES tickets(project_id, id) ON DELETE CASCADE
    )
    """,
)


def sentinel_path(legacy_dir):
    return Path(legacy_dir) / SENTINEL_NAME


def take_ownership(legacy_dir, *, owner, note=None):
    """Mark a legacy directory as server-owned. Refuses if already owned."""
    path = sentinel_path(legacy_dir)
    if path.exists():
        existing = json.loads(path.read_text())
        raise LegacyWriterActive(legacy_dir, existing.get("owner"))
    payload = {
        "owner": owner,
        "claimed_at": ids.now(),
        "note": note or "Board is served by ticket-board; legacy writes refused.",
        "pid": os.getpid(),
    }
    path.write_text(json.dumps(payload, indent=2) + "\n")
    return payload


def release_ownership(legacy_dir):
    """Hand the directory back to the legacy CLI. This is the rollback step."""
    path = sentinel_path(legacy_dir)
    if path.exists():
        path.unlink()
        return True
    return False


def ownership(legacy_dir):
    path = sentinel_path(legacy_dir)
    if not path.exists():
        return None
    return json.loads(path.read_text())


def assert_writable(legacy_dir):
    """Guard for the legacy write path.

    The legacy CLI calls this before writing a ticket file. While the server
    owns the board this raises `legacy_writer_active` (409), which is the whole
    point of the sentinel: a divergent write is worse than a refused one.
    """
    current = ownership(legacy_dir)
    if current is not None:
        raise LegacyWriterActive(legacy_dir, current.get("owner"))
    return True


# ------------------------------------------------------------------ readers

def read_legacy_board(legacy_dir, *, report=None):
    """Parse a legacy board directory into ticket dicts. Read-only.

    A file that is not a ticket is not an error and is not skipped work: the
    board keeps `master.json`, `roles.json` and `workforce.json` alongside the
    tickets. Those land in `report.non_ticket_files` and are archived by the
    import. A file that is *meant* to be a ticket and cannot be read lands in
    `report.skipped`, which is what the caller checks before trusting a count.
    """
    directory = Path(legacy_dir)
    if not directory.is_dir():
        raise MalformedRequest("Not a legacy board directory.",
                               {"path": str(legacy_dir)})
    tickets = []
    for path in sorted(directory.glob("*.json")):
        if path.name == SENTINEL_NAME:
            continue
        try:
            data = json.loads(path.read_text())
        except (ValueError, OSError) as exc:
            if report is not None:
                report.skipped.append({
                    "kind": "ticket", "id": path.stem,
                    "reason": "unreadable json: {}".format(exc.__class__.__name__),
                })
            continue
        if not isinstance(data, dict) or "title" not in data:
            if report is not None:
                report.non_ticket_files.append(path.name)
            continue
        data.setdefault("id", path.stem)
        tickets.append(data)
    return tickets


def read_legacy_documents(legacy_dir, *, report=None):
    """Everything on the board that is not a ticket and not a message.

    `agents/`, `epics/`, `sprints/`, `briefs/`, `coordination/` and the
    board-level `*.json`/`*.md` files. Returned as `(kind, name, content)` with
    the content verbatim, because the point of the archive is that an operator
    can read the original, not our re-rendering of it -- *below*
    `LEGACY_DOCUMENT_MAX_BYTES`. Every file is decoded with `errors="replace"`,
    not just ones over the cap: a legacy board is the realistic place to meet
    stray bytes (a hand-edited brief, latin-1 pasted into a note, a truncated
    write from a crashed agent), and refusing the whole board because one
    under-cap file has one bad byte is worse than importing it with the byte
    replaced. A file over the cap, or one whose replacement characters expand
    past the cap, is truncated at the byte boundary (re-decoded so a split
    multi-byte character at the cut does not raise) rather than imported whole
    or dropped: an archive that refuses the whole board because one brief grew
    too large is worse than an archive with one clipped document, and
    truncating still names the loss in `report.truncated_fields` instead of
    hiding it the way a silently-oversized row would.
    """
    directory = Path(legacy_dir)
    documents = []

    def _add(kind, path):
        try:
            size = path.stat().st_size
            with path.open("rb") as fh:
                raw = fh.read(min(size, LEGACY_DOCUMENT_MAX_BYTES))
            # Always decode tolerantly, whether the file is over the cap or
            # not: an under-cap file can still hold invalid UTF-8 (a
            # hand-edited brief, latin-1 pasted into a note), and a strict
            # decode there used to raise UnicodeDecodeError -- a ValueError
            # the surrounding `except OSError` does not catch -- aborting the
            # whole board import over one bad byte.
            content = raw.decode("utf-8", errors="replace")
            if (size > LEGACY_DOCUMENT_MAX_BYTES
                    or len(content.encode("utf-8")) > LEGACY_DOCUMENT_MAX_BYTES):
                # Either the source file itself was over the cap, or
                # replacement characters expanded an under-cap file's decoded
                # form past it. Either way, re-clip and say so: drop only a
                # partial trailing code point after clipping.
                content = content.encode("utf-8")[:LEGACY_DOCUMENT_MAX_BYTES].decode(
                    "utf-8", errors="ignore")
                if report is not None:
                    report._bump(report.truncated_fields,
                                 "document:{}/{}".format(kind, path.name))
        except OSError:
            return
        documents.append((kind, path.name, content))

    for path in sorted(directory.glob("*.json")):
        if path.name == SENTINEL_NAME:
            continue
        try:
            data = json.loads(path.read_text())
        except (ValueError, OSError):
            continue
        if isinstance(data, dict) and "title" in data:
            continue  # a ticket; imported as a row, not archived as a document
        _add("board_file", path)
    for path in sorted(directory.glob("*.md")):
        _add("board_file", path)

    for sub, kind in (("agents", "agent"), ("epics", "epic"),
                      ("sprints", "sprint"), ("briefs", "brief"),
                      ("coordination", "coordination")):
        folder = directory / sub
        if not folder.is_dir():
            continue
        for path in sorted(folder.iterdir()):
            # `.lock` files are transient mutexes held by a running CLI, not
            # board content. Importing one would archive a fact about a process
            # that no longer exists.
            if not path.is_file() or path.suffix == ".lock":
                continue
            _add(kind, path)
    return documents


def read_legacy_messages(legacy_dir, *, report=None):
    """Parse `messages.jsonl`. One dict per line; malformed lines are reported."""
    path = Path(legacy_dir) / "messages.jsonl"
    if not path.is_file():
        return []
    messages = []
    try:
        lines = path.read_text().splitlines()
    except OSError:
        return []
    for number, line in enumerate(lines, start=1):
        if not line.strip():
            continue
        try:
            data = json.loads(line)
        except ValueError:
            if report is not None:
                report.skipped.append({"kind": "message",
                                       "id": "messages.jsonl:{}".format(number),
                                       "reason": "unreadable json line"})
            continue
        if not isinstance(data, dict):
            if report is not None:
                report.skipped.append({"kind": "message",
                                       "id": "messages.jsonl:{}".format(number),
                                       "reason": "line is not an object"})
            continue
        messages.append((number, data, line))
    return messages


def read_legacy_agents(legacy_dir):
    """Agent runtime records from `agents/*.json`, keyed by agent name."""
    folder = Path(legacy_dir) / "agents"
    agents = {}
    if not folder.is_dir():
        return agents
    for path in sorted(folder.glob("*.json")):
        try:
            data = json.loads(path.read_text())
        except (ValueError, OSError):
            continue
        if isinstance(data, dict) and data.get("owner"):
            agents[str(data["owner"])] = data
    return agents


# ------------------------------------------------------------------- report

class ImportReport:
    """What the import did, in enough detail to verify it without the db."""

    def __init__(self):
        self.source_tickets = 0
        self.imported_tickets = 0
        self.source_dependencies = 0
        self.imported_dependencies = 0
        self.source_notes = 0
        self.imported_updates = 0      # source notes that made it in
        self.imported_update_rows = 0  # ticket_updates rows, > notes when split
        self.imported_agents = 0
        self.imported_documents = 0
        self.imported_messages = 0
        self.imported_reviews = 0
        self.dropped_dependencies = []
        self.skipped = []
        self.non_ticket_files = []
        self.unmapped_states = {}
        # field -> how many source tickets carried it. `archived` is "kept, but
        # not as a contract field"; `unknown` is "we had never heard of it",
        # which is the one a reviewer should actually read.
        self.archived_fields = {}
        self.unknown_fields = {}
        self.truncated_fields = {}
        self.contract_mismatches = {}
        self.project_id = None

    def _bump(self, bucket, key, amount=1):
        bucket[key] = bucket.get(key, 0) + amount

    @property
    def counts_preserved(self):
        """True only when nothing was skipped or dropped.

        Deliberately strict. The previous version of this property subtracted
        the dropped edges from the source count before comparing, which made it
        `True` by construction -- a preservation check that cannot fail is not a
        check. `counts_reconcile` is the weaker accounting identity.
        """
        return (self.source_tickets == self.imported_tickets
                and self.source_dependencies == self.imported_dependencies
                and self.source_notes == self.imported_updates
                and not self.skipped)

    @property
    def counts_reconcile(self):
        """Every source record is accounted for: imported, skipped or dropped."""
        return (
            self.source_tickets == self.imported_tickets + len(self.skipped_tickets)
            and self.source_dependencies
            == self.imported_dependencies + len(self.dropped_dependencies)
        )

    @property
    def skipped_tickets(self):
        """Skips that cost a whole ticket, as opposed to one note or one line."""
        return [s for s in self.skipped if s.get("kind") == "ticket"]

    @property
    def skipped_notes(self):
        return [s for s in self.skipped if s.get("kind") == "note"]

    def as_dict(self):
        return {
            "project_id": self.project_id,
            "source_tickets": self.source_tickets,
            "imported_tickets": self.imported_tickets,
            "source_dependencies": self.source_dependencies,
            "imported_dependencies": self.imported_dependencies,
            "source_notes": self.source_notes,
            "imported_updates": self.imported_updates,
            "imported_update_rows": self.imported_update_rows,
            "imported_agents": self.imported_agents,
            "imported_documents": self.imported_documents,
            "imported_messages": self.imported_messages,
            "imported_reviews": self.imported_reviews,
            "dropped_dependencies": self.dropped_dependencies,
            "skipped": self.skipped,
            "non_ticket_files": self.non_ticket_files,
            "unmapped_states": self.unmapped_states,
            "archived_fields": self.archived_fields,
            "unknown_fields": self.unknown_fields,
            "truncated_fields": self.truncated_fields,
            "contract_mismatches": self.contract_mismatches,
            "counts_preserved": self.counts_preserved,
            "counts_reconcile": self.counts_reconcile,
        }


# -------------------------------------------------------------------- import

def _legacy_ticket_key(raw_id, prefix):
    """Map a legacy id (T-179) onto the contract's TicketId pattern.

    The legacy board uses `T-179`, which already matches
    `^[A-Z][A-Z0-9]{1,15}-[0-9]{1,6}$`... except for the 2-char minimum on the
    alpha part, so a bare `T-` prefix is invalid. Re-prefixing keeps the number
    (the part humans actually cite) intact.
    """
    if raw_id is None:
        return None
    text = str(raw_id).strip()
    if "-" not in text:
        return None
    head, _, tail = text.rpartition("-")
    if not tail.isdigit():
        return None
    number = str(int(tail))
    if ids.TICKET_ID_RE.match(text):
        return text
    return "{}-{}".format(prefix, number)


def _timestamp(value, fallback, *, report=None):
    """A legacy timestamp, or the fallback if it is missing or not RFC 3339."""
    if not isinstance(value, str):
        return fallback
    match = TIMESTAMP_RE.match(value)
    if match is None:
        return fallback
    if match.group(4) in ZERO_OFFSETS:
        return value
    moment = _dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
    if report is not None:
        report._bump(report.contract_mismatches,
                     "timestamp carried a non-UTC offset")
    return moment.astimezone(_dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _truncate(value, limit, report, field):
    """Fit a value into a contract-limited column, recording that we did."""
    text = "" if value is None else str(value)
    if len(text) <= limit:
        return text, False
    report._bump(report.truncated_fields, field)
    return text[:limit], True


def _split_note(text):
    """A note as one or more contract-legal update bodies."""
    if len(text) <= UPDATE_BODY_MAX:
        return [text]
    return [text[i:i + UPDATE_BODY_MAX]
            for i in range(0, len(text), UPDATE_BODY_MAX)]


def _evidence(item, report, sha_resolver):
    """A contract-valid GitEvidence for this ticket, or None.

    Returns None far more often than you would expect, and that is the honest
    answer: see the module docstring on why the real board's `commit` values
    cannot legally become `GitEvidence`.
    """
    commit = item.get("commit")
    branch = item.get("branch")
    if not commit and not branch:
        return None

    sha = None
    short = None
    if isinstance(commit, str):
        candidate = commit.rsplit("@", 1)[-1].strip()
        if SHA_RE.match(candidate):
            sha = candidate
        elif candidate:
            short = candidate
            if sha_resolver is not None:
                resolved = sha_resolver(candidate, branch)
                if isinstance(resolved, str) and SHA_RE.match(resolved):
                    sha = resolved

    if sha is None:
        if short is not None:
            report._bump(report.contract_mismatches,
                         "commit is not a 40-hex Sha")
        return None

    # T-224: GitEvidence.repository is now required (the T-215 fix at the
    # contract layer). The legacy record has no repository concept at all --
    # `commit` is `branch@sha` with no remote or repo root attached, which is
    # the exact ambiguity T-215 exists to close. Inventing a repository value
    # would be the same mistake as inventing a sha: a real sha with a fake
    # repository is not honest evidence. So a genuinely 40-hex commit -- direct
    # or via `sha_resolver` -- still cannot become `GitEvidence` on this
    # importer until a caller can supply real repository identity; reported
    # the same way a bad sha is, so T-211/T-215 see the count.
    report._bump(report.contract_mismatches, "no repository identity")
    return None


def _archive_field(conn, project_id, key, field, value):
    conn.execute(
        "INSERT INTO legacy_ticket_fields (project_id, ticket_id, field, value)"
        " VALUES (?, ?, ?, ?)"
        " ON CONFLICT(project_id, ticket_id, field) DO UPDATE SET value=excluded.value",
        (project_id, key, field, _json(value)),
    )


def import_legacy_board(store, legacy_dir, *, project_name=None, prefix="LEG",
                        take_over=True, owner="ticket-board", sha_resolver=None):
    """Import a legacy board into `store`, preserving counts, edges and fields.

    Returns an `ImportReport`. Originals are left untouched; on success the
    directory is marked server-owned so the old CLI stops writing.

    The entire database side runs inside a single `BEGIN IMMEDIATE`
    transaction. If anything raises -- a duplicate id, an unexpected schema, a
    full disk -- the database is left exactly as it was, so a re-run imports
    once rather than leaving a half-populated second project behind.
    """
    legacy_dir = Path(legacy_dir)
    # Fail before writing anything, not after. A second import of an
    # already-owned directory used to create a whole project and then raise.
    if take_over:
        assert_writable(legacy_dir)

    report = ImportReport()
    raw = read_legacy_board(legacy_dir, report=report)
    # A `T-021.json` that is half-written is a source ticket that failed to
    # read, not a ticket that was never there. Counting only what parsed is how
    # a truncated file disappears from both sides of the comparison at once.
    report.source_tickets = len(raw) + len(report.skipped_tickets)
    documents = read_legacy_documents(legacy_dir, report=report)
    messages = read_legacy_messages(legacy_dir, report=report)
    agent_records = read_legacy_agents(legacy_dir)

    # First pass: ids, so dependency edges can be remapped in the second, and
    # so a collision (two source ids mapping to one key) is caught before it
    # silently overwrites a ticket.
    id_map = {}
    key_owner = {}
    for item in raw:
        raw_id = str(item.get("id"))
        key = _legacy_ticket_key(item.get("id"), prefix)
        if key is None:
            report.skipped.append({"kind": "ticket", "id": item.get("id"),
                                   "reason": "unparsable ticket id"})
            continue
        if key in key_owner:
            report.skipped.append({
                "kind": "ticket", "id": raw_id,
                "reason": "id collides with {} (both map to {})".format(
                    key_owner[key], key),
            })
            continue
        key_owner[key] = raw_id
        id_map[raw_id] = key

    for item in raw:
        if str(item.get("id")) not in id_map:
            continue
        report.source_dependencies += len(
            item.get("deps") or item.get("dependencies") or [])
        report.source_notes += len(item.get("notes") or [])

    now = ids.now()
    project_id = ids.project_id()
    report.project_id = project_id

    with write_txn(store.conn) as conn:
        for statement in ARCHIVE_DDL:
            conn.execute(statement)

        conn.execute(
            "INSERT INTO projects (id, name, version, created_at, source, paused)"
            " VALUES (?, ?, 1, ?, 'imported_legacy', 0)",
            (project_id, project_name or legacy_dir.resolve().name, now),
        )
        store._audit(conn, project_id, SYSTEM_ACTOR, "project.create",
                     subject_type="project", subject_id=project_id,
                     summary="imported legacy board {}".format(legacy_dir.name))

        agent_ids = _import_agents(conn, store, project_id, raw, agent_records,
                                   id_map, report, now)
        _import_documents(conn, project_id, documents, report)
        _import_messages(conn, project_id, messages, id_map, report)

        for item in raw:
            raw_id = str(item.get("id"))
            key = id_map.get(raw_id)
            if key is None:
                continue
            _import_ticket(conn, store, project_id, key, item, agent_ids,
                           report, sha_resolver, now)
            report.imported_tickets += 1

        _import_dependencies(conn, store, project_id, raw, id_map, report)

        store._audit(
            conn, project_id, SYSTEM_ACTOR, "board.import",
            subject_type="project", subject_id=project_id,
            summary="{} tickets, {} edges, {} updates, {} messages".format(
                report.imported_tickets, report.imported_dependencies,
                report.imported_updates, report.imported_messages),
        )

    if take_over:
        take_ownership(legacy_dir, owner=owner,
                       note="Imported into project {}".format(project_id))
    return report


def _import_agents(conn, store, project_id, raw, agent_records, id_map,
                   report, now):
    """Create a real agent row for every name the board mentions as an owner.

    Without this, an imported board shows ~110 tickets in progress with no
    owner, because `tickets.owner` is a foreign key to `agents(id)` and a bare
    legacy name is not one. Imported agents are `offline`: they have never
    connected to *this* server, and claiming otherwise would put a fake green
    dot on the dashboard.
    """
    names = set(agent_records)
    for item in raw:
        if item.get("owner"):
            names.add(str(item["owner"]))

    agent_ids = {}
    for name in sorted(names):
        record = agent_records.get(name) or {}
        aid = ids.agent_id()
        agent_ids[name] = aid
        seen = record.get("seen") if isinstance(record.get("seen"), str) else None
        conn.execute(
            "INSERT INTO agents (id, project_id, name, role, capabilities, state,"
            " connection_mode, runtime, max_active_tickets, current_ticket,"
            " worktree, hook_health, last_heartbeat_at, version, created_at)"
            " VALUES (?, ?, ?, NULL, '[]', 'offline', 'hook_only', ?, 1, ?, ?, ?,"
            " ?, 1, ?)",
            (aid, project_id, name,
             # `Agent.runtime` is a closed schema of adapter/version/
             # unsupported_capabilities. The legacy record's branch, sha and
             # dirty flag do not belong in it and would make every imported
             # agent fail the published schema; the whole
             # `agents/<name>.json` is archived as a document instead.
             "{}",
             # Through `id_map`: a bare legacy id is not a contract `TicketId`,
             # which is the same re-prefixing the ticket rows get.
             id_map.get(str(record.get("ticket"))) if record.get("ticket")
             else None,
             record.get("worktree") or None,
             _json({"config_installed": False, "server_received": False,
                    "response_delivered": False, "session_adopted": False}),
             _timestamp(seen, None, report=report) if seen else None,
             now),
        )
        report.imported_agents += 1
    store._audit(conn, project_id, SYSTEM_ACTOR, "agent.import",
                 subject_type="project", subject_id=project_id,
                 summary="imported {} agents as offline".format(len(agent_ids)))
    return agent_ids


def _import_documents(conn, project_id, documents, report):
    for kind, name, content in documents:
        conn.execute(
            "INSERT INTO legacy_documents (project_id, kind, name, content)"
            " VALUES (?, ?, ?, ?)"
            " ON CONFLICT(project_id, kind, name) DO UPDATE SET content=excluded.content",
            (project_id, kind, name, content),
        )
        report.imported_documents += 1


def _import_messages(conn, project_id, messages, id_map, report):
    for seq, data, line in messages:
        re_ticket = data.get("re") or None
        conn.execute(
            "INSERT INTO legacy_messages (project_id, seq, sent_at, sender,"
            " recipient, re_ticket, re_ticket_id, body, raw)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (project_id, seq, data.get("at"), data.get("from"),
             data.get("to") or None, re_ticket,
             id_map.get(str(re_ticket)) if re_ticket else None,
             data.get("text"), line),
        )
        report.imported_messages += 1


def _import_ticket(conn, store, project_id, key, item, agent_ids, report,
                   sha_resolver, now):
    legacy_state = str(item.get("status", "open")).strip().lower()
    state = STATUS_MAP.get(legacy_state)
    if state is None:
        state = "open"
        report._bump(report.unmapped_states, legacy_state)

    title, title_cut = _truncate(item.get("title") or key, TITLE_MAX,
                                report, "title")
    body = item.get("body")
    outcome = None
    body_cut = False
    if body:
        outcome, body_cut = _truncate(body, OUTCOME_MAX, report, "body")

    created = _timestamp(item.get("created"), now, report=report)
    updated = _timestamp(item.get("updated"), created, report=report)
    claimed_at = _timestamp(item.get("claimed_at"), None, report=report) \
        if item.get("claimed_at") else None
    owner_name = item.get("owner") or None
    owner_id = agent_ids.get(str(owner_name)) if owner_name else None
    evidence = _evidence(item, report, sha_resolver)

    conn.execute(
        "INSERT INTO tickets (id, project_id, title, outcome, acceptance, state,"
        " version, role, owner, files, evidence, handoff, created_at, updated_at,"
        " claimed_at) VALUES (?, ?, ?, ?, '[]', ?, 1, ?, ?, '[]', ?, NULL, ?, ?, ?)",
        (key, project_id, title, outcome, state, item.get("role") or None,
         owner_id, _json(evidence) if evidence else None,
         created, updated, claimed_at),
    )

    _import_review(conn, store, project_id, key, item, evidence, owner_name,
                   state, report)

    # Archive: every key with no contract home, plus the originals of anything
    # a contract limit forced us to truncate, plus any key this importer has
    # never seen. `id` and `owner` are archived too -- the legacy id is what a
    # human cites and the agent name is what the old board recorded.
    _archive_field(conn, project_id, key, "legacy_id", str(item.get("id")))
    if owner_name:
        _archive_field(conn, project_id, key, "legacy_owner", owner_name)
    if legacy_state:
        _archive_field(conn, project_id, key, "legacy_status", legacy_state)
    if title_cut:
        _archive_field(conn, project_id, key, "title_full", item.get("title"))
    if body_cut:
        _archive_field(conn, project_id, key, "body_full", body)

    for field, value in sorted(item.items()):
        if field in MAPPED_TICKET_FIELDS:
            continue
        if field not in KNOWN_TICKET_FIELDS:
            report._bump(report.unknown_fields, field)
        if value in (None, "", [], {}):
            continue
        _archive_field(conn, project_id, key, field, value)
        report._bump(report.archived_fields, field)

    _import_notes(conn, project_id, key, item, agent_ids, report)

    store._audit(conn, project_id, SYSTEM_ACTOR, "ticket.import",
                 subject_type="ticket", subject_id=key,
                 summary="imported as {} from {}".format(state, item.get("id")))


def _import_review(conn, store, project_id, key, item, evidence, owner_name,
                   state, report):
    """A `reviews` row -- `evidence` may be `None` (T-224 planner ruling,
    2026-09-06).

    A review that genuinely happened on the legacy board is real history even
    when it cannot be given contract-valid `GitEvidence`: `Review.evidence` is
    nullable precisely so this importer is not forced to choose between
    fabricating evidence and dropping the review entirely. `evidence` is
    `None` whenever `_evidence()` could not produce a real, repository-
    qualified sha (bad sha, or a real sha with no repository identity --
    either way `report.contract_mismatches` already counted it). `review_at`
    is archived either way, regardless of whether a row is written here.
    """
    submitted_at = item.get("review_at") or item.get("updated")
    if not isinstance(submitted_at, str) or not TIMESTAMP_RE.match(submitted_at):
        return
    rid = ids.review_id()
    conn.execute(
        "INSERT INTO reviews (id, project_id, ticket_id, state, submitted_by,"
        " submitted_at, evidence, notes) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (rid, project_id, key, "accepted" if state == "done" else "requested",
         _json({"type": "agent", "id": owner_name or "legacy",
                "display_name": owner_name or "legacy"}),
         submitted_at, _json(evidence) if evidence else None,
         "Imported from the legacy board."),
    )
    report.imported_reviews += 1


def _import_notes(conn, project_id, key, item, agent_ids, report):
    """Notes become `ticket_updates`, each keeping the note's own `at`.

    The previous importer passed every note through `add_update`, which stamps
    `ids.now()`; 1020 notes written over two days all collapsed onto the import
    minute, which destroys the one thing a progress trail is for.
    """
    for ordinal, note in enumerate(item.get("notes") or []):
        if not isinstance(note, dict):
            report.skipped.append({"kind": "note",
                                   "id": "{}#note{}".format(key, ordinal),
                                   "reason": "note is not an object"})
            continue
        by = note.get("by") or "legacy"
        author = {
            "type": "agent" if by in agent_ids else "system",
            "id": agent_ids.get(by, "system"),
            "display_name": str(by)[:120],
        }
        at = _timestamp(note.get("at"), None, report=report)
        if at is None:
            report._bump(report.contract_mismatches, "note has no usable 'at'")
            at = _timestamp(item.get("updated"), ids.now(), report=report)
        text = note.get("text") or ""
        parts = _split_note(text)
        update_ids = []
        for part in parts:
            uid = ids.update_id()
            update_ids.append(uid)
            conn.execute(
                "INSERT INTO ticket_updates (id, project_id, ticket_id, author,"
                " body, next_step, created_at, superseded)"
                " VALUES (?, ?, ?, ?, ?, NULL, ?, 0)",
                (uid, project_id, key, _json(author), part, at),
            )
        report.imported_update_rows += len(parts)
        if len(parts) > 1:
            report._bump(report.contract_mismatches,
                         "note longer than the 4000-char update body")
        extra = {k: v for k, v in note.items()
                 if k not in ("by", "at", "text")}
        conn.execute(
            "INSERT INTO legacy_note_map (project_id, ticket_id, ordinal,"
            " update_ids, extra) VALUES (?, ?, ?, ?, ?)",
            (project_id, key, ordinal, _json(update_ids), _json(extra)),
        )
        for field in extra:
            report._bump(report.archived_fields, "notes[].{}".format(field))
        report.imported_updates += 1


def _import_dependencies(conn, store, project_id, raw, id_map, report):
    """Wire edges after every ticket exists, so forward references work.

    A cycle drops the offending ticket's edges and is reported; it does not
    abort the import. The legacy board is data we do not control, and refusing
    130 good tickets because one pair points at each other is the wrong trade.
    """
    for item in raw:
        raw_id = str(item.get("id"))
        key = id_map.get(raw_id)
        if key is None:
            continue
        deps_raw = item.get("deps") or item.get("dependencies") or []
        mapped = []
        for dep in deps_raw:
            target = id_map.get(str(dep))
            if target is None:
                report.dropped_dependencies.append(
                    {"ticket": raw_id, "depends_on": str(dep),
                     "reason": "dependency not present in source board"}
                )
                continue
            mapped.append(target)
        if not mapped:
            continue
        deps = sorted(set(mapped))
        try:
            store._set_dependencies(conn, project_id, key, deps)
        except DependencyCycle as exc:
            for dep in mapped:
                report.dropped_dependencies.append(
                    {"ticket": raw_id, "depends_on": dep,
                     "reason": "would create a dependency cycle: {}".format(
                         exc.details.get("cycle"))}
                )
            continue
        report.imported_dependencies += len(deps)
        # A source board that lists the same edge twice has two source edges and
        # one imported edge. The surplus is recorded as a drop rather than
        # quietly deducted from the source count: an edge that vanishes between
        # the two totals with nothing to point at is exactly the kind of
        # accounting `counts_preserved` used to hide.
        for extra in range(len(mapped) - len(deps)):
            report.dropped_dependencies.append(
                {"ticket": raw_id, "depends_on": None,
                 "reason": "duplicate edge already present in this ticket's list"}
            )


# -------------------------------------------------------------- read it back

def _archive_query(conn, sql, params):
    try:
        return conn.execute(sql, params).fetchall()
    except sqlite3.OperationalError:
        # No import has ever run against this database.
        return []


def legacy_fields(store, project_id, ticket_id):
    """The archived legacy fields of one imported ticket, decoded."""
    rows = _archive_query(
        store.conn,
        "SELECT field, value FROM legacy_ticket_fields"
        " WHERE project_id = ? AND ticket_id = ? ORDER BY field",
        (project_id, ticket_id),
    )
    return {r["field"]: json.loads(r["value"]) for r in rows}


def legacy_documents(store, project_id, *, kind=None):
    if kind is None:
        rows = _archive_query(
            store.conn,
            "SELECT kind, name, content FROM legacy_documents"
            " WHERE project_id = ? ORDER BY kind, name", (project_id,))
    else:
        rows = _archive_query(
            store.conn,
            "SELECT kind, name, content FROM legacy_documents"
            " WHERE project_id = ? AND kind = ? ORDER BY name",
            (project_id, kind))
    return [{"kind": r["kind"], "name": r["name"], "content": r["content"]}
            for r in rows]


def legacy_messages(store, project_id, *, ticket_id=None):
    """Archived board messages. `ticket_id` is the imported key, e.g. LEG-213."""
    if ticket_id is None:
        rows = _archive_query(
            store.conn,
            "SELECT * FROM legacy_messages WHERE project_id = ? ORDER BY seq",
            (project_id,))
    else:
        rows = _archive_query(
            store.conn,
            "SELECT * FROM legacy_messages WHERE project_id = ?"
            " AND re_ticket_id = ? ORDER BY seq", (project_id, ticket_id))
    return [{"seq": r["seq"], "at": r["sent_at"], "from": r["sender"],
             "to": r["recipient"], "re": r["re_ticket"],
             "ticket_id": r["re_ticket_id"], "text": r["body"]}
            for r in rows]


def legacy_notes(store, project_id, ticket_id):
    """The source notes of one ticket, in source order, rejoined if split."""
    rows = _archive_query(
        store.conn,
        "SELECT ordinal, update_ids, extra FROM legacy_note_map"
        " WHERE project_id = ? AND ticket_id = ? ORDER BY ordinal",
        (project_id, ticket_id),
    )
    if not rows:
        return []
    updates = {
        r["id"]: r for r in store.conn.execute(
            "SELECT id, author, body, created_at FROM ticket_updates"
            " WHERE project_id = ? AND ticket_id = ?", (project_id, ticket_id))
    }
    notes = []
    for row in rows:
        parts = json.loads(row["update_ids"])
        present = [updates[u] for u in parts if u in updates]
        if not present:
            continue
        author = json.loads(present[0]["author"])
        note = {"by": author["display_name"],
                "at": present[0]["created_at"],
                "text": "".join(p["body"] for p in present)}
        note.update(json.loads(row["extra"]))
        notes.append(note)
    return notes


def reconstruct_legacy_ticket(store, project_id, ticket_id):
    """Rebuild the source ticket dict from what the database now holds.

    This is the import's own proof of fidelity: `verify_round_trip` diffs the
    result against the file it came from, field by field, and a reviewer can
    run it on their own board without trusting a report.
    """
    ticket = store.get_ticket(project_id, ticket_id)
    archived = legacy_fields(store, project_id, ticket_id)
    out = {}
    out["id"] = archived.get("legacy_id", ticket_id)
    out["title"] = archived.get("title_full", ticket["title"])
    body = archived.get("body_full", ticket.get("outcome"))
    out["body"] = body if body is not None else ""
    out["role"] = ticket.get("role") or ""
    out["status"] = archived.get("legacy_status", ticket["state"])
    out["deps"] = [archived_dep for archived_dep in ticket["dependencies"]]
    out["owner"] = archived.get("legacy_owner", "")
    out["created"] = ticket["created_at"]
    out["updated"] = ticket["updated_at"]
    if ticket.get("claimed_at"):
        out["claimed_at"] = ticket["claimed_at"]
    for field, value in archived.items():
        if field in ("legacy_id", "legacy_owner", "legacy_status",
                     "title_full", "body_full"):
            continue
        out[field] = value
    out["notes"] = legacy_notes(store, project_id, ticket_id)
    return out


def verify_round_trip(store, report, legacy_dir, *, prefix="LEG"):
    """Diff every source ticket against what the import stored.

    Returns a list of `{ticket, field, source, imported}` differences; an empty
    list means every field of every ticket survived. Dependency ids are
    compared after mapping, since re-prefixing is intentional.
    """
    differences = []
    source = read_legacy_board(legacy_dir)
    id_map = {}
    for item in source:
        key = _legacy_ticket_key(item.get("id"), prefix)
        if key is not None:
            id_map[str(item.get("id"))] = key
    skipped = {str(s.get("id")) for s in report.skipped_tickets}
    # Edges the report explicitly accounts for are not silent loss. Subtracting
    # them here is what makes an empty result mean "nothing went missing that
    # the report did not name".
    explained = set()
    for drop in report.dropped_dependencies:
        explained.add((str(drop["ticket"]), drop["depends_on"]))

    for item in source:
        raw_id = str(item.get("id"))
        if raw_id in skipped:
            continue
        key = id_map.get(raw_id)
        if key is None:
            continue
        rebuilt = reconstruct_legacy_ticket(store, report.project_id, key)
        for field, value in item.items():
            if field in ("deps", "dependencies"):
                expected = sorted({
                    id_map[str(d)] for d in (value or [])
                    if str(d) in id_map
                    and (raw_id, id_map[str(d)]) not in explained
                })
                actual = sorted(rebuilt.get("deps") or [])
                if expected != actual:
                    differences.append({"ticket": raw_id, "field": "deps",
                                        "source": expected, "imported": actual})
                continue
            if field == "notes":
                expected_notes = [n for n in (value or []) if isinstance(n, dict)]
                actual_notes = rebuilt.get("notes") or []
                if len(expected_notes) != len(actual_notes):
                    differences.append({
                        "ticket": raw_id, "field": "notes",
                        "source": len(expected_notes),
                        "imported": len(actual_notes)})
                    continue
                for index, (want, got) in enumerate(
                        zip(expected_notes, actual_notes)):
                    normalised = dict(want)
                    normalised.setdefault("by", "legacy")
                    normalised["text"] = want.get("text") or ""
                    if normalised != got:
                        differences.append({
                            "ticket": raw_id,
                            "field": "notes[{}]".format(index),
                            "source": normalised, "imported": got})
                continue
            if value in (None, "", [], {}):
                continue  # an empty legacy field carries no information
            if rebuilt.get(field) != value:
                differences.append({"ticket": raw_id, "field": field,
                                    "source": value,
                                    "imported": rebuilt.get(field)})
    return differences
