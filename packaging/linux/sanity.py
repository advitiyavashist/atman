#!/usr/bin/env python3
"""Platform sanity for the pieces T-866 names: file locking, AF_UNIX sockets,
the default app port being bindable, and XDG cache resolution. Runs in the
clean-Ubuntu acceptance image and is cheap enough for any CI runner.

  python3 packaging/linux/sanity.py
"""
import fcntl
import os
import socket
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))


def check_flock():
    with tempfile.NamedTemporaryFile() as f:
        fcntl.flock(f.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        fcntl.flock(f.fileno(), fcntl.LOCK_UN)
    return "flock: LOCK_EX|LOCK_NB then LOCK_UN ok"


def check_af_unix():
    d = tempfile.mkdtemp(prefix="atman-sanity-")
    path = os.path.join(d, "wake.sock")
    srv = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    srv.bind(path)
    srv.listen(1)
    cli = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    cli.connect(path)
    cli.sendall(b'{"type":"auth","token":"x"}\n')
    conn, _ = srv.accept()
    got = conn.recv(64)
    for s in (conn, cli, srv):
        s.close()
    os.unlink(path)
    assert got.startswith(b'{"type":"auth"'), got
    return "AF_UNIX: bind/connect/send/recv round-trip ok (%s)" % path


def check_default_port():
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    try:
        s.bind(("127.0.0.1", 8765))
    except OSError as e:
        return "default app port 127.0.0.1:8765 is busy on this host (%s); atm ui --port picks another" % e
    finally:
        s.close()
    return "default app port 127.0.0.1:8765 bindable"


def check_xdg_cache():
    sys.path.insert(0, ROOT)
    import session_adapters
    os.environ.pop("TICKETS_CACHE_DIR", None)
    os.environ["XDG_CACHE_HOME"] = "/tmp/atman-sanity-xdg"
    got = session_adapters.cache_root()
    assert got == "/tmp/atman-sanity-xdg/atman", got
    os.environ.pop("XDG_CACHE_HOME")
    home = os.path.expanduser("~")
    got = session_adapters.cache_root()
    assert got == os.path.join(home, ".cache", "atman"), got
    return "cache root: $XDG_CACHE_HOME/atman, else ~/.cache/atman"


def main():
    for check in (check_flock, check_af_unix, check_default_port, check_xdg_cache):
        print("linux sanity: " + check())
    print("linux sanity: OK on %s" % sys.platform)
    return 0


if __name__ == "__main__":
    sys.exit(main())
