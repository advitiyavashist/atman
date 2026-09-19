"""T-1085: the README walkthrough, run top to bottom, leaves a sound board.

Runs every ```sh block from "Feel it in five minutes" up to "Links", in
order, on a throwaway git repo with a throwaway HOME. The only edits are
placeholder substitutions a reader would make by hand; lines that cannot run
headless (the live-login gate demo, the long-running app) are skipped.

Also: `atm graph` names the repair for a dangling dependency, and that repair
command really fixes the board (tickets.py and src/ticket_board/cli.py).
"""
from __future__ import annotations

import os
import re
import shlex
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
README = ROOT / "README.md"
TOOLS = [ROOT / "tickets.py", ROOT / "src" / "ticket_board" / "cli.py"]
TOOL_IDS = ["tickets.py", "cli.py"]

SKIP_PREFIXES = (
    "atm quickstart --gate",   # needs a live coding-CLI login; own scratch dir
    "atm ui",                  # long-running local server
    "cd <your repo>",          # the same quickstart, run below on a real path
)


def _env(home: Path) -> dict:
    e = dict(os.environ, HOME=str(home), TICKETS_GC_OPEN_PRS="none",
             GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t@example.invalid",
             GIT_COMMITTER_NAME="t", GIT_COMMITTER_EMAIL="t@example.invalid")
    for var in ("TICKETS_DIR", "TICKET_AGENT", "TICKET_SEAT", "TICKETS_STOP_HOOK",
                "CLAUDE_CODE_SESSION_ID", "CODEX_SESSION_ID", "CURSOR_SESSION_ID",
                "TERM_SESSION_ID", "TICKET_SESSION_ID"):
        e.pop(var, None)
    e["PYTHONPATH"] = str(ROOT / "src") + os.pathsep + e.get("PYTHONPATH", "")
    return e


def _repo(tmp_path: Path) -> Path:
    proj = tmp_path / "proj"
    proj.mkdir()
    (tmp_path / "home").mkdir()
    env = _env(tmp_path / "home")
    subprocess.run(["git", "init", "-q", "-b", "main"], cwd=proj, check=True, env=env)
    subprocess.run(["git", "commit", "-q", "--allow-empty", "-m", "init"],
                   cwd=proj, check=True, env=env)
    return proj


def walkthrough_blocks() -> list[str]:
    text = README.read_text()
    start = text.index("## Feel it in five minutes")
    end = text.index("## Links", start)
    return [m.group(1) for m in
            re.finditer(r"^[ \t]*```sh\n(.*?)^[ \t]*```", text[start:end],
                        re.S | re.M)]


def walkthrough_script(proj: Path) -> str:
    out = []
    for block in walkthrough_blocks():
        for raw in block.splitlines():
            line = raw.strip()
            if not line or line.startswith(SKIP_PREFIXES):
                continue
            if line.startswith("# ... write"):
                out.append("echo 'def summarize(path): return {}' > csv_summary.py"
                           " && git add csv_summary.py && git commit -qm summarize")
                continue
            if line.startswith("#"):
                continue
            line = line.replace("/path/to/your-project", shlex.quote(str(proj)))
            line = line.replace("/path/to/project", shlex.quote(str(proj)))
            line = line.replace("<full 40-character SHA>", '"$(git rev-parse HEAD)"')
            out.append(line)
    return "\n".join(out) + "\n"


def run_readme(tmp_path: Path):
    proj = _repo(tmp_path)
    tool = shlex.quote(sys.executable) + " " + shlex.quote(str(ROOT / "tickets.py"))
    script = ("set -e\natm() { %s \"$@\"; }\n" % tool) + walkthrough_script(proj)
    r = subprocess.run(["bash", "-c", script], cwd=proj, capture_output=True,
                       text=True, env=_env(tmp_path / "home"), timeout=300)
    return proj, r


def atm(tool: Path, proj: Path, home: Path, *args, agent: str = ""):
    env = _env(home)
    if agent:
        env["TICKET_AGENT"] = agent
    return subprocess.run([sys.executable, str(tool), *args], cwd=proj,
                          capture_output=True, text=True, env=env, timeout=120)


def test_readme_removes_samples_before_own_tickets():
    script = "\n".join(walkthrough_blocks())
    lines = [ln.strip() for ln in script.splitlines()]
    remove = next(i for i, ln in enumerate(lines) if ln.startswith("atm quickstart --remove"))
    first_create = next(i for i, ln in enumerate(lines) if ln.startswith("atm create"))
    assert remove < first_create, "sample removal must be a literal line before `atm create`"
    assert "REVIEW:" not in script, "real handoff output never prints a REVIEW: line"


def test_readme_walkthrough_leaves_no_dangling_reference(tmp_path):
    proj, r = run_readme(tmp_path)
    assert r.returncode == 0, r.stdout[-3000:] + r.stderr[-3000:]
    out = r.stdout
    assert "removed 3 sample ticket(s) (T-001, T-002, T-003)" in out
    assert "created T-001  CSV summary function" in out
    assert "created T-002  CLI command that calls summarize()" in out
    assert "waiting on T-001" in out
    assert "unblocked: T-002" in out
    assert "[>] IN PROGRESS T-002" in out
    assert "Handoff from dependencies" in out
    assert "broken references" not in out

    g = atm(TOOLS[0], proj, tmp_path / "home", "graph")
    assert g.returncode == 0, g.stderr
    assert "broken references" not in g.stdout
    assert "Sample:" not in g.stdout
    assert "T-001 CSV summary function" in g.stdout


@pytest.mark.parametrize("tool", TOOLS, ids=TOOL_IDS)
def test_graph_names_repair_for_dangling_dep(tool, tmp_path):
    proj = _repo(tmp_path)
    home = tmp_path / "home"
    assert atm(tool, proj, home, "create", "sample").returncode == 0      # T-001
    assert atm(tool, proj, home, "create", "mine").returncode == 0        # T-002
    r = atm(tool, proj, home, "create", "follow", "--deps", "T-001")      # T-003
    assert r.returncode == 0, r.stderr
    (proj / ".tickets" / "T-001.json").unlink()   # the sample went away

    g = atm(tool, proj, home, "graph")
    assert "T-003 -> T-001 (no such ticket)" in g.stdout
    hint = [ln.strip() for ln in g.stdout.splitlines() if ln.strip().startswith("repair:")]
    assert hint == ["repair: atm dep T-003 --drop T-001   (re-point: add --after <id>)"]

    # The hinted command is real: re-point the edge and the graph is clean.
    fix = atm(tool, proj, home, "dep", "T-003", "--drop", "T-001", "--after", "T-002")
    assert fix.returncode == 0, fix.stderr
    assert "T-003 now waits for: T-002" in fix.stdout
    g2 = atm(tool, proj, home, "graph")
    assert "broken references" not in g2.stdout
    assert "repair:" not in g2.stdout
