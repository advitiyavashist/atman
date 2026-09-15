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

Still required: reviewed T-1014 runtime; verified provider auth/permission and
watcher execution; real Claude/Codex/Cursor tool traces; final uncut cast and
timings; T-972 clean-user checks; T-819 copy/CTA and Pages integration.
