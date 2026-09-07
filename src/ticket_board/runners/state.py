"""The supervisor's durable state, on disk and independent of a model turn.

The whole point of the managed runner is that it outlives the thing it is
supervising. A supervisor that keeps its identity in memory forgets, on every
crash, both who it is and what it had already started -- and the second of
those is what turns at-least-once delivery into at-least-twice execution.

So three facts live in a file next to the enrollment, not in a variable:

- **runner_id**, minted once per (project, agent, worktree) and reused
  forever after. Regenerating it on restart would take a *new* lease at a new
  epoch and fence out a supervisor that may still be alive.
- **the run in flight**, written *before* the spawn. The contract retains
  `Run.session_id` before launching for exactly this reason; the local half of
  that promise is this record, and it is what `reconcile()` reads.
- **the dedupe ledger**, keyed on the contract's own
  `(message_id, recipient_agent_id)`, so a redelivered wake job is recognised
  as one already executed rather than executed again.

Everything is written whole-file with an atomic rename. A supervisor killed
mid-write must find either the old state or the new one, never half of each --
a truncated ledger reads as "never seen", which is the one failure that would
duplicate work.
"""

from __future__ import annotations

import json
import os
import secrets
import tempfile
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

ALPHABET = "0123456789abcdefghijklmnopqrstuvwxyz"

# How many completed dedupe keys to keep. Bounded so the file cannot grow
# without limit on a long-lived runner; ordered oldest-first so the entries
# that fall off are the ones least likely to be redelivered.
LEDGER_CAP = 500


def new_runner_id() -> str:
    return "rnr_" + "".join(secrets.choice(ALPHABET) for _ in range(8))


def new_session_id() -> str:
    """A child session id, minted locally and passed to `claude --session-id`.

    Minted here rather than read back from Claude because it has to exist
    *before* the process does: it is retained on the run first, so a crash
    between the retain and the spawn leaves something to reconcile against.
    """
    return "ses_" + "".join(secrets.choice(ALPHABET) for _ in range(8))


@dataclass
class InFlight:
    """A run this supervisor has committed to, written before the spawn."""

    run_id: str
    wake_job_id: str
    dedupe_key: str
    session_id: str
    ticket_id: Optional[str] = None
    pid: Optional[int] = None
    spawned: bool = False


@dataclass
class RunnerState:
    runner_id: str
    project_id: str
    agent_id: str
    worktree: str
    epoch: int = 0
    in_flight: Optional[InFlight] = None
    completed: List[str] = field(default_factory=list)
    failed: List[Dict[str, Any]] = field(default_factory=list)

    def seen(self, dedupe_key: str) -> bool:
        if dedupe_key in self.completed:
            return True
        return any(entry.get("dedupe_key") == dedupe_key for entry in self.failed)

    def remember_completed(self, dedupe_key: str) -> None:
        if dedupe_key in self.completed:
            return
        self.completed.append(dedupe_key)
        del self.completed[:-LEDGER_CAP]

    def remember_failed(self, dedupe_key: str, reason: str,
                        run_id: Optional[str] = None) -> None:
        """The inspectable failed queue: kept, with why, and never retried.

        A final dispatch failure that is merely dropped looks identical to a
        message that was never sent, which is the shape of bug that gets
        diagnosed as "the board lost it".
        """
        self.failed.append({"dedupe_key": dedupe_key, "reason": reason,
                            "run_id": run_id})
        del self.failed[:-LEDGER_CAP]


def state_path(state_dir: Path) -> Path:
    return Path(state_dir) / "runner.json"


def load_state(state_dir: Path) -> Optional[RunnerState]:
    path = state_path(state_dir)
    if not path.exists():
        return None
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        # A corrupt state file is not a reason to invent a fresh identity: a
        # new runner_id would fence out a supervisor that may still be running
        # this agent. Refusing is the safe answer, and the operator can delete
        # the file deliberately if that is really what they want.
        raise RunnerStateCorrupt(str(path))
    in_flight = raw.pop("in_flight", None)
    state = RunnerState(**raw)
    if in_flight:
        state.in_flight = InFlight(**in_flight)
    return state


def save_state(state_dir: Path, state: RunnerState) -> Path:
    directory = Path(state_dir)
    directory.mkdir(parents=True, exist_ok=True)
    path = state_path(directory)
    payload = asdict(state)
    handle = tempfile.NamedTemporaryFile(
        "w", dir=str(directory), prefix=".runner-", suffix=".json",
        delete=False, encoding="utf-8")
    try:
        json.dump(payload, handle, indent=2, sort_keys=True)
        handle.flush()
        os.fsync(handle.fileno())
    finally:
        handle.close()
    os.replace(handle.name, path)
    os.chmod(path, 0o600)
    return path


class RunnerStateCorrupt(RuntimeError):
    def __init__(self, path: str):
        super().__init__(
            "Runner state at {} is unreadable. Refusing to mint a new runner "
            "identity, because that would fence out a supervisor that may "
            "still be running. Inspect or delete the file deliberately."
            .format(path))
