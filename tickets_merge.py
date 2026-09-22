"""Review and merge topic (T-1050).

Sibling of the tickets.py facade. Public entry points stay imported on
tickets.py; this file owns cmd_review / cmd_merge so parallel seats do not
edit the same god-file function.
"""
from __future__ import annotations

import json
import os
import sys

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
    "LABEL", "_agent_set", "_behind_trunk_refusal", "_clean_git_env", "_finish_followup",
    "_gc_hooks", "_gc_probes", "_master_log", "_notify_review_submitted", "_print_successor_release",
    "_record_pin", "_recovery", "_refuse_unreleased_deps", "_reopen_unverified_successors", "_review_verdict",
    "_round3", "_safe", "_sounding", "_start_successors", "_traj_git",
    "_trunk", "_work_view", "_worktree_gc", "artifact_tree", "checkin",
    "current_master", "fmt_hours", "git", "git_state", "load",
    "load_all", "now", "post_message", "repo_identity", "save",
    "timing", "traj_event", "whoami",
]

def cmd_review(a, board):
    """Agent finished: submit for the master to review + merge. Records branch@sha."""
    t = load(board, a.id)
    _refuse_unreleased_deps(t, load_all(board))
    if t["status"] not in ("claimed", "blocked", "open"):
        sys.exit("%s is %s; only in-progress work can be submitted" % (a.id, LABEL[t["status"]]))
    if not a.notes:
        sys.exit('review needs --notes "what to look at: paths, tests run, decisions"')
    if _sounding().review_requires_pr(t) and not (a.pr or "").strip():
        sys.exit('review: sounded code tickets require --pr N (or role=docs|pm and body says "no PR")')
    # T-272: every probe below asks the tree the DELIVERABLE is in, which is
    # not the tree the command was run from whenever --artifact is passed.
    art = artifact_tree(a)
    g = git_state(cwd=art)
    if g and g["branch"] in ("main", "master") and not a.force:
        sys.exit("RULE: submit from your own worktree branch, not %r (or --force)" % g["branch"])
    if g and g["dirty"] and not a.force:
        sys.exit("RULE: %d uncommitted files in %s -- commit before submitting for review "
                 "(or --force)" % (g["dirty"], g["top"]))
    if g and not a.force:
        trunk = _trunk(cwd=art)
        if git("merge-base", "--is-ancestor", trunk, "HEAD", cwd=art) is None:
            sys.exit(_behind_trunk_refusal(g, art, trunk))
    if (a.pr or "").strip():
        if not g:
            sys.exit("review --pr: not in a git working tree; cannot verify a submitted SHA")
        pin_err = _review_verdict().verify_submit(
            git, sha_full=g.get("sha_full") or "", branch=g.get("branch") or "",
            origin_url=g.get("repo") or "", pr=a.pr, dirty=g.get("dirty") or 0,
            cwd=art)
        if pin_err:
            sys.exit(pin_err)
    # `owner` decides who the ticket is filed under (unchanged: claim it via
    # review if nobody holds it yet, otherwise keep the existing owner).
    # `author` is who actually ran this command -- always whoami(), never
    # borrowed from `owner` -- so the note, checkin and message below credit
    # whoever really submitted, even when that is not the recorded owner
    # (T-238; this was the same by=owner bug as cmd_note, one level up).
    owner = t.get("owner") or whoami(a.owner)
    author = whoami(a.owner)
    tc = _recovery()
    if tc is not None:
        err = tc.stale_accept_error(t, author, kind="review")
        if err:
            sys.exit(err)
    expected_generation = t.get("owner_generation")
    _work_view().supersede_release_evidence(t)
    t.pop("review_head", None)
    t["status"] = "review"
    t["owner"] = owner
    t["review_at"] = now()
    text = a.notes
    if g:
        # T-215 records WHICH repo the pin belongs to so `atm merge` can
        # refuse a cross-repo ancestry close. T-272 fixes what that repo is:
        # the artifact's, not the caller's cwd.
        stamp = _record_pin(t, g, art)
        text = "%s -- %s" % (stamp, text)
        if g.get("sha_full"):
            _review_verdict().record_verified_head(t, g["sha_full"], pr=a.pr)
    if a.pr:
        t["pr"] = a.pr
        text += " (PR %s)" % a.pr
    t["notes"].append({"by": author, "at": now(), "text": "REVIEW: " + text})
    save(board, t, expected_generation=expected_generation)
    checkin(board, author, t["id"], "submitted %s for review" % t["id"])
    if owner != author:
        # T-428: the owner still holds the ticket in review; do not copy the
        # submitter's git location onto their agent record.
        _agent_set(board, owner, ticket=t["id"],
                   note="submitted %s for review by %s" % (t["id"], author))
    tmr = timing(t)
    _safe(lambda: traj_event(board, "review", agent=author, ticket=t,
                             state_before="claimed", state_after="review",
                             outcome="review", notes_len=len(a.notes or ""),
                             active_hours=_round3(tmr.get("active")),
                             pin=t.get("commit", ""),
                             **_traj_git(cwd=art)), None)
    m = current_master(board) or {}
    master_name, cos_name = _notify_review_submitted(board, author, t["id"], text, m)
    tm = timing(t)
    if t.get("commit"):
        print("pinned %s in %s (%s)" % (
            t["commit"], t.get("repo") or "?",
            "artifact tree %s" % t["artifact_dir"] if t.get("repo_source") == "artifact"
            else "this checkout -- pass --artifact <dir> if the deliverable is in another repo"))
    head = t.get("review_head") or ""
    if head:
        print("review_head: %s" % head)
    if cos_name:
        who = "CoS (%s) tasked" % cos_name
        if master_name and master_name != cos_name:
            who += "; master (%s) notified" % master_name
    else:
        who = "master%s notified" % ((" (%s)" % master_name) if master_name else "")
    print("%s -> IN REVIEW after %s of work; %s. Claim your next ticket." % (
        t["id"], fmt_hours(tm["active"]), who))
    _finish_followup(board, t["id"], "review")


def cmd_accept(a, board):
    """Record a structured accept bound to the submitted review head (T-944)."""
    t = load(board, a.id)
    ev, err = _review_verdict().apply(
        t, whoami(), a.sha, "accept", notes=a.notes, require_full=True)
    if err:
        sys.exit(err)
    save(board, t)
    print("%s accepted %s by %s" % (a.id, ev["sha"], ev["by"]))
    if t.get("status") == "done":
        _reopen_unverified_successors(board, a.id, sha=ev["sha"], seat=ev["by"])
        freed, started, held, capture_wait = _start_successors(board, a.id)
        _print_successor_release(t, freed, started, held, capture_wait)


def cmd_reject(a, board):
    """Record a structured reject bound to the submitted review head (T-944)."""
    t = load(board, a.id)
    ev, err = _review_verdict().apply(
        t, whoami(), a.sha, "reject", reason=a.reason, require_full=False)
    if err:
        sys.exit(err)
    save(board, t)
    print("%s rejected %s by %s" % (a.id, ev["sha"], ev["by"]))


MERGE_CONFIG_DEFAULT = {
    "test": ".venv/bin/python -m pytest -q -p no:cacheprovider -x --ignore=.worktrees --ignore=.claude",
    "integration_dir": ".worktrees/integration",
    "doc_exts": [".md", ".txt"],
}


def merge_config(board):
    cfg = dict(MERGE_CONFIG_DEFAULT)
    p = os.path.join(board, "merge.json")
    if os.path.isfile(p):
        try:
            with open(p) as f:
                cfg.update(json.load(f))
        except (IOError, ValueError):
            pass
    return cfg


def parse_review_sha(stamp):
    """Extract the git object from a review stamp like 'branch@abc1234'."""
    if not stamp or "@" not in str(stamp):
        return None
    return str(stamp).rsplit("@", 1)[-1].strip() or None


def require_integrator(board, owner, force_master=False):
    """Only the designated master.json owner may run atm merge."""
    m = current_master(board)
    if not m or not m.get("owner"):
        sys.exit("refused: no designated integrator in master.json (atm master take first)")
    # The master (planner) and the chief of staff (review/unblock/merge) may both integrate.
    if owner not in (m["owner"], m.get("cos")) and not force_master:
        sys.exit("rejected owner: %s is not the designated integrator (master %s, cos %s); "
                 "pass --force-master only for break-glass recovery" % (owner, m["owner"], m.get("cos") or "-"))
    return m


class IntegrationLock:
    """Serialize merge across processes; failed acquisition means another integrator is active."""

    def __init__(self, board):
        self.path = os.path.join(board, "merge.lock")
        self.fd = None

    def __enter__(self):
        import fcntl
        self.fd = os.open(self.path, os.O_CREAT | os.O_RDWR, 0o644)
        try:
            fcntl.flock(self.fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except (BlockingIOError, OSError):
            os.close(self.fd)
            self.fd = None
            sys.exit("refused: another integrator holds merge.lock; only one merge at a time")
        os.ftruncate(self.fd, 0)
        os.write(self.fd, ("%s %s\n" % (whoami(), now())).encode())
        return self

    def __exit__(self, *exc):
        import fcntl
        if self.fd is not None:
            try:
                fcntl.flock(self.fd, fcntl.LOCK_UN)
            finally:
                os.close(self.fd)
                self.fd = None


def _order_shas_by_ancestry(sh, cwd, shas):
    """Oldest-first unique SHAs so parent commits merge before descendants."""
    unique, seen = [], set()
    for s in shas:
        full = sh("git", "rev-parse", s, cwd=cwd)
        if full.returncode != 0:
            continue
        key = full.stdout.strip()
        if key and key not in seen:
            seen.add(key)
            unique.append(key)
    ordered = []
    remaining = list(unique)
    while remaining:
        roots = [
            cand for cand in remaining
            if not any(
                other != cand
                and sh("git", "merge-base", "--is-ancestor", other, cand, cwd=cwd).returncode == 0
                for other in remaining
            )
        ]
        if not roots:
            ordered.extend(remaining)
            break
        for cand in roots:
            ordered.append(cand)
            remaining.remove(cand)
    return ordered


def cmd_merge(a, board):
    """Master side: integrate pinned review SHAs into main behind a green test run.

    Guards (T-064 / T-079):
      - only the designated integrator (master.json) may merge
      - process lock serializes concurrent integrators
      - merge recorded review commits, not moving branch tips
      - close only tickets whose submitted SHA is ancestor of resulting main
      - refuse blanket -X ours unless --discard-code
      - failed checks leave review tickets untouched
      - close only tickets IN REVIEW, and only from the repo their pin names
        (T-215): ancestry alone is not identity -- this repo's own history can
        trivially "contain" a foreign sha that was never built on it
    """
    import subprocess
    # T-272: the board lives in one repo, the deliverable may live in another.
    # Integration must be able to happen where the artifact is, or a correctly
    # pinned cross-repo ticket could never be closed by a merge at all --
    # `root` used to be the board's own parent unconditionally.
    root = artifact_tree(a) or os.path.dirname(board)
    cfg = merge_config(board)
    trunk = _trunk(cwd=root)
    owner = whoami(a.owner)
    require_integrator(board, owner, force_master=getattr(a, "force_master", False))
    merge_repo = repo_identity(root)

    def sh(*args, cwd=root):
        # cwd= alone does not stop a leaked GIT_DIR/GIT_COMMON_DIR from
        # overriding repo discovery (T-243) -- the env must be scrubbed too.
        # _clean_git_env keeps GIT_AUTHOR_*/GIT_COMMITTER_*, which this
        # function's `git commit` depends on for correct authorship.
        return subprocess.run(list(args), cwd=cwd, capture_output=True, text=True,
                              env=_clean_git_env())

    with IntegrationLock(board):
        tickets = load_all(board)
        queue = [t for t in tickets if t["status"] == "review"]
        branches = list(a.branches) or sorted(set(t.get("branch") for t in queue if t.get("branch")))
        if not branches:
            sys.exit("nothing to merge: no branches given and the review queue is empty")
        dirty = sh("git", "status", "--porcelain", "--untracked-files=no").stdout.strip()
        if dirty:
            sys.exit("main checkout has uncommitted tracked changes; commit or move them first:\n" + dirty)
        # T-272: the final step fast-forwards the CURRENT branch, and the close
        # loop then tests ancestry against `trunk`. If the repo is parked on
        # anything else those are two different commits, so every ticket is
        # skipped with no reason printed -- indistinguishable from the repo
        # guard rejecting them. Never silent: this is the merger's own repo and
        # they need to know which.
        head_branch = git("rev-parse", "--abbrev-ref", "HEAD", cwd=root)
        if head_branch and head_branch != trunk:
            sys.exit("%s is checked out on %r, not %s. `atm merge` fast-forwards the "
                     "branch that is checked out, so merging from here would leave %s "
                     "untouched and close nothing, without saying why. "
                     "`git -C %s checkout %s` first." % (
                         root, head_branch, trunk, trunk, root, trunk))

        idir = os.path.join(root, cfg["integration_dir"])
        if os.path.isdir(idir):
            if sh("git", "status", "--porcelain", "--untracked-files=no", cwd=idir).stdout.strip():
                sys.exit("integration worktree %s has uncommitted changes; inspect it, then "
                         "`git worktree remove %s` and rerun" % (idir, idir))
            sh("git", "checkout", "-q", "-B", "integration", trunk, cwd=idir)
        else:
            sh("git", "branch", "-D", "integration")
            r = sh("git", "worktree", "add", "-q", idir, "-b", "integration", trunk)
            if r.returncode != 0:
                sys.exit("could not create integration worktree: " + (r.stderr or r.stdout).strip())
        print("integration: %s (from %s@%s in %s)" % (
            idir, trunk, git("rev-parse", "--short", trunk, cwd=root), merge_repo or root))
        stray = os.path.join(idir, ".tickets")
        if os.path.islink(stray):
            os.unlink(stray)

        merged_branches, merged_shas, actively_merged_shas, skipped, resolved_docs = [], [], [], [], []
        for b in branches:
            if sh("git", "rev-parse", "--verify", "-q", b).returncode != 0:
                skipped.append((b, "no such branch"))
                continue
            branch_tickets = [t for t in queue if t.get("branch") == b]
            pins = []
            for t in branch_tickets:
                pin = parse_review_sha(t.get("commit"))
                if not pin:
                    skipped.append((t["id"], "review ticket on %s has no recorded commit sha" % b))
                    continue
                if sh("git", "rev-parse", "--verify", "-q", pin).returncode != 0:
                    skipped.append((t["id"], "recorded sha %s not found" % pin))
                    continue
                pins.append(pin)
            if not pins:
                # Never fall back to moving tip — that was the T-064 hole.
                skipped.append((b, "no pinned review SHAs (refusing to merge live tip)"))
                continue
            if b in (a.ours or []) and not getattr(a, "discard_code", False):
                skipped.append((b, "refused -X ours without --discard-code"))
                print("  SKIPPED %s -- refused blanket conflict discard; pass --discard-code to override" % b)
                continue
            extra = ["-X", "ours"] if b in (a.ours or []) else []
            for pin in _order_shas_by_ancestry(sh, root, pins):
                if sh("git", "merge-base", "--is-ancestor", pin, "HEAD", cwd=idir).returncode == 0:
                    merged_shas.append(pin)
                    if b not in merged_branches:
                        merged_branches.append(b)
                    print("  already present %s (pinned %s)" % (b, pin[:12]))
                    continue
                short = sh("git", "rev-parse", "--short", pin).stdout.strip() or pin[:12]
                r = sh("git", "merge", "--no-edit", *extra, "-m",
                       "Integrate %s@%s into %s (master %s via atm merge%s)" % (
                           b, short, trunk, owner,
                           "; conflicts resolved in favour of the integrated tree" if extra else ""),
                       pin, cwd=idir)
                if r.returncode == 0:
                    merged_shas.append(pin)
                    actively_merged_shas.append(pin)
                    if b not in merged_branches:
                        merged_branches.append(b)
                    print("  merged %s@%s%s" % (b, short, "  (-X ours)" if extra else ""))
                    continue
                conflicted = sh("git", "diff", "--name-only", "--diff-filter=U", cwd=idir).stdout.split()
                if not conflicted:
                    sh("git", "merge", "--abort", cwd=idir)
                    why = (r.stderr or r.stdout).strip().splitlines()
                    skipped.append((b, "merge refused for %s: %s" % (short, why[0] if why else "?")))
                    print("  SKIPPED %s@%s -- %s" % (b, short, "; ".join(why[:3])))
                    continue
                only_docs = all(any(f.endswith(x) for x in cfg["doc_exts"]) for f in conflicted)
                if only_docs:
                    keep = os.path.join(idir, "docs", "handoffs", "conflicts")
                    os.makedirs(keep, exist_ok=True)
                    for f in conflicted:
                        theirs = sh("git", "show", "%s:%s" % (pin, f), cwd=idir).stdout
                        alt = os.path.join(keep, "%s.%s" % (os.path.basename(f), b.replace("/", "_")))
                        with open(alt, "w") as fh:
                            fh.write(theirs)
                        sh("git", "checkout", "--ours", f, cwd=idir)
                        sh("git", "add", f, os.path.relpath(alt, idir), cwd=idir)
                        resolved_docs.append((f, os.path.relpath(alt, idir)))
                    sh("git", "commit", "-q", "--no-edit", cwd=idir)
                    merged_shas.append(pin)
                    actively_merged_shas.append(pin)
                    if b not in merged_branches:
                        merged_branches.append(b)
                    print("  merged %s@%s (doc-only conflicts kept both copies: %s)" % (
                        b, short, ", ".join(conflicted)))
                else:
                    sh("git", "merge", "--abort", cwd=idir)
                    skipped.append((b, "code conflicts at %s: %s" % (short, ", ".join(conflicted[:6]))))
                    print("  SKIPPED %s@%s -- code conflicts in %s; ask its owner to `atm sync` and resolve"
                          % (b, short, ", ".join(conflicted[:6])))

        if not merged_shas and not merged_branches:
            print("nothing merged.")
            for b, why in skipped:
                print("  %s: %s" % (b, why))
            sys.exit(1)

        if sh("git", "ls-files", "--error-unmatch", ".tickets", cwd=idir).returncode == 0:
            sh("git", "rm", "-q", "-r", "--cached", ".tickets", cwd=idir)
            sh("git", "commit", "-q", "-m", "Untrack .tickets (the board is never versioned)", cwd=idir)
            print("  untracked .tickets that a branch had committed")

        test_cmd = cfg["test"].replace("{root}", root)
        if test_cmd.startswith(".venv/") and not os.path.exists(os.path.join(idir, ".venv")):
            test_cmd = os.path.join(root, test_cmd)
        print("tests: %s" % test_cmd)
        t = subprocess.run(test_cmd, shell=True, cwd=idir, capture_output=True, text=True)
        tail = (t.stdout or t.stderr).strip().splitlines()
        last = tail[-1] if tail else "(no output)"
        if t.returncode != 0 and not a.no_test:
            print("TESTS FAILED on the integrated tree; %s untouched; review tickets preserved. "
                  "Integration left at %s.\n%s" % (trunk, idir, "\n".join(tail[-8:])))
            _master_log(board, "merge of %s aborted: tests failed (%s); review state preserved" % (
                ", ".join(merged_branches), last), by=owner)
            sys.exit(2)
        print("  " + last)

        r = sh("git", "merge", "--ff-only", "integration")
        if r.returncode != 0:
            print("could not fast-forward %s. Usually an untracked file in the main checkout that a "
                  "branch now tracks; move it aside and rerun:\n%s" % (trunk, (r.stderr or r.stdout).strip()))
            sys.exit(3)
        sha = git("rev-parse", "--short", trunk, cwd=root)
        full_trunk = sh("git", "rev-parse", trunk).stdout.strip()
        if getattr(a, "push", False):
            pr = sh("git", "push", "origin", trunk)
            if pr.returncode != 0:
                why = (pr.stderr or pr.stdout).strip()
                print("PUSH FAILED: could not push %s to origin/%s\n%s" % (trunk, trunk, why))
                _master_log(board, "atm merge: integrated %s@%s locally but PUSH FAILED (%s)" % (
                    trunk, sha, why.splitlines()[0] if why else "?"), by=owner)
                sys.exit(4)
            remote = sh("git", "ls-remote", "--heads", "origin", trunk).stdout.strip().split()
            remote_sha = remote[0] if remote else ""
            if remote_sha != full_trunk:
                print("PUSH FAILED: origin/%s is %s, expected %s" % (
                    trunk, remote_sha[:12] if remote_sha else "?", sha))
                _master_log(board, "atm merge: push reported success but origin/%s != local %s" % (
                    trunk, sha), by=owner)
                sys.exit(4)
            print("%s -> %s   (pushed to origin/%s)" % (trunk, sha, trunk))
        else:
            print("%s -> %s   (push when ready: git push origin %s)" % (trunk, sha, trunk))

        # Resolve merged pin set to full SHAs for ancestry checks.
        pin_full = set()
        for pin in merged_shas:
            got = sh("git", "rev-parse", pin).stdout.strip()
            if got:
                pin_full.add(got)
        actively_merged_full = set()
        for pin in actively_merged_shas:
            got = sh("git", "rev-parse", pin).stdout.strip()
            if got:
                actively_merged_full.add(got)

        closed = []
        for t2 in queue:
            pin = parse_review_sha(t2.get("commit"))
            if not pin:
                skipped.append((t2["id"], "review ticket has no recorded commit sha"))
                continue
            # T-215: ancestry alone is not identity. This repo's history can
            # trivially "contain" a sha it never built on (a short-prefix
            # collision, or an unrelated commit that happens to be an
            # ancestor) -- refuse to close unless the pin names THIS repo.
            # A pin from before this field existed is not eligible for an
            # automatic close either: absence must never read as "matches
            # everything".
            recorded_repo = t2.get("repo")
            if not recorded_repo:
                skipped.append((t2["id"],
                    "no repo recorded on this review pin (reviewed before T-215) -- "
                    "not eligible for an automatic close; verify by hand which repo "
                    "its deliverable is in, then close it with `atm done`"))
                continue
            if recorded_repo != merge_repo:
                skipped.append((t2["id"],
                    "recorded repo %s does not match this merge's repo %s -- "
                    "refusing a cross-repo close" % (recorded_repo, merge_repo)))
                continue
            got = sh("git", "rev-parse", pin)
            if got.returncode != 0:
                skipped.append((t2["id"], "recorded sha %s not found in this repo" % pin))
                continue
            full = got.stdout.strip()
            if sh("git", "merge-base", "--is-ancestor", full, full_trunk).returncode != 0:
                continue  # submitted SHA not on resulting main
            # Close only tickets whose recorded SHA was actually integrated (not merely same branch).
            if full not in pin_full:
                continue
            # T-324: sha membership in pin_full alone is not identity either --
            # a ticket's `commit` field can be corrupted to hold a sha that
            # some OTHER ticket's branch legitimately merged this pass (seen in
            # production: T-223 was recorded with a sha ("cos-opus@af27511")
            # that was really an unrelated sync commit, matched merge_repo,
            # resolved, was ancestor, and happened to be in pin_full anyway --
            # closing T-223 while its real fix stayed unmerged). Require that
            # the ticket's OWN recorded branch was actually part of this pass;
            # a coincidental sha match on some other branch is not evidence.
            if t2.get("branch") not in merged_branches:
                skipped.append((t2["id"],
                    "recorded sha %s matches a commit integrated this pass, but ticket's own "
                    "branch %r was not one of the branches merged -- refusing to close on a "
                    "sha coincidence alone" % (pin, t2.get("branch"))))
                continue
            # T-389: the idempotency path ("already present") folds trunk snapshots
            # into pin_full when some OTHER ticket on the same bare agent branch had
            # real work integrated this pass. Refuse to close a ticket whose pin was
            # only already-on-trunk unless it was the sole work merged on that branch.
            if full not in actively_merged_full:
                branch = t2.get("branch")
                sibling_active = False
                if branch:
                    for ot in queue:
                        if ot["id"] == t2["id"] or ot.get("branch") != branch:
                            continue
                        op = parse_review_sha(ot.get("commit"))
                        if not op:
                            continue
                        og = sh("git", "rev-parse", op)
                        if og.returncode != 0:
                            continue
                        if og.stdout.strip() in actively_merged_full:
                            sibling_active = True
                            break
                if sibling_active:
                    skipped.append((t2["id"],
                        "recorded sha %s is already on trunk but another ticket on branch %r "
                        "was the work integrated this pass -- refusing to close a pin that "
                        "contributed nothing this merge" % (pin, branch)))
                    continue
            t2["status"] = "done"
            t2["done_at"] = now()
            t2["merge_record"] = _work_view().make_merge_record(
                owner, now(), full_trunk or sha, pin=full, trunk=trunk)
            t2["notes"].append({"by": owner, "at": now(),
                                "text": "merged into %s as %s (atm merge; pinned %s)" % (
                                    trunk, sha, pin)})
            save(board, t2)
            closed.append(t2["id"])
            _tm2 = timing(t2)
            _safe(lambda t2=t2, _tm2=_tm2: traj_event(
                board, "merge", agent=owner, ticket=t2,
                state_before="review", state_after="done", outcome="done",
                pin=pin, merged_as=sha, trunk=trunk,
                prev_owner=t2.get("owner", ""),
                active_hours=_round3(_tm2.get("active"))), None)
            post_message(board, owner, "%s merged into %s as %s" % (t2["id"], trunk, sha),
                         to=t2.get("owner", ""), re=t2["id"])
        if closed:
            print("closed: %s" % ", ".join(closed))
            for tid in closed:
                freed, started, held, capture_wait = _start_successors(board, tid)
                if started:
                    print("  started after %s: %s" % (tid, ", ".join(started)))
            gc_rows = _safe(lambda: _worktree_gc().sweep(
                board, _gc_hooks(), probes=_gc_probes(), repo_root=root, apply=True), [])
            if gc_rows:
                print("gc: %d worktree decision(s)" % len(gc_rows))
                print(_worktree_gc().format_digest(gc_rows))
        for b, why in skipped:
            print("  skipped %s: %s" % (b, why))
        for f, alt in resolved_docs:
            print("  doc conflict %s: kept %s's copy, branch copy at %s" % (f, trunk, alt))
        _master_log(board, "atm merge: pins %s -> %s@%s; tests %s; closed %s%s" % (
            ", ".join(p[:7] for p in merged_shas) or "-",
            trunk, sha, "green" if t.returncode == 0 else "skipped",
            ", ".join(closed) or "-",
            ("; skipped " + ", ".join(str(b) for b, _ in skipped)) if skipped else ""), by=owner)
        post_message(board, owner, "%s is now %s (merged %s). Everyone: run `atm sync` in your worktree "
                     "before your next `atm review`." % (trunk, sha, ", ".join(merged_branches)))
        _finish_followup(board, ",".join(closed) if closed else "merge", "done")
