"""Load fixtures from the frozen `tests/fixtures/` pack, honestly.

A few response schemas (`Ticket`, `Agent`, `Review`, `Channel`, `Member`) have
no dedicated fixture in the manifest -- the frozen pack only publishes them
nested inside a screen response (e.g. `TicketDetailResponse.ticket`). Rather
than inventing new example JSON that could drift from the real shape,
`FixtureRef.extract` pulls the nested value out of a fixture that the manifest
already lists, so every byte the harness serves or checks traces back to a
frozen, manifest-listed file.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
FIXTURES = REPO / "tests" / "fixtures"
MANIFEST = json.loads((FIXTURES / "manifest.json").read_text())


@dataclass(frozen=True)
class FixtureRef:
    rel_path: str  # relative to tests/fixtures/, must be a manifest key
    extract: tuple[object, ...] = ()  # keys/indices applied in order

    def __post_init__(self) -> None:
        if self.rel_path not in MANIFEST:
            raise KeyError(f"{self.rel_path} is not in tests/fixtures/manifest.json")

    @property
    def manifest_schema(self) -> str:
        return MANIFEST[self.rel_path]

    def load(self) -> dict:
        value = json.loads((FIXTURES / self.rel_path).read_text())
        for step in self.extract:
            value = value[step]
        return value
