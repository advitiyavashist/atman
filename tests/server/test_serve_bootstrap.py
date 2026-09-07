"""Starting a server: loopback, and how the operator gets a session at all.

The frozen contract has no sign-in route, so the bootstrap below *is* the
operator authentication story for V1. That makes these tests part of the
security surface rather than plumbing checks.
"""

import http.client
import json
import os
import stat
import uuid
from urllib.parse import urlsplit

import pytest

from ticket_board.server.httpd import advertised_url, is_loopback, serve


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


# --- T-280: what the board records about its own address ------------------
#
# `port=0` means "any free port"; the OS picks one and only the bound socket
# knows which. Building the recorded url from the *requested* port persisted
# the literal `http://127.0.0.1:0`, which is not dialable and never matches the
# Origin a browser sends -- so every operator write from the board's own
# dashboard was refused 403, and every enrollment advertised an install command
# pointing at port 0.
#
# These tests dial the RECORDED url rather than `httpd.server_address`. That
# distinction is the whole point: every pre-existing test here reached the
# board through the socket directly and so could not see the defect, and a test
# that merely asserts the url is non-zero would still pass a half-fix that got
# the port right and the host wrong.


def _dial(url, timeout=5):
    """Connect to a url as a client would, using only what was recorded."""
    split = urlsplit(url)
    assert split.hostname, "recorded url has no host: %r" % (url,)
    assert split.port, "recorded url has no port: %r" % (url,)
    return http.client.HTTPConnection(split.hostname, split.port,
                                      timeout=timeout)


def _operator_write(url, credentials, title="from the recorded url"):
    """POST a ticket over the recorded url, sending it as Origin too."""
    conn = _dial(url)
    body = json.dumps({
        "request_id": str(uuid.uuid4()),
        "title": title,
        "outcome": "the write lands",
        "acceptance": [{"text": "a real record exists afterwards"}],
    })
    conn.request("POST", "/tickets", body=body, headers={
        "Content-Type": "application/json",
        "Content-Length": str(len(body)),
        "X-Project-Id": credentials["project_id"],
        "Cookie": "tb_session=" + credentials["session_token"],
        "X-CSRF-Token": credentials["csrf_token"],
        # A browser sends the origin it loaded the dashboard from. If the board
        # recorded an address it is not reachable at, this is the header that
        # will not match.
        "Origin": url,
    })
    response = conn.getresponse()
    payload = response.read().decode()
    conn.close()
    return response.status, payload


def test_a_port_zero_board_records_the_port_it_actually_bound(running):
    httpd, credentials, _ = running
    recorded = json.loads(open(credentials["path"]).read())
    bound_port = httpd.server_address[1]
    assert bound_port != 0
    assert recorded["url"] == "http://127.0.0.1:%d" % bound_port
    # The board must describe itself the same way it was recorded, or the CSRF
    # allowlist and the dashboard disagree about where the board is.
    assert httpd.board.base_url == recorded["url"]


def test_an_operator_write_through_the_recorded_url_actually_lands(running):
    """The end-to-end assertion: a real write, reached only via the file.

    Not `assert ":0" not in url`. This connects to whatever was recorded, sends
    it as Origin, and then proves the ticket exists by reading it back -- so a
    fix that records a reachable address but breaks the write still fails.
    """
    httpd, credentials, _ = running
    recorded = json.loads(open(credentials["path"]).read())

    status, payload = _operator_write(recorded["url"], credentials)
    assert status == 201, payload
    created = json.loads(payload)

    conn = _dial(recorded["url"])
    conn.request("GET", "/tickets/" + created["id"], headers={
        "X-Project-Id": credentials["project_id"],
        "Cookie": "tb_session=" + credentials["session_token"]})
    read_back = conn.getresponse()
    body = read_back.read().decode()
    conn.close()
    assert read_back.status == 200, body
    assert json.loads(body)["ticket"]["title"] == "from the recorded url"


def test_the_csrf_allowlist_follows_the_bound_port_not_the_default(running):
    """The old allowlist hardcoded 4319, which is not where this board is."""
    httpd, credentials, _ = running
    recorded = json.loads(open(credentials["path"]).read())
    assert recorded["url"] in httpd.board.allowed_origins
    # Trusting a port this board is not on would accept a write from a
    # different server's dashboard.
    assert "http://127.0.0.1:4319" not in httpd.board.allowed_origins
    assert "http://localhost:4319" not in httpd.board.allowed_origins


def test_localhost_and_the_loopback_ip_are_one_origin(running):
    """Same origin to everyone except a string comparison."""
    httpd, credentials, _ = running
    port = httpd.server_address[1]
    assert httpd.board.allowed_origins == {
        "http://127.0.0.1:%d" % port, "http://localhost:%d" % port}


def test_an_origin_from_another_port_is_still_refused(running):
    """Deriving the allowlist must not have widened it."""
    httpd, credentials, _ = running
    recorded = json.loads(open(credentials["path"]).read())
    conn = _dial(recorded["url"])
    body = json.dumps({"request_id": str(uuid.uuid4()), "title": "x",
                       "outcome": "y", "acceptance": [{"text": "z"}]})
    conn.request("POST", "/tickets", body=body, headers={
        "Content-Type": "application/json",
        "Content-Length": str(len(body)),
        "X-Project-Id": credentials["project_id"],
        "Cookie": "tb_session=" + credentials["session_token"],
        "X-CSRF-Token": credentials["csrf_token"],
        "Origin": "http://127.0.0.1:%d" % (httpd.server_address[1] + 1)})
    response = conn.getresponse()
    payload = response.read().decode()
    conn.close()
    assert response.status == 403, payload
    assert "not allowed" in payload


def test_enrollment_advertises_an_address_an_agent_can_dial(running):
    """The third consequence: `install_command` is built from `base_url` too.

    An agent handed `--server http://127.0.0.1:0` cannot connect at all, and
    the failure surfaces later and elsewhere than its cause.
    """
    httpd, credentials, _ = running
    recorded = json.loads(open(credentials["path"]).read())
    conn = _dial(recorded["url"])
    body = json.dumps({"request_id": str(uuid.uuid4()), "agent_name": "a1",
                       "role": "backend"})
    conn.request("POST", "/enrollments", body=body, headers={
        "Content-Type": "application/json",
        "Content-Length": str(len(body)),
        "X-Project-Id": credentials["project_id"],
        "Cookie": "tb_session=" + credentials["session_token"],
        "X-CSRF-Token": credentials["csrf_token"],
        "Origin": recorded["url"]})
    response = conn.getresponse()
    payload = response.read().decode()
    conn.close()
    assert response.status == 201, payload
    assert "--server " + recorded["url"] in json.loads(payload)["install_command"]
    assert ":0" not in json.loads(payload)["install_command"]


@pytest.mark.parametrize("bound,expected", [
    (("127.0.0.1", 4319), "http://127.0.0.1:4319"),
    (("127.0.0.1", 55021), "http://127.0.0.1:55021"),
    # A wildcard bind is a bind target, not an address: nothing can dial it.
    (("0.0.0.0", 8080), "http://127.0.0.1:8080"),
    (("", 8080), "http://127.0.0.1:8080"),
    (("::", 8080), "http://127.0.0.1:8080"),
    # An IPv6 literal has to be bracketed or the port reads as part of it.
    (("::1", 8080, 0, 0), "http://[::1]:8080"),
])
def test_advertised_url_is_always_dialable(bound, expected):
    assert advertised_url(bound) == expected
