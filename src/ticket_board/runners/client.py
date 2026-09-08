"""The runner's view of the board API: four routes and a bounded retry.

Kept separate from the supervisor so the supervisor's tests can drive a fake
board without a socket, and so the retry policy lives in one readable place.

Two things worth being explicit about, because both are easy to get wrong in a
way that only shows up under failure:

**A retry needs the same request_id, not a fresh one.** Every mutation on this
surface carries an idempotency key. Retrying a timed-out register with a new
key is not a retry -- it is a second registration, and the contract will
happily replay-protect the wrong thing. So the key is minted by the caller,
once per logical operation, and reused across every attempt of it.

**A 409 is an answer, not a transport failure.** `run_already_active` means
another supervisor holds the lease; retrying it harder makes two supervisors
fight over one agent. Only transport failures and 5xx are retried; every
contract-shaped refusal is returned to the caller to act on.
"""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
import uuid
from typing import Any, Dict, Mapping, Optional, Tuple

RETRYABLE_STATUS = (500, 502, 503, 504)


def request_id() -> str:
    return str(uuid.uuid4())


class BoardUnreachable(RuntimeError):
    """Every bounded attempt failed at the transport. The board may be down."""


class ApiError(Exception):
    """A contract-shaped refusal. `code` is the frozen error enum member.

    Written as a plain class rather than a dataclass on purpose: a
    `@dataclass(frozen=True)` exception raises `FrozenInstanceError` the moment
    anything sets `__traceback__` on it, which `contextlib` does while
    unwinding -- so the real failure is replaced by an unrelated one at exactly
    the moment you most need to read it. Found by this happening.
    """

    def __init__(self, status: int, code: str, message: str,
                 details: Optional[Dict[str, Any]] = None):
        super().__init__("{} {}: {}".format(status, code, message))
        self.status = status
        self.code = code
        self.message = message
        self.details = details


class RunnerClient:
    def __init__(self, base_url: str, project_id: str, token: str, *,
                 timeout: float = 30.0, retries: int = 2,
                 backoff_seconds: float = 0.2, opener=None, sleep=time.sleep):
        self.base_url = base_url.rstrip("/")
        self.project_id = project_id
        self.token = token
        self.timeout = timeout
        self.retries = retries
        self.backoff_seconds = backoff_seconds
        self._opener = opener or urllib.request.urlopen
        self._sleep = sleep

    # ------------------------------------------------------------ transport

    def _call(self, method: str, path: str, *, body=None,
              query: str = "") -> Tuple[int, Dict[str, Any]]:
        url = "{}{}{}".format(self.base_url, path, ("?" + query) if query else "")
        headers = {
            "X-Project-Id": self.project_id,
            "Authorization": "Bearer " + self.token,
        }
        data = None
        if body is not None:
            headers["Content-Type"] = "application/json"
            data = json.dumps(body, separators=(",", ":")).encode("utf-8")
        last: Optional[Exception] = None
        for attempt in range(self.retries + 1):
            req = urllib.request.Request(url, data=data, headers=headers,
                                         method=method)
            try:
                with self._opener(req, timeout=self.timeout) as res:
                    return int(res.status), _payload(res.read())
            except urllib.error.HTTPError as exc:
                status = int(exc.code)
                payload = _payload(exc.read())
                if status in RETRYABLE_STATUS and attempt < self.retries:
                    last = exc
                    self._sleep(self.backoff_seconds * (2 ** attempt))
                    continue
                return status, payload
            except (urllib.error.URLError, OSError, TimeoutError) as exc:
                last = exc
                if attempt < self.retries:
                    self._sleep(self.backoff_seconds * (2 ** attempt))
                    continue
        raise BoardUnreachable(
            "{} {} failed after {} attempts: {}".format(
                method, path, self.retries + 1, last))

    def _ok(self, status: int, payload: Mapping[str, Any]) -> Dict[str, Any]:
        if status == 200:
            return dict(payload)
        error = payload.get("error") if isinstance(payload, dict) else None
        if isinstance(error, dict):
            raise ApiError(status, error.get("code", "unknown"),
                           error.get("message", ""), error.get("details"))
        raise ApiError(status, "unknown", "Unrecognised error body from board.")

    # ---------------------------------------------------------------- routes

    def register(self, runner_id: str, agent_id: str, worktree: str, *,
                 runtime_profile: Optional[str] = None,
                 permission_policy: Optional[str] = None,
                 expected_epoch: Optional[int] = None,
                 rid: Optional[str] = None) -> Dict[str, Any]:
        """Take the runner lease.

        T-192: `permission_policy` and `runtime_profile` DEFAULT TO NOT BEING
        SENT AT ALL. They used to default to `"prompt"` and
        `"claude-code-default"`, so every supervisor declared the broadest
        permission policy on every registration without anyone choosing it --
        an inherited broad grant in the most literal sense, and the board
        honoured it. Sending nothing means "give me what the operator approved
        for this agent". An explicit value is still allowed and is still
        enforced server-side: it may narrow the approved policy, never widen
        it (403 `forbidden_scope`).
        """
        body: Dict[str, Any] = {
            "request_id": rid or request_id(),
            "runner_id": runner_id,
            "agent_id": agent_id,
            "allowlisted_worktree": worktree,
        }
        if runtime_profile is not None:
            body["runtime_profile"] = runtime_profile
        if permission_policy is not None:
            body["permission_policy"] = permission_policy
        if expected_epoch is not None:
            body["expected_epoch"] = expected_epoch
        return self._ok(*self._call("POST", "/runners/register", body=body))

    def jobs(self, runner_id: str, wait_seconds: int = 20) -> Dict[str, Any]:
        query = "runner_id={}&wait_seconds={}".format(runner_id, int(wait_seconds))
        return self._ok(*self._call("GET", "/runners/jobs", query=query))

    def run_event(self, run_id: str, event: str, *, expected_version: int,
                  session_id: Optional[str] = None,
                  ticket_claim: Optional[str] = None,
                  reason: Optional[str] = None,
                  budget: Optional[Mapping[str, int]] = None,
                  rid: Optional[str] = None) -> Dict[str, Any]:
        body: Dict[str, Any] = {"request_id": rid or request_id(),
                                "expected_version": expected_version,
                                "event": event}
        if session_id is not None:
            body["session_id"] = session_id
        if ticket_claim is not None:
            body["ticket_claim"] = ticket_claim
        if reason is not None:
            body["reason"] = reason
        if budget is not None:
            body["budget"] = dict(budget)
        return self._ok(*self._call(
            "POST", "/runs/{}/events".format(run_id), body=body))

    def get_ticket(self, ticket_id: str) -> Dict[str, Any]:
        return self._ok(*self._call("GET", "/tickets/{}".format(ticket_id)))

    def claim_ticket(self, ticket_id: str, *, expected_version: int,
                     session_id: str, rid: Optional[str] = None) -> Dict[str, Any]:
        """Claim on behalf of this agent, with the supervisor's board session.

        `session_id` here is the supervisor's own session lease, not the child
        Claude session: the board binds a claim to a lease it can watch expire,
        and a child session id is a runtime conversation handle that the board
        has never issued and cannot revoke.
        """
        return self._ok(*self._call(
            "POST", "/tickets/{}/claim".format(ticket_id),
            body={"request_id": rid or request_id(),
                  "expected_version": expected_version,
                  "session_id": session_id}))

    def cancel(self, run_id: str, *, expected_version: int, reason: str,
               rid: Optional[str] = None) -> Dict[str, Any]:
        return self._ok(*self._call(
            "POST", "/runs/{}/cancel".format(run_id),
            body={"request_id": rid or request_id(),
                  "expected_version": expected_version, "reason": reason}))


def _payload(raw: bytes) -> Dict[str, Any]:
    try:
        return json.loads(raw.decode("utf-8") or "{}")
    except (ValueError, UnicodeDecodeError):
        return {}
