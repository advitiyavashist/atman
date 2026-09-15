# Harness support matrix

From T-1021 (adopt-from-Orca): Orca lists 40+ CLI agents; Atman registers 7
plus a custom/BYOA path, and the custom-harness contract (`--harness custom
--cmd '...{prompt_file}...'`) already generalises to any shell command. The
gap against Orca is evidence, not architecture. This page is that evidence,
one row per harness per capability, each with a file, test name or ticket a
reader can open. A generic shell adapter existing is not proof a harness is
supported; a row only reads `tested` when something was actually run and
observed.

Status words: **tested** (a named test passed, or a specific real run is
cited with a version/SHA), **partial** (works with a stated caveat or
narrower scope than the claim), **experimental** (documented but a core step
fails), **unsupported** (no native path; a documented fallback may still
apply), **not applicable** (the dimension does not exist for this harness).

Catalog ground truth: `INTEGRATION_CATALOG` and `session_adapters.py`
(`provider_for_harness`, `SUPERVISED_HARNESSES`), asserted single-registry by
`tests/test_t862_quota_adapters.py::test_catalog_is_single_registry`. Native
wake providers exist only for `claude`, `codex`, `cursor`, `remote`; `grok`
aliases to the `cursor` provider (`provider_for_harness("grok") == "cursor"`,
`tests/test_t785_t789_all_provider_wake.py::test_provider_for_harness_maps_grok_to_cursor`);
`gemini`, `devin` and `custom` have no native provider entry at all.

## Claude Code

| Dimension | Status | Evidence |
| --- | --- | --- |
| Discovery | tested | `atm harness available`; `tests/test_t793_harness_usage.py`, `tests/test_t1005_catalog_isolation.py` |
| Auth | tested | `docs/connect-claude.md` `doctor` six-fact report (`config_installed` … `session_adopted`) |
| Launch | tested | T-924 demo take (coordinator pane), cited `docs/product/README-promise-draft.md:306` |
| Continued wake | tested | `CLAUDE_CODE_MESSAGING_SOCKET` transport, T-857; sender-symmetry proven in `tests/test_t861_cursor_agy_wake.py::test_cursor_wake_is_the_same_from_a_codex_and_a_claude_sender` (Claude as sender) |
| Task completion | tested | T-924 demo take; `tests/test_t944_accept_reject.py` (accept/reject/SHA-binding, harness-agnostic, exercised on this take) |
| Recovery | partial | Same-provider restart tested generically (`tests/test_t977_recovery_contract.py::test_same_provider_restart_keeps_one_owner_and_the_same_lease` uses Cursor, not Claude, as the concrete fixture); no Claude-specific cross-provider case |

## Codex

| Dimension | Status | Evidence |
| --- | --- | --- |
| Discovery | tested | same catalog probe as above |
| Auth | partial | `docs/byoa.md` documents `atm harness auth` generically for built-ins; no Codex-specific doctor doc exists (unlike Claude's) |
| Launch | partial | T-924 demo take (worker pane); README known issue: Codex "may stop waking after one run" until the watcher restarts (`docs/product/README-promise-draft.md:307`) |
| Continued wake | tested | app-server control-socket handshake + `turn/start`, T-857; `tests/test_t857_codex_ws.py` (handshake order, loaded/unloaded thread, heartbeat), `tests/test_t947_codex_wake.py` (idle/busy/error/timeout/retry states) |
| Task completion | partial | T-924 demo take only; no Codex-specific `atm done` case beyond the generic accept/reject suite |
| Recovery | tested (cross-provider) | `tests/test_t977_recovery_contract.py::test_cross_provider_handoff_is_not_a_same_provider_restart` is Cursor → Codex, owner_generation 1 → 2 |

## Cursor

| Dimension | Status | Evidence |
| --- | --- | --- |
| Discovery | tested | same catalog probe as above |
| Auth | partial | `docs/byoa.md` `harness auth cursor-seat` example (`Login required` / `Usage quota reached` / missing-CLI states); `--recover-stale` for a dead watcher PID |
| Launch | partial | T-924 demo take; starts through a supervised watcher, not a native wake (`docs/product/README-promise-draft.md:308`) |
| Continued wake | tested | T-861, measured against real `cursor-agent 2026.09.10-fd3934a`: managed `agent persist` tmux server only, `store.db` row confirms a turn started, `woken` / `delivered-unconfirmed` / `supervised` states distinguished — `tests/test_t861_cursor_agy_wake.py`, `docs/wake-recipients.md` |
| Task completion | partial | T-924 demo take only; no isolated Cursor completion test beyond the generic accept/reject suite |
| Recovery | tested | `tests/test_t977_recovery_contract.py::test_same_provider_restart_keeps_one_owner_and_the_same_lease` (same-provider); Cursor is also the origin seat in the cross-provider case above |

## Grok

Grok is not an independent adapter: `provider_for_harness("grok") == "cursor"`.
Any wake evidence below is borrowed from Cursor's T-861 tests, not a direct
observation of a Grok session.

| Dimension | Status | Evidence |
| --- | --- | --- |
| Discovery | tested | catalog probe passes; quota **unsupported** — `tests/test_t862_quota_adapters.py:45` asserts `quota == "unsupported"` for `grok` |
| Auth | not applicable | rides the Cursor adapter entirely; `docs/connect-grok.md` |
| Launch | experimental | `docs/connect-grok.md` documents `atm join --harness grok`; no demo or test cites an actual Grok run |
| Continued wake | unsupported (no direct evidence) | covered only indirectly through Cursor's transport; the generic loop test `tests/test_t785_t789_all_provider_wake.py::test_msg_persist_pokes_each_harness` fakes the wake return rather than exercising a real endpoint |
| Task completion | unsupported | no evidence found |
| Recovery | unsupported | no Grok row in `tests/test_t977_recovery_contract.py` |

## Gemini

| Dimension | Status | Evidence |
| --- | --- | --- |
| Discovery | tested | catalog/status probe only, per `tests/test_t793_harness_usage.py`; quota **unsupported** (`tests/test_t862_quota_adapters.py:45`) |
| Auth | unsupported | no native provider (`provider_for_harness("gemini") == ""`) |
| Launch | experimental | `docs/connect-gemini.md`: "has no native session adapter. Do not spawn a live Gemini network from onboarding." No demo or test cites a real run |
| Continued wake | unsupported by design | `docs/connect-gemini.md`: native wake returns `no live endpoint`; a live persist watcher is poked instead — generic fallback in `tests/test_t785_t789_all_provider_wake.py::test_msg_persist_pokes_each_harness` |
| Task completion | unsupported | no evidence found |
| Recovery | unsupported | no evidence found |

## Devin

| Dimension | Status | Evidence |
| --- | --- | --- |
| Discovery | tested | catalog probe passes; quota **unsupported** (`tests/test_t862_quota_adapters.py:45`) |
| Auth | unsupported | no native provider; `docs/connect-devin.md`: "There is no native Devin adapter" |
| Launch | experimental | `docs/connect-devin.md` documents `atm spawn --harness devin`; explicitly "Do not spawn a live Devin network from tests." No demo or test cites a real run |
| Continued wake | unsupported by design | same watch-poll fallback as Gemini; generic test only |
| Task completion | unsupported | no evidence found |
| Recovery | unsupported | no evidence found |

## Antigravity (agy)

| Dimension | Status | Evidence |
| --- | --- | --- |
| Discovery | tested | catalog probe passes (`docs/product/README-promise-draft.md:309`) |
| Auth | tested | `tests/test_t861_cursor_agy_wake.py::test_agy_probe_reports_a_transport_instead_of_no_adapter` (`caps["native_inject"]=False, caps["supervised"]=True`); quota **supported** via `AGY_STATUS` fixture (`tests/test_t862_quota_adapters.py`) |
| Launch | experimental — fails headless | T-988 (open): `agy -p` returned only `[agy] print timeout after 5m0s with turn in progress` for a one-line prompt on 2026-09-15, against a 90 s watcher cap (`docs/product/README-promise-draft.md:309`) |
| Continued wake | tested (supervised, not native) | measured against real `agy 1.2.2`; supervised by design — `tests/test_t861_cursor_agy_wake.py::test_agy_wake_is_supervised_not_woken`, `test_agy_register_persistent_explains_the_supervised_seat`; `docs/wake-recipients.md` |
| Task completion | historical only | review pins T-811 (`agy-aira2-tty-work@04f7c4a`), T-869 (`atman-pmm-agy-0913@3679053`), T-870 (`steer-pmm-agy-0913@f9d9ee2`); done tickets T-754, T-775, T-797, T-801, T-806. T-823, T-890 and T-900 were named as Agy-delivered elsewhere but are **not** cited here: the board shows T-823 pinned by a Codex seat, T-890's Agy run stopped on a quota limit with a Cursor takeover, and T-900's Agy launch was rejected (`docs/product/README-promise-draft.md:309`) |
| Recovery | unsupported (no dedicated test) | no Agy row in `tests/test_t977_recovery_contract.py`; the T-890 quota-stop → Cursor takeover above is an anecdotal cross-provider instance, not a test |

Antigravity is the sharpest split in this table: discovery and auth pass and
quota is wired, but the actual headless invocation fails outright (T-988).
That is documented consistently everywhere it is mentioned, not hidden.

## Custom / BYOA

| Dimension | Status | Evidence |
| --- | --- | --- |
| Discovery | tested | `docs/byoa.md` `harness check` example; `tests/test_byoa.py::test_harness_check_passes_and_records`, `test_harness_check_fails_loudly`, `test_harness_check_missing_binary_is_a_failure_not_a_crash` |
| Auth | not applicable | `docs/byoa.md`: Atman does not manage credentials for a custom command |
| Launch | tested | `docs/byoa.md` end-to-end log excerpt; `tests/test_byoa.py::test_watch_hands_the_harness_prompt_file_cwd_and_agent` |
| Continued wake | not applicable | no provider entry; only the same persist-watch poll loop as Gemini/Devin applies, no BYOA-specific wake test |
| Task completion | tested | `tests/test_byoa.py::test_a_byoa_agent_gets_the_same_claim_and_review_path` |
| Recovery | unsupported (no test) | no BYOA row in `tests/test_t977_recovery_contract.py`; flagged as a gap in `docs/product/README-promise-draft.md:310` ("needs a test id from T-975") |

## Reading this table

- "Tested" on discovery/auth/launch never implies the harness completes real
  work; check the task-completion row separately.
- A harness with no native wake (Gemini, Devin, custom) still receives work
  through the persist-watch poll loop, labelled `watch-poked`, not `woken`.
  Do not describe that as a native wake in public copy.
- This table complements, and does not replace, T-975's broader
  `docs/launch/capability-checklist.md` (claim → evidence across README,
  landing and the app); that file did not exist yet when this page was
  written on 2026-09-15. Link the two once it lands.
