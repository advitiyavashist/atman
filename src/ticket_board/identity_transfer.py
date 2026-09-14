"""Identity transfer storage shared by the checkout and installed CLI.

The board identity lock must surround snapshot, mutation and restoration.
This module owns only the identity files; it does not import a checkout,
invoke a provider, or publish a successful-transfer audit before commit.
"""
from __future__ import annotations

import hashlib
import json
import os
import stat
import uuid

try:
    from .agent_checkin import _AgentLock, _agent_rec, _agent_update, now
except ImportError:  # python src/ticket_board/cli.py
    from agent_checkin import _AgentLock, _agent_rec, _agent_update, now


IDENTITY_BOUND_AGENT_KEYS = (
    "auth_check", "runner_context", "limit", "adapter_failure",
    "auth_resume_at", "harness_check",
)


def endpoint_path(board, owner):
    cache = os.environ.get("TICKETS_CACHE_DIR") or os.path.join(
        os.path.expanduser("~"), ".cache", "atman")
    digest = hashlib.sha256(os.path.abspath(board).encode("utf-8")).hexdigest()[:16]
    return os.path.join(cache, "sessions", digest, owner + ".json")


def remote_state_path(board, owner):
    return os.path.join(board, "adapters", owner + ".json")


def atomic_json_dump(path, value):
    directory = os.path.dirname(path) or "."
    os.makedirs(directory, exist_ok=True)
    temporary = "%s.tmp.%d.%s" % (path, os.getpid(), uuid.uuid4().hex[:8])
    try:
        with open(temporary, "w") as stream:
            json.dump(value, stream, indent=2, sort_keys=True)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass


def fence_remote_adapter(board, owner):
    path = remote_state_path(board, owner)
    if not os.path.isfile(path):
        return
    # Same lock used by root remote register/heartbeat/claim transitions.
    with _AgentLock(board, owner + ".remote"):
        with open(path) as stream:
            state = json.load(stream)
        state["fence"] = int(state.get("fence") or 0) + 1
        state["lease"] = {}
        state.pop("claim", None)
        state["revoked_at"] = now()
        atomic_json_dump(path, state)


def snapshot_identity_artifacts(board, owner):
    paths = {
        "agent": os.path.join(board, "agents", owner + ".json"),
        "remote": remote_state_path(board, owner),
        "workforce": os.path.join(board, "workforce.json"),
        "aliases": os.path.join(board, "aliases.json"),
        "roles": os.path.join(board, "roles.json"),
        "endpoint": endpoint_path(board, owner),
    }
    snapshot = {}
    for name, path in paths.items():
        try:
            with open(path, "rb") as stream:
                payload = stream.read()
            snapshot[name] = {"path": path, "payload": payload,
                              "mode": stat.S_IMODE(os.stat(path).st_mode)}
        except FileNotFoundError:
            snapshot[name] = {"path": path, "payload": None, "mode": None}
    return snapshot


def restore_identity_artifacts(board, owner, snapshot):
    for item in (snapshot or {}).values():
        path, payload = item["path"], item["payload"]
        if payload is None:
            try:
                os.unlink(path)
            except FileNotFoundError:
                pass
            continue
        directory = os.path.dirname(path) or "."
        os.makedirs(directory, exist_ok=True)
        temporary = "%s.tmp.%d.%s" % (path, os.getpid(), uuid.uuid4().hex[:8])
        try:
            fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL,
                         item["mode"] if item["mode"] is not None else 0o600)
            with os.fdopen(fd, "wb") as stream:
                stream.write(payload)
                stream.flush()
                os.fsync(stream.fileno())
            os.chmod(temporary, item["mode"])
            os.replace(temporary, path)
        finally:
            try:
                os.unlink(temporary)
            except FileNotFoundError:
                pass


def strip_identity_bound_state(board, owner):
    def clear(record):
        for key in IDENTITY_BOUND_AGENT_KEYS:
            record.pop(key, None)
        record["ticket"] = ""

    if _agent_rec(board, owner):
        _agent_update(board, owner, clear)
    try:
        os.unlink(endpoint_path(board, owner))
    except FileNotFoundError:
        pass
    fence_remote_adapter(board, owner)
