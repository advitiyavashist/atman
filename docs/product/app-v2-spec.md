# Atman app v2: chat with the project lead, beside a live execution plan

**Ticket:** T-1103 · **Status:** SPEC (no code) · **Base:** `main@9abf7cc` · **Surface:** the shipped `atm ui` app in `tickets.py` (`UI_HTML`, `cmd_ui`, `board_snapshot`). Not the older `ui/` dashboard. A native wrapper, if one is built, is a thin shell around this local page.

## 0. What this spec is for

The operator wants one screen where three things are visible at once:

1. **Chat with a persistent agent.** A long-lived coordinator for the project that keeps context across sessions, plans, dispatches work to seats, reports status and answers questions. The operator talks to it the way they would talk to a lead. It is not a message board between seats.
2. **The execution plan.** This is the centrepiece. It shows the objective broken into tickets, their dependency order, who owns each, what is running now, and what is blocked and why: a dependency that is not accepted, a seat that is limited, or a DECIDE that is waiting on the owner.
3. **Live status with drill-down.** Click a plan step to see its ticket, the runs working it (elapsed time, tokens, usage), the review head and accept/reject verdicts, the handoff, the messages about it, and the commit. Then back out to the plan.

Projects are first-class. A seat is shown as `seat@project`, there is a project switcher, and each project has its own plan, its own coordinator and its own fleet. For optimization work, datasets and evals are first-class objects alongside tickets and seats. Named rooms are an optional late phase. They are not the organising idea. The user's answers to the open questions are recorded as decisions in §9, and the spec below already reflects them.

### Invariants that must survive (already enforced; v2 may not weaken them)

| Invariant | Where it is enforced today | What v2 must keep |
|---|---|---|
| Delivery badges are receipts, never an agent ACK | `_message_delivery`, `_delivery_receipt_label` (tickets.py); `deliveryTags()` in `UI_HTML`; `#channelHonesty` copy | Chat bubbles show *posted / inbox read / wake confirmed*. They never say *acknowledged*, *understood* or *on it*. |
| Done-without-accept is never shown as accepted | `work_view.unverified_done_ids`, `work_view.review_of`, `counts.done_unverified` in `_board_snapshot_body` (T-992) | Plan nodes and cards say **done, not accepted** until a structured `review_events` accept exists. |
| Usage shows its age and never fabricates | `provider_usage.ui_reading` / `compact_reading` / `age_label`; `agent_map.build` tokens `None` means unknown | Every usage figure carries *checked N ago*. A missing figure renders as `unknown`, never as `0` or `$0`. |
| No subprocess and no writes on read paths | `board_snapshot` ("Read-only"), `agent_map_data` ("no ps, no writes"), test `tests/test_t1072_agent_map.py::test_read_only_no_subprocess_no_writes` (audit hook) | Every new `GET` route passes the same audit-hook test. No `git`, no `ps`, no marking-read on a GET. |
| Localhost-only | `atm ui --host 127.0.0.1` default; `_ui_msg_origin_ok` | Hardened in §6. Today `--host` accepts any address, and the Origin check does not check the Host header against loopback. |

Two current behaviours conflict with these rules. v2 fixes both in phase 1:

- **The composer can post as any seat.** `#cFrom` lists every agent, and `POST /msg` accepts any registered `from` (`cmd_ui.do_POST`: `if not _agent_rec(board, sender)`). A localhost page can therefore impersonate a seat. Provenance makes this worse. `post_message(..., explicit=None)` stamps `via` from the environment of the process that started `atm ui`, not from the person who clicked Post.
- **Harness defaults to `claude`.** `_board_snapshot_body` does `agent_wf.get("harness") or agent_wf.get("tool") or "claude"`, while `_seat_harness` says "Empty means unknown -- never invent claude". On the real board, 53 of 250 workforce entries have neither `harness` nor `tool`. Each of those would get a confident `claude` badge it has not earned.

### Relation to the Team IA v1 lock

`docs/product/atman-team-ia-v1.md` fixes the IA at *Objective · Team · Work · Intervene* and says "do not replace [the four columns] with a DAG". v2 keeps every one of those surfaces and changes only their arrangement:

- Work stays as the Plan's three layouts: graph, list and columns. The columns layout is the four-column board.
- Intervene becomes the conversation pane.
- Team becomes Fleet.

Per-agent chat stays on `messages.jsonl`. There is no second chat store and no shared-memory brain. That lock is kept.

---

## 1. Screen shape

```
sidebar                 | conversation (primary)        | execution plan (centrepiece)       | drill-down (on select)
------------------------+-------------------------------+------------------------------------+----------------------
project switcher        | coordinator status strip      | objective + exit criterion         | ticket, runs, review,
Lead (chat)             | operator <-> lead thread      | Finishing / Blocked / Next strip   | handoff, messages,
Plan                    | inline action cards (records) | graph | list | columns             | commit, steers,
Needs you (DECIDE)      | composer (posts as operator)  | typed blocker chips per node       | eval gate (phase 2B)
Fleet · Runs · Evals    |                               |                                    |
Mail · Rooms (later)    |                               |                                    |
Hub · clock             |                               |                                    |
```

---

## 2. Mapping table: every element, what backs it today, what is missing

Legend for **R/W**: R is read-only. W is a write route. W-existing is `POST /msg` or `POST /auth-reconnect`, which already exist.

### 2.1 Sidebar

| Element | What it shows | Real data that backs it TODAY (file:function) | Gap | R/W |
|---|---|---|---|---|
| Report an issue | Opens the public issue tracker with a prefilled, redacted diagnostic (board path hash, version, snapshot `error`) | none | No issue URL in app config. The diagnostic must be built client-side from `/board.json` (no `atm doctor`, which spawns git). | R (navigation only) |
| Voice on/off | (not built) | none | **Out of scope** (decision 5). Browser speech recognition can also be cloud-backed, which would conflict with localhost-only. No control is rendered. | — |
| Project switcher | Registered projects, current one highlighted, each with an open/blocked count | `_atman_config_path` + `_configured_shared_board` (tickets.py) read `~/.config/atman/board.json {"boards": {repo: board}}`; `_board_snapshot_body` returns `project` = basename of the board's parent dir | No project registry with names. No per-project cheap counts. `/board.json` serves one board only. See §4.1. | R |
| New project | Create a project: board and registry entry | `cmd_init`, `_init_resolve_board` (tickets.py) | No route and no registry write. Phase 3. | W |
| Lead (conversation) | The operator ↔ coordinator thread | `board_snapshot_for_request(seat=)`, `message_involves_seat`, `filter_messages_for_scope`; JS `openSeatChat`, `visibleMessages` | Thread is cut from the newest 40 messages of the whole board (`messages=40`). On a 9,647-message board that is only a few minutes of history. Needs a per-thread read route (§3.3). | R |
| Plan | The execution plan | `work_view.work_payload` via `_board_snapshot_body` → `work`; `workflow_graph`; JS `renderGraph`, `setWorkView` | See §3. | R |
| Needs you (DECIDE queue) | Open questions the owner must answer, oldest first | Phase 1 heuristic sources: messages `to`/`@` the operator (`_addressed_to`); `stuck:` convention (`pending_work` → `stuck_messages`); `_attention_snapshot`; escalated automated nodes (`work_view` `escalated`) | No DECIDE record exists. On the real board "DECIDE" appears only as prose (0 structured records). §4.5. | R (phase 1), W (phase 2A ruling) |
| Fleet | Seats of this project: state, harness, lifecycle, wake mode, limit, auth, usage | `_board_snapshot_body` → `agents[]` (`agent_liveness`, `_active_seat_limit`, `lifecycle_of`, `wake_mode_of`, `_agent_auth_surface`, `provider_usage.ui_reading`); JS `renderSeats` | Harness defaults to `claude` (fix in phase 1). Nothing else is missing. | R |
| Sessions / Runs | Seat runs grouped by ticket: running, stalled, limited, dead, with elapsed time and tokens | `agent_map.build` via `agent_map_data`; JS `renderAgentMap` | None for the list. | R |
| Two-agent side-by-side | Two seat threads next to each other (for example author and reviewer on one ticket) | `message_involves_seat` twice | Same 40-message window gap. Phase 4. | R |
| Evals | Datasets, evals, runs, results for optimization tasks | none on the board. Artifacts exist only as documents and scripts in the product repo (for example the stage-2 bake-off and binary-holdout reports, with custody under `~/.atman-custody/<product>/<ticket>/`) | Whole data model (§4.10). Phase 2B. | R, W |
| Team Launch | Start a seat for uncovered work (copyable command) | `cmd_spawn` (CLI only); harness catalog rows in tickets.py (`if_yes` strings) | The app does not spawn. It shows the copyable `atm spawn …` line. | R (copy) |
| Team recovery | Seats needing recovery: limited, dead, auth lost, adapter failed | `agents[]` → `adapter_state`, `adapter_reason`, `auth_surface.recovery`; `POST /auth-reconnect` (`_ui_auth_reconnect`, no login, no model start) | None. | R + W-existing |
| Messages · Room | The project's broadcast channel | `is_board_broadcast`, `seat_thread_summaries().board` | None (the room is the board). Named rooms: §4.9. | R |
| Messages · Mail + unread badge | Mail addressed to the operator, unread count | `_addressed`, `_seen_since`, `_is_unread`, `_visible_after_join` (read logic); `inbox_seen` on `agents/<operator>.json` | No operator identity. Unread must be computed without writing the watermark. Marking read is a write. §4.6. Phase 3. | R, W |
| Hub · Services | Adapters, watchers, remote bridge, native wake per seat | `agents[]` → `watcher_count`, `adapter_*`, `wake_delivery`; `sa.public_adapter_state` | None for the read. | R |
| Hub · Register | How to add a seat (copyable `atm join` / `atm spawn` lines, onboarding checklist) | `_onboarding_checklist`, `_next_step_hint` | None (read and copy only). | R |
| Hub · Config | Board path, shared-board mapping, primary marker, operator, port/host | `board_dir`, `_configured_shared_board`, `_is_marked_primary` (`.primary`), `cmd_board_mark_primary` | No config route. Read-only display in phase 4. Changes stay CLI. | R |
| Clock | Local time | JS `tickClock()` | None. | R |

### 2.2 Conversation pane (primary, left of the plan)

| Element | What it shows | Real data TODAY | Gap | R/W |
|---|---|---|---|---|
| Title pill | `Lead · <seat>@<project>`, its harness, and whether it takes mid-run messages or answers on its next turn | `current_master(board)` (for display of master / CoS roles); `steer.STEERABLE_PROVIDERS` / `steer.UNSTEERABLE` via `_seat_harness` | No lead setting exists. The lead is a per-project setting the user picks (decision 2): `master.json.lead`. **Unset means the app asks the user to pick; it never guesses.** | R (display), W (`POST /lead`) |
| Coordinator status strip | working / idle / limited / stalled / dead / unknown; run elapsed; last output age; limit reset; auth; harness usage with age | `agent_liveness` (`STATE_WORDS`); `agent_map` running row (`elapsed_s`, `tokens`); `_steer_seat_row` (`last_output_at`); `_active_seat_limit`; `_agent_auth_surface`; `provider_usage_snapshot` | None for the fields. They need joining into one strip. | R |
| Post author `seat@project` | e.g. `planner@steer` | `from` on the message; project slug (§4.2) | Display-only composition. | R |
| Harness badge (coloured by provider) | `claude` / `codex` / `cursor` / `unknown` | Workforce `harness` / `tool` (`_seat_harness`); trajectories stamp `harness` on events (`trajectories.build` → `agent_harness`) | Messages carry no harness. The workforce value is *current*, not at-post-time. §4.4. | R |
| Timestamp | "Today at 5:37 PM" in the viewer's zone | message `at` (raw UTC ISO, deliberately unformatted server-side) | None. | R |
| Text | Message body, `@mentions` highlighted, ticket ids linked into the plan | message `text`, `mentions`; JS `mentionText` | Ticket-id linking is new JS only (link only when the id exists in `work.nodes`). | R |
| Copy button | Copies text | none | JS only. | R |
| Delivery receipts | posted / inbox read / wake confirmed / queued-offline / limited | `_message_delivery` (`receipts[].label`), `deliveryTags()` | None. Must never read "acknowledged". | R |
| Status report posts | e.g. "reached independent audit at exact head 24fb8c0; CI green. No owner action." | plain messages | None. They render as text. The SHA links to the node whose `artifact.sha` matches. | R |
| Owner decision request (`@owner DECIDE: …`) | A decision card with state *asked* | Phase 1: prose only, flagged "unstructured ask" | DECIDE record §4.5. | R (1) / W (2A) |
| PM ruling on a DECIDE | "PM ruling (owner can override): …" with state *ruled* | none structured | DECIDE record §4.5. | W (2A) |
| Inline action cards | "created T-1110", "dispatched T-1110 → coder@atman", "ruled D-7", "started eval run" | Records that exist: `review_events` (`atm accept` / `reject`), trajectory `claim` / `review` / `done` / `block` / `reopen` / `merge` events, ticket `notes` with `dispatch: reserved for <seat>` (`agent_map._DISPATCH_RE`), ticket `steers[]` (`cmd_steer`), `master log` lines (`_master_log`) | No `create` or `dispatch` trajectory event with an actor. Tickets record no creator. §4.7. | R |
| Search + pager "^ n of N v" | Find in this thread and the plan; step through hits | none | §4.8. Phase 3. | R |
| Composer "Tell the lead…" | Posts as the operator to the lead (DM). Optional `re` ticket. | `POST /msg` → `post_message`; JS `cSend` handler; mention bar | `from` must be fixed server-side to the operator (§5). | W-existing (hardened) |
| Attach | Attach a file reference | none | Out of scope. v2 posts text only (`_UI_MSG_MAX_BYTES` 64 KiB, `_ui_payload_has_secrets`). Attach is a path reference, never an upload, in a later phase. | — |
| Pin | Pin a message or ticket to the top of the conversation | none | §4.6. Phase 3. | W |
| Mic | (not built) | none | Out of scope (decision 5). | — |
| Send | Post | `POST /msg` | Hardened. | W-existing |
| Header identity switcher | Who you are (operator, fixed) plus a read-only *view as seat* lens | `THREAD_SEAT` (`openSeatChat`) | The lens is read-only. Posting identity never switches (§5). | R |

### 2.3 Execution plan and drill-down

| Element | What it shows | Real data TODAY | Gap | R/W |
|---|---|---|---|---|
| Objective + exit | Standing objective, state, exit criterion, "no exit" flag | `load_objective`, `objective_state`, `objective_exit_missing` → `work.objective` | Tickets are not linked to an objective (§3). | R |
| Now strip | Finishing / Blocked / Next | `_first_screen(work)`, `work.summary` | None. | R |
| Plan layouts | graph / list / columns | `WORK_JS` (`work_view.py`), `renderGraph`, `#workViews` | None. Kept as-is. | R |
| Node: owner, phase, evidence | `working` / `review` / `posted` / `reserved` / `ready` / `waiting` / `capture` / `hold` / `blocked` / `done` | `work_view.phase_of`, `evidence_of`, `_who_of` | None. | R |
| Node: blocked-why chips | *dep T-x not accepted* · *seat limited until 17:40* · *waiting on DECIDE D-7* · *HOLD* · *capture* | `work_view.wait_of` (deps / capture / hold / blocked); `unreleased_dep_id`, `dep_released`, `refuse_unreleased_reason` (accept-gated release); `agents[].limit` | `wait_of` says "waits on T-x" and does not say whether T-x is *done-unaccepted* or *open*. Seat-limit is not joined to nodes. DECIDE does not exist. §3.2. | R |
| Node: running now | Pulse when an agent-map run is `running` on this ticket | `agent_map.build` groups by `ticket` → `running` | Join only (client-side). | R |
| Drill-down: ticket | Title, status, acceptance (`proof` / `cause` / `change`), open questions, last note | `work.nodes[].acceptance`, `last_note`; JS `showGraphDetail` | None. | R |
| Drill-down: runs | Each run: seat@project, harness, role (author / reviewer / fixer), state, elapsed, tokens in/out or *unknown* | `agent_map.build` rows (`elapsed_s`, `tokens_in`, `tokens_out`, `role`, `verdict`) | None. | R |
| Drill-down: usage | Harness quota with age | `provider_usage.ui_reading` per harness | None. | R |
| Drill-down: review | Review head (full SHA), structured accept/reject with reviewer and SHA, superseded verdicts, *done, not accepted* | `work_view.review_of`, `review_verdict.iter_structured`, `submitted_sha`, node `artifact` | None. | R |
| Drill-down: handoff | Inherited handoffs from finished deps and the ticket's own handoff note | `work.nodes[].handoff`, `work_view.handoff_notes` | None. | R |
| Drill-down: messages about it | All messages with `re = <id>`, newest first | `work_view._messages_by_ticket(all_msgs)` (computed but not shipped per node) | Snapshot ships 40 messages. Needs `GET /ticket/<id>.json` (§3.3). | R |
| Drill-down: steers | Mid-run course corrections and their receipts | ticket `steers[]` (`cmd_steer`, `steer.steer_record`) | Not in the snapshot. Add to the ticket route. | R |
| Drill-down: diff / commit | Branch, commit, PR link, diffstat | node `artifact` {commit, branch, pr, sha} | A diff needs `git`, which is forbidden on read paths. Record the diffstat at `atm review` time (§3.2). Until then: SHA, branch, PR link and a copyable `git diff main...<sha>`. | R |
| Drill-down: eval gate | For an optimization task: bound eval, frozen config, holdout result vs rule | none | §4.10. Phase 2B. | R |
| Back to plan | Close the panel, restore scroll and selection | `#graphDetail`, hash routing (`applyWorkHash`) | JS only. | R |

### 2.4 Datasets, evals, runs, results, optimization tasks (phase 2B)

| Element | What it shows | Real data TODAY | Gap | R/W |
|---|---|---|---|---|
| Dataset | Role (dev / holdout), provenance, size per slice, label metadata (who, blind?, agreement), version, content hash, custody reference | none on the board. Product-repo documents state these in prose. Custody dirs exist on disk under `~/.atman-custody/`. | `datasets/DS-*.json` (§4.10). | R |
| Eval | Pre-registration: metrics, thresholds, adoption rule, required n per slice, frozen commit, config hash, dev and holdout dataset refs | none | `evals/EV-*.json`. | R |
| Eval run | predict step (blind) and score step; dataset role; config hash; run-log digest; custody receipt digest; blindness-guard receipt | none. Scripts have a runtime guard (`install_blindness_guard`); one report records that no run log or custody receipt was kept. | `eval_runs.jsonl`. | R, W |
| Result | Aggregates only: metric, value, CI, n, DIRECTIONAL / DEFINITIVE, dev-vs-holdout gap, tokens per case, p95 latency, cost or *unknown*, departures from the rule | none | `eval_results.jsonl`. | R |
| Optimization task | Ticket bound to an eval. Accept requires the eval gate at the exact frozen config. | none | Ticket `eval_gate` field plus a gate check (§4.10). | R, W |

---

## 3. The execution plan: reuse vs genuinely missing

### 3.1 Reuse as-is

- **Work payload** (`work_view.work_payload`). It already provides:
  - every active ticket plus done-unaccepted tickets (`workflow_graph(keep_done=unverified_done)`);
  - `phase` with honest evidence text;
  - `wait` {kind, on, text, cmd};
  - `dispatch` with receipts;
  - `progress` (which dep finished, which still waits);
  - `starts` (success-to-next);
  - `review` (structured, SHA-bound, superseded-aware);
  - `artifact`;
  - `handoff`;
  - `layers` / `order` / `depths` (dependency order);
  - `summary.finishing` / `blocked` / `waiting_on` / `next`.

  This is the plan. v2 does not build a second planner.
- **Layouts:** the graph, list and columns views and `work-jump` (`#workJump`) stay. The plan pane is the Work tab moved beside the chat.
- **`atm graph` / `atm map` / `atm plan-status`:** same edges (`--after` deps) and lanes (`capture` / `ready` / `waiting-on-merge` / `parked`). The plan legend reuses their words.
- **`atm plan`** (bulk create from JSON) stays the write path for "break the objective into tickets". The lead runs it. The app shows the result as cards (§4.7).
- **Agent map** (`agent_map.build`): runs per ticket with elapsed time, tokens, role and verdict. It becomes the drill-down's Runs section and the node's "running now" pulse.
- **Provider usage** (`provider_usage_snapshot`, `ui_reading`): quota with age.

### 3.2 Genuinely missing (new data, each small)

| Missing | Why it matters | Proposal | Phase |
|---|---|---|---|
| **Typed blocker reasons** | "Blocked and why" is the plan's main question. Today `wait_of` returns `deps` without saying the dep is *done but not accepted*, and never says *seat limited* or *waiting on DECIDE*. | Add `work_view.blockers_of(t, …) -> [{kind, on, text, cmd}]` with kinds `dep_open`, `dep_unaccepted` (from `dep_released` / `unreleased_dep_id`), `seat_limited` (owner or `reserved_for` has `_active_seat_limit`), `seat_offline` (`adapter_state` in `offline` / `queued-offline` / `failed`), `auth` (`auth_surface.state` not ready), `decide_open` (§4.5), `hold`, `capture`, `blocked`. Pure function over data the snapshot already loads. | 1 (all except `decide_open`, computed client-side from `agents[]` and `work.nodes`); 2A (server-side, plus `decide_open`) |
| **Objective membership** | "The objective broken into tickets" needs to know which tickets belong to the current objective. Tickets have `epic` and `sprint`, not `objective_id`. Trajectory events do carry `objective_id` (`trajectories.objective_id`). | Stamp `objective_id` on tickets created while an objective is active (`create()`). Tickets without it show under "not linked to the objective", never silently in or out. | 2A |
| **Diffstat** | The drill-down should show what changed without running git on a read. | `atm review` already runs git to pin `review_head`. Record `review_stat: {head, files, insertions, deletions, paths[:50]}` on the ticket at that moment. The app shows it. A head that moved after review shows *stat is for <old head>*. | 2A |
| **Per-ticket messages and steers** | The snapshot ships the newest 40 messages board-wide. | `GET /ticket/<id>.json`: the ticket, its node, agent-map group, `steers[]`, `review_events`, and messages with `re=<id>` from live `messages.jsonl` (archives on `?all=1`). Read-only; audit-hook tested. | 1 |
| **ETA / critical path** | Tempting, but there is no data that supports an estimate. | Not built. The plan shows dependency depth and "running for N" only. It never shows an ETA. | — |

### 3.3 New read routes (all `GET`, no subprocess, no writes)

| Route | Returns | Built from |
|---|---|---|
| `/board.json?project=<slug>&seat=<name>` | Existing snapshot for the chosen project | `board_snapshot_for_request` on the registry path (§4.1). Never `board_dir()`, which can `sys.exit` on shadow boards. |
| `/projects.json` | `[{slug, board, repo_roots, open, blocked, review, lead, lead_state}]` | Registry plus a cheap per-board scan: count `T-*.json` statuses, read `master.json`. No liveness probe. |
| `/thread.json?project=&with=<seat>&before=<msg_id>&limit=100` | Operator ↔ seat thread, paged back through the live file (archives on demand) | `load_messages`, `message_involves_seat` |
| `/ticket/<id>.json?project=` | Drill-down payload (§3.2) | as above |
| `/decisions.json?project=` (2A) | Open, ruled and overridden DECIDEs | §4.5 |
| `/evals.json`, `/eval/<id>.json`, `/dataset/<id>.json` (2B) | Eval objects, aggregates only | §4.10 |
| `/search.json?project=&q=&cursor=` (3) | Hits across messages, tickets, notes | §4.8 |

---

## 4. Data-model gaps and proposals

### 4.1 The project model: **a project is a board**

**Decision.** A project is one board directory (`.tickets`). It is not a namespace inside a board. The app gains a machine-level **project registry** that names the boards.

**Why.** Everything the honesty rules depend on is already per-board:

- `master.json` (who is master / CoS);
- `objective.json`;
- `messages.jsonl` and its receipts;
- `agents/<seat>.json` (`inbox_seen`, limits, auth);
- `workforce.json` (harness, `wake_mode`, `lifecycle`);
- `trajectories.jsonl`;
- `provider_usage.json`;
- the accept gate (`review_events` on `T-*.json`);
- the T-959 shadow-board refusal.

A namespace inside one board would force a project filter into every one of those readers and writers. It would also leak across projects: a broadcast would wake seats of another project (`_addressed_to` treats empty `to` as everyone), and one `master.json` cannot hold two leads. Separate boards need zero reader changes. The only new code is in the app (which board to read) and in a small registry.

**Registry.** Extend the existing `~/.config/atman/board.json` (already outside every repo, already overridable with `ATMAN_BOARD_CONFIG` for tests):

```json
{"boards":   {"/path/to/repo": "/path/to/.tickets"},
 "projects": {"steer": {"board": "/Users/…/steer/.tickets", "repos": ["/Users/…/steer", "/Users/…/atman"]},
              "retc":  {"board": "/Users/…/retc/.tickets", "repos": ["/Users/…/retc"]}},
 "operator": "Advitiya"}
```

`boards` keeps its current meaning and resolution order (T-959). `projects` is only the app's list. A `boards` entry with no `projects` entry is listed under the slug `basename(dirname(board))`, which is the same value `board_snapshot` already returns as `project`.

**Migration for existing boards.** A single-project board needs none. It is a project the moment the registry lists it. The app auto-lists the board it was started on, plus every distinct value in `boards`. `atm board-mark-primary` and `.primary` keep deciding which local board wins; the registry never overrides that.

The shared board is different. Today's working board `steer/.tickets` holds tickets for four repos. **Decision 1: split it into one board per project.** The migration is specified in §4.11. Until the split runs, phase 1 lists the shared board as one project with a read-only **repo lens** that filters nodes by `ticket.repo`. The lens doubles as a preview of the split.

**Create from the app (phase 3).** `POST /projects {slug, repo_root}` is equivalent to `cd <repo_root> && atm init` followed by `atm project add <slug> --board <repo_root>/.tickets`. It refuses:

- a `repo_root` that is not an existing git work tree;
- a slug already registered;
- a board that `_configured_shared_board` maps elsewhere (the T-959 shadow shape).

### 4.2 `seat@project` identity

- A **seat name stays board-local and unchanged** (`agents/<seat>.json`, `workforce.json` keys). `seat@project` is a display and addressing composition: `<seat>` + `@` + registry slug. No record is renamed.
- The same seat name on two boards is two seats. Each has its own inbox, limit record and runs. A harness session is bound to one board by `TICKETS_DIR` / the session identity (`_identity_path`), exactly as today.
- **Mentions are unaffected.** `_MENTION_RE` has the look-behind `(?<![A-Za-z0-9_@])`, so `coder@retc` in text never parses as a mention of `retc`. Cross-project addressing is not supported. A post goes to the selected project's board only.
- The operator shows as `<operator>@<project>` with an **operator** badge instead of a harness badge.

### 4.3 Operator identity (needed by chat, mail, DECIDE, pins)

- `operator` in the registry file (above), overridable per launch with `atm ui --operator NAME`. The name must have an `agents/<name>.json` on the project's board. The real board already has `agents/Advitiya.json` from past CLI posts. `atm join <name>` creates one otherwise.
- With no operator configured, the app is **read-only**: the composer is disabled with the copy *set an operator: `atm ui --operator <name>`*.
- The operator is never a seat in `workforce.json`, has no harness and is never woken.

### 4.4 Harness per post

- New posts: `post_message` stamps `harness` and `model` from the sender's workforce entry at post time, using `trajectories.agent_harness` (the same function that stamps trajectory events). The field is omitted when unknown. The operator's posts carry `sender_kind: "operator"`.
- Old posts: the badge shows the **current** workforce harness with a dotted outline and the tooltip *current harness; not recorded at post time*. With no workforce harness it shows **unknown**. Never `claude` by default. Phase 1 also fixes `_board_snapshot_body` to use `_seat_harness` semantics.
- Colour is keyed by harness value (`claude`, `codex`, `cursor`, `gemini`, `grok`, `remote`, `custom`, `unknown`), not by model guesswork.

### 4.5 DECIDE: a decision record

**Store.** An append-only `decisions.jsonl` per board, with the same atomic `O_APPEND` pattern as `messages.jsonl` and `trajectories.jsonl`. State is derived from events. No record is ever edited.

```json
{"id":"D-0007","ev":"ask","at":"…","by":"pm","ticket":"T-1110","question":"Which rule should participants follow?",
 "options":["A: …","B: …"],"blocks":true,"msg_id":"msg_…"}
{"id":"D-0007","ev":"rule","at":"…","by":"lead-seat","authority":"lead","choice":"B","notes":"…"}
{"id":"D-0007","ev":"seen","at":"…","by":"Advitiya"}
{"id":"D-0007","ev":"rule","at":"…","by":"Advitiya","authority":"owner","choice":"A","notes":"override: …"}
```

**States:**

- `open`: asked, no ruling.
- `ruled`: the lead ruled. The ticket is **unblocked immediately** (decision 3). Until the owner has marked it seen, "Needs you" shows it as **ruled by lead · not yet seen by you**, with Override and Seen buttons.
- `decided`: the owner ruled first.
- `overridden`: the owner ruled after the lead, with a different choice. **An override reopens the affected ticket** (§4.5, "Override").
- `withdrawn`: the asker withdrew it.

**Who can rule.** The owner (the operator identity) can always rule. The project's **lead** (the seat in `master.json.lead`, decision 2) can also rule, unless the ask was made with `--owner-only`. No other seat can rule. The asker can never rule their own DECIDE, so when the lead asked, only the owner can rule. This mirrors the accept gate's author ≠ reviewer rule (`review_verdict.refuse`).

**Lead rulings unblock immediately (decision 3).** A `rule` event by the lead removes the `decide_open` blocker at once. "Needs you" keeps the item as *ruled by lead · not yet seen by you* until the owner appends a `seen` event (the **Seen** button, `POST /decide/seen`). Opening the page never marks it seen, because a GET never writes.

**Override.** An owner `rule` event after a lead ruling, with a different choice, sets the state to `overridden` and **reopens the affected ticket**. This is the same write as `atm reopen <ticket>`, with a note `reopened: owner overrode D-0007 (<lead choice> → <owner choice>)`:
- a ticket in `claimed` or `review` goes back to `open`, with its owner kept as `reserved_for` so the same seat picks it up;
- a `done` ticket reopens, and any accept on it is superseded by the reopen (the existing reopen semantics, not a new rule).

Successors that already started on the strength of the lead's ruling are not reopened automatically. They get a `decide_overridden` chip naming D-0007, and the lead decides what to redo. An owner ruling that agrees with the lead is recorded as `seen`, not `overridden`.

**Link to a ticket.** `ticket` is required. A DECIDE always blocks or informs some work. With `blocks: true` the ticket gets the typed blocker `decide_open` (§3.2) while the state is `open`. Dispatch refuses it (`cmd_dispatch` gains the check) the same way it refuses HOLD. Each event also posts a normal message (`kind: "decide"`, `re: <ticket>`, `to: <owner or lead>`) so delivery, receipts and wake work unchanged.

**CLI:**

- `atm decide ask T-1110 "question" --option "A: …" --option "B: …" [--owner-only] [--no-block]`
- `atm decide rule D-0007 --choice B --notes "…"`
- `atm decide withdraw D-0007`
- `atm decide list [--open]`

**Phase 1 (no record yet).** The "Needs you" queue lists, labelled **unstructured**:

- messages addressed to the operator with no later operator post in that thread;
- messages whose text starts with `DECIDE` or contains `@owner` / `@<operator>`;
- `stuck:` messages older than an hour;
- escalated automated nodes.

Each item shows *asked* only. Phase 1 never shows *ruled*. This follows the T-944 rule that prose is never a verdict.

### 4.6 Mail / unread per operator, and pins

- **Unread** is computed read-only. The logic is `_addressed(m, operator)`, `_seen_since(agents/<operator>.json)`, `_is_unread` and `_visible_after_join`, the same as `atm inbox`, but the GET never writes `inbox_seen`. The badge is a count of that set.
- **Mark read** is a write: `POST /inbox/seen {through_msg_id}`, equivalent to `atm inbox` as the operator (which advances `inbox_seen`). Opening Mail does not mark read by itself. Receipt semantics stay honest, because another seat's delivery badge flips to *inbox read* only when the operator really read it.
- **Pins:** `pins.json` per board, `{"operator": [{"kind":"msg"|"ticket"|"decision","id":"…","at":"…"}]}`, operator-only. `POST /pins` / `DELETE`-as-`POST /pins/remove`. Equivalent CLI: `atm pin <id>` / `atm unpin <id>`. A pin is presentation only. It never changes delivery, wake or ticket state.

### 4.7 Action cards: only from records

A card is rendered only from a board record. An agent's words are not receipts. If the lead writes "created T-1110" and no record exists, no card appears, and `T-1110` is not linked unless the ticket exists.

| Card | Record that backs it | Exists today? |
|---|---|---|
| created T-x | new trajectory kind `create` {agent, ticket, via: plan / create} written by `create()` | **No.** Add `create` to `TRAJ_KINDS` (both copies; `test_trajectories_entrypoints.py` guards drift). |
| dispatched T-x → seat | new trajectory kind `dispatch` written by `cmd_dispatch` / `cmd_assign`; today only a `dispatch: reserved for <seat>` note | Partial (note, no actor event) |
| claimed / submitted / done / blocked / reopened / merged | trajectory `claim` / `review` / `done` / `block` / `reopen` / `merge` | Yes |
| accepted / rejected at <sha> | `review_events` | Yes |
| steered seat (receipt) | ticket `steers[]` | Yes |
| asked / ruled D-x | `decisions.jsonl` | 2A |
| eval run started / scored | `eval_runs.jsonl` | 2B |

Cards interleave with chat by `at` and are attributed to the recorded actor. Every card links to its plan node or drill-down.

### 4.8 Search

`GET /search.json?project=&q=&scope=thread|project&cursor=` does a case-insensitive substring match (no user-supplied regex) over:

- message `text`, `from`, `re` (live file; archives with `&all=1`);
- ticket `id`, `title`, `notes[].text`.

Results are bounded (`limit ≤ 50`) and paged with a cursor. The "^ n of N v" pager steps through hits in the open thread and scrolls them into view. The pure read path is audit-hook tested. No index file is written.

### 4.9 Named rooms (optional, phase 5)

If built: `rooms.json` per board (`{slug, title, created_by}`), plus an optional `room` field on a message set by `post_message(room=…)`. **A room is a view filter, never a delivery mechanism.** Addressing (`to`, mentions, broadcast) and wake stay exactly as today, so a room cannot silently wake or silence anyone. Default rooms are the project broadcast (today's Board thread) and the lead conversation.

### 4.10 Datasets and evals (optimization work)

These are product requirements learned from the team's own evaluation runs (the stage-2 detector bake-off and the binary-holdout rerun, documented in the product repo under `docs/evals/`). Each rule below was learned by getting it wrong or nearly wrong.

**What lives where.**

| On the board (shareable, aggregates only) | In custody (`~/.atman-custody/<project>/…`, outside every repo) |
|---|---|
| `datasets/DS-*.json`: id, `role` (`dev` / `holdout`), provenance {how built, by whom, from what, `derived_from_holdout: false` asserted and checked}, slices [{name, n}], labels {who, blind, agreement_rate, adjudicated}, `version`, `content_sha256`, `custody_ref` {path, digest}. No case ids, no text, no labels. | Case files; gold labels for holdouts; the **generator and seed** of a synthetic set (a committed generator once leaked a holdout's answers through git history) |
| `evals/EV-*.json`: pre-registration {metrics, thresholds, adoption rule text, required n per slice, `frozen_commit`, `config_sha256`, dev and holdout dataset ids, blind-request allow-list version} | Raw per-case predictions |
| `eval_runs.jsonl`: {run id, eval, step `predict` / `score`, dataset id + role, `config_sha256`, `frozen_commit`, `reviewed_by` (accepting seat), started, ended, `started_by`, `authority` (`owner` / `lead`), executed_by, `run_log_digest`, `custody_receipt_digest`, `guard`: {allow_list_version, accepted_fields, refused: n}} | Run logs; custody receipts |
| `eval_results.jsonl`: {run id, metric, value, ci_low, ci_high, ci_method, n, `status` DIRECTIONAL / DEFINITIVE, dataset role, tokens_per_case / p95_ms / cost (or `null`), `rule_applied` `as_written` / `adapted`, `departures[]`} | nothing extra |

The board **references** custody by path plus digest only. The app never opens a custody path. It shows *custody receipt present / missing*. A run whose `custody_receipt_digest` is empty shows **missing custody receipt: blindness rests on code path only**, which is what one real run had to admit in prose.

**The rules as enforced behaviour.**

1. **Role and provenance.** A dataset without `role` cannot be bound to an eval. A dev set whose provenance names the holdout is refused at `atm dataset add`.
2. **Blindness is enforced by an allow-list, not trusted.** `atm eval predict` runs the eval's predict command under a guard that:
   - passes only the fields named in the eval's blind-request allow-list (a deny-list of label words was bypassed with synonyms);
   - refuses to open custody outside its own output dir.

   Only `atm eval score` reads gold. Both steps append to `eval_runs.jsonl` with the run-log digest and the custody receipt digest. A missing receipt is recorded as missing.
3. **Pre-registration is frozen and reviewed before the scored run.** `atm eval freeze EV-x` records `frozen_commit` and `config_sha256`. A frozen config counts as **reviewed** only when the eval's ticket carries a structured `review_events` accept whose SHA equals `frozen_commit`. The accept must come from a seat other than the author, which is the existing gate, reused. Decision 4 allows scoring only from a frozen, reviewed config. `score` refuses an eval that is not frozen, or whose current config hash differs. Each result records `rule_applied` and every departure. The app shows **as written** or **adapted: N departures** with the list. It never shows "pre-registered" beside an adapted rule.
4. **No tuning on the holdout.** Tuning runs are refused on `role: holdout`. **Who may start a scored run** (decision 4): the operator or the project's lead, never any other seat. Each scored run records `started_by` and `authority`, and the app shows them on the run. A holdout is scored **once per frozen config**: a second `score` on the same holdout dataset version with the same `config_sha256` is refused. A new config needs a new freeze at a new commit. The app shows *holdout exposures: k* per dataset, so re-reading the same holdout is visible. The **dev-vs-holdout gap per metric** is a first-class column. A real run moved from 0 blocks on dev to 22 on the holdout, from degenerate threshold bands (low == high) fitted on a 219-case dev set. The app also flags configs whose thresholds contain `low == high`.
5. **Directional vs definitive.** `status` is DEFINITIVE only when each slice's n ≥ the pre-registered required n. Otherwise it is DIRECTIONAL, and the badge sits on every figure. Every figure shows its CI and the n it rests on. A result without a CI renders "CI not reported" and is forced DIRECTIONAL. The board and the app carry aggregates only; a result row containing a case id is rejected at write.
6. **Cost and latency are metrics.** Tokens per case (on the subset actually asked) and p95 latency are result rows. Cost `null` renders **unknown**, never a number. A missing price sheet means the cost condition of the rule counts as not holding.
7. **Optimization task.** A ticket with `eval_gate: {eval, config_sha256, holdout_dataset}` gets a second gate. `atm accept` refuses unless a scored holdout result exists at exactly that config and the adoption rule holds as written. An adapted rule needs an operator ruling (a DECIDE) that names the departures. This is in addition to the normal SHA-bound accept by a different seat. The code accept alone never releases successors of an optimization task.

**Drill-down.**

- Dataset → slices (n each), provenance, labels (who / blind / agreement), version and hash, custody present / missing, holdout exposures.
- Eval → frozen commit and config hash, metrics and thresholds, rule text, runs list.
- Run → per-metric value with CI and n, DIRECTIONAL / DEFINITIVE, dev-vs-holdout gap, tokens per case, p95, cost or unknown, departures, guard receipt, custody receipt.

**Phase placement.** Phase 2B. A read-only view of *existing* eval artifacts does not fit phase 1. The existing artifacts are prose reports and scripts in another repo, and turning prose into rows would fabricate structure (the same reason prose is never a verdict). Phase 2B ships `atm eval import` so those runs can be entered by hand as records, with `rule_applied: adapted` and their stated departures.

### 4.11 Splitting the shared board (decision 1)

**What is on the real board today.** Measured read-only on `steer/.tickets`, 2026-09-19. The board is live, so the counts move.

- **Tickets: 1,025.**
  - By `repo`: atman 407, steer 281, the research repo 34, the tickets repo 8, and 1 whose `repo` is the board's own `.git`.
  - 294 have no `repo`: 171 done, 82 open, 22 blocked, 19 claimed. For 184 of them, `branch`, `artifact_dir` and title give no hint of a repo.
- **Dependency edges that cross projects: 264** (unattributed tickets counted as their own bucket).
  - 164 have a child that is not done.
  - Only **15** join two attributed repos with a live child. All 15 are steer ↔ atman, and in all 15 the parent is done. Examples: T-809 ← T-807 and T-829; T-536 ← T-481 and T-500.
  - The other 149 involve an unattributed ticket, so the real number is only known after attribution. The dry-run reports it.
- **Epics span repos:** E-010 and E-011 (the two largest), and E-012 through E-018.
- **Messages (live and archive):**
  - by `re`: atman 6,113, steer 3,078, unattributed 1,820, research 417, tickets 110, unknown id 201;
  - 6,973 have no `re`.
- **Trajectory events:** atman 11,033, steer 5,506, unattributed 3,198, research 846, no ticket 6,134.
- **Structured accepts:** `review_events` on 14 tickets, all atman.
- **Seats:** 325 agent records and 250 workforce entries. 184 seats own attributed tickets, and **38 of them work in more than one project**.

**Principles.**

- **Copy, never move.** The shared board is not edited, except for one marker file. Nothing is deleted.
- **Ticket ids are kept.** There is no renumbering, so `re` fields, notes, commit subjects (`T-809: …`) and PR titles stay valid. Each new board's id allocator (`_alloc`) is seeded above the shared board's highest id, so new ids never collide across projects. A reference into another project is written `project:T-id`.
- **Records are copied byte for byte.** That covers the ticket JSON, including `review_events`, `review_head`, `steers`, notes and `owner_lease`. It also covers message lines with their `id`. A legacy record without an id keeps its content-derived `_msg_id`, because its content is identical. Receipts keyed by message id therefore stay valid.
- **Append-only logs are partitioned, never rewritten.** Each line lands in one or more target files unchanged. The manifest records where each line went.

**The tool: `atm project split`.** It has four steps.

1. **Propose.** `atm project split --propose > split-plan.json` is read-only.
   - A ticket goes to the project named by its `repo`.
   - A ticket with no `repo` is listed as `unassigned`. Its branch, artifact dir and epic majority are shown as **hints only**; nothing is auto-attributed.
   - The plan also proposes seat homes and each project's master/CoS/objective (§ below), and lists every cross-project edge.
2. **Edit.** The operator edits the plan.
   - Every non-done unassigned ticket must be given a project.
   - A done unassigned ticket may be given one, or left in `archive`. `archive` means it stays readable in the frozen shared board, which the registry keeps as a read-only project `shared-archive`.
3. **Dry-run.** `atm project split --plan split-plan.json --dry-run` writes nothing (audit-hook tested). It prints:
   - per-project counts of tickets, messages, trajectory events and agent records;
   - cross-project edges by kind (below);
   - every refusal;
   - the exact manifest it would write.
4. **Apply.** `atm project split --plan split-plan.json --apply`.
   - It refuses while any `agents/*.run` receipt is active, a watcher is alive, or `merge.lock` is held. The operator stops seats first with `atm spawn --stop`.
   - It builds each new board in a temp dir and verifies line counts and sha256 digests against the dry-run.
   - It then renames each board into place, writes `split-manifest.json` into every new board, and writes the `.split` marker into the shared board.
   - Last, it updates `~/.config/atman/board.json`: each repo now maps to its project board, and the new `projects` entries are added. The previous values are kept in the manifest.

**What goes where.**

| Shared-board file | Where it goes | Why nothing is lost |
|---|---|---|
| `T-*.json` | The ticket's project | Unchanged file, so `review_events` (accepts), `review_head`, `steers`, `owner_lease` and notes travel with it. |
| `epics/`, `sprints/` | Every project that has a member ticket. Each copy counts only its local members and carries `split_from`. | Epic progress stays true per project. |
| `messages.jsonl`, `messages.<date>.jsonl` | A message with `re` goes to that ticket's project. A directed message with no `re` goes to every project where its sender or a recipient is homed. A broadcast with no `re` goes to the sender's home projects. A message whose sender is homed nowhere goes to the archive only. Dated archive names are kept. | A message copied to two projects has the same id in both, so receipts cannot fork. |
| `trajectories.jsonl` | An event with a ticket goes to that ticket's project. A ticketless event goes to the agent's home projects, or to the archive if the agent has none. | Turns and cost metrics are per ticket, so a copied ticketless event never double-counts a ticket. |
| `agents/<seat>.json` | Every home project of the seat, unchanged (`inbox_seen`, seen ids, `wake_delivery`, limit, `auth_check`) | No message is redelivered, and no receipt changes its label. |
| `agents/*.run` | Not copied. There are no active runs, by precondition. | Run history is in trajectories. |
| `workforce.json`, `roles.json` | Entries for the seats homed in the project | — |
| `master.json`, `objective.json`, `MASTER.md` | As the plan names per project. `lead` is left **unset** (decision 2: the user picks). `MASTER.md` gets a first line "forked from the shared board at <time>". | The decision log is preserved in every project that continues it. |
| `briefs/`, `CONTEXT.md`, `aliases.json`, `provider_usage.json` | Copied to every project. An alias whose holder is not homed there is dropped. | Usage readings keep their `checked_at`, so their age stays honest. |
| `coordination/`, `merge.json`, `retired.json` | Copied where their references resolve. Retired-seat forwards are kept. | — |
| `.identities/` | Not copied. A session re-binds with `atm join` on its new board. | Prevents a session silently answering as a seat on the wrong board. |

**Re-homing seats.**

- **Home rule.** A seat's home set is every project where it:
  - owns, or is `reserved_for`, a non-done ticket;
  - is named master or CoS in the plan;
  - owned a ticket in the last 30 days.

  A seat with no activity in 30 days stays in the archive only. The plan can override any of this.
- **Seats in several projects.** The 38 multi-project seats get one record per project: same name, separate inbox, shown as `seat@atman` and `seat@steer`.
- **Restarting sessions.** After apply, the tool prints the exact restart line for each home (`TICKETS_DIR=<board> atm spawn <seat> --persist …`). A session still pointed at the shared board is **refused on write** by the `.split` marker, with a message naming the seat's new board(s). This is the T-959 refusal pattern (`_shadow_board_refusal`), reused.
- **The existing local `atman/.tickets`.** It has 3 tickets and 15 messages. The dry-run reports it as a shadow. The operator archives it with the existing `atm board-archive-shadow` before the atman project board is created there.

**Cross-project dependencies.** The dry-run counts them after attribution. Today there are 15 attributed edges with a live child, and every parent is done. Handling depends on the parent's state at split time:

- **Parent released** (structured accept or recorded override, per `dep_released`). The child keeps the dependency as a snapshot, `external_deps: [{"project":"steer","id":"T-807","released":{"kind":"accept","sha":"…","at":"…"}}]`, and the id leaves `deps`. The child's gate reads the snapshot, never the other board, so boards stay independent.
- **Parent done but not accepted.** The snapshot is recorded as `released:false, reason:"done-unaccepted"`. The child keeps a `dep_unaccepted` chip naming `steer:T-807`. When the parent is accepted on its own board, `atm project refresh-external <child>` updates the snapshot. That command is an explicit write that reads the other board once. **The migration never turns unaccepted into accepted.**
- **Parent not done.** Refused by default: the plan must put both tickets in the same project. `--allow-pending-external` instead records a `released:false` snapshot, handled the same way.

**Reversible.**

- The shared board is untouched apart from the `.split` marker.
- Every new board carries `split-manifest.json`: the plan digest, the line count and sha256 of each copied file, and the registry before and after.
- `atm project split --undo <manifest> [--dry-run|--apply]` has two cases:
  - **No new board has records beyond its manifest:** undo removes `.split`, restores the registry, and moves the new boards aside to `.tickets.split-undone-<ts>` (never deletes them).
  - **Post-split writes exist:** `--undo --merge-back` appends the new boards' appended lines (messages, trajectories, decisions) back into the shared board, deduplicated by id, and copies back tickets whose `updated` is newer. The dry-run lists every conflict and refuses on any. A conflict should be impossible while the shared board is frozen, but it is checked.

---

## 5. The persistent coordinator (the conversation)

### 5.1 Which agent, how it persists, where memory lives

- **Who (decision 2: the user's choice).** The lead is a **per-project setting the user picks**: `master.json.lead = "<seat>"`. It can be any registered seat on that project's board. There is no hard-coded default role. If `lead` is unset, the conversation pane shows a picker ("Pick who you talk to on this project") instead of a thread, and the app never falls back to master or CoS on its own.
  - The picker lists each registered seat with its harness and a capability line from `steer.STEERABLE_PROVIDERS` / `steer.UNSTEERABLE`: **takes mid-run messages** (today: `claude` with a live messaging socket) or **answers on its next turn** (every other harness, for example `codex`, `cursor`). The line comes from the same table `atm steer` uses, so it cannot drift.
  - Once chosen, the title pill repeats that line. If the lead only answers on its next turn, the composer says so under the Send button: *"<seat> answers on its next turn; it cannot be interrupted mid-run."*
  - Set with `atm lead set <seat>` or `POST /lead {seat}` (operator only). Changing the lead is recorded with `atm master log`.
- **How it persists.** Through re-woken turns, not one immortal run. The master and CoS already default to `lifecycle: persistent`, `wake_mode: continuous` (`lifecycle_of`, `wake_mode_of`). A user-picked lead gets the same defaults (`lifecycle_of` / `wake_mode_of` treat `master.json.lead` like `owner` and `cos`), unless `workforce.json` sets them explicitly. They run under `atm spawn --persist` (`spawn_watch_max_runs(persist=True)` loops). The master prompt deliberately bounds each run to "ONE concrete outcome, then stop". v2 keeps that. A long-lived model session is neither assumed nor required.
- **Where memory lives** (board only, no new store):
  - `objective.json`;
  - `MASTER.md` (the decision log written by `atm master log`);
  - `.tickets/briefs/` (standing context, `atm brief`);
  - the operator ↔ lead thread in `messages.jsonl`;
  - `decisions.jsonl`;
  - trajectories for "what did I do last".

  Run transcripts belong to the harness. They are not product memory and the app does not read them.
- **Gap (2A): conversation carry-over.** Today a woken lead sees only *unread* mail (`atm inbox`) plus `MASTER.md`, so the last hour of conversation is gone once read. Add a **conversation digest** to the lead's wake prompt: the last N operator ↔ lead messages and open DECIDEs, rendered from the board by the same prompt builder that renders the seat brief (`seat_brief`, `prompt_text`). It is bounded (N ≤ 20, ≤ 4 KB) and is text from the board, not a memory product.

### 5.2 Round trip

1. The operator types. `POST /msg {text, re?}` → `post_message(board, <operator>, text, to=<lead>)`. The server sets `from`, not the page.
2. **Wake or inject.** `cmd_msg` today wakes via `sa.wake_seat` and then `_poke_persist_watch`. **Gap:** the `/msg` route calls `post_message` only. It does not run the wake half, so a UI post waits for the next poll. Phase 1 factors the wake loop of `cmd_msg` into `deliver_wakes(board, rec)` and calls it from both paths. If the lead is **mid-run on a steerable harness** (`claude` with a live socket), the composer offers **Ask mid-run**, which is `atm steer <lead> --ask "…"`. `cmd_steer` requires a ticket today ("holds no ticket; pass --ticket"). **Gap (2A):** let a steer to the lead record on the objective (an `objective.steers[]` list) when no ticket is held.
3. **What the operator sees while it works:**
   - the post's receipt (posted → inbox read → wake confirmed / queued-offline / limited);
   - the lead's liveness word;
   - the running run's elapsed time (agent map);
   - "last output N ago" (`_steer_seat_row.last_output_at`).

   There is no token streaming. Streaming would need transcript reads, which are harness-private and out of scope.
4. **Reply.** The lead answers with `atm msg --to <operator>`. It appears in the thread on the next poll (the page polls `/board.json` today; the thread route is polled the same way). A `STEER-REPLY <id>` ticket note is also shown in the thread, linked to its steer.

### 5.3 When the lead cannot answer

The strip and the composer say it plainly and never hang. The post still lands on the board (mail queues). Only the promise changes.

| Condition | Source | Copy |
|---|---|---|
| limited | `_active_seat_limit` → `limit_until`; provider usage age | "Lead is limited until 17:40 (usage checked 12m ago). Your message is queued." |
| dead / stalled / no adapter | `agent_liveness.state`, `adapter_state` `offline` / `queued-offline` / `failed` | "No live session for the lead. Message queued; it runs when a watcher is up: `atm spawn <lead> --persist`." |
| logged out | `auth_surface.state`, `recovery.cmd` | "Lead's harness is logged out. Recovery (run on the enrolled host): `<cmd>`" + **Recheck auth** (`POST /auth-reconnect`, existing) |
| harness at quota | `provider_usage` compact reading `remaining` 0 / observed limit | "Provider quota exhausted (checked N ago). Resets <reset_label>." Unknown stays "quota unknown". |

### 5.4 Multiple projects

Each project has its own lead (its own `master.json.lead`, possibly unset). The project switcher switches the conversation, the plan and the fleet together. Posting always targets the selected project's board. Unread badges per project come from phase 3.

### 5.5 Safety of the lead's authority

- The lead acts only through `atm` commands. The app has no route that performs a lead action on its behalf.
- It cannot self-accept (`review_verdict.refuse`: author ≠ reviewer). It cannot bypass the gate: successors release only on a structured accept (`dep_released`), and `atm done` without accept stays *done, not accepted*.
- The lead's authority is shown in the title pill: *lead* plus any board role it also holds (`master` / `CoS`), and what those roles may do. The words come from the existing master and CoS prompts (route, dispatch, brief, spawn, merge for CoS). Being lead adds exactly two powers: ruling a DECIDE and starting a scored eval run (§4.10). Every lead action shows as a record-backed card (§4.7).

---

## 6. Write paths the app adds

**Common rules for every write route:**

- `POST` only.
- `Content-Type: application/json`. A non-simple request forces a CORS preflight the server never answers, which blocks cross-site form posts.
- `Origin` must equal `Host`, and **`Host` must be `127.0.0.1:<port>` or `localhost:<port>`**. This closes DNS rebinding, which today's `_ui_msg_origin_ok` allows because it only compares the two headers.
- A per-launch random token in the page, required as `X-Atman-Token`.
- The body ≤ 64 KiB and passes `_ui_payload_has_secrets`.
- **`from` is never taken from the payload.** The server uses the configured operator.
- `atm ui` refuses a non-loopback `--host` (localhost-only becomes enforced, not a default).
- Each post records `via: "ui-operator"`, `session: <launch token hash>`, so provenance names the UI instead of the server's shell environment.

| Route | Equivalent atm command | Phase | Why it cannot bypass the gate or impersonate a seat |
|---|---|---|---|
| `POST /msg {text, to?, re?, kind: message\|task}` (hardened) | `atm msg --owner <operator> "text" [--to] [--re] [--task]` | 1 | `from` = operator, server-side. The payload `from` is rejected if present and different. A message is never a verdict (T-944). |
| `POST /auth-reconnect {agent}` (existing) | recheck only; never login, never a model | — | Unchanged. |
| `POST /steer {seat, text, ask: bool, ticket?}` | `atm steer <seat> [--ask] "text" --ticket` as operator | 2A | Records on the ticket (or objective) as a steer by the operator. Scope unchanged. `steer.harness_refuse_reason` still refuses non-steerable harnesses. |
| `POST /decide/rule {id, choice, notes}` | `atm decide rule` as operator | 2A | Rules a decision only. It never writes `review_events` and never releases successors except by removing the `decide_open` blocker. The asker ≠ ruler rule is checked. The one status change is the one decision 3 requires: an owner override of a lead ruling reopens the affected ticket, through the same code as `atm reopen`. |
| `POST /decide/seen {id}` | `atm decide seen D-x` as operator | 2A | Appends a `seen` event only. It changes no ticket. |
| `POST /lead {seat}` | `atm lead set <seat>` | 1 | Operator only. The seat must be registered on this board. It writes `master.json.lead` and a `master log` line. It grants DECIDE ruling and scored-run start, nothing else. |
| `POST /decide/ask {ticket, question, options[]}` | `atm decide ask` as operator | 2A | Same record as the CLI. |
| `POST /eval/score-request {eval, dataset}` | posts a `task` to the eval's owner seat to run `atm eval score --requested-by <operator>` | 2B | The server never runs the scorer. Decision 4: only the **operator** or the project's **lead** may start a scored run (from the app, the operator; from the CLI, `atm eval score` checks the caller is the operator or `master.json.lead`). The request is refused if the eval is not frozen, the frozen config is not reviewed (no structured accept at `frozen_commit`), the config hash drifted, or this holdout version was already scored at this config. `atm eval score` re-checks everything and records `started_by` and `authority` (`owner` / `lead`) on the run. |
| `POST /inbox/seen {through}` | `atm inbox --owner <operator>` (marks read) | 3 | Moves only the operator's own watermark. |
| `POST /pins`, `POST /pins/remove` | `atm pin` / `atm unpin` | 3 | Presentation only. |
| `POST /projects {slug, repo_root}` | `atm init` in repo_root + `atm project add` | 3 | Creates an empty board. The T-959 refusals apply. Creating a board creates no seats. |

**Not added, in any phase:** accept, reject, merge, done, claim, assign, dispatch, spawn and objective set. Those stay CLI-only for the seats and the lead whose role allows them. The app shows the copyable command.

---

## 7. Phased build plan

Every phase ships alone. Every phase keeps the audit-hook read-path test green for every GET route. That test (`tests/test_t1072_agent_map.py::test_read_only_no_subprocess_no_writes`) fails on `subprocess.Popen`, `os.system`, spawn, exec, fork, or any write-mode `open` during the request.

### Phase 1: the felt difference, over existing data only

Scope:

1. **New shell.** Sidebar (project switcher, Lead, Plan, Needs you, Fleet, Runs, Hub stub, clock). Conversation pane left, execution plan centre, drill-down right (a bottom sheet under 900px, full-screen at 390px). The `Objective · Team · Work · Intervene` content moves into this shell. No surface is deleted.
2. **Project switcher (read-only)** over the registry and `boards` values. `/projects.json`. `/board.json?project=`. The shared-board repo lens stays until the split (Phase 1S) runs.
2b. **Lead picker.** `POST /lead`. When no lead is set, the picker lists seats with their "takes mid-run messages / answers on its next turn" line. No default is ever chosen.
3. **Lead conversation** over existing messages. `/thread.json` pages back beyond 40. Each post shows `seat@project`, a harness badge (current workforce harness or `unknown`, dotted "not recorded at post time"), a local timestamp, text with ticket links, delivery receipts and copy. The lead status strip covers liveness, run elapsed, last output, limit, auth and usage with age. The composer posts **as the operator only** and runs the same wake path as `atm msg` (`deliver_wakes`).
4. **Live execution plan and drill-down.** The existing Work payload and layouts, plus client-side blocker chips (`dep_unaccepted` vs `dep_open` from `work.nodes[].review.verified`, `seat_limited` / `seat_offline` / `auth` from `agents[]`). A running pulse from the agent map. `/ticket/<id>.json` provides runs (elapsed, tokens or unknown, role, verdict), usage with age, review head and structured verdicts, handoff, messages `re=<id>`, steers, and commit / branch / PR plus a copyable diff command. Back returns to the plan with selection kept.
5. **Needs-you queue (read-only, unstructured)** per §4.5, with *asked* as the only state. Plus two hardening fixes: harness default `claude` → unknown, and loopback Host check + launch token + non-loopback `--host` refusal.

Acceptance tests (fixture boards via `ATMAN_BOARD_CONFIG` and `TICKETS_DIR`; never the real board):

- `test_app_v2_read_routes_no_subprocess_no_writes`: `/board.json?project=`, `/projects.json`, `/thread.json`, `/ticket/<id>.json` under the audit hook.
- `test_project_switch_reads_the_registered_board`: two fixture boards. Switching changes the objective, plan nodes, fleet and lead. A post lands only in the selected board's `messages.jsonl`.
- `test_registry_missing_or_bad_lists_only_the_started_board`: bad JSON gives one project, never a crash, never `board_dir()` exit.
- `test_no_lead_means_picker_not_guess`: a board with no `master.json.lead` renders the picker. `/thread.json` returns no thread. No master or CoS fallback happens.
- `test_lead_capability_line_matches_steer_table`: a `claude` seat with a socket shows "takes mid-run messages". A `codex` seat shows "answers on its next turn", and the composer shows the matching note.
- `test_composer_posts_as_operator_only`: payload `from: <seat>` returns 400. The record's `from` is the operator and `via` is `ui-operator`. With no operator configured the composer is disabled and `POST /msg` returns 400.
- `test_ui_post_wakes_like_cli`: a UI post to a `continuous` lead records the same `wake_delivery` label as `atm msg`.
- `test_host_header_must_be_loopback` and `test_write_requires_launch_token`: a DNS-rebinding-shaped request (`Host: evil.test:8765`, matching Origin) is refused. `atm ui --host 0.0.0.0` exits non-zero.
- `test_harness_badge_never_defaults_to_claude`: a seat with no workforce harness renders `unknown` in `agents[]` and in the post badge.
- `test_thread_pages_past_the_snapshot_window`: 500 messages; the operator ↔ lead thread returns the older ones via `before=`.
- `test_blocker_chip_distinguishes_unaccepted_dep`: dep `done` without accept gives `dep_unaccepted`. Dep open gives `dep_open`. Owner limited gives `seat_limited` with reset time.
- `test_drilldown_tokens_unknown_not_zero`: a run without tokens shows `unknown`. Usage without `checked_at` shows age unknown, never a number.
- `test_done_without_accept_is_never_accepted_in_v2_shell`: node, card and drill-down all say *done, not accepted*.
- `test_receipts_never_say_ack`: no rendered string in the conversation contains "acknowledged" / "ACK" for a receipt.
- `test_needs_you_is_asked_only`: prose "DECIDE: … ruling: A" still shows *asked (unstructured)*, never *ruled*.
- `test_seat_at_project_is_not_a_mention`: posting `coder@retc` creates no `mentions` entry.
- Browser check at 1440px and 390px: the plan, the conversation and the drill-down open and close. No horizontal body scroll.

### Phase 1S: split the shared board (decision 1)

Scope:
- `atm project split --propose | --dry-run | --apply | --undo [--merge-back]`
- `atm project refresh-external`
- `atm project add | list`
- The `.split` write refusal
- `external_deps` in the release gate

Phase 1 works before and after the split: before it, through the repo lens; after it, as one board per project. It ships right after phase 1. The split runs only when the operator applies it.

Acceptance tests (fixture boards built to mirror the real shapes: multi-repo tickets, unattributed tickets, cross-repo edges, multi-project seats, archives, accepts):
- `test_split_dry_run_writes_nothing`: audit hook. The manifest printed by the dry-run equals the one apply writes.
- `test_split_preserves_every_record`: every ticket file is byte-identical in its project. Each message and trajectory line appears in at least one target or the archive. The union of targets plus the archive equals the source, with no line lost.
- `test_split_keeps_accepts_and_receipts`: `review_events` are unchanged. `_message_delivery` labels for a sample message are identical before and after, in every project that holds it.
- `test_split_refuses_unassigned_live_ticket` and `test_split_refuses_with_active_run`.
- `test_cross_project_dep_snapshot`: a released parent unblocks the child. A done-unaccepted parent keeps `dep_unaccepted`. An open parent is refused without the flag. `refresh-external` flips the snapshot only after a real accept.
- `test_multi_project_seat_gets_one_record_per_home`, with `inbox_seen` carried so nothing is redelivered.
- `test_frozen_shared_board_refuses_writes_with_new_board_named`.
- `test_undo_restores_registry_and_sets_new_boards_aside`, and `test_undo_merge_back_dedups_by_id`.
- `test_new_ids_do_not_collide_across_projects`.

### Phase 2A: plan plumbing, DECIDE, lead continuity

Scope:

- Server-side `blockers_of` including `decide_open`.
- `objective_id` on new tickets.
- `review_stat` diffstat at `atm review`.
- `create` / `dispatch` trajectory kinds, and record-backed action cards in the chat.
- `decisions.jsonl` + `atm decide` + `/decisions.json` + ruling from the app (`POST /decide/rule`, `/decide/ask`).
- Conversation digest in the lead's wake prompt.
- Steer-without-ticket to the lead (`objective.steers[]`) + `POST /steer`.
- `harness` / `model` / `sender_kind` stamped on new posts.

Acceptance tests:

- `test_card_only_from_record`: the lead says "created T-9" with no ticket, so no card and no link. `atm create` by the lead produces a `create` card attributed to the lead.
- `test_decide_states`: ask gives open. A lead ruling gives ruled and **removes `decide_open` at once**. The asker ruling is refused. An `--owner-only` DECIDE refuses the lead. A non-lead seat is refused.
- `test_lead_ruling_shows_unseen_until_seen`: "Needs you" shows *ruled by lead · not yet seen by you*. GET routes never append `seen`. `POST /decide/seen` clears it.
- `test_owner_override_reopens_ticket`: override with a different choice gives `overridden`. A ticket in review goes back to `open`, reserved for its owner, with the reopen note. A started successor gets a `decide_overridden` chip and is not reopened. An owner ruling that agrees with the lead is recorded as `seen`, not `overridden`.
- `test_decide_blocks_dispatch_until_ruled`: the chip shows `decide_open`, `atm dispatch` refuses, and a ruling removes the chip.
- `test_ruling_never_touches_review_events`: `review_events` and ticket status are unchanged after `/decide/rule`.
- `test_diffstat_recorded_at_review_and_marked_stale_after_head_moves`.
- `test_digest_is_board_text_bounded`: the lead prompt includes ≤ 20 thread messages and ≤ 4 KB. Nothing is read from outside the board.
- `test_trajectory_kinds_match_entrypoints`: the existing drift test passes with `create` / `dispatch`.
- `test_steer_to_lead_without_ticket_records_on_objective`, and a `codex` lead gets the refusal copy.

### Phase 2B: datasets and evals

Scope:

- `datasets/`, `evals/`, `eval_runs.jsonl`, `eval_results.jsonl`.
- `atm dataset add|show`, `atm eval add|freeze|predict|score|import|show`.
- The blind predict guard (allow-list).
- The optimization-task `eval_gate` in `atm accept`.
- The Evals section and eval drill-down.
- `POST /eval/score-request`.

Acceptance tests:

- `test_predict_guard_is_allow_list`: a request carrying a synonym field for a label (not on the allow-list) is refused. Opening custody outside the run's output dir raises.
- `test_score_refuses_unfrozen_or_drifted_config`, and `test_score_refuses_unreviewed_freeze` (no structured accept at `frozen_commit`).
- `test_scored_run_only_operator_or_lead`: any other seat is refused. The run records `started_by` and `authority`.
- `test_second_holdout_score_same_config_refused`, and the same holdout under a new freeze is allowed with exposures = 2 shown.
- `test_tuning_on_holdout_refused`.
- `test_dev_holdout_gap_rendered_per_metric`, plus the degenerate-band flag for `low == high`.
- `test_status_directional_below_required_n`, and a missing CI forces DIRECTIONAL.
- `test_cost_unknown_renders_unknown`.
- `test_result_row_with_case_id_rejected`: aggregates only.
- `test_adapted_rule_lists_departures_never_says_preregistered`.
- `test_optimization_accept_requires_eval_gate`: a code accept alone does not release successors.
- `test_missing_custody_receipt_is_shown`.
- `test_app_never_opens_custody_paths` (audit hook on `/eval/*.json`).

### Phase 3: mail, pins, search, create project

Scope:

- Operator unread per project (read-only count) and `POST /inbox/seen`.
- Pins.
- `/search.json` + pager.
- `POST /projects` + `atm project add|list`.

Acceptance tests:

- `test_unread_count_matches_atm_inbox_without_writing`.
- `test_mark_read_moves_only_operator_watermark`: other seats' receipts are unchanged.
- `test_search_is_substring_bounded_and_read_only`.
- `test_pager_counts_hits`.
- `test_create_project_refuses_non_repo_and_shadow`.
- `test_pin_changes_no_delivery_state`.

### Phase 4: side-by-side, Hub, report an issue

Scope:

- Two seat threads side by side (read-only).
- Hub · Services / Register / Config as read-only displays with copyable commands.
- Report an issue with a client-built redacted diagnostic.

Acceptance tests:

- `test_hub_config_is_read_only_and_redacts_paths_to_hash_on_export`.
- `test_side_by_side_threads_use_message_involves_seat`.

### Phase 5 (optional): named rooms

Scope: §4.9 rooms as view filters. Voice is out of scope (decision 5).

Acceptance tests:

- `test_room_never_changes_addressing_or_wake`.

---

## 8. Phase-1 wireframe (low-fi, 1440px)

```
+----------------------+----------------------------------------+---------------------------------------------+-------------------------------+
| atman                | Lead · planner@steer  next-turn only ⌕≡| OBJECTIVE  Cut an honest developer preview…  | T-1110  Build plan view    [x]|
| [steer        v]     | ● working · run 14m · last output 40s  | Done when: fresh clone follows README…       | phase  WORKING  coder@steer   |
|   steer   12 open    |   codex · quota 62% left (checked 3m)  +---------------------------------------------+ blocked-by  —                 |
|   retc     4 open    +----------------------------------------+ Finishing  T-1110 coder@steer · 14m          |-------------------------------|
|   repo lens: all v   | Advitiya@steer  [operator]  Today 5:31 | Blocked    T-1112 dep T-1110 not accepted    | RUNS                          |
|                      |  What is blocking the preview cut?     | Next       T-1113 ready · atm next claims it |  coder@steer [codex] author   |
| > Lead               |  posted · inbox read · wake confirmed  |                  [graph] list  columns      |    running 14m · tokens unknown|
|   Plan               |                                    ⧉   |                                             |  rev@steer  [claude] reviewer |
|   Needs you     3    | planner@steer  [codex]      Today 5:37 |  T-1108 ✓accepted                           |    done 6m · 41,200 tok · REJECT|
|   Fleet          9   |  T-1112 waits on T-1110's accept; the  |     └─ T-1110 ● working  coder@steer        |-------------------------------|
|   Runs      2 live   |  reviewer is limited until 17:40.      |          └─ T-1112 ◌ waiting                |REVIEW  head 24fb8c0…(40)      |
|   Evals   (phase 2B) |  No owner action.                  ⧉   |              [dep T-1110 not accepted]      | REJECT by rev@steer @24fb8c0  |
|   Mail    (phase 3)  |                                        |              [seat rev@steer limited 17:40] | done, not accepted            |
|                      | ┌ card ─ recorded ────────────────────┐|  T-1109 ✓ done, not accepted  (amber)       |-------------------------------|
|   Hub                | │ claim  T-1110 by coder@steer  5:23 │|     └─ T-1113 ○ ready                       |HANDOFF from T-1108            |
|                      | └─────────────────────── open in plan ┘|  T-1111 ⏸ HOLD: waiting on pricing          | "schema frozen at …"          |
|                      | NEEDS YOU (unstructured, asked only)   |                                             |-------------------------------|
|                      |  pm@steer 4:58 "DECIDE: which rule…?"  |                                             |MESSAGES re T-1110 (12)   more |
|                      |    -> answer in chat (ruling: phase 2A)|                                             |COMMIT  t1110-plan@9c1e…  PR   |
|                      +----------------------------------------+                                             | $ git diff main...9c1e… [copy]|
|                      | Tell the lead…                   [re v]|                                             |USAGE codex 62% · checked 3m   |
| 17:42:10             | as Advitiya (operator)        [ Send ] |                                             |           [ ← back to plan ]  |
+----------------------+----------------------------------------+---------------------------------------------+-------------------------------+
At 390px: sidebar → top bar with project switcher; conversation and plan become two tabs; drill-down is a full-screen sheet.
```

---

## 9. Decisions (the user's answers, 2026-09-19)

These replace the open questions. They are binding on the sections cited.

1. **Projects: split the shared board, one board per project.** The migration is specified in §4.11. It is copy-based, dry-runnable and reversible, and loses no history, receipts, trajectories or accept records. Applied in §4.1, §4.11 and Phase 1S.
2. **Lead: the user's choice.** The lead is a per-project setting (`master.json.lead`) that the user picks from any registered seat. There is no hard-coded default. When it is unset, the app asks the user to pick and never guesses. The app shows each seat's harness and says plainly when the chosen lead can only answer on its next turn. Applied in §2.2, §5.1, §5.5, §6 and the phase 1 tests.
3. **Decisions: a lead's ruling unblocks immediately.** The owner can override, and an override reopens the affected ticket. "Needs you" shows the ruling as *ruled by lead · not yet seen by you* until the owner marks it seen. Applied in §4.5, §6 and the phase 2A tests.
4. **Scored holdout runs: the user or the lead may start one.** It must be from a frozen, reviewed config (a structured accept at the frozen commit), never a second time on the same holdout without a new frozen config, and the run records who started it. Applied in §4.10, §6 and the phase 2B tests.
5. **Voice: out of scope for now.** No voice or mic controls are rendered. Applied in §2.1, §2.2 and phase 5.

No open questions remain for phase 1.
