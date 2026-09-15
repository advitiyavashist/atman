#!/usr/bin/env python3
"""Install the published take's sanitized prompts in a disposable demo run."""

import argparse
import hashlib
import json
from pathlib import Path

HERE = Path(__file__).resolve().parent
PROMPTS = HERE / "evidence/prompts"
FILES = {
    "plan.prompt": "ceo-plan.prompt",
    "worker.prompt": "codex-worker.prompt",
    "review.prompt": "ceo-accept.prompt",
    "handoff.cursor.prompt": "cursor-worker.prompt",
}


def sha256(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def prepare(run):
    run = Path(run).resolve()
    manifest_path = run / "manifest.json"
    manifest = json.loads(manifest_path.read_text())
    if manifest.get("kind") != "atman-isolated-demo" or Path(manifest["run"]).resolve() != run:
        raise ValueError("not an isolated Atman demo run")
    installed = {}
    for source_name, target_name in FILES.items():
        source = PROMPTS / source_name
        target = run / target_name
        target.write_text(source.read_text().replace("<RUN>", str(run)))
        installed[target_name] = {"source": str(source.relative_to(HERE)), "sha256": sha256(target)}
    (run / "replay-prompts.json").write_text(json.dumps(installed, indent=2, sort_keys=True) + "\n")
    print("prepared replay prompts in", run)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("run")
    args = parser.parse_args()
    prepare(args.run)


if __name__ == "__main__":
    main()
