"""T-839: a REFUSED join must never stamp the session with the refused name.

`tickets join` is where a session declares who it is, so it persists the seat
for the session. The order mattered and was wrong: the write happened at the
top of cmd_join, ahead of every guard. So a refusal -- provider reuse, a bound
alias, an unusable --knowledge-dir -- still left the session answering as the
name it had just been refused. From there a bare `tickets inbox` read (and
marked read) the other seat's private mail and a bare `tickets msg` posted as
that seat: a privilege escalation through a command that FAILED.

Both entry points are pinned, because they carry separate copies of the join:
the root script and the installed wheel's console script.
"""
from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SOURCE = [sys.executable, str(ROOT / "tickets.py")]


def checked(command, **kwargs):
    result = subprocess.run(command, capture_output=True, text=True, **kwargs)
    assert result.returncode == 0, result.stdout + result.stderr
    return result


@pytest.fixture(scope="module")
def wheel(tmp_path_factory):
    directory = tmp_path_factory.mktemp("t839-refused-join-wheel")
    dist = directory / "dist"
    dist.mkdir()
    checked([sys.executable, "-m", "pip", "wheel", "--no-build-isolation",
             "--no-deps", "--wheel-dir", str(dist), str(ROOT)])
    artifact, = dist.glob("ticket_board-*.whl")
    venv = directory / "venv"
    checked([sys.executable, "-m", "venv", str(venv)])
    python = venv / "bin/python"
    checked([str(python), "-m", "pip", "install", "--no-index", "--no-deps", str(artifact)])
    return [str(venv / "bin/tickets")]


def identity_of(board, sid):
    """What the board says THIS session is, or None when it has never said."""
    path = board / ".identities" / hashlib.sha256(sid.encode()).hexdigest()[:16]
    return path.read_text().strip() if path.exists() else None


def seat_state(board, seat):
    """Every file a join writes for a seat, so a refusal can be proven inert."""
    agent = board / "agents" / (seat + ".json")
    return (agent.read_bytes() if agent.exists() else None,
            json.loads((board / "workforce.json").read_text()).get(seat),
            json.loads((board / "roles.json").read_text()).get(seat),
            json.loads((board / "aliases.json").read_text()) if (board / "aliases.json").exists() else {})


def messages(board):
    return [json.loads(line) for line in (board / "messages.jsonl").read_text().splitlines()]


def case(tmp_path, command):
    """alpha (claude, alias cos) and bravo, each with private mail waiting."""
    repo = tmp_path / "repo"
    repo.mkdir()
    home = tmp_path / "home"
    home.mkdir()
    board = repo / ".tickets"
    env = {"PATH": os.environ.get("PATH", "/usr/bin:/bin"), "HOME": str(home),
           "TICKETS_DIR": str(board), "TICKETS_CACHE_DIR": str(home / "cache"),
           "PYTHONDONTWRITEBYTECODE": "1"}

    def run(args, sid, agent, check=True):
        current = dict(env, TICKET_SESSION_ID=sid, TICKET_AGENT=agent)
        if check:
            return checked(command + args, cwd=str(repo), env=current)
        return subprocess.run(command + args, cwd=str(repo), env=current,
                              capture_output=True, text=True)

    run(["init"], "setup-sid", "setup")
    run(["join", "alpha", "--roles", "backend", "--tool", "claude", "--alias", "cos"],
        "alpha-sid", "alpha")
    run(["join", "bravo", "--roles", "backend", "--tool", "claude"], "bravo-sid", "bravo")
    run(["msg", "SECRET_FOR_ALPHA", "--to", "alpha"], "sender-sid", "sender")
    run(["msg", "ONLY_FOR_BRAVO", "--to", "bravo"], "sender-sid", "sender")
    return repo, board, env, run


REFUSALS = {
    # name -> (join args, the guard it must be stopped by)
    "provider-reuse": (["join", "alpha", "--tool", "codex"], "refusing"),
    "alias-clash": (["join", "echo", "--roles", "backend", "--alias", "cos"], "cos"),
}


@pytest.mark.parametrize("entry", ["root", "wheel"])
@pytest.mark.parametrize("refusal", sorted(REFUSALS))
def test_refused_join_leaves_the_session_identity_untouched(tmp_path, wheel, entry, refusal):
    command = SOURCE if entry == "root" else wheel
    repo, board, env, run = case(tmp_path, command)
    args, _ = REFUSALS[refusal]
    before = seat_state(board, "alpha")

    refused = run(args, "bravo-sid", "bravo", check=False)

    assert refused.returncode != 0, refused.stdout
    # The session is still bravo -- the refused name never landed.
    assert identity_of(board, "bravo-sid") == "bravo"
    assert seat_state(board, "alpha") == before


@pytest.mark.parametrize("entry", ["root", "wheel"])
def test_refused_session_cannot_read_or_post_as_the_seat_it_was_refused(tmp_path, wheel, entry):
    command = SOURCE if entry == "root" else wheel
    repo, board, env, run = case(tmp_path, command)

    assert run(["join", "alpha", "--tool", "codex"], "bravo-sid", "bravo",
               check=False).returncode != 0

    # The escalation the refusal exists to prevent: alpha's private mail, read
    # and marked read by bravo, and a message posted under alpha's name.
    inbox = run(["inbox"], "bravo-sid", "bravo")
    assert "ONLY_FOR_BRAVO" in inbox.stdout
    assert "SECRET_FOR_ALPHA" not in inbox.stdout
    run(["msg", "ATTRIBUTION_AFTER_REFUSAL"], "bravo-sid", "bravo")
    posted = next(row for row in reversed(messages(board))
                  if row["text"] == "ATTRIBUTION_AFTER_REFUSAL")
    assert posted["from"] == "bravo"


@pytest.mark.parametrize("entry", ["root", "wheel"])
def test_a_successful_join_still_persists_the_session_identity(tmp_path, wheel, entry):
    command = SOURCE if entry == "root" else wheel
    repo, board, env, run = case(tmp_path, command)

    # Fresh session, ambient env naming someone else: the join is what decides.
    assert identity_of(board, "gamma-sid") is None
    run(["join", "gamma", "--roles", "backend", "--tool", "claude"], "gamma-sid", "stale-ambient")

    assert identity_of(board, "gamma-sid") == "gamma"
    inbox = run(["inbox"], "gamma-sid", "stale-ambient")
    assert "SECRET_FOR_ALPHA" not in inbox.stdout


def test_refused_knowledge_dir_leaves_a_never_joined_session_nameless(tmp_path):
    """Root only: --knowledge-dir is a root-script flag, and it exits mid-join."""
    repo, board, env, run = case(tmp_path, SOURCE)
    missing = tmp_path / "no-such-graph"

    refused = run(["join", "gamma", "--roles", "backend", "--knowledge-dir", str(missing)],
                  "gamma-sid", "stale-ambient", check=False)

    assert refused.returncode != 0
    assert "--knowledge-dir" in refused.stderr
    # Never joined, so the board must not claim this session is anyone -- least
    # of all the name the join was refused for.
    assert identity_of(board, "gamma-sid") is None
    assert not (board / "agents/gamma.json").exists()


LATE_REFUSALS = {
    # The validations that sit LATE in the root join, after roles.json has
    # already been written -- the easiest ones to leave on the wrong side of
    # the identity write, and root-only because the packaged CLI has no such
    # flags. Only these two are reachable from the command line: bad
    # --wake-mode and --lifecycle VALUES are caught by argparse `choices`
    # before cmd_join runs at all, so they would prove nothing here.
    "harness-custom-without-cmd": ["--harness", "custom"],
    "persistent-plus-ephemeral": ["--persistent", "--lifecycle", "ephemeral"],
}


@pytest.mark.parametrize("refusal", sorted(LATE_REFUSALS))
def test_late_flag_validation_refusals_leave_the_session_nameless(tmp_path, refusal):
    repo, board, env, run = case(tmp_path, SOURCE)

    refused = run(["join", "gamma", "--roles", "backend"] + LATE_REFUSALS[refusal],
                  "gamma-sid", "stale-ambient", check=False)

    assert refused.returncode != 0
    assert identity_of(board, "gamma-sid") is None


def test_knowledge_dir_inside_the_board_is_refused_without_renaming_the_session(tmp_path):
    """The other --knowledge-dir exit, from a session that already has a name."""
    repo, board, env, run = case(tmp_path, SOURCE)
    inside = board / "knowledge"
    inside.mkdir(parents=True)
    (inside / "manifest.json").write_text("{}")

    refused = run(["join", "alpha", "--tool", "claude", "--knowledge-dir", str(inside)],
                  "bravo-sid", "bravo", check=False)

    assert refused.returncode != 0
    assert identity_of(board, "bravo-sid") == "bravo"
