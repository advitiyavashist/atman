"""The managed Claude runner (T-188).

`Supervisor` is a local process that outlives the model turns it starts: it
holds a fenced lease on one agent, consumes durable wake jobs from the board,
and runs exactly one Claude session at a time in an allowlisted worktree.

See `docs/managed-runner.md` for the operator-facing story and the numbers.
"""

from .client import ApiError, BoardUnreachable, RunnerClient
from .launcher import (
    ClaudeLauncher,
    LaunchFailed,
    LaunchHandle,
    LaunchResult,
    LaunchSpec,
    REQUIRED_FLAGS,
    build_argv,
    child_env,
    is_process_alive,
    preflight,
    resolve_claude,
)
from .state import (
    InFlight,
    RunnerState,
    RunnerStateCorrupt,
    board_session_id,
    load_state,
    new_runner_id,
    new_runtime_session_id,
    runtime_session_id,
    save_state,
    state_path,
)
from .supervisor import DEFAULT_BUDGET, RunOutcome, Supervisor, default_prompt

__all__ = [
    "ApiError",
    "BoardUnreachable",
    "ClaudeLauncher",
    "DEFAULT_BUDGET",
    "InFlight",
    "LaunchFailed",
    "LaunchHandle",
    "LaunchResult",
    "LaunchSpec",
    "REQUIRED_FLAGS",
    "RunOutcome",
    "RunnerClient",
    "RunnerState",
    "RunnerStateCorrupt",
    "Supervisor",
    "board_session_id",
    "build_argv",
    "child_env",
    "default_prompt",
    "is_process_alive",
    "load_state",
    "new_runner_id",
    "new_runtime_session_id",
    "preflight",
    "resolve_claude",
    "runtime_session_id",
    "save_state",
    "state_path",
]
