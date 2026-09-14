"""T-927: one physical board shares cache, locks, remove, and wake across aliases."""

import ast
import fcntl
import hashlib
import importlib.util
import json
import os
import shutil
import socket
import tempfile
import threading
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
ADAPTERS = ROOT / "session_adapters.py"
CLI = ROOT / "src/ticket_board/cli.py"


def _adapters():
    spec = importlib.util.spec_from_file_location("session_adapters_t927", ADAPTERS)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _legacy_abspath_hash(board):
    return hashlib.sha256(os.path.abspath(board).encode("utf-8")).hexdigest()[:16]


def _symlink_alias(physical):
    alias = physical + "-alias"
    os.symlink(physical, alias)
    return alias


def _macos_tmp_aliases_supported():
    tmp, private = "/tmp", "/private/tmp"
    return os.path.isdir(private) and os.path.realpath(tmp) == os.path.realpath(private)


def _macos_tmp_pair():
    """(/private/tmp/name, /tmp/name) when those are the same volume."""
    physical = tempfile.mkdtemp(prefix="t927-", dir="/private/tmp")
    alias = os.path.join("/tmp", os.path.basename(physical))
    return physical, alias


@pytest.fixture
def cache_dir(tmp_path, monkeypatch):
    d = str(tmp_path / "cache")
    monkeypatch.setenv("TICKETS_CACHE_DIR", d)
    return d


@pytest.fixture
def physical_board(tmp_path):
    board = str(tmp_path / "board")
    os.makedirs(board)
    return board


def test_symlink_alias_shares_hash_and_endpoint(cache_dir, physical_board):
    sa = _adapters()
    alias = _symlink_alias(physical_board)
    try:
        assert sa.board_identity(physical_board) == sa.board_identity(alias)
        assert sa.board_hash(physical_board) == sa.board_hash(alias)
        assert sa.board_hash(physical_board) != _legacy_abspath_hash(alias)
        sa.write_endpoint(physical_board, "alice", {
            "seat": "alice", "provider": "claude", "socket": "/tmp/alice.sock",
            "pid": os.getpid(), "at": "now"})
        via_alias = sa.read_endpoint(alias, "alice")
        assert via_alias is not None
        assert via_alias["socket"] == "/tmp/alice.sock"
        assert sa.endpoint_path(physical_board, "alice") == sa.endpoint_path(alias, "alice")
    finally:
        os.unlink(alias)


def test_alias_locks_contend_different_boards_isolated(cache_dir, physical_board, tmp_path):
    sa = _adapters()
    alias = _symlink_alias(physical_board)
    other = str(tmp_path / "other-board")
    os.makedirs(other)
    try:
        fd_a = sa.acquire_seat_lock(physical_board, "alice")
        path = sa._seat_lock_path(alias, "alice")
        fd_b = os.open(path, os.O_RDWR)
        with pytest.raises(BlockingIOError):
            fcntl.flock(fd_b, fcntl.LOCK_EX | fcntl.LOCK_NB)
        os.close(fd_b)
        sa.release_seat_lock(fd_a)

        fd_other = sa.acquire_seat_lock(other, "alice")
        fd_phys = sa.acquire_seat_lock(physical_board, "alice")
        assert os.fstat(fd_other).st_ino != os.fstat(fd_phys).st_ino
        sa.release_seat_lock(fd_other)
        sa.release_seat_lock(fd_phys)

        sa.write_endpoint(physical_board, "alice", {"seat": "alice", "socket": "a"})
        sa.write_endpoint(other, "alice", {"seat": "alice", "socket": "b"})
        assert sa.read_endpoint(physical_board, "alice")["socket"] == "a"
        assert sa.read_endpoint(other, "alice")["socket"] == "b"
        assert sa.read_endpoint(alias, "alice")["socket"] == "a"
    finally:
        os.unlink(alias)


def test_alias_remove_and_wake_agree(cache_dir, physical_board):
    sa = _adapters()
    alias = _symlink_alias(physical_board)
    sock_dir = tempfile.mkdtemp(prefix="t927-sock-", dir="/tmp")
    sock_path = os.path.join(sock_dir, "alice.sock")
    inbox = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    inbox.bind(sock_path)
    inbox.listen(1)
    inbox.settimeout(5)
    received = []

    def _accept():
        try:
            conn, _ = inbox.accept()
        except OSError:
            return
        with conn:
            conn.settimeout(2)
            chunks = []
            while True:
                data = conn.recv(4096)
                if not data:
                    break
                chunks.append(data)
            received.append(b"".join(chunks).decode("utf-8", "replace"))

    thread = threading.Thread(target=_accept, daemon=True)
    thread.start()
    try:
        sa.write_endpoint(physical_board, "alice", {
            "seat": "alice", "provider": "claude", "mode": "native",
            "socket": sock_path, "token": "", "pid": os.getpid(), "at": "now",
            "lease_id": "lease-1", "fence": 1})
        label = sa.wake_seat(alias, "alice", "ping-via-alias", harness="claude")
        assert "no live endpoint" not in label
        thread.join(timeout=5)
        assert received and "ping-via-alias" in received[0]
        sa.remove_endpoint(alias, "alice")
        assert sa.read_endpoint(physical_board, "alice") is None
        assert sa.read_endpoint(alias, "alice") is None
    finally:
        inbox.close()
        shutil.rmtree(sock_dir, ignore_errors=True)
        os.unlink(alias)


def test_legacy_abspath_cache_is_not_adopted(cache_dir, physical_board):
    sa = _adapters()
    alias = _symlink_alias(physical_board)
    try:
        assert sa.legacy_abspath_board_hash(alias) != sa.board_hash(alias)
        legacy = sa.legacy_endpoint_dir(alias)
        os.makedirs(legacy)
        planted = os.path.join(legacy, "alice.json")
        with open(planted, "w") as f:
            json.dump({
                "seat": "alice", "provider": "claude",
                "socket": "/secret/parent.sock", "token": "stolen",
                "pid": os.getpid(), "at": "old"}, f)
        assert sa.read_endpoint(alias, "alice") is None
        assert sa.read_endpoint(physical_board, "alice") is None
        leftovers = sa.legacy_endpoint_dirs(alias)
        assert any(os.path.isdir(p) and os.path.samefile(p, legacy) for p in leftovers)
        sa.write_endpoint(physical_board, "alice", {
            "seat": "alice", "provider": "claude", "socket": "/new/child.sock",
            "at": "new"})
        adopted = sa.read_endpoint(alias, "alice")
        assert adopted["socket"] == "/new/child.sock"
        with open(planted) as f:
            leftover = json.load(f)
        assert leftover["socket"] == "/secret/parent.sock"
        removed = sa.invalidate_legacy_board_caches(alias)
        assert removed >= 1
        assert not os.path.exists(planted)
        assert sa.read_endpoint(alias, "alice")["socket"] == "/new/child.sock"
    finally:
        os.unlink(alias)


@pytest.mark.skipif(not _macos_tmp_aliases_supported(), reason="/tmp is not an alias of /private/tmp")
def test_tmp_and_private_tmp_share_namespace(cache_dir):
    physical, alias = _macos_tmp_pair()
    sa = _adapters()
    try:
        assert os.path.abspath(physical) != os.path.abspath(alias)
        assert sa.board_hash(physical) == sa.board_hash(alias)
        assert _legacy_abspath_hash(physical) != _legacy_abspath_hash(alias)
        sa.write_endpoint(physical, "bob", {"seat": "bob", "socket": "via-private"})
        assert sa.read_endpoint(alias, "bob")["socket"] == "via-private"
        fd_a = sa.acquire_seat_lock(physical, "bob")
        fd_b = os.open(sa._seat_lock_path(alias, "bob"), os.O_RDWR)
        with pytest.raises(BlockingIOError):
            fcntl.flock(fd_b, fcntl.LOCK_EX | fcntl.LOCK_NB)
        os.close(fd_b)
        sa.release_seat_lock(fd_a)
        planted = os.path.join(sa.legacy_endpoint_dir(alias), "stale.json")
        os.makedirs(os.path.dirname(planted), exist_ok=True)
        with open(planted, "w") as f:
            json.dump({"socket": "old-tmp-transport"}, f)
        assert sa.read_endpoint(physical, "bob")["socket"] == "via-private"
        assert sa.invalidate_legacy_board_caches(physical) >= 1
        assert not os.path.exists(planted)
        sa.remove_endpoint(alias, "bob")
        assert sa.read_endpoint(physical, "bob") is None
    finally:
        shutil.rmtree(physical, ignore_errors=True)


def test_source_and_package_share_one_board_hash():
    sa = _adapters()
    src = ADAPTERS.read_text()
    tree = ast.parse(src)
    names = {node.name for node in tree.body if isinstance(node, ast.FunctionDef)}
    assert "board_identity" in names
    assert "legacy_abspath_board_hash" in names
    cli_src = CLI.read_text()
    assert "def board_hash(" not in cli_src
    assert "sessions" not in cli_src or "from session_adapters import" in cli_src
    staged = importlib.util.spec_from_file_location(
        "session_adapters_t927_staged", ADAPTERS)
    staged_mod = importlib.util.module_from_spec(staged)
    staged.loader.exec_module(staged_mod)
    sample = "/tmp/t927-package-check"
    assert sa.board_hash(sample) == staged_mod.board_hash(sample)
    assert sa.board_hash(sample) == hashlib.sha256(
        os.path.realpath(os.path.abspath(sample)).encode("utf-8")).hexdigest()[:16]
