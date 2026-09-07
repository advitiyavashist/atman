"""T-322: `tickets quickstart` -- zero to a first ticket claimed by a real agent.

The acceptance criterion for T-322 is a human one ("a second agent who has not
read the code reaches 'first ticket claimed by an agent' from a fresh clone
following README only, under 5 minutes"), so the tests here pin the mechanical
half of it: the command must work on a genuinely fresh repo, be safe to run
twice, and leave a board a worker can immediately claim from.

Two properties are worth more than the rest and are tested directly:

1. IDEMPOTENCE. Quickstart is the command a new user runs when they are not
   sure what happened the first time, so running it twice must not double the
   sample tickets. test_second_run_creates_nothing_new pins the count.

2. THE SAMPLES TEACH THE DEPENDENCY MODEL. Three tickets in a chain, not three
   loose ones: `next` hands out the first and WITHHOLDS the other two. A new
   user's first encounter with "no ticket ready" should be the graph working,
   not the board being broken. test_the_samples_are_a_real_dependency_chain
   asserts the withholding, not just the deps field.

Everything runs the real CLI as a subprocess under a scrubbed env with a
throwaway HOME and no ambient TICKETS_DIR (the test_t263_init_isolation.py /
test_t257_board_guard.py pattern), so no live board is reachable from any of
it. PYTEST_CURRENT_TEST is kept on purpose: it arms T-257's guard, so a case
that ever resolved a board outside the temp dir would refuse loudly rather
than touch something real.

Only tickets.py is exercised. Unlike T-263's board-resolution fix, quickstart
is not part of the T-243 two-copy surface: src/ticket_board/cli.py is a
reduced copy that has no guide/ui/spawn/prompt/watch, which are the commands
quickstart drives. test_quickstart_is_not_claimed_to_be_in_the_reduced_copy
pins that as a deliberate decision rather than an oversight.
"""
import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
TOOL = ROOT / "tickets.py"
REDUCED_COPY = ROOT / "src" / "ticket_board" / "cli.py"


def clean_env(tmp_path, **overrides):
    e = {
        "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
        "HOME": str(tmp_path / "fake-home"),
        "PYTEST_CURRENT_TEST": "tests/test_t322_quickstart.py::simulated (call)",
    }
    for passthrough in ("TMPDIR", "TMP", "TEMP"):
        if os.environ.get(passthrough):
            e[passthrough] = os.environ[passthrough]
    (tmp_path / "fake-home").mkdir(exist_ok=True)
    e.update(overrides)
    return e


def run(cwd, *args, env=None, tmp_path=None):
    return subprocess.run([sys.executable, str(TOOL), *args], capture_output=True,
                          text=True, cwd=str(cwd), env=env or clean_env(tmp_path))


def git(cwd, *args):
    return subprocess.run(
        ["git", "-c", "user.email=t322@test", "-c", "user.name=t322", *args],
        cwd=str(cwd), capture_output=True, text=True, check=True)


def make_repo(path):
    path.mkdir(parents=True, exist_ok=True)
    git(path, "init", "-q", "-b", "main")
    (path / "README").write_text("t322\n")
    git(path, "add", "README")
    git(path, "commit", "-qm", "init")
    return path


def ticket_ids(cwd, tmp_path):
    """Ids the board actually holds, read back through the CLI."""
    r = run(cwd, "list", env=clean_env(tmp_path), tmp_path=tmp_path)
    assert r.returncode == 0, r.stdout + r.stderr
    out = []
    for line in r.stdout.splitlines():
        for word in line.split():
            if word.startswith("T-") and word[2:].isdigit():
                out.append(word)
                break
    return out


@pytest.fixture
def fresh(tmp_path):
    return make_repo(tmp_path / "proj")


# --------------------------------------------------------------------------
# It works at all, on a repo with no board -- the actual first-run case.
# --------------------------------------------------------------------------

def test_quickstart_on_a_fresh_repo_creates_and_binds_a_board(fresh, tmp_path):
    r = run(fresh, "quickstart", "--agent", "alice", tmp_path=tmp_path)
    assert r.returncode == 0, r.stdout + r.stderr
    assert (fresh / ".tickets").is_dir()
    # init ran through cmd_init, so its binding confirmation is still printed
    assert "bound:" in r.stdout


def test_quickstart_runs_init_through_the_real_init_so_the_t263_guard_survives(fresh, tmp_path):
    """Quickstart must not hand-roll board creation around T-263's refusal.

    If it ever writes the board itself, the guard that stops init installing
    into another project stops covering the command new users are told to run.
    """
    outer = make_repo(tmp_path / "outer")
    run(outer, "quickstart", "--agent", "alice", tmp_path=tmp_path)
    nested = make_repo(outer / "nested")

    r = run(nested, "quickstart", "--agent", "bob", tmp_path=tmp_path)
    assert r.returncode != 0, r.stdout + r.stderr
    assert "REFUSING TO INIT" in r.stdout + r.stderr
    assert not (nested / ".tickets").exists()


def test_a_new_flag_on_init_cannot_silently_break_quickstart(fresh, tmp_path):
    """Regression guard for the namespace quickstart builds for cmd_init.

    quickstart calls cmd_init with a hand-built Namespace. When init grows an
    argument, that Namespace goes stale and the failure lands on a new user's
    very first command as an AttributeError. This test is the thing that fails
    first instead.
    """
    r = run(fresh, "quickstart", "--agent", "alice", tmp_path=tmp_path)
    assert "AttributeError" not in r.stderr, r.stderr
    assert r.returncode == 0, r.stdout + r.stderr


# --------------------------------------------------------------------------
# Idempotence.
# --------------------------------------------------------------------------

def test_second_run_creates_nothing_new(fresh, tmp_path):
    run(fresh, "quickstart", "--agent", "alice", tmp_path=tmp_path)
    after_one = ticket_ids(fresh, tmp_path)
    assert len(after_one) == 3, after_one

    r = run(fresh, "quickstart", "--agent", "alice", tmp_path=tmp_path)
    assert r.returncode == 0, r.stdout + r.stderr
    assert ticket_ids(fresh, tmp_path) == after_one
    assert "already here" in r.stdout


def test_second_run_says_so_rather_than_failing(fresh, tmp_path):
    run(fresh, "quickstart", "--agent", "alice", tmp_path=tmp_path)
    r = run(fresh, "quickstart", "--agent", "alice", tmp_path=tmp_path)
    assert r.returncode == 0
    assert "already here" in r.stdout


def test_samples_deleted_by_hand_are_recreated(fresh, tmp_path):
    """The marker file must not outlive the tickets it describes."""
    run(fresh, "quickstart", "--agent", "alice", tmp_path=tmp_path)
    for path in (fresh / ".tickets").glob("T-*.json"):
        path.unlink()
    r = run(fresh, "quickstart", "--agent", "alice", tmp_path=tmp_path)
    assert r.returncode == 0, r.stdout + r.stderr
    assert len(ticket_ids(fresh, tmp_path)) == 3


# --------------------------------------------------------------------------
# The samples teach the model.
# --------------------------------------------------------------------------

def test_the_samples_are_a_real_dependency_chain(fresh, tmp_path):
    """Not three loose tickets: `next` gives one and withholds the other two."""
    run(fresh, "quickstart", "--agent", "alice", tmp_path=tmp_path)

    first = run(fresh, "next", env=clean_env(tmp_path, TICKET_AGENT="alice"), tmp_path=tmp_path)
    assert first.returncode == 0, first.stdout + first.stderr
    assert "IN PROGRESS" in first.stdout

    second = run(fresh, "next", env=clean_env(tmp_path, TICKET_AGENT="bob"), tmp_path=tmp_path)
    assert "no ticket ready" in second.stdout + second.stderr, second.stdout + second.stderr


def test_a_real_agent_can_claim_the_first_ticket(fresh, tmp_path):
    """The acceptance criterion, mechanically: first ticket claimed by an agent."""
    run(fresh, "quickstart", "--agent", "alice", tmp_path=tmp_path)
    claimed = run(fresh, "next", env=clean_env(tmp_path, TICKET_AGENT="alice"), tmp_path=tmp_path)
    assert claimed.returncode == 0, claimed.stdout + claimed.stderr

    mine = run(fresh, "mine", env=clean_env(tmp_path, TICKET_AGENT="alice"), tmp_path=tmp_path)
    assert "T-" in mine.stdout, mine.stdout + mine.stderr


def test_the_agent_is_registered_so_next_has_someone_to_route_to(fresh, tmp_path):
    r = run(fresh, "quickstart", "--agent", "alice", "--roles", "backend", tmp_path=tmp_path)
    assert "joined as alice" in r.stdout, r.stdout
    who = run(fresh, "who", tmp_path=tmp_path)
    assert "alice" in who.stdout, who.stdout + who.stderr


# --------------------------------------------------------------------------
# It tells the user what to do next -- the whole point of the command.
# --------------------------------------------------------------------------

def test_it_prints_the_three_commands_and_the_ui_url(fresh, tmp_path):
    r = run(fresh, "quickstart", "--agent", "alice", tmp_path=tmp_path)
    out = r.stdout
    assert "tickets next" in out
    assert "tickets update" in out
    assert "tickets review" in out
    assert "http://127.0.0.1:8765" in out


def test_it_names_the_board_it_wrote_to(fresh, tmp_path):
    r = run(fresh, "quickstart", "--agent", "alice", tmp_path=tmp_path)
    assert str(fresh / ".tickets") in r.stdout


# --------------------------------------------------------------------------
# Cleanup, and not starting things behind the user's back.
# --------------------------------------------------------------------------

def test_remove_deletes_the_samples(fresh, tmp_path):
    run(fresh, "quickstart", "--agent", "alice", tmp_path=tmp_path)
    r = run(fresh, "quickstart", "--remove", tmp_path=tmp_path)
    assert r.returncode == 0, r.stdout + r.stderr
    assert ticket_ids(fresh, tmp_path) == []


def test_remove_is_safe_when_there_is_nothing_to_remove(fresh, tmp_path):
    run(fresh, "quickstart", "--agent", "alice", tmp_path=tmp_path)
    run(fresh, "quickstart", "--remove", tmp_path=tmp_path)
    r = run(fresh, "quickstart", "--remove", tmp_path=tmp_path)
    assert r.returncode == 0, r.stdout + r.stderr


def test_remove_leaves_tickets_the_user_created_alone(fresh, tmp_path):
    run(fresh, "quickstart", "--agent", "alice", tmp_path=tmp_path)
    made = run(fresh, "create", "My own real ticket", tmp_path=tmp_path)
    assert made.returncode == 0, made.stdout + made.stderr

    run(fresh, "quickstart", "--remove", tmp_path=tmp_path)
    left = ticket_ids(fresh, tmp_path)
    assert len(left) == 1, left
    shown = run(fresh, "show", left[0], tmp_path=tmp_path)
    assert "My own real ticket" in shown.stdout


def test_with_agent_does_not_launch_a_background_process(fresh, tmp_path):
    """It prints the spawn line; it never starts a worker unasked."""
    r = run(fresh, "quickstart", "--agent", "alice", "--with-agent", "worker1",
            tmp_path=tmp_path)
    assert r.returncode == 0, r.stdout + r.stderr
    assert "not launched for you" in r.stdout or "no agent harness found" in r.stdout


# --------------------------------------------------------------------------
# The deliberate scope decision, pinned so it reads as a choice.
# --------------------------------------------------------------------------

def test_quickstart_is_not_claimed_to_be_in_the_reduced_copy():
    """cli.py is a reduced copy without guide/ui/spawn/prompt/watch.

    Quickstart drives exactly those, so it lives in tickets.py alone. If
    someone later ports it, they should port its dependencies too -- and this
    assertion is where they will be told so.
    """
    reduced = REDUCED_COPY.read_text()
    missing = [name for name in ("cmd_guide", "cmd_ui", "cmd_spawn", "cmd_prompt")
               if "def %s(" % name not in reduced]
    assert missing, (
        "cli.py has grown the commands quickstart drives (%s); it may now be "
        "worth porting cmd_quickstart there too." % missing)
    assert "def cmd_quickstart(" not in reduced
