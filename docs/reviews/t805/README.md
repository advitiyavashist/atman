# T-805 independent Core migration review

Verdict: **REQUEST FIX** for T-642 (`ed57905`) and for the combined
T-656/T-657 pin (`0ba928a`). **T-817** is the concrete repair blocker for
T-643. No candidate implementation was changed, merged, or accepted here.

Reviewed 2026-09-12 on macOS with Python 3.9.6. The Atman remote main was
`0f3d577f5bac5ec7eb56fcadc9d6b25b58cca387`, confirmed again with
`git ls-remote origin refs/heads/main` after execution. A board announcement
of main `7571278` did not match this repository's remote; it was not used.

## Pins and reconciliation

| Artifact | Exact pin | Decision |
| --- | --- | --- |
| T-642 corpus | `ed5790565682eb9dc41ecc12daf8c115f297018a` | REQUEST FIX: golden is environment-sensitive and does not describe current main |
| T-656 dependency refusal | `0ba928a3fa363ee05eed142663db63babc8bcba9` | REQUEST FIX as submitted: clean-board refusal works, but the pin conflicts with current lane gates and leaks claim locks on corruption |
| T-657 corrupt committed JSON | same `0ba928a` | REQUEST FIX: refused claims mutate lock state; dangling symlinks produce traceback |

`git merge-tree --write-tree origin/main ed57905` exits 0 and produces tree
`076a2c6aeca0955551522b407c225f27d3408b8c`. This tree was archived and executed
without merging into any branch. Only the corpus, runner, adapter, fixtures,
and corpus tests differ from main.

`git merge-tree --write-tree origin/main 0ba928a` exits 1 with conflicts in
`tickets.py`: `try_claim` and `cmd_claim`. Main adds ready-lane checks where
the repair adds dependency handling. Both checks must survive reconciliation.
The two pins otherwise touch disjoint paths; they cannot simply be accepted
in either order because of the behavioral findings below. T-656 and T-657
share one commit and must not be applied twice.

Recommended sequence: T-817 owner reconciles and repairs the oracle against
current main; promotes both negative cases into the executable corpus and
adapts it to current accepted lane behavior; an independent reviewer accepts
the new pins and semantic golden diff; the master merges and closes the gates;
then T-643 starts. Do not refresh a golden just to erase a failure.

## Executed evidence

`results.json` contains every case's outcome, differing observation paths,
representative exact stream differences, warm metrics, and adversarial command
stdout/stderr/exit plus changed paths. Large stream samples and difference
lists are explicitly bounded; the replay script regenerates the observations.

| Run | Scenario assertions | Exact golden matches | Warm 250-ticket p50 / p95 |
| --- | --- | --- | --- |
| Exact `ed57905` | 16/16 | 11/16 | 184.144 / 208.702 ms |
| Main + T-642 conflict-free merge tree | 15/16 | 9/16 | 196.806 / 343.312 ms |

Both warm measurements use seven samples and meet the 1000 ms p95 limit.
This is local regression evidence, not a throughput claim.

The standard command `python3 tools/run_core_conformance.py --json` exits 1
on the exact pin with `CONFORMANCE FAIL: candidate differs from the Python
oracle golden`. On the main merge tree it exits 1 at:

```text
CONFORMANCE FAIL: first-claim: exit 1 not in [0]
stdout: no ticket ready: 2 open, all waiting on unfinished work
stderr:
```

The per-case replay continues after a failed case to expose the remaining
results. It does not record or replace a golden. Storage cases use the
candidate tree's Python storage adapter.

T-642's missing-command, relative-command, schema, and contract checks:

```text
python3 -m pytest -q tests/test_t642_core_conformance.py -k 'not black_box'
6 passed, 1 deselected in 1.20s
```

T-656/T-657 author tests on exact `0ba928a`:

```text
python3 -m pytest -q tests/test_claim_dependencies.py tests/test_malformed_ticket_json.py
11 passed in 8.43s
```

Hook identity and continuous-wake regression tests on exact `ed57905`:

```text
python3 -m pytest -q tests/test_t633_hook_identity.py tests/test_t640_continuous_wake.py
29 passed in 97.18s (0:01:37)
```

## Findings

1. **Corruption refusal leaks locks.** Seed valid open T-001 and malformed
   T-002 (`{"id":`). On `0ba928a`, each of `claim T-001`,
   `status T-001 in-progress`, and `assign T-001 --owner worker` exits 1 with:

   ```text
   corrupt ticket T-002: Expecting value: line 1 column 7 (char 6)
   ```

   Ticket bytes remain unchanged but a new `T-001.lock` remains. `try_claim`
   acquires the lock before `load_all` can exit on corruption. This violates
   the stated no-mutation contract and can obstruct a later valid claim.
   The author's create/graph tests do not exercise this sequence. The corpus
   snapshot also excludes `.lock` paths, so explicit lock assertions are needed.

2. **Incomplete corruption boundary.** A dangling canonical `T-002.json`
   symlink makes `board --quiet` exit 1 with a `FileNotFoundError` traceback,
   rather than a deterministic corruption diagnostic. `update T-001 probe`
   still exits 0 and modifies T-001 plus its trajectory despite a malformed
   neighboring record. In contrast, malformed `board`, `graph`, and `create`
   correctly exit 1 without any changed paths; orphan `.partial` residue is
   correctly ignored. The full promised mutation boundary needs explicit
   coverage, not just a create test.

3. **Dependency repair works on a clean board, but is not integrated.** With
   T-002 depending on unfinished T-001, exact `0ba928a` returns exit 1 and
   `T-002 has unfinished dependencies: T-001`, preserving the entire board
   including absence of a claim lock. Current main instead claims T-002.
   Both new JSON files are prose-shaped case descriptions, not cases in
   `corpus.json`; the runner never executes them. In particular, the STORE
   description's final orphan step never removes the malformed record left
   by its initial setup. It needs a real executable fixture lifecycle.

4. **Golden isolation is incomplete.** Exact pin scenario assertions all pass,
   but five cases have differing snapshot hashes. Identity stdout concretely
   differs at `dirty: 1` (golden) versus `dirty: 2` (actual). An instrumented
   read of the unmodified runner's fixture showed Git status:

   ```text
   ?? .fixture-board/
   ?? home/
   ```

   Canonical `agents/worker.json` retains `dirty:2`. The fixture/environment
   needs deterministic Git state; do not mask genuine candidate differences.
   The exact historical cause of the author's `dirty:1` is not established.

5. **Main has accepted behavior absent from the old corpus.** Unsounded
   `plan` records now have `lane=capture`; dependency-ordering's first `next`
   cannot claim them. Main's join output now points to `tickets connect
   --worker`, and bulk plan output and records carry the new lane. These are
   visible in the report. Preserve those semantics while revising the fixture
   and reviewing the new golden; do not drop the lane checks to regain parity.

## Reproduction

Create disposable source archives, not working copies of the live board:

```sh
scratch=$(mktemp -d /tmp/t805-review.XXXXXX)
mkdir "$scratch/main" "$scratch/corpus" "$scratch/repairs" "$scratch/combined-corpus"
git archive 0f3d577 | tar -x -C "$scratch/main"
git archive ed57905 | tar -x -C "$scratch/corpus"
git archive 0ba928a | tar -x -C "$scratch/repairs"
git archive 076a2c6aeca0955551522b407c225f27d3408b8c | tar -x -C "$scratch/combined-corpus"
python3 docs/reviews/t805/replay.py "$scratch" > /tmp/t805-results.json
```

If the merge tree object is unavailable, regenerate it with
`git merge-tree --write-tree 0f3d577 ed57905` before archiving it.
All direct fixture writes in the review script affect fresh temporary boards.

One earlier per-case run was interrupted by host `ENOSPC`. The issue was
reported on the board immediately; after available capacity recovered from
174 MiB to 2.5 GiB, the entire replay was rerun. `results.json` contains the
completed rerun with no disk-exhaustion failures. Candidate source was not
changed to address infrastructure failures.
