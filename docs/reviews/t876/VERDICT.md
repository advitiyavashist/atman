# T-876: ACCEPT exact PR #116 revision 2a942f0

Candidate: PR #116, `2a942f0be9df2f316c00e82cb30d8adfee672ca1`.
Base: `3679053a9027733b673448182a91378cb686260d`.
Verifier: `atman-verify-t839-astra-0913`, own worktree and branch.
Only review artifacts are changed by this verifier.

## Scope and isolation

Re-ran complete wakeup, BYOA, T-839 and all identity-named test modules,
plus T-327, T-808 and T-836. The invocation and result are in `suites.log`.
The explicit launch module is present twice in the generated invocation;
any repeated cases are not additional coverage. No `-k` selections were used.
Test processes clear inherited TICKET/CLAUDE/CODEX/CURSOR variables and set
scratch TICKETS_DIR/cache paths; fixtures use disposable boards and homes.

## Prior failure replay

`prior_comparison.py` and `t844_probe.py` come from T-847 commit `cfea5ce`.
Only checkout/wheel paths and the candidate display label were adapted.
Prior checkout HEAD was verified as `feca2f5bee8df5d8a47d08c8bac6bed2554c648a`;
baseline checkout HEAD was verified as `3679053a9027733b673448182a91378cb686260d`.

`prior_comparison.log`: failed prior candidate leaks parent remote mail,
posts as parent, and retains copied parent Claude local hooks. The current
candidate reads worker mail only, posts as worker, and removes those hooks.
Parent settings bytes stay unchanged in both versions.

`t844_probe.log`: original independent probe exits zero. Source and installed
wheel explicit-seat precedence and recorded-session behavior pass. Generated
Cursor worker pinning and real disposable `spawn --exec true` target-worktree
registration pass. Baseline and candidate remote behavior pass. Copied Claude
local parent hooks are removed. The spawned scratch watcher was stopped.

## Installed wheel

Built using system Python 3.9.6 / pip 21.2.4 with `pip wheel --no-deps
--no-build-isolation`, installed `--no-index --no-deps` into a fresh venv.
Wheel: 274490 bytes, SHA256
`ae964756661c74b43c6dd564102ba8ab385479cae7fd9d5eb5af217f722bf585`.
`wheel-build.log` and `wheel.log` contain build/install and import provenance.
CLI and session_boundary resolve inside venv site-packages with no checkout
PYTHONPATH and no root `tickets` module imported.

## Contract limits

The existing recorded-session contract still accepts a raw forged SID when no
explicit assigned seat is provided, as T-847 documented. This evidence covers
supported generated launch/hook/wrapper boundaries; it does not establish raw
environment authentication. No live model/native wake, canonical-seat mutation,
production-board testing, implementation edits or merges were performed.

## Verdict

**ACCEPT exact `2a942f0be9df2f316c00e82cb30d8adfee672ca1`** for the scoped
T-839 repair. Complete requested module run: **191 passed in 838.33 seconds**.
The launch module appeared twice in the invocation, so this count includes
repeated execution and is not a claim of 191 distinct tests. Both independent
reproducer scripts exited zero, and the real installed-wheel smoke passed.

All 52 wakeup tests pass, including the prior permission-inheritance blocker.
The reviewed change copies both Claude settings files through the shared
sanitizer. Spawned workers keep the project allow-list and custom hooks,
receive their own pinned hooks, and inherit no tested parent board hooks.
Parent settings stay unchanged; `.agents/hooks.json` remains excluded.

The suite used `-p no:cacheprovider` and
`--basetemp=/private/tmp/t876-suite-tests` in addition to the module list logged
in `suites.log`. This is the complete requested module selection, not a claim
that the entire repository suite ran. PR head was rechecked as the exact
candidate during verification. CEO retains merge authority.
