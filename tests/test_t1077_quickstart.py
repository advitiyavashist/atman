"""T-1077: `atm quickstart --gate` -- a new user feels the accept gate.

The claim the demo makes is strong ("work stays blocked until a DIFFERENT seat
accepts the commit"), so these tests pin the parts that could be faked:

1. THE GATE MOMENT IS CAUSED BY THE GATE. The happy path asserts the refusal
   that the real CLI printed (`blocked: T-002 -- ...`, and a refused
   `atm claim T-002`) and then the release, and the demo itself exits non-zero
   if T-002 ever opens without an accept -- so a print statement alone cannot
   make these tests pass.
2. IT NEVER SELF-ACCEPTS. The recorded accept event is read out of the scratch
   board and must carry a `by` that is not the ticket's owner.
3. NO AUTH MEANS NO RUN. A logged-out CLI gets the T-1043/#224 refusal, the
   login command, and a labelled walkthrough with no board, no accept record
   and no hex sha anywhere in the output.
4. A USAGE LIMIT IS REPORTED, NOT HIDDEN. A harness that comes back with a
   provider limit is said out loud, its reading reaches the provider-usage
   ledger (T-1040 reader, the T-1076 formatter), and nothing is invented.
5. IT WRITES ONLY INSIDE THE DIR IT PRINTED. HOME, the caller's cwd and a
   pre-existing board next door are hash-snapshotted before and after.

Everything runs the real CLI as a subprocess under `env -i`-style isolation: a
throwaway HOME, a PATH holding only a stub harness and coreutils, TMPDIR inside
tmp_path (so the scratch dir the demo makes is collected with the test), and no
ambient TICKETS_DIR. PYTEST_CURRENT_TEST is kept deliberately: it arms T-257's
board guard and keeps the provider-usage refresh off the network.

T-1006/T-1007/T-1008 are onboarding defects on the same surface, fixed with the
demo and pinned at the bottom of this file.
"""
import hashlib
import json
import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
TOOL = ROOT / "tickets.py"
CLI = ROOT / "src" / "ticket_board" / "cli.py"

GATE_REASON = "marked done without verification; accept it or reopen"
SHA_RE = re.compile(r"\b[0-9a-f]{7,40}\b")

# A stub that behaves like a coding CLI: `auth status` reports a live login and
# the print-mode run makes a real commit in the worktree it was handed.
HARNESS_OK = """#!/bin/sh
if [ "$1" = "auth" ]; then echo "Login: demo@example.com (Pro)"; exit 0; fi
printf 'atman demo\\n' >> demo.txt
git add demo.txt >/dev/null 2>&1
git -c user.email=a@demo -c user.name=a commit -qm "T-001: add a line to demo.txt" >/dev/null 2>&1
echo "wrote demo.txt and committed it"
exit 0
"""
HARNESS_LOGGED_OUT = """#!/bin/sh
if [ "$1" = "auth" ]; then echo "Not logged in. Please run /login"; exit 1; fi
echo "no session"; exit 1
"""
HARNESS_LIMITED = """#!/bin/sh
if [ "$1" = "auth" ]; then echo "Login: demo@example.com (Pro)"; exit 0; fi
echo "You've hit your weekly limit. Your limit resets at 2099-01-01T00:00:00Z"
exit 1
"""


def make_env(tmp_path, harness_script, name="claude"):
    """Scrubbed env: throwaway HOME, only the stub on PATH, TMPDIR in tmp_path."""
    bindir = tmp_path / "bin"
    bindir.mkdir(exist_ok=True)
    script = bindir / name
    script.write_text(harness_script)
    script.chmod(0o755)
    home = tmp_path / "home"
    home.mkdir(exist_ok=True)
    tmpdir = tmp_path / "tmpdir"
    tmpdir.mkdir(exist_ok=True)
    cwd = tmp_path / "cwd"
    cwd.mkdir(exist_ok=True)
    env = {
        "PATH": os.pathsep.join([str(bindir), "/usr/bin", "/bin", "/usr/sbin"]),
        "HOME": str(home),
        "TMPDIR": str(tmpdir),
        "PYTHONPATH": str(ROOT / "src"),
        "PYTHONDONTWRITEBYTECODE": "1",
        # Arms T-257's guard and keeps provider usage off the network.
        "PYTEST_CURRENT_TEST": "tests/test_t1077_quickstart.py::simulated (call)",
    }
    return env, cwd


def run_gate(tmp_path, harness_script, *extra, name="claude"):
    env, cwd = make_env(tmp_path, harness_script, name=name)
    r = subprocess.run(
        [sys.executable, str(TOOL), "quickstart", "--gate", *extra],
        capture_output=True, text=True, env=env, cwd=str(cwd), timeout=600)
    return r, cwd


def scratch_of(text):
    for line in (text or "").splitlines():
        if line.startswith("scratch:"):
            return Path(line.split(":", 1)[1].strip())
    return None


def ticket(scratch, tid):
    return json.loads((scratch / "repo" / ".tickets" / (tid + ".json")).read_text())


def accept_event(t):
    for ev in reversed(list(t.get("review_events") or [])):
        if isinstance(ev, dict) and ev.get("kind") == "accept":
            return ev
    return {}


def snapshot(path):
    """{relative path: sha1} for every file under path."""
    out = {}
    for base, _dirs, files in os.walk(path):
        for f in files:
            p = Path(base) / f
            try:
                out[str(p.relative_to(path))] = hashlib.sha1(p.read_bytes()).hexdigest()
            except OSError:
                out[str(p.relative_to(path))] = "unreadable"
    return out


# ---- 1. the happy path -----------------------------------------------------


@pytest.fixture(scope="module")
def happy(tmp_path_factory):
    tmp_path = tmp_path_factory.mktemp("t1077-happy")
    r, cwd = run_gate(tmp_path, HARNESS_OK)
    assert r.returncode == 0, r.stdout + r.stderr
    scratch = scratch_of(r.stdout)
    assert scratch and scratch.is_dir(), r.stdout
    return r, scratch, cwd, tmp_path


def test_happy_path_narrates_every_step_and_says_where_it_is(happy):
    r, scratch, cwd, tmp_path = happy
    out = r.stdout
    # The scratch dir is printed, and it is a temp dir, not the caller's cwd.
    assert str(scratch).startswith(str(tmp_path / "tmpdir"))
    assert "your repo and your board are not touched" in out
    for step, label in ((1, "preflight"), (2, "repo"), (3, "board"), (4, "seats"),
                        (5, "tickets"), (6, "dispatch"), (7, "gate"), (8, "released")):
        assert re.search(r"^%d/8 %s" % (step, label), out, re.M), (step, label, out)
    # one short line per step, not a wall of harness output
    for line in out.splitlines():
        if re.match(r"^\d/8 ", line):
            assert len(line) <= 120, line
    # the three closing lines
    assert "what happened:" in out
    assert "in your repo:  cd <your repo> && atm quickstart --agent <you>" in out
    assert "atm agents" in out and "atm ui" in out
    # and the provider usage line, so cost is visible too
    assert re.search(r"^usage:\s+claude ", out, re.M), out


def test_the_gate_moment_is_the_gate_refusing_not_a_print(happy):
    r, scratch, _cwd, _tmp = happy
    out = r.stdout
    assert "T-002 blocked: T-001 has no accept from another seat" in out
    # the CLI's own words, quoted under the narration
    assert "blocked: T-002 -- T-001 %s" % GATE_REASON in out
    assert "atm claim T-002 (as bob) refused: T-002: T-001 %s" % GATE_REASON in out
    # and the board agrees: T-002 was never claimable before the accept
    notes = " ".join(n.get("text") or "" for n in ticket(scratch, "T-002").get("notes") or [])
    assert "T-001" in notes


def test_release_moment_names_the_accepted_sha(happy):
    r, scratch, _cwd, _tmp = happy
    out = r.stdout
    t1 = ticket(scratch, "T-001")
    ev = accept_event(t1)
    assert ev, t1
    full = ev["sha"]
    short = full[:7]
    assert "T-002 released by bob accepting %s" % short in out
    assert "T-001 accepted %s by bob" % full in out
    assert "unblocked: T-002" in out
    # the release is bound to the commit that actually exists in the scratch repo
    head = subprocess.check_output(
        ["git", "rev-parse", "HEAD"], cwd=str(scratch / "repo"), text=True).strip()
    assert full == head
    assert ticket(scratch, "T-002")["status"] in ("open", "claimed")


def test_the_accept_is_recorded_by_a_different_seat_than_the_author(happy):
    """No self-accept: the board record must carry author != evaluator."""
    _r, scratch, _cwd, _tmp = happy
    t1 = ticket(scratch, "T-001")
    ev = accept_event(t1)
    author = (t1.get("owner") or "").strip()
    evaluator = (ev.get("by") or "").strip()
    assert author == "alice", t1
    assert evaluator == "bob", ev
    assert evaluator != author
    assert len(ev.get("sha") or "") == 40
    # and the demo printed that comparison from the record, not as a slogan
    assert "author alice != evaluator bob" in _r.stdout


# ---- 2. no coding CLI is authenticated -------------------------------------


def test_logged_out_says_so_prints_login_and_only_walks_through(tmp_path):
    r, cwd = run_gate(tmp_path, HARNESS_LOGGED_OUT)
    out = r.stdout + r.stderr
    assert r.returncode == 0, out
    # the #224 preflight refusal, verbatim from ticket_board.preflight
    assert "preflight: claude is logged out" in out
    assert "log in with:  claude auth login" in out
    assert "atm harness auth" in out
    # a walkthrough that cannot be mistaken for a run
    assert "WALKTHROUGH ONLY" in out
    assert "That was a WALKTHROUGH, not a run" in out
    assert "no accept record" in out
    assert out.count("would ") >= 8
    # nothing was created: no scratch dir, no board, no sha anywhere
    assert scratch_of(out) is None
    assert not list((tmp_path / "tmpdir").iterdir())
    assert not list(cwd.iterdir())
    assert not (cwd / ".tickets").exists()
    for token in SHA_RE.findall(out):
        pytest.fail("walkthrough printed a sha-like token %r" % token)


def test_dry_run_is_a_walkthrough_even_with_a_live_login(tmp_path):
    r, cwd = run_gate(tmp_path, HARNESS_OK, "--dry-run")
    out = r.stdout
    assert r.returncode == 0, out + r.stderr
    assert "WALKTHROUGH ONLY" in out
    assert scratch_of(out) is None
    assert not list((tmp_path / "tmpdir").iterdir())
    assert not list(cwd.iterdir())
    for token in SHA_RE.findall(out):
        pytest.fail("dry run printed a sha-like token %r" % token)


def test_no_harness_installed_at_all_falls_back_to_the_walkthrough(tmp_path):
    env, cwd = make_env(tmp_path, HARNESS_OK, name="not-a-harness")
    r = subprocess.run([sys.executable, str(TOOL), "quickstart", "--gate"],
                       capture_output=True, text=True, env=env, cwd=str(cwd),
                       timeout=600)
    out = r.stdout + r.stderr
    assert r.returncode == 0, out
    assert "no coding CLI found on PATH" in out
    assert "WALKTHROUGH ONLY" in out
    assert not list((tmp_path / "tmpdir").iterdir())


# ---- 3. the provider is at its usage limit ---------------------------------


def test_usage_limit_is_reported_and_reaches_the_usage_ledger(tmp_path):
    r, _cwd = run_gate(tmp_path, HARNESS_LIMITED)
    out = r.stdout
    assert r.returncode == 0, out + r.stderr
    scratch = scratch_of(out)
    assert scratch, out
    # said out loud, with the provider's own sentence, and never papered over
    assert "produced no commit" in out
    assert "weekly limit" in out
    assert "the demo commits the one-line change itself, as alice" in out
    # the T-1040 ledger got the reading, and the shared formatter printed it
    usage = [ln for ln in out.splitlines() if ln.startswith("usage:")]
    assert usage and "limited" in usage[0], usage
    assert "resets 2099-01-01T00:00:00Z" in usage[0], usage
    ledger = json.loads((scratch / "repo" / ".tickets" / "provider_usage.json").read_text())
    assert ledger["providers"]["claude"]["status"] == "limited"
    # a usage limit is not an auth failure: the gate is still demonstrated,
    # and the accept is still recorded by the other seat
    assert "T-002 blocked: T-001 has no accept from another seat" in out
    ev = accept_event(ticket(scratch, "T-001"))
    assert ev.get("by") == "bob" and ev.get("by") != ticket(scratch, "T-001").get("owner")


# ---- 4. write footprint ----------------------------------------------------


def test_nothing_is_written_outside_the_scratch_dir(tmp_path):
    env, cwd = make_env(tmp_path, HARNESS_OK)
    home = Path(env["HOME"])
    # a HOME that already has things in it, and somebody else's board next door
    (home / ".claude").mkdir(parents=True, exist_ok=True)
    (home / ".claude" / "settings.json").write_text('{"kept": true}\n')
    (home / ".atman-note").write_text("do not touch\n")
    other = tmp_path / "their-repo" / ".tickets"
    other.mkdir(parents=True, exist_ok=True)
    (other / "T-900.json").write_text('{"id": "T-900", "status": "open"}\n')
    before_home, before_other = snapshot(home), snapshot(other)

    r = subprocess.run([sys.executable, str(TOOL), "quickstart", "--gate"],
                       capture_output=True, text=True, env=env, cwd=str(cwd),
                       timeout=600)
    assert r.returncode == 0, r.stdout + r.stderr
    scratch = scratch_of(r.stdout)
    assert scratch and scratch.is_dir()

    assert snapshot(home) == before_home, "the demo wrote inside HOME"
    assert snapshot(other) == before_other, "the demo wrote in another board"
    assert not list(cwd.iterdir()), "the demo wrote in the caller's cwd"
    # everything it did make is under the one path it printed
    made = set(os.listdir(tmp_path / "tmpdir"))
    assert made == {scratch.name}, made
    assert (scratch / "repo" / ".tickets" / "T-001.json").exists()


# ---- 5. onboarding defects fixed on the same surface -----------------------


def test_t1006_no_surface_tells_a_worker_done_hands_off(tmp_path):
    """T-1006: `done` never released dependents; only an accept does."""
    env, cwd = make_env(tmp_path, HARNESS_OK)
    repo = tmp_path / "t1006"
    repo.mkdir()
    for args in (["init", "-q", "-b", "main", "."], ["add", "-A"]):
        subprocess.run(["git", *args], cwd=str(repo), check=False,
                       capture_output=True)
    (repo / "README").write_text("t1006\n")
    subprocess.run(["git", "-c", "user.email=t@t", "-c", "user.name=t",
                    "commit", "-qm", "init", "-a"], cwd=str(repo),
                   capture_output=True)

    def atm(*args, agent="alice"):
        e = dict(env, TICKETS_DIR=str(repo / ".tickets"), TICKET_AGENT=agent,
                 TICKET_SESSION_ID="t1006-" + agent)
        return subprocess.run([sys.executable, str(TOOL), *args], cwd=str(repo),
                              capture_output=True, text=True, env=e, timeout=300)

    assert atm("init").returncode == 0
    assert atm("join", "alice", "--roles", "backend").returncode == 0
    assert atm("create", "work", "--role", "backend").returncode == 0
    board = atm("board")
    assert board.returncode == 0, board.stderr
    assert "`atm done <id>" not in board.stdout
    assert "hands off to dependents" not in board.stdout
    assert "atm accept <id> --sha <sha>" in board.stdout
    assert "DIFFERENT seat" in board.stdout
    # AGENTS.md is what an external agent reads; it must not teach the bypass
    agents_md = (repo / "AGENTS.md").read_text()
    assert "until those dependencies are marked done" not in agents_md
    assert "atm accept <id> --sha <sha>" in agents_md
    assert "releases the dependents" in agents_md
    # both copies of the CLI carry the same wording (T-243 two-copy surface)
    for path in (TOOL, CLI):
        src = path.read_text()
        assert "hands off to dependents" not in src, path
        assert "DIFFERENT seat runs `atm accept" in src, path


def test_t1007_harness_available_keeps_this_boards_policy_off_a_new_project(tmp_path):
    """T-1007: Atman's own CEO/CoS staffing policy is not printed on your board."""
    env, cwd = make_env(tmp_path, HARNESS_OK)
    repo = tmp_path / "t1007"
    repo.mkdir()
    subprocess.run(["git", "init", "-q", "-b", "main", "."], cwd=str(repo),
                   capture_output=True)
    (repo / "README").write_text("t1007\n")
    subprocess.run(["git", "-c", "user.email=t@t", "-c", "user.name=t", "add", "-A"],
                   cwd=str(repo), capture_output=True)
    subprocess.run(["git", "-c", "user.email=t@t", "-c", "user.name=t",
                    "commit", "-qm", "init"], cwd=str(repo), capture_output=True)

    def atm(*args, agent="alice"):
        e = dict(env, TICKETS_DIR=str(repo / ".tickets"), TICKET_AGENT=agent,
                 TICKET_SESSION_ID="t1007-" + agent, TICKETS_USAGE_REFRESH="0")
        return subprocess.run([sys.executable, str(TOOL), *args], cwd=str(repo),
                              capture_output=True, text=True, env=e, timeout=300)

    assert atm("init").returncode == 0
    assert atm("join", "alice", "--roles", "backend").returncode == 0
    assert atm("create", "their own work", "--role", "backend").returncode == 0
    out = atm("harness", "available")
    assert out.returncode == 0, out.stderr
    blob = out.stdout
    # a board with work on it is no longer treated as Atman's own board
    for leak in ("Announce the Atman role", "CEO does not claim worker tickets",
                 "which should CoS start", "CoS does not spawn them",
                 "grok-worker"):
        assert leak not in blob, leak
    assert "This board already has work and seats on it" in blob
    # the catalog itself stays generic and still reports capability
    assert "INTEGRATIONS" in blob and "if they say yes:" in blob
    # ... and the Atman team's own board still gets its briefing
    import importlib.util
    spec = importlib.util.spec_from_file_location("tickets_t1077", TOOL)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    assert mod.board_is_atman_operator(str(repo / ".tickets")) is False
    wf = repo / ".tickets" / "workforce.json"
    wf.write_text(json.dumps({"atman-ceo": {"harness": "claude"}}))
    assert mod.board_is_atman_operator(str(repo / ".tickets")) is True
    assert "Announce the Atman role" in atm("harness", "available").stdout


def test_t1008_quickstart_and_init_teach_atm_not_tickets(tmp_path):
    """T-1008: `atm` is the primary command in everything a new user is shown."""
    env, cwd = make_env(tmp_path, HARNESS_OK)
    repo = tmp_path / "t1008"
    repo.mkdir()
    subprocess.run(["git", "init", "-q", "-b", "main", "."], cwd=str(repo),
                   capture_output=True)
    (repo / "README").write_text("t1008\n")
    subprocess.run(["git", "-c", "user.email=t@t", "-c", "user.name=t", "add", "-A"],
                   cwd=str(repo), capture_output=True)
    subprocess.run(["git", "-c", "user.email=t@t", "-c", "user.name=t",
                    "commit", "-qm", "init"], cwd=str(repo), capture_output=True)
    e = dict(env, TICKET_AGENT="alice", TICKET_SESSION_ID="t1008-alice")
    r = subprocess.run([sys.executable, str(TOOL), "quickstart"], cwd=str(repo),
                       capture_output=True, text=True, env=e, timeout=300)
    assert r.returncode == 0, r.stdout + r.stderr
    out = r.stdout
    assert "bound: `atm` run from" in out
    assert "bound: `tickets` run from" not in out
    for line in out.splitlines():
        stripped = line.strip()
        if stripped.startswith("tickets ") or stripped.startswith("TICKET_AGENT=alice tickets "):
            pytest.fail("quickstart taught `tickets` as the command: %r" % line)
    # every command it hands the user is spelled `atm`
    assert "atm next" in out and "atm review" in out
    # and it points at the gate demo, which is the point of T-1077
    assert "atm quickstart --gate" in out
    assert "a DIFFERENT seat accepts the commit" in out


def test_readme_gate_section_is_after_one_path():
    """T-1093: install (`## One path`) comes before `atm quickstart --gate`."""
    text = (ROOT / "README.md").read_text()
    assert text.index("## One path") < text.index("## Feel it in five minutes")


def test_gate_did_not_hold_stops_loudly(tmp_path, capsys):
    """The #248 STOPPED branch: T-002 opened with no accept."""
    import importlib.util
    spec = importlib.util.spec_from_file_location("tickets_t1093", TOOL)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    src = TOOL.read_text()
    assert '_gate_fail(state, "the gate did NOT hold: T-002 opened with no accept on T-001"' in src
    state = {"root": str(tmp_path / "scratch"), "done": []}
    os.makedirs(state["root"], exist_ok=True)
    with pytest.raises(SystemExit) as ei:
        mod._gate_fail(state, "the gate did NOT hold: T-002 opened with no accept on T-001",
                       "claimed T-002")
    assert ei.value.code == 1
    out = capsys.readouterr().out
    assert "STOPPED:" in out
    assert "T-002 opened with no accept on T-001" in out
    assert "Nothing was accepted" in out
