# Delivering the shared CLI

Merging changes into `tickets` main does not install them. The production
entrypoint is `~/.local/bin/tickets`, currently pointing to
`~/.claude/tools/tickets.py`. After each approved CLI merge, the master installs
an exact reviewed commit using `scripts/install_live.py`. The legacy default
`install.sh` mode links a development checkout; do not use that mode for the
shared fleet. `install.sh --live-release ...` invokes the production installer.

## Stage, review drift, activate

From the tickets repository, choose the full reviewed commit SHA:

```sh
python3 scripts/install_live.py --ref <reviewed-full-sha>
```

This exports committed `tickets.py`, `ticket_coordination.py`, and
`board_backup.py` into `~/.claude/tools/tickets-releases/<sha>/` with a SHA-256
manifest. It ignores dirty checkout contents. It executes `--version`, creates
a disposable fixture ticket, and executes `show T-001` with an explicit
throwaway board and home. It never smoke-tests by creating live board tickets.
Staging does not switch the live entrypoint.

Before activation, compare the existing live file and its sibling modules with
the candidate. If live contains changes absent from main, obtain the master's
reviewed composition or merge those fixes first. **Do not erase a live-only fix
by installing plain main.** T-243's Git environment guard was such a patch when
T-223 was started. Its presence is not proof the rest of that live file matches
any revision. Checking marker names is not behavior verification.

After this review, hash the current live file, then explicitly acknowledge those
bytes when activating:

```sh
shasum -a 256 ~/.claude/tools/tickets.py
python3 scripts/install_live.py --ref <reviewed-full-sha> \
  --activate --expected-live-sha256 <reviewed-live-file-hash>
tickets --version
tickets --help
```

The first adoption and each subsequent replacement require the exact current
live hash. A mismatched or missing acknowledgement refuses replacement. The
flag is a concurrency/drift check, not a substitute for reviewing live-only
changes. `--live /absolute/path/tickets.py` permits isolated installations.
No acknowledgement is needed when creating a previously absent entrypoint.

## Drift check

Answer "is the fleet running the latest merged fix?" without grepping for
private symbols -- one line, comparing what the live launcher reports against
what `tickets` main actually is:

```sh
tickets --version
git -C ~/your-tickets-checkout rev-parse origin/main
```

If the two shas differ, the live tool is behind main by whatever landed since
the pinned commit was staged -- run the stage/review/activate steps above with
the new tip. Equal shas (or the pinned commit being main plus only its own
still-unmerged install commit, as immediately after this ticket's own release)
means the fleet is current. `tickets --version` failing to report a commit at
all (`uninstalled checkout`, `INVALID`, `DRIFTED`) means something other than
a stale pin -- see Provenance below.

## Process safety, provenance and recovery

The live Python launcher names an immutable release script by absolute path.
It execs that script before command execution. An old process therefore keeps
its old modules and child-command paths even while a new launcher is installed.
Only the launcher is atomically replaced; payload files are never overwritten
in place. A release with modified bytes is rejected, not silently repaired.
Installs serialize on a file lock; external hand edits still require coordination.
The existing mode is retained with owner execution enabled. The default mode
for a new launcher is 0755.

`--version` (also shown in existing `--help`) reports the installed commit and
verifies the three payload hashes. `DRIFTED` or `INVALID` means provenance has
failed. An uninstalled checkout is explicitly labeled uninstalled. The manifest
is local provenance, not a cryptographic signature or protection against a user
who can rewrite both it and the release.

Before activation the previous entrypoint is retained under
`tickets-releases/previous-<sha256>`. Both pre-activation and post-activation
smokes execute the script directly, checking executable mode and shebang as
well as command behavior. A failed post-activation smoke restores the previous
entrypoint atomically. The old files remain available for running sessions;
there is no automatic garbage collection.

To deliberately roll back a managed deployment, invoke the same installer with
the previous release's commit and the current launcher's expected hash. For a
legacy hand-maintained previous entrypoint, coordinate restoration with the
master using the preserved backup; do not hand-patch the live script. Existing
PATH symlinks and Claude hooks pointing to the live path require no changes.

## Behavioral verification and acceptance

`tests/test_live_install.py` installs into a temporary home and exercises both
T-215's two-repository/same-SHA closure attack and T-212's forced message rotation
and archive reads through the installed launcher. It also verifies refusal of
unknown live bytes, mode preservation, failed-activation rollback, dirty-source
exclusion, and installed payload drift reporting.

A successful isolated installer test is not proof the fleet was updated. Record
the activated full SHA, `tickets --version` output, and results of the same
behavioral probes against the actual live path (with an explicit disposable
board). Do not close T-223 before that final evidence exists. Never point tests
at the shared board or overwrite unreviewed live-only safety fixes to make an
installation look complete.
