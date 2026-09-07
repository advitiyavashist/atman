"""`tickets ui` must not bake a server-side timezone into what it shows.

board_snapshot() used to slice message timestamps down to a UTC substring
("09-07 05:44") before they ever left the server, which is not convertible
to the viewer's local time on the client. Timestamps must leave the server
as full ISO-8601 (so `new Date(iso)` can parse them) and the embedded page's
JS must actually run them through a local-time formatter before display.

Runs the real CLI as a subprocess against a throwaway board (see test_wakeup).
"""

import json
import re

from test_wakeup import TOOL, board, run  # noqa: F401


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
    # toLocaleString/toLocaleTimeString with no explicit timeZone option
    # renders in the host (browser) local timezone -- that's the mechanism,
    # so make sure it is actually there and not, e.g., a UTC-fixed formatter.
    assert "toLocaleString" in src
    assert "toUTCString" not in src
