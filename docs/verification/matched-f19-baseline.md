# Matched f19 baseline (T-911)

Public-safe reuse page. Private raw log and assertion dumps stay under
`/private/tmp/t911-baseline`. This is a complete selected-suite receipt, not
app/CLI/code ACCEPT.

Do not reuse T-891's 61-failure receipt, T-877/T-896 operator-HOME logs, or
`bac7` as equivalent evidence. Compare candidate failure IDs only against this
receipt.

## Tested tree

| Field | Value |
|---|---|
| Tested SHA | `f19bdab093f14930e990141eaf5c8eed4858de93` |
| Merge-base of CLI `26ff79f` | same f19 |
| Merge-base of app `c2f7dd5` | same f19 |
| Review pin | this docs commit on current main; **not** full-suite evidence |
| Worktree that must stay at f19 | `atman-matched-baseline-cursor-t911-0914` |

`tickets sync` fast-forwards the local branch to `origin/main`. Do not leave the
f19 worktree on that merge if the reused venv is still bound to that path.

## Environment

Reuse the accepted T-909 baseline source venv. Do not run
`scripts/hermetic_preflight.py` (it recreates environments). Do not create a
new venv while a compare is in flight.

```sh
VENV=/private/tmp/t909-preflight/origins/baseline-t891-raw/source-venv
# bind YOUR tree only; source path changes, pins must not
$VENV/bin/pip install -e YOUR_WORKTREE --no-deps --no-build-isolation
```

Rebind-time pins (excluding the editable path) matched
`docs/verification/hermetic-preflight.manifest.json`:

- Python 3.9.6
- pytest 8.4.2 from the venv, not operator user-site
- pip 25.3 / setuptools 75.9.1 / wheel 0.45.1
- runtime extras: PyYAML 6.0.3, jsonschema 4.25.1, and the rest of the T-909 freeze

After this suite the same venv also had `build`, `importlib_metadata`,
`pyproject_hooks`, and `zipp`. Leave them. Do not uninstall after the fact.

Sanitized test process: temporary `HOME`/`TMPDIR`, `PYTHONNOUSERSITE=1`,
`PYTHONPATH` unset, `PATH` is `$VENV/bin` plus system bins, no
`TICKET*`/`TICKETS*`/`CURSOR*`/`CODEX*`/`CLAUDE*`/provider/IDE variables.

## Suite

One run. Same single operator-home deselection as T-891. That node is known
fixed by T-890 on later main; do not rerun the debt.

```sh
$VENV/bin/python -m pytest -q --tb=line \
  --deselect tests/test_contracts.py::test_no_test_file_names_a_real_operator_home
```

| Field | Value |
|---|---|
| Result | 8 failed, 2126 passed, 9 skipped, 1 deselected, 11 xfailed in 2039.33s |
| Wrapper | exit 1, 2169.2s, timeout=false, cap 2400s |
| Raw digest | `1d21c4ecb1f95dcf2aa6fb1a43be5d5abdc40b2f1a08f9eb6bd9a861b1efe509` |
| Verdict | NOT ACCEPT. Complete reusable matched baseline. |

T-891 on the same SHA was 61 failed / 2072 passed under operator user-site
pytest. The extra T-891 failures were child `ModuleNotFoundError` after hermetic
`HOME` dropped that user-site. Treat the count drop as environment diagnosis,
never as candidate equivalence.

## Reuse for T-877 and T-896

1. Keep the T-909 baseline source-venv. Do not recreate it.
2. Bind the candidate worktree with `pip install -e WT --no-deps --no-build-isolation`.
3. Confirm `ticket_board` imports from that worktree and pytest stays in the venv.
4. Use the same sanitized env and the same deselect.
5. One complete suite. Compare failure IDs to this receipt only.
