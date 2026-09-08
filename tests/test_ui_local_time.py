"""`tickets ui` must not bake a server-side timezone into what it shows.

board_snapshot() used to slice message timestamps down to a UTC substring
("09-07 05:44") before they ever left the server, which is not convertible
to the viewer's local time on the client. Timestamps must leave the server
as full ISO-8601 (so `new Date(iso)` can parse them) and the embedded page's
JS must actually run them through a local-time formatter before display.

Runs the real CLI as a subprocess against a throwaway board (see test_wakeup).
"""

import importlib.util
import json
import os
import re
import time
from pathlib import Path

from test_wakeup import TOOL, board, run  # noqa: F401

UTC_STAMP = "2026-09-06T14:32:00Z"
# 2026-09-06 is PDT (UTC-7) / EDT (UTC-4).
LA_DISPLAY = "09-06 07:32"
NY_DISPLAY = "09-06 10:32"
UTC_SLICE = "09-06 14:32"


def _load_module(path):
    spec = importlib.util.spec_from_file_location("local_time_mod", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_board_json_sends_full_iso_message_timestamps(board):
    run(board, "msg", "hello from the past", agent="alice")
    r = run(board, "ui", "--json")
    assert r.returncode == 0, r.stderr
    data = json.loads(r.stdout)
    msgs = [m for m in data["messages"] if m["text"] == "hello from the past"]
    assert msgs, data["messages"]
    at = msgs[0]["at"]
    # Full ISO-8601 UTC, not a truncated "MM-DDTHH:MM" substring -- must be
    # parseable by JS `new Date()` on the client and carry enough information
    # (a real date) to convert to the viewer's own timezone.
    assert re.match(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$", at), at


def test_ui_html_formats_message_times_client_side():
    # The served page must define and use a local-time formatter for message
    # timestamps rather than printing the raw server string verbatim. Read
    # the embedded page straight out of the source rather than spinning up
    # the HTTP server -- it's a literal, so this is exactly what gets served.
    src = TOOL.read_text()
    assert "UI_HTML" in src
    assert "fmtLocal" in src
    assert "fmtLocal(m.at)" in src
    # toLocaleString with no explicit timeZone option renders in the host
    # (browser) local timezone -- that's the mechanism, so make sure it is
    # actually there and not, e.g., a UTC-fixed formatter.
    assert "toLocaleString(undefined" in src
    assert "timeZoneName:'short'" in src
    assert "toUTCString" not in src
    assert "timeZone:'UTC'" not in src
    assert 'timeZone:"UTC"' not in src


def _with_tz(name):
    """Set TZ for this process and put it back afterwards.

    `time.tzset()` is what `datetime.astimezone()` actually reads; restoring
    the env var alone would leak the test zone into later cases.
    """
    previous = os.environ.get("TZ")

    class _Guard:
        def __enter__(self):
            os.environ["TZ"] = name
            time.tzset()
            return self

        def __exit__(self, *exc):
            if previous is None:
                os.environ.pop("TZ", None)
            else:
                os.environ["TZ"] = previous
            time.tzset()
            return False

    return _Guard()


def test_fmt_local_converts_utc_to_the_reader_timezone():
    tickets = _load_module(TOOL)
    with _with_tz("America/Los_Angeles"):
        assert tickets.fmt_local(UTC_STAMP) == LA_DISPLAY
        # Naive stamps are the wire contract's UTC without a Z — still convert.
        assert tickets.fmt_local("2026-09-06T14:32:00") == LA_DISPLAY
        assert tickets.fmt_local(UTC_STAMP) != UTC_SLICE
    with _with_tz("America/New_York"):
        assert tickets.fmt_local(UTC_STAMP) == NY_DISPLAY


def test_packaged_cli_fmt_local_matches_root_helper():
    cli = _load_module(Path(__file__).resolve().parents[1] / "src" / "ticket_board" / "cli.py")
    with _with_tz("America/Los_Angeles"):
        assert cli.fmt_local(UTC_STAMP) == LA_DISPLAY
        assert cli.fmt_msg({"at": UTC_STAMP, "from": "alice", "to": "all",
                            "text": "hi"}) == "%s  alice: hi" % LA_DISPLAY


def test_inbox_prints_local_time_not_the_utc_substring(board):
    path = Path(board) / "messages.jsonl"
    path.write_text(json.dumps({
        "at": UTC_STAMP, "from": "alice", "to": "all", "re": "",
        "text": "fixed stamp",
    }) + "\n")
    r = run(board, "inbox", "--all", agent="bob", env={"TZ": "America/Los_Angeles"})
    assert r.returncode == 0, r.stderr
    assert LA_DISPLAY in r.stdout
    assert UTC_SLICE not in r.stdout
    assert "fixed stamp" in r.stdout
