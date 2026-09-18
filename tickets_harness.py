"""Harness catalog, probe, auth, and usage (T-1050).

Sibling of the tickets.py facade. Public entry points stay imported on
tickets.py; this file owns the harness topic so parallel seats do not
edit the same god-file function.
"""
from __future__ import annotations

import json
import os
import re
import shlex
import subprocess
import sys

# Defaults for signatures evaluated at import, before attach(). attach()
# overwrites the names used in function bodies from the facade.
HARNESS_PROBE_PROMPT = "reply OK"
HARNESS_CHECK_TIMEOUT = 60
HARNESS_USAGE_TIMEOUT = 8
HARNESS_USAGE_SPAWN_TOKENS = frozenset({
    "-p", "--print", "--prompt", "--prompt-interactive", "-i", "exec", "--yolo",
})

_BOUND = False


def attach(mod_or_globals):
    """Bind facade names this topic calls. Runtime only; no import-time work.

    Accepts a module or that module's globals() so spec_from_file_location
    loaders that never enter sys.modules still work.
    """
    global _BOUND
    src = mod_or_globals if isinstance(mod_or_globals, dict) else vars(mod_or_globals)
    g = globals()
    for name in NEED:
        if name in src:
            g[name] = src[name]
    _BOUND = True


NEED = [
    "HARNESS_CHECK_TIMEOUT", "HARNESS_PROBE_PROMPT", "HARNESS_USAGE_SPAWN_TOKENS",
    "HARNESS_USAGE_TIMEOUT", "INTEGRATION_CATALOG", "SUPPORTED_QUOTA",
    "_AUTH_NETWORK_STRINGS", "_USAGE_REMAINING_KEYS", "_USAGE_RESET_KEYS",
    "_agent_rec", "_agent_set", "_auth_execution_context", "_auth_probe_env",
    "_classify_auth_output", "_cos_label", "_expand_harness_cmd",
    "_harness_auth_spec", "_harness_template_error", "_json_candidates",
    "_print_role_discovery", "_profile_kind_for", "_refresh_auth_check",
    "_render_prompt_file", "_safe", "_sounding", "_supervisor_launch_env",
    "_time", "_which_on_path", "_worker_cmd", "board_is_living",
    "classify_catalog_usage", "detect_auth_error", "fmt_hours", "harness_of",
    "hours_since", "load_agents", "load_workforce", "now",
    "parse_cursor_admin", "parse_provider_quota", "print_integration_catalog",
    "print_recorded_usage", "reclaim_stale_watch_lock", "retarget_stale_local_codex",
    "whoami", "_provider_usage", "_maybe_refresh_provider_usage",
]


def harness_auth_probe(board, owner, harness="", timeout=15):
    """Cheap credential preflight; never starts a model or consumes a turn."""
    import subprocess

    resolved, _ = harness_of(board, owner, harness, "")
    spec = _harness_auth_spec(board, owner, resolved)
    ctx = _auth_execution_context(board, owner, (spec or (["tickets"], []))[0])
    if not spec:
        rec = {"state": "unsupported", "harness": resolved, "at": now(),
               "detail": "auth preflight is not defined for this harness", "login_cmd": "",
               "execution_context": ctx, "profile_kind": _profile_kind_for(resolved)}
        return rec
    status_cmd, login_cmd = spec
    ctx = _auth_execution_context(board, owner, status_cmd)
    env = _auth_probe_env()
    try:
        r = subprocess.run(status_cmd, capture_output=True, text=True, timeout=timeout,
                           stdin=subprocess.DEVNULL, env=env)
        output = ((r.stdout or "") + (r.stderr or "")).strip()
        state = _classify_auth_output(r.returncode, output)
        rc = r.returncode
    except FileNotFoundError:
        state, rc, output = "unavailable", 127, "%s is not installed" % status_cmd[0]
    except subprocess.TimeoutExpired:
        output = "authentication status timed out"
        state, rc = ("network" if any(s in output.lower() for s in _AUTH_NETWORK_STRINGS)
                    else "unavailable"), 124
    line = (output.splitlines()[0][:240] if output else "status returned no identity")
    identity = line if state == "ready" and output else ""
    return {
        "state": state, "harness": resolved, "at": now(), "exit": rc,
        "detail": line, "identity": identity, "identity_label": identity,
        "status_cmd": " ".join(status_cmd), "login_cmd": " ".join(login_cmd),
        "execution_context": ctx, "profile_kind": _profile_kind_for(resolved),
    }

def _print_auth_result(owner, result):
    labels = {
        "ready": "Ready",
        "login_required": "Login required",
        "expired": "Credential expired",
        "quota": "Usage quota reached",
        "network": "Network error",
        "unavailable": "Harness unavailable",
        "unsupported": "Auth check unsupported",
    }
    print("%s: %s" % (owner, labels.get(result.get("state"), result.get("state", "unknown"))))
    if result.get("identity_label") or result.get("identity"):
        print("  identity: %s" % (result.get("identity_label") or result.get("identity")))
    elif result.get("detail"):
        print("  detail:   %s" % result["detail"])
    if result.get("authoritative") is False:
        print("  context:  non-authoritative observation (enrolled runner unchanged)")
    ctx = result.get("execution_context") or {}
    if ctx.get("hostname"):
        print("  context:  %s@%s %s %s" % (
            ctx.get("username") or "?", ctx.get("hostname"),
            ctx.get("runner_kind") or "?", ctx.get("origin_url") or ""))
    if result.get("credential_profile_ref"):
        print("  profile:  %s" % result["credential_profile_ref"])
    path = (result.get("pause") or {}).get("operator_path")
    if result.get("state") in ("login_required", "expired") and result.get("login_cmd"):
        print("  recover:  %s" % result["login_cmd"])
        print("  verify:   atm harness auth %s" % owner)
    elif path and path != "ready":
        print("  recover:  %s" % (result.get("login_cmd") or path))

def cmd_harness_auth(a, board):
    """Show auth, optionally run the interactive login, and verify afterward."""
    import subprocess

    owner = a.name or whoami()
    if owner.startswith("agent-"):
        sys.exit("harness auth needs an agent name: atm harness auth <name>")
    if a.recover_stale:
        recovered = reclaim_stale_watch_lock(board, owner)
        print("watcher: %s" % recovered["detail"])
        if recovered["state"] == "live":
            sys.exit(2)
    result = _refresh_auth_check(board, owner, a.harness)
    _print_auth_result(owner, result)
    if a.login:
        if result.get("state") == "unsupported":
            sys.exit("interactive login is not supported for %s" % result.get("harness"))
        login_line = result.get("login_cmd") or ""
        spec = _harness_auth_spec(board, owner, result.get("harness") or a.harness)
        login_cmd = shlex.split(login_line) if login_line else (spec[1] if spec else [])
        if not login_cmd:
            sys.exit("no safe recovery command for %s" % owner)
        env = _auth_probe_env()
        rc = subprocess.call(login_cmd, env=env)
        if rc:
            sys.exit(rc)
        result = _refresh_auth_check(board, owner, a.harness)
        print("verification:")
        _print_auth_result(owner, result)
    if result.get("state") != "ready":
        sys.exit(1)

def _harness_check_label(check):
    """One column's worth of the last `harness check`, for `spawn --list`."""
    if not check:
        return "-"
    age = hours_since(check.get("at", ""))
    ms = check.get("latency_ms")
    return "%s %s" % ("ok" if check.get("ok") else "FAIL",
                      ("%.1fs" % (ms / 1000.0)) if isinstance(ms, (int, float)) else
                      ((fmt_hours(age) + " ago") if age is not None else ""))

def harness_probe(board, owner, harness="", cmd="", model="", cwd="", timeout=HARNESS_CHECK_TIMEOUT):
    """Run the agent's harness on a trivial prompt and report what happened.

    Returns the record stored on the agent: harness, cmd, ok, exit, latency_ms,
    output (head), at. The probe runs the REAL command shape -- same binary,
    same flags, same template -- with only the prompt swapped, because the
    failure this is for ("that harness is not installed / not logged in / the
    template is wrong") lives in the command, not in the model's answer.

    `ok` is exit status only. Whether the model actually said OK is reported in
    `replied`, and deliberately does not gate `ok`: a harness that answers a
    trivial prompt with a preamble is working, and a check that called that a
    failure would take a live agent out of the fleet.
    """
    import subprocess
    import time as _time

    harness, cmd_template = harness_of(board, owner, harness, cmd)
    cwd = cwd or (_agent_rec(board, owner) or {}).get("worktree") or os.path.dirname(board)
    if not os.path.isdir(cwd):
        cwd = os.path.dirname(board)
    prompt_file, cleanup = "", None
    if cmd_template:
        template_err = _harness_template_error(cmd_template)
        if template_err:
            return {"harness": harness, "cmd": cmd_template, "ok": False, "exit": 1,
                    "timed_out": False, "latency_ms": 0, "replied": False,
                    "output": template_err, "at": now()}
        if "{prompt_file}" in cmd_template:
            prompt_file, cleanup = _render_prompt_file(board, owner, text=HARNESS_PROBE_PROMPT)
        run_cmd = _expand_harness_cmd(cmd_template, agent=owner, cwd=cwd, prompt_file=prompt_file)
    else:
        run_cmd = _worker_cmd(board, owner, model, "bypassPermissions", harness,
                              prompt_expr=shlex.quote(HARNESS_PROBE_PROMPT))
    # TICKET_SEAT alongside the legacy TICKET_AGENT: this launch is a
    # deliberate assignment of the seat to the process it starts, not
    # ambient inheritance, so seat_confirmed() must trust it even though
    # the launched process has a brand-new session id with no record of
    # its own yet -- otherwise a worker would be hidden from the very
    # mail it was launched to handle.
    env = _supervisor_launch_env(board, owner)
    started = _time.time()
    out, rc, timed_out = "", None, False
    try:
        r = subprocess.run(run_cmd, shell=True, cwd=cwd, env=env, stdin=subprocess.DEVNULL,
                           capture_output=True, text=True, timeout=timeout)
        rc, out = r.returncode, ((r.stdout or "") + (r.stderr or ""))
    except subprocess.TimeoutExpired as e:
        timed_out, rc = True, 124
        out = "".join(x.decode("utf-8", "replace") if isinstance(x, bytes) else (x or "")
                      for x in (e.stdout, e.stderr))
    except OSError as e:
        rc, out = 127, str(e)
    finally:
        if cleanup:
            cleanup()
    latency_ms = int((_time.time() - started) * 1000)
    out = out.strip()
    return {"harness": harness, "cmd": run_cmd, "ok": rc == 0, "exit": rc, "timed_out": timed_out,
            "latency_ms": latency_ms, "replied": "ok" in out.lower()[:400],
            "output": out[:400], "at": now()}

def usage_probe_is_spawn(argv):
    """True if argv would start a model session. Usage probes must stay False."""
    for t in (str(x).lower() for x in (argv or ())):
        if t in HARNESS_USAGE_SPAWN_TOKENS or t.split("=", 1)[0] in HARNESS_USAGE_SPAWN_TOKENS:
            return True
    return False

def catalog_usage_argv(spec_or_id):
    """Non-spawning usage argv for a catalog row (empty = no probe)."""
    spec = spec_or_id
    if isinstance(spec_or_id, str):
        spec = next((s for s in INTEGRATION_CATALOG if s["id"] == spec_or_id), {})
    args = tuple(spec.get("usage_args") or ())
    if usage_probe_is_spawn(args):
        return ()
    return args

def _usage_key_norm(key):
    return str(key or "").replace("-", "").replace("_", "").lower()

def _usage_field_str(value):
    if value is None or value is False:
        return None
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        if isinstance(value, float) and value != value:  # NaN
            return None
        if float(value) == int(value):
            return str(int(value))
        return str(value)
    text = str(value).strip()
    if not text or text.lower() in ("none", "null", "missing", "unknown", "n/a", "-"):
        return None
    return text

def _usage_fields_from_mapping(obj, remaining=None, reset=None):
    """Pull remaining/reset from a dict. Never invent; first hit wins."""
    if not isinstance(obj, dict):
        return remaining, reset
    for key, val in obj.items():
        norm = _usage_key_norm(key)
        if remaining is None and norm in _USAGE_REMAINING_KEYS:
            remaining = _usage_field_str(val)
        elif reset is None and norm in _USAGE_RESET_KEYS:
            reset = _usage_field_str(val)
    prefer = []
    for key, val in obj.items():
        if _usage_key_norm(key) in ("usage", "quota", "ratelimit", "ratelimits", "limits"):
            prefer.append(val)
    for val in prefer:
        remaining, reset = _usage_fields_from_mapping(val, remaining, reset)
        if remaining is not None and reset is not None:
            return remaining, reset
    for val in obj.values():
        if isinstance(val, dict):
            remaining, reset = _usage_fields_from_mapping(val, remaining, reset)
        elif isinstance(val, list):
            for item in val:
                remaining, reset = _usage_fields_from_mapping(item, remaining, reset)
        if remaining is not None and reset is not None:
            return remaining, reset
    return remaining, reset

def parse_usage_remaining_reset(text):
    """Extract remaining and reset when a harness reports them. Absent stays None."""
    remaining, reset = None, None
    for blob in _json_candidates(text):
        try:
            rec = json.loads(blob)
        except ValueError:
            continue
        if isinstance(rec, dict):
            remaining, reset = _usage_fields_from_mapping(rec, remaining, reset)
            if remaining is not None and reset is not None:
                return remaining, reset
    raw = text or ""
    if remaining is None:
        m = re.search(r"remaining[:\s]+([^\n,;]+)", raw, re.I)
        if m:
            remaining = _usage_field_str(m.group(1))
    if reset is None:
        m = re.search(r"resets?\s+(?:at\s+)?([^\n]+)", raw, re.I)
        if m:
            reset = _usage_field_str(m.group(1).rstrip("."))
    return remaining, reset

def _run_usage_probe(argv, env, timeout=HARNESS_USAGE_TIMEOUT):
    """Run a status/about probe. Never a model prompt. Returns (output, detail)."""
    import subprocess

    if not argv or usage_probe_is_spawn(argv):
        return "", "spawn probe refused" if argv else "no usage probe"
    try:
        r = subprocess.run(list(argv), capture_output=True, text=True, timeout=timeout,
                           stdin=subprocess.DEVNULL, env=env)
        out = ((r.stdout or "") + (r.stderr or "")).strip()
        return out, ""
    except FileNotFoundError:
        return "", "probe binary missing"
    except subprocess.TimeoutExpired:
        return "", "usage probe timed out"
    except OSError as e:
        return "", str(e)

def probe_catalog_usage(row, env=None, timeout=HARNESS_USAGE_TIMEOUT):
    """Usage check for one catalog row. Missing remaining/reset is unknown, not FAIL."""
    try:
        from quota_adapters import (
            SUPPORTED_QUOTA, classify_catalog_usage, detect_auth_error,
            parse_cursor_admin, parse_provider_quota)
    except ImportError:
        from ticket_board.quota_adapters import (
            SUPPORTED_QUOTA, classify_catalog_usage, detect_auth_error,
            parse_cursor_admin, parse_provider_quota)
    spec = next((s for s in INTEGRATION_CATALOG if s["id"] == row.get("id")), {})
    args = catalog_usage_argv(spec)
    reason = ""
    output = ""
    remaining, reset = None, None
    probe_env = dict(env if env is not None else os.environ)
    cursor_admin = bool(probe_env.get("ATMAN_CURSOR_ADMIN_USAGE"))
    if not row.get("on_disk") or not row.get("path"):
        reason = "missing binary"
    elif not args:
        reason = "no usage probe"
    else:
        argv = [row["path"]] + list(args)
        output, err = _run_usage_probe(argv, probe_env, timeout=timeout)
        reason = err or detect_auth_error(output)
        hid = row.get("id")
        if hid == "cursor" and cursor_admin:
            remaining, reset = parse_cursor_admin(output)
        else:
            remaining, reset = parse_provider_quota(hid, output)
        if remaining is None and reset is None and hid in SUPPORTED_QUOTA:
            remaining, reset = parse_usage_remaining_reset(output)
        if remaining is None and reset is None and not reason:
            reason = "missing remaining and reset"
    usage, reason = classify_catalog_usage(
        row.get("id"), remaining, reset, reason=reason,
        on_disk=bool(row.get("on_disk") and row.get("path")),
        cursor_admin=cursor_admin)
    return {
        "usage": usage,
        "remaining": remaining,
        "reset": reset,
        "usage_reason": reason,
        "usage_ok": usage == "ok",
        "usage_status": usage,
    }

def attach_catalog_usage(rows, env=None, timeout=HARNESS_USAGE_TIMEOUT):
    """Fill usage/remaining/reset on every catalog row. Does not spawn."""
    for row in rows:
        row.update(probe_catalog_usage(row, env=env, timeout=timeout))
    return rows

def _fmt_usage_field(value):
    return value if value else "(missing)"

def probe_integration_catalog(home=None, search_path=None):
    """Every catalog row, including missing binaries. Does not spawn."""
    home = home if home is not None else os.path.expanduser("~")
    search_path = search_path if search_path is not None else os.environ.get("PATH", "")
    local_bin = os.path.join(home, ".local", "bin")
    if local_bin not in search_path.split(os.pathsep):
        search_path = local_bin + os.pathsep + search_path
    note = retarget_stale_local_codex(home)
    rows = []
    for spec in INTEGRATION_CATALOG:
        found = []
        for b in spec["binaries"]:
            loc = _which_on_path(b, search_path)
            if loc:
                found.append((b, loc))
        rows.append({
            "id": spec["id"],
            "name": spec["name"],
            "binaries": spec["binaries"],
            "found": found,
            "on_disk": bool(found),
            "path": found[0][1] if found else "",
            "if_yes": spec["if_yes"],
            "policy": spec["policy"],
            "usage_status": "",
        })
    return rows, note

def _print_catalog_usage_line(row):
    print("         usage %-4s remaining=%s  reset=%s%s" % (
        row.get("usage") or "unknown",
        _fmt_usage_field(row.get("remaining")),
        _fmt_usage_field(row.get("reset")),
        ("  (%s)" % row["usage_reason"]) if row.get("usage_reason") and row.get("usage") != "ok" else ""))

def cmd_harness_usage(a, board):
    """Auto-check remaining/reset for every catalog row. Do not spawn."""
    home = os.path.expanduser("~")
    rows, note = probe_integration_catalog(home=home)
    attach_catalog_usage(rows)
    print("USAGE (probe only — do not spawn)")
    if note:
        print("codex: %s" % note)
    print("%-8s %-6s %-18s %s" % ("id", "usage", "remaining", "reset"))
    for r in rows:
        print("%-8s %-6s %-18s %s" % (
            r["id"], r.get("usage") or "unknown",
            (_fmt_usage_field(r.get("remaining")))[:18],
            _fmt_usage_field(r.get("reset"))))
        if r.get("usage") != "ok" and r.get("usage_reason"):
            print("         %s" % r["usage_reason"])
        fail = _sounding().catalog_dispatch_fail(r)
        if fail:
            print("         FAIL %s" % r["policy"])
    print("")
    print("Unsupported or missing remaining/reset is unknown, not exhausted. Codex stays cataloged.")
    print("Spawn only harnesses the operator chooses.")
    _maybe_refresh_provider_usage(board)
    for line in _provider_usage().format_ledger_lines(board):
        print(line)

def cmd_harness_available(a, board):
    """Probe the integration catalog and auto-check usage. Print every row. Do not spawn."""
    home = os.path.expanduser("~")
    rows, note = probe_integration_catalog(home=home)
    attach_catalog_usage(rows)
    print_integration_catalog(rows, note)
    print("")
    _maybe_refresh_provider_usage(board)
    print_recorded_usage(board)
    print("")
    print("USAGE: unsupported or missing remaining/reset is unknown, not exhausted. Do not spawn a FAIL or exhausted seat.")
    print("Ask: Which of these do you want to use?")
    _print_role_discovery(rows)
    if board_is_living(board):
        print("This is a living board. Do not invent a new team.")
        print("Announce the Atman role as atman-<seat>. CoS (%s) staffs." % _cos_label(board))
        print("CEO does not claim worker tickets.")
    else:
        print("Then ask the board/team name, then:")
        print('  atm msg --to everyone "<name> is onboarding. Integrating: <list>. Objective and tasks next. @everyone"')
        print("Then ask for the objective and tasks. Do not spawn until they answer.")
    print("Codex stays in the catalog with unknown usage. Spawn only the harnesses the operator chooses.")

def cmd_harness(a, board):
    """`atm harness check <name>` / `atm harness list` / `available` / `usage`.

    check: prove the agent's harness actually runs before a watcher spends a
    poll interval discovering it does not. The result is written to the agent
    record so `spawn --list` and the master can see who is really reachable.
    available: probe command -v for every catalog integration (missing is a row)
    and auto-check usage (unsupported remaining/reset is unknown). Does not spawn.
    usage: the usage table alone (same probes as available).
    """
    if a.harness_cmd == "available":
        return cmd_harness_available(a, board)
    if a.harness_cmd == "usage":
        return cmd_harness_usage(a, board)
    if a.harness_cmd == "auth":
        return cmd_harness_auth(a, board)
    if a.harness_cmd == "list":
        wf = load_workforce(board)
        names = sorted(set(list(wf) + [r["owner"] for r in load_agents(board)]))
        if not names:
            print("no agents registered yet -- atm join <name> --harness ...")
            return
        print("%-16s %-12s %-14s %s" % ("agent", "harness", "check", "cmd"))
        for n in names:
            e = wf.get(n, {}) or {}
            rec = _agent_rec(board, n) or {}
            print("%-16s %-12s %-14s %s" % (
                n[:16], (e.get("harness") or e.get("tool") or "claude")[:12],
                _harness_check_label(rec.get("harness_check")),
                e.get("cmd") or "(built-in)"))
        return
    owner = a.name or whoami()
    if owner.startswith("agent-"):
        sys.exit("harness check needs an agent name: atm harness check <name>")
    harness, cmd_template = harness_of(board, owner, a.harness, a.cmd_template)
    print("checking %s: harness=%s%s" % (owner, harness, (" cmd=%s" % cmd_template) if cmd_template else ""))
    res = harness_probe(board, owner, a.harness, a.cmd_template, a.model, a.cwd, a.timeout)
    _safe(lambda: _agent_set(board, owner, harness_check=res), None)
    print("  cmd:      %s" % res["cmd"])
    print("  exit:     %s%s" % (res["exit"], " (TIMEOUT after %ds)" % a.timeout if res["timed_out"] else ""))
    print("  latency:  %.1fs" % (res["latency_ms"] / 1000.0))
    print("  replied:  %s" % ("saw 'OK' in the output" if res["replied"] else "no 'OK' in the first 400 chars"))
    if res["output"]:
        print("  output:   %s" % res["output"].splitlines()[0][:160])
    print("%s: %s" % (owner, "harness OK" if res["ok"] else "HARNESS FAILED"))
    if not res["ok"]:
        sys.exit(1)
