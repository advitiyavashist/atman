"""Operator CLI for connecting one project to a Ticket Board server.

`docs/connect-claude.md` documents these commands verbatim. Everything here is
project-scoped: each subcommand takes a `--project-dir` and refuses any target
that would write user-level Claude configuration or a live agent's worktree.

Separate from `hook.py` on purpose. `hook.py` runs inside Claude Code hundreds
of times a session and must never fail loudly; this runs a handful of times at
an operator's prompt and must never fail QUIETLY -- so its errors are reported
and its exit codes are meaningful.
"""

from __future__ import annotations

import argparse
import json
import sys
import uuid
from pathlib import Path

from .adapter import (
    AdapterConfig,
    BoardClient,
    ClaudeHookError,
    OperatorCredentials,
    diagnose_live,
    disconnect_project,
    exchange_enrollment,
    install_hooks,
    load_enrollment,
    preflight_hook_command,
    revoke_session_lease,
    uninstall_hooks,
)

EXIT_OK = 0
EXIT_ERROR = 1
EXIT_UNHEALTHY = 2


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m ticket_board.adapters.claude.connect",
        description="Connect a project directory to a Ticket Board server.",
    )
    parser.add_argument("--project-dir", default=".", help="the project to enroll (never ~/.claude)")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("enroll", help="redeem a one-time enrollment code and install hooks")
    p.add_argument("--server-url", required=True)
    p.add_argument("--project-id", required=True)
    p.add_argument("--code", required=True, help="from the operator's POST /enrollments")
    p.add_argument("--session-id", default=None, help="board lease id; generated when omitted")
    p.add_argument("--runtime-version", default="unknown", help="`claude --version`")
    p.add_argument("--no-install", action="store_true", help="write identity only, do not touch settings")

    p = sub.add_parser("install", help="(re)write the project-scoped hook entries")
    p = sub.add_parser("doctor", help="report config, delivery and adoption separately")
    p.add_argument("--json", action="store_true", help="machine-readable report")
    p.add_argument("--no-preflight", action="store_true", help="skip executing the hook command")

    p = sub.add_parser("uninstall", help="remove our hooks, keep the local identity")
    p = sub.add_parser("disconnect", help="remove our hooks AND destroy the local credential")

    p = sub.add_parser("revoke", help="operator-only: revoke this agent's board session lease")
    p.add_argument("--note", required=True, help="recovery notes are mandatory")
    p.add_argument("--session-token", required=True, help="operator session token")
    p.add_argument("--csrf-token", required=True)
    p.add_argument("--origin", default=None, help="defaults to the enrolled server url")

    args = parser.parse_args(argv)
    project_dir = Path(args.project_dir).resolve()

    try:
        return _dispatch(args, project_dir)
    except ClaudeHookError as exc:
        print("error: %s" % exc, file=sys.stderr)
        return EXIT_ERROR
    except Exception as exc:  # noqa: BLE001 - an operator command reports its failures
        print("error: %s" % exc, file=sys.stderr)
        return EXIT_ERROR


def _dispatch(args, project_dir: Path) -> int:
    if args.command == "enroll":
        client = BoardClient(args.server_url, args.project_id)
        session_id = args.session_id or ("ses_" + uuid.uuid4().hex[:8])
        enrollment = exchange_enrollment(
            client, project_dir, code=args.code, session_id=session_id,
            runtime_version=args.runtime_version,
        )
        print("enrolled %s (%s) in %s" % (enrollment.agent_name, enrollment.agent_id, project_dir))
        if args.no_install:
            return EXIT_OK
        config = AdapterConfig(enrollment.project_id, enrollment.server_url)
        settings_path = install_hooks(project_dir, enrollment, config)
        print("hooks installed in %s" % settings_path)
        ok, detail = preflight_hook_command(project_dir, enrollment, config)
        print("hook command runnable: %s (%s)" % (ok, detail))
        print("\nStart a NEW Claude session in this project -- hook settings are")
        print("read at session start, so a session already running will not pick them up.")
        return EXIT_OK if ok else EXIT_UNHEALTHY

    enrollment = load_enrollment(project_dir)
    config = AdapterConfig(enrollment.project_id, enrollment.server_url)

    if args.command == "install":
        print("hooks installed in %s" % install_hooks(project_dir, enrollment, config))
        ok, detail = preflight_hook_command(project_dir, enrollment, config)
        print("hook command runnable: %s (%s)" % (ok, detail))
        return EXIT_OK if ok else EXIT_UNHEALTHY

    if args.command == "doctor":
        report = diagnose_live(project_dir, enrollment, config,
                               run_preflight=not args.no_preflight)
        if args.json:
            print(json.dumps(report.as_dict(), indent=2))
        else:
            _print_report(report)
        return EXIT_OK if not report.remediation else EXIT_UNHEALTHY

    if args.command == "uninstall":
        print("hooks removed from %s" % uninstall_hooks(project_dir, enrollment))
        print("local identity kept; run `disconnect` to destroy it too.")
        return EXIT_OK

    if args.command == "disconnect":
        result = disconnect_project(project_dir, enrollment)
        print(json.dumps(result, indent=2))
        if result["spooled_events_left"]:
            print("\n%d undelivered event(s) left in the spool; delete them yourself if unwanted."
                  % len(result["spooled_events_left"]))
        return EXIT_OK

    if args.command == "revoke":
        client = BoardClient(enrollment.server_url, enrollment.project_id)
        operator = OperatorCredentials(
            session_token=args.session_token,
            csrf_token=args.csrf_token,
            origin=args.origin or enrollment.server_url,
        )
        agent = revoke_session_lease(client, enrollment, note=args.note, operator=operator)
        print("lease revoked; agent state is now %s" % agent.get("state"))
        return EXIT_OK

    raise ClaudeHookError("unknown command: %s" % args.command)


def _print_report(report) -> None:
    def mark(value):
        return {True: "ok  ", False: "FAIL", None: "n/a "}[value]

    print("config")
    print("  %s installed        project settings name our hook" % mark(report.config_installed))
    print("  %s hook_executable  that command actually runs on this machine" % mark(report.hook_executable))
    print("delivery")
    print("  %s hook_executed    Claude Code has invoked it at least once" % mark(report.hook_executed))
    print("  %s server_received  the board answered" % mark(report.server_received))
    print("  %s response_deliv.  the board returned injectable context" % mark(report.response_delivered))
    print("adoption")
    print("  %s session_adopted  a REAL session event arrived, not just a probe" % mark(report.session_adopted))
    if report.remediation:
        print("\nremediation:")
        for line in report.remediation:
            print("  - %s" % line)


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
