"""T-1078: the seat brief arrives in the seat's FIRST prompt.

A spawned coding agent reads one prompt and gets no second round trip. These
tests take that literally: a fake harness binary stands in for claude, codex
and cursor, records the exact argv it was launched with, and the assertions
are made against the prompt string that actually reached it -- not against a
template, and not against a doc the seat may never open.

What is pinned here:
  * the gate rule, the seat's own ticket, the exact commands and the three
    refusals are all in that first prompt, for every supported harness;
  * the brief is the HEAD of the prompt, not buried in its tail;
  * a second wake of the same seat does not re-send the brief (but still
    carries the one-line gate), and a steer is not a briefing at all;
  * no credential value, no path to one, and no other seat's ticket content
    is in the prompt.
"""
from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
TOOL = ROOT / "tickets.py"
sys.path.insert(0, str(ROOT / "src"))
from ticket_board import seat_brief  # noqa: E402

# (harness name as `atm join --harness` takes it, binary the runtime launches)
HARNESSES = [("claude", "claude"), ("codex", "codex"), ("cursor", "agent")]

SECRETS = {
    "ANTHROPIC_API_KEY": "sk-ant-T1078-SECRET-ANTHROPIC",
    "CLAUDE_CODE_OAUTH_TOKEN": "sk-ant-oat-T1078-SECRET-OAUTH",
    "OPENAI_API_KEY": "sk-T1078-SECRET-OPENAI",
    "CURSOR_API_KEY": "cur-T1078-SECRET-CURSOR",
}


def _tickets():
    spec = importlib.util.spec_from_file_location("tickets_t1078", TOOL)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# ---- a board, a fake PATH, and a recorder --------------------------------

def _env(board, agent="", extra=None):
    e = dict(os.environ, TICKETS_DIR=str(board), TICKET_AGENT=agent or "",
             HOME=str(board.parent.parent / "home"),
             PATH="%s:%s" % (board.parent.parent / "bin", os.environ.get("PATH", "")),
             T1078_RECORD=str(board.parent.parent / "record.jsonl"))
    e.pop("TICKETS_STOP_HOOK", None)
    e.pop("TICKET_SEAT", None)
    for var in ("CLAUDE_CODE_SESSION_ID", "CODEX_SESSION_ID", "CURSOR_SESSION_ID",
                "TERM_SESSION_ID", "TICKET_SESSION_ID"):
        e.pop(var, None)
    if agent:
        e["TICKET_SESSION_ID"] = "test-session-" + agent
    e.update(SECRETS)
    if extra:
        e.update(extra)
    return e


def run(board, *args, agent="", extra=None, timeout=180):
    return subprocess.run([sys.executable, str(TOOL), *args], capture_output=True,
                          text=True, env=_env(board, agent, extra),
                          cwd=str(board.parent), timeout=timeout)


def _write_fake_bin(bindir):
    """`atm` plus one recorder per harness binary.

    The recorder appends every invocation it sees to T1078_RECORD, so a test
    can read the first launch and the second separately. An auth-status probe
    carries no prompt; it answers "ready" so the runtime's credential
    preflight passes without a real harness or a real credential.
    """
    bindir.mkdir(parents=True, exist_ok=True)
    atm = bindir / "atm"
    atm.write_text('#!/bin/sh\nexec %s "$TICKETS_PY" "$@"\n' % sys.executable)
    atm.chmod(0o755)
    (bindir / "tickets").write_text(atm.read_text())
    (bindir / "tickets").chmod(0o755)
    for _harness, binary in HARNESSES:
        p = bindir / binary
        p.write_text(
            '#!/bin/sh\n'
            '%s - "$@" <<\'PY\'\n'
            'import json, os, sys\n'
            'argv = sys.argv[1:]\n'
            'prompt = max(argv, key=len) if argv else ""\n'
            'rec = {"binary": %r, "argv": argv, "prompt": prompt,\n'
            '       "run_no": os.environ.get("TICKETS_RUN_NO", ""),\n'
            '       "agent": os.environ.get("TICKET_AGENT", ""),\n'
            '       "cwd": os.getcwd()}\n'
            'with open(os.environ["T1078_RECORD"], "a") as f:\n'
            '    f.write(json.dumps(rec) + "\\n")\n'
            'print("ready" if len(prompt) < 200 else "OK")\n'
            'PY\n' % (sys.executable, binary))
        p.chmod(0o755)


@pytest.fixture
def board(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    (tmp_path / "home").mkdir()
    # A planted credential file: nothing in the brief may read or name it.
    cred = tmp_path / "home" / ".claude"
    cred.mkdir()
    (cred / ".credentials.json").write_text(
        json.dumps({"claudeAiOauth": {"accessToken": SECRETS["CLAUDE_CODE_OAUTH_TOKEN"]}}))
    _write_fake_bin(tmp_path / "bin")
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    subprocess.run(["git", "-C", str(repo), "commit", "-q", "--allow-empty", "-m", "init"],
                   check=True, env=dict(os.environ, GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t@t",
                                        GIT_COMMITTER_NAME="t", GIT_COMMITTER_EMAIL="t@t"))
    return repo / ".tickets"


def records(board):
    p = board.parent.parent / "record.jsonl"
    if not p.exists():
        return []
    return [json.loads(ln) for ln in p.read_text().splitlines() if ln.strip()]


def launches(board):
    """Only the invocations that carried a prompt (not the auth probes)."""
    return [r for r in records(board) if len(r["prompt"]) >= 200]


TICKET_TITLE = "Wire the seat brief"
TICKET_BODY = "T1078-OWN-SCOPE: hand the seat its rules in the first token."
OTHER_TITLE = "T1078-BOB-ONLY-TITLE"
OTHER_BODY = "T1078-BOB-ONLY-SCOPE: nothing here may reach another seat."


def seat_with_ticket(board, harness, agent="alice"):
    """One claimed ticket for `agent`, one for another seat, both on the board."""
    assert run(board, "create", TICKET_TITLE, "--body", TICKET_BODY,
               "--role", "backend", agent=agent).returncode == 0
    assert run(board, "create", OTHER_TITLE, "--body", OTHER_BODY,
               "--role", "backend", agent="bob").returncode == 0
    assert run(board, "join", "bob", "--roles", "backend", agent="bob").returncode == 0
    assert run(board, "claim", "T-002", agent="bob").returncode == 0
    r = run(board, "join", agent, "--roles", "backend", "--harness", harness, agent=agent)
    assert r.returncode == 0, r.stderr
    r = run(board, "claim", "T-001", agent=agent)
    assert r.returncode == 0, r.stderr + r.stdout
    return "T-001"


def watch_once(board, agent="alice", max_runs=1, timeout=240):
    r = run(board, "watch", "--agent", agent, "--cwd", str(board.parent),
            "--every", "1", "--max-runs", str(max_runs), "--run-timeout", "2",
            agent=agent, timeout=timeout)
    assert r.returncode == 0, r.stdout + r.stderr
    return r


# ---- the brief reaches every harness -------------------------------------

@pytest.mark.parametrize("harness,binary", HARNESSES, ids=[h for h, _ in HARNESSES])
def test_first_prompt_states_the_gate_the_ticket_and_the_refusals(board, harness, binary):
    tid = seat_with_ticket(board, harness)
    watch_once(board)
    got = launches(board)
    assert got, "the %s harness was launched without a prompt: %s" % (harness, records(board))
    first = got[0]
    assert first["binary"] == binary, first
    prompt = first["prompt"]

    # The brief is the head of the prompt, not a footnote.
    assert prompt.startswith(seat_brief.BRIEF_HEAD), prompt[:200]

    # who it is, what it owns
    assert "alice" in prompt
    assert "harness " + harness in prompt
    assert tid in prompt and TICKET_TITLE in prompt
    assert "T1078-OWN-SCOPE" in prompt

    # the gate, stated in full and before the ordinary worker loop
    gate = prompt.index(seat_brief.GATE_HEAD)
    assert gate < prompt.index("Do now, in order")
    assert "atm accept %s --sha <full 40-char review head>" % tid in prompt
    assert "A DIFFERENT seat" in prompt
    assert "refuses the ticket's own author" in prompt
    assert "every ticket that depends on %s stays shut" % tid in prompt
    assert "the accept is void" in prompt

    # how to act
    for cmd in ("atm next", "atm mine", "atm reopen %s --notes" % tid,
                "atm update %s" % tid, "atm sync && atm review %s --notes" % tid,
                "atm block %s --reason" % tid, "atm note %s" % tid, "atm msg"):
        assert cmd in prompt, cmd

    # the refusals
    assert seat_brief.REFUSE_HEAD in prompt
    assert "Another seat's ticket, branch or worktree" in prompt
    assert "Accepting your own work" in prompt
    assert "Editing .tickets/ JSON" in prompt

    # one usage line, from the existing provider usage reader
    assert "\nUSAGE     " in prompt
    assert harness.split("+")[0] in prompt.split("\nUSAGE     ")[1].splitlines()[0]


@pytest.mark.parametrize("harness,_binary", HARNESSES, ids=[h for h, _ in HARNESSES])
def test_a_reader_of_only_the_prompt_can_state_the_gate(board, harness, _binary):
    """The load-bearing sentences must be present verbatim, once each."""
    seat_with_ticket(board, harness)
    watch_once(board)
    prompt = launches(board)[0]["prompt"]
    facts = [
        "a DIFFERENT seat",            # who accepts
        "full 40-char review head",    # bound to the exact head
        "hands the\n     follow-on work to nobody",  # the next ticket does not open
        "the accept is void",          # a moved head voids it
    ]
    for f in facts:
        assert f.lower() in prompt.lower(), f


# ---- a wake is not a re-briefing ----------------------------------------

@pytest.mark.parametrize("harness,_binary", HARNESSES, ids=[h for h, _ in HARNESSES])
def test_second_wake_does_not_resend_the_brief(board, harness, _binary):
    seat_with_ticket(board, harness)
    watch_once(board, max_runs=2)
    got = launches(board)
    assert len(got) >= 2, "expected two launches, got %d" % len(got)
    assert got[0]["prompt"].startswith(seat_brief.BRIEF_HEAD)
    later = got[1]["prompt"]
    assert seat_brief.BRIEF_HEAD not in later, "the seat was briefed twice"
    assert seat_brief.REFUSE_HEAD not in later
    # ... but the rule that decides whether work counts is still on every turn.
    assert seat_brief.GATE_ONE_LINER in later
    assert got[1]["run_no"] == "2"


def test_byoa_prompt_file_is_briefed_once_too(board, tmp_path):
    """The {prompt_file} path renders in-process, so it needs the same run_no."""
    stub = tmp_path / "byoa.sh"
    rec = tmp_path / "byoa.jsonl"
    stub.write_text(
        '#!/bin/sh\n%s - "$1" <<\'PY\'\nimport json, sys\n'
        'open(%r, "a").write(json.dumps({"prompt": open(sys.argv[1]).read()}) + "\\n")\n'
        'PY\necho OK\n' % (sys.executable, str(rec)))
    stub.chmod(0o755)
    assert run(board, "create", TICKET_TITLE, "--body", TICKET_BODY,
               "--role", "backend", agent="alice").returncode == 0
    assert run(board, "join", "alice", "--roles", "backend", "--harness",
               "custom:%s {prompt_file}" % stub, agent="alice").returncode == 0
    assert run(board, "claim", "T-001", agent="alice").returncode == 0
    watch_once(board, max_runs=2)
    seen = [json.loads(ln)["prompt"] for ln in rec.read_text().splitlines() if ln.strip()]
    assert len(seen) >= 2, seen
    assert seen[0].startswith(seat_brief.BRIEF_HEAD)
    assert seat_brief.BRIEF_HEAD not in seen[1]
    assert seat_brief.GATE_ONE_LINER in seen[1]


def test_steer_is_not_a_briefing(board):
    """A mid-run course correction must not re-brief a running seat."""
    payload = seat_brief_module_steer().frame_payload(
        "redirect", "mira", "use the other adapter", "T-001", "st-1")
    assert seat_brief.BRIEF_HEAD not in payload
    assert seat_brief.REFUSE_HEAD not in payload
    ask = seat_brief_module_steer().frame_payload(
        "ask", "mira", "which adapter?", "T-001", "st-2")
    assert seat_brief.BRIEF_HEAD not in ask
    seat_with_ticket(board, "claude")
    r = run(board, "steer", "alice", "use the other adapter", agent="mira")
    assert seat_brief.BRIEF_HEAD not in (r.stdout + r.stderr)


def seat_brief_module_steer():
    from ticket_board import steer
    return steer


# ---- nothing that must not be there -------------------------------------

@pytest.mark.parametrize("harness,_binary", HARNESSES, ids=[h for h, _ in HARNESSES])
def test_no_credentials_and_no_credential_paths_in_the_prompt(board, harness, _binary):
    seat_with_ticket(board, harness)
    watch_once(board)
    prompt = launches(board)[0]["prompt"]
    for name, value in SECRETS.items():
        assert value not in prompt, "%s value leaked into the seat brief" % name
        assert name not in prompt, "%s named in the seat brief" % name
    for needle in ("sk-ant-", ".credentials.json", "keychain", "Keychain",
                   "auth.json", "oauth", "OAuth", "access_token", "accessToken"):
        assert needle not in prompt, needle
    home = str(board.parent.parent / "home")
    assert home + "/.claude" not in prompt


@pytest.mark.parametrize("harness,_binary", HARNESSES, ids=[h for h, _ in HARNESSES])
def test_no_other_seats_ticket_reaches_the_prompt(board, harness, _binary):
    seat_with_ticket(board, harness)
    watch_once(board)
    prompt = launches(board)[0]["prompt"]
    assert OTHER_TITLE not in prompt
    assert "T1078-BOB-ONLY-SCOPE" not in prompt
    assert "T-002" not in prompt


def test_already_running_seat_sees_no_change(board):
    """Same board, same seat, a later turn: byte-identical to pre-T-1078 shape.

    The steady-state prompt is the worker prompt plus its one-line gate; the
    brief appears only where run_no says this is the seat's first turn.
    """
    seat_with_ticket(board, "claude")
    e = {"TICKETS_RUN_NO": "7"}
    later = run(board, "prompt", "--agent", "alice", agent="alice", extra=e).stdout
    assert later.startswith("You are alice, a worker")
    assert seat_brief.BRIEF_HEAD not in later
    first = run(board, "prompt", "--agent", "alice", agent="alice",
                extra={"TICKETS_RUN_NO": "1"}).stdout
    assert first.startswith(seat_brief.BRIEF_HEAD)
    assert first.endswith(later[-200:]), "the steady-state prompt must be unchanged under the brief"


# ---- the composition itself ---------------------------------------------

def test_is_first_turn_reads_the_run_counter():
    assert seat_brief.is_first_turn(None) is True
    assert seat_brief.is_first_turn("") is True
    assert seat_brief.is_first_turn("1") is True
    assert seat_brief.is_first_turn(1) is True
    assert seat_brief.is_first_turn("2") is False
    assert seat_brief.is_first_turn(9) is False
    # An unreadable counter briefs rather than silently withholding the rules.
    assert seat_brief.is_first_turn("garbage") is True


def test_compose_without_a_ticket_says_so_and_still_states_the_gate():
    text = seat_brief.compose("alice", roles="backend", harness="codex")
    assert "none held yet" in text
    assert "atm accept <id> --sha <full 40-char review head>" in text
    assert seat_brief.REFUSE_HEAD in text
    assert "atm next" in text


def test_compose_truncates_a_long_scope():
    text = seat_brief.compose("alice", ticket_id="T-9", ticket_title="t",
                              scope="x" * 5000)
    assert len(text) < 4000
    assert "`atm show <id>` for the rest" in text


def test_a_seat_is_never_its_own_reviewer(board):
    """If the board says the master is this seat, the brief names nobody."""
    tk = _tickets()
    assert run(board, "join", "alice", "--roles", "backend", agent="alice").returncode == 0
    assert run(board, "master", "take", "--owner", "alice", agent="alice").returncode == 0
    text = tk.seat_brief_text(str(board), "alice")
    assert "Ask alice for the accept" not in text
    assert "Accepting your own work" in text
