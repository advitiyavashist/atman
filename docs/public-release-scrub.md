# Public release scrub (T-366)

Checklist for making `advitiyavashist/atman` public. **Do not flip GitHub
visibility until this report is green and merged.**

## Automated gate

```sh
python3 scripts/public_scrub_check.py
```

CI runs the same check on every push/PR (`.github/workflows/public-scrub.yml`).

## Manual checks

| # | Check | How |
|---|---|---|
| 1 | Secrets / tokens | `public_scrub_check.py` + optional `gitleaks detect --source .` on full history |
| 2 | Absolute home paths | Scrub script; no `/Users/<operator>/…` in tracked files |
| 3 | Live board dumps | No committed `.tickets/` trees, `messages.jsonl` from production, or `workforce.json` with secrets. Fixture `tests/data/legacy_board/` is synthetic only. |
| 4 | Private ops docs | Removed: `CROSS_REPO_PINS.md`, `INTEGRATION.md`, `implementation-plan.json`, `messaging-plan.json` |
| 5 | Steer-only fleet docs | `connect-claude.md` documents explicit `TICKET_BOARD_FORBIDDEN_ROOTS`; no implicit host defaults in code |
| 6 | LICENSE + README | MIT `LICENSE`; `README.md` is product-facing, no operator paths |

## Evidence to post on review

- Output of `python3 scripts/public_scrub_check.py` (must print `CLEAN`)
- `pytest -q` summary on the scrub branch
- PR link; **no visibility change in the scrub PR**

## After merge

Open a **separate** CoS/CEO ticket to flip repository visibility. That ticket
owns the GitHub settings change, not this scrub.
