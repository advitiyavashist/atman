# Connect Claude Code to Ticket Board V1

Draft for T-181. This page documents the fixture-driven adapter core from
T-201; live Claude session capture and final operator copy belong to T-181.

## Safety Boundaries

- Install hooks only into a project-scoped `.claude/settings.json`.
- Never write to `~/.claude/settings.json`.
- Do not enroll live agent worktrees under `steer/.worktrees/*` or
  `tickets/.worktrees/*`; use scratch project directories for tests.
- The adapter never forwards raw prompts, tool inputs, tool outputs,
  transcripts, environment values or credentials.

## Local Files

The adapter stores durable identity at:

```text
.ticket-board/enrollment.json
```

That file contains `project_id`, `server_url`, `agent_id`, `agent_name`,
`session_id`, `token`, `created_at` and `lease_version`. The adapter refuses to
run from environment variables alone, so changing shells does not create a new
identity by accident.

Offline events are queued under:

```text
.ticket-board/spool/
```

The queue is capped. If the board is offline, hook delivery is retried with
bounded backoff and then spooled; the hook command exits successfully so normal
Claude work is not blocked.

## Envelope Rendered By T-184

The dashboard/enrollment/doctor UI should render this adapter envelope:

```json
{
  "request_id": "<uuid>",
  "event": {
    "event_id": "hev_<sha256-prefix>",
    "agent_id": "agt_backend01",
    "session_id": "ses_a1b2c3d4",
    "kind": "session_start | user_prompt_submit | stop | probe",
    "occurred_at": "2026-09-06T14:32:00Z",
    "cwd": "/path/to/project",
    "note": null
  }
}
```

There is no `actor` field. The frozen T-178 decision is that actor identity is
bound from the credential. Tool and notification hook events currently map to
`probe` because the frozen contract has no tool-specific event kind; their
`note` contains only bounded status text such as `PreToolUse: Bash`.

## Doctor Semantics

The doctor reports DELIVERY and ADOPTION separately:

- `config_installed`: project settings contain Ticket Board-owned hook entries.
- `server_received`: a board fixture response accepted an event.
- `response_delivered`: the accepted response included injectable context.
- `session_adopted`: a real Claude hook event fired. A synthetic probe must not
  set this to true.

T-181 must replace the synthetic fixtures in `tests/fixtures/claude_hooks/` with
captured Claude payloads, or add captured fixtures beside them while keeping the
synthetic labels. It must capture at least: `SessionStart`,
`UserPromptSubmit`, `PreToolUse`, `PostToolUse`, `Stop`, `SubagentStop` and
`Notification`, plus the exact hook schema version emitted by the installed
Claude Code version.
