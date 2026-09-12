"""Independent T-805 review probes; run only against disposable source archives.

Usage: python3 replay.py ARCHIVE_ROOT > results.json
ARCHIVE_ROOT contains corpus (ed57905), repairs (0ba928a), and
combined-corpus (the conflict-free merge tree of main and ed57905).
This does not refresh goldens or modify candidate source.
"""
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import tempfile


def module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    result = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(result)
    return result


def differences(a, b, path=""):
    if type(a) is not type(b):
        return [path]
    if isinstance(a, dict):
        return [p for k in sorted(a.keys() | b.keys()) for p in (
            differences(a[k], b[k], path + "/" + k) if k in a and k in b
            else [path + "/" + k])]
    if isinstance(a, list):
        if len(a) != len(b):
            return [path + "/length"]
        return [p for i, (x, y) in enumerate(zip(a, b))
                for p in differences(x, y, path + "/" + str(i))]
    return [] if a == b else [path]


base = Path(sys.argv[1]).resolve()
output = {"corpus": {}, "adversarial": []}
for revision in ("corpus", "combined-corpus"):
    root = base / revision
    runner = module("runner", root / "tools/run_core_conformance.py")
    suite = json.loads(runner.DEFAULT_SUITE.read_text())
    golden = json.loads(runner.DEFAULT_GOLDEN.read_text())
    results = []
    for case in suite["cases"]:
        row = {"case": case["id"]}
        try:
            actual = runner._execute_case(case, [sys.executable, str(root / "tickets.py")],
                                          [sys.executable, str(runner.DEFAULT_ADAPTER)],
                                          runner.DEFAULT_SUITE.parent)
            expected = next(c for c in golden["cases"] if c["id"] == case["id"])
            comparable = lambda c: {k: c[k] for k in ("id", "area", "captures", "steps")}
            diff = differences(comparable(expected), comparable(actual))
            row.update(assertions="PASS", golden="FAIL" if diff else "PASS",
                       differences=diff, metrics=actual["metrics"])
            # Preserve a few exact differing observations without enormous board dumps.
            samples = []
            for path in diff:
                if path.endswith(("stdout", "stderr")):
                    parts = path.strip("/").split("/")
                    a, b = comparable(expected), comparable(actual)
                    for part in parts:
                        a, b = a[part], b[part]
                    samples.append({"path": path, "expected": a, "actual": b})
            row["stream_samples"] = samples[:2]
            row["difference_count"] = len(diff)
            row["differences"] = diff[:50]
            for sample in row["stream_samples"]:
                for key in ("expected", "actual"):
                    if len(sample[key]) > 2000:
                        sample[key] = sample[key][:2000] + "\n[truncated by review reporter]\n"
        except Exception as exc:
            row.update(assertions="FAIL", error=str(exc))
        results.append(row)
    output["corpus"][revision] = results

helper = module("helper", base / "repairs/tests/test_malformed_ticket_json.py")
for revision in ("main", "repairs"):
    helper.TICKETS_PY = base / revision / "tickets.py"
    probes = [("corrupt-neighbor", args) for args in (
        ("board", "--quiet"), ("graph",), ("create", "Should not land"),
        ("claim", "T-001"), ("status", "T-001", "in-progress"),
        ("assign", "T-001", "--owner", "worker"), ("update", "T-001", "probe"))]
    probes += [("unfinished-dependency", ("claim", "T-002")),
               ("dangling-symlink", ("board", "--quiet")),
               ("orphan-partial", ("list",))]
    for fixture, args in probes:
        with tempfile.TemporaryDirectory(prefix="t805-probe-") as tmp:
            root = Path(tmp)
            subprocess.run(["git", "init", "-q", "-b", "review", str(root)], check=True)
            board = root / ".tickets"
            board.mkdir()
            helper.write_valid_ticket(board, "T-001")
            if fixture == "corrupt-neighbor":
                (board / "T-002.json").write_text('{"id":')
            elif fixture == "unfinished-dependency":
                helper.write_valid_ticket(board, "T-002", deps=["T-001"])
            elif fixture == "dangling-symlink":
                (board / "T-002.json").symlink_to(root / "missing.json")
            else:
                (board / ".T-001.json.partial").write_text('{"id":')
            def snapshot():
                return {str(p.relative_to(board)): ("symlink:" + str(p.readlink())
                        if p.is_symlink() else p.read_bytes().hex())
                        for p in board.rglob("*") if p.is_symlink() or p.is_file()}
            before = snapshot()
            result = helper.tickets(root, *args)
            after = snapshot()
            output["adversarial"].append({
                "revision": revision, "fixture": fixture, "argv": args,
                "exit": result.returncode, "stdout": result.stdout, "stderr": result.stderr,
                "changed": sorted(k for k in before.keys() | after.keys()
                                  if before.get(k) != after.get(k)),
            })
print(json.dumps(output, indent=2))
