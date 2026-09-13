# T-828 installed-wheel integration

Base: Atman `origin/main@3679053`. This branch integrates the accepted
`c5945c7` transfer behavior on top of the T-836 packaged checkin implementation.

The installed CLI does not load `tickets.py` from a checkout. Both entrypoints
use `ticket_board.identity_transfer` for identity snapshots, rollback, state
clearing, native endpoint removal and remote lease fencing. The shared module
uses the same board identity and remote-record locks as the existing commands.
Endpoint rollback preserves the original bytes and file permissions.

Join validates the replacement before mutation. A later exception restores the
identity files. A successful-transfer audit is published after the replacement
join commits, so a failed join cannot claim that the handover succeeded.
Same-provider joins and message history retain their prior behavior.

## Verification

System interpreter: Python **3.9.6**, pip **21.2.4**.

The wheel test builds with `pip wheel --no-deps --no-build-isolation`, installs
the resulting `ticket_board-0.2.0` wheel into a new virtual environment with
`pip install --no-index --no-deps`, and runs the installed `tickets` command
from a temporary git repository. It clears `PYTHONPATH` and verifies that
neither a checkout path nor a root `tickets` module is used.

```sh
PYTHONPATH=src:. python3 -m pytest -q -p no:cacheprovider \
  tests/test_t828_wheel_transfer.py \
  --basetemp=/private/tmp/atman-t828-wheel-install
```

Result: **3 passed**. The installed wheel proves invalid-transfer byte
preservation, committed native endpoint removal and remote lease/claim fencing,
and exact raw endpoint restoration after an injected late join failure.

```sh
PYTHONPATH=src:. python3 -m pytest -q -p no:cacheprovider \
  docs/reviews/t825/test_t825_adversarial.py \
  tests/test_t804_identity_isolation.py tests/test_t836_packaged_checkin.py \
  tests/test_byoa.py --basetemp=/private/tmp/atman-t828-final-source

PYTHONPATH=src:. python3 -m pytest -q -p no:cacheprovider \
  tests/test_t409_watch_teardown.py \
  --basetemp=/private/tmp/atman-t828-final-teardown
```

Watcher teardown requires host process visibility so its fixture reaper can
find detached test watchers. It passed **5/5** outside the sandbox.

The source/BYOA regression passed **63/63**. The explicit T-841 old-provider,
legacy-unscoped and mixed-entrypoint cases passed **5/5** with this command:

```sh
PYTHONPATH=src:. python3 -m pytest -q -p no:cacheprovider \
  docs/reviews/t825/test_t825_adversarial.py -k 'stale_provider or mixed_entrypoint' \
  --basetemp=/private/tmp/atman-t828-final-exact-gates
```

The T-841 transfer gates remain the review contract: invalid transfers leave
identity bytes unchanged; committed transfers revoke old transports;
old-provider and legacy unscoped limits cannot suppress ready authenticated
work; root and packaged concurrent joins remain isolated.
