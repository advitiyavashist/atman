"""T-988: the Antigravity (agy) print-mode adapter can deliver.

The reported symptom was `agy -p "Reply with exactly: AGY OK"` returning only
"[agy] print timeout after 5m0s with turn in progress; returning partial
output", and Agy seats holding tickets and producing no artifact.

Every fixture in this file is a verbatim capture from a live `agy` 1.2.2 run on
2026-09-15, because the whole defect was that the invocation asked agy for the
one output format that throws the reason away. Nothing here launches a real
agy: the end-to-end test puts a stub named `agy` on PATH that replays the
captured envelope, so the test asserts what the runtime records, not what a
provider happens to be doing today.

The measured facts the code now encodes:
  * print mode is NOT interactive-only. With a model that has quota it answers
    in ~2s with stdin closed, in a plain worktree, with no --project.
  * the hang was a masked HTTP 429: quota is per-model-family, and the default
    model was an exhausted one. Only the json/stream-json formats carry it.
  * agy exits 0 whether the turn SUCCEEDED or ERRORED, so nothing downstream
    may treat its exit code as evidence that the run did anything.
"""
import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

TOOL = Path(__file__).resolve().parents[1] / "tickets.py"

# ---- verbatim captures ----------------------------------------------------

# `agy --output-format stream-json -p ...` on a quota-exhausted model family.
# Note status=ERROR alongside a process exit of 0.
AGY_QUOTA_STREAM = (
    '{"event":"result","result":{"conversation_id":"f4d5f8ec-bb4f-4c90-9b07-1c566a53d3b1",'
    '"status":"ERROR","response":"","error":"API error (attempt 5): RESOURCE_EXHAUSTED '
    '(code 429): Individual quota reached. Please upgrade your subscription to increase '
    'your limits. Resets in 76h3m46s.","duration_seconds":50.33932,"num_turns":1,'
    '"usage":{"input_tokens":0,"output_tokens":0,"thinking_tokens":0,'
    '"cache_read_tokens":0,"total_tokens":0}}}'
)

# The same failure through `--output-format json`: the result object is bare.
AGY_QUOTA_JSON = (
    '{"conversation_id":"a14abd1e-f89e-41b6-a7f7-84bf424b1ebf","status":"ERROR",'
    '"response":"","error":"API error (attempt 5): RESOURCE_EXHAUSTED (code 429): '
    'Individual quota reached. Please upgrade your subscription to increase your limits. '
    'Resets in 76h1m55s.","duration_seconds":45.085081,"num_turns":1,'
    '"usage":{"input_tokens":0,"output_tokens":0,"thinking_tokens":0,'
    '"cache_read_tokens":0,"total_tokens":0}}'
)

# A model that had quota, same prompt, same flags: 2 seconds, real answer.
AGY_OK_STREAM = (
    '{"event":"result","result":{"conversation_id":"4f461581-ddc4-4c37-8878-24447124f8c4",'
    '"status":"SUCCESS","response":"AGY OK\\n","duration_seconds":1.9961820000000001,'
    '"num_turns":1,"usage":{"input_tokens":15115,"output_tokens":17,"thinking_tokens":0,'
    '"cache_read_tokens":0,"total_tokens":15132}}}'
)

# What the DEFAULT text format printed for the very same quota failure: no
# error, no status, no 429 -- and then exit 0.
AGY_TEXT_ALL_IT_SAID = (
    "[agy] print timeout after 5m0s with turn in progress; returning partial output"
)

# An agy failure that is NOT a limit; must not be labelled one.
AGY_TOOL_ERROR_STREAM = (
    '{"event":"result","result":{"conversation_id":"c0ffee00-0000-0000-0000-000000000000",'
    '"status":"ERROR","response":"","error":"tool call failed: write_to_file: permission '
    'denied","duration_seconds":3.5,"num_turns":2,'
    '"usage":{"input_tokens":120,"output_tokens":4,"cache_read_tokens":0,"total_tokens":124}}}'
)

CLAUDE_RESULT = (
    '{"type":"result","subtype":"success","num_turns":3,"duration_ms":4200,'
    '"total_cost_usd":0.0731,"usage":{"input_tokens":91,"output_tokens":12,'
    '"cache_read_input_tokens":4400,"cache_creation_input_tokens":900}}'
)
CODEX_EVENT = (
    '{"type":"token_count","info":{"total_token_usage":{"input_tokens":70,'
    '"output_tokens":9,"cached_input_tokens":14,"cache_write_input_tokens":6}}}'
)


@pytest.fixture(scope="module")
def mod():
    spec = importlib.util.spec_from_file_location("tickets_t988", str(TOOL))
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


# ---- the launch recipe ----------------------------------------------------

def test_agy_command_bounds_its_own_timeout_and_asks_for_a_readable_result(mod, tmp_path):
    cmd = mod._worker_cmd(str(tmp_path), "agy-worker", model="gemini-3.8-flash-high",
                          permission_mode="bypassPermissions", tool="agy")
    assert cmd == (
        'agy -p "$(tickets prompt)" --dangerously-skip-permissions '
        '--output-format stream-json --print-timeout %s --model gemini-3.8-flash-high'
        % mod.AGY_PRINT_TIMEOUT)
    # The unattended flag is still the only difference for the supervised mode,
    # and `antigravity` is the same adapter under its long name.
    safe = mod._worker_cmd(str(tmp_path), "agy-worker", permission_mode="acceptEdits", tool="agy")
    assert safe == (
        'agy -p "$(tickets prompt)" --mode accept-edits '
        '--output-format stream-json --print-timeout %s' % mod.AGY_PRINT_TIMEOUT)
    assert mod._worker_cmd(str(tmp_path), "agy-worker", tool="antigravity") == \
        mod._worker_cmd(str(tmp_path), "agy-worker", tool="agy")


def test_agy_print_timeout_stays_inside_the_watcher_cap(mod):
    """agy's own default is 5m, which truncates a real ticket turn. Raising it
    past the watcher's cap would only move the truncation, so the watcher has
    to stay the outer bound."""
    assert mod.AGY_PRINT_TIMEOUT.endswith("m")
    agy_minutes = int(mod.AGY_PRINT_TIMEOUT[:-1])
    assert agy_minutes > 5, "5m is the broken default this ticket exists to raise"
    watch_default = [a for a in _watch_help().splitlines() if "--run-timeout" in a]
    assert watch_default, "watch no longer documents --run-timeout"
    assert agy_minutes < 90, "the watcher's --run-timeout default is 90 MINUTES"


def _watch_help():
    r = subprocess.run([sys.executable, str(TOOL), "watch", "--help"],
                       capture_output=True, text=True)
    return r.stdout + r.stderr


def test_text_format_is_why_the_failure_was_invisible(mod):
    """The regression this recipe exists to prevent: the default text format
    prints no status, no error and no 429, so every downstream reader -- the
    limit outcome, the auth classifier, the limit note -- correctly concludes
    nothing happened, and the run still exits 0 and looks like a success."""
    assert mod._structured_limit_signal(AGY_TEXT_ALL_IT_SAID) is False
    assert mod._looks_limited(AGY_TEXT_ALL_IT_SAID) is False
    assert mod._run_end_limit_outcome(0, False, AGY_TEXT_ALL_IT_SAID) is None
    assert mod._classify_auth_output(0, AGY_TEXT_ALL_IT_SAID) == "ready"


# ---- an exit of 0 is not evidence ----------------------------------------

@pytest.mark.parametrize("blob", [AGY_QUOTA_STREAM, AGY_QUOTA_JSON])
def test_quota_429_is_a_limit_even_though_agy_exited_zero(mod, blob):
    assert mod._structured_limit_signal(blob) is True
    assert mod._run_end_limit_outcome(0, False, blob) == "limit"
    # ...and with the surrounding noise a real run log carries.
    log = "waking agy-worker\n%s\n%s\n" % (AGY_TEXT_ALL_IT_SAID, blob)
    assert mod._run_end_limit_outcome(0, False, log) == "limit"


def test_successful_agy_run_is_not_a_limit(mod):
    assert mod._structured_limit_signal(AGY_OK_STREAM) is False
    assert mod._run_end_limit_outcome(0, False, AGY_OK_STREAM) is None


def test_a_non_limit_agy_error_is_not_labelled_limit(mod):
    """status ERROR alone must not mean limit, or every tool failure would be
    filed as an exhausted quota and suppress the seat's own wake."""
    assert mod._structured_limit_signal(AGY_TOOL_ERROR_STREAM) is False
    assert mod._run_end_limit_outcome(0, False, AGY_TOOL_ERROR_STREAM) is None


def test_quota_is_classified_from_the_output_not_the_exit_code(mod):
    assert mod._classify_auth_output(0, AGY_QUOTA_STREAM) == "quota"
    assert mod._classify_auth_output(0, AGY_OK_STREAM) == "ready"


def test_agy_credential_probe_is_real_and_claims_nothing_about_quota(mod):
    """`agy models` is a real subcommand and reaches the provider. It is not a
    quota check: it listed models while the account's Gemini family was 429."""
    assert mod.HARNESS_AUTH_COMMANDS["agy"] == (["agy", "models"], [])
    assert "-p" not in mod.HARNESS_AUTH_COMMANDS["agy"][0], \
        "a credential probe must never start a turn"


def test_an_agy_auth_record_is_legal_under_the_v2_contract(mod):
    """agy was missing from PROFILE_KINDS, so validate_auth_check called every
    agy record an unknown harness and merge_auth_check dropped it on the floor:
    a healthy agy run recorded no auth state at all, which reads the same as a
    seat nobody ever checked."""
    from auth_v2_contract import PROFILE_KINDS, validate_auth_check
    assert PROFILE_KINDS["agy"] == ("adapter",)
    assert mod._profile_kind_for("agy") == "adapter"
    # Asserted on the one error this fix removes, so the test does not have to
    # restate every required execution_context field to stay meaningful.
    rec = {"state": "ready", "harness": "agy", "profile_kind": "adapter",
           "execution_context": {}}
    errors = validate_auth_check(rec)
    assert not any("unknown harness" in e for e in errors), errors
    assert not any("profile_kind" in e for e in errors), errors
    # The same record with a kind agy cannot hold is still refused.
    bad = dict(rec, profile_kind="subscription")
    assert any("profile_kind" in e for e in validate_auth_check(bad))


# ---- usage off agy's own result ------------------------------------------

def test_usage_comes_off_the_agy_result(mod):
    got = mod.parse_harness_usage(AGY_OK_STREAM)
    assert got["tokens_in"] == 15115 and got["tokens_out"] == 17
    assert got["tokens_cache_read"] == 0
    assert got["turns"] == 1
    assert got["harness_duration_ms"] == 1996, "seconds are converted, not stored raw"
    assert "cost_usd" not in got and "cost_source" not in got, \
        "agy reports no cost and a made-up one is worse than none"
    assert "tokens_thinking" not in got, "no field the board does not price or read"


def test_errored_turn_reports_its_real_zeros(mod):
    """Zero tokens on a 429 is a measurement, not a missing reading: the turn
    never reached the model. Dropping it would look like 'reported nothing'."""
    got = mod.parse_harness_usage(AGY_QUOTA_STREAM)
    assert got["tokens_in"] == 0 and got["tokens_out"] == 0
    assert got["turns"] == 1


def test_agy_parser_leaves_claude_and_codex_alone(mod):
    claude = mod.parse_harness_usage(CLAUDE_RESULT)
    assert claude["tokens_cache_write"] == 900 and claude["cost_source"] == "harness"
    codex = mod.parse_harness_usage(CODEX_EVENT)
    assert codex["tokens_in"] == 70 and codex["tokens_cache_read"] == 14


def test_agy_result_is_matched_strictly(mod):
    """It is looked for in the same output as the other two, so the shape has
    to be one only agy emits."""
    assert mod._agy_result({"conversation_id": "x"}) is None            # no status
    assert mod._agy_result({"status": "SUCCESS"}) is None               # no id
    assert mod._agy_result({"conversation_id": "x", "status": "WAT"}) is None
    assert mod._agy_result(json.loads(CLAUDE_RESULT)) is None
    assert mod._agy_result({"event": "step_update", "step_update": {}}) is None
    unwrapped = mod._agy_result(json.loads(AGY_QUOTA_STREAM))
    assert unwrapped["status"] == "ERROR"


def test_unreadable_agy_usage_is_an_error_not_a_silent_zero(mod):
    bad = '{"conversation_id":"x","status":"SUCCESS","usage":["input_tokens"]}'
    with pytest.raises(mod.HarnessUsageError):
        mod.parse_harness_usage(bad)
    worse = '{"conversation_id":"x","status":"SUCCESS","usage":{"input_tokens":"lots"}}'
    with pytest.raises(mod.HarnessUsageError):
        mod.parse_harness_usage(worse)


# ---- end to end on a real board -----------------------------------------

def run(board, *args, agent="", env=None, cwd=None):
    e = dict(os.environ, TICKETS_DIR=str(board), TICKET_AGENT=agent or "",
             HOME=str(board.parent.parent / "home"))
    # TICKET_SEAT outranks TICKET_AGENT in session_seat(), so a run launched
    # from inside a live seat would otherwise answer as the LAUNCHING seat.
    e["TICKET_SEAT"] = agent or ""
    e.pop("TICKETS_STOP_HOOK", None)
    if env:
        e.update(env)
    return subprocess.run([sys.executable, str(TOOL), *args], capture_output=True,
                          text=True, env=e, cwd=str(cwd or board.parent))


@pytest.fixture
def board(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    (tmp_path / "home").mkdir()
    git = dict(os.environ, GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t@t",
               GIT_COMMITTER_NAME="t", GIT_COMMITTER_EMAIL="t@t")
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    subprocess.run(["git", "-C", str(repo), "commit", "-q", "--allow-empty", "-m", "init"],
                   check=True, env=git)
    b = repo / ".tickets"
    assert run(b, "create", "Write docs", "--role", "backend", cwd=repo).returncode == 0
    return b


def stub_agy(tmp_path, stdout_blob):
    """An `agy` on PATH that records its argv and replays a captured envelope.

    It exits 0 on purpose: that is the behaviour under test.
    """
    bindir = tmp_path / "bin"
    bindir.mkdir(exist_ok=True)
    record = tmp_path / "agy-argv.json"
    script = bindir / "agy"
    # The record path goes in DOUBLE quotes: it sits inside the single-quoted
    # `python3 -c` argument, and %r would close that quote.
    script.write_text(
        '#!/bin/sh\n'
        'python3 -c \'import json,sys; json.dump({"argv": sys.argv[1:]}, open("%s","w"))\' "$@"\n'
        'cat <<\'BLOB\'\n%s\nBLOB\n'
        'exit 0\n' % (str(record), stdout_blob))
    script.chmod(0o755)
    return bindir, record


def agent_rec(board, name):
    return json.loads((board / "agents" / (name + ".json")).read_text())


def traj(board):
    path = board / "trajectories.jsonl"
    if not path.exists():
        return []
    return [json.loads(l) for l in path.read_text().splitlines() if l.strip()]


def test_quota_exhausted_agy_run_is_recorded_as_limited_not_as_ready(board, tmp_path):
    """The whole ticket, end to end: an agy seat whose provider is out of
    quota exits 0 with an empty answer. The board must say limit and quota,
    never 'headless run succeeded'."""
    bindir, record = stub_agy(tmp_path, AGY_QUOTA_STREAM)
    assert run(board, "join", "agy-seat", "--roles", "backend", "--harness", "agy").returncode == 0
    r = run(board, "watch", "--agent", "agy-seat", "--cwd", str(board.parent),
            "--every", "5", "--max-runs", "1", "--run-timeout", "1",
            env={"PATH": "%s:%s" % (bindir, os.environ["PATH"])})
    assert r.returncode == 0, r.stderr + r.stdout
    assert record.exists(), "the stub agy was never invoked:\n%s" % r.stdout

    argv = json.loads(record.read_text())["argv"]
    assert "--output-format" in argv and argv[argv.index("--output-format") + 1] == "stream-json"
    assert "--print-timeout" in argv, "an unbounded agy turn is cut off at its own 5m default"

    rec = agent_rec(board, "agy-seat")
    auth = rec.get("auth_check") or {}
    assert auth.get("state") == "quota", \
        "exit 0 with a 429 in the output must not be filed as ready: %r" % auth
    assert auth.get("exit") == 0, "the honest exit code is still recorded"
    assert "429" in (auth.get("detail") or ""), auth
    assert (rec.get("limit") or {}).get("note"), "a limited seat must carry a limit record"
    assert "76h" in (rec["limit"].get("until") or ""), rec["limit"]

    ends = [e for e in traj(board) if e.get("kind") == "run_end"]
    assert ends, "no run_end recorded"
    assert ends[-1]["exit"] == 0
    assert ends[-1].get("outcome") == "limit", ends[-1]
    assert ends[-1].get("tokens_in") == 0 and ends[-1].get("turns") == 1


def test_successful_agy_run_is_recorded_as_ready_and_carries_its_tokens(board, tmp_path):
    """The other half: the same seat, a model with quota. Nothing about the
    limit path may make a healthy run look limited."""
    bindir, record = stub_agy(tmp_path, AGY_OK_STREAM)
    assert run(board, "join", "agy-seat", "--roles", "backend", "--harness", "agy").returncode == 0
    r = run(board, "watch", "--agent", "agy-seat", "--cwd", str(board.parent),
            "--every", "5", "--max-runs", "1", "--run-timeout", "1",
            env={"PATH": "%s:%s" % (bindir, os.environ["PATH"])})
    assert r.returncode == 0, r.stderr + r.stdout

    rec = agent_rec(board, "agy-seat")
    assert (rec.get("auth_check") or {}).get("state") == "ready"
    assert not rec.get("limit"), "a healthy run must not leave a limit record"
    ends = [e for e in traj(board) if e.get("kind") == "run_end"]
    assert ends and ends[-1].get("outcome") != "limit"
    assert ends[-1].get("tokens_in") == 15115 and ends[-1].get("tokens_out") == 17
