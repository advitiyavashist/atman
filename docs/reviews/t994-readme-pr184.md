# T-994 — README successor promise-honesty review

**Verdict: FIX.** Re-reviewed T-819 PR #184 at exact successor head
`de5f2abd47c34e57c382212af5eef3c5e846bf46` against the twelve-item T-994
ledger recorded at report commit `61563b6`. Ten items are resolved; items 9
and 11 remain partial, and one first-user agent-support defect remains.

## Exact fixes

1. Replace the onboarding sentence at README lines 112–114 with:

   > `atm connect` prints tool-specific hook and watcher setup for Claude Code,
   > Codex and Cursor. `atm join qwen --roles backend --harness custom --cmd 'ollama run qwen3:8b < {prompt_file}'` registers a custom harness ([Bring your own agent](docs/byoa.md)).

   A custom command must contain `{prompt_file}`. The literal `--cmd '...'`
   example is rejected by the CLI, so the first onboarding path is still not
   executable even though the later custom-runner example is correct.

2. Replace working-as-a-team step 4 at README line 337:

   > 4. Submit through human review before merge. Merge is an explicit command.

   The philosophy section now correctly calls review a documented workflow,
   but this later `Human review is the gate` absolute reintroduces the old
   enforcement claim. `atm done` can still close claimed work without review.

3. Replace the Antigravity status row at README line 276 with:

   > | Antigravity seat | experimental: discovery is documented; headless launch and task completion are not supported in this preview | `docs/connect-agy.md`; `docs/wake-recipients.md` |

   `task completion historical only` is an unsupported public claim: the
   linked repository document contains no historical completion evidence, and
   the current headless path is explicitly unsupported.

## Twelve-item ledger

| # | Result at `de5f2ab` | Evidence |
| --- | --- | --- |
| 1 | Resolved | Competitive absolute removed; heading is `Three things Atman does today`. |
| 2 | Resolved | Missing GIF, `docs/demo/` link, recording, splice and release-commit claims removed. |
| 3 | Resolved | Source clone is the only supported preview install and is bounded to one tested macOS machine; Homebrew/Linux/pipx are planned. |
| 4 | Resolved | `--prefix` is accurately described as two symlinks that retain the checkout dependency. |
| 5 | Resolved | `atm quickstart --remove` now precedes the T-001/T-002 walkthrough. |
| 6 | Resolved | `done --artifact` preserves the reviewed worker pin; unreserved successors are described as ready rather than posted; `done` is named as the release action. |
| 7 | Resolved | Productive watcher runs, reported cost, labelled list-price estimates and blanks are distinguished consistently. |
| 8 | Resolved | Local coordination records are separated from provider-bound prompts; the false one-Python-file claim is gone. |
| 9 | Partial | Philosophy wording is fixed, but working-as-a-team step 4 repeats the unenforced human-review gate claim; exact fix 2. |
| 10 | Resolved | `accept` non-author rule and `done` owner/IN-REVIEW behavior match `review_verdict.py` and `stale_accept_error`. |
| 11 | Partial | Local T-number evidence is removed, remote is unsupported, metrics rows are split, and the feature Antigravity sentence is bounded; the Antigravity table row still claims unevidenced historical completion; exact fix 3. |
| 12 | Resolved | Every remaining repository-local Markdown/HTML target exists in the candidate tree. The old demo, onboarding/first-run and docs/internal targets are gone. |

## Boundary checks

- **Install:** `git clone` plus root `./install.sh` is the sole supported path,
  explicitly macOS-only for the preview. Homebrew, Linux packages and pipx
  have no runnable instructions and are labelled planned/unavailable.
- **Lifecycle:** dependency visibility, `accept` versus `done`, reserved or
  suggested successor posting, manual `atm next`, and supervised watcher
  behavior now match the runtime. Cursor is never called a native wake.
- **Metrics/privacy:** watcher-run turns, reported cost, estimates, blanks,
  local board state, credentials and provider prompt traffic are separated.
- **Remote/demo:** remote coordinator access is unsupported; `remote` is
  described only as a fail-closed adapter contract. There is no hosted-board,
  Vercel, recording or demo-asset claim. The checked-in local-app image exists.
- **Scope:** the successor changes README sentences and table rows within the
  prior ledger; it does not redesign the document.

## Verification

- Fetched PR #184 and resolved exact head
  `de5f2abd47c34e57c382212af5eef3c5e846bf46`.
- Checked every README-local link and image with `git cat-file -e`; all resolve.
- Read the cited implementation and focused tests for successor posting,
  acceptance, completion ownership, cost estimates, custom harness templates,
  and supervised wake. Per task scope, no full suite or provider process ran.
