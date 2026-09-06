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
import subprocess
import time
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List, Mapping, Optional, Tuple
from urllib import error, request

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


class ClaudeHookError(ValueError):
    """Raised when a Claude hook payload cannot be safely converted."""


class SpoolFull(RuntimeError):
    """Raised when offline delivery would exceed the configured spool cap."""


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

    def as_dict(self) -> Dict[str, Any]:
        return {
            "config": {"installed": self.config_installed},
            "delivery": {
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


def _event_id(agent_id: str, session_id: str, event_name: str, occurred_at: str) -> str:
    raw = "%s:%s:%s:%s" % (agent_id, session_id, event_name, occurred_at)
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
        "event_id": _event_id(enrollment.agent_id, session_id, event_name, occurred_at),
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

    repo = _git_common_dir(resolved)
    if repo is not None:
        for root in protected:
            root_repo = _git_common_dir(root)
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
    set, including when set empty to mean "protect nothing". The fallback is this
    host's two board checkouts, derived from the real home directory so the paths
    are not literals from one laptop.
    """
    raw = os.environ.get(FORBIDDEN_ROOTS_ENV)
    if raw is not None:
        return tuple(Path(part).expanduser().resolve(strict=False) for part in raw.split(os.pathsep) if part)

    home = _real_home_dir()
    return (
        (home / "Downloads" / "steer").resolve(strict=False),
        (home / "Downloads" / "tickets").resolve(strict=False),
    )


def _git_common_dir(path: Path) -> Optional[Path]:
    """Identify the repository ``path`` belongs to, or None if it is not in one.

    ``--git-common-dir`` is the shared repository directory: a checkout and every
    worktree registered against it report the same one no matter where on disk the
    worktree sits, which is what makes this a structural check and not another
    path list. Git prints it relative to the directory the command ran in.
    """
    if not path.exists():
        return None
    try:
        result = subprocess.run(
            ["git", "-C", str(path), "rev-parse", "--git-common-dir"],
            capture_output=True,
            check=False,
            text=True,
            timeout=5,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if result.returncode != 0:
        return None
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
    settings_path.write_text(json.dumps(settings, indent=2, sort_keys=True) + "\n")
    return settings_path


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

    settings_path.parent.mkdir(parents=True, exist_ok=True)
    settings_path.write_text(json.dumps(settings, indent=2, sort_keys=True) + "\n")
    return settings_path


def _load_settings(settings_path: Path) -> Dict[str, Any]:
    if not settings_path.exists():
        return {}
    data = json.loads(settings_path.read_text())
    if not isinstance(data, dict):
        raise ClaudeHookError("Claude project settings must be a JSON object")
    return data


def _hook_command(project_id: str, server_url: str, agent_id: str) -> str:
    return (
        "python -m ticket_board.adapters.claude.hook "
        "--project-id %s --server-url %s --agent-id %s #%s:%s"
        % (project_id, server_url, agent_id, OWNER_MARKER, agent_id)
    )


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

    def delete_json(
        self,
        path: str,
        body: Mapping[str, Any],
        *,
        token: str,
        expected_status: Tuple[int, ...] = (200,),
    ) -> Dict[str, Any]:
        url = "%s%s" % (self.base_url, path)
        headers = {
            "Content-Type": "application/json",
            "X-Project-Id": self.project_id,
            "Authorization": "Bearer %s" % token,
        }
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
    expected_version: Optional[int] = None,
) -> Dict[str, Any]:
    if not note.strip():
        raise ClaudeHookError("lease revocation requires an explicit note")
    body = {
        "request_id": str(uuid.uuid4()),
        "expected_version": expected_version if expected_version is not None else enrollment.lease_version,
        "note": note,
    }
    return client.delete_json(
        "/agents/%s/session-lease" % enrollment.agent_id,
        body,
        token=enrollment.token,
        expected_status=(200,),
    )


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
