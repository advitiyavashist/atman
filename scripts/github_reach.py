#!/usr/bin/env python3
"""Collect GitHub acquisition evidence using existing gh authentication.

This standalone utility does not read the ticket board or invoke any model.
Snapshots and reports belong in a private directory outside the repository.
"""

import argparse
from datetime import datetime, timedelta, timezone
import hashlib
import html
import json
import os
from pathlib import Path
import re
import subprocess
import tempfile

API_VERSION = "2026-03-10"
REPO_PATTERN = re.compile(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+\Z")


def utc_now():
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def timestamp(value):
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("timestamp must have a timezone")
    return parsed.astimezone(timezone.utc)


def count(value):
    if type(value) is not int or value < 0:
        raise ValueError("count must be a nonnegative integer")
    return value


def array(value):
    if not isinstance(value, list):
        raise ValueError("expected an array")
    return value


class GitHubAPI:
    def __init__(self, executable="gh", runner=subprocess.run):
        self.executable = executable
        self.runner = runner

    def get(self, endpoint, paginated=False):
        command = [self.executable, "api", "--hostname", "github.com", "--method", "GET",
                   "-H", "Accept: application/vnd.github+json",
                   "-H", "X-GitHub-Api-Version: " + API_VERSION, endpoint]
        if paginated:
            command.extend(["--paginate", "--slurp"])
        result = {"source": "https://api.github.com/" + endpoint,
                  "api_version": API_VERSION, "observed_at": utc_now()}
        try:
            response = self.runner(command, capture_output=True, text=True, timeout=45)
        except FileNotFoundError:
            return dict(result, status="unavailable", reason="gh_not_installed", data=None)
        except (OSError, subprocess.TimeoutExpired):
            return dict(result, status="unavailable", reason="request_failed", data=None)
        result["observed_at"] = utc_now()
        if response.returncode:
            # Never persist subprocess stderr: it may contain credentials or
            # private response bodies. Record only the HTTP status if available.
            matched = re.search(r"\bHTTP\s+(\d{3})\b", response.stderr or "")
            reason = "http_" + matched.group(1) if matched else "request_failed"
            return dict(result, status="unavailable", reason=reason, data=None)
        try:
            data = json.loads(response.stdout)
            if paginated:
                if not isinstance(data, list) or any(not isinstance(page, list) for page in data):
                    raise ValueError("expected array pages")
                data = [item for page in data for item in page]
            return dict(result, status="ok", data=data)
        except (ValueError, TypeError):
            return dict(result, status="unavailable", reason="invalid_payload", data=None)


def normalize(result, converter):
    if result["status"] != "ok":
        return result
    try:
        return dict(result, data=converter(result["data"]))
    except (KeyError, ValueError, TypeError, AttributeError):
        return dict(result, status="unavailable", reason="invalid_payload", data=None)


def metadata(data, repo):
    if data["full_name"].casefold() != repo.casefold():
        raise ValueError("repository identity mismatch")
    return {"full_name": data["full_name"], "stars": count(data["stargazers_count"]),
            "forks": count(data["forks_count"])}


def traffic(data, metric):
    rows = []
    seen = set()
    for item in array(data[metric]):
        day = timestamp(item["timestamp"])
        if day.time().isoformat() != "00:00:00" or day.date().isoformat() in seen:
            raise ValueError("expected distinct UTC days")
        seen.add(day.date().isoformat())
        rows.append({"day": day.date().isoformat(), "count": count(item["count"]),
                     "uniques": count(item["uniques"])})
    rows.sort(key=lambda item: item["day"])
    return {"count": count(data["count"]), "uniques": count(data["uniques"]),
            "days": rows, "window": {
                "kind": "github_rolling_14_days", "timezone": "UTC", "per": "day",
                "reported_start": rows[0]["day"] if rows else None,
                "reported_end_exclusive": (timestamp(rows[-1]["day"] + "T00:00:00Z")
                    + timedelta(days=1)).date().isoformat() if rows else None}}


def referrers(data):
    return [{"referrer": str(item["referrer"]), "count": count(item["count"]),
             "uniques": count(item["uniques"])} for item in array(data)]


def release_list(data):
    return [{"id": count(item["id"]), "tag": str(item["tag_name"]),
             "published_at": item["published_at"]}
            for item in array(data) if not item["draft"] and item["published_at"]]


def assets(data):
    return [{"id": count(item["id"]), "name": str(item["name"]),
             "download_count": count(item["download_count"])} for item in array(data)]


def collect(repo, api=None):
    if not REPO_PATTERN.fullmatch(repo) or any(part in (".", "..") for part in repo.split("/")):
        raise ValueError("repository must be OWNER/REPO")
    api = api or GitHubAPI()
    prefix = "repos/" + repo
    observations = {
        "metadata": normalize(api.get(prefix), lambda data: metadata(data, repo)),
        "views": normalize(api.get(prefix + "/traffic/views?per=day"), lambda data: traffic(data, "views")),
        "clones": normalize(api.get(prefix + "/traffic/clones?per=day"), lambda data: traffic(data, "clones")),
        "referrers": normalize(api.get(prefix + "/traffic/popular/referrers"), referrers),
        "releases": normalize(api.get(prefix + "/releases?per_page=100", paginated=True), release_list),
    }
    releases = observations["releases"]
    if releases["status"] == "ok":
        for release in releases["data"]:
            release["assets"] = normalize(api.get(prefix + "/releases/" + str(release["id"])
                + "/assets?per_page=100", paginated=True), assets)
        if any(item["assets"]["status"] != "ok" for item in releases["data"]):
            releases["status"] = "partial"
            releases["reason"] = "asset_collection_incomplete"
    # The project metadata name is not evidence of owning a published package.
    return {"v": 1, "repo": repo, "observed_at": utc_now(), "observations": observations,
            "packages": {"npm": "ownership_unverified", "pypi": "ownership_unverified"}}


def newer(candidate, previous):
    return previous is None or timestamp(candidate["observed_at"]) >= timestamp(previous["observed_at"])


def merge_snapshot(state, snapshot):
    if state and (state.get("v") != 1 or state["repo"].casefold() != snapshot["repo"].casefold()):
        raise ValueError("store schema or repository does not match")
    state = json.loads(json.dumps(state)) if state else {
        "v": 1, "repo": snapshot["repo"], "latest": {}, "daily": {"views": {}, "clones": {}}}
    for metric, observation in snapshot["observations"].items():
        if newer(observation, state["latest"].get(metric)):
            state["latest"][metric] = observation
        if metric in state["daily"] and observation["status"] == "ok":
            for row in observation["data"]["days"]:
                incoming = dict(row, source=observation["source"],
                                observed_at=observation["observed_at"], window=observation["data"]["window"])
                existing = state["daily"][metric].get(row["day"])
                if newer(incoming, existing):
                    state["daily"][metric][row["day"]] = incoming
    return state


def atomic_write(path, contents):
    fd, temporary = tempfile.mkstemp(prefix=path.name + ".", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w") as stream:
            stream.write(contents)
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def save_snapshot(directory, snapshot):
    # macOS/Linux collector. Serialize local state updates, not API requests.
    import fcntl
    directory = Path(directory).resolve()
    for parent in (directory,) + tuple(directory.parents):
        if parent.name == ".tickets" or (parent / ".git").exists():
            raise ValueError("snapshot store must be outside Git repositories and ticket boards")
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    snapshot_dir = directory / "snapshots"
    snapshot_dir.mkdir(exist_ok=True, mode=0o700)
    encoded = json.dumps(snapshot, indent=2, sort_keys=True) + "\n"
    name = hashlib.sha256(encoded.encode()).hexdigest() + ".json"
    with open(directory / ".lock", "a") as lock:
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
        path = directory / "state.json"
        state = json.loads(path.read_text()) if path.exists() else {}
        updated = merge_snapshot(state, snapshot)
        atomic_write(snapshot_dir / name, encoded)
        atomic_write(path, json.dumps(updated, indent=2, sort_keys=True) + "\n")
        atomic_write(directory / "report.md", report(updated))
    return updated


def text(value):
    return html.escape(str(value)).replace("|", "\\|").replace("\n", " ").replace("\r", " ")


def report(state):
    lines = ["# GitHub reach · " + text(state["repo"]), "",
             "Repository activity is not customers, installs or successful agent work.", "",
             "| Observation | Value | State | Observed at (UTC) | Reported UTC interval | Source |",
             "| --- | --- | --- | --- | --- | --- |"]

    def row(label, value, observation, status=None):
        data = observation.get("data")
        window = data.get("window") if isinstance(data, dict) else None
        interval = "—"
        if window:
            interval = (window.get("reported_start") or "not reported") + " to " + (
                window.get("reported_end_exclusive") or "not reported") + " (end exclusive)"
        lines.append("| " + " | ".join(map(text, [label, "—" if value is None else value,
            status or observation.get("reason", observation["status"]),
            observation["observed_at"], interval, observation["source"]])) + " |")

    for metric, fields in [("metadata", [("Stars", "stars"), ("Forks", "forks")]),
                            ("views", [("Views · GitHub rolling 14 days", "count"),
                                       ("Unique visitors · same API window", "uniques")]),
                            ("clones", [("Full clones · GitHub rolling 14 days", "count"),
                                        ("Unique cloners · same API window", "uniques")])]:
        observation = state["latest"][metric]
        for label, field in fields:
            row(label, observation["data"][field] if observation["status"] == "ok" else None, observation)
    release_observation = state["latest"]["releases"]
    release_data = release_observation.get("data") or []
    if release_observation["status"] != "ok":
        row("Published release asset downloads · cumulative", None, release_observation)
    elif not release_data:
        row("Published release asset downloads · cumulative", None, release_observation, "no_published_releases")
    else:
        published_assets = [asset for release in release_data for asset in release["assets"]["data"]]
        row("Published release asset downloads · cumulative",
            sum(asset["download_count"] for asset in published_assets) if published_assets else None,
            release_observation, "ok" if published_assets else "no_published_release_assets")
        lines.extend(["", "## Release assets", "", "| Tag | Release ID | Asset ID | Asset | Cumulative downloads |",
                      "| --- | --- | --- | --- | --- |"])
        for release in release_data:
            for asset in release["assets"]["data"]:
                lines.append("| " + " | ".join(map(text, [release["tag"], release["id"], asset["id"],
                    asset["name"], asset["download_count"]])) + " |")
    lines.extend(["", "npm/PyPI downloads: — · package ownership/publication unverified.", "",
                  "## Retained daily observations (UTC)", "",
                  "Daily unique values must not be summed into lifetime distinct people.", "",
                  "| Day | Views | Daily unique visitors | Clones | Daily unique cloners |",
                  "| --- | --- | --- | --- | --- |"])
    days = sorted(set(state["daily"]["views"]) | set(state["daily"]["clones"]))
    for day in days:
        values = []
        for metric in ["views", "clones"]:
            daily = state["daily"][metric].get(day)
            values.extend([daily["count"], daily["uniques"]] if daily else ["—", "—"])
        lines.append("| " + " | ".join(map(text, [day] + values)) + " |")
    refs = state["latest"]["referrers"]
    lines.extend(["", "## Top referrers · GitHub rolling 14 days", "",
                  "State: " + text(refs.get("reason", refs["status"])) + " · " + text(refs["observed_at"]),
                  "Source: " + text(refs["source"]) + " · exact dates not returned by this endpoint.", ""])
    if refs["status"] == "ok":
        lines.extend(["| Referrer | Views | Unique visitors in API window |", "| --- | --- | --- |"])
        for ref in refs["data"]:
            lines.append("| " + " | ".join(map(text, [ref["referrer"], ref["count"], ref["uniques"]])) + " |")
    lines.extend(["", "Private snapshot/state JSON retains per-endpoint provenance and daily observation times.",
                  "Missing days remain missing; a failed latest collection does not turn older data into current data.", ""])
    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", required=True, help="GitHub OWNER/REPO")
    parser.add_argument("--store", required=True, type=Path, help="private directory outside the repository")
    parser.add_argument("--gh", default="gh", help="existing authenticated GitHub CLI")
    parser.add_argument("--report-only", action="store_true", help="print retained report without API calls")
    args = parser.parse_args()
    if args.report_only:
        state = json.loads((args.store / "state.json").read_text())
        if state["repo"].casefold() != args.repo.casefold():
            parser.error("store belongs to a different repository")
    else:
        try:
            state = save_snapshot(args.store, collect(args.repo, GitHubAPI(args.gh)))
        except ValueError as error:
            parser.error(str(error))
    print(report(state))
    return 0 if all(item["status"] == "ok" for item in state["latest"].values()) else 2


if __name__ == "__main__":
    raise SystemExit(main())
