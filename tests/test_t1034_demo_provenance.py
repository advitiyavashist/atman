"""Publication checks for the repaired PR #211 demo provenance."""

import hashlib
import json
from pathlib import Path
import re
import subprocess
import sys

from PIL import Image


ROOT = Path(__file__).resolve().parents[1]
ASSETS = ROOT / "docs/assets/demo"
EVIDENCE = ASSETS / "evidence"
ACCEPTED = "db0849bb7c3928e0123b6007ff6fb8b1a23d352d"
B_COMMIT = "ba286156e85d715815ec90bd18d372bbfde55e8a"
ANSI = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]")


def sha256(data):
    return hashlib.sha256(data).hexdigest()


def test_published_media_bytes_and_cast_body_are_preserved():
    assert sha256((ASSETS / "hero.gif").read_bytes()) == "be0a24e40ce0836a28d439b7782ccaf8e605519e875db24d8eb36326bf75e1f7"
    assert sha256((ASSETS / "poster.png").read_bytes()) == "5146ead7ed57bbefeb5a6568d69881b02ec68ca43353aa0eabdfb28aa9339d53"
    body = (ASSETS / "demo.cast").read_bytes().split(b"\n", 1)[1]
    assert sha256(body) == "23527a2bf7aa1e61ba279d9c8d6ccf3bb6fc04fd0b0c23d4b784032dd40f6b8c"


def test_gif_and_poster_decode_and_hero_is_under_eight_mb():
    hero = ASSETS / "hero.gif"
    assert hero.stat().st_size < 8 * 1024 * 1024
    with Image.open(hero) as image:
        assert image.size == (790, 560)
        assert image.n_frames == 22
        for frame in range(image.n_frames):
            image.seek(frame)
            image.load()
    with Image.open(ASSETS / "poster.png") as image:
        assert image.size == (790, 560)
        image.load()


def test_cast_header_and_public_evidence_do_not_expose_operator_paths():
    cast_lines = (ASSETS / "demo.cast").read_text().splitlines()
    header = json.loads(cast_lines[0])
    assert header["command"] == "zsh docs/assets/demo/record-take.sh <RUN>"
    published = "\n".join(
        path.read_text(errors="replace")
        for path in [ASSETS / "demo.cast", ASSETS / "captions.md", *EVIDENCE.rglob("*")]
        if path.is_file()
    )
    for forbidden in ("/Users/", "/private/", "kavana"):
        assert forbidden not in published


def test_replay_is_parameterized_and_installs_all_recorded_prompts(tmp_path):
    run = tmp_path / "demo-run"
    run.mkdir()
    (run / "manifest.json").write_text(json.dumps({"kind": "atman-isolated-demo", "run": str(run)}))
    subprocess.run([sys.executable, ASSETS / "prepare-replay.py", run], check=True)
    for name in ("ceo-plan.prompt", "codex-worker.prompt", "ceo-accept.prompt", "cursor-worker.prompt"):
        text = (run / name).read_text()
        assert "<RUN>" not in text
        assert str(run) in text or name in ("codex-worker.prompt", "cursor-worker.prompt")
    script = (ASSETS / "record-take.sh").read_text()
    assert 'R="${1:?usage: record-take.sh <RUN>}"' in script
    assert "atman-demo-llt758p0" not in script


def test_cast_commands_match_the_parameterized_replay_sequence():
    script = (ASSETS / "record-take.sh").read_text()
    expected = re.findall(r"^run '(.+)'$", script, re.MULTILINE)
    actual = []
    for raw in (ASSETS / "demo.cast").read_text().splitlines()[1:]:
        event = json.loads(raw)
        clean = ANSI.sub("", event[2]).replace("\r", "")
        if clean.startswith("$ "):
            actual.append(clean[2:].rstrip("\n"))
    assert actual == expected


def test_execution_receipts_bind_distinct_reviewer_acceptance_and_cursor_handoff():
    manifest = json.loads((EVIDENCE / "manifest.json").read_text())
    claims = manifest["claims"]
    assert claims["worker"] != claims["reviewer"]
    assert claims["reviewer_is_not_worker"] is True
    assert claims["accepted_commit"] == ACCEPTED
    assert claims["cursor_commit"] == B_COMMIT
    review = (EVIDENCE / "review.codex.jsonl").read_text()
    cursor = (EVIDENCE / "handoff.cursor.jsonl").read_text()
    assert f"atm accept T-001 --sha {ACCEPTED}" in review
    assert f"git merge --ff-only {ACCEPTED}" in cursor
    assert B_COMMIT in cursor
    assert claims["ancestry_evidence"]["independent_object_check"].startswith("unavailable:")


def test_remote_pr_snapshot_binds_acceptance_to_the_full_head():
    snapshot = json.loads((EVIDENCE / "remote-pr.json").read_text())
    assert snapshot["head_sha"] == ACCEPTED
    assert snapshot["pull_request"] == 5
    assert snapshot["repository"] == "advitiyavashist/atman-demo-sales"


def test_copy_discloses_caption_header_and_receipt_transformations():
    captions = (ASSETS / "captions.md").read_text().lower()
    assert "one unedited run" not in captions
    assert "raw recording" not in captions
    assert "two narration payloads" in captions
    assert "header" in captions and "sanitized" in captions
    assert "command output" in captions and "not changed" in captions
    assert "independently checkable" in captions
    assert "tail of each provider turn" in captions
    assert "merge conflict" in captions
