# T-976: current-main launch journeys, 2026-09-22

**Verdict: PARTIAL, not final release acceptance.** The fresh-install CLI,
artifact gate, scripted interruption recovery, adverse probes, and local UI
checks below passed. No live provider was dispatched. In particular, a
registered Codex label and a fenced ownership transfer do **not** prove
cross-provider continuation. The public README already narrows that promise.
The master must not interpret this report or closing its evidence ticket as
certification of the unrun live journeys.

## Candidate and method

- Source: fresh `git clone https://github.com/advitiyavashist/atman.git
  /tmp/atman-t976-0922-source`.
- Exact main: `e65f1817f873477579cf821c6bb015f29ccd9c1f`. Rechecked remote main
  before submission; unchanged. This supersedes the earlier `08a4a0d`
  diagnostic as current-main evidence, without upgrading its claims.
- macOS 26.6.2 (25G83), arm64, Python 3.9.6; local Chromium via Playwright.
- `./install.sh --prefix /tmp/atman-t976-0922-bin` succeeded. `atm self`
  resolved the clone's root `tickets.py`, with `atm` and `tickets` sharing
  that implementation. No packaged/core-board CLI substituted for it.
- Report authored on `codex/t976-current-main-0922`, in its own Atman
  worktree. No production implementation changed. No author intervention.
- All probe boards were newly created under OS temporary directories.
  Every board mutation used the installed CLI; JSON was read only for
  assertions and receipts. The living Steer board received coordination
  messages and ticket updates only.
- Probe subprocesses strip inherited ticket/seat/session/runtime variables.
  Provider-usage refresh is disabled and the test guard prevents provider
  usage/network work. Harness invocations are deterministic local scripts.
  No paid/live model, credential login, provider auth claim, or Linux run.

## Results

| Journey | Observed outcome | Boundary |
| --- | --- | --- |
| NORMAL | PASS for CLI/runtime mechanics | Stub-auth dispatch separately passed; not live authentication evidence |
| INTERRUPTED | PASS for killed custom process and persisted handoff | Same scripted harness, not a live supported-provider restart |
| INTERCHANGEABILITY | Ownership/fencing mechanics PASS; live continuation NOT RUN | No second supported harness executed the continuation |
| ADVERSE | All listed bounded probes PASS after correcting two probe invocation mistakes | Stale-version case tests foreign executable protection, not a historical release upgrade |
| Local app | Desktop and 390px rendering, mobile post/reload, exact-SHA drilldown PASS after UI build | Fresh source install alone does not supply `/app/`; root legacy UI works |

### Normal: the artifact and gate, not an HTTP response

A fresh git repo was onboarded with `quickstart --agent boss`, samples removed,
and objective plus dependent tickets recorded. The README's documented local
`create --deps` route was used; its existing `plan`/PR limitation was not
bypassed. Alice claimed T-001 in a separate worktree, posted to Bob's inbox,
and committed `stats.json` containing exactly `{ "rows": 3, "columns": 2 }`.

Review pinned `ad72b8cb3626b954ace2ba6b9307119e34de322c`. Alice's self-accept
was refused. Bob's claim of T-002 failed before acceptance. The probe read
`git show <sha>:stats.json` from the coordinator checkout and checked the
values independently of the author's working file. Bob accepted the full
SHA, boss closed T-001 with `--artifact` and fast-forwarded the disposable
main to it, and Bob could then claim T-002. The JSON receipt confirms a Bob
accept event bound to the full SHA and an Alice author.

The successor was only claimed (no code written) in the coordinator checkout;
the UI correctly flagged that Bob needed his own worktree. This does not
certify the implementation of the successor. The author artifact was written
and committed in Alice's worktree throughout.

Separately, `atm quickstart --gate` dispatched a stub `claude` executable whose
auth subcommand returned a synthetic login and whose worker command made a
real commit. The runtime printed both the blocked claim and the subsequent
independent-seat accept. Its commit was
`feeb25f15d464383cda8965502ea1fb882996d04`. This is **stub evidence only**.

### Interrupted worker and coordinator

A separate Python worker wrote `mid.txt`, called `atm handover` with all
contract headings, and signalled readiness before sleeping. The probe sent
SIGKILL to its process group (exit -9). A new CLI process found the original
claim; a second `next` was refused; `pulse` exposed the persisted next action.
`mid.txt` retained `stage-one\n`. Continuation created `finished.txt` with both
stages, committed, submitted review, and was independently read from git and
accepted by Bob. Owner stayed Alice and owner generation did not change.
Accepted SHA: `fcdaba983aaf769a7718403a49498c9450a7f903`.

A separate `atm watch --prompt-kind master --exec <custom script>` was killed
after writing a durable intermediate artifact. Restarting the same master
watcher with a bounded `/usr/bin/true {prompt_file}` command succeeded,
completed one run, and preserved the artifact. This establishes watcher
restart mechanics; it is not proof of a live coordinator's reasoning state.

### Interchangeability boundary

A custom Alice claim transferred to a newly registered Codex-labelled Carol.
Generation advanced from 1 to 2; the old owner's review was refused and the
intermediate artifact remained intact. **Carol did not run Codex or complete
the work.** The full cross-provider journey remains NOT RUN. README's
“cross-provider continuation not proven” remains accurate and should stay.

### Adverse cases

- Missing binary: no harness on isolated PATH; clear install guidance and
  `WALKTHROUGH ONLY`; no scratch board/accept invented.
- Missing authentication: stub logout; exact `claude auth login` guidance,
  verification command, and no executed workflow or fabricated acceptance.
- Stale/foreign installed executable: installer refused overwrite, preserved
  contents, and printed `--prefix`/`--force` recovery. An actual older pinned
  Atman release upgrade was not exercised.
- Repeated action: repeat winner claim refused without advancing generation.
- Concurrent claim: two subprocesses raced; exit codes 1 and 0, one owner.
- Failed check: committed rows=999 failed an independent expected-3 check;
  Bob used `reject --sha ... --reason ...`; rejection stayed on the review
  record. No accept was substituted.
- Interrupted coordinator: custom master watcher killed/restarted as above.

Two initial probe mistakes are retained in the raw evidence, rather than
hidden: `reject --notes` should be `reject --reason`; rejoining an already
custom-bound Bob as Codex is correctly refused. The corrected run uses the
proper reject argument and a unique provider-specific Carol identity. Both
corrected probes passed. Neither mistake is counted as a product defect.

## UI and installation finding

Fresh source-prefix install: `atm ui` starts and serves the legacy root UI.
But `GET /app/` returned HTTP 404 because the clone has no `ui/dist`.
The root README does not include the React bundle build in its install path.
The separate `ui/README.md` does document it. Following that document with
`npm ci` and `npm run build -w ui` succeeded and made `/app/` available.

This is an integrated-app setup/documentation gap, **not a claim that the
legacy root board fails**. The launch owner must decide whether the preview
entry is the legacy root or the React app and connect its first-run directions
to the corresponding setup. Reported to the planner on T-976; no unrequested
installer/UI implementation was changed during independent verification.

After build, Chromium rendered `/` and `/app/` at 1440×900 and 390×900.
Document width equalled viewport width in all four checks; no page errors.
Screenshots were inspected. Read-only mode honestly says no operator is
configured; no lead is silently invented. With `--operator boss` and the
CLI-selected Alice lead, a mobile composer message survived reload. The
screen explicitly said the lead was offline and the message queued; this
was **not** reported as a confirmed live wake. The mobile ticket drilldown
showed the full submitted SHA. All 17 README relative Markdown links resolve
on disk; remote URLs and anchor targets were not validated.

## Targeted regression checks

On the fresh clone at the pinned SHA, with inherited TICKET/ATMAN/CLAUDE/CODEX/
CURSOR environment names removed:

```text
python3 -m pytest -q tests/test_t977_recovery_contract.py \
  tests/test_t944_accept_reject.py tests/test_t1077_quickstart.py
51 passed in 84.09s
```

No full suite, Linux run, Homebrew upgrade, paid model invocation, or
cross-provider live recovery was performed. The README marks Linux packages
planned; this report supplies no additional distribution claim.

## Evidence and next decision

Raw commands, results, scripts, ticket receipts and browser captures live in
[t976-current-main-evidence](t976-current-main-evidence/). Scripts deliberately
use the recorded isolated install path and temporary boards; they are run
receipts, not a new production test framework. The initial and corrected
adverse attempts are separate files. `browser-interact.json` records the
mobile reload/SHA checks. The JSON ticket receipts preserve the accepted
artifact identities even if temporary worktrees are later removed.

For final acceptance the launch owner needs to select the UI entry/setup,
and either obtain separately pinned live same-provider/cross-provider/wake
receipts or explicitly retain the preview's current narrower claims. Do not
turn stub tests, metadata labels, queued messages, or this report into proof
that a provider actually continued interrupted work.
