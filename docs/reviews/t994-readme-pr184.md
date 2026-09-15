# T-994 — final README promise-honesty review

**Verdict: FIX.** Reviewed PR #184 at exact head
`2bba43302ce2f58b55f05e5ee9533519af3f745b` against
`origin/main@e201436ea72b21601c94811347ed90ea19cae705` for the 2026-09-16
macOS developer preview. T-819 should make these sentence-level fixes.

1. Replace `## Three things it does that nothing else does` with
   `## Three things Atman does today`. The repository has no comparative
   evidence for the absolute claim.

2. Remove the image reference to `docs/demo/atman-handoff.gif` and replace its
   caption paragraph with:

   > **Your agents. One handoff. No retyping.** The executable walkthrough
   > below shows the exact commands and handoff output.

   Neither the GIF nor `docs/demo/` exists at the reviewed head, so the image,
   recording link, and claims about an uncut recording and release commit are
   dangling. In the status table, replace `board history; recording under
   docs/demo/` with a public test or checked-in evidence path before calling
   the Claude process path tested.

3. Replace the install paragraph at lines 67–71 with:

   > Python 3.9+ and Git are required. The supported developer-preview path is
   > `git clone` plus `./install.sh` on macOS; it has been tested on one macOS
   > machine. Homebrew, Linux packages and pipx are planned.

   Replace the Homebrew paragraph at lines 86–92 with:

   > Homebrew on macOS is planned but unavailable: the checked-in formula has
   > a placeholder checksum, the tap is unpublished, and no release is tagged.
   > Until those release artifacts exist, use the clone path above.

   An unavailable path is not a second official install path. The tap still
   does not resolve and the formula still contains
   `REPLACE_BEFORE_RELEASE_run_scripts/build_release_tarball.py`.

4. Replace `./install.sh --prefix DIR installs somewhere isolated` with:

   > `./install.sh --prefix DIR` places the `atm` and `tickets` symlinks in
   > `DIR`; they still run `tickets.py` and its sibling modules from this
   > checkout, so keep the clone at the same path.

   `--prefix` changes the bin directory; it does not copy an isolated runtime.

5. After the quickstart paragraph, insert:

   > Before the walkthrough, run `atm quickstart --remove`. Otherwise the
   > sample tickets remain T-001 through T-003, the CSV tickets receive later
   > IDs, and the commands below operate on the wrong tickets.

   This removal happened in T-819's proof run but is absent from README.md.

6. Make the acceptance/completion example preserve its reviewed artifact.
   Replace the `atm done` command and its output with:

   ```sh
   atm done T-001 --artifact /path/to/project/.worktrees/worker \
     --notes "summarize(path) -> dict in csv_summary.py"
   # T-001 done ... recorded worker@76279da ... unblocked: T-002 ... started: T-002
   ```

   Then replace `` `started: T-002` means the task was posted to a seat, not
   that work began. `` with:

   > `started: T-002` means B became ready. Because this example does not
   > reserve or suggest B to a seat, that line does not mean a task was posted.

   Replace step 3's opening with:

   > Atman unblocks B. In this shell walkthrough, the eligible worker claims B
   > with `atm next`, and its prompt prints A's handoff.

   Finally, replace `No further instruction from you after accepting A` with
   `No further instruction from you after accepting A and marking it done`.
   `atm accept` does not release successors; `atm done` does. The runtime posts
   a successor task only when the child is reserved or suggested to a seat.
   The supervised-watcher qualification that follows is accurate.

7. Replace the last three sentences of feature 3 with:

   > The local app reports productive watcher-run turns and harness-reported
   > cost for completed tickets, leaving missing values blank. `atm turns` can
   > also show clearly labelled list-price cost estimates. Unknown is not zero.

   Make the north-star paragraph use the same definitions. The current
   `What the board shows is measured, not estimated` claim conflicts with the
   `cost_usd_est` output implemented in `src/ticket_board/turns.py`.

8. Replace the philosophy opener with:

   > The value is in how your agents work together, and you own the
   > coordination records: the board is local, the code is open source, and
   > model calls use your own provider subscriptions.

   Replace the `Local, open source, your own subscriptions` paragraph with:

   > **Local, open source, your own subscriptions.** The MIT-licensed preview
   > keeps coordination state in plain files under `.tickets/` and requires no
   > hosted Atman service or external database. Atman stores no provider
   > credentials; agent CLIs use their own logins and send prompts to their own
   > providers.

   `Every trace stays on your machine` overstates the provider boundary, and
   `one Python file` is false: the installed runtime imports sibling modules
   and `src/ticket_board`.

9. Replace the two proof/review claims with:

   > **Planned tickets can carry their own proof.** A sounded plan item is
   > ready only when it says what caused it, what changes, and what proves it.
   > A submitted review points at an exact commit, and acceptance records a
   > verdict on that commit. The app distinguishes a plain done flag from
   > accepted work.
   >
   > **Review is an explicit step in the documented workflow.** Submitted work
   > goes to a review queue. Merge is a command rather than a side effect of a
   > green check, and a posted task is not working until it is claimed.

   Quickstart and ordinary created tickets need not carry cause/change/proof,
   and `atm done` can close claimed work without `atm review` or `atm accept`.
   `Every ticket carries its own proof` and `Human review is the gate`
   therefore describe policy that the product does not enforce.

10. Replace the known-defect sentence beginning `atm done and atm accept need
    the coordinator seat` with:

    > `atm accept` requires a reviewer other than the ticket's author; it does
    > not require the master seat. `atm done` requires the ticket owner or the
    > current master. It records the commit of the artifact checkout, so use
    > `--artifact` to point it at the accepted worktree when closing elsewhere.

    Structured acceptance checks reviewer identity and the submitted SHA;
    stale-owner enforcement applies separately to `done`.

11. Remove non-public ticket references from the product prose and evidence
    table. In particular, replace the capability-checklist introduction with:

    > This table records what is tested, partial, and planned for this preview.
    > Its evidence links point to files and tests in this repository.

    Replace the remote row's status with `not supported in the developer
    preview`, split `Metrics dashboard, efficiency comparison against another
    tool` into separate rows because the local metrics view exists, and remove
    evidence cells that contain only T- numbers or `board history`. The public
    tree contains no `.tickets/` records or `docs/launch/capability-checklist.md`,
    so T-818/T-898/T-972/T-975/T-977/T-988 citations are not reviewable by a
    first user. Replace the author-machine Antigravity sentence with:

    > Antigravity is experimental: discovery is documented, but its current
    > headless one-shot path is not supported in this preview.

12. Fix the remaining dangling navigation links: change the top `First run`
    link to `docs/first-session.md`, remove the separate missing `First run`
    entry under `Read next`, and remove the link to absent `docs/internal/`.
    All other repository-local README links resolve at this head. The GitHub
    Pages site and GitHub issues links each returned HTTP 200; no Vercel URL or
    hosted-board promise remains.

## Checks that pass

- Cursor is consistently described as supervised rather than native wake.
- The custom-harness command includes required `{prompt_file}`.
- Claude Code, Codex, Cursor, custom, remote-adapter and experimental
  Antigravity status are differentiated rather than called interchangeable.
- Remote coordinator access is labelled unsupported for the preview; the app
  is positioned as local and the checked-in app image resolves.
- The known `atm plan`/PR, `atm sync`/origin, `atm reserve`/master and Codex
  watcher limitations are disclosed with workarounds.

## Verification

- Candidate-tree link check: four absent targets (`docs/demo/atman-handoff.gif`,
  `docs/demo/`, `docs/onboarding/first-run.md`, `docs/internal/`).
- `python3 -m pytest -q tests/test_t322_quickstart.py
  tests/test_t944_accept_reject.py tests/test_t781_success_trigger.py
  tests/test_t861_cursor_agy_wake.py tests/test_t480_cost_estimate.py
  tests/test_t865_homebrew_release.py`: 81 passed; one localhost-bind case was
  denied by the sandbox and passed when rerun with localhost permission.
- Read-only tap check: `advitiyavashist/homebrew-tap` did not resolve.
- External link check on 2026-09-15: GitHub Pages site and issues returned 200.
