"""Watch and spawn topic (T-1050).

Sibling of the tickets.py facade. Public entry points stay imported on
tickets.py; this file owns cmd_watch / cmd_spawn so parallel seats do not
edit the same god-file function.

_FACADE_FILE is the facade path: spawn must exec tickets.py (not this
sibling), and idle re-exec must identify the live release from that file.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
from datetime import datetime, timezone

_BOUND = False
_FACADE_FILE = __file__


def attach(mod_or_globals):
    """Bind facade names this topic calls. Runtime only; no import-time work.

    Accepts a module or that module's globals() so spec_from_file_location
    loaders that never enter sys.modules still work.
    """
    global _BOUND, _FACADE_FILE
    src = mod_or_globals if isinstance(mod_or_globals, dict) else vars(mod_or_globals)
    g = globals()
    for name in NEED:
        if name in src:
            g[name] = src[name]
    if src.get("__file__"):
        _FACADE_FILE = src["__file__"]
        g["_FACADE_FILE"] = _FACADE_FILE
    _BOUND = True


NEED = [
    "HARNESS_PLACEHOLDERS", "LOCAL_DISPATCH_MAX_ATTEMPTS", "RUN_HEARTBEAT_SECS",
    "STOP_CONDITION", "WAKE_KEYS", "WATCH_LOG_MAX_BYTES", "WATCH_MIN_INTERVAL",
    "WATCH_STOP_SLICE", "_active_seat_limit", "_adapter_provider", "_agent_holds_ticket",
    "_agent_rec", "_agent_set", "_agent_update", "_auth_gates_spawn", "_classify_auth_output",
    "_cursor_hook_agent", "_drop_unowned_agent_ticket", "_expand_harness_cmd",
    "_expected_origin_for", "_finalize_active_watch_run", "_find_checkout_for_origin",
    "_format_live_watchers", "_git_main_worktree", "_git_remote_origin", "_git_root_from",
    "_harness_auth_spec", "_harness_check_label", "_harness_of_cmd", "_identity_reuse_conflict",
    "_identity_reuse_error", "_inherit_settings", "_init_cwd_worktree_root", "_iso_span_secs",
    "_join_namespace", "_live_watch_pids", "_local_adapter_failure", "_mark_run_interrupted",
    "_master_log", "_origin_key", "_pin_spawned_worker_hooks", "_preflight", "_preflight_seat",
    "_print_auth_result", "_read_run_slice", "_reclaim_unrelated_watch_pidfile",
    "_record_watch_stall", "_refresh_auth_check", "_refuse_limited_seat", "_release_commit",
    "_remote_epoch", "_remote_trigger_key", "_render_prompt_file", "_replace_seat_watchers",
    "_repo_spec_is_path", "_resolve_watch_stall", "_run_beat", "_run_begin", "_run_end",
    "_run_end_limit_outcome", "_run_had_bound_write", "_run_usage", "_safe", "_seat_live_watchers",
    "_session_adapters", "_shared_watch_table", "_silent", "_spawn_base_ref", "_spawn_verify_started_watcher",
    "_split_harness", "_stall_watch", "_stop_file", "_store_auth_check", "_strip_identity_bound_state",
    "_supervisor_launch_env", "_validated_owned_watch_pid", "_watch_bind_ticket",
    "_watch_lock", "_watch_note_limit_from_log", "_watch_poke_file", "_watch_poll_wait",
    "_watch_table_available", "_watch_trigger_fingerprint", "_watcher_run_active",
    "_worker_cmd", "actionable", "agents_dir", "checkin", "cmd_brief", "cmd_join",
    "current_master", "fmt_hours", "harness_of", "hours_since", "lifecycle_of",
    "load_agents", "load_workforce", "master_state_path", "now", "pending_work",
    "post_message", "resolve_launch_policy", "save_workforce", "spawn_watch_max_runs",
    "traj_event", "wake_mode_of", "wake_reason_of", "whoami",
]

def _watch_run_capped(cmd, cwd, env, log_path, timeout_s, cap_bytes,
                      on_beat=None, beat_secs=None, should_stop=None,
                      on_stall=None, on_stall_resolved=None, limited=False):
    """Run cmd with stdout+stderr teed into log_path, capped at cap_bytes for
    this run alone -- a single verbose run must not be able to blow past the
    log's rotation budget before the between-run rotation in cmd_watch's
    log() ever gets a chance to fire. Keeps draining the pipe past the cap so
    the child never blocks on a full pipe buffer. Returns (rc, timed_out);
    rc is 124 on timeout, matching the previous subprocess.call behavior.

    `on_beat` is called every `beat_secs` for as long as the child is running
    (T-237). It is a separate ticker thread rather than a hook on the output
    pump because a session that is thinking writes nothing for minutes, and a
    heartbeat that only fires when the child speaks reports silence as death.
    """
    import subprocess
    import threading
    import time as _time

    proc = subprocess.Popen(cmd, shell=True, cwd=cwd, env=env,
                             stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                             start_new_session=True)
    state = {"written": 0, "capped": False, "last_output": _time.monotonic(),
             "stalled": False}

    def _kill_run():
        _stall_watch().kill_process_group(proc.pid)
        try:
            proc.wait(timeout=5)
        except Exception:
            try:
                proc.kill()
                proc.wait()
            except Exception:
                pass

    def pump():
        def note_capped(lf):
            if not state["capped"]:
                lf.write("\n[watch log: run output capped at %d bytes]\n" % cap_bytes)
                lf.flush()
                state["capped"] = True

        try:
            with open(log_path, "a") as lf:
                for chunk in iter(lambda: proc.stdout.read(65536), b""):
                    if chunk:
                        state["last_output"] = _time.monotonic()
                        if state["stalled"]:
                            state["stalled"] = False
                            if on_stall_resolved:
                                _safe(on_stall_resolved, None)
                    if state["written"] >= cap_bytes:
                        note_capped(lf)
                        continue
                    take = chunk[: cap_bytes - state["written"]]
                    lf.write(take.decode("utf-8", "replace"))
                    state["written"] += len(take)
                    if len(take) < len(chunk):
                        # this single chunk already carried past the cap --
                        # there may be no further chunk to trigger the note.
                        note_capped(lf)
        except OSError:
            pass

    pump_thread = threading.Thread(target=pump, daemon=True)
    pump_thread.start()

    beat_stop = threading.Event()
    beat_thread = None
    try:
        if on_beat:
            interval = beat_secs or RUN_HEARTBEAT_SECS

            def beat():
                while not beat_stop.wait(interval):
                    _safe(on_beat, None)

            # Keep setup inside the InterruptedError boundary. SIGTERM is
            # delivered to the main thread at any bytecode; wrapping this in
            # _safe would swallow the signal handler's InterruptedError and
            # leave the child running until its timeout.
            try:
                on_beat()  # stamp the start of the run, do not wait a tick
            except InterruptedError:
                raise
            except Exception:
                pass
            beat_thread = threading.Thread(target=beat, daemon=True)
            beat_thread.start()

        deadline = None if timeout_s is None else (_time.monotonic() + float(timeout_s))
        rc = None
        sw = _stall_watch()
        thresh = sw.threshold_s()
        last_check = 0.0
        while True:
            if should_stop and should_stop():
                raise InterruptedError()
            now_m = _time.monotonic()
            remaining = None if deadline is None else (deadline - now_m)
            if remaining is not None and remaining <= 0:
                raise subprocess.TimeoutExpired(proc.args, timeout_s)
            if on_stall and not state["stalled"] and (now_m - last_check) >= sw.check_s():
                last_check = now_m
                if sw.should_mark_stalled(state["last_output"], now_m, thresh,
                                          limited=limited):
                    state["stalled"] = True
                    measured = sw.measured_stall_s(state["last_output"], now_m)
                    _safe(lambda m=measured: on_stall(m, proc.pid), None)
            slice_s = WATCH_STOP_SLICE if remaining is None else min(WATCH_STOP_SLICE, remaining)
            try:
                rc = proc.wait(timeout=slice_s)
                break
            except subprocess.TimeoutExpired:
                continue
        timed_out = False
    except subprocess.TimeoutExpired:
        _kill_run()
        rc = 124
        timed_out = True
    except InterruptedError:
        _kill_run()
        raise
    finally:
        beat_stop.set()
        # Join, do not just signal: a beat already inside its write would
        # otherwise land AFTER the caller's run-end record and resurrect
        # active=True on a finished run -- the exact class of lie this
        # heartbeat exists to remove.
        if beat_thread is not None:
            beat_thread.join(timeout=10)
    pump_thread.join(timeout=5)
    return rc, timed_out


# --- T-427: watch idle-boundary self-execv (byte-identical in tickets.py and cli.py) ---
# A tickets-releases/<sha> dir is safe to delete only when (1) it is not the
# live shim target and (2) no watch process is executing that dir. T-388 kept
# 8f513fe and 1c8335b as rollback pins even after cutover. After this hop,
# idle loops move themselves; leftover recycle tickets are only for processes
# that never reach this boundary (stuck in a run).


def _t427_release_dir(path):
    real = os.path.realpath(path or "")
    parts = real.split(os.sep)
    try:
        i = parts.index("tickets-releases")
    except ValueError:
        return ""
    if i + 1 >= len(parts) or not parts[i + 1]:
        return ""
    return os.sep.join(parts[: i + 2])


def _t427_shim_tickets(shim_path):
    """Path the live shim execv's into, or None if unreadable, or '' if unknown."""
    import re as _re
    try:
        raw = open(shim_path, "rb").read()
    except OSError:
        return None
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        text = ""
    m = _re.search(r"\[sys\.executable,\s*(['\"])(.+?)\1\]", text)
    if m:
        return m.group(2)
    real = os.path.realpath(shim_path)
    if os.path.isfile(real):
        return real
    return ""


def _t427_verified_sha(tickets_py):
    """Commit sha if tickets_py is a verified release, else ''."""
    root = os.path.dirname(os.path.realpath(tickets_py))
    manifest = os.path.join(root, "release.json")
    try:
        with open(manifest) as source:
            release = json.load(source)
        files = release["files"]
        if "tickets.py" not in files:
            return ""
        if any(name.startswith("src/") for name in files):
            for required in (
                "src/ticket_board/ticket_coordination.py",
                "src/ticket_board/board_backup.py",
            ):
                if required not in files:
                    return ""
        for name in files:
            rel = name.replace("\\", "/")
            if (not rel or rel.startswith("/") or ".." in rel.split("/")
                    or os.path.normpath(rel) != rel):
                return ""
            path = os.path.join(root, rel)
            recorded = files[name]
            expected_sha, expected_size = (
                (recorded["sha256"], recorded["size"]) if isinstance(recorded, dict)
                else (recorded, None))
            if expected_size is not None and os.stat(path).st_size != expected_size:
                return ""
            with open(path, "rb") as source:
                actual = hashlib.sha256(source.read()).hexdigest()
            if actual != expected_sha:
                return ""
        return release.get("commit") or ""
    except (OSError, ValueError, KeyError, TypeError):
        return ""


def _t427_idle_shim_path(executing_file):
    """Shim for idle-boundary hop, or '' when hop must not run."""
    if not _t427_release_dir(executing_file):
        return ""
    live = os.environ.get("TICKETS_LIVE_SHIM")
    if live:
        return live
    if os.environ.get("PYTEST_CURRENT_TEST"):
        return ""
    return os.path.expanduser("~/.claude/tools/tickets.py")


def watch_idle_reexec(executing_file, shim_path, argv, log, pid_path=None,
                      executable=None, execv=None):
    """If idle and the live shim points at a different verified release, execv.

    Returns False when this process should keep running. Does not return on hop.
    Caller invokes only at the idle boundary so an in-flight child is never
    severed. Never hops onto an unverified release. Unreadable/missing shim:
    log once, stay put, do not busy-loop.
    """
    warned = watch_idle_reexec._warned
    current = _t427_release_dir(executing_file)
    if not current or not shim_path:
        return False
    target_py = _t427_shim_tickets(shim_path)
    if target_py is None:
        key = "unreadable:" + shim_path
        if key not in warned:
            warned.add(key)
            log("release shim unreadable; staying on %s" % os.path.basename(current))
        return False
    if not target_py:
        key = "missing:" + shim_path
        if key not in warned:
            warned.add(key)
            log("release shim target missing; staying on %s" % os.path.basename(current))
        return False
    new_dir = _t427_release_dir(target_py)
    if not new_dir:
        key = "not-release:" + target_py
        if key not in warned:
            warned.add(key)
            log("release shim is not a tickets-releases dir; staying on %s"
                % os.path.basename(current))
        return False
    if os.path.dirname(current) != os.path.dirname(new_dir):
        return False
    if os.path.realpath(current) == os.path.realpath(new_dir):
        return False
    if not os.path.isfile(target_py):
        key = "missing-py:" + target_py
        if key not in warned:
            warned.add(key)
            log("release target missing; staying on %s" % os.path.basename(current))
        return False
    new_sha = _t427_verified_sha(target_py)
    if not new_sha:
        key = "unverified:" + new_dir
        if key not in warned:
            warned.add(key)
            log("release %s is not a verified release; staying on %s"
                % (os.path.basename(new_dir), os.path.basename(current)))
        return False
    old_sha = os.path.basename(current)
    log("release %s -> %s, re-exec" % (old_sha, new_sha))
    if pid_path:
        try:
            with open(pid_path, "w") as pf:
                pf.write(str(os.getpid()))
        except OSError:
            pass
    exe = executable or sys.executable
    hop = execv or os.execv
    hop(exe, [exe, os.path.realpath(shim_path)] + list(argv))
    return False


watch_idle_reexec._warned = set()
# --- end T-427 ---


def _reexec_watch_if_unpinned(board, owner, dry_run=False):
    """Replace this watch process when inherited identity is still present.

    Spawn already Popen's with `_supervisor_launch_env`. Direct `tickets watch`
    from a leadership shell does not: the process keeps TICKET_SEAT of the
    caller (CEO impersonation 2026-09-14). One execve pins the seat.

    Dry-run starts no model turn, so skip the auth-bearing re-exec (T-999).
    """
    if dry_run:
        return
    if (os.environ.get("TICKETS_WATCH_PINNED") or "").strip() == owner:
        return
    if os.environ.get("TICKETS_NO_WATCH_REEXEC"):
        return
    env = _supervisor_launch_env(board, owner)
    os.execve(sys.executable, [sys.executable] + sys.argv, env)


def cmd_watch(a, board):
    """Poll the board; when there is work for the agent, launch a worker command.

    One watcher per agent (pid lock), one run at a time, per-run timeout,
    exponential backoff after failed runs. SIGTERM and spawn --stop take
    effect within WATCH_STOP_SLICE, including during an in-flight --exec
    (the child is terminated and run_end is written). Ctrl-C still breaks
    immediately.
    """
    import signal
    import time as _time

    owner = whoami(a.agent)
    if owner.startswith("agent-"):
        sys.exit("set --agent or TICKET_AGENT to a real name")
    dry_run = bool(getattr(a, "dry_run", False))
    _reexec_watch_if_unpinned(board, owner, dry_run=dry_run)
    if not dry_run:
        print("seat=%s (TICKET_SEAT pinned; inherited TICKET_SEAT/TICKET_AGENT/TICKET_SESSION_ID stripped)"
              % owner)
    _safe(lambda: _drop_unowned_agent_ticket(board, owner), None)
    root = os.path.dirname(board)
    cwd = os.path.abspath(a.cwd or root)
    if not os.path.isdir(cwd):
        sys.exit("--cwd %s does not exist" % cwd)
    mode, launch = resolve_launch_policy(
        safe=bool(getattr(a, "safe", False)),
        permission_mode=getattr(a, "permission_mode", "") or "",
    )
    if a.exec:
        cmd = a.exec
    else:
        # No --exec: run whatever `atm join` registered for this agent.
        # Defaulting to claude here would make `atm watch --agent qwen`
        # (the cron-able form, used without spawn) launch the wrong harness.
        # Same _worker_cmd as spawn so watch and spawn share one launch policy.
        harness, cmd_template = harness_of(board, owner)
        cmd = _worker_cmd(board, owner, "", mode, harness,
                          master=getattr(a, "prompt_kind", "") or "", cmd_template=cmd_template)
        allowed = getattr(a, "allowed_tools", "") or ""
        if allowed and not cmd_template:
            cmd = "%s --allowedTools %s" % (cmd, allowed)
    # BYOA: a custom harness command is a template, not a finished command line.
    # Expansion happens per run rather than once here because {prompt_file} must
    # be a FRESH prompt every time -- the whole point of the watcher is that the
    # board changed since the last run.
    templated = any(ph in cmd for ph in HARNESS_PLACEHOLDERS)
    wake_mode = wake_mode_of(board, owner)
    requested_every = max(1, int(a.every))
    # Continuous adapters are the event bridge for already-ended model turns.
    # Keep their durable poll inside the local <5s acceptance target even when
    # the generic worker default is 60s. This is a cheap file read, not a model
    # call; the message gates still decide whether any paid turn starts.
    every = min(WATCH_MIN_INTERVAL, requested_every) if wake_mode == "continuous" else max(
        WATCH_MIN_INTERVAL, requested_every)
    # Retry/cost gates follow the enrolled seat, not argv[0]. Built-in Cursor
    # is `agent -p` (and cursor+claude starts the same way), which _harness_of_cmd
    # reports as custom — scoping from that would ignore the failure and relaunch.
    workforce_rec = load_workforce(board).get(owner, {}) or {}
    workforce_harness = (workforce_rec.get("harness") or workforce_rec.get("tool") or "").strip()
    if not workforce_harness:
        workforce_harness, _ = harness_of(board, owner)
    harness = _safe(lambda: _harness_of_cmd(cmd), "") or ""
    retry_harness = workforce_harness or harness
    # Cron/--once used to bypass this lock and could overlap a persistent
    # adapter. All launch paths now share one lease per seat.
    sa = _session_adapters()
    if sa.has_live_native_session(board, owner) and not getattr(a, "force", False):
        ep, _ = sa.live_endpoint(board, owner)
        print("skip: %s has a live native session (provider=%s, pid=%s) -- "
              "a headless watcher would double up on the seat; use watch --force to override"
              % (owner, (ep or {}).get("provider", "?"), (ep or {}).get("pid", "?")))
        if lifecycle_of(board, owner) == "ephemeral":
            sa.remove_endpoint(board, owner)
        sys.exit(0)
    lock = _watch_lock(board, owner)
    if lock is None:
        sys.exit("another watcher for %s is already running (see %s)" % (
            owner, os.path.join(agents_dir(board), owner + ".watch.pid")))
    log_path = os.path.join(agents_dir(board), owner + ".watch.log")
    # T-243: strip Git's LOCATION vars before handing the parent's environment
    # to a spawned/exec'd child, or an ambient GIT_DIR in *this* process
    # cascades into every agent this launches. _clean_git_env is deliberately
    # narrow: GIT_AUTHOR_*/GIT_COMMITTER_* survive, because this is the
    # fleet-launch env and stripping identity here would be a T-238-class
    # attribution loss (T-259 defect 3).
    # TICKET_SEAT alongside the legacy TICKET_AGENT: this launch is a
    # deliberate assignment of the seat to the process it starts, not
    # ambient inheritance, so seat_confirmed() must trust it even though
    # the launched process has a brand-new session id with no record of
    # its own yet -- otherwise a worker would be hidden from the very
    # mail it was launched to handle.
    env = _supervisor_launch_env(board, owner)
    import select
    poke_read, poke_write = os.pipe()
    os.set_blocking(poke_read, False)
    os.set_blocking(poke_write, False)
    stop = {"now": False}
    persist = bool(getattr(a, "persist", False))
    max_runs = int(getattr(a, "max_runs", 1) or 0)
    if persist:
        max_runs = 0
    stop_cond = STOP_CONDITION if max_runs else "until spawn --stop or SIGTERM (--persist)"

    def _term(signum, frame):
        # Flag only: sliced _watch_poll_wait / _watch_run_capped observe this
        # within WATCH_STOP_SLICE. Do not raise InterruptedError here —
        # Event.wait + a same-thread handler deadlocked the poll wait.
        stop["now"] = True

    def _should_stop_watch():
        return stop["now"] or os.path.exists(_stop_file(board, owner))

    def _usr1(signum, frame):
        # The interpreter writes this signal to poke_write through
        # set_wakeup_fd below. Returning normally is essential: raising here
        # used to let _WatchPoke escape proc.wait() and kill the watcher.
        pass

    signal.signal(signal.SIGTERM, _term)
    signal.signal(signal.SIGUSR1, _usr1)
    previous_wakeup_fd = signal.set_wakeup_fd(poke_write, warn_on_full_buffer=False)

    def drain_pokes():
        """Coalesce all queued SIGUSR1 bytes into one board rescan."""
        while True:
            try:
                if not os.read(poke_read, 4096):
                    return
            except BlockingIOError:
                return
            except OSError:
                return

    def log(line):
        try:
            if os.path.exists(log_path) and os.path.getsize(log_path) > WATCH_LOG_MAX_BYTES:
                os.replace(log_path, log_path + ".1")
            with open(log_path, "a") as lf:
                lf.write(line.rstrip("\n") + "\n")
        except OSError:
            pass

    runs = failures = 0
    try:
        print("watching %s for %s every %ds; wake=%s; launch=%s; cwd=%s; cmd=%s" % (
            board, owner, every, wake_mode, launch, cwd, cmd), flush=True)
        if not a.once:
            _safe(lambda: checkin(board, owner, None, "watch loop online (%s, every %ds)" % (
                wake_mode, every)), None)
        _safe(lambda: _agent_set(board, owner, drive_every=int(getattr(a, "heartbeat", 0) or 0)), None)
        while not stop["now"]:
            # Consume every queued edge at the start of one scan. A signal that
            # races this drain remains readable and forces another scan after
            # the current child, while the message itself stays durable.
            drain_pokes()
            try:
                os.unlink(_watch_poke_file(board, owner))
            except OSError:
                pass
            if os.path.exists(_stop_file(board, owner)):
                try:
                    os.unlink(_stop_file(board, owner))
                except OSError:
                    pass
                print("stop requested via atm spawn --stop")
                break
            if not a.once:
                shim = _t427_idle_shim_path(_FACADE_FILE)
                if shim:
                    watch_idle_reexec(
                        executing_file=_FACADE_FILE,
                        shim_path=shim,
                        argv=sys.argv[1:],
                        log=log,
                        pid_path=lock,
                    )
            p = _safe(lambda: pending_work(board, owner), {})
            force = bool(getattr(a, "force", False))
            if force and not actionable(p):
                p = dict(p or {}, forced=True)
            trigger_fp = _watch_trigger_fingerprint(board, owner, p) if actionable(p) else None
            trigger_key = _remote_trigger_key(trigger_fp)
            retry_state = _local_adapter_failure(
                _agent_rec(board, owner) or {}, retry_harness)
            same_failure = bool(trigger_key and retry_state.get("trigger") == trigger_key)
            retry_deferred = (not a.once and same_failure and not force and
                              (retry_state.get("state") == "failed" or
                               float(retry_state.get("retry_epoch") or 0) > _remote_epoch()))
            if actionable(p) and retry_deferred:
                log("%s skip retrigger on unchanged trigger; state=%s attempts=%s retry_at=%s" % (
                    now(), retry_state.get("state"), retry_state.get("attempts"),
                    retry_state.get("retry_at") or "manual"))
                if a.verbose:
                    print("%s dispatch %s (attempts=%s; queued trigger unchanged)" % (
                        now(), retry_state.get("state"), retry_state.get("attempts")))
            elif actionable(p):
                if (_auth_gates_spawn(retry_harness) and not getattr(a, "exec", None)
                        and not getattr(a, "dry_run", False)):
                    auth = _refresh_auth_check(board, owner)
                    if (auth.get("state") != "ready"
                            and ((auth.get("pause") or {}).get("retry_model") is False)):
                        log("%s skip model run; auth=%s pause retry_model=%s" % (
                            now(), auth.get("state"), (auth.get("pause") or {}).get("retry_model")))
                        print("  auth paused (%s); not starting a model turn" % auth.get("state"))
                        if a.once:
                            sys.exit(1)
                        _time.sleep(every)
                        continue
                if not same_failure:
                    failures = 0
                    def _clear_stale_failure(rec):
                        rec.pop("adapter_failure", None)
                    _safe(lambda: _agent_update(board, owner, _clear_stale_failure), None)
                runs += 1
                log("%s run %d trigger=%s" % (now(), runs, json.dumps(p)[:400]))
                print("%s work found (%s) wake=%s stop=%s -> run %d" % (
                    now(), ", ".join(k for k in p if k in WAKE_KEYS or k in ("messages_to_me", "review_queue")),
                    wake_reason_of(p), stop_cond, runs))
                run_cmd, cleanup = cmd, None
                if templated:
                    pf = ""
                    if "{prompt_file}" in cmd:
                        try:
                            pf, cleanup = _render_prompt_file(board, owner, getattr(a, "prompt_kind", "") or "")
                        except OSError as e:
                            log("%s run %d could not write the prompt file: %s" % (now(), runs, e))
                            print("  run %d skipped: could not write the prompt file (%s)" % (runs, e))
                            failures += 1
                            if a.once:
                                sys.exit(1)
                            if _watch_poll_wait(min(every * (2 ** min(failures, 5)), 900),
                                                stop, board, owner):
                                break
                            continue
                    run_cmd = _expand_harness_cmd(cmd, agent=owner, cwd=cwd, prompt_file=pf)
                # The trigger is recorded as the pending KEYS only: the values
                # are message text and ticket titles, and neither belongs in
                # the trajectory log (T-311 privacy rule).
                run_started = now()
                # After T-439 drop, bind via holding or agent.json ticket= left
                # by review submit — not a board scan that ignores here-empty.
                held_ticket = (p.get("holding") or [""])[0].split(" ")[0] or _watch_bind_ticket(board, owner) or None
                # T-425 FLAG: writes in the child stamp this id; aggregator
                # credits THAT run_id, not a time window (two seats, one ticket).
                run_id = "r-%s-%d-%s" % (
                    owner, runs,
                    hashlib.sha1(("%s:%d:%s:%d:%d" % (
                        owner, runs, run_started, os.getpid(), _time.time_ns()
                    )).encode()).hexdigest()[:12])
                env["TICKETS_RUN_ID"] = run_id
                env["TICKETS_RUN_NO"] = str(runs)
                release_sha = _release_commit()
                _safe(lambda rid=run_id, ht=held_ticket, rs=release_sha: traj_event(
                    board, "run_start", agent=owner, ticket=ht, run_no=runs,
                    run_id=rid, trigger=sorted(p), harness_cmd=harness,
                    worktree=cwd, release_sha=rs), None)
                if a.dry_run:
                    print("  dry-run; would execute: %s" % run_cmd)
                    if cleanup:
                        cleanup()
                    rc = 0
                    bound = _run_had_bound_write(
                        board, run_id, held_ticket, agent=owner, run_no=runs)
                    _safe(lambda rid=run_id, ht=held_ticket, bw=bound: traj_event(
                        board, "run_end", agent=owner, ticket=ht, run_no=runs,
                        run_id=rid, trigger=sorted(p), harness_cmd=harness,
                        worktree=cwd, started_at=run_started, ended_at=now(),
                        exit=0, dry_run=True,
                        bound_write=True if bw else None,
                        duration_s=_iso_span_secs(run_started, now())), None)
                else:
                    # The whole point of T-237: something must record that this
                    # agent is alive WHILE the child runs. checkin() cannot --
                    # the next call to it is on the far side of this line.
                    _safe(lambda: _run_begin(board, owner, runs, cwd,
                                             run_id=run_id, ticket=held_ticket), None)
                    # Where this run's own output starts in the shared log, so
                    # the usage parse below reads THIS run's tail and not the
                    # previous run's result object (T-311: a stale JSON blob
                    # would attribute one run's tokens to another).
                    log_before = _safe(lambda: os.path.getsize(log_path), 0) or 0
                    try:
                        rc, timed_out = _watch_run_capped(
                            run_cmd, cwd, env, log_path,
                            a.run_timeout * 60 if a.run_timeout else None,
                            WATCH_LOG_MAX_BYTES,
                            on_beat=lambda: _run_beat(board, owner, pid=os.getpid(), run=runs,
                                                      cwd=cwd, active=True),
                            beat_secs=int(getattr(a, "beat_every", 0) or RUN_HEARTBEAT_SECS),
                            should_stop=_should_stop_watch,
                            on_stall=lambda measured, pid, ht=held_ticket: _record_watch_stall(
                                board, owner, measured, pid, ticket=ht),
                            on_stall_resolved=lambda ht=held_ticket: _resolve_watch_stall(
                                board, owner, ticket=ht),
                            limited=bool(_active_seat_limit(board, owner)),
                        )
                    except InterruptedError:
                        _safe(lambda: _finalize_active_watch_run(board, owner), None)
                        if cleanup:
                            cleanup()
                        raise
                    _safe(lambda: _run_end(board, owner, runs, rc), None)
                    if cleanup:
                        cleanup()
                    ended = now()
                    run_output = _read_run_slice(log_path, log_before)
                    _watch_note_limit_from_log(
                        board, owner, run_output, rc=rc, timed_out=timed_out,
                        ticket=held_ticket, harness=_harness_of_cmd(run_cmd),
                        bound_write=_run_had_bound_write(
                            board, run_id, held_ticket, agent=owner, run_no=runs))
                    if _auth_gates_spawn(retry_harness) or retry_harness == "cursor":
                        run_auth_state = _classify_auth_output(rc, run_output)
                        if run_auth_state in ("login_required", "expired", "quota", "network") or rc == 0:
                            previous_auth = (_agent_rec(board, owner) or {}).get("auth_check") or {}
                            spec = _harness_auth_spec(board, owner, retry_harness)
                            status_cmd, login_cmd = spec if spec else (["status"], [""])
                            run_auth = {
                                "state": "ready" if rc == 0 else run_auth_state,
                                "harness": retry_harness or "cursor", "at": now(), "exit": rc,
                                "detail": ("headless run succeeded" if rc == 0 else
                                           ((run_output or "run failed").strip().splitlines()[-1][:240])),
                                "identity": previous_auth.get("identity", "") if rc == 0 else "",
                                "identity_label": previous_auth.get("identity_label", "") if rc == 0 else "",
                                "status_cmd": " ".join(status_cmd),
                                "login_cmd": " ".join(login_cmd) if login_cmd else previous_auth.get("login_cmd", ""),
                            }
                            _safe(lambda ra=run_auth: _store_auth_check(board, owner, ra), None)
                    # Tokens/cost for THIS run: the harness's own stdout when
                    # it was asked for a JSON format, else its session store.
                    # usage_error records "reported something unreadable",
                    # which must never be confused with "reported nothing".
                    usage, usage_error = _run_usage(
                        log_path, log_before, _harness_of_cmd(run_cmd), cwd,
                        run_started, ended)
                    if usage_error:
                        log("%s run %d usage not recorded: %s" % (now(), runs, usage_error))
                    bound = _run_had_bound_write(
                        board, run_id, held_ticket, agent=owner, run_no=runs)
                    _safe(lambda rid=run_id, ht=held_ticket, bw=bound: traj_event(
                        board, "run_end", agent=owner, ticket=ht,
                        run_no=runs, run_id=rid, trigger=sorted(p),
                        harness_cmd=harness, worktree=cwd,
                        started_at=run_started, ended_at=ended,
                        exit=rc, timed_out=bool(timed_out),
                        bound_write=True if bw else None,
                        duration_s=_iso_span_secs(run_started, ended),
                        outcome=_run_end_limit_outcome(
                            rc, timed_out,
                            _read_run_slice(log_path, log_before),
                            bound_write=bool(bw)),
                        usage_error=usage_error, **usage), None)
                    if timed_out:
                        with open(log_path, "a") as lf:
                            lf.write("%s run %d TIMEOUT after %d min\n" % (now(), runs, a.run_timeout))
                    log("%s run %d exit %s" % (now(), runs, rc))
                    print("  run %d finished exit=%s (log: %s)" % (runs, rc, log_path))
                if not a.once and rc not in (0, None):
                    previous = ((_agent_rec(board, owner) or {}).get("adapter_failure") or {})
                    attempts = (int(previous.get("attempts") or 0) + 1
                                if previous.get("trigger") == trigger_key else 1)
                    retrying = attempts < LOCAL_DISPATCH_MAX_ATTEMPTS
                    delay = min(every * (2 ** max(0, attempts - 1)), 60) if retrying else 0
                    retry_epoch = _remote_epoch() + delay if delay else 0
                    failure_record = {
                        "state": "retrying" if retrying else "failed",
                        "trigger": trigger_key, "attempts": attempts,
                        "max_attempts": LOCAL_DISPATCH_MAX_ATTEMPTS,
                        "reason": "local harness exit %s" % rc,
                        "retry_epoch": retry_epoch,
                        "retry_at": (datetime.fromtimestamp(retry_epoch, timezone.utc).strftime(
                            "%Y-%m-%dT%H:%M:%SZ") if retry_epoch else ""),
                        "at": now(),
                        "provider": _adapter_provider(retry_harness),
                        "harness": retry_harness or "",
                    }
                    _safe(lambda fr=failure_record: _agent_set(board, owner, adapter_failure=fr), None)
                    log("%s skip retrigger armed for unchanged failed trigger; bounded attempt %d/%d state=%s" % (
                        now(), attempts, LOCAL_DISPATCH_MAX_ATTEMPTS, failure_record["state"]))
                elif not a.once:
                    def clear_failure(rec):
                        rec.pop("adapter_failure", None)
                    _safe(lambda: _agent_update(board, owner, clear_failure), None)
                failures = failures + 1 if rc not in (0, None) else 0
                if max_runs and runs >= max_runs:
                    print("max-runs reached")
                    break
            elif a.verbose:
                print("%s nothing pending%s" % (now(), " (limited)" if p.get("limited") else ""))
            if a.once:
                sys.exit(0 if actionable(p) else 1)
            wait = min(every * (2 ** min(failures, 5)), 900) if failures else every
            _safe(lambda: checkin(board, owner, None, "watching (%d runs, %d failed in a row)" % (runs, failures)), None)
            import time as _time
            deadline = _time.monotonic() + wait
            poked = False
            while not stop["now"]:
                remaining = deadline - _time.monotonic()
                if remaining <= 0:
                    break
                if select.select([poke_read], [], [], 0)[0]:
                    poked = True
                    break
                if _watch_poll_wait(
                    min(WATCH_STOP_SLICE, remaining), stop, board, owner
                ):
                    stop["now"] = True
                    break
            if stop["now"]:
                break
            if poked:
                continue
    except InterruptedError:
        pass
    finally:
        signal.set_wakeup_fd(previous_wakeup_fd)
        os.close(poke_read)
        os.close(poke_write)
        _safe(lambda: _finalize_active_watch_run(board, owner), None)
        if lock:
            try:
                os.unlink(lock)
            except OSError:
                pass
        if lifecycle_of(board, owner) == "ephemeral":
            _safe(lambda: _session_adapters().remove_endpoint(board, owner), None)
        if not a.once:
            print("watch stopped")


def _spawn_stop(board, owner, all_boards=False):
    """`atm spawn <owner> --stop`: stop that seat's watcher ON THIS BOARD.

    Scope is the invariant here. The seat name is not globally unique: the
    same person runs one board per repo and gives the seat the same name on
    each, so a stop that matched on the name alone reached across boards and
    killed a loop the operator never named (T-926). Discovery is the board's
    own: a loop whose --cwd is under this repo, or one this board's pid file
    claims (the Steer-board + Atman-worktree shape), on any release path --
    the release-agnostic own-board discovery T-554 added is unchanged.

    `all_boards` is the separate, explicit fleet intent; it is never implied.
    """
    import signal

    with _shared_watch_table():
        pids = _live_watch_pids(owner) if all_boards else _live_watch_pids(owner, board=board)
        table_ok = _watch_table_available()
    _mark_run_interrupted(board, owner)
    scope = "any board" if all_boards else board
    if not pids and not table_ok:
        # `ps` did not run. An empty table is "unknown", and reporting it as
        # "no running watcher" is the answer that gets a duplicate loop
        # spawned beside a live one. Fall back to the pid file this board
        # wrote, but only signal a pid whose identity is confirmed.
        pid, state = _validated_owned_watch_pid(board, owner)
        if state == "owned":
            pids, table_ok = [pid], True
            print("process table unavailable; using this board's verified pid file (pid %d)" % pid)
        else:
            _stop_requested(board, owner)
            if state == "unknown":
                print("unverified: cannot read the process table, and pid %d from this board's "
                      "pid file could not be identified -- stop requested, liveness unknown. "
                      "Check the seat before `atm spawn %s`." % (pid, owner))
            else:
                print("unverified: cannot read the process table and this board has no watcher "
                      "pid file for %s -- stop requested, liveness unknown." % owner)
            return
    if not pids:
        # Watcher is already gone; a late heartbeat from the dead run
        # must not reopen the receipt. Re-apply the stop fence after the
        # liveness check so a beat that raced the first mark stays closed.
        _mark_run_interrupted(board, owner)
        print("no running watcher for %s on %s" % (owner, scope))
        if not all_boards:
            elsewhere = _live_watch_pids(owner)
            if elsewhere:
                print("note: %d loop(s) for %s are running against another board "
                      "(pids %s) -- `atm spawn %s --stop --all-boards` stops those too"
                      % (len(elsewhere), owner, ", ".join(str(p) for p in elsewhere), owner))
        return
    busy = [p for p in pids if _watcher_run_active(board, owner, p)]
    _stop_requested(board, owner)
    stopped = 0
    for pid in pids:
        try:
            os.kill(pid, signal.SIGTERM)
            stopped += 1
        except ProcessLookupError:
            pass
    print("stopped %d watcher(s) for %s on %s (pids %s)" % (
        stopped, owner, scope, ", ".join(str(p) for p in pids)))
    if busy:
        # SIGTERM + stop-file are observed within WATCH_STOP_SLICE, including
        # mid-run. The duplicate guard in the start path below only sees a
        # pid that has actually gone, so say so rather than let the operator
        # spawn into a still-live loop (T-554).
        print("mid-run: %s -- wait for the pid(s) to exit before `atm spawn %s`" % (
            ", ".join(str(p) for p in busy), owner))
    post_message(board, whoami(), "%s watcher asked to stop (%d loop(s))" % (owner, stopped))
    return


def _stop_requested(board, owner):
    """Raise this board's stop fence; the watcher exits at its next poll."""
    try:
        with open(_stop_file(board, owner), "w") as f:
            f.write(now())
    except OSError:
        pass


def cmd_spawn(a, board):
    """Bring up a persistent worker: register it, give it a worktree, and start a
    detached watcher that launches the tool (with the chosen model) whenever the
    board has work for it. --stop asks the watcher to exit at its next poll;
    --list shows who is running. The watcher lives until stopped, logout or
    reboot; `atm guide` shows how to make it a login item."""
    import subprocess

    root = os.path.dirname(board)
    if a.list:
        wf = load_workforce(board)
        print("%-14s %-9s %-9s %-10s %-8s %-12s %-8s %s" % (
            "agent", "watcher", "harness", "wake", "model", "check", "seen", "worktree"))
        with _shared_watch_table():
          for r in sorted(load_agents(board), key=lambda r: r["owner"]):
            pids = _live_watch_pids(r["owner"], board=board)
            wc = len(pids)
            wlabel = ("pid %d" % pids[0]) if wc == 1 else ("%d pids" % wc if wc else "-")
            if wc > 1:
                wlabel += " !!"
            entry = wf.get(r["owner"], {})
            print("%-14s %-9s %-9s %-10s %-8s %-12s %-8s %s" % (
                r["owner"][:14], wlabel,
                (entry.get("harness") or entry.get("tool") or "claude")[:9],
                wake_mode_of(board, r["owner"], workforce=wf)[:10],
                (entry.get("model") or "-")[:8],
                _harness_check_label(r.get("harness_check")),
                (fmt_hours(hours_since(r["seen"])) + " ago") if r.get("seen") else "never",
                (r.get("worktree") or "").replace(os.path.expanduser("~"), "~")))
        return
    if not a.name:
        sys.exit("spawn needs a name (or --list)")
    owner = a.name
    if not a.stop:
        _refuse_limited_seat(board, owner, "spawn")
    if a.stop:
        return _spawn_stop(board, owner, all_boards=bool(getattr(a, "all_boards", False)))
    requested_harness = getattr(a, "harness", "") or a.tool
    incoming_harness, _ = _split_harness(requested_harness)
    conflict = _identity_reuse_conflict(board, owner, incoming_harness)
    if conflict:
        if not getattr(a, "transfer", False):
            sys.exit(_identity_reuse_error(owner, conflict, incoming_harness))
        if _agent_holds_ticket(board, owner):
            sys.exit("refusing --transfer: %s holds a ticket; reopen or finish it first" % owner)
        _strip_identity_bound_state(board, owner)
    git_root, wt, expected_origin, base, origin_err = _resolve_spawn_target(
        board, owner,
        worktree_arg=getattr(a, "worktree", "") or "",
        repo_arg=getattr(a, "repo", "") or "",
        base_arg=getattr(a, "base", "") or "")
    if origin_err:
        sys.exit(origin_err)
    dedicated = os.path.realpath(wt) == os.path.realpath(os.path.join(git_root, ".worktrees", owner))
    if os.path.isdir(wt) and not dedicated:
        pinned_hook = _cursor_hook_agent(wt)
        if pinned_hook and pinned_hook != owner:
            sys.exit(
                "refusing to spawn %s into %s: hooks already pin %s. "
                "Use a dedicated --worktree under the target --repo "
                "(default <repo>/.worktrees/%s)."
                % (owner, wt, pinned_hook, owner))
    resolved_harness, _ = harness_of(board, owner, requested_harness,
                                     getattr(a, "cmd_template", ""))
    sa = _session_adapters()
    if sa.has_live_native_session(board, owner):
        ep, _ = sa.live_endpoint(board, owner)
        print("skip: %s has a live native session (provider=%s, pid=%s) -- "
              "spawn would double up on the interactive seat"
              % (owner, (ep or {}).get("provider", "?"), (ep or {}).get("pid", "?")))
        return
    # Zero-model preflight. Merge uses the enrolled runner (join-time host), so
    # a sandboxed coordinator cannot clobber it. Built-in Claude/Codex/Cursor
    # must be ready before a watcher starts. Keep this before cmd_join so a
    # failed relaunch cannot alter roles/harness/worktree. --exec skips it.
    if _auth_gates_spawn(resolved_harness) and not a.exec:
        auth = _preflight_seat(board, owner, requested_harness or resolved_harness)
        reason = _preflight().dispatch_refuse(auth, resolved_harness)
        if reason:
            _print_auth_result(owner, auth)
            sys.exit("watcher not started; %s" % reason)
    if not os.path.isdir(wt):
        r = subprocess.run(["git", "-C", git_root, "worktree", "add", "-q", wt, "-b", owner, base],
                           capture_output=True, text=True)
        if r.returncode != 0:
            r = subprocess.run(["git", "-C", git_root, "worktree", "add", "-q", wt, owner],
                               capture_output=True, text=True)
        if r.returncode != 0:
            sys.exit("could not create worktree %s: %s" % (wt, (r.stderr or r.stdout).strip()))
        print("worktree %s (branch %s)" % (wt, owner))
    print("target repo %s%s base %s" % (
        git_root,
        (" (%s)" % expected_origin) if expected_origin else "",
        base))
    if expected_origin:
        wf = load_workforce(board)
        entry = wf.get(owner, {})
        entry["expected_origin"] = expected_origin
        entry["spawn_repo"] = git_root
        wf[owner] = entry
        save_workforce(board, wf)
    ns = _join_namespace(a, owner)
    ns.worktree = wt
    _silent(lambda: cmd_join(ns, board))
    checkin(board, owner, None, "spawned", cwd=wt)
    # --tool/--harness no longer defaults to "claude" in the parser: an absent
    # flag must mean "use what `atm join` registered for this agent",
    # otherwise a BYOA agent silently reverts to the Claude CLI on every spawn.
    harness, cmd_template = harness_of(board, owner, getattr(a, "harness", "") or a.tool,
                                       getattr(a, "cmd_template", ""))
    if harness == "remote" and not (cmd_template or a.exec):
        sys.exit("remote adapter is offline; no local executable was selected. "
                 "Run `atm hooks remote --agent %s`, connect its long-poll/callback bridge, "
                 "or pass --cmd for a local adapter. Pending wakes remain queued." % owner)
    if a.brief:
        bn = argparse.Namespace(agent=owner, text=a.brief, ticket="", file="", show=False,
                                role="", by=whoami())
        _silent(lambda: cmd_brief(bn, board))
    inherited = _inherit_settings(git_root, wt)
    if inherited:
        print("inherited project settings into the worktree: %s" % ", ".join(inherited))
    if _pin_spawned_worker_hooks(board, owner, wt, harness):
        print("hooks pinned to %s (unique worker; canonical role hooks not inherited)" % owner)
    if a.master:
        prev = current_master(board) or {}
        with open(master_state_path(board), "w") as f:
            json.dump({"owner": owner, "since": now(), "cos": prev.get("cos", "")}, f)
        _master_log(board, "%s spawned as persistent master (planner)" % owner, by=whoami())
    if a.cos:
        prev = current_master(board) or {}
        if not prev.get("owner"):
            sys.exit("no master yet; spawn or take the master seat first")
        prev["cos"] = owner
        with open(master_state_path(board), "w") as f:
            json.dump(prev, f)
        _master_log(board, "%s spawned as persistent chief of staff (review/unblock/merge)" % owner, by=whoami())
    sa = _session_adapters()
    if sa.has_live_native_session(board, owner):
        ep, _ = sa.live_endpoint(board, owner)
        print("skip: %s has a live native session (provider=%s, pid=%s) -- "
              "spawn would double up on the interactive seat"
              % (owner, (ep or {}).get("provider", "?"), (ep or {}).get("pid", "?")))
        return
    existing = _seat_live_watchers(board, owner)
    if existing:
        detail = _format_live_watchers(existing)
        if not getattr(a, "replace", False):
            print("watcher for %s already running (%d process(es)); --stop or --replace first" % (
                owner, len(existing)))
            print(detail)
            sys.exit("refuse: live watcher for %s still holds the seat" % owner)
        stopped, stop_err = _replace_seat_watchers(board, owner, existing)
        if stop_err:
            sys.exit(stop_err)
        print("replaced %d watcher(s) for %s (pids %s)" % (
            len(stopped), owner, ", ".join(str(p) for p, _c, _s in existing)))
    else:
        _reclaim_unrelated_watch_pidfile(board, owner)
    _safe(lambda: _drop_unowned_agent_ticket(board, owner), None)
    try:
        os.unlink(_stop_file(board, owner))
    except OSError:
        pass
    mode, launch = resolve_launch_policy(safe=bool(getattr(a, "safe", False)))
    kind = "cos" if a.cos else ("master" if a.master else "")
    cmd = a.exec or _worker_cmd(board, owner, a.model, mode, harness, master=kind, cmd_template=cmd_template)
    argv = [sys.executable, os.path.realpath(_FACADE_FILE), "watch", "--agent", owner, "--every", str(a.every),
            "--cwd", wt, "--exec", cmd, "--run-timeout", str(a.run_timeout),
            "--prompt-kind", kind, "--permission-mode", mode,
            "--heartbeat", str(int(getattr(a, "heartbeat", 0) or 0))]
    if getattr(a, "safe", False):
        argv.append("--safe")
    effective_wake_mode = wake_mode_of(board, owner)
    max_runs = spawn_watch_max_runs(
        wake_mode=effective_wake_mode, persist=bool(getattr(a, "persist", False)),
        max_runs=getattr(a, "max_runs", None))
    if max_runs == 0:
        argv += ["--persist", "--max-runs", "0"]
    else:
        argv += ["--max-runs", str(max_runs)]
    # T-243: strip Git's LOCATION vars before handing the parent's environment
    # to a spawned/exec'd child, or an ambient GIT_DIR in *this* process
    # cascades into every agent this launches. _clean_git_env is deliberately
    # narrow: GIT_AUTHOR_*/GIT_COMMITTER_* survive, because this is the
    # fleet-launch env and stripping identity here would be a T-238-class
    # attribution loss (T-259 defect 3).
    # TICKET_SEAT alongside the legacy TICKET_AGENT: this launch is a
    # deliberate assignment of the seat to the process it starts, not
    # ambient inheritance, so seat_confirmed() must trust it even though
    # the launched process has a brand-new session id with no record of
    # its own yet -- otherwise a worker would be hidden from the very
    # mail it was launched to handle.
    env = _supervisor_launch_env(board, owner)
    env["PYTHONUNBUFFERED"] = "1"
    log_path = os.path.join(agents_dir(board), owner + ".watch.log")
    with open(log_path, "a") as lf:
        started = subprocess.Popen(argv, cwd=wt, env=env, stdout=lf, stderr=subprocess.STDOUT,
                                   stdin=subprocess.DEVNULL, start_new_session=True)
    started_pid = started.pid
    ok, verify_detail, pid, started_cmd = _spawn_verify_started_watcher(
        board, owner, started_pid)
    if not ok:
        import signal
        try:
            os.kill(started_pid, signal.SIGTERM)
        except (ProcessLookupError, OSError):
            pass
        sys.exit("failure: spawn did not install a live watcher for %s "
                 "(started pid %d). %s" % (owner, started_pid, verify_detail))
    model = a.model or load_workforce(board).get(owner, {}).get("model") or "default"
    print("watcher for %s started (pid %d); harness=%s; model=%s; wake=%s; launch=%s; persist=%s; max-runs=%s; seat=%s pinned; log %s" % (
        owner, pid, harness, model,
        effective_wake_mode, launch, "yes" if max_runs == 0 else "no", max_runs, owner, log_path))
    print("cmd: %s" % cmd)
    print("watch-cmdline: %s" % started_cmd)
    post_message(board, whoami(), "%s spawned as a persistent worker (%s, model %s); it wakes whenever the board has work for it"
                 % (owner, harness, model))


def _resolve_spawn_target(board, owner, worktree_arg="", repo_arg="", base_arg=""):
    """Choose the git root, worktree path, expected origin, and base ref.

    dirname(board) is not identity. Cross-repo boards (Steer `.tickets` driving
    Atman work) must pass --repo or fail closed when --worktree sits under a
    different origin. Same-repo spawn keeps the historic default
    `<board-parent>/.worktrees/<name>`.
    """
    from auth_v2_contract import normalize_git_origin, repo_identity_matches
    board_root = os.path.dirname(os.path.abspath(board))
    board_origin = _origin_key(board_root)
    repo_arg = (repo_arg or "").strip()
    worktree_arg = (worktree_arg or "").strip()
    pinned = _expected_origin_for(board, owner, worktree_arg)
    git_root = ""
    expected = ""

    if repo_arg:
        if _repo_spec_is_path(repo_arg):
            checkout = _git_root_from(os.path.abspath(os.path.expanduser(repo_arg)))
            if not checkout:
                return "", "", "", "", (
                    "spawn --repo %s is not a git checkout" % repo_arg)
            git_root = _git_main_worktree(checkout)
            expected = _origin_key(git_root) or pinned
        else:
            expected = normalize_git_origin(repo_arg) or repo_arg
            repo_name = expected.split("/")[-1] if "/" in expected else expected
            hints = [
                worktree_arg,
                os.getcwd(),
                board_root,
                os.path.join(os.path.dirname(board_root), repo_name),
            ]
            git_root = _find_checkout_for_origin(expected, hints)
            if not git_root:
                return "", "", "", "", (
                    "spawn --repo %s: no local checkout whose origin matches; "
                    "pass --repo /path/to/checkout" % repo_arg)
    else:
        wt_probe = os.path.abspath(os.path.expanduser(worktree_arg)) if worktree_arg else ""
        inferred_root = _git_main_worktree(_git_root_from(wt_probe)) if wt_probe else ""
        inferred_origin = _origin_key(inferred_root) if inferred_root else ""
        if pinned:
            expected = pinned
            repo_name = expected.split("/")[-1] if "/" in expected else expected
            git_root = _find_checkout_for_origin(expected, [
                wt_probe, os.getcwd(), board_root,
                os.path.join(os.path.dirname(board_root), repo_name),
            ])
            if not git_root:
                return "", "", "", "", (
                    "repo_mismatch: spawn git root origin must be %s "
                    "(dirname(board) is not identity); pass --repo /path/to/checkout"
                    % expected)
        elif inferred_root and inferred_origin and board_origin and inferred_origin != board_origin:
            return "", "", "", "", (
                "cross-repo spawn is ambiguous: --worktree is under %s but the "
                "board lives in %s. Pass --repo %s (or the checkout path) to "
                "derive the worktree from that repository."
                % (inferred_origin, board_origin, inferred_root))
        elif inferred_root:
            git_root = inferred_root
            expected = inferred_origin or board_origin or pinned
        else:
            git_root = board_root
            expected = board_origin or pinned

    if not git_root:
        return "", "", "", "", "repo_mismatch: could not resolve a spawn git root"
    git_root = os.path.realpath(git_root)
    wt = (os.path.abspath(os.path.expanduser(worktree_arg)) if worktree_arg
          else os.path.join(git_root, ".worktrees", owner))
    exists = os.path.isdir(wt)
    spawn_origin = _origin_key(git_root)
    if expected and spawn_origin and not repo_identity_matches(expected, spawn_origin):
        return "", "", "", "", (
            "repo_mismatch: --repo origin %s does not match checkout %s"
            % (expected, spawn_origin))
    wt_origin = _origin_key(wt) if exists else ""
    if exists and wt_origin and expected and not repo_identity_matches(expected, wt_origin):
        return "", "", "", "", "repo_mismatch: worktree origin does not match %s" % expected
    ancestor = _git_main_worktree(_git_root_from(wt)) if (exists or worktree_arg) else ""
    if ancestor and os.path.realpath(ancestor) != git_root:
        anc_origin = _origin_key(ancestor)
        if anc_origin and spawn_origin and not repo_identity_matches(anc_origin, spawn_origin):
            return "", "", "", "", (
                "repo_mismatch: --worktree is under %s but --repo is %s"
                % (anc_origin, spawn_origin or expected))
    base = _spawn_base_ref(git_root, base_arg)
    return git_root, wt, expected or spawn_origin, base, ""


def _spawn_git_root(board, owner, worktree):
    """Git object database for `worktree add`. Origin, not dirname(board)."""
    from auth_v2_contract import repo_identity_matches, spawn_repo_identity_ok
    board_root = os.path.dirname(board)
    expected = _expected_origin_for(board, owner, worktree)
    exists = os.path.isdir(worktree)
    wt_origin = _git_remote_origin(worktree) if exists else ""
    if expected:
        candidates = []
        for path in (worktree if exists else "", board_root, os.getcwd()):
            root = _init_cwd_worktree_root(path) if path else ""
            if root and root not in candidates:
                candidates.append(root)
        git_root = ""
        for root in candidates:
            if repo_identity_matches(expected, _git_remote_origin(root)):
                git_root = root
                break
        if not git_root:
            return board_root, (
                "repo_mismatch: spawn git root origin must be %s (dirname(board) is not identity)"
                % expected)
        spawn_origin = _git_remote_origin(git_root)
        if not spawn_repo_identity_ok(expected, wt_origin or spawn_origin, spawn_origin,
                                     worktree_exists=exists):
            return git_root, "repo_mismatch: worktree origin does not match %s" % expected
        return git_root, ""
    return board_root, ""
