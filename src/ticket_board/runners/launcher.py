"""Starting and resuming a Claude session, without a shell in the path.

Claude Code documents programmatic invocation at
https://code.claude.com/docs/en/headless and its flags at
https://code.claude.com/docs/en/cli-reference (both checked 2026-09-06). What
this module encodes, beyond assembling those flags:

**The message body never touches a command line.** It goes on stdin. A task
message is arbitrary operator- and agent-authored text; interpolating it into
a shell string is command injection with extra steps, and quoting it correctly
is a thing you get wrong once. `shell=False` plus a real argv plus stdin means
there is no shell to inject into at all.

**The binary is resolved and checked, not assumed.** T-181 lost a whole live
session to a hook command spelled `python`, on a machine whose only interpreter
is `python3`: the process died at exec time with 127 and every status field
still read healthy, because nothing distinguishes "installed" from "runnable"
until something actually runs. `resolve_claude()` is the same lesson applied to
the supervisor -- a missing `claude` is a concrete precondition failure with a
name, reported before any run is marked started.

**A managed session is never adopted from an interactive one.** The session id
is minted by this supervisor and used with `--session-id` for the first turn
and `--resume` afterwards. The design doc's rule -- never resume a session
simultaneously owned by an interactive terminal -- is kept structurally: the
supervisor only ever resumes ids it minted itself.

**TICKET_AGENT is stripped from the child environment.** A spawned Claude
inherits the parent's environment, and on this machine a `TICKET_AGENT` in it
makes the child's own Stop hook post to the live board under the *supervisor's*
identity. That is not a hypothetical; it happened during T-214. The child gets
an explicit environment with the board's own variables and without that one.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional

# Inherited variables that would let a child session act as somebody else, or
# resolve a different board than the one this run belongs to. Stripped rather
# than overwritten so a typo in a value cannot restore them.
STRIPPED_ENV = (
    "TICKET_AGENT",
    "TICKET_BOARD",
    "TICKETS_DIR",
    "CLAUDE_SESSION_ID",
)


class LaunchFailed(RuntimeError):
    """A precondition failed, named. Never a silent green start."""


@dataclass(frozen=True)
class LaunchSpec:
    # A UUID: `--session-id` is documented and enforced as "must be a valid
    # UUID". This is NOT the board's `ses_...` SessionId; see
    # `state.board_session_id` for the conversion between the two id spaces.
    session_id: str
    worktree: Path
    prompt: str
    resume: bool = False
    permission_policy: str = "prompt"
    timeout_seconds: Optional[int] = None


@dataclass(frozen=True)
class LaunchResult:
    started: bool
    returncode: Optional[int]
    stdout: str
    stderr: str
    reason: Optional[str] = None


def resolve_claude(explicit: Optional[str] = None) -> str:
    """The absolute path to the `claude` binary, or a named failure.

    Resolved once and stored, rather than left as the bare name `claude` on an
    argv: a supervisor is a long-lived process and its PATH is whatever it was
    started with, which on a launchd or systemd unit is close to nothing.
    """
    candidate = explicit or os.environ.get("CLAUDE_BIN") or "claude"
    found = candidate if os.path.isabs(candidate) else shutil.which(candidate)
    if not found or not os.access(found, os.X_OK):
        raise LaunchFailed(
            "No executable `claude` on this machine (looked for {!r}). A "
            "managed runner cannot start work without one; install Claude Code "
            "or set CLAUDE_BIN.".format(candidate))
    return found


def child_env(base: Optional[Mapping[str, str]] = None,
              extra: Optional[Mapping[str, str]] = None) -> Dict[str, str]:
    env = dict(os.environ if base is None else base)
    for name in STRIPPED_ENV:
        env.pop(name, None)
    env.update(extra or {})
    return env


# The permission policy the operator configured, expressed in the runtime's own
# flags. Nothing here widens what the operator allowed: `prompt` deliberately
# maps to no flag at all, so the runtime's default applies and a permission
# request surfaces and pauses the run rather than being auto-answered by the
# supervisor. Verified against the installed CLI's own `--permission-mode`
# choices rather than taken from the docs -- `preflight()` re-checks it, because
# a flag that a future version drops is a run that dies at exec with nothing to
# read.
_PERMISSION_FLAGS = {
    "prompt": (),
    "allowlist": ("--permission-mode", "acceptEdits"),
    "deny_all": ("--permission-mode", "plan"),
}

# The flags this supervisor actually emits, for `preflight()` to check.
REQUIRED_FLAGS = ("-p", "--session-id", "--resume", "--permission-mode")

def build_argv(binary: str, spec: LaunchSpec) -> List[str]:
    if spec.permission_policy not in _PERMISSION_FLAGS:
        raise LaunchFailed(
            "Unknown permission policy {!r}.".format(spec.permission_policy))
    argv = [binary, "-p"]
    argv.extend(["--resume", spec.session_id] if spec.resume
                else ["--session-id", spec.session_id])
    argv.extend(_PERMISSION_FLAGS[spec.permission_policy])
    # No `--max-turns`. Claude Code 2.1.263 has no such flag -- it was in an
    # earlier draft of this module, taken from the shape of the API rather than
    # from the installed binary, and it would have died at exec. The turn
    # budget is therefore carried on `RunBudget` and reported, but NOT enforced
    # here; only the time budget is, because only the time budget can be. Said
    # plainly in docs/managed-runner.md rather than left to be discovered.
    return argv


class LaunchHandle:
    """A live child process, split from its result on purpose.

    `started` and `finished` are different facts and the run's state machine
    needs them at different moments: `started` is reportable as soon as the
    process exists (that is what the <=5s acceptance target measures), while
    the outcome is only known when it exits. A launcher that only offered
    "run to completion" would force the supervisor to report a start that had
    already ended, which is the false-green shape the contract's 422 on
    `started` exists to prevent.
    """

    def __init__(self, process, spec: LaunchSpec):
        self._process = process
        self.spec = spec

    @property
    def pid(self) -> Optional[int]:
        return getattr(self._process, "pid", None)

    def wait(self, timeout: Optional[float] = None) -> LaunchResult:
        try:
            stdout, stderr = self._process.communicate(timeout=timeout)
        except subprocess.TimeoutExpired:
            self.terminate()
            return LaunchResult(True, None, "", "",
                                reason="Run exceeded its time budget.")
        code = self._process.returncode
        return LaunchResult(
            started=True, returncode=code,
            stdout=stdout or "", stderr=stderr or "",
            reason=None if code == 0 else "claude exited {}".format(code))

    def terminate(self) -> None:
        """Cooperative first, then final. Artifacts are never deleted.

        Cancel is cooperative by contract, so the child gets a TERM and a grace
        period to write out whatever it was holding. The KILL is the backstop
        for a child that ignores TERM, not the first move.
        """
        if self._process.poll() is not None:
            return
        try:
            self._process.terminate()
            self._process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            self._process.kill()
        except OSError:
            pass


class ClaudeLauncher:
    """Spawns real `claude` processes. Tests substitute a fake for this."""

    def __init__(self, binary: Optional[str] = None,
                 env: Optional[Mapping[str, str]] = None):
        self.binary = resolve_claude(binary)
        self.env = child_env(extra=env)

    def start(self, spec: LaunchSpec) -> LaunchHandle:
        worktree = Path(spec.worktree)
        if not worktree.is_dir():
            raise LaunchFailed(
                "Allowlisted worktree {} does not exist.".format(worktree))
        argv = build_argv(self.binary, spec)
        try:
            process = subprocess.Popen(
                argv,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                cwd=str(worktree),
                env=self.env,
                text=True,
                shell=False,
            )
        except OSError as exc:
            # exec failed: the binary vanished, or the worktree is not
            # executable. Concrete, and never reported as a started run.
            raise LaunchFailed("Could not start claude: {}".format(exc))
        # The body goes down stdin and the pipe is closed, so the child sees
        # EOF and does not wait for more input. It never appears on an argv.
        try:
            process.stdin.write(spec.prompt)
            process.stdin.close()
        except (BrokenPipeError, OSError):
            pass
        finally:
            # communicate() flushes any stdin the Popen still holds; on a
            # closed pipe that is `ValueError: I/O operation on closed file`,
            # and it crashed every real run at wait() (T-190 F-12). Dropping
            # the reference tells communicate() there is no stdin to manage.
            process.stdin = None
        return LaunchHandle(process, spec)

    def launch(self, spec: LaunchSpec) -> LaunchResult:
        """start() + wait(), for callers that do not need the two apart."""
        return self.start(spec).wait(timeout=spec.timeout_seconds)


def is_process_alive(pid: Optional[int]) -> bool:
    """Whether a pid recorded before a crash is still running.

    Deliberately conservative about what it can prove. `kill(pid, 0)` answers
    "a process with this id exists and I may signal it", not "it is the process
    I started" -- pids are reused. The supervisor therefore treats a live pid
    as a reason to *not* start a second process and to reconcile instead, which
    is safe under reuse; it never treats it as proof the original run is fine.
    """
    if not pid:
        return False
    try:
        os.kill(int(pid), 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except (TypeError, ValueError, OSError):
        return False
    return True


def preflight(binary: Optional[str] = None) -> Dict[str, Any]:
    """Prove the installed `claude` accepts the flags this supervisor sends.

    T-181's lesson was that *installed* and *runnable* are different facts and
    only execution separates them. This is the next one along: runnable and
    *compatible* are also different. `--max-turns` looked entirely reasonable
    and does not exist; `--session-id` takes a UUID and not the board's own
    session id. Both would have failed at spawn time, and a hook or a
    supervisor that dies at exec leaves nothing to read.

    So this asks the binary, not the documentation: `--version` for the record,
    and `--help` for the flags. Cheap, and it starts no session.
    """
    report: Dict[str, Any] = {"binary": None, "version": None,
                              "missing_flags": [], "error": None}
    try:
        report["binary"] = resolve_claude(binary)
    except LaunchFailed as exc:
        report["error"] = str(exc)
        return report
    try:
        version = subprocess.run([report["binary"], "--version"],
                                 capture_output=True, text=True, timeout=20,
                                 check=False)
        report["version"] = (version.stdout or "").strip() or None
        helped = subprocess.run([report["binary"], "--help"],
                                capture_output=True, text=True, timeout=20,
                                check=False)
    except (OSError, subprocess.SubprocessError) as exc:
        report["error"] = "Could not interrogate claude: {}".format(exc)
        return report
    text = (helped.stdout or "") + (helped.stderr or "")
    report["missing_flags"] = [f for f in REQUIRED_FLAGS if f not in text]
    if report["missing_flags"]:
        report["error"] = (
            "The installed claude does not advertise {}. This supervisor would "
            "fail at spawn time.".format(", ".join(report["missing_flags"])))
    return report
