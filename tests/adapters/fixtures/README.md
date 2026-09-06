# Real Claude Code hook payload fixtures

Captured from a live, disposable Claude Code session on 2026-09-06 (`claude --version` ->
`2.1.263 (Claude Code)`), not hand-written. The capturing session ran in a scratch project
with a project-local `.claude/settings.json` whose hooks did nothing but pipe their raw
stdin to a file (`cat > capture/<event>_<ts>.json`), then it was driven through a Write,
a Read, an Edit, a Bash call, and a Task-tool subagent launch. Every file here is that raw
stdin, unmodified except for the scrub below -- nothing was tidied, reordered, or invented.

## Why this exists

Every adapter test before this ticket (`tests/fixtures/claude_hooks/*.json`) asserted
against payloads the team invented. This directory is ground truth instead, so the T-181
adapter is testable against what Claude Code actually sends rather than our guess at it.

## hook_schema_version: does not exist

The ticket asked to preserve `hook_schema_version` verbatim because "that field is the
whole point of the exercise." Real payloads from this Claude Code version carry **no such
field at all** -- not `"1"`, not `"claude-code-hooks-v1"`, absent entirely. Same for
`occurred_at` / `timestamp`: neither is present; there is no event timestamp in the raw
hook payload. `src/ticket_board/adapters/claude/adapter.py::parse_claude_hook_event`
already tolerates both of these correctly (schema_version accepts `None`; occurred_at
falls back to `utc_now()` when missing), so no adapter change is required -- but the
frozen contract and any future adapter work should stop assuming either field will show
up on the wire.

## What was scrubbed

Real payloads carried this machine's absolute paths, one real session id, one real
prompt id, and one real subagent id. Every fixture had these literal strings replaced,
consistently, everywhere they occurred (including inside nested `transcript_path`,
`scratchpad_dir`, and tool `file_path` values) -- key names, value types, and nesting are
otherwise untouched:

| Real value (this machine) | Placeholder |
|---|---|
| the operator's home directory prefix (`/Users/kavana/...`) | `/home/agent/...` |
| the capture project's absolute cwd | `/home/agent/projects/example-project` |
| Claude Code's path-flattened transcript project directory | `/home/agent/.claude/projects/example-project` |
| the real session id (one UUID, reused across every event since they're one session) | `11111111-1111-1111-1111-111111111111` |
| the real `prompt_id` (one UUID) | `22222222-2222-2222-2222-222222222222` |
| the real subagent `agent_id` | `a0000000000000000` |

Left un-scrubbed, deliberately: `tool_use_id` values (opaque per-call correlation ids,
not identifying, not read by the adapter) and all file *content* (`hello`, `hello world`,
`echo capture-test`, `OK`) -- none of it is sensitive, and it is real tool I/O the adapter
must eventually consume as-is. No prompt in this batch contained a secret or transcript
excerpt worth redacting; the *existing* invented fixtures
(`tests/fixtures/claude_hooks/{prompt,pre_tool}_with_secret.json`) already cover the
"a secret must not leak downstream" case at the adapter level and are out of scope here.

## Files

| File | Event | Notes |
|---|---|---|
| `session_start.json` | SessionStart | `source: "startup"`. Claude Code also emits `resume`/`clear`/`compact` as `source` values; only `startup` was reachable from a scripted, non-interactive capture session (see Gaps). |
| `user_prompt_submit.json` | UserPromptSubmit | Real `prompt` text is the literal instruction script given to the capture session. |
| `pre_tool_use_write.json` / `post_tool_use_write.json` | PreToolUse / PostToolUse | `Write` tool, new-file create. |
| `pre_tool_use_read.json` / `post_tool_use_read.json` | PreToolUse / PostToolUse | `Read` tool. |
| `pre_tool_use_edit.json` / `post_tool_use_edit.json` | PreToolUse / PostToolUse | `Edit` tool -- the file-edit shape the ticket called out; note `tool_response` carries `structuredPatch`, unlike Write/Read. |
| `pre_tool_use_bash.json` / `post_tool_use_bash.json` | PreToolUse / PostToolUse | `Bash` tool -- the ticket's other required shape; `tool_response` is `{stdout, stderr, interrupted, isImage, noOutputExpected}`, structurally unrelated to the file-edit shapes above. |
| `pre_tool_use_task.json` / `post_tool_use_task.json` | PreToolUse / PostToolUse | `Task`/Agent-launch tool (`tool_name: "Agent"`) that started the subagent below. Not required by the ticket but captured for free and kept because its `tool_response` (nested token usage, `agentId`, `resolvedModel`, ...) is a third distinct shape worth having ground truth for. |
| `subagent_stop.json` | SubagentStop | From the subagent launched above; `last_assistant_message: "OK"`. |
| `stop.json` | Stop | The *first* Stop of the run (`stop_hook_active: false`). |

## Gap: Notification was not captured

Not included, and not fabricated. `Notification` fires for a permission request Claude Code
can't resolve non-interactively, or for a genuine idle-input timeout -- neither is
reachable from a scripted `claude -p ... --permission-mode bypassPermissions` capture
session, which is the only kind of session that can be driven end-to-end without a human
at a TTY. Forcing it would mean either attaching to a real interactive terminal (not
available to this agent) or dropping `bypassPermissions`, which risks a hang waiting on
approval that nothing here can answer. State it as a gap rather than inventing a payload:
whoever next has a real interactive Claude Code session in front of them should capture
`Notification` and drop it in next to these.

## Incidental finding, not a fixture concern

The capture session's *global* (user-level) hooks stayed active alongside the
project-local capture hooks used here (Claude Code merges both), including a `Stop` hook
that is aware of the shared ticket board and this agent's `TICKET_AGENT` identity. That
combination made the capture session post one real, misleading status message to the
board under this agent's name mid-capture; it was corrected on the board immediately
(`tickets msg`, 2026-09-06 12:38Z) and is unrelated to the fixtures themselves. Anyone
repeating this capture technique should run the nested session with `TICKET_AGENT`
unset so its Stop hook has no owner to act on.
