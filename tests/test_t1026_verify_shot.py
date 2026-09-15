"""T-1026: annotated screenshot evidence — files, not an IDE panel.

Live Playwright is optional. These tests cover the contract a verification
seat can rely on without a browser: bbox honesty, HTML mark, sidecar, and
ticket attach. --url without Playwright must fail out loud.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from test_wakeup import board  # noqa: F401

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from ticket_board import verify_shot as vs  # noqa: E402

TOOLS = [ROOT / "tickets.py", ROOT / "src" / "ticket_board" / "cli.py"]
TOOL_IDS = ["tickets.py", "cli.py"]

# 1x1 RGB PNG (valid signature). Used as --png input; we do not decode pixels.
MIN_PNG = bytes.fromhex(
    "89504e470d0a1a0a0000000d4948445200000001000000010802000000907753de"
    "0000000c4944415408d763f8cfc00000000300013b6ea85a0000000049454e44ae426082"
)


def run(tool, *args, board=None, agent="t1026", cwd=None, env=None):
    e = dict(os.environ, TICKET_AGENT=agent)
    e.pop("TICKETS_STOP_HOOK", None)
    e["PYTHONPATH"] = str(ROOT / "src") + os.pathsep + e.get("PYTHONPATH", "")
    if board is not None:
        e["TICKETS_DIR"] = str(board)
        e["HOME"] = str(board.parent.parent / "home")
        (board.parent.parent / "home").mkdir(exist_ok=True)
    if env:
        e.update(env)
    where = cwd or (board.parent if board is not None else ROOT)
    return subprocess.run(
        [sys.executable, str(tool), *args],
        capture_output=True, text=True, env=e, cwd=str(where),
    )


def write_png(path):
    path.write_bytes(MIN_PNG)
    return path


def test_parse_bbox_rejects_dishonest_values():
    box = vs.parse_bbox("10,20,30,40")
    assert box == {"x": 10.0, "y": 20.0, "width": 30.0, "height": 40.0}
    with pytest.raises(ValueError):
        vs.parse_bbox("10,20,30")
    with pytest.raises(ValueError):
        vs.parse_bbox("10,20,0,40")
    with pytest.raises(ValueError):
        vs.parse_bbox("1,2,3,nan")
    with pytest.raises(ValueError):
        vs.parse_bbox("1,2,inf,4")


def test_write_pack_marks_element_in_html_and_sidecar(tmp_path):
    rec = {
        "ticket": "T-1026",
        "label": "counted as 1/6 done",
        "selector": ".done-chip",
        "note": "unverified done inflated the header",
        "bbox": {"x": 12, "y": 34, "width": 80, "height": 18},
        "url": "http://127.0.0.1:18765/",
        "captured_at": "2026-09-15T07:40:00Z",
        "playwright": False,
    }
    out = vs.write_pack(tmp_path, rec, MIN_PNG)
    paths = out["_paths"]
    sidecar = json.loads(Path(paths["json"]).read_text(encoding="utf-8"))
    assert sidecar["kind"] == vs.KIND
    assert sidecar["v"] == 1
    assert sidecar["selector"] == ".done-chip"
    assert sidecar["bbox"]["width"] == 80
    assert sidecar["files"]["png"].endswith(".png")
    html = Path(paths["html"]).read_text(encoding="utf-8")
    assert 'src="%s"' % sidecar["files"]["png"] in html
    assert "counted as 1/6 done" in html
    assert ".done-chip" in html
    assert "left:12.00px" in html
    assert "width:80.00px" in html
    assert sidecar["files"]["annotated_png"] is None
    note = vs.note_text(sidecar)
    assert note.startswith("evidence-shot:")
    assert "counted as 1/6 done" in note
    assert "selector=.done-chip" in note


def test_write_pack_keeps_baked_mark_when_given(tmp_path):
    rec = {
        "label": "OFFLINE is not lost",
        "selector": "#status",
        "bbox": {"x": 1, "y": 2, "width": 3, "height": 4},
        "captured_at": "2026-09-15T07:41:00Z",
    }
    out = vs.write_pack(tmp_path, rec, MIN_PNG, annotated_bytes=MIN_PNG + b"")
    assert out["files"]["annotated_png"].endswith("-marked.png")
    assert Path(out["_paths"]["annotated_png"]).is_file()


@pytest.mark.parametrize("tool,tool_id", zip(TOOLS, TOOL_IDS))
def test_cli_png_writes_pack_and_attaches_note(board, tmp_path, tool, tool_id):
    png = write_png(tmp_path / "raw.png")
    out = tmp_path / "evidence"
    r = run(
        tool, "shot",
        "--png", str(png),
        "--bbox", "8,16,40,12",
        "--label", "submitted is not accepted",
        "--note", "T-005 still awaiting review",
        "--ticket", "T-001",
        "--out", str(out),
        "--json",
        board=board, agent="t1026",
    )
    assert r.returncode == 0, tool_id + "\n" + r.stdout + r.stderr
    payload = json.loads(r.stdout)
    assert payload["kind"] == vs.KIND
    assert payload["ticket"] == "T-001"
    assert payload["label"] == "submitted is not accepted"
    assert payload["bbox"]["x"] == 8
    html_path = Path(payload["paths"]["html"])
    assert html_path.is_file()
    assert "submitted is not accepted" in html_path.read_text(encoding="utf-8")
    shown = run(tool, "show", "T-001", "--json", board=board, agent="t1026")
    assert shown.returncode == 0, shown.stderr
    ticket = json.loads(shown.stdout)
    texts = [n.get("text") or "" for n in ticket.get("notes") or []]
    assert any(t.startswith("evidence-shot:") and "submitted is not accepted" in t
               for t in texts), texts


@pytest.mark.parametrize("tool", TOOLS)
def test_cli_refuses_png_without_bbox(tmp_path, tool, board):
    png = write_png(tmp_path / "raw.png")
    r = run(
        tool, "shot", "--png", str(png), "--label", "no mark",
        "--out", str(tmp_path / "out"),
        board=board,
    )
    assert r.returncode != 0
    err = r.stderr + r.stdout
    assert "--bbox" in err
    assert not list((tmp_path / "out").glob("*"))


@pytest.mark.parametrize("tool", TOOLS)
def test_cli_refuses_url_without_playwright_loudly(tmp_path, tool, board):
    r = run(
        tool, "shot",
        "--url", "http://127.0.0.1:9/",
        "--selector", ".done-chip",
        "--label", "binary is not connected",
        "--out", str(tmp_path / "out"),
        board=board,
    )
    try:
        import playwright  # noqa: F401
        has_pw = True
    except ImportError:
        has_pw = False
    if has_pw:
        # Chromium may or may not be installed; either a navigation failure
        # or a missing-browser error is honest. An empty success is not.
        assert r.returncode != 0
        assert "shot:" in (r.stderr + r.stdout)
        return
    assert r.returncode != 0
    err = r.stderr + r.stdout
    assert "Playwright is not installed" in err
    assert "not a new browser" in err


@pytest.mark.parametrize("tool", TOOLS)
def test_cli_help_names_shot(tool, board):
    r = run(tool, "shot", "--help", board=board)
    assert r.returncode == 0, r.stderr
    out = r.stdout
    assert "--selector" in out and "--pick" in out and "--label" in out
    assert "IDE" not in out or "not" in out.lower()


def test_default_out_dir_uses_ticket_slug():
    d = vs.default_out_dir("T-1026", cwd="/tmp/repo")
    assert str(d) == "/tmp/repo/docs/reviews/t1026"


@pytest.mark.parametrize("tool", TOOLS)
def test_cli_refuses_url_and_png_together(tmp_path, tool, board):
    png = write_png(tmp_path / "raw.png")
    r = run(
        tool, "shot",
        "--url", "http://127.0.0.1:9/",
        "--png", str(png),
        "--label", "both",
        board=board,
    )
    assert r.returncode != 0
    assert "exactly one" in (r.stderr + r.stdout)


def test_overlay_js_includes_bbox_and_escaped_label():
    js = vs.overlay_js({"x": 4, "y": 5, "width": 6, "height": 7}, '1/6 "done"')
    assert '"x": 4' in js
    assert "1/6" in js
    assert '\\"' in js or '"done"' in js


def test_live_playwright_marks_fixture_element(tmp_path):
    pytest.importorskip("playwright.sync_api")
    html = ROOT / "tests" / "fixtures" / "t1026" / "state.html"
    url = html.resolve().as_uri()
    try:
        live = vs.capture_live(url, selector=".done-chip", label="counted as 1/6 done")
    except RuntimeError as exc:
        if "Chromium is not installed" in str(exc):
            pytest.skip(str(exc))
        raise
    assert live["playwright"] is True
    assert live["selector"] == ".done-chip"
    assert live["bbox"]["width"] > 0
    pack = vs.write_pack(tmp_path, {
        "label": "counted as 1/6 done",
        "selector": live["selector"],
        "bbox": live["bbox"],
        "url": url,
        "captured_at": "2026-09-15T07:42:00Z",
    }, live["png"], annotated_bytes=live["annotated_png"])
    assert Path(pack["_paths"]["annotated_png"]).stat().st_size > 0
    assert Path(pack["_paths"]["png"]).stat().st_size > 0
