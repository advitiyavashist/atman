"""T-273: T-257's board guard must not take its safety boundary from $TMPDIR.

The guard refuses, while PYTEST_CURRENT_TEST is set, any board that resolves
outside the temp dir -- the T-256 backstop. It compared against
tempfile.gettempdir(), which honours $TMPDIR, so the boundary was settable by
the very caller it polices:

    cd <steer> && env -i PATH=... HOME=... \
        PYTEST_CURRENT_TEST="fake::test (call)" TMPDIR=/Users/<operator>/Downloads \
        python3 tickets.py board --quiet

printed the full live board, exit 0, no refusal. Without the TMPDIR override
the identical call refused. Had the subcommand been `create`, that is T-256
happening again.

The first fix for this was itself defective and half of this file exists for
that: alongside the platform-sourced roots it trusted any path containing a
`pytest-of-*` component, on the reasoning that pytest creates that name rather
than reading it from the environment. True of pytest, irrelevant as a guard --
any process can mkdir that name anywhere, so `mkdir pytest-of-evil` disabled
the guard for everything beneath it. An opt-out spelled as a filename is still
an opt-out. The A/B below is the control that made that finding credible and
it ships with the fix: two boards under the SAME untrusted parent differing
only by a `pytest-of-*` path component, which must now be treated identically.

THE FIXTURE PROBLEM, and why decoy_root() looks paranoid: an "outside tmp"
decoy is only meaningful if it really is outside. An earlier draft put it
beside the repo checkout -- but the red/green baseline worktree lived under
/private/tmp, so the decoy was INSIDE a trusted root and two tests passed
vacuously on broken code. So this file computes the trusted roots ITSELF,
duplicating the logic on purpose rather than importing the code under test,
and skips loudly if it cannot find a base that is genuinely outside them.
"""
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
TOOLS = [ROOT / "tickets.py", ROOT / "src" / "ticket_board" / "cli.py"]
TOOL_IDS = ["tickets.py", "cli.py"]

FAKE_PYTEST = "tests/test_t273_tmpdir_guard.py::simulated (call)"


def independent_trusted_roots():
    """The trusted roots, recomputed here on purpose.

    Deliberately NOT imported from the tool: a fixture that asks the code
    under test where the safe zone is cannot prove a path is outside it.
    """
    roots = []
    try:
        v = os.confstr(65537)  # _CS_DARWIN_USER_TEMP_DIR
    except (ValueError, OSError, AttributeError):
        v = None
    if v:
        roots.append(v)
    roots += ["/tmp", "/private/tmp", "/var/tmp", "/private/var/tmp", "/usr/tmp"]
    # $TMPDIR is included here even though the tool must NOT trust it, so that
    # "outside every trusted root" is the stricter claim of the two.
    if os.environ.get("TMPDIR"):
        roots.append(os.environ["TMPDIR"])
    out = []
    for r in roots:
        real = os.path.realpath(r)
        if real not in out:
            out.append(real)
    return out


def is_outside_trusted(path):
    real = os.path.realpath(path)
    return not any(real == r or real.startswith(r + os.sep)
                   for r in independent_trusted_roots())


@pytest.fixture
def decoy_root():
    """A scratch directory that is genuinely outside every trusted temp root.

    Stands in for the live steer board, which is what an attacker actually
    aims the guard away from.
    """
    for base in (Path.home(), ROOT.parent, Path.cwd()):
        cand = Path(base) / (".t273-decoy-%d" % os.getpid())
        if is_outside_trusted(cand):
            cand.mkdir(parents=True, exist_ok=True)
            try:
                yield cand
            finally:
                shutil.rmtree(cand, ignore_errors=True)
            return
    pytest.skip("no writable base outside every trusted temp root; the decoy "
                "would be inside the safe zone and the test would pass vacuously")


def run(tool, cwd, *args, **env_overrides):
    e = {
        "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
        "HOME": os.environ.get("HOME", "/tmp"),
        "PYTEST_CURRENT_TEST": FAKE_PYTEST,
    }
    if os.environ.get("TMPDIR"):
        e["TMPDIR"] = os.environ["TMPDIR"]
    e.update({k: v for k, v in env_overrides.items() if v is not None})
    for k, v in env_overrides.items():
        if v is None:
            e.pop(k, None)
    return subprocess.run([sys.executable, str(tool), *args], capture_output=True,
                          text=True, cwd=str(cwd), env=e)


def make_board(parent, name=".tickets"):
    b = parent / name
    b.mkdir(parents=True, exist_ok=True)
    (b / "T-001.json").write_text('{"id": "T-001", "title": "decoy", "status": "open"}')
    return b


# --------------------------------------------------------------------------
# The filed defect: a poisoned TMPDIR must not widen the safe zone.
# --------------------------------------------------------------------------

@pytest.mark.parametrize("tool", TOOLS, ids=TOOL_IDS)
def test_poisoned_tmpdir_does_not_silence_the_guard(tool, decoy_root, tmp_path):
    board = make_board(decoy_root)
    assert is_outside_trusted(board)

    # TMPDIR names an ancestor of the decoy: under the old guard this made the
    # board resolve as "inside tmp" and the refusal never fired.
    r = run(tool, tmp_path, "board", "--quiet",
            TICKETS_DIR=str(board), TMPDIR=str(decoy_root))

    assert r.returncode != 0, (
        "poisoned TMPDIR silenced the guard; board printed:\n%s" % r.stdout)
    assert "REFUSING TO USE BOARD" in r.stderr, r.stderr


@pytest.mark.parametrize("tool", TOOLS, ids=TOOL_IDS)
def test_control_same_board_without_the_tmpdir_override_also_refuses(tool, decoy_root, tmp_path):
    """The A/B partner of the test above: identical call, TMPDIR not poisoned.

    Both must refuse. If only this one refuses, the guard is TMPDIR-controlled.
    """
    board = make_board(decoy_root)
    r = run(tool, tmp_path, "board", "--quiet", TICKETS_DIR=str(board))
    assert r.returncode != 0, r.stdout
    assert "REFUSING TO USE BOARD" in r.stderr, r.stderr


@pytest.mark.parametrize("tool", TOOLS, ids=TOOL_IDS)
def test_poisoned_tmpdir_cannot_write_a_ticket_to_an_untrusted_board(tool, decoy_root, tmp_path):
    """`board` only reads. This is the same attack with the write verb, which
    is what actually happened in T-256."""
    board = decoy_root / ".tickets"
    board.mkdir(parents=True)
    before = set(board.glob("T-*.json"))

    r = run(tool, tmp_path, "create", "probe ticket", "--role", "backend",
            TICKETS_DIR=str(board), TMPDIR=str(decoy_root), TICKET_AGENT="t273-probe")

    assert r.returncode != 0, r.stdout
    assert set(board.glob("T-*.json")) == before, "the guard must refuse BEFORE any write"


# --------------------------------------------------------------------------
# The A/B control that produced the second finding: a directory NAME must not
# confer trust. These two differ only by the `pytest-of-*` component.
# --------------------------------------------------------------------------

@pytest.mark.parametrize("tool", TOOLS, ids=TOOL_IDS)
def test_a_directory_named_pytest_of_outside_trusted_roots_is_not_trusted(tool, decoy_root, tmp_path):
    board = make_board(decoy_root / "pytest-of-evil" / "pytest-1")
    assert is_outside_trusted(board), "decoy must be outside the safe zone to mean anything"

    r = run(tool, tmp_path, "board", "--quiet", TICKETS_DIR=str(board))

    assert r.returncode != 0, (
        "a path was trusted for being NAMED pytest-of-*; board printed:\n%s" % r.stdout)
    assert "REFUSING TO USE BOARD" in r.stderr, r.stderr


@pytest.mark.parametrize("tool", TOOLS, ids=TOOL_IDS)
def test_control_same_parent_without_the_pytest_of_component_also_refuses(tool, decoy_root, tmp_path):
    """The other half of the A/B: same untrusted parent, ordinary directory
    name. Both must refuse identically -- that equality IS the property."""
    board = make_board(decoy_root / "plain" / "sub")
    r = run(tool, tmp_path, "board", "--quiet", TICKETS_DIR=str(board))
    assert r.returncode != 0, r.stdout
    assert "REFUSING TO USE BOARD" in r.stderr, r.stderr


@pytest.mark.parametrize("tool", TOOLS, ids=TOOL_IDS)
def test_pytest_of_name_cannot_smuggle_a_write_either(tool, decoy_root, tmp_path):
    board = decoy_root / "pytest-of-evil" / "pytest-1" / ".tickets"
    board.mkdir(parents=True)
    before = set(board.glob("T-*.json"))
    r = run(tool, tmp_path, "create", "probe ticket", "--role", "backend",
            TICKETS_DIR=str(board), TICKET_AGENT="t273-probe")
    assert r.returncode != 0, r.stdout
    assert set(board.glob("T-*.json")) == before


# --------------------------------------------------------------------------
# The guard must still let legitimate work through -- a refusing-everything
# guard is not a fix.
# --------------------------------------------------------------------------

@pytest.mark.parametrize("tool", TOOLS, ids=TOOL_IDS)
def test_a_real_tmp_path_board_still_works(tool, tmp_path):
    board = tmp_path / ".tickets"
    board.mkdir()
    r = run(tool, tmp_path, "create", "legit ticket", "--role", "backend",
            TICKETS_DIR=str(board), TICKET_AGENT="t273-probe")
    assert r.returncode == 0, r.stdout + r.stderr
    assert "REFUSING" not in r.stderr
    assert list(board.glob("T-*.json")), "a legitimate tmp_path board must still work"


@pytest.mark.parametrize("tool", TOOLS, ids=TOOL_IDS)
def test_outside_pytest_the_guard_stays_out_of_the_way(tool, decoy_root, tmp_path):
    """No PYTEST_CURRENT_TEST means a real agent at a shell: unaffected."""
    board = make_board(decoy_root)
    r = run(tool, tmp_path, "board", "--quiet",
            TICKETS_DIR=str(board), PYTEST_CURRENT_TEST=None)
    assert r.returncode == 0, r.stdout + r.stderr
    assert "REFUSING" not in r.stderr


# --------------------------------------------------------------------------
# The structural property, stated directly rather than via its consequences.
# --------------------------------------------------------------------------

@pytest.mark.parametrize("tool", TOOLS, ids=TOOL_IDS)
def test_trusted_roots_are_identical_with_and_without_a_poisoned_tmpdir(tool, tmp_path, monkeypatch):
    import importlib.util
    spec = importlib.util.spec_from_file_location("t273_tool_%s" % tool.stem, tool)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)

    baseline = mod._trusted_tmp_roots()
    monkeypatch.setenv("TMPDIR", str(tmp_path / "poison"))
    (tmp_path / "poison").mkdir()
    poisoned = mod._trusted_tmp_roots()

    assert poisoned == baseline, "the environment moved the safety boundary"
    assert baseline, "the trusted root list must never be empty"
