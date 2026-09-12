"""BYOA: any harness under the same prompt contract (T-314).

Every test drives the real CLI as a subprocess against a throwaway board, and
the "model" is a stub shell script that records what the runtime handed it.
Nothing here launches a real agent, so the tests answer the only question that
matters for bring-your-own-agent: does the runtime hand an arbitrary command
the prompt, the cwd and the identity it promised, and does it remember which
command belongs to which agent.
"""

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from watch_reaper import collect_watch_pids_from_board

TOOL = Path(__file__).resolve().parents[1] / "tickets.py"


def run(board, *args, agent="", env=None, cwd=None):
    e = dict(os.environ, TICKETS_DIR=str(board), TICKET_AGENT=agent or "",
             HOME=str(board.parent.parent / "home"))
    e.pop("TICKETS_STOP_HOOK", None)
    if env:
        e.update(env)
    r = subprocess.run([sys.executable, str(TOOL), *args], capture_output=True, text=True,
                       env=e, cwd=str(cwd or board.parent))
    if args and args[0] == "spawn" and "--stop" not in args and "--list" not in args:
        collect_watch_pids_from_board(board)
    return r


@pytest.fixture
def board(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    (tmp_path / "home").mkdir()
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    subprocess.run(["git", "-C", str(repo), "commit", "-q", "--allow-empty", "-m", "init"], check=True,
                   env=dict(os.environ, GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t@t",
                            GIT_COMMITTER_NAME="t", GIT_COMMITTER_EMAIL="t@t"))
    b = repo / ".tickets"
    r = run(b, "create", "Write docs", "--role", "docs", cwd=repo)
    assert r.returncode == 0, r.stderr
    return b


def stub_harness(tmp_path, name="stub", body="", exit_code=0, echo="OK"):
    """A shell script standing in for a model harness.

    It writes everything it was given -- argv, cwd, TICKET_AGENT, and the
    contents of the prompt file -- to <name>.json next to itself, so a test can
    assert on the contract instead of on the harness.
    """
    script = tmp_path / (name + ".sh")
    record = tmp_path / (name + ".json")
    script.write_text(
        '#!/bin/sh\n'
        'python3 - "$@" <<\'PY\'\n'
        'import json, os, sys\n'
        'argv = sys.argv[1:]\n'
        'prompt = ""\n'
        'for a in argv:\n'
        '    if os.path.isfile(a):\n'
        '        prompt = open(a).read()\n'
        'json.dump({"argv": argv, "cwd": os.getcwd(), "agent": os.environ.get("TICKET_AGENT", ""),\n'
        '           "tickets_dir": os.environ.get("TICKETS_DIR", ""), "prompt": prompt},\n'
        '          open(%r, "w"))\n'
        'PY\n'
        '%s'
        'echo %s\n'
        'exit %d\n' % (str(record), body, echo, exit_code))
    script.chmod(0o755)
    return script, record


def wf(board):
    try:
        with open(board / "workforce.json") as f:
            return json.load(f)
    except FileNotFoundError:
        return {}


def agent_rec(board, name):
    with open(board / "agents" / (name + ".json")) as f:
        return json.load(f)


# ---- join: registering an arbitrary harness ------------------------------

def test_join_records_harness_and_cmd(board):
    r = run(board, "join", "qwen", "--roles", "backend", "--harness", "custom",
            "--cmd", "ollama run qwen3 < {prompt_file}")
    assert r.returncode == 0, r.stderr
    e = wf(board)["qwen"]
    assert e["harness"] == "custom"
    assert e["cmd"] == "ollama run qwen3 < {prompt_file}"
    assert e["tool"] == "custom", "older readers look at `tool`; it must not go stale"


def test_join_inline_custom_spec_splits(board):
    r = run(board, "join", "qwen", "--harness", "custom:my-agent --prompt {prompt_file}")
    assert r.returncode == 0, r.stderr
    e = wf(board)["qwen"]
    assert e["harness"] == "custom" and e["cmd"] == "my-agent --prompt {prompt_file}"


def test_join_custom_without_cmd_is_refused(board):
    r = run(board, "join", "qwen", "--harness", "custom")
    assert r.returncode != 0
    assert "--cmd" in (r.stderr + r.stdout)
    assert "qwen" not in wf(board), "an unrunnable agent must not reach the workforce"


def test_join_tool_is_still_the_same_field(board):
    """--tool was the original spelling; it must keep writing the same record."""
    assert run(board, "join", "c1", "--tool", "codex").returncode == 0
    assert wf(board)["c1"]["harness"] == "codex"


def test_join_builtin_harness_keeps_no_command(board):
    assert run(board, "join", "c2", "--harness", "claude").returncode == 0
    assert "cmd" not in wf(board)["c2"]


# ---- the prompt contract, as the harness sees it -------------------------

def test_watch_hands_the_harness_prompt_file_cwd_and_agent(board, tmp_path):
    """One run of the real watch loop against a stub harness."""
    script, record = stub_harness(tmp_path)
    assert run(board, "join", "qwen", "--roles", "docs", "--harness",
               "custom:%s {prompt_file} {cwd} {agent}" % script).returncode == 0
    r = run(board, "watch", "--agent", "qwen", "--exec",
            "%s {prompt_file} {cwd} {agent}" % script,
            "--cwd", str(board.parent), "--every", "5", "--max-runs", "1", "--run-timeout", "1")
    assert r.returncode == 0, r.stderr + r.stdout
    got = json.loads(record.read_text())
    prompt_file, cwd, agent = got["argv"]
    assert agent == "qwen" and got["agent"] == "qwen"
    assert Path(cwd) == Path(got["cwd"]) == Path(board.parent).resolve()
    assert got["tickets_dir"] == str(board)
    assert "qwen" in got["prompt"] and "tickets next" in got["prompt"], \
        "a BYOA harness must get the SAME worker prompt the built-in ones get"
    assert not os.path.exists(prompt_file), "the prompt file is removed after the run"


def test_prompt_file_is_rewritten_each_run(board, tmp_path):
    """{prompt_file} is per-run, not per-watcher: the board moved, or the
    watcher would not have woken up."""
    script, record = stub_harness(
        tmp_path, body='cp "$1" %s.$(ls %s.p* 2>/dev/null | wc -l | tr -d " ")\n'
                       % (str(tmp_path / "seen"), str(tmp_path / "seen")))
    assert run(board, "join", "qwen", "--roles", "docs").returncode == 0
    r = run(board, "watch", "--agent", "qwen", "--exec", "%s {prompt_file}" % script,
            "--cwd", str(board.parent), "--every", "5", "--max-runs", "2", "--run-timeout", "1")
    assert r.returncode == 0, r.stderr + r.stdout
    paths = [json.loads(record.read_text())["argv"][0]]
    # two runs, and the second one's path is gone too -- both were cleaned up
    assert all(not os.path.exists(p) for p in paths)
    assert r.stdout.count("run 2") >= 1


def test_template_with_braces_of_its_own_survives(board, tmp_path):
    """A real harness command carries braces (a JSON body, an awk program).
    Expansion is str.replace, so those must pass through untouched."""
    script, record = stub_harness(tmp_path)
    body = '{"model":"local","messages":[{"role":"user"}]}'
    r = run(board, "join", "qwen", "--roles", "docs")
    assert r.returncode == 0, r.stderr
    r = run(board, "watch", "--agent", "qwen", "--exec",
            "%s {prompt_file} '%s'" % (script, body),
            "--cwd", str(board.parent), "--every", "5", "--max-runs", "1", "--run-timeout", "1")
    assert r.returncode == 0, r.stderr + r.stdout
    assert json.loads(record.read_text())["argv"][1] == body


def test_no_placeholders_means_no_rewriting(board, tmp_path):
    """The pre-BYOA form -- a command that reads the prompt itself -- is
    untouched by the template layer."""
    script, record = stub_harness(tmp_path)
    assert run(board, "join", "qwen", "--roles", "docs").returncode == 0
    r = run(board, "watch", "--agent", "qwen", "--exec", "%s plain-arg" % script,
            "--cwd", str(board.parent), "--every", "5", "--max-runs", "1", "--run-timeout", "1")
    assert r.returncode == 0, r.stderr + r.stdout
    assert json.loads(record.read_text())["argv"] == ["plain-arg"]


# ---- spawn: the stored harness is what actually runs ---------------------

def test_spawn_uses_the_stored_harness_when_tool_is_absent(board, tmp_path):
    """The defect this ticket exists for: --tool defaulted to 'claude', so a
    registered BYOA agent silently reverted to the Claude CLI on every spawn."""
    script, _ = stub_harness(tmp_path)
    assert run(board, "join", "qwen", "--roles", "docs", "--harness",
               "custom:%s {prompt_file}" % script).returncode == 0
    r = run(board, "spawn", "qwen", "--every", "3600")
    assert r.returncode == 0, r.stderr + r.stdout
    try:
        assert str(script) in r.stdout, r.stdout
        assert "claude -p" not in r.stdout
        assert "harness=custom" in r.stdout
    finally:
        run(board, "spawn", "qwen", "--stop")


def test_spawn_tool_flag_still_overrides(board, tmp_path):
    script, _ = stub_harness(tmp_path)
    bindir = tmp_path / "bin"
    bindir.mkdir()
    codex = bindir / "codex"
    codex.write_text("#!/bin/sh\n"
                     "if [ \"$1\" = login ] && [ \"$2\" = status ]; then echo logged in; exit 0; fi\n"
                     "echo OK; exit 0\n")
    codex.chmod(0o755)
    env = dict(PATH=str(bindir) + os.pathsep + os.environ.get("PATH", ""))
    assert run(board, "join", "qwen", "--roles", "docs", "--harness",
               "custom:%s {prompt_file}" % script).returncode == 0
    r = run(board, "spawn", "qwen", "--tool", "codex", "--every", "3600", env=env)
    assert r.returncode == 0, r.stderr + r.stdout
    try:
        assert "codex exec" in r.stdout and str(script) not in r.stdout
        assert "cmd" not in wf(board)["qwen"], \
            "the old harness's command template must not survive a harness switch"
    finally:
        run(board, "spawn", "qwen", "--stop")


def test_spawn_custom_cmd_without_prior_join(board, tmp_path):
    script, _ = stub_harness(tmp_path)
    r = run(board, "spawn", "qwen", "--harness", "custom", "--cmd",
            "%s {prompt_file}" % script, "--roles", "docs", "--every", "3600")
    assert r.returncode == 0, r.stderr + r.stdout
    try:
        assert str(script) in r.stdout
        assert wf(board)["qwen"]["cmd"] == "%s {prompt_file}" % script
    finally:
        run(board, "spawn", "qwen", "--stop")


def test_spawn_list_shows_harness_and_check(board, tmp_path):
    script, _ = stub_harness(tmp_path)
    assert run(board, "join", "qwen", "--roles", "docs",
               "--harness", "custom:%s {prompt_file}" % script).returncode == 0
    assert run(board, "harness", "check", "qwen").returncode == 0
    r = run(board, "spawn", "--list")
    assert r.returncode == 0, r.stderr
    assert "harness" in r.stdout and "custom" in r.stdout and "ok" in r.stdout


# ---- harness check -------------------------------------------------------

def test_harness_check_passes_and_records(board, tmp_path):
    script, record = stub_harness(tmp_path)
    assert run(board, "join", "qwen", "--roles", "docs",
               "--harness", "custom:%s {prompt_file}" % script).returncode == 0
    r = run(board, "harness", "check", "qwen")
    assert r.returncode == 0, r.stderr + r.stdout
    assert "harness OK" in r.stdout
    chk = agent_rec(board, "qwen")["harness_check"]
    assert chk["ok"] is True and chk["exit"] == 0 and chk["harness"] == "custom"
    assert isinstance(chk["latency_ms"], int) and chk["latency_ms"] >= 0
    assert chk["replied"] is True
    assert json.loads(record.read_text())["prompt"] == "reply OK", \
        "the probe must go through the same {prompt_file} path a real run uses"


def test_harness_check_fails_loudly(board, tmp_path):
    script, _ = stub_harness(tmp_path, name="broken", exit_code=3, echo="not installed")
    assert run(board, "join", "qwen", "--roles", "docs",
               "--harness", "custom:%s {prompt_file}" % script).returncode == 0
    r = run(board, "harness", "check", "qwen")
    assert r.returncode == 1, r.stdout
    assert "HARNESS FAILED" in r.stdout
    chk = agent_rec(board, "qwen")["harness_check"]
    assert chk["ok"] is False and chk["exit"] == 3


def test_harness_check_missing_binary_is_a_failure_not_a_crash(board):
    assert run(board, "join", "qwen", "--roles", "docs",
               "--harness", "custom:definitely-not-a-real-binary-9271 {prompt_file}").returncode == 0
    r = run(board, "harness", "check", "qwen")
    assert r.returncode == 1
    assert agent_rec(board, "qwen")["harness_check"]["ok"] is False


def test_harness_check_honours_the_time_cap(board, tmp_path):
    script, _ = stub_harness(tmp_path, name="slow", body="sleep 30\n")
    assert run(board, "join", "qwen", "--roles", "docs",
               "--harness", "custom:%s {prompt_file}" % script).returncode == 0
    r = run(board, "harness", "check", "qwen", "--timeout", "2")
    assert r.returncode == 1
    chk = agent_rec(board, "qwen")["harness_check"]
    assert chk["timed_out"] is True and chk["exit"] == 124
    assert chk["latency_ms"] < 20000, "the cap, not the harness, must end the probe"


def test_harness_check_does_not_answer_ok_but_still_passes(board, tmp_path):
    """Exit status is the verdict. A harness that prefaces its answer is
    working, and calling that a failure would take a live agent out of the
    fleet on a wording difference."""
    script, _ = stub_harness(tmp_path, name="chatty", echo="'Sure thing, here goes.'")
    assert run(board, "join", "qwen", "--roles", "docs",
               "--harness", "custom:%s {prompt_file}" % script).returncode == 0
    r = run(board, "harness", "check", "qwen")
    assert r.returncode == 0
    chk = agent_rec(board, "qwen")["harness_check"]
    assert chk["ok"] is True and chk["replied"] is False


def test_harness_check_probes_the_builtin_command_shape(board):
    """For a built-in harness the probe swaps only the prompt, so what fails is
    the real command line -- not a shape the fleet never runs."""
    assert run(board, "join", "c1", "--roles", "docs", "--harness", "codex").returncode == 0
    r = run(board, "harness", "check", "c1", "--timeout", "5")
    assert "codex exec" in r.stdout and "'reply OK'" in r.stdout
    assert "$(tickets prompt)" not in r.stdout


def test_harness_list_shows_every_agent(board, tmp_path):
    script, _ = stub_harness(tmp_path)
    run(board, "join", "qwen", "--roles", "docs", "--harness", "custom:%s {prompt_file}" % script)
    run(board, "join", "c1", "--roles", "backend", "--harness", "codex")
    r = run(board, "harness", "list")
    assert r.returncode == 0, r.stderr
    assert "qwen" in r.stdout and "custom" in r.stdout
    assert "c1" in r.stdout and "codex" in r.stdout and "(built-in)" in r.stdout


# ---- what the runtime guarantees, for a stranger harness -----------------

def test_a_byoa_agent_gets_the_same_claim_and_review_path(board, tmp_path):
    """The runtime never drives the harness's inner loop, so the claim itself
    is what has to work for a stranger: same atomic claim, same review."""
    assert run(board, "join", "qwen", "--roles", "docs",
               "--harness", "custom:%s {prompt_file}" % stub_harness(tmp_path)[0]).returncode == 0
    r = run(board, "next", agent="qwen")
    assert r.returncode == 0, r.stderr + r.stdout
    tid = next(w for w in r.stdout.split() if w.startswith("T-"))
    r2 = run(board, "next", agent="other")
    assert tid not in r2.stdout, "an atomic claim is atomic regardless of harness"
    # the runtime's own worktree rule applies to a BYOA agent unchanged
    assert run(board, "review", tid, "--notes", "done", agent="qwen").returncode == 1
    subprocess.run(["git", "-C", str(board.parent), "checkout", "-q", "-b", "qwen"], check=True)
    (board.parent / ".gitignore").write_text(".tickets/\n")
    git = dict(os.environ, GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t@t",
               GIT_COMMITTER_NAME="t", GIT_COMMITTER_EMAIL="t@t")
    subprocess.run(["git", "-C", str(board.parent), "add", "-A"], check=True)
    subprocess.run(["git", "-C", str(board.parent), "commit", "-q", "-m", "work"], check=True, env=git)
    assert run(board, "review", tid, "--notes", "done", agent="qwen").returncode == 0


# ---- degenerate registrations --------------------------------------------

def test_watch_without_exec_uses_the_registered_harness(board, tmp_path):
    """`tickets watch --agent x` is the cron-able form and is used without
    spawn; defaulting it to claude would launch the wrong harness."""
    script, record = stub_harness(tmp_path)
    assert run(board, "join", "qwen", "--roles", "docs",
               "--harness", "custom:%s {prompt_file} {cwd} {agent}" % script).returncode == 0
    r = run(board, "watch", "--agent", "qwen", "--cwd", str(board.parent),
            "--every", "5", "--max-runs", "1", "--run-timeout", "1")
    assert r.returncode == 0, r.stderr + r.stdout
    assert json.loads(record.read_text())["agent"] == "qwen"


def test_custom_harness_with_no_command_refuses_to_launch(board):
    """A record can lose its template (hand-edited, or an older writer). Running
    it would launch a binary literally named 'custom'."""
    (board / "workforce.json").write_text(json.dumps({"qwen": {"harness": "custom", "can": [], "cost": "medium"}}))
    r = run(board, "watch", "--agent", "qwen", "--once", "--cwd", str(board.parent))
    assert r.returncode != 0
    assert "custom harness with no command" in (r.stderr + r.stdout)


# ---- agy / Devin / gemini / grok built-in harness & hooks (T-772 / T-789) -

def test_join_records_agy_harness(board):
    r = run(board, "join", "agy-worker", "--roles", "backend", "--harness", "agy")
    assert r.returncode == 0, r.stderr
    entry = json.loads((board / "workforce.json").read_text())["agy-worker"]
    assert entry["harness"] == "agy"
    assert entry.get("cmd") is None


def test_agy_worker_cmd_shape(board):
    import importlib.util
    spec = importlib.util.spec_from_file_location("tickets", str(TOOL))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)

    cmd = mod._worker_cmd(str(board), "agy-worker", model="gemini-3.8-flash-high",
                          permission_mode="bypassPermissions", tool="agy")
    assert cmd == 'agy -p "$(tickets prompt)" --dangerously-skip-permissions --model gemini-3.8-flash-high'

    cmd_safe = mod._worker_cmd(str(board), "agy-worker", permission_mode="acceptEdits", tool="agy")
    assert cmd_safe == 'agy -p "$(tickets prompt)" --mode accept-edits'


def test_hooks_agy_writes_agents_hooks_json(board, tmp_path):
    wt = tmp_path / "worktree"
    wt.mkdir()
    r = run(board, "hooks", "agy", "--agent", "agy-worker", "--worktree", str(wt))
    assert r.returncode == 0, r.stderr
    hooks_file = wt / ".agents" / "hooks.json"
    assert hooks_file.exists()
    cfg = json.loads(hooks_file.read_text())
    assert "tickets-board" in cfg
    entry = cfg["tickets-board"]
    assert "PreInvocation" in entry
    assert "Stop" in entry
    assert any("agy-inbox" in h["command"] for h in entry["PreInvocation"])
    assert any("agy-stop" in h["command"] for h in entry["Stop"])


def test_hook_run_accepts_agy_events(board):
    r = run(board, "hook-run", "--help")
    assert r.returncode == 0, r.stderr
    help_text = r.stdout + r.stderr
    assert "agy-inbox" in help_text
    assert "agy-stop" in help_text


def test_inherit_settings_copies_agents_dir(tmp_path):
    import importlib.util
    spec = importlib.util.spec_from_file_location("tickets", str(TOOL))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)

    root = tmp_path / "root"
    wt = tmp_path / "wt"
    (root / ".agents").mkdir(parents=True)
    (root / ".agents" / "hooks.json").write_text('{"test": true}')

    copied = mod._inherit_settings(str(root), str(wt))
    assert ".agents/hooks.json" in copied
    assert (wt / ".agents" / "hooks.json").exists()
    assert json.loads((wt / ".agents" / "hooks.json").read_text()) == {"test": True}


def test_devin_builtin_harness_join_records_no_cmd(board):
    r = run(board, "join", "devin-worker", "--roles", "backend", "--harness", "devin")
    assert r.returncode == 0, r.stderr
    entry = json.loads((board / "workforce.json").read_text())["devin-worker"]
    assert entry["harness"] == "devin"
    assert entry.get("cmd") is None


def test_devin_worker_cmd_shape(board):
    import importlib.util
    spec = importlib.util.spec_from_file_location("tickets", str(TOOL))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)

    cmd = mod._worker_cmd(str(board), "devin-worker", model="gpt-5",
                          permission_mode="bypassPermissions", tool="devin")
    assert cmd == 'devin --print "$(tickets prompt)" --dangerously-skip-permissions --model gpt-5'

    cmd_safe = mod._worker_cmd(str(board), "devin-worker", permission_mode="safe", tool="devin")
    assert cmd_safe == 'devin --print "$(tickets prompt)"'


def test_hooks_devin_writes_wrapper(board, tmp_path):
    wrapper = tmp_path / "tickets-devin"
    r = run(board, "hooks", "devin", "--agent", "devin-worker", "--wrapper", str(wrapper))
    assert r.returncode == 0, r.stderr
    assert wrapper.exists()
    assert (tmp_path / "tickets-devin.hooks.json").exists()


def test_hooks_gemini_and_grok_are_valid_tools(board, tmp_path):
    r = run(board, "hooks", "--help")
    help_text = r.stdout + r.stderr
    for name in ("agy", "gemini", "devin", "grok", "cursor", "claude", "codex"):
        assert name in help_text
    wrapper = tmp_path / "tickets-gemini"
    r = run(board, "hooks", "gemini", "--agent", "gem-seat", "--wrapper", str(wrapper))
    assert r.returncode == 0, r.stderr
    assert wrapper.exists()
    wt = tmp_path / "grok-wt"
    wt.mkdir()
    r = run(board, "hooks", "grok", "--agent", "grok-worker", "--worktree", str(wt))
    assert r.returncode == 0, r.stderr
    assert (wt / ".cursor" / "hooks.json").exists()
