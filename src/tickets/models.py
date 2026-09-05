"""Ticket document model. Unknown JSON keys are preserved on save."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

STATUSES = ("open", "claimed", "in_review", "blocked", "done")
PRIORITIES = ("p0", "p1", "p2", "p3")
ACTIVE_STATUSES = frozenset({"claimed", "in_review"})


def utcnow() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _as_list(value: Any) -> list:
    if value is None:
        return []
    if isinstance(value, list):
        return list(value)
    return [value]


@dataclass
class Ticket:
    id: str
    title: str = ""
    body: str = ""
    status: str = "open"
    assignee: str | None = None
    claimed_by: str | None = None
    priority: str = "p2"
    epic: str | None = None
    sprint: str | None = None
    deps: list[str] = field(default_factory=list)
    collaborators: list[str] = field(default_factory=list)
    notes: list[dict[str, Any]] = field(default_factory=list)
    created_at: str = ""
    updated_at: str = ""
    created_by: str | None = None
    extra: dict[str, Any] = field(default_factory=dict)

    KNOWN = frozenset(
        {
            "id",
            "title",
            "body",
            "status",
            "assignee",
            "claimed_by",
            "priority",
            "epic",
            "sprint",
            "deps",
            "depends_on",
            "collaborators",
            "notes",
            "created_at",
            "updated_at",
            "created_by",
        }
    )

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Ticket:
        extra = {k: v for k, v in data.items() if k not in cls.KNOWN}
        deps = _as_list(data.get("deps") or data.get("depends_on"))
        return cls(
            id=str(data.get("id") or ""),
            title=str(data.get("title") or ""),
            body=str(data.get("body") or ""),
            status=str(data.get("status") or "open"),
            assignee=_opt_str(data.get("assignee") or data.get("claimed_by")),
            claimed_by=_opt_str(data.get("claimed_by") or data.get("assignee")),
            priority=str(data.get("priority") or "p2").lower(),
            epic=_opt_str(data.get("epic")),
            sprint=_opt_str(data.get("sprint")),
            deps=[str(d) for d in deps],
            collaborators=[str(c) for c in _as_list(data.get("collaborators"))],
            notes=list(data.get("notes") or []),
            created_at=str(data.get("created_at") or ""),
            updated_at=str(data.get("updated_at") or ""),
            created_by=_opt_str(data.get("created_by")),
            extra=extra,
        )

    def to_dict(self) -> dict[str, Any]:
        payload = dict(self.extra)
        payload.update(
            {
                "id": self.id,
                "title": self.title,
                "body": self.body,
                "status": self.status,
                "assignee": self.assignee,
                "claimed_by": self.claimed_by,
                "priority": self.priority,
                "epic": self.epic,
                "sprint": self.sprint,
                "deps": list(self.deps),
                "collaborators": list(self.collaborators),
                "notes": list(self.notes),
                "created_at": self.created_at,
                "updated_at": self.updated_at,
                "created_by": self.created_by,
            }
        )
        return payload

    def add_note(self, author: str, text: str, *, kind: str = "note") -> dict[str, Any]:
        entry = {"at": utcnow(), "by": author, "kind": kind, "text": text}
        self.notes.append(entry)
        self.updated_at = entry["at"]
        return entry

    def holder(self) -> str | None:
        return self.claimed_by or self.assignee

    def is_held_by(self, agent: str) -> bool:
        return self.holder() == agent and self.status in ACTIVE_STATUSES | {"blocked"}

    def unclaim(self, agent: str, *, note: str | None = None) -> None:
        self.assignee = None
        self.claimed_by = None
        if self.status in ACTIVE_STATUSES:
            self.status = "open"
        self.updated_at = utcnow()
        if note:
            self.add_note(agent, note, kind="unclaim")
        else:
            self.add_note(agent, f"{agent} released claim", kind="unclaim")


def _opt_str(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None
