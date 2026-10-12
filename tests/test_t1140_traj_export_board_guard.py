"""T-1140: trajectories export --out must not land inside the board.

Reproduction (steer-split during T-1118):
  atm trajectories export --out <board>/agents/<seat>.json
silently replaced the seat record with JSONL (exit 0, no warning) on any
board. T-1118 only freezes the split shared board; this guards every board.

Both delivery paths must refuse: root tickets.py and packaged cli.py.

CEO reject@a5ce62f: also refuse symlink escapes (dir link into board,
``..`` through a symlink, and ``<out>.tmp`` symlink / dangling plant).
"""

import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from test_wakeup import board  # noqa: F401

ROOT = Path(__file__).resolve().parents[1]
ROOT_TOOL = ROOT / "tickets.py"
PKG_TOOL = ROOT / "src" / "ticket_board" / "cli.py"


def run_tool(tool, board_path, *args, agent="", cwd=None):
    e = dict(os.environ, TICKETS_DIR=str(board_path), TICKET_AGENT=agent or "",
             HOME=str(board_path.parent.parent / "home"))
    e.pop("TICKETS_STOP_HOOK", None)
    where = cwd or (board_path.parent if board_path.parent.is_dir() else Path("/"))
    return subprocess.run([sys.executable, str(tool), *args], capture_output=True,
                          text=True, env=e, cwd=where)


def board_content_hash(board_path: Path) -> str:
    """Stable hash of board files + symlink targets (detect silent overwrite)."""
    h = hashlib.sha256()
    root = Path(board_path)
    for p in sorted(root.rglob("*")):
        rel = str(p.relative_to(root)).encode()
        if p.is_symlink():
            h.update(rel)
            h.update(b"->")
            h.update(os.readlink(p).encode())
        elif p.is_file():
            h.update(rel)
            h.update(p.read_bytes())
    return h.hexdigest()


@pytest.fixture
def joined(board):
    """Board with one joined agent so agents/<seat>.json exists to protect."""
    repo = board.parent
    r = run_tool(ROOT_TOOL, board, "join", "alice", "--roles", "backend",
                 "--tool", "claude", "--model", "opus", agent="alice", cwd=repo)
    assert r.returncode == 0, r.stdout + r.stderr
    run_tool(ROOT_TOOL, board, "create", "Ship it", "--role", "backend",
             agent="alice", cwd=repo)
    run_tool(ROOT_TOOL, board, "next", "--role", "backend",
             agent="alice", cwd=repo)
    return board


@pytest.mark.parametrize("tool", [ROOT_TOOL, PKG_TOOL], ids=["tickets.py", "cli.py"])
def test_export_refuses_out_inside_board_agents_record(tool, joined, tmp_path):
    b = joined
    repo = b.parent
    seat = b / "agents" / "alice.json"
    assert seat.is_file()
    before = seat.read_text()
    before_obj = json.loads(before)
    assert before_obj.get("name") == "alice" or "alice" in before
    before_hash = board_content_hash(b)

    r = run_tool(tool, b, "trajectories", "export", "--out", str(seat),
                 agent="alice", cwd=repo)
    text = r.stdout + r.stderr
    assert r.returncode != 0, "must refuse export onto agents/<seat>.json:\n" + text
    assert "REFUSING" in text
    assert "outside the ticket board" in text
    assert seat.read_text() == before, "agent record must be unchanged"
    assert not (b / "agents" / "alice.json.tmp").exists()
    assert board_content_hash(b) == before_hash

    # Sibling board paths (not just agents/) are also refused.
    other = b / "trajectories-dump.jsonl"
    r2 = run_tool(tool, b, "trajectories", "export", "--out", str(other),
                  agent="alice", cwd=repo)
    assert r2.returncode != 0, r2.stdout + r2.stderr
    assert not other.exists()
    assert board_content_hash(b) == before_hash

    # Outside the board still works.
    safe = tmp_path / "ok.jsonl"
    r3 = run_tool(tool, b, "trajectories", "export", "--out", str(safe),
                  agent="alice", cwd=repo)
    assert r3.returncode == 0, r3.stdout + r3.stderr
    assert safe.is_file()
    lines = [json.loads(x) for x in safe.read_text().splitlines() if x.strip()]
    assert lines
    assert board_content_hash(b) == before_hash


def test_helper_agrees_across_entrypoints(tmp_path):
    """Package helper classifies paths; root tickets.py keeps a named mirror."""
    sys.path.insert(0, str(ROOT / "src"))
    from ticket_board import trajectories as traj

    board_dir = tmp_path / "board"
    board_dir.mkdir()
    (board_dir / "agents").mkdir()
    inside = str(board_dir / "agents" / "x.json")
    outside = str(tmp_path / "outside.jsonl")
    assert traj.export_path_inside_board(inside, str(board_dir)) is True
    assert traj.export_path_inside_board(outside, str(board_dir)) is False
    # <out>.tmp under the board is refused even when --out itself is new.
    tmp_inside = str(board_dir / "agents" / "new.jsonl.tmp")
    assert traj._path_is_under_board_samefile(tmp_inside, str(board_dir)) is True
    # Directory symlink into the board must classify as inside.
    link_dir = tmp_path / "agents_link"
    os.symlink(board_dir / "agents", link_dir)
    assert traj.export_path_inside_board(str(link_dir / "x.json"), str(board_dir)) is True
    src = ROOT_TOOL.read_text(encoding="utf-8")
    assert "def _traj_export_path_inside_board" in src
    assert "_refuse_traj_export_inside_board" in src
    assert "_traj_path_is_under_board_samefile" in src
    assert "_traj_resolved_path_for_board_guard" in src
    assert "_traj_write_export_file" in src


def _fs_is_case_insensitive(probe_dir: Path) -> bool:
    probe_dir.mkdir(parents=True, exist_ok=True)
    marker = probe_dir / "CaseProbeT1140"
    marker.mkdir(exist_ok=True)
    return (probe_dir / "caseprobet1140").is_dir()


@pytest.mark.parametrize("tool", [ROOT_TOOL, PKG_TOOL], ids=["tickets.py", "cli.py"])
def test_export_refuses_case_aliased_board_path(tool, joined, tmp_path):
    """macOS APFS: .TICKETS vs .tickets bypassed realpath/commonpath (CEO REJECT)."""
    if not _fs_is_case_insensitive(tmp_path / "case-probe"):
        pytest.skip("case-sensitive filesystem")

    b = joined
    repo = b.parent
    seat = b / "agents" / "alice.json"
    assert seat.is_file()
    before = seat.read_text()
    before_hash = board_content_hash(b)

    # Case-aliased board root: string paths differ, inode is the same.
    aliased_board = str(b).replace("/.tickets", "/.TICKETS")
    if aliased_board == str(b):
        # Board path may already use mixed case; force a leaf rename style.
        aliased_board = str(Path(str(b.parent)) / ".TICKETS")
    assert aliased_board != str(b)
    aliased_seat = str(Path(aliased_board) / "agents" / "alice.json")
    assert os.path.samefile(aliased_seat, seat)

    r = run_tool(tool, b, "trajectories", "export", "--out", aliased_seat,
                 agent="alice", cwd=repo)
    text = r.stdout + r.stderr
    assert r.returncode != 0, "must refuse case-aliased --out onto seat record:\n" + text
    assert "REFUSING" in text
    assert seat.read_text() == before, "agent record must be unchanged"
    assert not Path(aliased_seat + ".tmp").exists()
    assert not (seat.parent / "alice.json.tmp").exists()
    assert board_content_hash(b) == before_hash


@pytest.mark.parametrize("tool", [ROOT_TOOL, PKG_TOOL], ids=["tickets.py", "cli.py"])
def test_export_refuses_directory_symlink_into_board(tool, joined, tmp_path):
    """CEO reject (1): ln -s <board>/agents /tmp/x/agents_link; --out link/alice.json."""
    b = joined
    repo = b.parent
    seat = b / "agents" / "alice.json"
    before = seat.read_text()
    before_hash = board_content_hash(b)

    link_dir = tmp_path / "agents_link"
    os.symlink(b / "agents", link_dir)
    out = str(link_dir / "alice.json")

    r = run_tool(tool, b, "trajectories", "export", "--out", out,
                 agent="alice", cwd=repo)
    text = r.stdout + r.stderr
    assert r.returncode != 0, "must refuse dir-symlink --out onto seat:\n" + text
    assert "REFUSING" in text
    assert seat.read_text() == before
    assert board_content_hash(b) == before_hash


@pytest.mark.parametrize("tool", [ROOT_TOOL, PKG_TOOL], ids=["tickets.py", "cli.py"])
def test_export_refuses_dotdot_through_symlink(tool, joined, tmp_path):
    """CEO reject (2): ``dlink/../agents/alice.json`` and ``dlink/../new.jsonl``.

    abspath collapses ``..`` by string and misses the open() resolution into
    the board; the guard must realpath the symlink ancestor first.
    """
    b = joined
    repo = b.parent
    seat = b / "agents" / "alice.json"
    before = seat.read_text()
    before_hash = board_content_hash(b)

    dlink = tmp_path / "dlink"
    os.symlink(b / "agents", dlink)

    out_overwrite = str(dlink) + "/../agents/alice.json"
    assert os.path.abspath(out_overwrite) != str(seat)
    assert Path(out_overwrite).resolve() == seat.resolve()

    r = run_tool(tool, b, "trajectories", "export", "--out", out_overwrite,
                 agent="alice", cwd=repo)
    text = r.stdout + r.stderr
    assert r.returncode != 0, "must refuse ..-through-symlink overwrite:\n" + text
    assert "REFUSING" in text
    assert seat.read_text() == before
    assert board_content_hash(b) == before_hash

    out_plant = str(dlink) + "/../new.jsonl"
    assert Path(out_plant).resolve() == (b / "new.jsonl").resolve()
    r2 = run_tool(tool, b, "trajectories", "export", "--out", out_plant,
                  agent="alice", cwd=repo)
    assert r2.returncode != 0, r2.stdout + r2.stderr
    assert "REFUSING" in (r2.stdout + r2.stderr)
    assert not (b / "new.jsonl").exists()
    assert board_content_hash(b) == before_hash


@pytest.mark.parametrize("tool", [ROOT_TOOL, PKG_TOOL], ids=["tickets.py", "cli.py"])
def test_export_refuses_out_tmp_symlink_into_board(tool, joined, tmp_path):
    """CEO reject (3): existing or dangling ``<out>.tmp`` symlink into the board."""
    b = joined
    repo = b.parent
    seat = b / "agents" / "alice.json"
    before = seat.read_text()
    before_hash = board_content_hash(b)

    safe = tmp_path / "safe.jsonl"
    tmp = Path(str(safe) + ".tmp")

    # Existing .tmp symlink onto the seat record.
    os.symlink(seat, tmp)
    r = run_tool(tool, b, "trajectories", "export", "--out", str(safe),
                 agent="alice", cwd=repo)
    text = r.stdout + r.stderr
    assert r.returncode != 0, "must refuse --out when .tmp is a symlink:\n" + text
    assert "REFUSING" in text
    assert seat.read_text() == before
    assert not safe.exists()
    assert board_content_hash(b) == before_hash

    # Dangling .tmp symlink that would plant a new board file.
    tmp.unlink()
    planted = b / "agents" / "planted.jsonl"
    os.symlink(planted, tmp)
    r2 = run_tool(tool, b, "trajectories", "export", "--out", str(safe),
                  agent="alice", cwd=repo)
    assert r2.returncode != 0, r2.stdout + r2.stderr
    assert "REFUSING" in (r2.stdout + r2.stderr)
    assert not planted.exists()
    assert not safe.exists()
    assert board_content_hash(b) == before_hash
