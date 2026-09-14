"""T-933: stack PRs on one train branch, verify once, land at exact SHAs."""

import json
import os
import subprocess
import sys


def train_state_path(board):
    return os.path.join(board, "train.json")


def load_train(board):
    path = train_state_path(board)
    try:
        with open(path) as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def save_train(board, rec):
    path = train_state_path(board)
    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        json.dump(rec, f, indent=2, sort_keys=True)
        f.write("\n")
    os.replace(tmp, path)
    return path


def _git(root, *args):
    return subprocess.run(
        ["git", "-C", root, *args],
        capture_output=True, text=True)


def _rev(root, ref):
    r = _git(root, "rev-parse", ref)
    if r.returncode != 0:
        return ""
    return (r.stdout or "").strip()


def failure_ids(text):
    """Nodeids / failure labels, one per line; ignore blanks and comments."""
    out = []
    for line in (text or "").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        out.append(line)
    return out


def verdict_from_failures(main_ids, train_ids):
    main_set, train_set = set(main_ids), set(train_ids)
    new = sorted(train_set - main_set)
    gone = sorted(main_set - train_set)
    return {
        "verdict": "ACCEPT" if not new else "REJECT",
        "new_failures": new,
        "cleared_failures": gone,
        "train_failures": sorted(train_set),
        "main_failures": sorted(main_set),
    }


def build_train(root, refs, branch="train/stack", trunk="main"):
    """no-ff merge refs in order onto branch. Stop on the first conflict pair."""
    if not refs:
        raise SystemExit("train build needs --prs or --refs")
    start = _rev(root, trunk)
    if not start:
        raise SystemExit("train build: trunk %s not found" % trunk)
    _git(root, "branch", "-D", branch)
    r = _git(root, "checkout", "-B", branch, trunk)
    if r.returncode != 0:
        raise SystemExit("train build: cannot checkout %s from %s: %s" % (
            branch, trunk, (r.stderr or r.stdout).strip()))
    members = []
    prev = trunk
    for spec in refs:
        name = spec.get("ref") or spec.get("sha") or ""
        sha = spec.get("sha") or _rev(root, name)
        if not sha:
            _git(root, "checkout", trunk)
            raise SystemExit("train build: missing ref %s" % name)
        merged = _git(root, "merge", "--no-ff", "--no-edit", sha)
        if merged.returncode != 0:
            _git(root, "merge", "--abort")
            _git(root, "checkout", trunk)
            raise SystemExit("conflict: %s then %s" % (prev, name))
        members.append({
            "pr": spec.get("pr"),
            "ref": name,
            "sha": sha,
            "train_sha": _rev(root, "HEAD"),
        })
        prev = name
    rec = {
        "train_branch": branch,
        "train_sha": _rev(root, "HEAD"),
        "main_sha": start,
        "members": members,
        "verdict": "",
    }
    _git(root, "checkout", trunk)
    return rec


def gh_pr_ref(pr, gh_bin="gh"):
    r = subprocess.run(
        [gh_bin, "pr", "view", str(pr), "--json", "headRefOid,headRefName,number"],
        capture_output=True, text=True)
    if r.returncode != 0:
        raise SystemExit("train build: gh pr view %s failed: %s" % (
            pr, (r.stderr or r.stdout).strip()))
    data = json.loads(r.stdout)
    return {
        "pr": data.get("number", pr),
        "ref": data.get("headRefName") or ("pr-%s" % pr),
        "sha": data.get("headRefOid") or "",
    }


def land_train(rec, executor_ok, gh_bin="gh"):
    if rec.get("verdict") != "ACCEPT":
        raise SystemExit("train land refused: verdict is %s (need ACCEPT)" % (
            rec.get("verdict") or "unset"))
    if not executor_ok:
        raise SystemExit("train land refused: caller is not the named merge executor")
    landed = []
    for member in rec.get("members") or []:
        pr = member.get("pr")
        sha = member.get("sha")
        if not pr or not sha:
            raise SystemExit("train land refused: member missing pr/sha: %s" % member)
        cmd = [gh_bin, "pr", "merge", str(pr), "--match-head-commit", sha, "--merge"]
        r = subprocess.run(cmd, capture_output=True, text=True)
        if r.returncode != 0:
            raise SystemExit("train land: gh merge %s@%s failed: %s" % (
                pr, sha[:12], (r.stderr or r.stdout).strip()))
        landed.append({"pr": pr, "sha": sha})
    return landed
