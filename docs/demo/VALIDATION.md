# T-924 rehearsal validation — 2026-09-15

Runtime under test: `1e6d595bb7705d319f434659e34ddd60f4b56c66`.
Scope: rehearsal scripts only; no product code changes, provider calls or final recording.

- `python3 -m pytest tests/test_t924_demo_rehearsal.py -q`: **7 passed**.
  Negative cases reject echoed success, mismatched acceptance SHA, wrong B
  owner, claim before done, missing actual handoff, and a failed CLI command.
- Fresh `rehearsal.py setup` + `rehearsal.py dry <RUN>`: **passed**.
  A's Python unittest passed; the reviewer independently reran it; B integrated
  the accepted commit, imported the function, passed both summary and CLI tests,
  and printed `north 15 600.00`, `south 7 315.50`, `east 21 987.25`.
- Accepted A: `f997b37c13c68ebd6d9a37bbbed89c7e486184c3`.
  B: `7ff397bc5ab34a17e53fc5668a683187f536ab67`; ancestry check passed.
- `screenshot.py <RUN>`: **4 asserted frames passed**, A/B at 1280×900 and
  390×844. Exact accepted artifact/handoff, selected ticket, `Board synced`,
  no horizontal overflow, no page errors. Chrome via Python Playwright.
- Real-seat launcher refused a dry run even with `--release-approved`.
- Python compilation and `git diff --check`: passed.

The local run preserves `logs/events.jsonl`, four PNGs and
`logs/screenshots.json`. These are dry-run evidence, not launch hero assets.
The generated sample board uses a local bare remote and a labeled dry PR
fixture; no real PR or provider inference is claimed.

Historical remaining work (T-1014 gate superseded below): verified provider auth/permission and
watcher execution; real Claude/Codex/Cursor tool traces; final uncut cast and
timings; T-972 clean-user checks; T-819 copy/CTA and Pages integration.


## Recovery and real attempt — 2026-09-15 14:26 +08

CEO removed the T-1014 prerequisite at 14:22. Runtime pinned to current main
`799cc0a6c172b104f86492150880acf44ce5659a` in
`/private/tmp/atman-t924-runtime-0915`.

- Recovered `ab3cdb9` pushed before further work; feature branch then rebased.
- Receipt/permission tests: **9 passed**. The new cases cover a provider's
  zero-exit “success” result containing tool denials and explicit denial events.
- Fresh dry flow on current main: **passed**, including independent A test,
  exact structured acceptance, B handoff, accepted-code ancestry and CLI test.
  Dry run: `/private/var/folders/qr/j_ljkf713xl8td9p7c7mwznh0000gp/T/atman-demo-jk2qvfpr`.
- Current-main browser check: **4 asserted frames passed**, A/B at desktop
  and mobile sizes, exact accepted SHA, handoff, synced state and no overflow.
- Claude and Cursor authentication status reported logged in. No global
  `~/.cursor/hooks.json` existed at the check.
- Created private disposable GitHub repo
  `advitiyavashist/atman-demo-t924-0915`; pushed seed main only. No sample PR.
- Started actual Claude inference under asciinema on the real throwaway board.
  It attempted `atm objective --set` twice; both Bash tool calls were denied
  with `This command requires approval`. Claude exited zero and labeled its
  result success despite completing no board command. Launcher now treats
  those denial receipts as a failed attempt. No bypass was attempted.
- Real attempt: `/private/var/folders/qr/j_ljkf713xl8td9p7c7mwznh0000gp/T/atman-demo-8pmzgx4w`.
  Original uncut evidence: `logs/ceo-uncut.cast`, `logs/ceo.jsonl`,
  `logs/events.jsonl`; preserve these on disk, including all denials.

**Blocker sent to the master:** need an interactive approved Claude seat or an
operator-approved scoped tool policy for this disposable board. A retry must
use separate logs so the denied take remains intact. No Codex or Cursor turn
was started; no completed multi-agent take, hero.gif, demo.mp4, per-beat clips,
poster.png or timestamped publication captions are claimed. Removing the
Claude beat alone does not prove a second real seat or the acceptance handoff.
Do not publish dry receipts or permission-denial footage as a successful demo.

## Completed Codex/Cursor take and recovered evidence

A later run completed with a Codex planner/worker/reviewer and Cursor worker and
was published in PR #211. The earlier Claude denial remains historical; it is
not footage from the published take. The original disposable run directory was
subsequently deleted, but the provider session records survived in the local
provider stores. T-1034 preserved sanitized excerpts and the exact prompts under
`docs/assets/demo/evidence/`, with source and excerpt hashes in `manifest.json`.

The evidence shows Codex's board actions, worker edits/tests/PR, the independent
review and full-SHA acceptance, plus Cursor's own `atm next`, accepted-SHA check,
ff-only merge, edits, tests and commit. GitHub PR #5 independently resolves to
the accepted A SHA. B was a local-only commit and its deleted Git object cannot
be rechecked, so the provider trace is the remaining ancestry evidence; this
limit is stated beside the published claims. Media bytes were not changed.
