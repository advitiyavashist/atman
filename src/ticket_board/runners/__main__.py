"""Operator CLI for the managed runner.

Mirrors `adapters.claude.connect`: a handful of commands run at an operator's
prompt, so this must never fail *quietly*. Exit codes are meaningful and every
refusal names its cause.

The credentials come from the project enrollment T-181 already wrote, so
connecting a project and putting it in managed mode are two steps rather than
one flow that has to re-ask for a token.

    python -m ticket_board.runners status  --project-dir ./scratch
    python -m ticket_board.runners run     --project-dir ./scratch --worktree ./scratch
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from ..adapters.claude import load_enrollment
from .client import ApiError, BoardUnreachable, RunnerClient
from .launcher import LaunchFailed, resolve_claude
from .state import RunnerStateCorrupt, load_state
from .supervisor import Supervisor

EXIT_OK = 0
EXIT_ERROR = 1
EXIT_UNHEALTHY = 2


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m ticket_board.runners",
        description="Run or inspect the managed Claude runner for one project.")
    sub = parser.add_subparsers(dest="command", required=True)

    for name, help_text in (("run", "Supervise this project's agent."),
                            ("status", "Report what the runner would do.")):
        cmd = sub.add_parser(name, help=help_text)
        cmd.add_argument("--project-dir", required=True, type=Path)
        cmd.add_argument("--worktree", type=Path,
                         help="The allowlisted worktree runs execute in. "
                              "Defaults to --project-dir.")
        cmd.add_argument("--state-dir", type=Path)
        cmd.add_argument("--permission-policy", default="prompt",
                         choices=("prompt", "allowlist", "deny_all"))
        if name == "run":
            cmd.add_argument("--wait-seconds", type=int, default=20)
            cmd.add_argument("--max-polls", type=int, default=None,
                             help="Stop after this many polls. Omit to run "
                                  "until interrupted.")

    args = parser.parse_args(argv)
    try:
        enrollment = load_enrollment(args.project_dir)
    except Exception as exc:                              # noqa: BLE001
        print("This project is not connected: {}".format(exc), file=sys.stderr)
        print("Run `python -m ticket_board.adapters.claude.connect connect` "
              "first.", file=sys.stderr)
        return EXIT_ERROR

    worktree = args.worktree or args.project_dir
    state_dir = args.state_dir or (Path(args.project_dir) / ".ticket-board")

    if args.command == "status":
        return _status(args, enrollment, worktree, state_dir)
    return _run(args, enrollment, worktree, state_dir)


def _client(enrollment) -> RunnerClient:
    return RunnerClient(enrollment.server_url, enrollment.project_id,
                        enrollment.token)


def _status(args, enrollment, worktree, state_dir) -> int:
    """Report the two facts that are actually different: configured, and able.

    T-181's lesson, restated for the runner: a status that only reads files
    says "installed" for a runner that can never start a process. So this
    resolves the binary too, and says which of the two failed.
    """
    report = {
        "project_id": enrollment.project_id,
        "agent_id": enrollment.agent_id,
        "worktree": str(worktree),
        "state_dir": str(state_dir),
        "runner_id": None,
        "epoch": None,
        "in_flight": None,
        "claude_executable": None,
        "board_reachable": None,
        "remediation": [],
    }
    try:
        state = load_state(state_dir)
    except RunnerStateCorrupt as exc:
        report["remediation"].append(str(exc))
        state = None
    if state is not None:
        report["runner_id"] = state.runner_id
        report["epoch"] = state.epoch
        report["in_flight"] = state.in_flight.run_id if state.in_flight else None

    try:
        report["claude_executable"] = resolve_claude()
    except LaunchFailed as exc:
        report["remediation"].append(str(exc))

    try:
        _client(enrollment).jobs(state.runner_id if state else "rnr_00000000",
                                 wait_seconds=0)
        report["board_reachable"] = True
    except ApiError as exc:
        # A refusal still proves the board answered, which is what this asks.
        report["board_reachable"] = True
        if exc.code == "run_already_active":
            report["remediation"].append(
                "Another supervisor holds this agent's lease: {}".format(
                    exc.details))
        elif exc.status == 403 and "register" in exc.message:
            report["remediation"].append(
                "Not registered yet; `run` will register on start.")
    except BoardUnreachable as exc:
        report["board_reachable"] = False
        report["remediation"].append(str(exc))

    print(json.dumps(report, indent=2, sort_keys=True))
    healthy = report["claude_executable"] and report["board_reachable"]
    return EXIT_OK if healthy else EXIT_UNHEALTHY


def _run(args, enrollment, worktree, state_dir) -> int:
    supervisor = Supervisor(
        _client(enrollment), agent_id=enrollment.agent_id,
        session_id=enrollment.session_id, worktree=worktree,
        state_dir=state_dir, permission_policy=args.permission_policy)
    try:
        outcomes = supervisor.run_forever(wait_seconds=args.wait_seconds,
                                          max_polls=args.max_polls)
    except KeyboardInterrupt:
        # Cooperative: the lease is left to expire rather than torn down, so a
        # run still in flight is reconciled by whoever registers next instead
        # of being orphaned with nothing recorded.
        print("Stopped. The runner lease will expire on its own.",
              file=sys.stderr)
        return EXIT_OK
    except ApiError as exc:
        print("Board refused: {}".format(exc), file=sys.stderr)
        return EXIT_ERROR
    except BoardUnreachable as exc:
        print(str(exc), file=sys.stderr)
        return EXIT_ERROR
    for outcome in outcomes:
        print(json.dumps({"wake_job": outcome.wake_job_id,
                          "run": outcome.run_id, "state": outcome.state,
                          "reason": outcome.reason}, sort_keys=True))
    return EXIT_OK


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
