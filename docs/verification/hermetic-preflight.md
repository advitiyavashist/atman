# Hermetic verification preflight (T-909)

Future acceptance suites (T-911 and later candidate compares) must use this
recipe. Do not reuse T-877 / T-896 operator-HOME logs or the T-891 full-suite
receipt as an equivalent environment.

T-891 ran `/usr/bin/python3 -m pytest` with pytest 8.4.2 loaded from operator
user-site via `PYTHONPATH`. Hermetic `HOME`/`TMPDIR` then dropped that user-site
for children, so many subprocesses raised `ModuleNotFoundError: ticket_board` or
`No module named pytest`. Those failures are an environment fault, not a
production defect and not a waiver.

## Distinctions that stay true

| Artifact | SHA | Role |
|---|---|---|
| T-891 raw receipt | `f19bdab093f14930e990141eaf5c8eed4858de93` | Complete selected suite only. Digest `55b014fde225dcbc4af1c8e9bf12c25492f18aeb631cb6d1066a1d29e4b767ce`. 61 failed / 2072 passed. Not ACCEPT. |
| Post-run pin | `bac7cb8b00c14237db2fb23d41961f7f285c881c` | T-890 test-literal repair plus public docs. Not full-suite evidence. |
| T-877 candidate | `26ff79f1612bf3e756a91d30bfad46f0f9d29d16` | Incomplete operator-HOME run. Not a matched baseline. |
| T-896 candidate | `c2f7dd59009451da5d7667c2c1930a10af12d884` | Incomplete operator-HOME run. Not a matched baseline. |

## Command

Zero-model. No provider sessions. No live board actors. Private receipts stay
under the workdir; commit only the public manifest beside this page.

```sh
/usr/bin/python3 scripts/hermetic_preflight.py \
  --python /usr/bin/python3 \
  --repo . \
  --sha f19bdab093f14930e990141eaf5c8eed4858de93 \
  --sha c2f7dd59009451da5d7667c2c1930a10af12d884 \
  --sha 26ff79f1612bf3e756a91d30bfad46f0f9d29d16 \
  --workdir /tmp/atman-hermetic-preflight \
  --manifest-out docs/verification/hermetic-preflight.manifest.json
```

The script:

1. Creates a fresh venv from the requested interpreter (prefer Python 3.9.6).
2. Installs pinned `pytest==8.4.2` plus declared contracts extras into that
   venv (`scripts/requirements-preflight.txt` records the freeze).
3. Installs the selected tree (`pip install -e --no-deps`) and a no-deps wheel
   into a second venv.
4. Runs `--help` on root `tickets.py`, `src/ticket_board/cli.py`, and the
   installed `tickets` console (and `atm` when the wheel provides it).
5. Spawns a child with throwaway `HOME`/`TMPDIR`, `PYTHONNOUSERSITE=1`, no
   `PYTHONPATH`, and provider/`TICKET*` names stripped.
6. Collects only `tests/test_t263_init_isolation.py` and
   `tests/test_t836_clean_wheel.py`. Wheel collection overrides
   `pytest.ini` `pythonpath` so `ticket_board` cannot come from `src/`.

Pass means every origin resolved `ticket_board` and `pytest` from the selected
tree or its venv, never operator user-site or another SHA. It does not mean the
full suite is green.

## Fixture versus packaging

`tests/test_t544_conftest_shadow.py` sets `PYTHONPATH=src:.` on purpose. That
binds a combined runners+server collect to the checkout. It is a fixture
contract, not a wheel defect. Direct-file `cli.py` is a supported two-copy
entry. Do not file a packaging ticket from T-891 child import errors alone.

## Reuse

T-911 and candidate verifiers must:

- invoke this script (or the same env it records) before any full suite
- use the venv absolute interpreter for parent and children
- keep sanitized `HOME`/`TMPDIR` and no operator `PYTHONPATH`
- compare failure IDs only across receipts that share this manifest
