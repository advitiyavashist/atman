"""Repository boundary for automatic claims on a shared board (T-1135)."""
import os
import re
import subprocess
from functools import lru_cache
from urllib.parse import urlsplit


def _git(cwd, *args):
    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    try:
        result = subprocess.run(["git", *args], cwd=cwd, env=env,
                                capture_output=True, text=True, timeout=10)
    except (OSError, subprocess.TimeoutExpired):
        return ""
    return result.stdout.strip() if result.returncode == 0 else ""


def _normalize(origin):
    origin = origin.strip().rstrip("/")
    scp = re.fullmatch(r"[^/@]+@([^:]+):(.+)", origin)
    if scp:
        origin = "ssh://%s/%s" % scp.groups()
    parsed = urlsplit(origin)
    if parsed.hostname:
        return (parsed.hostname + parsed.path.removesuffix(".git")).lower()
    return origin


def checkout_repo(cwd):
    """This checkout's repository identity, or "" when it has no origin."""
    return _normalize(_git(cwd, "config", "--get", "remote.origin.url"))


def filter_for_checkout(tickets, cwd):
    """Skip a ticket only when its KNOWN repository differs from this checkout.

    A ticket whose repository cannot be established stays claimable: no
    `repo`, no `target_repo`, an unresolvable worktree hint and an unknown
    branch name are absence of evidence, not a mismatch. Making them skip
    (the first cut of this filter) broke `atm next` on every ordinary board,
    because a freshly created ticket carries none of these fields while any
    real clone has an origin. Covered by
    tests/test_t946_worktree_gc.py::test_next_and_claim_skip_automated and
    tests/test_t1135_repo_routing.py (the fresh-clone repro and
    test_unattributed_ticket_stays_claimable).

    Unknown callers (a checkout with no origin) retain legacy behavior.
    Known checkouts never infer a ticket's repository from the board
    directory, its title, or a branch spelling that only exists elsewhere.
    Probes are cached for this claim attempt only, not across invocations.
    """
    @lru_cache(maxsize=None)
    def identity(path):
        origin = _git(path, "config", "--get", "remote.origin.url")
        if origin:
            return _normalize(origin)
        common = _git(path, "rev-parse", "--git-common-dir")
        return os.path.realpath(os.path.join(path, common)) if common else ""

    actual = checkout_repo(cwd)
    if not actual:
        return list(tickets), []
    branches = None

    def known_repo(ticket):
        """The ticket's established repository, or "" when unattributed."""
        nonlocal branches
        for field in ("repo", "target_repo"):
            explicit = (ticket.get(field) or "").strip()
            if explicit:
                return _normalize(explicit)
        worktree = ticket.get("worktree") or ""
        # A relative hint would resolve inside an unrelated caller's tree,
        # and an absolute one can name a directory that no longer exists.
        if worktree and os.path.isabs(worktree):
            resolved = identity(worktree)
            if resolved:
                return resolved
        branch = (ticket.get("branch") or "").removeprefix("refs/heads/")
        if branch:
            if branches is None:
                branches = set(_git(cwd, "for-each-ref", "--format=%(refname:short)",
                                    "refs/heads/").splitlines())
            # The branch existing here is evidence for this checkout; it
            # existing nowhere is evidence for no repository at all.
            if branch in branches:
                return actual
        return ""

    ready, skipped = [], []
    for ticket in tickets:
        found = known_repo(ticket)
        (skipped if found and found != actual else ready).append(ticket)
    return ready, skipped
