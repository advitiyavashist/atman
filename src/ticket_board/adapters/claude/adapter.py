"""Fixture-driven Claude Code hook adapter for the Ticket Board V1 contract.

This module deliberately contains no live Claude integration.  It gives T-181
the reusable pieces that can be driven by captured hook payloads later, while
keeping T-201 safe to test in scratch project directories only.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shlex
import subprocess
import sys
import time
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List, Mapping, Optional, Tuple
from urllib import error, request

from ticket_board.git_env import clean_git_env

try:
    import pwd
except ImportError:  # pragma: no cover - Claude Code itself is POSIX, but keep imports portable.
    pwd = None


ALLOWED_CLAUDE_EVENTS = {
    "SessionStart",
    "UserPromptSubmit",
    "PreToolUse",
    "PostToolUse",
    "Stop",
    "SubagentStop",
    "Notification",
}

CONTRACT_KIND_BY_CLAUDE_EVENT = {
    "SessionStart": "session_start",
    "UserPromptSubmit": "user_prompt_submit",
    "Stop": "stop",
    "SubagentStop": "stop",
    # The frozen contract has no tool/notification kinds yet.  These events are
    # delivered as bounded activity probes without claiming session adoption.
    "PreToolUse": "probe",
    "PostToolUse": "probe",
    "Notification": "probe",
}

SENSITIVE_FIELDS = {
    "prompt",
    "tool_input",
    "tool_output",
    "tool_response",
    "transcript",
    "transcript_path",
    "environment",
    "env",
    "credentials",
    "api_key",
    "token",
}

SECRET_RE = re.compile(
    r"(sk-[A-Za-z0-9_-]{8,}|ghp_[A-Za-z0-9_]{8,}|Bearer\s+[A-Za-z0-9._-]{8,}|"
    r"eyJ[A-Za-z0-9._-]{12,}|AKIA[0-9A-Z]{16})"
)
FORBIDDEN_ROOTS_ENV = "TICKET_BOARD_FORBIDDEN_ROOTS"
# A shell can turn an unset variable into an empty string by accident
# (``VAR="$SOME_UNSET_VAR"``), so an empty value must not be read as a
# deliberate opt-out. Only this literal, which no accidental expansion
# produces, disables the guard.
FORBIDDEN_ROOTS_DISABLE_SENTINEL = "NONE"


class ClaudeHookError(ValueError):
    """Raised when a Claude hook payload cannot be safely converted."""


class SpoolFull(RuntimeError):
    """Raised when offline delivery would exceed the configured spool cap."""


class GitProbeInconclusive(RuntimeError):
    """Raised when git exits non-zero for a reason other than "not a repository".

    A guard that reads any non-zero exit as "not a repository" cannot tell a
    healthy checkout with a broken ``~/.gitconfig`` or ``$XDG_CONFIG_HOME/git/config``
    (neither stripped by :func:`clean_git_env`, which only removes ``GIT_*``) from
    an ordinary non-repository directory. Only the latter is a negative result;
    the former is indeterminate and must not be treated as one.
    """


@dataclass(frozen=True)
class AdapterConfig:
    project_id: str
    server_url: str
    max_event_bytes: int = 64 * 1024
    max_status_chars: int = 180
    spool_cap: int = 25
    retries: int = 2
    backoff_seconds: float = 0.01


@dataclass(frozen=True)
class Enrollment:
    project_id: str
    server_url: str
    agent_id: str
    agent_name: str
    session_id: str
    token: str
    created_at: str
    lease_version: int


@dataclass(frozen=True)
class OperatorCredentials:
    """An operator session, which is a different principal from an agent token.

    The frozen contract makes `DELETE /agents/{id}/session-lease` operator- or
    master-only, so lease revocation cannot be done with the agent's own bearer
    token no matter how natural that reads from the adapter's side.
    """

    session_token: str
    csrf_token: str
    origin: str


@dataclass(frozen=True)
class DeliveryResult:
    delivered: bool
    spooled: bool
    status_code: Optional[int]
    response: Optional[Dict[str, Any]]
    error: Optional[str] = None


@dataclass(frozen=True)
class DoctorReport:
    config_installed: bool
    server_received: bool
    response_delivered: bool
    session_adopted: bool
    remediation: List[str]
    # Defaulted so the fixture-driven `diagnose()` signature from T-201 keeps
    # working unchanged; only the live path populates them.
    hook_executable: Optional[bool] = None
    hook_executed: Optional[bool] = None

    def as_dict(self) -> Dict[str, Any]:
        return {
            "config": {
                "installed": self.config_installed,
                # Installed and runnable are different facts. A hook command
                # naming an interpreter that does not exist is installed and
                # will never run, and nothing else in this report can see that.
                "hook_executable": self.hook_executable,
            },
            "delivery": {
                "hook_executed": self.hook_executed,
                "server_received": self.server_received,
                "response_delivered": self.response_delivered,
            },
            "adoption": {"session_adopted": self.session_adopted},
            "remediation": list(self.remediation),
        }


def utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _json_size(payload: Mapping[str, Any]) -> int:
    return len(json.dumps(payload, separators=(",", ":"), sort_keys=True).encode("utf-8"))


def _bounded_status(text: Optional[str], limit: int) -> Optional[str]:
    if not text:
        return None
    clean = SECRET_RE.sub("<redacted>", str(text).replace("\n", " ").strip())
    if len(clean) <= limit:
        return clean
    return clean[: max(0, limit - 1)].rstrip() + "..."


def _required_string(payload: Mapping[str, Any], *names: str) -> str:
    for name in names:
        value = payload.get(name)
        if isinstance(value, str) and value:
            return value
    raise ClaudeHookError("missing required field: %s" % " or ".join(names))


# Wire fields that identify WHICH event this is, as opposed to what happened in
# it.  Every one is an opaque correlation id or a small enum -- none of them is
# prompt text, tool input or tool output, so digesting them cannot leak content
# (`_assert_no_sensitive_fields` still guards the envelope itself).
#
# `tool_use_id` is the load-bearing one: Claude Code emits the same value on the
# PreToolUse and PostToolUse of a single call and a fresh one per call, so it is
# exactly the discriminator a per-call event id needs.
CORRELATION_FIELDS = (
    "tool_use_id",
    "prompt_id",
    "source",
    "stop_hook_active",
    "agent_id",
    "agent_type",
)


def _correlation_digest(payload: Mapping[str, Any]) -> str:
    """Stable digest of the wire's identity fields, or "" when it carries none."""
    present = {
        name: payload[name]
        for name in CORRELATION_FIELDS
        if name in payload and isinstance(payload[name], (str, int, float, bool))
    }
    if not present:
        return ""
    return hashlib.sha256(
        json.dumps(present, separators=(",", ":"), sort_keys=True).encode("utf-8")
    ).hexdigest()[:16]


def _event_id(
    agent_id: str,
    session_id: str,
    event_name: str,
    occurred_at: str,
    correlation: str = "",
) -> str:
    """Identify one hook event.

    `correlation` is what makes this per-event rather than per-second.  Real
    payloads carry no timestamp (T-214 ground truth), so `occurred_at` falls back
    to `utc_now()` at one-second resolution; without a correlation term every
    tool call inside the same second hashes to the same id and the server's
    `event_id` dedupe -- which is required behaviour, not a bug -- silently drops
    all but the first as retries.  Digesting the wire's own correlation ids keeps
    a genuine retry stable (identical payload -> identical id) while separating
    distinct calls.
    """
    raw = "%s:%s:%s:%s:%s" % (agent_id, session_id, event_name, occurred_at, correlation)
    return "hev_" + hashlib.sha256(raw.encode("utf-8")).hexdigest()[:24]


def parse_claude_hook_event(
    payload: Mapping[str, Any],
    enrollment: Enrollment,
    config: AdapterConfig,
    *,
    synthetic_probe: bool = False,
) -> Dict[str, Any]:
    """Convert a Claude Code hook payload into the frozen HookEvent schema.

    The returned object is the `event` value inside `HookEventRequest`.  Raw
    prompt and tool payload fields are ignored even when present.
    """

    if _json_size(payload) > config.max_event_bytes:
        raise ClaudeHookError("hook payload is larger than the configured limit")

    schema_version = payload.get("hook_schema_version")
    if schema_version not in (None, "1", 1, "claude-code-hooks-v1"):
        raise ClaudeHookError("unsupported Claude hook schema version")

    event_name = _required_string(payload, "hook_event_name", "event_name")
    if event_name not in ALLOWED_CLAUDE_EVENTS:
        raise ClaudeHookError("unsupported Claude hook event: %s" % event_name)

    session_id = _required_string(payload, "session_id")
    occurred_at = payload.get("occurred_at") or payload.get("timestamp") or utc_now()
    if not isinstance(occurred_at, str):
        raise ClaudeHookError("occurred_at must be a timestamp string")

    cwd = payload.get("cwd")
    if cwd is not None and not isinstance(cwd, str):
        raise ClaudeHookError("cwd must be a string or null")
    if isinstance(cwd, str) and len(cwd) > 300:
        cwd = cwd[:300]

    kind = "probe" if synthetic_probe else CONTRACT_KIND_BY_CLAUDE_EVENT[event_name]
    note = _status_note(payload, event_name, config.max_status_chars)

    return {
        "event_id": _event_id(
            enrollment.agent_id,
            session_id,
            event_name,
            occurred_at,
            _correlation_digest(payload),
        ),
        "agent_id": enrollment.agent_id,
        "session_id": session_id,
        "kind": kind,
        "occurred_at": occurred_at,
        "cwd": cwd,
        "note": note,
    }


def _status_note(payload: Mapping[str, Any], event_name: str, limit: int) -> Optional[str]:
    if event_name in ("PreToolUse", "PostToolUse"):
        tool_name = payload.get("tool_name") or payload.get("name")
        if isinstance(tool_name, str) and tool_name:
            return _bounded_status("%s: %s" % (event_name, tool_name), limit)
        return _bounded_status(event_name, limit)
    if event_name == "Notification":
        return _bounded_status(payload.get("message") or payload.get("status"), limit)
    if event_name == "SubagentStop":
        return _bounded_status("Subagent turn finished", limit)
    return None


def build_hook_envelope(event: Mapping[str, Any], request_id: Optional[str] = None) -> Dict[str, Any]:
    envelope = {
        "request_id": request_id or str(uuid.uuid4()),
        "event": dict(event),
    }
    forbidden = set(envelope) & {"actor"}
    if forbidden:
        raise ClaudeHookError("actor is bound from the credential, not the body")
    _assert_no_sensitive_fields(envelope)
    return envelope


def _assert_no_sensitive_fields(value: Any, path: Tuple[str, ...] = ()) -> None:
    if isinstance(value, Mapping):
        for key, nested in value.items():
            key_text = str(key)
            if key_text in SENSITIVE_FIELDS:
                raise ClaudeHookError("sensitive field cannot be forwarded: %s" % ".".join(path + (key_text,)))
            _assert_no_sensitive_fields(nested, path + (key_text,))
    elif isinstance(value, list):
        for index, nested in enumerate(value):
            _assert_no_sensitive_fields(nested, path + (str(index),))


def enrollment_path(project_dir: Path) -> Path:
    return project_dir / ".ticket-board" / "enrollment.json"


def save_enrollment(project_dir: Path, enrollment: Enrollment) -> Path:
    _ensure_safe_project_dir(project_dir)
    path = enrollment_path(project_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(enrollment.__dict__, indent=2, sort_keys=True) + "\n")
    os.chmod(path, 0o600)
    return path


def load_enrollment(project_dir: Path) -> Enrollment:
    path = enrollment_path(project_dir)
    if not path.exists():
        raise ClaudeHookError(
            "missing project enrollment file; refusing env-only identity for Ticket Board hooks"
        )
    data = json.loads(path.read_text())
    return Enrollment(
        project_id=str(data["project_id"]),
        server_url=str(data["server_url"]),
        agent_id=str(data["agent_id"]),
        agent_name=str(data["agent_name"]),
        session_id=str(data["session_id"]),
        token=str(data["token"]),
        created_at=str(data["created_at"]),
        lease_version=int(data["lease_version"]),
    )


def exchange_enrollment(
    client: "BoardClient",
    project_dir: Path,
    *,
    code: str,
    session_id: str,
    runtime_version: str,
) -> Enrollment:
    # Check the destination BEFORE spending the code. `save_enrollment` guards
    # the same path, but it runs after the exchange, so a refused project dir
    # would burn a single-use enrollment code and force the operator to mint a
    # new one -- and would do it only after talking to the board about a target
    # we had already decided was forbidden.
    _ensure_safe_project_dir(project_dir)

    body = {
        "request_id": str(uuid.uuid4()),
        "code": code,
        "session_id": session_id,
        "runtime": {"adapter": "claude_code", "version": runtime_version},
    }
    response = client.post_json("/sessions", body, token=None, expected_status=(201,))
    agent = response["agent"]
    lease = response["lease"]
    enrollment = Enrollment(
        project_id=agent["project_id"],
        server_url=client.base_url,
        agent_id=agent["id"],
        agent_name=agent["name"],
        session_id=lease["session_id"],
        token=response["token"],
        created_at=utc_now(),
        lease_version=int(lease["version"]),
    )
    save_enrollment(project_dir, enrollment)
    return enrollment


def _ensure_safe_project_dir(project_dir: Path) -> None:
    resolved = project_dir.resolve(strict=False)
    home = _real_home_dir()
    user_claude_dir = (home / ".claude").resolve(strict=False)
    target_settings = (resolved / ".claude" / "settings.json").resolve(strict=False)
    user_settings = (user_claude_dir / "settings.json").resolve(strict=False)

    if _same_path(resolved, home):
        raise ClaudeHookError("refusing to write user-level Claude settings: %s" % target_settings)
    if _path_is_or_under(resolved, user_claude_dir):
        raise ClaudeHookError("refusing to use user-level Claude config as a project dir: %s" % resolved)
    if _same_path(target_settings, user_settings) or _path_is_or_under(target_settings, user_claude_dir):
        raise ClaudeHookError("refusing to write user-level Claude settings: %s" % target_settings)

    # Protected roots are checkouts that live agents are already working out of.
    # Two independent checks, because they degrade differently:
    #   1. path containment -- needs no git, and is the floor: it holds even when
    #      git is missing, and it also covers roots that are not git checkouts at
    #      all (a bare `.worktrees` directory).
    #   2. repository identity -- catches a worktree of a protected checkout that
    #      was registered somewhere else entirely, where no path comparison can
    #      see it. Requires git, so it is a superset, never the only line.
    protected = _protected_roots()
    for root in protected:
        if _path_is_or_under(resolved, root):
            raise ClaudeHookError(
                "refusing to enroll a live agent worktree: %s is inside the protected checkout %s" % (resolved, root)
            )

    try:
        repo = _git_common_dir(resolved)
    except GitProbeInconclusive as exc:
        raise ClaudeHookError(
            "refusing to enroll: could not determine whether %s is a git repository: %s" % (resolved, exc)
        ) from exc
    if repo is not None:
        for root in protected:
            try:
                root_repo = _git_common_dir(root)
            except GitProbeInconclusive as exc:
                raise ClaudeHookError(
                    "refusing to enroll: could not determine whether protected root %s is a git repository: %s"
                    % (root, exc)
                ) from exc
            if root_repo is not None and _same_path(repo, root_repo):
                raise ClaudeHookError(
                    "refusing to enroll a live agent worktree: %s shares the git repository %s with the protected "
                    "checkout %s" % (resolved, repo, root)
                )


def _real_home_dir() -> Path:
    if pwd is not None:
        try:
            return Path(pwd.getpwuid(os.getuid()).pw_dir).resolve(strict=False)
        except (KeyError, OSError):
            pass
    return Path.home().resolve(strict=False)


def _protected_roots() -> Tuple[Path, ...]:
    """Checkouts that must never be enrolled, from configuration -- never hardcoded.

    ``TICKET_BOARD_FORBIDDEN_ROOTS`` is an os.pathsep-separated list and wins when
    set. Unset means no extra protected roots (public default). Operators who
    need host-specific checkouts protected should set the variable.

    An empty string is NOT treated as "protect nothing": a shell produces an
    empty value by accident (``VAR="$UNSET_VAR"``) far more often than an
    operator deliberately opts out, and unlike every other malformed value it
    used to disable the guard without so much as a log line. Deliberate
    opt-out requires the literal sentinel below, which no accidental
    expansion produces.
    """
    raw = os.environ.get(FORBIDDEN_ROOTS_ENV)
    if raw is not None:
        if raw == FORBIDDEN_ROOTS_DISABLE_SENTINEL:
            return ()  # Explicit opt-out, spelled so it can't happen by accident.
        roots = []
        for part in raw.split(os.pathsep):
            try:
                if not part or not part.strip():
                    raise ValueError("empty entry")
                root = Path(part).expanduser()
                if not root.is_absolute():
                    raise ValueError("entry must be absolute (or start with ~)")
                root = root.resolve(strict=True)
                # Opening the directory checks both directory type and access.
                with os.scandir(root) as entries:
                    next(entries, None)
            except (OSError, ValueError, RuntimeError) as exc:
                raise ClaudeHookError(
                    "%s has an invalid or unreadable root %r: %s" %
                    (FORBIDDEN_ROOTS_ENV, part, exc)
                ) from exc
            roots.append(root)
        return tuple(roots)

    return ()


def _git_common_dir(path: Path) -> Optional[Path]:
    """Identify the repository ``path`` belongs to, or None if it is not in one.

    ``--git-common-dir`` is the shared repository directory: a checkout and every
    worktree registered against it report the same one no matter where on disk the
    worktree sits, which is what makes this a structural check and not another
    path list. Git prints it relative to the directory the command ran in.

    Raises :class:`GitProbeInconclusive` when git exits non-zero for a reason
    other than "not a repository" -- for example a malformed ``~/.gitconfig``
    or ``$XDG_CONFIG_HOME/git/config``. Reading that kind of error as a plain
    None would tell the caller "not a repository" when the honest answer is
    "could not tell", and this probe backs a security guard that must not
    silently degrade to the open state.
    """
    if not path.exists():
        return None
    # LC_ALL/LANGUAGE=C pin git's stderr to English regardless of the caller's
    # locale: the "not a repository" match below is a text match, and a
    # translated message would otherwise misclassify an ordinary non-repo
    # directory as an inconclusive probe instead of a plain negative.
    probe_env = dict(clean_git_env())
    probe_env["LC_ALL"] = "C"
    probe_env["LANGUAGE"] = "C"
    try:
        result = subprocess.run(
            ["git", "-C", str(path), "rev-parse", "--git-common-dir"],
            env=probe_env,
            capture_output=True,
            check=False,
            text=True,
            timeout=5,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if result.returncode != 0:
        stderr = (result.stderr or "").strip()
        if "not a git repository" in stderr.lower():
            return None
        raise GitProbeInconclusive(
            "git -C %s rev-parse --git-common-dir exited %d: %s"
            % (path, result.returncode, stderr or "<no stderr>")
        )
    common = result.stdout.strip()
    if not common:
        return None
    return Path(os.path.join(str(path), common)).resolve(strict=False)


def _same_path(left: Path, right: Path) -> bool:
    try:
        return os.path.samefile(left, right)
    except OSError:
        return left == right


def _path_is_or_under(path: Path, root: Path) -> bool:
    if _same_path(path, root):
        return True
    try:
        path.relative_to(root)
        return True
    except ValueError:
        pass
    return any(_same_path(parent, root) for parent in path.parents)


OWNER_MARKER = "ticket-board-hook"
HOOK_EVENTS = ("SessionStart", "UserPromptSubmit", "PreToolUse", "PostToolUse", "Stop", "SubagentStop", "Notification")


def install_hooks(project_dir: Path, enrollment: Enrollment, config: AdapterConfig) -> Path:
    _ensure_safe_project_dir(project_dir)
    settings_path = project_dir / ".claude" / "settings.json"
    settings = _load_settings(settings_path)
    hooks = settings.setdefault("hooks", {})
    command = _hook_command(config.project_id, config.server_url, enrollment.agent_id)

    for event_name in HOOK_EVENTS:
        entries = hooks.setdefault(event_name, [])
        if not _has_owned_hook(entries, enrollment.agent_id):
            entries.append(
                {
                    "matcher": "",
                    "hooks": [
                        {
                            "type": "command",
                            "command": command,
                            "ticket_board_owner": enrollment.agent_id,
                        }
                    ],
                }
            )

    settings_path.parent.mkdir(parents=True, exist_ok=True)
    _write_settings(settings_path, settings)
    return settings_path


def _write_settings(settings_path: Path, settings: Mapping[str, Any]) -> None:
    """Write project settings without reformatting anything we do not own.

    No `sort_keys`: `json.load` preserves document order, so a round trip leaves
    an operator's own keys where they wrote them.  Sorting reorders the whole
    file on every install/uninstall, which makes "we touched nothing of yours"
    impossible to demonstrate with a diff -- and a diff is the evidence this
    ticket has to produce.
    """
    rendered = json.dumps(settings, indent=2) + "\n"
    if settings_path.exists() and settings_path.read_text() == rendered:
        return  # nothing of ours to change: leave the operator's file alone
    settings_path.write_text(rendered)


def uninstall_hooks(project_dir: Path, enrollment: Enrollment) -> Path:
    _ensure_safe_project_dir(project_dir)
    settings_path = project_dir / ".claude" / "settings.json"
    settings = _load_settings(settings_path)
    hooks = settings.get("hooks")
    if not isinstance(hooks, dict):
        return settings_path

    for event_name in list(hooks.keys()):
        entries = hooks[event_name]
        if not isinstance(entries, list):
            continue
        kept = [_without_owned_hooks(entry, enrollment.agent_id) for entry in entries]
        hooks[event_name] = [entry for entry in kept if entry is not None]
        if not hooks[event_name]:
            del hooks[event_name]
    if not hooks:
        # We added the `hooks` key on a project that had none; take it back out
        # rather than leaving an empty object behind as a footprint.
        if not settings.get("hooks"):
            del settings["hooks"]

    if not settings_path.exists():
        return settings_path  # nothing installed here; do not create a file
    settings_path.parent.mkdir(parents=True, exist_ok=True)
    _write_settings(settings_path, settings)
    return settings_path


def _load_settings(settings_path: Path) -> Dict[str, Any]:
    if not settings_path.exists():
        return {}
    data = json.loads(settings_path.read_text())
    if not isinstance(data, dict):
        raise ClaudeHookError("Claude project settings must be a JSON object")
    return data


def hook_runtime() -> Tuple[str, str]:
    """The interpreter and import root the installed hook command must use.

    `sys.executable` rather than the string "python": this machine, like a
    default macOS install, has no `python` on PATH at all -- only `python3` --
    so a command spelled "python -m ..." dies in the shell with 127 before any
    adapter code runs.  Claude Code reports nothing for a failed hook and
    `hook.py` exits 0 on every error by design, so that failure is *silent*:
    `config_installed` still reads true while not one event is ever delivered.
    An absolute interpreter path removes the PATH dependency entirely.

    The import root is `ticket_board`'s parent, exported as PYTHONPATH so the
    hook resolves the package from a source checkout as well as an installed
    environment.  A hook runs in Claude Code's environment, not in the shell the
    operator installed from, so it inherits neither a virtualenv nor a PATH.
    """
    package_root = Path(__file__).resolve().parents[3]
    return sys.executable, str(package_root)


def _hook_command(project_id: str, server_url: str, agent_id: str) -> str:
    executable, package_root = hook_runtime()
    return (
        "PYTHONPATH=%s %s -m ticket_board.adapters.claude.hook "
        "--project-id %s --server-url %s --agent-id %s #%s:%s"
        % (
            shlex.quote(package_root),
            shlex.quote(executable),
            shlex.quote(project_id),
            shlex.quote(server_url),
            shlex.quote(agent_id),
            OWNER_MARKER,
            agent_id,
        )
    )


PREFLIGHT_MARKER = "ticket-board-hook-preflight-ok"


def preflight_hook_command(project_dir: Path, enrollment: Enrollment, config: AdapterConfig) -> Tuple[bool, str]:
    """Actually execute the installed hook command and prove it can run.

    This is the check that separates "the config file says a hook is installed"
    from "the hook command is executable on this machine".  Nothing else in the
    adapter can tell those apart, because a hook that cannot start looks exactly
    like a hook that started and had nothing to say.
    """
    command = _installed_hook_command(project_dir, enrollment.agent_id)
    if command is None:
        return False, "no Ticket Board hook is installed in this project"
    # The owner marker is a trailing `#...` shell comment, so a flag appended to
    # the end of the command lands INSIDE the comment and is silently ignored.
    # Insert before it. (Found by running this: the probe reported "missing
    # required field: hook_event_name" -- the delivery path, not preflight.)
    head, marker, tail = command.partition("#" + OWNER_MARKER)
    probe = head.rstrip() + " --preflight" + (" " + marker + tail if marker else "")
    try:
        result = subprocess.run(
            ["/bin/sh", "-c", probe],
            capture_output=True,
            text=True,
            timeout=30,
            cwd=str(project_dir),
            input="{}",
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return False, "hook command could not be executed: %s" % exc
    if PREFLIGHT_MARKER in (result.stdout or ""):
        return True, "hook command runs"
    detail = (result.stderr or result.stdout or "").strip().splitlines()
    tail = detail[-1] if detail else "no output"
    return False, "hook command exited %s without the preflight marker: %s" % (result.returncode, tail)


def _installed_hook_command(project_dir: Path, agent_id: str) -> Optional[str]:
    settings_path = project_dir / ".claude" / "settings.json"
    try:
        settings = _load_settings(settings_path)
    except (OSError, ValueError, ClaudeHookError):
        return None
    hooks = settings.get("hooks")
    if not isinstance(hooks, Mapping):
        return None
    for entries in hooks.values():
        if not isinstance(entries, list):
            continue
        for entry in entries:
            if not isinstance(entry, Mapping):
                continue
            for hook in entry.get("hooks", []):
                if not isinstance(hook, Mapping):
                    continue
                command = hook.get("command")
                if not isinstance(command, str):
                    continue
                if hook.get("ticket_board_owner") == agent_id or (
                    "%s:%s" % (OWNER_MARKER, agent_id)
                ) in command:
                    return command
    return None


def _has_owned_hook(entries: Iterable[Any], agent_id: str) -> bool:
    for entry in entries:
        if isinstance(entry, Mapping) and _entry_has_owner(entry, agent_id):
            return True
    return False


def _entry_has_owner(entry: Mapping[str, Any], agent_id: str) -> bool:
    for hook in entry.get("hooks", []):
        if not isinstance(hook, Mapping):
            continue
        if hook.get("ticket_board_owner") == agent_id:
            return True
        command = hook.get("command")
        if isinstance(command, str) and ("%s:%s" % (OWNER_MARKER, agent_id)) in command:
            return True
    return False


def _without_owned_hooks(entry: Any, agent_id: str) -> Optional[Any]:
    if not isinstance(entry, dict):
        return entry
    hooks = entry.get("hooks")
    if not isinstance(hooks, list):
        return entry
    kept = []
    for hook in hooks:
        if isinstance(hook, Mapping) and (
            hook.get("ticket_board_owner") == agent_id
            or ("%s:%s" % (OWNER_MARKER, agent_id)) in str(hook.get("command", ""))
        ):
            continue
        kept.append(hook)
    if not kept:
        return None
    clone = dict(entry)
    clone["hooks"] = kept
    return clone


class BoardClient:
    def __init__(self, base_url: str, project_id: str, timeout: float = 2.0):
        self.base_url = base_url.rstrip("/")
        self.project_id = project_id
        self.timeout = timeout

    def post_json(
        self,
        path: str,
        body: Mapping[str, Any],
        *,
        token: Optional[str],
        expected_status: Tuple[int, ...] = (200,),
    ) -> Dict[str, Any]:
        url = "%s%s" % (self.base_url, path)
        headers = {"Content-Type": "application/json", "X-Project-Id": self.project_id}
        if token:
            headers["Authorization"] = "Bearer %s" % token
        data = json.dumps(body, separators=(",", ":")).encode("utf-8")
        req = request.Request(url, data=data, headers=headers, method="POST")
        try:
            with request.urlopen(req, timeout=self.timeout) as res:
                status = int(res.status)
                payload = json.loads(res.read().decode("utf-8") or "{}")
        except error.HTTPError as exc:
            status = int(exc.code)
            payload = json.loads(exc.read().decode("utf-8") or "{}")
        if status not in expected_status:
            raise RuntimeError("board returned %s: %s" % (status, payload))
        return payload

    def get_json(
        self,
        path: str,
        *,
        token: Optional[str] = None,
        operator: Optional[OperatorCredentials] = None,
    ) -> Dict[str, Any]:
        url = "%s%s" % (self.base_url, path)
        headers = {"X-Project-Id": self.project_id}
        if operator is not None:
            headers["Cookie"] = "tb_session=%s" % operator.session_token
        elif token:
            headers["Authorization"] = "Bearer %s" % token
        req = request.Request(url, headers=headers, method="GET")
        with request.urlopen(req, timeout=self.timeout) as res:
            return json.loads(res.read().decode("utf-8") or "{}")

    def delete_json(
        self,
        path: str,
        body: Mapping[str, Any],
        *,
        token: Optional[str] = None,
        operator: Optional[OperatorCredentials] = None,
        expected_status: Tuple[int, ...] = (200,),
    ) -> Dict[str, Any]:
        url = "%s%s" % (self.base_url, path)
        headers = {
            "Content-Type": "application/json",
            "X-Project-Id": self.project_id,
        }
        if operator is not None:
            # Cookie + double-submit CSRF token + Origin: an operator session is
            # a browser credential, and the server refuses an unsafe method that
            # arrives without all three.
            headers["Cookie"] = "tb_session=%s" % operator.session_token
            headers["X-CSRF-Token"] = operator.csrf_token
            headers["Origin"] = operator.origin
        elif token:
            headers["Authorization"] = "Bearer %s" % token
        data = json.dumps(body, separators=(",", ":")).encode("utf-8")
        req = request.Request(url, data=data, headers=headers, method="DELETE")
        try:
            with request.urlopen(req, timeout=self.timeout) as res:
                status = int(res.status)
                payload = json.loads(res.read().decode("utf-8") or "{}")
        except error.HTTPError as exc:
            status = int(exc.code)
            payload = json.loads(exc.read().decode("utf-8") or "{}")
        if status not in expected_status:
            raise RuntimeError("board returned %s: %s" % (status, payload))
        return payload


def deliver_hook_event(
    client: BoardClient,
    enrollment: Enrollment,
    envelope: Mapping[str, Any],
    spool_dir: Path,
    *,
    config: AdapterConfig,
    sleep: Callable[[float], None] = time.sleep,
) -> DeliveryResult:
    last_error = None
    for attempt in range(config.retries + 1):
        try:
            response = client.post_json("/hook-events", envelope, token=enrollment.token, expected_status=(200,))
            return DeliveryResult(True, False, 200, response)
        except Exception as exc:  # noqa: BLE001 - delivery must not block Claude sessions.
            last_error = str(exc)
            if attempt < config.retries:
                sleep(config.backoff_seconds * (2**attempt))

    _enqueue_spool(spool_dir, envelope, config.spool_cap)
    return DeliveryResult(False, True, None, None, error=last_error)


def _enqueue_spool(spool_dir: Path, envelope: Mapping[str, Any], cap: int) -> Path:
    spool_dir.mkdir(parents=True, exist_ok=True)
    queued = sorted(spool_dir.glob("*.json"))
    if len(queued) >= cap:
        raise SpoolFull("offline hook spool cap exceeded")
    event_id = envelope.get("event", {}).get("event_id", uuid.uuid4().hex)
    path = spool_dir / ("%s.json" % event_id)
    path.write_text(json.dumps(envelope, indent=2, sort_keys=True) + "\n")
    return path


def revoke_session_lease(
    client: BoardClient,
    enrollment: Enrollment,
    *,
    note: str,
    operator: OperatorCredentials,
    expected_version: Optional[int] = None,
) -> Dict[str, Any]:
    """Revoke an agent's board session lease. Operator credentials required.

    `operator` is not optional and deliberately has no agent-token fallback.
    This call used to send `enrollment.token`; against a real server that is a
    403 `agent_token_insufficient` on every single invocation, because the
    frozen contract says "Operator or master only" for this route. Falling back
    to the agent token would restore a call that cannot ever succeed.
    """
    if not note.strip():
        raise ClaudeHookError("lease revocation requires an explicit note")
    if expected_version is None:
        expected_version = current_agent_version(client, enrollment, operator=operator)
    body = {
        "request_id": str(uuid.uuid4()),
        "expected_version": expected_version,
        "note": note,
    }
    return client.delete_json(
        "/agents/%s/session-lease" % enrollment.agent_id,
        body,
        operator=operator,
        expected_status=(200,),
    )


def current_agent_version(
    client: BoardClient,
    enrollment: Enrollment,
    *,
    operator: Optional[OperatorCredentials] = None,
) -> int:
    """Read the agent's live version for an optimistic-concurrency check.

    `Enrollment.lease_version` is the wrong number twice over and must not be
    used here. It is the SESSION LEASE's version, while this route compares the
    AGENT's version; and it is captured once at enrollment and never refreshed,
    while the agent's version increments on every hook event the board records.
    Sending it produced a guaranteed 409 `ticket_version_conflict`
    (expected_version=1, actual_version=7) for any agent that had delivered even
    a handful of events -- which is every agent that ever worked.
    """
    # `items`, per AgentListResponse in the frozen contract -- not `agents`.
    listing = client.get_json("/agents", token=enrollment.token, operator=operator)
    for agent in listing.get("items", []):
        if agent.get("id") == enrollment.agent_id:
            return int(agent["version"])
    raise ClaudeHookError("agent %s is not present in this project" % enrollment.agent_id)


def disconnect_project(project_dir: Path, enrollment: Enrollment) -> Dict[str, Any]:
    """Agent-side teardown: stop the hooks and destroy the local credential.

    The counterpart to `revoke_session_lease`, and the half an agent can
    actually perform. Revoking the board lease is an operator action; removing
    this machine's copy of the token is not, and leaving the token on disk after
    an uninstall would keep a working credential in a project nobody is watching.
    """
    settings_path = uninstall_hooks(project_dir, enrollment)
    removed = []
    for path in (enrollment_path(project_dir), receipts_path(project_dir)):
        if path.exists():
            path.unlink()
            removed.append(str(path))

    spool_dir = project_dir / ".ticket-board" / "spool"
    spooled = sorted(spool_dir.glob("*.json")) if spool_dir.is_dir() else []
    return {
        "settings_path": str(settings_path),
        "removed": removed,
        # Reported, never silently deleted: a spooled event is undelivered work,
        # and the operator decides whether it is still wanted.
        "spooled_events_left": [str(p) for p in spooled],
    }


def diagnose(
    project_dir: Path,
    enrollment: Enrollment,
    recorded_events: Iterable[Mapping[str, Any]],
    board_responses: Iterable[Mapping[str, Any]],
) -> DoctorReport:
    config_installed = _project_has_owned_hooks(project_dir, enrollment.agent_id)
    responses = list(board_responses)
    server_received = any(bool(item.get("accepted")) for item in responses)
    response_delivered = any(isinstance(item.get("context"), Mapping) for item in responses)

    session_adopted = False
    for payload in recorded_events:
        event_name = payload.get("hook_event_name") or payload.get("event_name")
        synthetic = bool(payload.get("synthetic_probe"))
        if event_name in ALLOWED_CLAUDE_EVENTS and event_name not in {"PreToolUse", "PostToolUse", "Notification"}:
            if not synthetic and event_name != "Probe":
                session_adopted = True
                break

    remediation = []
    if not config_installed:
        remediation.append("Run project-scoped hook install; do not edit user-level Claude settings.")
    if not server_received:
        remediation.append("Check board URL, project id and agent token; events have not reached /hook-events.")
    if server_received and not response_delivered:
        remediation.append("The board accepted an event but no injectable context was delivered; inspect response errors.")
    if not session_adopted:
        remediation.append("Run one real Claude turn in this project; a synthetic probe does not prove hook adoption.")

    return DoctorReport(
        config_installed=config_installed,
        server_received=server_received,
        response_delivered=response_delivered,
        session_adopted=session_adopted,
        remediation=remediation,
    )


def _project_has_owned_hooks(project_dir: Path, agent_id: str) -> bool:
    settings_path = project_dir / ".claude" / "settings.json"
    try:
        settings = _load_settings(settings_path)
    except (OSError, ValueError, ClaudeHookError):
        return False
    hooks = settings.get("hooks")
    if not isinstance(hooks, Mapping):
        return False
    return any(
        isinstance(entries, list) and _has_owned_hook(entries, agent_id)
        for entries in hooks.values()
    )


# --------------------------------------------------------------------------
# Delivery receipts
#
# `diagnose()` from T-201 takes the events and responses as arguments, which is
# right for a fixture test and unusable for an operator: a live hook runs inside
# Claude Code's process, so by the time anyone types `doctor` in a shell there is
# nothing in memory to pass it.  The hook therefore records one bounded receipt
# per invocation and the live doctor reads them back off disk.
#
# A receipt records only outcome metadata -- never prompts, tool input or tool
# output -- so the file is safe to keep in the project directory.
# --------------------------------------------------------------------------

RECEIPT_CAP = 200
RECEIPT_FIELDS = (
    "ts",
    "hook_event_name",
    "kind",
    "event_id",
    "session_id",
    "delivered",
    "spooled",
    "status_code",
    "deduplicated",
    "context_lines",
    "synthetic_probe",
    "error",
)


def receipts_path(project_dir: Path) -> Path:
    return project_dir / ".ticket-board" / "receipts.jsonl"


def append_receipt(project_dir: Path, record: Mapping[str, Any], cap: int = RECEIPT_CAP) -> Path:
    """Append one bounded receipt, trimming to the most recent `cap` lines."""
    path = receipts_path(project_dir)
    path.parent.mkdir(parents=True, exist_ok=True)

    # Fail closed on the INPUT, then whitelist. Checking only the whitelisted
    # copy would silently drop a `prompt` a caller wrongly passed in, hiding the
    # upstream bug; the adapter's privacy bound refuses rather than redacts, and
    # receipts hold that line too. The whitelist stays as defence in depth.
    _assert_no_sensitive_fields(dict(record))
    bounded = {name: record.get(name) for name in RECEIPT_FIELDS if name in record}
    if isinstance(bounded.get("error"), str):
        bounded["error"] = _bounded_status(bounded["error"], 180)

    lines = []
    if path.exists():
        lines = [line for line in path.read_text().splitlines() if line.strip()]
    lines.append(json.dumps(bounded, separators=(",", ":"), sort_keys=True))
    if len(lines) > cap:
        lines = lines[-cap:]
    path.write_text("\n".join(lines) + "\n")
    os.chmod(path, 0o600)
    return path


def load_receipts(project_dir: Path) -> List[Dict[str, Any]]:
    path = receipts_path(project_dir)
    if not path.exists():
        return []
    receipts = []
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            parsed = json.loads(line)
        except ValueError:
            continue  # a torn final write must not break the doctor
        if isinstance(parsed, dict):
            receipts.append(parsed)
    return receipts


REAL_SESSION_EVENTS = frozenset(ALLOWED_CLAUDE_EVENTS - {"PreToolUse", "PostToolUse", "Notification"})


def diagnose_live(
    project_dir: Path,
    enrollment: Enrollment,
    config: AdapterConfig,
    *,
    run_preflight: bool = True,
) -> DoctorReport:
    """Diagnose a real enrollment from what is on disk.

    Reports five facts that fail independently, because collapsing them is how a
    doctor shows green for a connection that will never deliver anything:

    * `config_installed`  -- the project settings name our hook.
    * `hook_executable`   -- that command actually runs on this machine.
    * `hook_executed`     -- Claude Code has in fact invoked it at least once.
    * `server_received`   -- the board answered one of those invocations.
    * `session_adopted`   -- a REAL session event arrived, not just a probe.
    """
    config_installed = _project_has_owned_hooks(project_dir, enrollment.agent_id)

    hook_executable: Optional[bool] = None
    preflight_detail = ""
    if run_preflight and config_installed:
        hook_executable, preflight_detail = preflight_hook_command(project_dir, enrollment, config)

    receipts = load_receipts(project_dir)
    mine = [r for r in receipts if r.get("session_id") and r.get("event_id")]
    hook_executed = bool(mine)
    server_received = any(r.get("status_code") == 200 and r.get("delivered") for r in mine)
    response_delivered = any(
        r.get("delivered") and isinstance(r.get("context_lines"), int) for r in mine
    )
    session_adopted = any(
        r.get("hook_event_name") in REAL_SESSION_EVENTS and not r.get("synthetic_probe")
        for r in mine
    )

    remediation: List[str] = []
    if not config_installed:
        remediation.append(
            "No Ticket Board hook in this project's .claude/settings.json. Run the project-scoped "
            "install; never edit user-level Claude settings."
        )
    if hook_executable is False:
        remediation.append(
            "The installed hook command cannot run: %s. Re-run install so the command is rewritten "
            "with this machine's interpreter." % preflight_detail
        )
    if config_installed and hook_executable is not False and not hook_executed:
        remediation.append(
            "The hook is installed and runnable but Claude Code has never invoked it. Start a NEW "
            "Claude session in this project -- hook settings are read at session start, so a session "
            "already running when you installed will not pick them up."
        )
    if hook_executed and not server_received:
        spooled = sum(1 for r in mine if r.get("spooled"))
        remediation.append(
            "The hook ran but no event reached the board (%d spooled offline). Check the board URL, "
            "project id and agent token." % spooled
        )
    if server_received and not response_delivered:
        remediation.append(
            "The board accepted an event but returned no injectable context; inspect response errors."
        )
    if not session_adopted:
        remediation.append(
            "No real Claude session event has been recorded. Run one real Claude turn in this project; "
            "a synthetic probe proves reachability, not hook adoption."
        )

    return DoctorReport(
        config_installed=config_installed,
        server_received=server_received,
        response_delivered=response_delivered,
        session_adopted=session_adopted,
        remediation=remediation,
        hook_executable=hook_executable,
        hook_executed=hook_executed,
    )
