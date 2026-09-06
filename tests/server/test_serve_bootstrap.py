"""Starting a server: loopback, and how the operator gets a session at all.

The frozen contract has no sign-in route, so the bootstrap below *is* the
operator authentication story for V1. That makes these tests part of the
security surface rather than plumbing checks.
"""

import http.client
import json
import os
import stat

import pytest

from ticket_board.server.httpd import is_loopback, serve


@pytest.fixture()
def running(tmp_path):
    httpd, thread, credentials = serve(
        tmp_path / "board.sqlite3", host="127.0.0.1", port=0,
        project_name="Demo", state_dir=str(tmp_path), block=False)
    try:
        yield httpd, credentials, tmp_path
    finally:
        httpd.shutdown()
        httpd.server_close()
        thread.join(timeout=5)


def test_the_operator_session_file_is_private_before_it_is_written(running):
    """0600 at creation, not chmod-ed afterwards.

    Creating it world-readable and fixing the mode later leaves a window in
    which any process on the machine can read the session token.
    """
    _, credentials, _ = running
    mode = stat.S_IMODE(os.stat(credentials["path"]).st_mode)
    assert mode == 0o600


def test_the_session_file_carries_what_a_dashboard_needs(running):
    _, credentials, _ = running
    payload = json.loads(open(credentials["path"]).read())
    assert set(payload) == {"project_id", "session_token", "csrf_token", "url"}
    assert payload["project_id"].startswith("prj_")


def test_the_bootstrapped_session_actually_works(running):
    httpd, credentials, _ = running
    conn = http.client.HTTPConnection("127.0.0.1", httpd.server_address[1],
                                      timeout=5)
    conn.request("GET", "/overview", headers={
        "X-Project-Id": credentials["project_id"],
        "Cookie": "tb_session=" + credentials["session_token"]})
    response = conn.getresponse()
    assert response.status == 200
    conn.close()


def test_a_stolen_csrf_token_alone_is_not_a_session(running):
    httpd, credentials, _ = running
    conn = http.client.HTTPConnection("127.0.0.1", httpd.server_address[1],
                                      timeout=5)
    conn.request("GET", "/overview", headers={
        "X-Project-Id": credentials["project_id"],
        "X-CSRF-Token": credentials["csrf_token"]})
    assert conn.getresponse().status == 401
    conn.close()


def test_binding_a_non_loopback_host_is_refused(tmp_path):
    """This build has no remote operator authentication to bind *to*."""
    with pytest.raises(ValueError) as excinfo:
        serve(tmp_path / "b.sqlite3", host="0.0.0.0", port=0, block=False)
    assert "no remote operator authentication" in str(excinfo.value)


def test_the_refusal_can_be_overridden_deliberately(tmp_path):
    """Explicit, and it has to be said out loud in code, not by config drift."""
    httpd, thread, _ = serve(tmp_path / "b.sqlite3", host="127.0.0.1", port=0,
                             allow_remote=True, state_dir=str(tmp_path),
                             block=False)
    httpd.shutdown()
    httpd.server_close()
    thread.join(timeout=5)


@pytest.mark.parametrize("host,expected", [
    ("127.0.0.1", True), ("localhost", True), ("::1", True),
    ("0.0.0.0", False), ("10.0.0.4", False), ("example.com", False),
])
def test_loopback_detection(host, expected):
    assert is_loopback(host) is expected


def test_restarting_reuses_the_existing_project(tmp_path):
    """A second start must not silently create a second board in one file."""
    first, thread, credentials = serve(tmp_path / "b.sqlite3", host="127.0.0.1",
                                       port=0, state_dir=str(tmp_path),
                                       block=False)
    first.shutdown()
    first.server_close()
    thread.join(timeout=5)
    second, thread2, again = serve(tmp_path / "b.sqlite3", host="127.0.0.1",
                                   port=0, state_dir=str(tmp_path), block=False)
    try:
        assert again["project_id"] == credentials["project_id"]
    finally:
        second.shutdown()
        second.server_close()
        thread2.join(timeout=5)


def test_restarting_onto_a_loosened_file_tightens_it(tmp_path):
    """`O_CREAT`'s mode is ignored for a file that already exists.

    So a second start onto a file someone had chmod-ed open would keep those
    permissions. Found by mutation testing: making the open mode 0644 left every
    other test green, because a `chmod` afterwards was quietly covering for it.
    """
    path = tmp_path / "operator-session.json"
    path.write_text("{}")
    os.chmod(path, 0o644)
    httpd, thread, credentials = serve(tmp_path / "b.sqlite3", host="127.0.0.1",
                                       port=0, state_dir=str(tmp_path),
                                       block=False)
    try:
        assert stat.S_IMODE(os.stat(credentials["path"]).st_mode) == 0o600
    finally:
        httpd.shutdown()
        httpd.server_close()
        thread.join(timeout=5)
