"""Command entrypoint for project-scoped Claude Code hook delivery."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .adapter import (
    AdapterConfig,
    BoardClient,
    ClaudeHookError,
    build_hook_envelope,
    deliver_hook_event,
    load_enrollment,
    parse_claude_hook_event,
)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Forward a Claude hook event to Ticket Board V1.")
    parser.add_argument("--project-id", required=True)
    parser.add_argument("--server-url", required=True)
    parser.add_argument("--agent-id", required=True)
    parser.add_argument("--project-dir", default=".")
    parser.add_argument("--spool-cap", type=int, default=25)
    args = parser.parse_args(argv)

    try:
        project_dir = Path(args.project_dir).resolve()
        enrollment = load_enrollment(project_dir)
        if enrollment.project_id != args.project_id or enrollment.agent_id != args.agent_id:
            raise ClaudeHookError("hook command identity does not match the project enrollment file")
        payload = json.loads(sys.stdin.read() or "{}")
        config = AdapterConfig(args.project_id, args.server_url, spool_cap=args.spool_cap)
        event = parse_claude_hook_event(payload, enrollment, config)
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
        print("Ticket Board hook queued/skipped: %s" % exc, file=sys.stderr)
        return 0

    if result.response:
        for line in result.response.get("context", {}).get("lines", []):
            print(line)
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
