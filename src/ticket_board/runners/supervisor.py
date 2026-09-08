"""The local supervisor: a process that outlives the model turns it starts.

This is the half of the managed runner that does not live in the board. It
registers a fenced lease, consumes durable wake jobs, starts exactly one Claude
session at a time in an allowlisted worktree, and reports what actually
happened rather than what it hoped would happen.

The ordering below is the whole design, and each step is placed where it is
because of a specific failure it prevents:

    register (fenced)        two supervisors for one agent is the fatal case
      -> reconcile           a crash between claim and spawn must not re-run
      -> lease a wake job    the server refuses to lease while a run is live
      -> dedupe locally      at-least-once delivery, exactly-once execution
      -> retain the run      session id on the board BEFORE the process exists
      -> claim the ticket    "started" is a lie without a valid claim
      -> spawn               stdin, allowlisted cwd, stripped environment
      -> report started      only now: the process exists and the claim held
      -> wait within budget  a budget pause is visible, not a silent hang
      -> report the outcome  responded / failed / budget_reached, with a reason

Three things this deliberately does NOT do:

- **It does not promise exactly-once shell execution.** Nothing local can. What
  it promises is that a *redelivered* job is recognised, and that an uncertain
  one -- a crash after spawn -- is reported as needing review instead of being
  quietly retried. The design doc asks for exactly that distinction.
- **It does not decide who gets woken.** Wake jobs are created by the messaging
  lane, which is also where "a reply does not wake its author" and the hop
  budget live. A supervisor that inferred recipients would be a second, quieter
  routing policy.
- **It does not widen permissions.** `permission_policy` comes from the lease
  the operator configured. A permission request pauses the run with
  `needs_approval`; the supervisor never answers one on the operator's behalf.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from .client import ApiError, BoardUnreachable, RunnerClient
from .launcher import ClaudeLauncher, LaunchFailed, LaunchSpec, is_process_alive
from .state import (
    InFlight,
    RunnerState,
    board_session_id,
    load_state,
    new_runner_id,
    new_runtime_session_id,
    save_state,
)

DEFAULT_BUDGET = {"max_hops": 3, "max_turns": 10, "max_seconds": 900}


@dataclass
class RunOutcome:
    """What one wake job actually did. Returned so callers can assert on it."""

    wake_job_id: str
    run_id: Optional[str]
    state: str                      # responded | failed | paused | skipped
    reason: Optional[str] = None
    # How long `start()` took, NOT the wake-to-start latency an operator cares
    # about -- that interval begins when the message is committed, before this
    # supervisor has seen anything. tests/runners/test_idle_start_latency.py
    # measures the real one from outside. Named for what it holds.
    spawn_seconds: Optional[float] = None


class Supervisor:
    def __init__(self, client: RunnerClient, *, agent_id: str, session_id: str,
                 worktree: Path, state_dir: Path,
                 launcher=None, prompt_builder=None,
                 # T-192: None means "whatever the operator approved for this
                 # agent". The old defaults declared the broadest policy on
                 # every registration without anyone deciding to.
                 permission_policy: Optional[str] = None,
                 runtime_profile: Optional[str] = None,
                 budget: Optional[Dict[str, int]] = None,
                 clock: Callable[[], float] = time.monotonic):
        self.client = client
        self.agent_id = agent_id
        # The supervisor's own board session lease, from its enrollment. It is
        # what a ticket claim is bound to, and it is NOT the child session a
        # run executes in -- see `_claim`.
        self.session_id = session_id
        self.worktree = Path(worktree)
        self.state_dir = Path(state_dir)
        self.launcher = launcher
        self.permission_policy = permission_policy
        self.runtime_profile = runtime_profile
        self.budget = dict(DEFAULT_BUDGET, **(budget or {}))
        self.clock = clock
        self.prompt_builder = prompt_builder or default_prompt
        self.state = self._load_or_mint()

    # ------------------------------------------------------------- identity

    def _load_or_mint(self) -> RunnerState:
        existing = load_state(self.state_dir)
        if existing is not None:
            return existing
        return RunnerState(runner_id=new_runner_id(),
                           project_id=self.client.project_id,
                           agent_id=self.agent_id,
                           worktree=str(self.worktree))

    def _save(self) -> None:
        save_state(self.state_dir, self.state)

    def register(self) -> Dict[str, Any]:
        """Take (or renew) the fenced lease for this agent.

        `expected_epoch` is the compare-and-swap. On a first registration it is
        omitted; afterwards it is the epoch this supervisor believes it holds,
        so a supervisor that was superseded while it was away gets 409
        `run_already_active` instead of quietly stealing the agent back.
        """
        lease = self.client.register(
            self.state.runner_id, self.agent_id, str(self.worktree),
            runtime_profile=self.runtime_profile,
            permission_policy=self.permission_policy,
            expected_epoch=self.state.epoch or None,
        )
        self.state.epoch = lease["epoch"]
        self.permission_policy = lease.get("permission_policy",
                                           self.permission_policy)
        if lease.get("budget"):
            self.budget = dict(self.budget, **lease["budget"])
        self._save()
        return lease

    # ---------------------------------------------------------- reconcile

    def reconcile(self) -> Optional[RunOutcome]:
        """Settle whatever was in flight when this supervisor last stopped.

        Three cases, and the middle one is why this method exists at all:

        - nothing in flight -> nothing to do.
        - a record with a live pid -> a process from the previous supervisor is
          still running. Do not start a second one and do not claim its result.
          The lease we just took fences the board; the run is left alone and
          reported as needing review.
        - a record with a dead pid, or one written before the spawn -> the
          external side effects are *uncertain*. The design doc's rule is that
          uncertain side effects need review, so the run is failed with that
          reason and the job is recorded in the failed queue. It is not retried:
          silently re-running a task that may have half-finished is worse than
          telling a human it is unclear.
        """
        flight = self.state.in_flight
        if flight is None:
            return None
        alive = is_process_alive(flight.pid)
        reason = ("A previous supervisor left this run's process alive (pid "
                  "{}); not started twice. Needs review.".format(flight.pid)
                  if alive else
                  "Supervisor restarted while this run was in flight; its "
                  "effects are uncertain. Needs review.")
        outcome = self._close_out(flight, "failed", reason)
        self.state.in_flight = None
        self.state.remember_failed(flight.dedupe_key, reason, flight.run_id)
        self._save()
        return outcome

    def _close_out(self, flight: InFlight, event: str,
                   reason: str) -> RunOutcome:
        """Report a terminal event for a run whose version we do not hold."""
        problem = self._post_terminal(flight.run_id, event, reason)
        return RunOutcome(
            flight.wake_job_id, flight.run_id, event,
            reason if problem is None else "{} ({})".format(reason, problem))

    def _post_terminal(self, run_id: str, event: str, reason: Optional[str],
                       version: Optional[int] = None,
                       budget: Optional[Dict[str, int]] = None) -> Optional[str]:
        """Post a terminal run event; return None on success, else why not.

        The version is discovered rather than assumed when we do not hold one.
        A restarted supervisor has no cached `expected_version`, and the frozen
        contract has no `GET /runs/{id}` to read one from -- so it sends the
        event and reads `actual_version` out of the 409's details. One retry at
        that version is enough. A second conflict means somebody else is
        writing this run, which is a thing to report, not a race to win.
        """
        attempt = version or 1
        for _ in range(2):
            try:
                self.client.run_event(run_id, event, expected_version=attempt,
                                      reason=reason, budget=budget)
                return None
            except ApiError as exc:
                actual = (exc.details or {}).get("actual_version")
                if exc.code == "ticket_version_conflict" and actual:
                    attempt = int(actual)
                    continue
                return "report failed: {}".format(exc)
            except BoardUnreachable as exc:
                return "report failed: {}".format(exc)
        return "gave up after a second version conflict"

    # --------------------------------------------------------------- polling

    def poll_once(self, wait_seconds: int = 20) -> List[RunOutcome]:
        jobs = self.client.jobs(self.state.runner_id,
                                wait_seconds=wait_seconds)["items"]
        return [self.execute(job) for job in jobs]

    def execute(self, job: Dict[str, Any]) -> RunOutcome:
        dedupe_key = job.get("dedupe_key") or "{}:{}".format(
            job["message_id"], job["recipient_agent_id"])
        run_id = "run_" + job["id"][4:]
        if self.state.seen(dedupe_key):
            # At-least-once delivery, recognised. The job is closed out so the
            # board stops redelivering it, and nothing is executed twice.
            self._report_duplicate(run_id)
            return RunOutcome(job["id"], run_id, "skipped",
                              "Already executed; duplicate delivery.")

        # Two forms of one session: the UUID the child process is given, and
        # the `ses_...` the board records. Minted together so they cannot drift.
        runtime_session = new_runtime_session_id()
        session_id = board_session_id(runtime_session)
        # Written BEFORE anything external happens, and fsynced. If the process
        # dies on the next line, `reconcile()` finds this record on restart.
        self.state.in_flight = InFlight(
            run_id=run_id, wake_job_id=job["id"], dedupe_key=dedupe_key,
            session_id=session_id, runtime_session_id=runtime_session,
            ticket_id=job.get("ticket_id"))
        self._save()

        try:
            run = self.client.run_event(run_id, "starting",
                                        expected_version=1,
                                        session_id=session_id)
        except (ApiError, BoardUnreachable) as exc:
            return self._fail(job, run_id, "Could not retain the run: {}".format(exc))

        ticket_id = job.get("ticket_id")
        if ticket_id and not self._claim(ticket_id):
            return self._fail(
                job, run_id,
                "Could not claim {}; not started.".format(ticket_id),
                version=run["version"])

        try:
            spec = LaunchSpec(
                session_id=runtime_session, worktree=self.worktree,
                prompt=self.prompt_builder(job),
                resume=False,
                permission_policy=self.permission_policy,
            )
            began = self.clock()
            handle = self._launcher().start(spec)
        except LaunchFailed as exc:
            return self._fail(job, run_id, str(exc), version=run["version"])

        self.state.in_flight.pid = handle.pid
        self.state.in_flight.spawned = True
        self._save()

        spawn_seconds = self.clock() - began
        try:
            run = self.client.run_event(
                run_id, "started", expected_version=run["version"],
                session_id=session_id, ticket_claim=ticket_id or None)
        except (ApiError, BoardUnreachable) as exc:
            handle.terminate()
            return self._fail(job, run_id, "Started but unreportable: {}".format(exc))

        result = handle.wait(timeout=self.budget.get("max_seconds"))
        # `seconds_used` is the one budget counter this supervisor can fill in
        # honestly: it timed the process itself. `turns_used` is not enforced
        # or counted -- the installed Claude Code has no `--max-turns` flag and
        # a turn count would have to be parsed out of the child's transcript --
        # and `hops_used` belongs to the messaging lane that creates the
        # causation chain. Reporting a number nobody measured is worse than
        # reporting none, so they are passed through unchanged.
        spent = dict(self.budget,
                     seconds_used=int(max(0.0, self.clock() - began)))
        if result.returncode is None:
            # The time budget, not a crash. Pause with a visible reason; the
            # design doc says an operator resumes from here.
            # `keep_unfinished` leaves this out of the local ledger on
            # purpose: the run is paused on the board, which both holds the
            # agent and excludes it from the orphan sweep, so the board is the
            # guard against a re-run and a local "already done" entry would be
            # a second, quieter claim about a run nobody has finished.
            outcome = self._terminal(job, run_id, "budget_reached",
                                     result.reason or "Run budget reached.",
                                     run["version"], keep_unfinished=True,
                                     budget=spent)
            outcome.spawn_seconds = spawn_seconds
            return outcome

        event = "responded" if result.returncode == 0 else "failed"
        outcome = self._terminal(job, run_id, event, result.reason,
                                 run["version"], budget=spent)
        outcome.spawn_seconds = spawn_seconds
        return outcome

    # ---------------------------------------------------------------- pieces

    def _launcher(self):
        if self.launcher is None:
            self.launcher = ClaudeLauncher()
        return self.launcher

    def _claim(self, ticket_id: str) -> bool:
        """Claim the ticket before reporting a start, or report no start.

        The claim is made with the *supervisor's* board session, not the child
        Claude session: only the former is a session lease the board issued and
        can watch expire. The child session id is a runtime conversation handle
        and must not be used as a board credential.

        A refusal here -- already claimed, dependency unmet, version moved --
        is not an error to retry. It means this agent may not do this work, and
        the run says so instead of starting and finding out.
        """
        try:
            detail = self.client.get_ticket(ticket_id)
            version = detail["ticket"]["version"]
            self.client.claim_ticket(ticket_id, expected_version=version,
                                     session_id=self.session_id)
        except (ApiError, BoardUnreachable, KeyError):
            return False
        return True

    def _report_duplicate(self, run_id: str) -> None:
        """Best effort. A duplicate we cannot close out is re-leased later and
        recognised again -- wasteful, never wrong."""
        try:
            self.client.cancel(run_id, expected_version=1,
                               reason="Duplicate delivery; already executed.")
        except (ApiError, BoardUnreachable):
            pass

    def _fail(self, job: Dict[str, Any], run_id: str, reason: str,
              version: Optional[int] = None) -> RunOutcome:
        return self._terminal(job, run_id, "failed", reason, version)

    def _terminal(self, job: Dict[str, Any], run_id: str, event: str,
                  reason: Optional[str], version: Optional[int] = None,
                  keep_unfinished: bool = False,
                  budget: Optional[Dict[str, int]] = None) -> RunOutcome:
        outcome = RunOutcome(job["id"], run_id, event, reason)
        problem = self._post_terminal(run_id, event, reason, version,
                                      budget=budget)
        if problem is not None:
            outcome.reason = "{} ({})".format(reason, problem)

        dedupe_key = self.state.in_flight.dedupe_key if self.state.in_flight \
            else job.get("dedupe_key", "")
        if event == "responded":
            self.state.remember_completed(dedupe_key)
        elif not keep_unfinished:
            self.state.remember_failed(dedupe_key, reason or event, run_id)
        self.state.in_flight = None
        self._save()
        return outcome

    # ----------------------------------------------------------------- loop

    def run_forever(self, *, wait_seconds: int = 20,
                    max_polls: Optional[int] = None,
                    renew_every: int = 10) -> List[RunOutcome]:
        """Register, reconcile, then consume until told to stop.

        `max_polls` exists so a test can drive a bounded number of iterations
        of the real loop rather than a reimplementation of it. The lease is
        renewed every `renew_every` polls: a lease that expires under a long
        idle poll would let a second supervisor in behind this one.
        """
        self.register()
        outcomes: List[RunOutcome] = []
        reconciled = self.reconcile()
        if reconciled is not None:
            outcomes.append(reconciled)
        polls = 0
        while max_polls is None or polls < max_polls:
            polls += 1
            if polls % renew_every == 0:
                self.register()
            outcomes.extend(self.poll_once(wait_seconds=wait_seconds))
        return outcomes


def default_prompt(job: Dict[str, Any]) -> str:
    """What the child session is told, assembled from the job alone.

    Deliberately plain text on stdin with no interpolation into anything
    executable, and deliberately explicit that the ticket is already claimed --
    a session that re-claims its own ticket burns a turn and a version.
    """
    lines = ["You have been woken by the Ticket Board to do one task."]
    if job.get("ticket_id"):
        lines.append("Ticket {} is already claimed for you by your runner."
                     .format(job["ticket_id"]))
    lines.append("Wake job {} (message {}).".format(job.get("id"),
                                                    job.get("message_id")))
    lines.append("Read the ticket, do the work, and report back on the board.")
    return "\n".join(lines)
