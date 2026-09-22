"""T-1443: documented install alone serves the React app at /app/.

A stranger who follows the README (fresh clone + ./install.sh --prefix) and
runs `atm ui` must get HTTP 200 with the app shell at /app/, not a bare 404.
Approach (a): install.sh builds ui/dist when Node/npm are present; when the
bundle is still missing, atm ui serves an HTML page that names the build
command.
"""
from __future__ import annotations

import http.client
import os
from pathlib import Path
import shutil
import socket
import subprocess
import sys
import time

import pytest

ROOT = Path(__file__).resolve().parents[1]
HAS_NPM = bool(shutil.which("npm") and shutil.which("node"))
UI_BUILD_CMD = "npm install && npm run build -w ui"


def _copy_checkout(dest: Path) -> Path:
    """Working-tree copy that looks like a fresh clone (no dist / node_modules)."""
    ignore = shutil.ignore_patterns(
        "node_modules", "ui/dist", "dist", ".git", "__pycache__", "*.pyc",
        ".venv", "venv", ".pytest_cache", ".mypy_cache", "*.egg-info",
        ".tickets", ".DS_Store",
    )
    shutil.copytree(ROOT, dest, ignore=ignore, symlinks=True)
    shutil.rmtree(dest / "ui" / "dist", ignore_errors=True)
    shutil.rmtree(dest / "node_modules", ignore_errors=True)
    shutil.rmtree(dest / "ui" / "node_modules", ignore_errors=True)
    return dest


def _free_port():
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _http_get(port, path):
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=30)
    conn.request("GET", path, headers={"Host": "127.0.0.1:%d" % port})
    r = conn.getresponse()
    body = r.read()
    headers = dict(r.getheaders())
    conn.close()
    return r.status, headers, body


def _assert_build_cmd_named(text: str):
    """HTML escapes && to &amp;&amp;; plain text keeps &&."""
    assert "npm install" in text and "npm run build -w ui" in text


def test_install_sh_prints_build_cmd_when_node_missing(tmp_path):
    """Without node/npm, install still links and prints the exact one-liner."""
    clone = _copy_checkout(tmp_path / "clone")
    prefix = tmp_path / "bin"
    env = dict(os.environ, HOME=str(tmp_path / "home"))
    env.pop("PREFIX", None)
    env.pop("ATMAN_SKIP_UI_BUILD", None)
    fake_bin = tmp_path / "empty-bin"
    fake_bin.mkdir()
    env["PATH"] = "%s:/usr/bin:/bin" % fake_bin

    result = subprocess.run(
        ["sh", str(clone / "install.sh"), "--prefix", str(prefix)],
        cwd=str(clone), capture_output=True, text=True, env=env,
    )
    assert result.returncode == 0, result.stderr + result.stdout
    assert (prefix / "atm").is_symlink()
    out = result.stdout + result.stderr
    assert UI_BUILD_CMD in out
    assert not (clone / "ui" / "dist" / "index.html").is_file()


def test_missing_dist_serves_html_build_page_not_json_404(tmp_path):
    """atm ui must never answer GET /app/ with a bare JSON 404."""
    import argparse
    import importlib.util
    import threading

    spec = importlib.util.spec_from_file_location("tickets_t1443", ROOT / "tickets.py")
    tk = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(tk)

    board = tmp_path / "board"
    board.mkdir()
    (board / "agents").mkdir()
    missing = tmp_path / "no-dist"
    port = _free_port()
    parent = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(600)"])
    a = argparse.Namespace(
        json=False, host="127.0.0.1", port=port, open=False,
        parent_pid=parent.pid, operator="", dev_origin=[], app_dir=str(missing),
    )
    thread = threading.Thread(target=tk.cmd_ui, args=(a, str(board)), daemon=True)
    thread.start()
    try:
        deadline = time.time() + 20
        while time.time() < deadline:
            try:
                st, _, _ = _http_get(port, "/")
                if st == 200:
                    break
            except OSError:
                time.sleep(0.05)
        else:
            raise RuntimeError("atm ui never came up")
        st, headers, body = _http_get(port, "/app/")
        text = body.decode()
        assert st == 200, text
        assert "text/html" in headers.get("Content-Type", "")
        _assert_build_cmd_named(text)
        assert not text.lstrip().startswith("{")
    finally:
        parent.kill()
        parent.wait()
        thread.join(5)


@pytest.mark.skipif(not HAS_NPM, reason="Node/npm required for the documented install path")
def test_fresh_clone_install_serves_app_shell_at_app(tmp_path):
    """Fresh checkout + install.sh --prefix + atm ui -> GET /app/ is the app shell."""
    clone = _copy_checkout(tmp_path / "atman")
    assert not (clone / "ui" / "dist" / "index.html").exists()

    prefix = tmp_path / "prefix-bin"
    env = dict(os.environ, HOME=str(tmp_path / "home"))
    env.pop("PREFIX", None)
    env.pop("ATMAN_SKIP_UI_BUILD", None)
    for k in list(env):
        if k.startswith(("TICKET", "ATMAN_", "TICKETS_")):
            env.pop(k, None)

    result = subprocess.run(
        ["sh", str(clone / "install.sh"), "--prefix", str(prefix)],
        cwd=str(clone), capture_output=True, text=True, env=env,
    )
    assert result.returncode == 0, result.stderr + result.stdout
    assert (clone / "ui" / "dist" / "index.html").is_file(), result.stdout + result.stderr
    assert (prefix / "atm").is_symlink()

    board = tmp_path / "proj" / ".tickets"
    board.mkdir(parents=True)
    (board / "agents").mkdir()
    port = _free_port()
    run_env = dict(env)
    run_env["PATH"] = "%s:%s" % (prefix, run_env.get("PATH", ""))
    run_env["TICKETS_DIR"] = str(board)
    proc = subprocess.Popen(
        [str(prefix / "atm"), "ui", "--host", "127.0.0.1", "--port", str(port)],
        cwd=str(tmp_path / "proj"), env=run_env,
        stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
    )
    try:
        deadline = time.time() + 30
        while time.time() < deadline:
            if proc.poll() is not None:
                last_err = (proc.stderr.read() or b"").decode()
                raise RuntimeError("atm ui exited early: " + last_err)
            try:
                st, headers, body = _http_get(port, "/app/")
                if st == 200:
                    text = body.decode()
                    assert "text/html" in headers.get("Content-Type", "")
                    assert 'id="root"' in text or "id='root'" in text
                    assert "atman" in text.lower()
                    assert "atman-token" in text
                    assert text.lstrip().startswith("<!") or "<html" in text.lower()
                    return
            except OSError:
                time.sleep(0.1)
        raise RuntimeError("GET /app/ never returned 200")
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait()
