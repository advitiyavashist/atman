"""Command entrypoint for project-scoped Claude Code hook delivery.

Two invariants govern this file:

1. **It must never block a Claude session.** Every failure path exits 0. A hook
   that raises stops the user's turn, and a board integration is not worth that.
2. **Because of (1), it must leave evidence.** Exiting 0 on failure makes a
   broken hook indistinguishable from a working one from the outside, so every
   invocation appends a bounded receipt that `doctor` reads back. Without the
   receipt, invariant (1) would make the integration undiagnosable.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .adapter import (
    PREFLIGHT_MARKER,
    AdapterConfig,
    BoardClient,
    ClaudeHookError,
    append_receipt,
    build_hook_envelope,
    deliver_hook_event,
    load_enrollment,
    parse_claude_hook_event,
    utc_now,
)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Forward a Claude hook event to Ticket Board V1.")
    parser.add_argument("--project-id", required=True)
    parser.add_argument("--server-url", required=True)
    parser.add_argument("--agent-id", required=True)
    parser.add_argument("--project-dir", default=".")
    parser.add_argument("--spool-cap", type=int, default=25)
    parser.add_argument(
        "--preflight",
        action="store_true",
        help="Prove this command can run, then exit without delivering anything.",
    )
    args = parser.parse_args(argv)

    if args.preflight:
        # Deliberately before any board or enrollment work: this answers exactly
        # one question -- can the interpreter named in the installed command
        # import the adapter and start? -- and must not fail for any other
        # reason. It is the only signal that separates "installed" from
        # "runnable"; see adapter.preflight_hook_command.
        print(PREFLIGHT_MARKER)
        return 0

    project_dir = Path(args.project_dir).resolve()
    receipt = {"ts": utc_now(), "delivered": False, "spooled": False}

    try:
        payload = _read_payload()
        receipt["hook_event_name"] = payload.get("hook_event_name") or payload.get("event_name")
        receipt["session_id"] = payload.get("session_id")

        enrollment = load_enrollment(project_dir)
        if (
            enrollment.project_id != args.project_id
            or enrollment.agent_id != args.agent_id
            or _normalize_url(enrollment.server_url) != _normalize_url(args.server_url)
        ):
            raise ClaudeHookError("hook command identity does not match the project enrollment file")

        config = AdapterConfig(args.project_id, args.server_url, spool_cap=args.spool_cap)
        event = parse_claude_hook_event(payload, enrollment, config)
        receipt["event_id"] = event["event_id"]
        receipt["kind"] = event["kind"]
        receipt["session_id"] = event["session_id"]

        envelope = build_hook_envelope(event)
        client = BoardClient(args.server_url, args.project_id)
        result = deliver_hook_event(
            client,
            enrollment,
            envelope,
            project_dir / ".ticket-board" / "spool",
            config=config,
        )
    except Exception as exc:  # noqa: BLE001 - hook failures must not block Claude.
        receipt["error"] = str(exc)
        _record(project_dir, receipt)
        print("Ticket Board hook queued/skipped: %s" % exc, file=sys.stderr)
        return 0

    receipt["delivered"] = result.delivered
    receipt["spooled"] = result.spooled
    receipt["status_code"] = result.status_code
    receipt["error"] = result.error

    lines = []
    if result.response:
        receipt["deduplicated"] = bool(result.response.get("deduplicated"))
        context = result.response.get("context")
        if isinstance(context, dict):
            lines = [line for line in context.get("lines", []) if isinstance(line, str)]
        receipt["context_lines"] = len(lines)

    _record(project_dir, receipt)

    for line in lines:
        print(line)
    return 0


def _read_payload() -> dict:
    raw = sys.stdin.read() if not sys.stdin.isatty() else ""
    parsed = json.loads(raw or "{}")
    if not isinstance(parsed, dict):
        raise ClaudeHookError("hook payload must be a JSON object")
    return parsed


def _record(project_dir: Path, receipt: dict) -> None:
    """Best-effort receipt write; a failure here must not surface to Claude."""
    try:
        append_receipt(project_dir, receipt)
    except Exception:  # noqa: BLE001 - see invariant (1) in the module docstring.
        pass


def _normalize_url(value: str) -> str:
    return value.rstrip("/")


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
