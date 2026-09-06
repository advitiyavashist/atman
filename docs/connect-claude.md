# Connect Claude Code to Ticket Board V1

Operator guide for the Claude Code hook adapter. Every command below was run
against a real board server and a real Claude Code session (2.1.263) before
being written down; none of it is illustrative.

The adapter has two halves. `hook.py` runs *inside* Claude Code, hundreds of
times a session, and never fails loudly -- a hook that raises would stop the
user's turn. `connect.py` runs at your prompt, a handful of times, and never
fails *quietly* -- its errors are printed and its exit codes mean something.

## Safety boundaries

- Hooks are installed only into a project-scoped `.claude/settings.json`.
- **Never** `~/.claude/settings.json`. The guard refuses `$HOME`, anything under
  `~/.claude`, and any project dir that is a symlink to either.
- A checkout that live agents are already working out of is refused, along with
  anything under it and any git worktree registered against it -- recognised by
  repository identity (`git rev-parse --git-common-dir`), not by comparing path
  strings. Protected checkouts come from `TICKET_BOARD_FORBIDDEN_ROOTS`, an
  `os.pathsep`-separated list; setting it replaces the defaults, and setting it
  empty protects nothing. Ordinary projects are not protected -- enrolling one
  is the normal use.
- The adapter never forwards raw prompts, tool inputs, tool outputs,
  transcripts, environment values or credentials. The bound is fail-closed: it
  refuses to forward a sensitive field rather than redacting it.

> **If you run a nested Claude session for testing, unset `TICKET_AGENT` first.**
> Claude Code merges user-level hooks with project ones, so a machine-global
> `Stop` hook that knows the shared ticket board will fire inside your test
> session and post to the board under your name. This has happened twice
> (T-214 during fixture capture; guarded against during T-181's live runs).

## 1. Mint an enrollment code (operator)

`POST /enrollments` needs an operator session: the `tb_session` cookie, a
matching `X-CSRF-Token` header, and an allowed `Origin`. Starting a server
writes all three to `<state_dir>/operator-session.json` with mode 0600.

```sh
curl -sS -X POST "$BOARD/enrollments" \
  -H 'Content-Type: application/json' \
  -H "X-Project-Id: $PROJECT_ID" \
  -H "Cookie: tb_session=$SESSION_TOKEN" \
  -H "X-CSRF-Token: $CSRF_TOKEN" \
  -H "Origin: $BOARD" \
  -d '{"request_id":"'"$(uuidgen)"'","agent_name":"backend-1","role":"backend"}'
```

The `code` in the response is returned exactly once and is single-use.

## 2. Enroll the project (agent machine)

```sh
python3 -m ticket_board.adapters.claude.connect \
  --project-dir /path/to/project \
  enroll \
  --server-url "$BOARD" \
  --project-id "$PROJECT_ID" \
  --code "$CODE" \
  --runtime-version "$(claude --version | awk '{print $1}')"
```

This exchanges the code for a durable identity, writes it to
`.ticket-board/enrollment.json` (mode 0600), installs the project-scoped hooks,
and then **executes the installed hook command** to prove it can actually run.

Then start a **new** Claude session in that project. Hook settings are read at
session start, so a session that was already running will not pick them up.

## 3. Check it (`doctor`)

```sh
python3 -m ticket_board.adapters.claude.connect --project-dir /path/to/project doctor
```

Exit code `0` means healthy, `2` means something needs attention, `1` is an
error. `--json` prints the machine-readable report.

The doctor reports six facts that fail independently. Collapsing them is how a
doctor shows green for a connection that will never deliver anything:

| Fact | Means |
|---|---|
| `config_installed` | the project settings name our hook |
| `hook_executable` | that command actually *runs* on this machine |
| `hook_executed` | Claude Code has invoked it at least once |
| `server_received` | the board answered one of those invocations |
| `response_delivered` | the board returned injectable context |
| `session_adopted` | a **real** session event arrived, not just a probe |

`config_installed` and `hook_executable` are deliberately separate. A hook whose
command names an interpreter that does not exist is fully installed and will
never run, and nothing else in this report can tell those two apart -- the
command dies in the shell before any adapter code executes, and `hook.py` exits
0 on every error by design. That is exactly the state T-181 found the adapter
in: the command was spelled `python -m ...`, and the machine had only `python3`.

`hook_executed` and `session_adopted` are separate for the same reason in the
other direction: a synthetic probe proves the board is reachable, never that a
real Claude session adopted the hook.

## 4. Disconnect

```sh
# remove our hooks, keep the local identity
python3 -m ticket_board.adapters.claude.connect --project-dir PROJECT uninstall

# remove our hooks AND destroy the local credential and receipts
python3 -m ticket_board.adapters.claude.connect --project-dir PROJECT disconnect
```

`uninstall` removes only entries this agent owns. A foreign hook in the same
file survives, and the file comes back **byte-identical** to what it was before
install -- key order included, which is why the writer does not sort keys. If
the project had no `.claude/settings.json`, uninstall does not create one.

`disconnect` additionally deletes `enrollment.json` and `receipts.jsonl`.
Undelivered events in `.ticket-board/spool/` are **reported, not deleted** --
they are unsent work and dropping them is not the adapter's call.

## 5. Revoke a session lease (operator only)

```sh
python3 -m ticket_board.adapters.claude.connect --project-dir PROJECT revoke \
  --note "agent unresponsive, recovering ticket" \
  --session-token "$SESSION_TOKEN" --csrf-token "$CSRF_TOKEN"
```

Operator credentials are required and there is no agent-token fallback. The
frozen contract makes `revokeSessionLease` "Operator or master only"; sending
the agent's own bearer token is a 403 `agent_token_insufficient` every time.

The `expected_version` for the optimistic-concurrency check is read live from
`GET /agents`. It is the **agent's** version, not the session lease's, and it
increments on every hook event the board records -- so the value cached in
`enrollment.json` at enrollment time is always stale by the time it matters.

## Local files

```text
.ticket-board/enrollment.json   durable identity, mode 0600
.ticket-board/receipts.jsonl    bounded delivery receipts, mode 0600
.ticket-board/spool/            events queued while the board was unreachable
```

Identity is on disk, never from the environment, so changing shells or exporting
a different variable cannot silently create a second identity. The hook command
cross-checks its own `--project-id/--agent-id/--server-url` against this file and
refuses to deliver on a mismatch.

Receipts exist because `hook.py` exits 0 on failure. That is required -- a hook
must not block a session -- but it also makes a broken hook indistinguishable
from a working one from the outside, so every invocation records a bounded
outcome line that `doctor` reads back. Receipts carry outcome metadata only
(event id, kind, session id, status code, spooled flag, redacted error text);
passing a sensitive field is refused, not dropped.

## Envelope rendered by T-184

```json
{
  "request_id": "<uuid>",
  "event": {
    "event_id": "hev_<sha256-prefix>",
    "agent_id": "agt_backend01",
    "session_id": "37461677-bbf8-418b-903d-2afe6374e4ad",
    "kind": "session_start | user_prompt_submit | stop | probe",
    "occurred_at": "2026-09-06T22:12:56Z",
    "cwd": "/path/to/project",
    "note": "PreToolUse: Bash"
  }
}
```

There is no `actor` field; actor identity is bound from the credential (frozen
T-178). Tool and notification events map to `probe` because the frozen contract
has no tool-specific kind, and their `note` carries bounded status text only.

Two things worth knowing about `event.session_id` and `event_id`:

- `session_id` here is **Claude Code's** session UUID from the wire, not the
  `ses_...` board lease id in `enrollment.json`. They are different identifiers
  with different lifetimes: one enrollment spans many Claude sessions.
- `event_id` digests the wire's correlation ids (`tool_use_id`, `prompt_id`,
  `source`, `stop_hook_active`, `agent_type`) alongside agent, session, event
  name and timestamp. Real payloads carry **no timestamp at all**, so
  `occurred_at` falls back to `utc_now()` at one-second resolution; without the
  correlation term every tool call in the same second hashed to the same id and
  the server's `event_id` dedupe -- which is required behaviour -- silently
  dropped all but the first. A byte-identical retry still hashes stable, so
  dedupe keeps working.

## Ground truth about the wire

From real captured payloads (`tests/adapters/fixtures/`, T-214):

- There is **no** `hook_schema_version` field. Not `"1"`, not
  `"claude-code-hooks-v1"` -- absent entirely.
- There is **no** `occurred_at` or `timestamp`.

The adapter tolerates both absences. Do not add a requirement the wire does not
carry.

### Known gap: `Notification` has never been captured

`Notification` fires for a permission request Claude Code cannot resolve, or an
idle-input timeout. Neither is reachable from a scripted, non-interactive
session, which is the only kind that can be driven end to end without a human at
a TTY. T-214 reported this; T-181 re-tested it twice, including a run where a
permission request was genuinely refused, and no `Notification` hook fired
either time.

The adapter handles the event (it maps to `probe`), but that path is covered by
a hand-written fixture, not ground truth. Whoever next has a real interactive
Claude session should capture one and drop it into `tests/adapters/fixtures/`.
