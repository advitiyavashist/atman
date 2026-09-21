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


def filter_for_checkout(tickets, cwd):
    """Explicit repo wins; otherwise use a real worktree/branch hint.

    Unknown callers retain legacy behavior. Known checkouts never infer a
    ticket's repository from the board directory, title, or branch spelling.
    Probes are cached only for this claim attempt, not across CLI invocations.
    """
    @lru_cache(maxsize=None)
    def identity(path):
        origin = _git(path, "config", "--get", "remote.origin.url")
        if origin:
            return _normalize(origin)
        common = _git(path, "rev-parse", "--git-common-dir")
        return os.path.realpath(os.path.join(path, common)) if common else ""

    actual = _normalize(_git(cwd, "config", "--get", "remote.origin.url"))
    if not actual:
        return list(tickets), []
    branches = None

    def matches(ticket):
        nonlocal branches
        expected = (ticket.get("repo") or "").strip()
        if expected:
            return _normalize(expected) == actual
        worktree = ticket.get("worktree") or ""
        if worktree:
            # Relative stale hints must not resolve in an unrelated caller's tree.
            return os.path.isabs(worktree) and identity(worktree) == actual
        branch = ticket.get("branch") or ""
        if not branch:
            return False
        if branches is None:
            branches = set(_git(cwd, "for-each-ref", "--format=%(refname:short)",
                                "refs/heads/").splitlines())
        return branch.removeprefix("refs/heads/") in branches

    ready, skipped = [], []
    for ticket in tickets:
        (ready if matches(ticket) else skipped).append(ticket)
    return ready, skipped
