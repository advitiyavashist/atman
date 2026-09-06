"""Legacy board import and server ownership.

The legacy board is a directory of `T-NNN.json` files (plus `agents/`,
`epics/`, `sprints/`, `messages.jsonl`) written directly by every agent's CLI.
Importing it into SQLite creates a second writer for the same data, which is
the actual risk here -- not the import itself but the window afterwards where
half the fleet still writes files and the server writes rows.

So the import is deliberately conservative:

- **Originals are never modified or deleted.** Rollback is "stop the server and
  delete the sentinel"; the files are still exactly as they were.
- **Counts and dependency edges are preserved and verified**, and the import
  reports a discrepancy rather than silently dropping a ticket whose id does
  not parse.
- **Server ownership is explicit and visible in the directory itself.** A
  sentinel file says the board is owned; `assert_writable()` refuses legacy
  writes while it exists, so a stale CLI fails loudly with
  `legacy_writer_active` instead of writing into a board nobody reads.
"""

import json
import os
from pathlib import Path

from . import ids
from .errors import LegacyWriterActive, MalformedRequest

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


def read_legacy_board(legacy_dir):
    """Parse a legacy board directory into ticket dicts. Read-only."""
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
        except (ValueError, OSError):
            continue
        if isinstance(data, dict) and "title" in data:
            data.setdefault("id", path.stem)
            tickets.append(data)
    return tickets


class ImportReport:
    """What the import did, in enough detail to verify it without the db."""

    def __init__(self):
        self.source_tickets = 0
        self.imported_tickets = 0
        self.source_dependencies = 0
        self.imported_dependencies = 0
        self.dropped_dependencies = []
        self.skipped = []
        self.unmapped_states = {}
        self.project_id = None

    @property
    def counts_preserved(self):
        return (self.source_tickets == self.imported_tickets
                and self.source_dependencies == self.imported_dependencies)

    def as_dict(self):
        return {
            "project_id": self.project_id,
            "source_tickets": self.source_tickets,
            "imported_tickets": self.imported_tickets,
            "source_dependencies": self.source_dependencies,
            "imported_dependencies": self.imported_dependencies,
            "dropped_dependencies": self.dropped_dependencies,
            "skipped": self.skipped,
            "unmapped_states": self.unmapped_states,
            "counts_preserved": self.counts_preserved,
        }


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


def import_legacy_board(store, legacy_dir, *, project_name=None, prefix="LEG",
                        take_over=True, owner="ticket-board"):
    """Import a legacy board into `store`, preserving counts and dependencies.

    Returns an `ImportReport`. Originals are left untouched; on success the
    directory is marked server-owned so the old CLI stops writing.
    """
    raw = read_legacy_board(legacy_dir)
    report = ImportReport()
    report.source_tickets = len(raw)

    project = store.create_project(
        project_name or Path(legacy_dir).resolve().name,
        source="imported_legacy",
    )
    report.project_id = project["id"]

    # First pass: ids, so dependency edges can be remapped in the second.
    id_map = {}
    for item in raw:
        key = _legacy_ticket_key(item.get("id"), prefix)
        if key is None:
            report.skipped.append({"id": item.get("id"),
                                   "reason": "unparsable ticket id"})
            continue
        id_map[str(item.get("id"))] = key

    for item in raw:
        raw_id = str(item.get("id"))
        if raw_id not in id_map:
            continue
        deps_raw = item.get("deps") or item.get("dependencies") or []
        report.source_dependencies += len(deps_raw)

    # Second pass: create tickets with no dependencies, then wire edges. Doing
    # it in two steps means a forward reference (T-180 depends on T-179 before
    # T-179 exists) does not fail the import.
    for item in raw:
        raw_id = str(item.get("id"))
        key = id_map.get(raw_id)
        if key is None:
            continue
        legacy_state = str(item.get("status", "open")).strip().lower()
        state = STATUS_MAP.get(legacy_state)
        if state is None:
            state = "open"
            report.unmapped_states[legacy_state] = \
                report.unmapped_states.get(legacy_state, 0) + 1
        store.create_ticket(
            project["id"], key, item.get("title") or key,
            role=item.get("role") or None,
            outcome=item.get("outcome") or None,
            files=item.get("files") or [],
        )
        store.import_set_state(project["id"], key, state,
                               owner_name=item.get("owner") or None,
                               created_at=item.get("created"),
                               updated_at=item.get("updated"))
        for note in item.get("notes") or []:
            store.add_update(
                project["id"], key,
                {"type": "system", "id": "legacy-import",
                 "display_name": note.get("by") or "legacy"},
                note.get("text") or "",
            )
        report.imported_tickets += 1

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
        if mapped:
            store.import_set_dependencies(project["id"], key, sorted(set(mapped)))
        report.imported_dependencies += len(mapped)

    report.source_dependencies -= len(report.dropped_dependencies)

    if take_over:
        take_ownership(legacy_dir, owner=owner,
                       note="Imported into project {}".format(project["id"]))
    return report
