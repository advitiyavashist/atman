# Atman app v2: the local JSON API

**Ticket:** T-1103 · **Spec:** `docs/product/app-v2-spec.md` (§3.3, §4.1, §5.1, §6, §9) · **Server:** `atm ui` (`cmd_ui` in `tickets.py`) · **Version:** `/api/v1`

The app is a TypeScript web app in `ui/`. It holds no board data of its own. The board and the CLI stay in Python, and `atm ui` serves the data as JSON under `/api/v1/`. This document and the JSON Schemas in `docs/api/schemas/` are the contract the app builds against. `tests/test_t1103_app_api.py` checks every route's real output against its schema on a fixture board.

The embedded `atm ui` page (`/`, `/board.json`, `POST /msg`, `POST /auth-reconnect`) is unchanged by this API and keeps working beside it.

## Running it

```sh
atm ui                                                # http://127.0.0.1:8765, bundle at /app/
atm ui --dev-origin http://localhost:5173             # also allow the app's dev server
atm ui --app-dir path/to/dist                         # serve a bundle from somewhere else
```

- **`atm ui --operator <name>` does not exist yet.** T-1104 (#265) adds the flag, and #265 has not merged. Until it does, no operator is configured: `operator` is `""`, `operator_note` says how to set one, the API is read-only, and `POST /api/v1/lead` answers 400 ("set an operator"). The API already reads the flag, so it works as soon as #265 lands.
- `--dev-origin` may be repeated. Each value must be a loopback `http(s)://host:port` origin. Anything else makes `atm ui` exit.
- `--app-dir` defaults to `ui/dist` next to `tickets.py`. The bundle is served at `/app/`.

## Security model

The API sits behind the same gate as the embedded page. That gate comes from T-1105 (#266): `_ui_host_header_is_loopback`, `_ui_token_ok`, `_ui_bind_host_ok` and the per-launch token from `_ui_new_launch_token`.

| Rule | Where | Result when broken |
|---|---|---|
| `--host` must be loopback | `_ui_bind_host_ok` (#266) | `atm ui` exits non-zero |
| The request's `Host` must be a loopback name. This closes DNS rebinding. | `_gate` (#266) | 400 |
| Every write carries `X-Atman-Token: <launch token>` | `_gate(write=True)` (#266) | 400 |
| A request with an `Origin` must come from an allowed origin: the app's own (`http://127.0.0.1:<port>`, `http://localhost:<port>`, `http://[::1]:<port>`) or a `--dev-origin` | `_ui_api_refuse_foreign` | 403, with no CORS headers |
| A request with no `Origin` that the browser marks `Sec-Fetch-Site: cross-site` or `same-site` is refused. This blocks a no-cors embed. | `_ui_api_refuse_foreign` | 403 |
| CORS headers go only to an allowed origin, and only on `/api/v1/*` (never on `/app/` or the embedded page's routes) | `_ui_api_send` | — |
| A CORS preflight is answered only for an allowed origin | `ui_api_options` | 403 |
| Write bodies are `application/json`, at most 64 KiB, and carry no secret-named keys | `_ui_read_json_body` | 400 |

Every response is sent with `Cache-Control: no-store`, `X-Content-Type-Options: nosniff`, `X-Frame-Options: DENY`, `Content-Security-Policy: frame-ancestors 'none'` and `Referrer-Policy: no-referrer`.

### How the app gets the write token

The token is never put in a URL, a query string or a cookie.

1. **Served bundle (production).** `GET /app/` returns the bundle's `index.html` with `<meta name="atman-token" content="…">` and `<meta name="atman-api" content="/api/v1">` inserted before `</head>`. The page is same-origin with the API, so it reads the tag and sends `X-Atman-Token` on writes. A cross-origin page cannot read the HTML: the same-origin policy blocks it, and no CORS header is ever sent for `/app/`.
2. **Dev server.** Start `atm ui --dev-origin http://localhost:5173`. The app then calls `GET /api/v1/session` with the header `X-Atman-Client: app`. That custom header forces a CORS preflight, and the preflight is answered only for the app's own origin or a `--dev-origin`. Any other origin gets 403 and no CORS headers, so it can neither send the request nor read the answer.

A local process that is not a browser, such as `curl`, can also read `/api/v1/session`. That is within the threat model: a local process can already read the board files.

## Routes

Every `GET` is read-only. It starts no subprocess (no `ps`, no `git`), opens nothing in write mode, and moves no watermark. Liveness comes from pid files under `_read_only_liveness`. An audit hook checks this in `test_api_read_routes_no_subprocess_no_writes`. Most routes take `?project=<slug>`. Without it, the route uses the board `atm ui` started on. An unknown slug is 404.

| Method and path | Returns (schema) | Notes |
|---|---|---|
| `GET /api/v1/session` | `session.json` | Needs `X-Atman-Client`. Returns the token, the operator, the default project and its lead. |
| `GET /api/v1/projects` | `projects.json` | One entry per board, from `~/.config/atman/board.json` (`ATMAN_BOARD_CONFIG`): the started board first, then the `projects` entries, then each distinct `boards` value. A bad or missing registry lists only the started board. Each entry has ticket counts and the chosen lead. It never calls `board_dir()`. |
| `GET /api/v1/board?project=&seat=` | `board.json` | The board snapshot for that project, plus an `app` block (operator, lead). `seat=` filters `messages` to that seat. Only the fields in the schema are contract. |
| `GET /api/v1/plan?project=` | `plan.json` | The execution plan: Work nodes in dependency order (`layers`, `order`, `edges`), each with `blockers[]`, `running`, `accepted`, `released` and `status_label`. |
| `GET /api/v1/thread?project=&with=<seat>&before=<msg id>&limit=&all=1` | `thread.json` | The operator ↔ seat thread. `with` defaults to the lead. It pages back through the whole live log (`before=` the previous page's `oldest_id`), with 100 messages by default and at most 200. `all=1` includes archives. With no lead and no `with`, it returns `needs_lead: true` and no messages. An unknown cursor is 400. An unregistered seat is 404. |
| `GET /api/v1/ticket/{id}?project=&all=1` | `ticket.json` | The drill-down: runs with tokens or `unknown`, usage with age, the full review head, structured verdicts, handoff, messages `re=<id>` (newest 50, with `messages_total`), steers, commit / branch / PR, and a copyable `diff_cmd`. No git runs. `{id}` must match `T-<digits>` (400 otherwise). A missing ticket is 404. |
| `GET /api/v1/lead?project=` | `lead.json` | Who the operator talks to. With no lead there is a `picker`: every registered seat with its harness and its capability line from the steer table. It never falls back to master or CoS. With a lead there is a `status`: liveness, running ticket and elapsed time, last output, limit, auth, usage with age, reachability, capability, and `cannot_answer`. |
| `GET /api/v1/needs-you?project=` | `needs-you.json` | Asks waiting on the operator: direct messages with no reply since, prose DECIDEs, `@owner` / `@<operator>` mentions, `stuck:` posts older than an hour, and escalated automated nodes. Every item is `state: "asked"` and `label: "asked (unstructured)"`. Prose is never a ruling. |
| `POST /api/v1/lead` | request `lead-request.json`, response `lead-response.json` | `{seat, project?}`. Needs the operator, the token and an allowed or absent Origin. The seat must be registered on that board and must not be the operator. The route writes `master.json.lead` and a `MASTER.md` line. It is the same as `atm lead set <seat>` (spec §9, decision 2). **Picking a lead also changes how that seat runs:** unless `workforce.json` sets `lifecycle` or `wake_mode` for it, the lead becomes `persistent` and `continuous`, as the master and CoS are (`lifecycle_of` / `wake_mode_of`). A master or CoS change keeps the lead. Until #265 adds `--operator`, this route answers 400. |
| `OPTIONS /api/v1/*` | — | The CORS preflight: 204 for an allowed origin, 403 otherwise. Allowed headers are `Content-Type`, `X-Atman-Token` and `X-Atman-Client`. |
| `GET /app/`, `GET /app/<path>` | the bundle | Static files from `--app-dir`. `index.html` gets the token meta tag. A client-side route (a path without an extension) gets `index.html`. Paths cannot leave the bundle directory. With no bundle, the route returns 404 JSON with a hint. |

Any non-2xx answer is a JSON object with an `error` string (`error.json`).

**Not in this API:** posting a message. The app posts through `POST /msg`, the embedded composer's route. T-1104 (#265) makes it post as the operator only, with `via: ui-operator`. T-1106 (#267) gives it the same wake path as `atm msg`. There is also no accept, reject, merge, done, claim, assign, dispatch, spawn or objective-set route, in any phase (spec §6). The app shows the copyable command instead.

## Invariants the contract encodes

- **Done without an accept is never accepted.** `accepted` is true only with a structured accept or merge record bound to the review head. A release override sets `released`, but the label says *released by override (not accepted)*. `plan.json` and `ticket.json` enforce this with `if`/`then`.
- **Usage never fabricates and always carries its age.** `usage_view.age` is always present. With an empty `checked_at`, `age` must be `"age unknown"` and `remaining_pct` must be `null`. Run `tokens` are `null` when unknown, never 0, and then `tokens_label` is `"unknown"`.
- **Harness is never defaulted to claude.** In the API's own fields (`post.harness`, `picker_row`, `lead_status`, `ticket.runs[].harness`), a harness with no record is `"unknown"`. The badge on a post says whether the harness was recorded at post time. The `agents[].harness` field in `board.json` is the snapshot's own value; T-1106 (#267) fixes its default.
- **Receipts are not acknowledgements.** `receipt.words` may not contain *acknowledged*, *ACK*, *understood* or *on it*.
- **Blocker kinds are closed:** `dep_unaccepted` (the dep is done but not accepted), `dep_open` (the dep is still open, or not on this board), `seat_limited`, `seat_offline`, `auth`, `hold`, `capture`, `blocked`, and `unaccepted` (this node is done without an accept). Each blocker has `{kind, on, text, cmd}`, where `cmd` is a copyable `atm` command.
- **Seat identity:** `author` is `seat@project`. The seat name stays local to its board.

## Schema files

All schemas are JSON Schema draft 2020-12. Their `$id`s share the base `https://schemas.atman.invalid/app-v2/`, so relative `$ref`s such as `common.json#/$defs/post` resolve offline.

| File | Describes |
|---|---|
| `common.json` | Shared `$defs`: `post`, `harness_badge`, `receipt`, `usage_view`, `blocker`, `capability`, `picker_row`, `lead_status`, `error`, `slug`, `ticket_id` |
| `session.json` | `GET /api/v1/session` |
| `projects.json` | `GET /api/v1/projects` |
| `board.json` | `GET /api/v1/board` (the stable subset) |
| `plan.json` | `GET /api/v1/plan` |
| `thread.json` | `GET /api/v1/thread` |
| `ticket.json` | `GET /api/v1/ticket/{id}` |
| `lead.json` | `GET /api/v1/lead` |
| `needs-you.json` | `GET /api/v1/needs-you` |
| `lead-request.json` | `POST /api/v1/lead` request body |
| `lead-response.json` | `POST /api/v1/lead` 200 body |
| `error.json` | Every non-2xx body |

A change to a route's output must update its schema in the same PR. The contract tests fail otherwise.
