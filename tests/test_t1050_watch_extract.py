"""T-1050: watch/spawn topic is a sibling; facade still exposes the same callables.

Proven red on cursor-onboard-t1050@2352598: tickets_watch.py does not exist,
so importing it fails. After the extract the facade aliases must point at
the sibling. Spawn must still exec the facade file, not the sibling.
"""
import importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def load_tickets():
    spec = importlib.util.spec_from_file_location("tickets_t1050_watch", ROOT / "tickets.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_watch_sibling_owns_cmd_watch_and_spawn():
    assert (ROOT / "tickets_watch.py").is_file()
    tool = load_tickets()
    assert tool.cmd_watch.__module__.endswith("tickets_watch")
    assert tool.cmd_spawn.__module__.endswith("tickets_watch")
    assert tool.watch_idle_reexec.__module__.endswith("tickets_watch")
    assert tool._watch_run_capped.__module__.endswith("tickets_watch")
    assert tool._spawn_stop.__module__.endswith("tickets_watch")
    assert callable(tool.cmd_watch)
    assert callable(tool.cmd_spawn)


def test_watch_extract_cmd_watch_still_over_300_lines():
    """The split signal is the function body, not the facade alias."""
    tool = load_tickets()
    src = Path(tool.cmd_watch.__code__.co_filename).read_text()
    start = src.index("def cmd_watch(")
    rest = src[start:]
    # next top-level def after cmd_watch
    nxt = rest.find("\ndef ", 1)
    body = rest if nxt < 0 else rest[:nxt]
    assert body.count("\n") + 1 > 300


def test_spawn_execs_facade_not_sibling():
    """cmd_spawn must launch tickets.py. Sibling __file__ would break watch."""
    tool = load_tickets()
    src = Path(tool.cmd_spawn.__code__.co_filename).read_text()
    assert "os.path.realpath(_FACADE_FILE)" in src
    assert 'os.path.realpath(__file__), "watch"' not in src
    # attach binds the facade path
    import tickets_watch
    assert Path(tickets_watch._FACADE_FILE).name == "tickets.py"
