"""T-933: stack PRs on one train branch, verify once, land at exact SHAs."""

import hashlib
import json
import os
import re
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


# Completed-run only. Substring "suite-ran" is not evidence: false, aborted,
# timed-out, and "not-suite-ran" must not ACCEPT.
_COMPLETED_SUITE = re.compile(
    r"^#?\s*suite-ran(?:\s*=\s*(?:true|yes|1|completed|ok))?\s*$",
    re.IGNORECASE,
)
RECEIPT_FIELDS = ("repo", "main_sha", "train_sha", "strategy")


def suite_evidence(text):
    """True only for an explicit completed-run marker, not a false/aborted token."""
    for line in (text or "").splitlines():
        if _COMPLETED_SUITE.match(line.strip()):
            return True
    return False


def completed_run_receipt(text, *, source, repo, sha, kind):
    """Source/env/digest receipt for a completed suite, or None if not completed."""
    if not suite_evidence(text):
        return None
    return {
        "kind": kind,
        "source": source,
        "repo": repo,
        "sha": sha,
        "completed": True,
        "env": {"python": sys.version.split()[0]},
        "digest": hashlib.sha256((text or "").encode("utf-8")).hexdigest(),
    }


def github_repo(origin):
    """Canonical owner/name from a GitHub remote URL, or empty."""
    if not origin:
        return ""
    raw = str(origin).strip().rstrip("/")
    if raw.endswith(".git"):
        raw = raw[:-4]
    if "github.com" not in raw.lower():
        return ""
    raw = raw.replace("git@", "").replace(":", "/")
    parts = [p for p in raw.split("/") if p and p.lower() != "github.com"]
    if len(parts) < 2:
        return ""
    return parts[-2] + "/" + parts[-1]


def receipt_missing(rec):
    return [key for key in RECEIPT_FIELDS if not rec.get(key)]


def debt_authorized(rec):
    """GO-WITH-DEBT needs named authority and a reviewed baseline, not a literal."""
    return (
        rec.get("verdict") == "GO-WITH-DEBT"
        and rec.get("policy") == "go-with-debt"
        and str(rec.get("debt_authority") or "").strip()
        and str(rec.get("debt_baseline") or "").strip()
    )


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
            "base": spec.get("base") or spec.get("baseRefName") or "",
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


def gh_pr_ref(pr, gh_bin="gh", root=None, repo=None):
    cmd = [gh_bin, "pr", "view", str(pr),
           "--json", "headRefOid,headRefName,number,baseRefName"]
    if repo:
        cmd.extend(["--repo", repo])
    r = subprocess.run(cmd, capture_output=True, text=True, cwd=root)
    if r.returncode != 0:
        raise SystemExit("train build: gh pr view %s failed: %s" % (
            pr, (r.stderr or r.stdout).strip()))
    data = json.loads(r.stdout)
    return {
        "pr": data.get("number", pr),
        "ref": data.get("headRefName") or ("pr-%s" % pr),
        "sha": data.get("headRefOid") or "",
        "base": data.get("baseRefName") or "",
    }


def land_train(rec, executor_ok, gh_bin="gh", root=None, trunk="main"):
    verdict = rec.get("verdict") or "unset"
    if verdict == "GO-WITH-DEBT" and not debt_authorized(rec):
        raise SystemExit(
            "train land refused: GO-WITH-DEBT needs named debt_authority + "
            "debt_baseline (not only policy=go-with-debt)")
    if verdict != "ACCEPT" and not debt_authorized(rec):
        raise SystemExit("train land refused: verdict is %s (need ACCEPT)" % verdict)
    if not executor_ok:
        raise SystemExit("train land refused: caller is not the named merge executor")
    if not root:
        raise SystemExit("train land refused: pass --artifact at the built repo")
    missing = receipt_missing(rec)
    if missing:
        raise SystemExit(
            "train land refused: receipt missing %s; rebuild/reverify" %
            ",".join(missing))
    if rec.get("strategy") != "no-ff":
        raise SystemExit("train land refused: strategy %s is not the verified no-ff train" %
                         rec.get("strategy"))
    if not same_repo(rec["repo"], repo_bind(root)):
        raise SystemExit("train land refused: --artifact is not the repo on the receipt")
    train_branch = rec.get("train_branch") or ""
    train_now = _rev(root, train_branch)
    main_now = _rev(root, trunk)
    remote_now = _rev(root, "origin/" + trunk)
    if train_now != rec.get("train_sha"):
        raise SystemExit("train land refused: train branch moved (receipt %s, now %s)" % (
            (rec.get("train_sha") or "")[:12], (train_now or "missing")[:12]))
    if main_now != rec.get("main_sha"):
        raise SystemExit("train land refused: trunk moved since verify (receipt %s, now %s)" % (
            rec["main_sha"][:12], (main_now or "missing")[:12]))
    if remote_now and remote_now != rec.get("main_sha"):
        raise SystemExit(
            "train land refused: remote origin/%s moved (receipt %s, now %s)" % (
                trunk, rec["main_sha"][:12], remote_now[:12]))
    gh_repo = github_repo((rec.get("repo") or {}).get("origin"))
    landed = []
    for member in rec.get("members") or []:
        pr = member.get("pr")
        sha = member.get("sha")
        if not pr or not sha:
            raise SystemExit("train land refused: member missing pr/sha: %s" % member)
        target = member.get("base") or member.get("baseRefName") or ""
        if target and target != trunk:
            raise SystemExit(
                "train land refused: member %s target %s != trunk %s" % (
                    pr, target, trunk))
        if member.get("repo") and not same_repo(member["repo"], rec["repo"]):
            raise SystemExit("train land refused: member %s repo != receipt repo" % pr)
        cmd = [gh_bin, "pr", "merge", str(pr), "--match-head-commit", sha, "--merge"]
        if gh_repo:
            cmd.extend(["--repo", gh_repo])
        r = subprocess.run(cmd, capture_output=True, text=True, cwd=root)
        if r.returncode != 0:
            raise SystemExit("train land: gh merge %s@%s failed: %s" % (
                pr, sha[:12], (r.stderr or r.stdout).strip()))
        landed.append({"pr": pr, "sha": sha, "repo": gh_repo, "target": trunk})
    return landed
