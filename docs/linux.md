# Atman on Linux (T-866)

What is supported on Linux, what was actually run to say so, and what is
only labelled. Target: a clean Ubuntu 24.04 machine (or container) gets from
nothing to `atm join` and the local app's first win.

## Install paths

| Path | Linux status | How it is proven |
|---|---|---|
| Checkout: `git clone` + `./install.sh` | Supported | `packaging/smoke.sh checkout` in CI (`ubuntu-latest`, `macos-latest`) and in `packaging/linux/Dockerfile.acceptance` (clean Ubuntu 24.04, arm64 and x86_64) |
| Homebrew on Linux: `brew install .../atman` | Supported (x86_64; Homebrew itself does not support Linux arm64) | CI `brew-formula` job installs and `brew test`s the real `packaging/homebrew/atman.rb` from a tarball built at the commit |
| pipx: `pipx install git+https://github.com/advitiyavashist/atman.git` | Supported for the packaged subset | `packaging/smoke.sh pipx` in the container build; `smoke.sh wheel` in CI |

The packaged subset (wheel/pipx) is `ticket_board.cli:main`: board commands
(`join`, `who`, `next`, `msg`, `review`, ...) work; `atm ui`, `atm --version`,
`hooks`, `watch`/`spawn` and release verification are checkout/formula only
(E-016 tracks closing that gap). `smoke.sh` prints exactly that line so a
reader of the log is not misled.

## First win

```sh
sh packaging/smoke.sh checkout
# smoke[checkout]: atm join + tickets who: seat smoke-seat is on the board at .../project/.tickets
# smoke: atm ui answered /board.json on 127.0.0.1:<port> (counts={'total': 0, 'done': 0})
# smoke[checkout]: FIRST WIN: install -> atm join -> atm ui health
```

Everything lands under `$SMOKE_WORK` (its own HOME, board and bin/); the
script never reads the caller's board, hooks or cache. The same file runs
the clean-container acceptance:

```sh
docker build -f packaging/linux/Dockerfile.acceptance -t atman-linux-acceptance .
```

A successful build is the proof: every `RUN` is an acceptance step.

## Platform pieces the ticket named

| Piece | Linux behaviour | Verified by |
|---|---|---|
| File locking (atomic claims, agent records) | `fcntl.flock`, native on Linux; same code path as macOS | full suite on `ubuntu-latest`; flock sanity in the acceptance image |
| AF_UNIX wake sockets (Claude injector, Codex app-server control, Cursor ACP control) | Same socket code; Linux `sun_path` limit is 108 bytes vs 104 on macOS, so any endpoint path that works on macOS fits | `tests/test_t683_session_adapters.py`, `tests/test_t857_*` on Linux; AF_UNIX round-trip in the acceptance image |
| Default app port | `atm ui` binds `127.0.0.1:8765` (`--port`/`--host` to change); localhost-only | `/board.json` first-win probe on Linux and macOS |
| `atm ui --open` | `xdg-open` when present, else the stdlib `webbrowser` (`$BROWSER`); `open` stays macOS-only | `tests/test_t866_linux_support.py` |
| Cache paths | `$TICKETS_CACHE_DIR` > `$XDG_CACHE_HOME/atman` > `~/.cache/atman`, one resolver for session endpoints and auth profiles | `tests/test_t866_linux_support.py`; XDG check in the acceptance image |
| Process scans (`spawn --stop`, duplicate-loop guard, desk pytest gate) | `ps -ww`/`-axww` everywhere: procps clips argv to 80 columns without it when there is no tty | `tests/test_t866_linux_support.py` (static sweep + live check); t409/t427/t554/t559 green on Linux |
| Test fixtures' git default branch | pinned to `main` in `tests/conftest.py` (`GIT_CONFIG_COUNT`); macOS only ever passed because Xcode's system gitconfig sets it | t422/t merge-identity suites green on Linux |

## Agent adapters on Linux: labelled, not claimed

Harness discovery is PATH-based (`agent`/`cursor-agent`, `agy`, `claude`,
`codex`, `devin`, `gemini`); none of it branches on the OS. What this ticket
did **not** do is run a live provider session on Linux, so the wake paths are
labelled by what was exercised:

| Adapter | On Linux | Status label |
|---|---|---|
| Claude Code hooks + AF_UNIX injector socket | socket path comes from the harness env; protocol tests pass on Linux | protocol verified on Linux; live session verified on macOS only |
| Codex app-server control socket (`$CODEX_HOME/app-server-control/`) | path logic portable; WebSocket upgrade tests pass on Linux | protocol verified on Linux; live session verified on macOS only |
| Cursor ACP control socket (`~/.cursor/acp-control/`) and tmux persist | portable; tmux is a Linux staple | live session verified on macOS only |
| Antigravity (`agy`), Devin, Gemini CLI, Grok (Cursor persist) | prompt-file harnesses, no native adapter; whether the vendor ships a Linux binary is the vendor's call | not exercised on Linux in this ticket |
| Remote bridge (schema-2) | platform-neutral HTTP | unchanged |

If you run one of these live on Linux, change the label in this table in the
same PR and say what you ran.

## Known CI failures that are not Linux-specific

After T-866 the `ubuntu-latest` job still reports the same set the macOS
baseline (T-877/T-911 receipts) reports: `test_t278_agent_record_race`
(timing, 4 cases), `test_t611_objective_bounds`, `test_t778_master_onboarding`,
`test_t784_team_intro`, `test_trajectories::test_claim_update_review_done_are_all_recorded`.
They fail identically on macOS and belong to their own tickets; do not read
them as a Linux regression.
