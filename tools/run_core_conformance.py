#!/usr/bin/env python3
"""Run the language-neutral Atman core conformance corpus.

The implementation is always launched as a subprocess.  The runner knows
nothing about Python modules or Atman's internal storage layout beyond the
public files that a corpus case explicitly asks it to inspect.
"""

from __future__ import annotations

import argparse
import concurrent.futures
import hashlib
import json
import math
import os
import re
import shutil
import statistics
import stat
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any


REPO = Path(__file__).resolve().parents[1]
DEFAULT_SUITE = REPO / "conformance" / "v1" / "corpus.json"
DEFAULT_GOLDEN = REPO / "conformance" / "v1" / "golden" / "python-oracle.json"
DEFAULT_ADAPTER = REPO / "tools" / "python_storage_oracle.py"
TREE_DIGEST_VERSION = "atman-board-tree/v1"

VOLATILE_JSON_KEYS = {
    "at",
    "claimed_at",
    "closed_at",
    "created",
    "created_at",
    "done_at",
    "drive_at",
    "ended_at",
    "heartbeat_at",
    "inbox_seen",
    "inbox_seen_ids",
    "joined_at",
    "last_heartbeat",
    "last_seen",
    "loop_seen",
    "seen",
    "seen_at",
    "started_at",
    "ts",
    "updated",
    "updated_at",
}
SKIP_TREE_NAMES = {".git", "__pycache__", ".DS_Store"}
SKIP_TREE_FILES = {"trajectories.jsonl"}
SKIP_TREE_SUFFIXES = (".lock", ".lock.swp", ".partial", ".tmp", ".log")
ISO_TS_RE = re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?Z?")
BOARD_CLOCK_RE = re.compile(r"\b\d{2}-\d{2} \d{2}:\d{2}(?::\d{2})? UTC\b")
RELATIVE_TIME_RE = re.compile(r"\b\d+(?:\.\d+)?[smhd] ago\b")
MSG_ID_RE = re.compile(r"\bmsg_[0-9a-f]{32}\b")
UUID_RE = re.compile(
    r"\b[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\b",
    re.I,
)
TEMP_DIR_RE = re.compile(r"atman-conformance-[A-Za-z0-9_]+")


class ConformanceFailure(RuntimeError):
    pass


def _expand(value: Any, variables: dict[str, str]) -> Any:
    if isinstance(value, str):
        for name, replacement in variables.items():
            value = value.replace("${%s}" % name, replacement)
        return value
    if isinstance(value, list):
        return [_expand(item, variables) for item in value]
    if isinstance(value, dict):
        return {key: _expand(item, variables) for key, item in value.items()}
    return value


def _json_path(value: Any, path: str) -> Any:
    current = value
    if not path:
        return current
    for component in path.split("."):
        if isinstance(current, list):
            current = current[int(component)]
        else:
            current = current[component]
    return current


def _percentile(values: list[float], percentile: float) -> float:
    ordered = sorted(values)
    if not ordered:
        return 0.0
    index = max(0, math.ceil(percentile * len(ordered)) - 1)
    return ordered[index]


def _path_aliases(raw: str) -> list[str]:
    if not raw:
        return []
    aliases = []
    resolved = str(Path(raw).resolve())
    for item in (raw, resolved):
        aliases.append(item)
        if item.startswith("/") and not item.startswith("/private"):
            aliases.append("/private" + item)
        if item.startswith("/private"):
            aliases.append(item[len("/private"):])
    unique = []
    for item in aliases:
        if item and item not in unique:
            unique.append(item)
    unique.sort(key=len, reverse=True)
    return unique


def _normalize_stream(text: str, variables: dict[str, str]) -> str:
    if not text:
        return ""
    replacements = []
    for name, value in variables.items():
        for alias in _path_aliases(value):
            replacements.append((alias, "${%s}" % name))
    replacements.append((str(REPO), "${REPO}"))
    replacements.extend((alias, "${REPO}") for alias in _path_aliases(str(REPO)))
    replacements.sort(key=lambda item: len(item[0]), reverse=True)
    seen = set()
    for raw, token in replacements:
        if not raw or raw in seen:
            continue
        seen.add(raw)
        text = text.replace(raw, token)
    text = TEMP_DIR_RE.sub("atman-conformance-<id>", text)
    text = ISO_TS_RE.sub("<ts>", text)
    text = BOARD_CLOCK_RE.sub("<ts>", text)
    text = RELATIVE_TIME_RE.sub("<ago>", text)
    text = MSG_ID_RE.sub("msg_<id>", text)
    text = UUID_RE.sub("<uuid>", text)
    text = re.sub(r"\ninbox: [^\n]+", "", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.replace("\r\n", "\n")


def _canonicalize_json(value: Any, redact_keys: set[str]) -> Any:
    if isinstance(value, dict):
        out = {}
        for key, item in sorted(value.items()):
            if key in redact_keys or key in VOLATILE_JSON_KEYS:
                continue
            if isinstance(item, str) and (
                ISO_TS_RE.fullmatch(item)
                or BOARD_CLOCK_RE.fullmatch(item)
                or MSG_ID_RE.fullmatch(item)
            ):
                continue
            out[key] = _canonicalize_json(item, redact_keys)
        return out
    if isinstance(value, list):
        return [_canonicalize_json(item, redact_keys) for item in value]
    if isinstance(value, str):
        text = MSG_ID_RE.sub("msg_<id>", value)
        text = ISO_TS_RE.sub("<ts>", text)
        return BOARD_CLOCK_RE.sub("<ts>", text)
    return value


def _canonical_file_bytes(path: Path, variables: dict[str, str], redact_keys: set[str]) -> bytes:
    raw = path.read_bytes()
    if path.suffix in {".json", ".jsonl"} or path.name.endswith(".jsonl"):
        text = raw.decode("utf-8")
        if path.suffix == ".json":
            payload = _canonicalize_json(json.loads(text), redact_keys)
            encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"))
            return _normalize_stream(encoded, variables).encode("utf-8")
        lines = []
        for line in text.splitlines():
            if not line.strip():
                continue
            payload = _canonicalize_json(json.loads(line), redact_keys)
            encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"))
            lines.append(_normalize_stream(encoded, variables))
        return ("\n".join(lines) + ("\n" if lines else "")).encode("utf-8")
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        return raw
    return _normalize_stream(text, variables).encode("utf-8")


def _skip_tree_path(relative: Path) -> bool:
    if any(part in SKIP_TREE_NAMES for part in relative.parts):
        return True
    name = relative.name
    if name in SKIP_TREE_FILES or name.startswith("trajectories."):
        return True
    if name.endswith(SKIP_TREE_SUFFIXES):
        return True
    if name.startswith(".") and name not in {".fixture-board"}:
        return True
    return False


def _board_tree_snapshot(
    board: Path, variables: dict[str, str], redact_keys: set[str],
    names_only: tuple[str, ...] = (),
) -> dict[str, Any]:
    entries = []
    if board.exists():
        for path in sorted(board.rglob("*")):
            relative = path.relative_to(board)
            if _skip_tree_path(relative):
                continue
            rel = relative.as_posix()
            if path.is_symlink():
                entries.append({
                    "path": rel,
                    "kind": "symlink",
                    "target": _normalize_stream(os.readlink(path), variables),
                })
                continue
            if path.is_dir():
                continue
            if any(rel == prefix or rel.startswith(prefix) for prefix in names_only):
                entries.append({"path": rel, "kind": "file"})
                continue
            mode = stat.S_IMODE(path.stat().st_mode)
            payload = _canonical_file_bytes(path, variables, redact_keys)
            entries.append({
                "path": rel,
                "kind": "file",
                "mode": mode,
                "bytes": len(payload),
                "sha256": hashlib.sha256(payload).hexdigest(),
            })
    digest = hashlib.sha256(
        json.dumps(
            {"version": TREE_DIGEST_VERSION, "entries": entries},
            sort_keys=True, separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()
    return {"version": TREE_DIGEST_VERSION, "digest": digest, "entries": entries}


def _step_observation(
    result: dict[str, Any], variables: dict[str, str], redact_keys: set[str],
) -> dict[str, Any]:
    stdout = result.get("stdout") or ""
    try:
        payload = json.loads(stdout)
    except json.JSONDecodeError:
        stdout_norm = _normalize_stream(stdout, variables)
    else:
        stdout_norm = _normalize_stream(
            json.dumps(
                _canonicalize_json(payload, redact_keys), indent=2, sort_keys=True,
            ) + "\n",
            variables,
        )
    return {
        "exit_code": result["exit_code"],
        "stdout": stdout_norm,
        "stderr": _normalize_stream(result.get("stderr") or "", variables),
    }


def _resolve_program(program: str, *, search_cwd: Path) -> str:
    path = Path(program)
    searched: list[Path] = []
    if path.is_absolute():
        searched.append(path)
    else:
        searched.append((search_cwd / path).resolve())
        located = shutil.which(program, path=os.environ.get("PATH", ""))
        if located:
            searched.append(Path(located).resolve())
        located_from_cwd = shutil.which(program, path=str(search_cwd) + os.pathsep + os.environ.get("PATH", ""))
        if located_from_cwd:
            searched.append(Path(located_from_cwd).resolve())
    for candidate in searched:
        if candidate.is_file():
            return str(candidate)
    raise ConformanceFailure(
        "missing implementation binary: %s (searched before fixture cwd: %s)"
        % (program, ", ".join(str(item) for item in searched) or "<empty>")
    )


def _resolve_command(command: list[str], *, search_cwd: Path) -> list[str]:
    if not command or not all(isinstance(item, str) and item for item in command):
        raise ConformanceFailure("implementation command must be a non-empty list of strings")
    resolved = [_resolve_program(command[0], search_cwd=search_cwd)]
    for item in command[1:]:
        looks_like_path = item.startswith(".") or "/" in item or "\\" in item
        if looks_like_path:
            candidate = Path(item)
            if not candidate.is_absolute():
                candidate = (search_cwd / candidate).resolve()
            if candidate.is_file():
                resolved.append(str(candidate))
                continue
            if item.startswith(".") or item.startswith("/"):
                raise ConformanceFailure(
                    "missing implementation binary: %s (resolved from %s before fixture cwd)"
                    % (item, search_cwd)
                )
        resolved.append(item)
    return resolved


def _run_process(
    command: list[str], argv: list[str], *, cwd: Path, env: dict[str, str],
    stdin: str = "", timeout: float = 30.0,
) -> dict[str, Any]:
    started = time.perf_counter()
    try:
        result = subprocess.run(
            command + argv,
            cwd=str(cwd),
            env=env,
            input=stdin,
            text=True,
            capture_output=True,
            timeout=timeout,
        )
        return {
            "exit_code": result.returncode,
            "stdout": result.stdout,
            "stderr": result.stderr,
            "elapsed_ms": round((time.perf_counter() - started) * 1000, 3),
        }
    except FileNotFoundError as exc:
        missing = exc.filename or (command[0] if command else "<empty>")
        raise ConformanceFailure("missing implementation binary: %s" % missing) from exc
    except subprocess.TimeoutExpired as exc:
        return {
            "exit_code": 124,
            "stdout": exc.stdout or "",
            "stderr": (exc.stderr or "") + "\nconformance timeout",
            "elapsed_ms": round((time.perf_counter() - started) * 1000, 3),
        }


def _assert_result(result: dict[str, Any], expect: dict[str, Any], label: str) -> None:
    allowed = expect.get("exit_codes", [expect.get("exit_code", 0)])
    if result["exit_code"] not in allowed:
        raise ConformanceFailure(
            "%s: exit %s not in %s\nstdout: %s\nstderr: %s"
            % (label, result["exit_code"], allowed, result["stdout"], result["stderr"])
        )
    combined = result["stdout"] + result["stderr"]
    for text in expect.get("contains", []):
        if text not in combined:
            raise ConformanceFailure("%s: output missing %r" % (label, text))
    for text in expect.get("not_contains", []):
        if text in combined:
            raise ConformanceFailure("%s: output unexpectedly contains %r" % (label, text))
    if "json" in expect:
        try:
            payload = json.loads(result["stdout"])
        except json.JSONDecodeError as exc:
            raise ConformanceFailure("%s: stdout is not JSON: %s" % (label, exc)) from exc
        for path, expected in expect["json"].items():
            actual = _json_path(payload, path)
            if actual != expected:
                raise ConformanceFailure(
                    "%s: JSON %s is %r, expected %r" % (label, path, actual, expected)
                )


def _assert_files(expect: dict[str, Any], variables: dict[str, str], label: str) -> None:
    for item in expect.get("files", []):
        item = _expand(item, variables)
        path = Path(item["path"])
        exists = path.exists() or path.is_symlink()
        if exists != item.get("exists", True):
            raise ConformanceFailure("%s: unexpected existence for %s" % (label, path))
        if not exists:
            continue
        if "contains" in item and item["contains"] not in path.read_text():
            raise ConformanceFailure("%s: %s is missing %r" % (label, path, item["contains"]))
        if "json" in item:
            payload = json.loads(path.read_text())
            for json_path, expected in item["json"].items():
                actual = _json_path(payload, json_path)
                if actual != expected:
                    raise ConformanceFailure(
                        "%s: %s JSON %s is %r, expected %r"
                        % (label, path, json_path, actual, expected)
                    )


def _capture(
    result: dict[str, Any], capture: dict[str, Any], variables: dict[str, str]
) -> dict[str, Any]:
    observed: dict[str, Any] = {}
    if capture.get("exit_code"):
        observed["exit_code"] = result["exit_code"]
    if capture.get("json_paths"):
        payload = json.loads(result["stdout"])
        observed["json"] = {
            path: _json_path(payload, path) for path in capture["json_paths"]
        }
    if capture.get("output_tokens"):
        combined = result["stdout"] + result["stderr"]
        observed["output_tokens"] = {
            token: token in combined for token in capture["output_tokens"]
        }
    file_values = {}
    for raw_path, paths in capture.get("file_json_paths", {}).items():
        path = Path(_expand(raw_path, variables))
        payload = json.loads(path.read_text())
        file_values[raw_path] = {json_path: _json_path(payload, json_path) for json_path in paths}
    if file_values:
        observed["files"] = file_values
    return observed


def _base_env(variables: dict[str, str], agent: str) -> dict[str, str]:
    env = dict(os.environ)
    for name in ("GIT_DIR", "GIT_COMMON_DIR", "GIT_WORK_TREE", "PYTHONPATH"):
        env.pop(name, None)
    env.update(
        HOME=variables["HOME"],
        TICKETS_DIR=variables["BOARD"],
        TICKET_AGENT=agent,
        LC_ALL="C",
        LANG="C",
        TZ="UTC",
    )
    return env


def _prepare(case: dict[str, Any], root: Path, suite_dir: Path) -> dict[str, str]:
    home = root / "home"
    board = root / ".fixture-board"
    home.mkdir()
    subprocess.run(["git", "init", "-q", str(root)], check=True)
    variables = {
        "ROOT": str(root),
        "HOME": str(home),
        "BOARD": str(board),
        "CORPUS": str(suite_dir),
    }
    for item in case.get("copy", []):
        item = _expand(item, variables)
        source = (suite_dir / item["from"]).resolve()
        destination = Path(item["to"])
        if destination.exists():
            shutil.rmtree(destination)
        shutil.copytree(source, destination, symlinks=True)
    for item in case.get("files", []):
        item = _expand(item, variables)
        path = Path(item["path"])
        path.parent.mkdir(parents=True, exist_ok=True)
        if "json" in item:
            path.write_text(json.dumps(item["json"], indent=2) + "\n")
        else:
            path.write_text(item.get("text", ""))
    return variables


def _execute_case(
    case: dict[str, Any], command: list[str], adapter_command: list[str], suite_dir: Path,
) -> dict[str, Any]:
    redact_keys = set(case.get("snapshot_redact_keys", []))
    names_only = tuple(case.get("snapshot_names_only", []))
    with tempfile.TemporaryDirectory(prefix="atman-conformance-") as directory:
        root = Path(directory)
        variables = _prepare(case, root, suite_dir)
        case_command = (
            adapter_command
            if case.get("driver", "cli") == "storage-adapter"
            else command
        )
        captures: dict[str, Any] = {}
        metrics: dict[str, Any] = {}
        steps: dict[str, Any] = {}
        board = Path(variables["BOARD"])
        for step in case.get("steps", []):
            step = _expand(step, variables)
            step_id = step["id"]
            kind = step.get("kind", "command")
            if kind == "write":
                path = Path(step["path"])
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(step.get("text", ""))
                result = {"exit_code": 0, "stdout": "", "stderr": "", "elapsed_ms": 0.0}
            elif kind == "sleep":
                time.sleep(float(step.get("seconds", 1.1)))
                result = {"exit_code": 0, "stdout": "", "stderr": "", "elapsed_ms": 0.0}
            elif kind == "bulk_plan":
                count = int(step["count"])
                plan = [
                    {
                        "key": "perf-%04d" % index,
                        "title": "Performance fixture %04d" % index,
                        "role": step.get("role", "backend"),
                        "deps": [],
                    }
                    for index in range(count)
                ]
                env = _base_env(variables, step.get("agent", "oracle"))
                result = _run_process(
                    case_command, ["plan"], cwd=root, env=env, stdin=json.dumps(plan),
                    timeout=float(step.get("timeout", 60)),
                )
                _assert_result(result, step.get("expect", {}), step_id)
            elif kind == "symlink":
                path = Path(step["path"])
                path.parent.mkdir(parents=True, exist_ok=True)
                path.symlink_to(
                    Path(step["target"]), target_is_directory=step.get("directory", False)
                )
                result = {"exit_code": 0, "stdout": "", "stderr": "", "elapsed_ms": 0.0}
            elif kind == "parallel":
                spec = step["parallel"]
                workers = spec["workers"]

                def invoke(worker: str) -> dict[str, Any]:
                    local = dict(variables, WORKER=worker)
                    argv = _expand(spec["argv"], local)
                    agent = _expand(spec.get("agent", "${WORKER}"), local)
                    env = _base_env(variables, agent)
                    env.update(_expand(spec.get("env", {}), local))
                    return _run_process(
                        case_command, argv, cwd=root, env=env,
                        stdin=spec.get("stdin", ""),
                    )

                with concurrent.futures.ThreadPoolExecutor(max_workers=len(workers)) as pool:
                    results = list(pool.map(invoke, workers))
                successes = sum(item["exit_code"] == 0 for item in results)
                summary = {
                    "successes": successes,
                    "failures": len(results) - successes,
                    "exit_codes": sorted(item["exit_code"] for item in results),
                }
                expected = step.get("expect", {})
                for key in ("successes", "failures"):
                    if key in expected and summary[key] != expected[key]:
                        raise ConformanceFailure(
                            "%s: %s=%s, expected %s; results=%s"
                            % (step_id, key, summary[key], expected[key], results)
                        )
                allowed_failure_text = expected.get("failure_contains_any", [])
                if allowed_failure_text:
                    for item in results:
                        if item["exit_code"] and not any(
                            token in item["stdout"] + item["stderr"]
                            for token in allowed_failure_text
                        ):
                            raise ConformanceFailure(
                                "%s: unexpected race failure %s" % (step_id, item)
                            )
                result = {
                    "exit_code": 0,
                    "stdout": json.dumps(summary),
                    "stderr": "",
                    "elapsed_ms": 0.0,
                }
                captures[step_id] = summary
            elif kind == "measure":
                env = _base_env(variables, step.get("agent", "oracle"))
                env.update(step.get("env", {}))
                argv = step["argv"]
                for _ in range(step.get("warmup", 1)):
                    _run_process(case_command, argv, cwd=root, env=env)
                samples = [
                    _run_process(case_command, argv, cwd=root, env=env)
                    for _ in range(step.get("samples", 5))
                ]
                if any(item["exit_code"] != 0 for item in samples):
                    raise ConformanceFailure("%s: measured command failed: %s" % (step_id, samples))
                elapsed = [item["elapsed_ms"] for item in samples]
                p50 = statistics.median(elapsed)
                p95 = _percentile(elapsed, 0.95)
                threshold = float(step["p95_under_ms"])
                if p95 >= threshold:
                    raise ConformanceFailure(
                        "%s: p95 %.3fms is not under %.3fms" % (step_id, p95, threshold)
                    )
                metrics[step_id] = {
                    "samples": len(elapsed),
                    "p50_ms": round(p50, 3),
                    "p95_ms": round(p95, 3),
                    "p95_under_ms": threshold,
                }
                result = samples[-1]
            else:
                env = _base_env(variables, step.get("agent", "oracle"))
                env.update(step.get("env", {}))
                if "external_argv" in step:
                    external = step["external_argv"]
                    result = _run_process(external[:1], external[1:], cwd=root, env=env,
                                          stdin=step.get("stdin", ""))
                else:
                    result = _run_process(case_command, step["argv"], cwd=root, env=env,
                                          stdin=step.get("stdin", ""))
                _assert_result(result, step.get("expect", {}), step_id)
            _assert_files(step.get("expect", {}), variables, step_id)
            if kind != "parallel" and step.get("capture"):
                captures[step_id] = _capture(result, step["capture"], variables)
            observation = _step_observation(result, variables, redact_keys)
            observation["board_tree"] = _board_tree_snapshot(
                board, variables, redact_keys, names_only,
            )
            steps[step_id] = observation
        return {
            "id": case["id"],
            "area": case["area"],
            "captures": captures,
            "metrics": metrics,
            "steps": steps,
        }


def run_suite(
    suite: dict[str, Any], command: list[str], adapter_command: list[str],
    case_ids: set[str], suite_dir: Path, *, search_cwd: Path | None = None,
) -> dict[str, Any]:
    search_cwd = (search_cwd or Path.cwd()).resolve()
    command = _resolve_command(command, search_cwd=search_cwd)
    adapter_command = _resolve_command(adapter_command, search_cwd=search_cwd)
    selected = [case for case in suite["cases"] if not case_ids or case["id"] in case_ids]
    unknown = case_ids - {case["id"] for case in selected}
    if unknown:
        raise ConformanceFailure("unknown case(s): %s" % ", ".join(sorted(unknown)))
    cases = [_execute_case(case, command, adapter_command, suite_dir) for case in selected]
    return {
        "contract_version": suite["contract_version"],
        "oracle_revision": suite["oracle_revision"],
        "corpus_sha256": hashlib.sha256(
            json.dumps(suite, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest(),
        "implementation_command": _display_command(command),
        "storage_adapter_command": _display_command(adapter_command),
        "cases": cases,
        "summary": {"passed": len(cases), "failed": 0},
    }


def _semantic_view(report: dict[str, Any]) -> dict[str, Any]:
    return {
        "contract_version": report["contract_version"],
        "oracle_revision": report["oracle_revision"],
        "corpus_sha256": report["corpus_sha256"],
        "cases": [
            {
                "id": case["id"],
                "area": case["area"],
                "captures": case["captures"],
                "steps": {
                    step_id: {
                        "exit_code": step["exit_code"],
                        "stdout": step["stdout"],
                        "stderr": step["stderr"],
                        "board_tree": step["board_tree"],
                    }
                    for step_id, step in case.get("steps", {}).items()
                },
            }
            for case in report["cases"]
        ],
    }


def _display_command(command: list[str]) -> list[str]:
    displayed = []
    for item in command:
        if item == sys.executable:
            displayed.append("python3")
            continue
        try:
            relative = Path(item).resolve().relative_to(REPO)
        except (OSError, ValueError):
            displayed.append(item)
        else:
            displayed.append("${REPO}/" + str(relative))
    return displayed


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--suite", type=Path, default=DEFAULT_SUITE)
    parser.add_argument(
        "--command-json",
        help='implementation argv as JSON, e.g. \'["./target/release/atman"]\'',
    )
    parser.add_argument(
        "--adapter-command-json",
        help='storage adapter argv as JSON, e.g. \'["./atman", "conformance-adapter"]\'',
    )
    parser.add_argument("--golden", type=Path, default=DEFAULT_GOLDEN)
    parser.add_argument("--record", type=Path, help="write this run as the new oracle golden")
    parser.add_argument("--case", action="append", default=[])
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    suite = json.loads(args.suite.read_text())
    invocation_cwd = Path.cwd().resolve()
    command = json.loads(args.command_json) if args.command_json else [
        sys.executable, str(REPO / "tickets.py")
    ]
    adapter_command = json.loads(args.adapter_command_json) if args.adapter_command_json else [
        sys.executable, str(DEFAULT_ADAPTER)
    ]
    if not isinstance(command, list) or not command or not all(isinstance(x, str) for x in command):
        parser.error("--command-json must be a non-empty JSON string array")
    if not isinstance(adapter_command, list) or not adapter_command or not all(
        isinstance(x, str) for x in adapter_command
    ):
        parser.error("--adapter-command-json must be a non-empty JSON string array")
    try:
        report = run_suite(
            suite, command, adapter_command, set(args.case), args.suite.parent,
            search_cwd=invocation_cwd,
        )
        if args.record:
            args.record.parent.mkdir(parents=True, exist_ok=True)
            args.record.write_text(json.dumps(report, indent=2) + "\n")
        elif args.golden.exists():
            golden = json.loads(args.golden.read_text())
            expected = _semantic_view(golden)
            actual = _semantic_view(report)
            if args.case:
                expected["cases"] = [
                    case for case in expected["cases"] if case["id"] in set(args.case)
                ]
            if actual != expected:
                raise ConformanceFailure(
                    "candidate differs from the Python oracle golden\nexpected=%s\nactual=%s"
                    % (json.dumps(expected, indent=2), json.dumps(actual, indent=2))
                )
    except ConformanceFailure as exc:
        print("CONFORMANCE FAIL: %s" % exc, file=sys.stderr)
        return 1

    if args.json:
        print(json.dumps(report, indent=2))
    else:
        print("Atman core conformance %s: %d/%d passed" % (
            report["contract_version"], report["summary"]["passed"],
            report["summary"]["passed"],
        ))
        for case in report["cases"]:
            print("  PASS %-28s %s" % (case["id"], case["area"]))
        for case in report["cases"]:
            for name, metric in case["metrics"].items():
                print("  PERF %s/%s p50=%.3fms p95=%.3fms (<%.0fms)" % (
                    case["id"], name, metric["p50_ms"], metric["p95_ms"],
                    metric["p95_under_ms"],
                ))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
