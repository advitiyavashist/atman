"""T-866: Linux support for the Atman distribution.

What broke on a stock Linux (ubuntu-latest, a clean Ubuntu 24.04 container)
and the guards that keep it fixed:

* procps `ps` clips every command line to 80 columns when it runs under
  pytest (no tty, nothing else says otherwise). BSD `ps` on macOS never
  does, so every watch/pytest scanner that read argv out of `ps` was blind
  on Linux only (t409, t427, t554, t559). `-ww` lifts the limit on both.
* procps `pgrep -fl` prints only the process NAME; BSD pgrep prints the
  argv. A needle like `<worktree>/tickets.py` can never match on Linux.
* Fixtures `git init` then `checkout main`; only Xcode's system gitconfig
  made that `main`. conftest.py now pins init.defaultBranch for the suite.
* ~/.cache/atman ignored $XDG_CACHE_HOME; `ui --open` shelled to macOS `open`.
* packaging/smoke.sh is the first-win acceptance (install -> atm join ->
  atm ui /board.json) that the clean-container Dockerfile and CI both run.
"""
import importlib.util
import json
import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


def _load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def tk():
    return _load("tickets_t866", ROOT / "tickets.py")


@pytest.fixture(scope="module")
def adapters():
    return _load("session_adapters_t866", ROOT / "session_adapters.py")


# ---------------------------------------------------------------- ps width

_PS_LITERAL = re.compile(r'\[\s*"ps"\s*,(?P<rest>[^\]]*)\]', re.S)


def _ps_call_sites():
    """Every `["ps", ...]` argv literal in the CLI copies and the test tree."""
    files = [ROOT / "tickets.py", ROOT / "src/ticket_board/cli.py", ROOT / "session_adapters.py"]
    files += sorted(p for p in (ROOT / "tests").rglob("*.py") if ".venv" not in p.parts)
    sites = []
    for path in files:
        # errors="replace": the tree carries deliberately non-UTF-8 fixtures.
        text = path.read_text(encoding="utf-8", errors="replace")
        for m in _PS_LITERAL.finditer(text):
            line = text.count("\n", 0, m.start()) + 1
            sites.append((path.relative_to(ROOT).as_posix(), line, m.group("rest")))
    return sites


def test_every_ps_call_that_reads_argv_asks_for_unlimited_width():
    """procps clips `command=`/`args=` to the terminal width; `-ww` is the
    documented way to say "no limit" and BSD ps accepts it too. A format that
    never prints argv (pid=, ppid=) is exempt: nothing to truncate."""
    sites = _ps_call_sites()
    reads_argv = [(f, n, rest) for f, n, rest in sites if "command" in rest or "args" in rest]
    # The sweep must actually visit the sites this ticket fixed (assert
    # coverage, do not compute it): 3 in tickets.py + 6 test helpers.
    assert len(reads_argv) >= 9, sites
    assert any(f == "tickets.py" for f, _, _ in reads_argv), "tickets.py sites not swept"
    bad = [(f, n) for f, n, rest in reads_argv if '"-ww"' not in rest and "ww" not in rest]
    assert not bad, "ps calls that will be clipped to 80 columns on Linux: %s" % bad


def test_no_pgrep_l_flag_is_used_to_read_argv():
    """`pgrep -l` prints the process name on procps and the argv on BSD --
    the one flag whose meaning differs is banned; scan `ps -axww` instead."""
    offenders = []
    for path in [ROOT / "tickets.py", ROOT / "src/ticket_board/cli.py"] + sorted((ROOT / "tests").rglob("*.py")):
        if ".venv" in path.parts:
            continue
        for m in re.finditer(r'"pgrep"\s*,\s*"(-[a-zA-Z]+)"', path.read_text(encoding="utf-8", errors="replace")):
            if "l" in m.group(1):
                offenders.append((path.relative_to(ROOT).as_posix(), m.group(1)))
    assert not offenders, offenders


def _settle(fn, ok, tries=40, pause=0.05):
    """Poll until `ok(fn())`; a just-Popen'd child can still show its parent's
    argv in ps for a few ms (fork happened, exec has not)."""
    import time
    got = fn()
    for _ in range(tries):
        if ok(got):
            return got
        time.sleep(pause)
        got = fn()
    return got


def test_ps_ww_reports_the_whole_command_line_on_this_platform(tmp_path):
    """Live check: a 150+ char argv survives `ps -ww -p` and `ps -axww`
    exactly the way tickets.py reads them (from a captured subprocess)."""
    long_cwd = tmp_path / ("x" * 60) / ("y" * 60)
    long_cwd.mkdir(parents=True)
    child = subprocess.Popen(
        [sys.executable, "-c", "import time; time.sleep(30)", "--agent", "qwen",
         "--cwd", str(long_cwd), "--persist", "--every", "3600", "--exec", "true"],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        one = _settle(
            lambda: subprocess.run(["ps", "-ww", "-p", str(child.pid), "-o", "command="],
                                   capture_output=True, text=True).stdout.strip(),
            lambda s: "--exec true" in s)
        assert str(long_cwd) in one and one.endswith("--exec true"), one
        table = subprocess.run(["ps", "-axww", "-o", "pid=,command="],
                               capture_output=True, text=True).stdout
        rows = [l for l in table.splitlines() if l.split(None, 1)[0] == str(child.pid)]
        assert rows and str(long_cwd) in rows[0], rows
    finally:
        child.kill()
        child.wait(timeout=5)


def test_parse_watch_table_sees_a_loop_with_a_long_fixture_path(tk, tmp_path):
    """The exact shape t409/t554 spawn: `python .../tickets.py watch --agent
    qwen --cwd <deep tmp path> ...` must come back from _parse_watch_table
    with its --cwd intact (clipped argv used to lose the --cwd entirely)."""
    deep = tmp_path / "pytest-of-someone" / "pytest-0" / ("test_" + "n" * 40) / "repo" / ".worktrees" / "qwen"
    deep.mkdir(parents=True)
    tool = tmp_path / "tickets.py"  # basename is what _watch_cmd_agent keys on
    tool.write_text("import time\ntime.sleep(30)\n")
    child = subprocess.Popen(
        [sys.executable, str(tool), "watch", "--agent", "qwen", "--cwd", str(deep),
         "--persist", "--every", "3600", "--exec", "true"],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        def _rows():
            # T-926: _parse_watch_table returns (rows, available).
            table = tk._parse_watch_table()
            rows = table[0] if isinstance(table, tuple) else table
            return [r for r in rows if r["pid"] == child.pid]

        rows = _settle(_rows, lambda rs: bool(rs) and rs[0]["cwd"] == str(deep))
        assert rows, "watch loop missing from the ps table"
        assert rows[0]["agent"] == "qwen"
        assert rows[0]["cwd"] == str(deep), rows[0]
        assert tk._process_command(child.pid).endswith("--exec true")
    finally:
        child.kill()
        child.wait(timeout=5)


# ------------------------------------------------------- git default branch

def test_suite_git_init_defaults_to_main_on_every_platform(tmp_path):
    """conftest pins init.defaultBranch; fixtures may `checkout main` after a
    bare `git init` on Linux too, not only under Xcode's system gitconfig."""
    repo = tmp_path / "r"
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    head = subprocess.check_output(["git", "-C", str(repo), "symbolic-ref", "HEAD"], text=True).strip()
    assert head == "refs/heads/main"
    assert os.environ.get("GIT_CONFIG_KEY_0") == "init.defaultBranch"


# ---------------------------------------------------------------- XDG cache

def test_cache_root_prefers_pin_then_xdg_then_home(adapters, monkeypatch, tmp_path):
    home = tmp_path / "home"
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.delenv("TICKETS_CACHE_DIR", raising=False)
    monkeypatch.delenv("XDG_CACHE_HOME", raising=False)
    assert adapters.cache_root() == str(home / ".cache" / "atman")
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "xdg"))
    assert adapters.cache_root() == str(tmp_path / "xdg" / "atman")
    # The XDG spec says a relative XDG_CACHE_HOME is invalid and must be ignored.
    monkeypatch.setenv("XDG_CACHE_HOME", "relative/cache")
    assert adapters.cache_root() == str(home / ".cache" / "atman")
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "xdg"))
    monkeypatch.setenv("TICKETS_CACHE_DIR", str(tmp_path / "pinned"))
    assert adapters.cache_root() == str(tmp_path / "pinned")


def test_auth_profile_store_follows_xdg_cache_home(tk, monkeypatch, tmp_path):
    """tickets.py's auth-profile store used its own ~/.cache/atman literal;
    it now shares the one resolver, so a Linux user with XDG_CACHE_HOME set
    gets every Atman cache file under one root."""
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.delenv("TICKETS_CACHE_DIR", raising=False)
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "xdg"))
    board = tmp_path / "repo" / ".tickets"
    board.mkdir(parents=True)
    ref = tk._persist_auth_profile(str(board), "alice", "claude", "alice@example", "adapter")
    assert isinstance(ref, str) and ref.startswith("prf_")
    written = [p for p in (tmp_path / "xdg" / "atman").rglob("*") if p.is_file()]
    assert written, "auth profile was not written under $XDG_CACHE_HOME/atman"
    assert not (tmp_path / "home" / ".cache").exists()


# ------------------------------------------------------------- ui --open

def test_browser_opener_is_platform_aware(tk):
    assert tk._browser_opener_argv("http://h:1", platform="darwin", which=lambda n: None) == ["open", "http://h:1"]
    assert tk._browser_opener_argv("http://h:1", platform="linux",
                                   which=lambda n: "/usr/bin/xdg-open" if n == "xdg-open" else None) == ["xdg-open", "http://h:1"]
    # Headless Linux (CI, a container): nothing to exec; caller falls back to webbrowser.
    assert tk._browser_opener_argv("http://h:1", platform="linux", which=lambda n: None) is None
    assert 'Popen(["open"' not in (ROOT / "tickets.py").read_text()


# ---------------------------------------------------------- first-win smoke

def test_smoke_checkout_mode_reaches_the_first_win(tmp_path):
    """The same script the clean-Ubuntu Dockerfile and CI run: install.sh into
    an isolated prefix, atm join, atm ui answering /board.json. Everything it
    creates stays under SMOKE_WORK; the caller's board is never touched."""
    work = tmp_path / "work"
    env = dict(os.environ, SMOKE_WORK=str(work), PYTHON=sys.executable,
               PATH=os.path.dirname(sys.executable) + os.pathsep + os.environ.get("PATH", ""))
    env.pop("TICKETS_DIR", None)
    r = subprocess.run(["sh", str(ROOT / "packaging/smoke.sh"), "checkout"],
                       capture_output=True, text=True, env=env, timeout=180)
    assert r.returncode == 0, r.stdout + r.stderr
    assert "FIRST WIN" in r.stdout, r.stdout
    assert (work / "project" / ".tickets").is_dir()
    assert (work / "bin" / "atm").is_symlink()
    assert r.stdout.count("/board.json") >= 1


def test_smoke_script_rejects_an_unknown_mode():
    r = subprocess.run(["sh", str(ROOT / "packaging/smoke.sh"), "nope"],
                       capture_output=True, text=True, timeout=30,
                       env=dict(os.environ, SMOKE_WORK=str(Path(os.environ.get("TMPDIR", "/tmp")) / "atman-smoke-reject")))
    assert r.returncode != 0
    assert "unknown mode" in r.stderr


# ------------------------------------------------------ brew formula pinning

def test_pin_formula_rewrites_only_url_and_sha256(tmp_path):
    pin = _load("pin_homebrew_formula_t866", ROOT / "scripts/pin_homebrew_formula.py")
    src = (ROOT / "packaging/homebrew/atman.rb").read_text()
    sha = "ab" * 32
    out = pin.pin(src, "http://127.0.0.1:8766/atman-0.2.0.tar.gz", sha)
    assert '  url "http://127.0.0.1:8766/atman-0.2.0.tar.gz"' in out
    assert '  sha256 "%s"' % sha in out

    def rest(text):
        return [l for l in text.splitlines() if not l.strip().startswith(("url ", "sha256 "))]
    assert rest(out) == rest(src), "pinning must not touch anything but url/sha256"
    with pytest.raises(ValueError):
        pin.pin(src, "http://x/a.tar.gz", "not-a-sha")
    with pytest.raises(ValueError):
        pin.pin("class Atman < Formula\n  desc \"x\"\nend\n", "http://x/a.tar.gz", sha)


def test_pin_formula_cli_reads_the_release_manifest(tmp_path):
    release = {"tarball": "/somewhere/dist/atman-0.2.0.tar.gz", "sha256": "cd" * 32, "version": "0.2.0"}
    (tmp_path / "release.json").write_text(json.dumps(release))
    out = tmp_path / "out" / "atman.rb"
    r = subprocess.run([sys.executable, str(ROOT / "scripts/pin_homebrew_formula.py"),
                        "--formula", str(ROOT / "packaging/homebrew/atman.rb"),
                        "--release-json", str(tmp_path / "release.json"),
                        "--url-base", "http://127.0.0.1:8766", "--out", str(out)],
                       capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    text = out.read_text()
    assert 'url "http://127.0.0.1:8766/atman-0.2.0.tar.gz"' in text
    assert 'sha256 "%s"' % ("cd" * 32) in text
    assert "class Atman < Formula" in text


# ------------------------------------------------------------------ CI shape

def test_ci_runs_the_suite_and_the_formula_on_ubuntu_and_macos():
    yaml = pytest.importorskip("yaml")
    wf = yaml.safe_load((ROOT / ".github/workflows/contracts.yml").read_text())
    validate = wf["jobs"]["validate"]
    assert validate["strategy"]["matrix"]["os"] == ["ubuntu-latest", "macos-latest"]
    steps = " ".join(s.get("run", "") for s in validate["steps"])
    assert "python -m pytest -q" in steps
    assert "packaging/smoke.sh wheel" in steps and "packaging/smoke.sh checkout" in steps
    assert any(s.get("env", {}).get("TICKET_BOARD_CONTRACTS_REQUIRED") == "1" for s in validate["steps"])
    brew = wf["jobs"]["brew-formula"]
    assert brew["strategy"]["matrix"]["os"] == ["ubuntu-latest", "macos-latest"]
    brew_steps = " ".join(s.get("run", "") for s in brew["steps"])
    assert "packaging/homebrew/atman.rb" in brew_steps, "CI must install the REAL formula"
    assert "brew install --formula" in brew_steps and "brew test" in brew_steps
    assert "verified release" in brew_steps
    # The root CLI and packaging now trigger CI (they never did before T-866).
    for key in ("push", "pull_request"):
        paths = wf[True if key == "on" else "on"][key]["paths"] if "on" in wf else wf[True][key]["paths"]
        assert "*.py" in paths and "packaging/**" in paths and "install.sh" in paths


# ------------------------------------------------ sync: no identity != conflict

def _git(repo, *args, env=None):
    e = dict(os.environ, GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t@t",
             GIT_COMMITTER_NAME="t", GIT_COMMITTER_EMAIL="t@t")
    if env:
        e.update(env)
    r = subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True, env=e)
    assert r.returncode == 0, "git %s failed: %s" % (list(args), r.stderr)
    return r.stdout.strip()


def _repo_behind_its_own_origin(tmp_path):
    """A branch that is behind origin/main, where origin is the repo itself
    (the T-423 sync shape the t422 fixtures use)."""
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q", "-b", "main")
    (repo / ".gitignore").write_text(".tickets/\n")
    _git(repo, "add", ".gitignore")
    _git(repo, "commit", "-q", "-m", "init")
    _git(repo, "checkout", "-q", "-b", "alice/work")
    (repo / "work.txt").write_text("branch work\n")
    _git(repo, "add", "work.txt")
    _git(repo, "commit", "-q", "-m", "work")
    _git(repo, "checkout", "-q", "main")
    (repo / "trunk.txt").write_text("someone else's merge\n")
    _git(repo, "add", "trunk.txt")
    _git(repo, "commit", "-q", "-m", "trunk")
    _git(repo, "checkout", "-q", "alice/work")
    _git(repo, "remote", "add", "origin", str(repo.resolve()))
    _git(repo, "fetch", "-q", "origin", "main")
    return repo


@pytest.mark.parametrize("entry", ["tickets.py", "src/ticket_board/cli.py"])
def test_sync_names_a_merge_that_never_started_instead_of_an_empty_conflict_list(tmp_path, entry):
    """Both CLI copies. HOME has a .gitconfig that forbids identity
    auto-detection and gives none -- the deterministic stand-in for a Linux
    host whose hostname has no domain (git refuses `user@host.(none)`).
    The merge subprocess scrubs GIT_* from its env (T-287), so the identity
    can only come from config, exactly like a real fresh machine."""
    repo = _repo_behind_its_own_origin(tmp_path)
    home = tmp_path / "home"
    home.mkdir()
    (home / ".gitconfig").write_text("[user]\n\tuseConfigOnly = true\n")
    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    env.update(HOME=str(home), TICKETS_DIR=str(repo / ".tickets"), TICKET_AGENT="alice",
               PYTHONPATH=os.pathsep.join([str(ROOT / "src"), str(ROOT)]))
    env.pop("TICKETS_STOP_HOOK", None)
    tool = [sys.executable, str(ROOT / entry)]
    j = subprocess.run(tool + ["join", "alice", "--roles", "backend"], cwd=str(repo),
                       capture_output=True, text=True, env=env)
    assert j.returncode == 0, j.stdout + j.stderr
    r = subprocess.run(tool + ["sync"], cwd=str(repo), capture_output=True, text=True, env=env)
    out = r.stdout + r.stderr
    assert r.returncode != 0
    assert "failed before any conflict" in out, out
    assert "no email was given" in out or "auto-detect" in out or "identity" in out.lower(), out
    assert "CONFLICTS" not in out, "a merge that never started must not be reported as a conflict: %r" % out
    # Nothing was merged: the branch still lacks trunk.txt and there is no MERGE_HEAD.
    assert not (repo / "trunk.txt").exists()
    assert not (repo / ".git" / "MERGE_HEAD").exists()


def test_sync_with_an_identity_still_merges_and_a_real_conflict_still_lists_files(tmp_path):
    """The happy path and the genuine-conflict path are unchanged."""
    repo = _repo_behind_its_own_origin(tmp_path)
    home = tmp_path / "home"
    home.mkdir()
    (home / ".gitconfig").write_text("[user]\n\tname = t\n\temail = t@t\n")
    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    env.update(HOME=str(home), TICKETS_DIR=str(repo / ".tickets"), TICKET_AGENT="alice")
    env.pop("TICKETS_STOP_HOOK", None)
    tool = [sys.executable, str(ROOT / "tickets.py")]
    subprocess.run(tool + ["join", "alice", "--roles", "backend"], cwd=str(repo),
                   capture_output=True, text=True, env=env, check=True)
    r = subprocess.run(tool + ["sync"], cwd=str(repo), capture_output=True, text=True, env=env)
    assert r.returncode == 0, r.stdout + r.stderr
    assert "merged origin/main into alice/work" in r.stdout
    assert (repo / "trunk.txt").exists()
    # Now a real conflict: both sides edit work.txt.
    _git(repo, "checkout", "-q", "main")
    (repo / "work.txt").write_text("trunk version\n")
    _git(repo, "add", "work.txt")
    _git(repo, "commit", "-q", "-m", "trunk edits work.txt")
    _git(repo, "checkout", "-q", "alice/work")
    (repo / "work.txt").write_text("branch version\n")
    _git(repo, "add", "work.txt")
    _git(repo, "commit", "-q", "-m", "branch edits work.txt")
    r = subprocess.run(tool + ["sync"], cwd=str(repo), capture_output=True, text=True, env=env)
    assert r.returncode != 0
    assert "CONFLICTS merging origin/main into alice/work" in r.stdout
    assert "  work.txt" in r.stdout, r.stdout
    assert "failed before any conflict" not in r.stdout
