# T-994 — final README promise-honesty review

**Verdict: ACCEPT.** Reviewed T-819 PR #184 at exact head
`ec098286d4ee50e7006c18476bfb50865b197ee6` against the twelve-item T-994
ledger and the three remaining fixes identified at report commit `cd62206`.

## Final three fixes

1. **Custom harness: resolved.** The first-user example contains literal
   `{prompt_file}` in a complete command:
   `atm join qwen --roles backend --harness custom --cmd 'ollama run qwen3:8b < {prompt_file}'`.
   This matches the template contract in `docs/byoa.md` and is no longer the
   rejected `--cmd '...'` shape.

2. **Review and merge: resolved.** Working-as-a-team step 4 says human review
   is the documented workflow rather than an enforced gate. It accurately
   states that a reviewer other than the author accepts the exact review
   commit with `atm accept --sha`, and that `atm merge` is explicit rather
   than silent auto-promotion.

3. **Antigravity: resolved.** Its row is limited to an experimental status,
   documented discovery, and the runtime-observed boundary that headless
   launch and task completion are unsupported in this preview. The
   unevidenced historical-completion claim is gone.

## Twelve-item regression ledger

| # | Result at `ec09828` |
| --- | --- |
| 1 | Pass — no unsupported competitive absolute. |
| 2 | Pass — no missing GIF, demo directory, recording, splice or release-commit claim. |
| 3 | Pass — source clone is the sole supported preview install, bounded to one tested macOS machine; Homebrew/Linux/pipx are planned. |
| 4 | Pass — `--prefix` is described as symlinks that retain the checkout dependency. |
| 5 | Pass — quickstart samples are removed before the T-001/T-002 walkthrough. |
| 6 | Pass — artifact-aware `done`, successor readiness/posting and supervised/manual claim paths remain exact. |
| 7 | Pass — productive watcher runs, reported cost, labelled estimates and unknown values remain distinct. |
| 8 | Pass — local coordination state, provider prompt traffic, credentials and multi-file runtime boundaries remain exact. |
| 9 | Pass — proof and review language now describes supported records and documented workflow without claiming enforcement. |
| 10 | Pass — non-author acceptance and `done` ownership/IN-REVIEW behavior remain exact. |
| 11 | Pass — public evidence replaces local ticket references; remote and Antigravity remain unsupported, metrics are split from comparisons. |
| 12 | Pass — every remaining repository-local Markdown/HTML target exists in the candidate tree. |

## Boundary checks

- No runnable or supported Homebrew, Linux, pipx, remote-control, Antigravity
  task-execution, hosted-board, Vercel, recording or demo-asset claim appears.
- Cursor remains labelled a supervised watcher rather than a native wake.
- Metrics and privacy wording retain the measured/estimated and
  local/provider boundaries.
- The `de5f2ab..ec09828` delta changes only the three reviewed README areas; it
  does not redesign the document or regress the other accepted items.

## Verification

- Fetched PR #184 and resolved exact head
  `ec098286d4ee50e7006c18476bfb50865b197ee6`.
- Inspected the exact three-line-area diff and the full candidate README.
- Checked every remaining README-local link and image with `git cat-file -e`;
  all resolve.
- No full suite or provider process ran, as required by the review scope.
