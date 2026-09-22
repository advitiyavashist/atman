"""T-1050: harness topic is a sibling; facade still exposes the same callables.

Proven red on origin/main@d311de5: tickets_harness.py does not exist, so
importing it fails. After the extract the facade aliases must point at
the sibling, and ok must stay an exit-status fact (not replied).
"""
import importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def load_tickets():
    spec = importlib.util.spec_from_file_location("tickets_t1050", ROOT / "tickets.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_harness_sibling_owns_cmd_and_probe():
    assert (ROOT / "tickets_harness.py").is_file()
    tool = load_tickets()
    assert tool.cmd_harness.__module__.endswith("tickets_harness")
    assert tool.harness_probe.__module__.endswith("tickets_harness")
    assert tool.probe_integration_catalog.__module__.endswith("tickets_harness")
    assert tool.harness_auth_probe.__module__.endswith("tickets_harness")
    assert callable(tool.cmd_harness)


def test_harness_probe_ok_is_exit_status_not_replied():
    """ok follows exit==0; replied is a separate observation (T-harness check)."""
    tool = load_tickets()
    src = Path(tool.harness_probe.__code__.co_filename).read_text()
    assert '"ok": rc == 0' in src
    assert '"replied": "ok" in out.lower()[:400]' in src
    assert "replied" in src
    # A spawn-shaped usage argv is never a usage probe.
    assert tool.usage_probe_is_spawn(["claude", "-p", "hi"]) is True
    assert tool.usage_probe_is_spawn(["claude", "auth", "status"]) is False
    assert tool.usage_probe_is_spawn(["claude", "-p", "hi"]) != tool.usage_probe_is_spawn(
        ["claude", "auth", "status"])


def test_provider_usage_still_wired_on_usage_and_available():
    """Extraction must not drop provider-usage ledger refresh (CEO #229 finding)."""
    tool = load_tickets()
    usage_src = Path(tool.cmd_harness_usage.__code__.co_filename).read_text()
    avail_src = Path(tool.cmd_harness_available.__code__.co_filename).read_text()
    assert "_maybe_refresh_provider_usage(board)" in usage_src
    assert "_provider_usage().format_ledger_lines(board)" in usage_src
    assert "_maybe_refresh_provider_usage(board)" in avail_src
