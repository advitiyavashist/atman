# T-1002 combined auth acceptance

Verdict: **ACCEPT**

Reviewed as one virtual integration on Atman `origin/main` at
`e201436ea72b21601c94811347ed90ea19cae705`:

1. PR #182 exact `411284df7fdc6620c286d442469b9938a0df731a`
2. PR #118 exact `cb0ed317cbecbcce688595ca354df6e4de460a64`

The resulting review tree is
`26092c89d350ad9ddc5507451f5e77464808fb49`.

## Evidence

`python3 -m pytest -q tests/test_t685_auth_v2_contract.py
tests/test_t686_auth_v2_probes.py tests/test_t687_auth_connect.py
tests/test_t991_stale_ready.py` passed **71/71** with localhost and process
inspection enabled.

A separate throwaway-board endpoint probe passed **1/1**. It enrolled a
persistent Cursor seat against a synthetic provider binary, obtained an
authoritative Ready response, removed that binary, and invoked the same
`_ui_auth_reconnect` path used by `POST /auth-reconnect`. The response and
stored record both became `unavailable`; `checked_at` advanced; `ready` was
false; `ran` remained false; and recovery told the operator to fix the runner,
binary, or seat identity and recheck.

The focused contracts also keep first-connect missing binaries,
unauthenticated responses, wrong-seat observations, sandbox observations, and
board-move observations fail closed. Configuration and delivery metadata do
not produce Ready; an authoritative response from the enrolled runner can.

The first sandboxed test invocation was not evidence: two localhost fixtures
were denied socket binding and one watcher process could not be inspected.
The identical bounded suite passed once those host capabilities were enabled.
No provider model was called, and no full suite was run.

## Merge boundary

The preview claim remains the source-prefix install proven by PR #182: `atm`
and the legacy `tickets` alias resolve to the same root `tickets.py`. This
acceptance does not establish packaged-wheel auth parity or a package-release
claim.
