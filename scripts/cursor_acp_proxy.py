#!/usr/bin/env python3
"""Forward a unix JSON-RPC control sock to a long-lived `agent acp` stdio.

Operator-started, like `codex app-server daemon start`. tickets.py never
spawns this on poke — a missing sock stays supervised. Starting the proxy
is one persistent ACP session, not `agent -p --resume` per message.
"""
from __future__ import print_function

import os
import shutil
import socket
import subprocess
import sys
import threading


def sock_path():
    override = (os.environ.get("CURSOR_ACP_CONTROL_SOCK") or "").strip()
    if override:
        return override
    return os.path.join(os.path.expanduser("~"), ".cursor", "acp-control",
                         "acp-control.sock")


def main():
    agent = shutil.which("agent")
    if not agent:
        sys.exit("agent CLI not on PATH")
    path = sock_path()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    try:
        os.unlink(path)
    except OSError:
        pass
    srv = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    srv.bind(path)
    os.chmod(path, 0o600)
    srv.listen(8)
    proc = subprocess.Popen(
        [agent, "acp"],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=sys.stderr)
    lock = threading.Lock()
    print("listening on %s (pid %d)" % (path, proc.pid), file=sys.stderr)

    def pipe_stdout():
        while True:
            line = proc.stdout.readline()
            if not line:
                return
            # ACP server stdout is for the current RPC client; last conn wins.
            with lock:
                current = getattr(main, "_conn", None)
                if current is not None:
                    try:
                        current.sendall(line)
                    except OSError:
                        pass

    threading.Thread(target=pipe_stdout, daemon=True).start()
    try:
        while True:
            conn, _ = srv.accept()
            data = b""
            conn.settimeout(30)
            try:
                while b"\n" not in data:
                    chunk = conn.recv(4096)
                    if not chunk:
                        break
                    data += chunk
            except OSError:
                conn.close()
                continue
            if not data:
                conn.close()
                continue
            with lock:
                main._conn = conn
                try:
                    proc.stdin.write(data if data.endswith(b"\n") else data + b"\n")
                    proc.stdin.flush()
                except OSError:
                    conn.close()
                    break
    finally:
        srv.close()
        try:
            os.unlink(path)
        except OSError:
            pass
        proc.terminate()


if __name__ == "__main__":
    main()
