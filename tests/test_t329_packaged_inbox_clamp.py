"""T-329: the packaged entry point must have the same skew clamp as the root.

T-228 fixed the same-second inbox hole and then, in a later commit, clamped the
watermark so a future-stamped record could not drag it past the present. The
clamp went into the root tickets.py only -- but the EARLIER T-228 commits had
already ported the same-second rewrite into src/ticket_board/cli.py. The
packaged path was therefore left with `watermark = max(at)` and no ceiling,
which newly blinds it for the length of any clock skew. Found by opus-liveness
in the T-302 adversarial pass on T-228; the analysis there is the specification
for this file.

WHY IT COULD DRIFT AT ALL, which is the part worth fixing beyond the port: all
15 T-228 tests load the root tickets.py by path, and exactly one file in the
whole suite touches the packaged cli. So the two copies of the delivery path
had no shared coverage and one of them could go stale inside a single ticket
with nothing going red.

Every test here therefore runs against BOTH modules. If the copies drift again
in either direction, these fail.
"""
import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


def _load_root():
    spec = importlib.util.spec_from_file_location("tickets_t329", str(ROOT / "tickets.py"))
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def _load_pkg():
    sys.path.insert(0, str(ROOT / "src"))
    import ticket_board.cli as m  # noqa: E402
    return m


@pytest.fixture(params=["root", "packaged"])
def mod(request):
    """The two copies of the delivery path. Both must behave identically."""
    return _load_root() if request.param == "root" else _load_pkg()


def _stamp(mod, seconds):
    """An ISO stamp `seconds` away from the module's own notion of now()."""
    from datetime import datetime, timedelta, timezone
    base = datetime.strptime(mod.now(), "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
    return (base + timedelta(seconds=seconds)).strftime("%Y-%m-%dT%H:%M:%SZ")


def _board(tmp_path, msgs, rec=None, owner="dave"):
    b = tmp_path / ".tickets"
    (b / "agents").mkdir(parents=True)
    (b / "agents" / (owner + ".json")).write_text(json.dumps(rec or {"name": owner}))
    (b / "messages.jsonl").write_text("".join(json.dumps(m) + "\n" for m in msgs))
    return b


def _texts(msgs):
    return [m.get("text") for m in msgs]


# ---- the regression ------------------------------------------------------

def test_a_skewed_dm_between_two_others_does_not_blind_a_bystander(tmp_path, mod):
    """opus-liveness's exact scenario, and the sharpest form of the defect.

    The watermark is a max over the WHOLE file, taken before the addressing
    filter, so a future-stamped DM that dave can never even see still drags
    his watermark into the future -- one bad record blinds every agent that
    reads after it.
    """
    far = _stamp(mod, 7200)          # +2h, a plausible clock skew
    b = _board(tmp_path, [
        {"at": far, "from": "alice", "to": "carol", "re": "", "text": "SKEWED-DM-NOT-FOR-DAVE"},
    ])
    mod._mark_inbox_read(str(b), "dave")          # dave reads; watermark set
    rec = json.loads((b / "agents" / "dave.json").read_text())
    assert rec["inbox_seen"] <= mod.now(), (
        "watermark was advanced into the future: %s" % rec["inbox_seen"])

    # now real mail arrives for dave, stamped normally
    with (b / "messages.jsonl").open("a") as f:
        f.write(json.dumps({"at": mod.now(), "from": "alice", "to": "dave",
                            "re": "", "text": "AFTER-THE-READ"}) + "\n")
    assert "AFTER-THE-READ" in _texts(mod.unread(str(b), "dave"))


def test_watermark_is_never_advanced_past_the_present(tmp_path, mod):
    """The write-side ceiling, asserted directly on the scan's return value."""
    far = _stamp(mod, 86400)         # a day out, not merely a second
    b = _board(tmp_path, [
        {"at": far, "from": "alice", "to": "all", "re": "", "text": "FROM-THE-FUTURE"},
    ])
    _out, watermark, _retained = mod._inbox_scan(str(b), "dave")
    assert watermark <= mod.now(), "watermark %s is ahead of now()" % watermark


def test_a_watermark_already_in_the_future_heals_on_the_next_read(tmp_path, mod):
    """The READ-side clamp. A record poisoned before this shipped must recover.

    A preventive-only clamp cannot heal an agent whose inbox_seen is already
    in the future -- it would stay blind until the skew expired.
    """
    far = _stamp(mod, 7200)
    b = _board(tmp_path, [
        {"at": _stamp(mod, 0), "from": "alice", "to": "dave", "re": "", "text": "ORDINARY-MAIL"},
    ], rec={"name": "dave", "inbox_seen": far})   # already poisoned
    assert "ORDINARY-MAIL" in _texts(mod.unread(str(b), "dave"))


def test_future_stamped_message_is_delivered_once_not_every_poll(tmp_path, mod):
    """Clamping alone would leave the message permanently above the watermark
    and redelivered on every poll -- the wake storm the identity set exists to
    prevent. It must arrive exactly once."""
    far = _stamp(mod, 7200)
    b = _board(tmp_path, [
        {"at": far, "from": "alice", "to": "dave", "re": "", "text": "FROM-THE-FUTURE"},
    ])
    first = _texts(mod.unread(str(b), "dave"))
    mod._mark_inbox_read(str(b), "dave")
    second = _texts(mod.unread(str(b), "dave"))
    assert "FROM-THE-FUTURE" in first, "never delivered at all"
    assert "FROM-THE-FUTURE" not in second, "redelivered on the next poll (wake storm)"


# ---- the property the port must not break --------------------------------

def test_same_second_message_is_still_delivered(tmp_path, mod):
    """T-228's original defect stays fixed in both copies."""
    tie = _stamp(mod, 0)
    b = _board(tmp_path, [
        {"at": tie, "from": "alice", "to": "dave", "re": "", "text": "SAME-SECOND-DM"},
    ], rec={"name": "dave", "inbox_seen": tie})
    assert "SAME-SECOND-DM" in _texts(mod.unread(str(b), "dave"))


def test_boundary_message_is_not_redelivered_forever(tmp_path, mod):
    """...and the naive `>=` repair stays ruled out in both copies."""
    tie = _stamp(mod, 0)
    b = _board(tmp_path, [
        {"at": tie, "from": "alice", "to": "dave", "re": "", "text": "SAME-SECOND-DM"},
    ], rec={"name": "dave", "inbox_seen": tie})
    assert "SAME-SECOND-DM" in _texts(mod.unread(str(b), "dave"))
    mod._mark_inbox_read(str(b), "dave")
    assert "SAME-SECOND-DM" not in _texts(mod.unread(str(b), "dave"))


# ---- and end to end, through the shipped console script ------------------

def test_packaged_console_script_sees_mail_after_a_skewed_record(tmp_path):
    """Not the module -- the actual `tickets = ticket_board.cli:main` path.

    cli.py's main() imports top-level `ticket_coordination`, which lives at the
    repo root, so the root goes on PYTHONPATH beside src/. That is a packaging
    wart that predates this ticket; it is worked around here rather than fixed.
    """
    root_mod = _load_root()
    b = tmp_path / "repo" / ".tickets"
    (b / "agents").mkdir(parents=True)
    (tmp_path / "home").mkdir()
    far = _stamp(root_mod, 7200)
    (b / "messages.jsonl").write_text(json.dumps(
        {"at": far, "from": "alice", "to": "carol", "re": "", "text": "SKEWED-DM"}) + "\n")

    env = dict(os.environ, TICKETS_DIR=str(b), TICKET_AGENT="dave",
               HOME=str(tmp_path / "home"),
               PYTHONPATH=os.pathsep.join([str(ROOT / "src"), str(ROOT)]))
    env.pop("TICKETS_STOP_HOOK", None)

    def cli(*args):
        return subprocess.run([sys.executable, "-m", "ticket_board", *args],
                              capture_output=True, text=True, env=env, cwd=str(tmp_path))

    cli("inbox")                                  # dave reads, watermark set
    with (b / "messages.jsonl").open("a") as f:
        f.write(json.dumps({"at": root_mod.now(), "from": "alice", "to": "dave",
                            "re": "", "text": "AFTER-THE-READ"}) + "\n")
    r = cli("inbox")
    assert "AFTER-THE-READ" in r.stdout, (r.stdout, r.stderr)
