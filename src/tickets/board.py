"""Load/save the file-backed ticket board."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Iterable

from tickets.models import PRIORITIES, STATUSES, Ticket, utcnow
from tickets.paths import (
    BOARD_META_NAME,
    INBOX_NAME,
    iter_ticket_paths,
    normalize_ticket_id,
    ticket_number,
    ticket_path,
)


class BoardError(Exception):
    """User-facing board operation error."""


def atomic_write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    tmp.replace(path)


def load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


class Board:
    def __init__(self, root: Path) -> None:
        self.root = root

    def ensure(self) -> None:
        self.root.mkdir(parents=True, exist_ok=True)

    def ticket_files(self) -> list[Path]:
        return iter_ticket_paths(self.root)

    def load(self, ticket_id: str) -> Ticket:
        tid = normalize_ticket_id(ticket_id)
        path = ticket_path(self.root, tid)
        if not path.is_file():
            raise BoardError(f"{tid} not found in {self.root}")
        try:
            data = load_json(path)
        except json.JSONDecodeError as exc:
            raise BoardError(f"{tid} is not valid JSON: {exc}") from exc
        ticket = Ticket.from_dict(data)
        ticket.id = tid
        return ticket

    def save(self, ticket: Ticket) -> Path:
        self.ensure()
        ticket.updated_at = ticket.updated_at or utcnow()
        path = ticket_path(self.root, ticket.id)
        atomic_write(path, json.dumps(ticket.to_dict(), indent=2, sort_keys=False) + "\n")
        return path

    def all_tickets(self) -> list[Ticket]:
        tickets: list[Ticket] = []
        for path in self.ticket_files():
            try:
                data = load_json(path)
            except (OSError, json.JSONDecodeError):
                continue
            ticket = Ticket.from_dict(data)
            if not ticket.id:
                ticket.id = path.stem.upper().replace("T-", "T-")
                # Normalize stem T-001 -> T-1 via normalize when possible
                try:
                    ticket.id = normalize_ticket_id(path.stem)
                except ValueError:
                    ticket.id = path.stem
            tickets.append(ticket)
        tickets.sort(key=lambda t: ticket_number(t.id) if _can_num(t.id) else 0)
        return tickets

    def next_id(self) -> str:
        numbers = []
        for path in self.ticket_files():
            try:
                numbers.append(ticket_number(path.stem))
            except ValueError:
                continue
        meta = self.meta()
        meta_next = int(meta.get("next_id") or 0)
        nxt = max([0, meta_next - 1, *numbers]) + 1
        return f"T-{nxt}"

    def meta_path(self) -> Path:
        return self.root / BOARD_META_NAME

    def meta(self) -> dict[str, Any]:
        path = self.meta_path()
        if not path.is_file():
            return {"epics": [], "sprints": []}
        try:
            data = load_json(path)
        except (OSError, json.JSONDecodeError):
            return {"epics": [], "sprints": []}
        if not isinstance(data, dict):
            return {"epics": [], "sprints": []}
        data.setdefault("epics", [])
        data.setdefault("sprints", [])
        return data

    def save_meta(self, meta: dict[str, Any]) -> None:
        self.ensure()
        atomic_write(self.meta_path(), json.dumps(meta, indent=2) + "\n")

    def create(
        self,
        title: str,
        *,
        body: str = "",
        agent: str,
        priority: str = "p2",
        epic: str | None = None,
        sprint: str | None = None,
        deps: Iterable[str] | None = None,
        status: str = "open",
    ) -> Ticket:
        priority = priority.lower()
        if priority not in PRIORITIES:
            raise BoardError(f"priority must be one of {', '.join(PRIORITIES)}")
        if status not in STATUSES:
            raise BoardError(f"status must be one of {', '.join(STATUSES)}")
        tid = self.next_id()
        now = utcnow()
        ticket = Ticket(
            id=tid,
            title=title.strip(),
            body=body,
            status=status,
            priority=priority,
            epic=epic or None,
            sprint=sprint or None,
            deps=[normalize_ticket_id(d) for d in (deps or [])],
            created_at=now,
            updated_at=now,
            created_by=agent,
        )
        ticket.add_note(agent, f"created by {agent}", kind="create")
        self.save(ticket)
        meta = self.meta()
        meta["next_id"] = ticket_number(tid) + 1
        if epic and epic not in meta["epics"]:
            meta["epics"].append(epic)
        if sprint and sprint not in meta["sprints"]:
            meta["sprints"].append(sprint)
        self.save_meta(meta)
        return ticket

    def held_by(self, agent: str) -> list[Ticket]:
        return [t for t in self.all_tickets() if t.is_held_by(agent)]

    def deps_satisfied(self, ticket: Ticket, catalog: list[Ticket] | None = None) -> bool:
        catalog = catalog if catalog is not None else self.all_tickets()
        by_id = {t.id: t for t in catalog}
        for dep_id in ticket.deps:
            try:
                dep_id = normalize_ticket_id(dep_id)
            except ValueError:
                return False
            dep = by_id.get(dep_id)
            if dep is None or dep.status != "done":
                return False
        return True

    def next_open(self, *, agent: str | None = None) -> Ticket | None:
        catalog = self.all_tickets()
        candidates = [
            t
            for t in catalog
            if t.status == "open" and not t.holder() and self.deps_satisfied(t, catalog)
        ]
        if agent:
            mine = [t for t in candidates if agent in t.collaborators]
            if mine:
                candidates = mine
        candidates.sort(key=lambda t: (PRIORITIES.index(t.priority) if t.priority in PRIORITIES else 9, ticket_number(t.id)))
        return candidates[0] if candidates else None

    def claim(self, ticket: Ticket, agent: str) -> Ticket:
        holder = ticket.holder()
        if holder and holder != agent and ticket.status in {"claimed", "in_review"}:
            raise BoardError(f"{ticket.id} is already claimed by {holder}")
        ticket.assignee = agent
        ticket.claimed_by = agent
        ticket.status = "claimed"
        ticket.updated_at = utcnow()
        ticket.add_note(agent, f"claimed by {agent}", kind="claim")
        self.save(ticket)
        return ticket

    def mark_done(self, ticket: Ticket, agent: str, note: str | None = None) -> Ticket:
        ticket.status = "done"
        ticket.updated_at = utcnow()
        ticket.add_note(agent, note or f"done by {agent}", kind="done")
        self.save(ticket)
        return ticket

    def append_inbox(self, sender: str, target: str, text: str) -> None:
        self.ensure()
        line = json.dumps({"at": utcnow(), "from": sender, "to": target, "text": text})
        path = self.root / INBOX_NAME
        with path.open("a", encoding="utf-8") as fh:
            fh.write(line + "\n")


def _can_num(ticket_id: str) -> bool:
    try:
        ticket_number(ticket_id)
        return True
    except ValueError:
        return False
