"""T-024: large command output spills to a file; the seat gets head, tail and a path.

Throwaway boards only.
"""
from __future__ import annotations

import os
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import tickets  # noqa: E402


def test_small_output_is_untouched(tmp_path):
    assert tickets.spill_text(str(tmp_path), "alice", "x", "short\ntext", limit=100) == "short\ntext"
    assert not (tmp_path / "agents").exists()


def test_large_output_keeps_head_and_tail_and_writes_everything(tmp_path):
    text = "\n".join("line %03d %s" % (i, "x" * 40) for i in range(200))
    out = tickets.spill_text(str(tmp_path), "alice", "show-T-001", text, limit=2000)
    assert len(out.encode()) < 2400
    assert out.splitlines()[0].startswith("line 000") and out.splitlines()[-1].startswith("line 199")
    m = re.search(r"(\d+) of 200 lines elided \((\d+) bytes total\); full output: (\S+)", out)
    assert m and int(m.group(2)) == len(text.encode())
    path = Path(m.group(3))
    assert path.parent == tmp_path / "agents" / "alice" / "spill"
    assert path.read_text() == text + "\n"


def test_one_huge_line_shows_its_start(tmp_path):
    out = tickets.spill_text(str(tmp_path), "alice", "x", "A" * 10000, limit=1000)
    assert out.startswith("A" * 400) and "full output:" in out and len(out) < 1200


def test_zero_disables_and_old_spills_are_pruned(tmp_path):
    big = "\n".join(str(i) * 50 for i in range(100))
    assert tickets.spill_text(str(tmp_path), "alice", "x", big, limit=0) == big
    for _ in range(tickets.SPILL_KEEP + 5):
        tickets.spill_text(str(tmp_path), "alice", "x", big, limit=500)
    assert len(list((tmp_path / "agents" / "alice" / "spill").glob("*.txt"))) == tickets.SPILL_KEEP


def test_unsafe_seat_name_cannot_escape_the_board(tmp_path):
    out = tickets.spill_text(str(tmp_path), "../../etc", "../x", "y\n" * 3000, limit=500)
    path = Path(re.search(r"full output: (\S+)", out).group(1)).resolve()
    assert str(path).startswith(str((tmp_path / "agents").resolve()) + os.sep)


def cli(tmp_path, *args, env=None):
    e = dict(os.environ, TICKETS_DIR=str(tmp_path / ".tickets"), TICKET_AGENT="alice",
             TICKET_SEAT="alice", HOME=str(tmp_path / "home"), TICKETS_SPILL_BYTES="1500")
    for k in ("TICKETS_STOP_HOOK", "TICKET_SESSION_ID", "CLAUDE_CODE_SESSION_ID"):
        e.pop(k, None)
    e.update(env or {})
    return subprocess.run([sys.executable, str(ROOT / "tickets.py"), *args],
                          capture_output=True, text=True, env=e, cwd=str(tmp_path))


def test_show_and_inbox_spill_and_full_prints_all(tmp_path):
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    assert cli(tmp_path, "init").returncode == 0
    assert cli(tmp_path, "create", "big", "--body", "\n".join("body %d" % i for i in range(400))).returncode == 0
    r = cli(tmp_path, "show", "T-001")
    assert r.returncode == 0 and "full output:" in r.stdout and "body 399" not in r.stdout.splitlines()[:5]
    assert "body 399" in cli(tmp_path, "show", "T-001", "--full").stdout
    assert "full output:" not in cli(tmp_path, "show", "T-001", "--json").stdout
    for i in range(60):
        cli(tmp_path, "msg", "note %02d %s" % (i, "z" * 60), "--to", "bob", env={"TICKET_AGENT": "carol", "TICKET_SEAT": "carol"})
    r = cli(tmp_path, "inbox", "--owner", "bob", "--keep")
    assert "60 unread for bob:" in r.stdout and "full output:" in r.stdout and "note 59" in r.stdout
    full = cli(tmp_path, "inbox", "--owner", "bob", "--full").stdout
    assert all("note %02d" % i in full for i in range(60))
    assert "inbox empty" in cli(tmp_path, "inbox", "--owner", "bob").stdout
