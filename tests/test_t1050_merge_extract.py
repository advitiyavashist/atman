"""T-1050: review/merge topic is a sibling; facade still exposes the same callables.

Proven red on cursor-onboard-t1050-watch@46b4b6f: tickets_merge.py does not
exist, so importing it fails. After the extract the facade aliases must
point at the sibling, and cmd_merge must stay over 300 lines.
"""
import importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def load_tickets():
    spec = importlib.util.spec_from_file_location("tickets_t1050_merge", ROOT / "tickets.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_merge_sibling_owns_review_and_merge():
    assert (ROOT / "tickets_merge.py").is_file()
    tool = load_tickets()
    assert tool.cmd_review.__module__.endswith("tickets_merge")
    assert tool.cmd_accept.__module__.endswith("tickets_merge")
    assert tool.cmd_reject.__module__.endswith("tickets_merge")
    assert tool.cmd_merge.__module__.endswith("tickets_merge")
    assert tool.parse_review_sha.__module__.endswith("tickets_merge")
    assert tool.IntegrationLock.__module__.endswith("tickets_merge")
    assert callable(tool.cmd_merge)


def test_merge_extract_cmd_merge_still_over_300_lines():
    """The split signal is the function body, not the facade alias."""
    tool = load_tickets()
    src = Path(tool.cmd_merge.__code__.co_filename).read_text()
    start = src.index("def cmd_merge(")
    rest = src[start:]
    nxt = rest.find("\ndef ", 1)
    body = rest if nxt < 0 else rest[:nxt]
    assert body.count("\n") + 1 > 300


def test_parse_review_sha_still_reads_branch_at_sha():
    tool = load_tickets()
    assert tool.parse_review_sha("cos-opus@af27511") == "af27511"
    assert tool.parse_review_sha("no-stamp") is None
    assert tool.parse_review_sha("") is None
