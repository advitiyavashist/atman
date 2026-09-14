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


SUITE_RAN_MARK = "suite-ran"


def suite_evidence(text):
    """True only when the log proves a suite ran, not that the list is empty."""
    for line in (text or "").splitlines():
        if SUITE_RAN_MARK in line.strip().lower().replace(" ", ""):
            return True
    return False


def verdict_from_failures(main_ids, train_ids, *, suite_ran=False):
    if not suite_ran or main_ids is None or train_ids is None:
        return {
            "verdict": "UNKNOWN",
            "new_failures": [],
            "cleared_failures": [],
            "train_failures": list(train_ids or []),
            "main_failures": list(main_ids or []),
        }
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


def _branch_exists(root, branch):
    return _git(root, "show-ref", "--verify", "--quiet", "refs/heads/" + branch).returncode == 0


def repo_bind(root):
    origin = (_git(root, "config", "--get", "remote.origin.url").stdout or "").strip()
    common = (_git(root, "rev-parse", "--git-common-dir").stdout or "").strip()
    if common and not os.path.isabs(common):
        common = os.path.join(root, common)
    if common:
        common = os.path.realpath(common)
    return {"origin": origin, "git_common_dir": common}


def same_repo(expected, got):
    if not expected or not got:
        return False
    if expected.get("origin") and got.get("origin"):
        return expected["origin"] == got["origin"]
    return bool(expected.get("git_common_dir")
                and expected["git_common_dir"] == got.get("git_common_dir"))


def build_train(root, refs, branch="train/stack", trunk="main"):
    """no-ff merge refs in order onto branch. Stop on the first conflict pair."""
    if not refs:
        raise SystemExit("train build needs --prs or --refs")
    start = _rev(root, trunk)
    if not start:
        raise SystemExit("train build: trunk %s not found" % trunk)
    if _branch_exists(root, branch):
        raise SystemExit("train build refused: branch %s already exists (will not delete it)" % branch)
    r = _git(root, "checkout", "-b", branch, trunk)
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
        bind = repo_bind(root)
        members.append({
            "pr": spec.get("pr"),
            "ref": name,
            "sha": sha,
            "train_sha": _rev(root, "HEAD"),
            "repo": bind,
        })
        prev = name
    rec = {
        "train_branch": branch,
        "train_sha": _rev(root, "HEAD"),
        "main_sha": start,
        "members": members,
        "verdict": "",
        "strategy": "no-ff",
        "repo": repo_bind(root),
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


def land_train(rec, executor_ok, gh_bin="gh", root=None, trunk="main"):
    verdict = rec.get("verdict") or "unset"
    if verdict == "GO-WITH-DEBT" and rec.get("policy") != "go-with-debt":
        raise SystemExit("train land refused: GO-WITH-DEBT needs named policy in receipt")
    if verdict != "ACCEPT" and not (
            verdict == "GO-WITH-DEBT" and rec.get("policy") == "go-with-debt"):
        raise SystemExit("train land refused: verdict is %s (need ACCEPT)" % verdict)
    if not executor_ok:
        raise SystemExit("train land refused: caller is not the named merge executor")
    if not root:
        raise SystemExit("train land refused: pass --artifact at the built repo")
    if rec.get("strategy") and rec.get("strategy") != "no-ff":
        raise SystemExit("train land refused: strategy %s is not the verified no-ff train" %
                         rec.get("strategy"))
    if rec.get("repo") and not same_repo(rec["repo"], repo_bind(root)):
        raise SystemExit("train land refused: --artifact is not the repo on the receipt")
    train_now = _rev(root, rec.get("train_branch") or "")
    main_now = _rev(root, trunk)
    if rec.get("train_sha") and train_now != rec.get("train_sha"):
        raise SystemExit("train land refused: train branch moved (receipt %s, now %s)" % (
            (rec.get("train_sha") or "")[:12], (train_now or "missing")[:12]))
    if rec.get("main_sha") and main_now != rec.get("main_sha"):
        raise SystemExit("train land refused: trunk moved since verify (receipt %s, now %s)" % (
            rec["main_sha"][:12], (main_now or "missing")[:12]))
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
