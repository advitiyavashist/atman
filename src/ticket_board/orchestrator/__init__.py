"""Master routing loop (T-182): deterministic eligibility, fenced writes.

`server/master.py` owns the lease primitives the frozen routes need -- take,
pause, reserve. This package owns the *decision*: which open ticket goes to
which agent, why, and what to record when nothing can move.
"""

from .eligibility import Candidate, Decision, Snapshot, eligible_agents, plan
from .sweep import Sweeper, SweepResult

__all__ = ["Candidate", "Decision", "Snapshot", "Sweeper", "SweepResult",
           "eligible_agents", "plan"]
