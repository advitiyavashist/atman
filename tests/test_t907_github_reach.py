"""Acquisition accounting must not manufacture growth, zeros or credentials."""

import importlib.util
import json
from pathlib import Path
import subprocess
import sys

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "github_reach.py"
spec = importlib.util.spec_from_file_location("github_reach", SCRIPT)
reach = importlib.util.module_from_spec(spec)
spec.loader.exec_module(reach)

REPO = "example/atman"


def observation(data, at="2026-09-14T03:00:00Z", status="ok", **extra):
    return dict(source="https://api.github.com/repos/" + REPO, observed_at=at,
                status=status, data=data, **extra)


def snapshot(at="2026-09-14T03:00:00Z", day_count=4):
    metric = {"count": day_count, "uniques": 2,
              "days": [{"day": "2026-09-13", "count": day_count, "uniques": 2}],
              "window": {"kind": "github_rolling_14_days"}}
    return {"v": 1, "repo": REPO, "observed_at": at, "observations": {
        "metadata": observation({"stars": 3, "forks": 1}, at),
        "views": observation(metric, at), "clones": observation(metric, at),
        "referrers": observation([], at), "releases": observation([], at)}}


def test_overlap_revisions_replace_and_replay_is_idempotent():
    first = snapshot(day_count=4)
    latest = snapshot("2026-09-14T04:00:00Z", day_count=7)
    state = reach.merge_snapshot({}, first)
    state = reach.merge_snapshot(state, latest)
    assert state["daily"]["views"]["2026-09-13"]["count"] == 7
    assert len(state["daily"]["views"]) == 1
    assert reach.merge_snapshot(state, latest) == state
    assert reach.merge_snapshot(state, first) == state
    # GitHub corrections can decrease an earlier count; do not max() it.
    correction = snapshot("2026-09-14T05:00:00Z", day_count=2)
    assert reach.merge_snapshot(state, correction)["daily"]["views"]["2026-09-13"]["count"] == 2


def test_failed_latest_collection_is_unknown_but_preserves_daily_history():
    state = reach.merge_snapshot({}, snapshot())
    failed = snapshot("2026-09-14T04:00:00Z")
    failed["observations"]["views"] = observation(None, "2026-09-14T04:00:00Z",
        status="unavailable", reason="http_403")
    state = reach.merge_snapshot(state, failed)
    assert state["daily"]["views"]["2026-09-13"]["count"] == 4
    assert "Views · GitHub rolling 14 days | — | http_403" in reach.report(state)


def test_daily_uniques_are_not_added_to_window_uniques():
    item = snapshot()
    item["observations"]["views"]["data"].update(count=8, uniques=2, days=[
        {"day": "2026-09-12", "count": 4, "uniques": 2},
        {"day": "2026-09-13", "count": 4, "uniques": 2}])
    rendered = reach.report(reach.merge_snapshot({}, item))
    assert "Unique visitors · same API window | 2 | ok" in rendered
    assert "Unique visitors · same API window | 4" not in rendered


def test_no_release_is_not_zero_downloads():
    rendered = reach.report(reach.merge_snapshot({}, snapshot()))
    assert "cumulative | — | no_published_releases" in rendered
    assert "npm/PyPI downloads: —" in rendered


def test_release_asset_counts_are_cumulative_not_added_across_snapshots():
    first = snapshot()
    latest = snapshot("2026-09-14T04:00:00Z")
    for item, downloads in [(first, 5), (latest, 8)]:
        item["observations"]["releases"]["data"] = [{"id": 10, "tag": "v0.2",
            "published_at": "2026-09-12T00:00:00Z", "assets": observation([
                {"id": 101, "name": "atman.zip", "download_count": downloads}])}]
    state = reach.merge_snapshot(reach.merge_snapshot({}, first), latest)
    rendered = reach.report(state)
    assert "cumulative | 8 | ok" in rendered
    assert "| v0.2 | 10 | 101 | atman.zip | 8 |" in rendered
    assert "cumulative | 13" not in rendered


def test_partial_release_assets_cannot_be_presented_as_complete_downloads():
    item = snapshot()
    item["observations"]["releases"] = observation([
        {"id": 10, "tag": "v1", "assets": observation(None, status="unavailable", reason="http_403")}],
        status="partial", reason="asset_collection_incomplete")
    assert "cumulative | — | asset_collection_incomplete" in reach.report(reach.merge_snapshot({}, item))


def test_http_errors_never_store_stderr_or_token():
    canary = "test-token-do-not-retain"
    def runner(*args, **kwargs):
        return subprocess.CompletedProcess(args[0], 1, stdout=canary,
            stderr="gh: Forbidden (HTTP 403) " + canary)
    result = reach.GitHubAPI(runner=runner).get("repos/" + REPO)
    assert result["status"] == "unavailable" and result["reason"] == "http_403"
    assert result["data"] is None and canary not in json.dumps(result)


def test_array_pagination_covers_all_pages_and_is_explicit_get():
    calls = []
    def runner(command, **kwargs):
        calls.append(command)
        return subprocess.CompletedProcess(command, 0, stdout='[[{"id":1}],[{"id":2}]]', stderr="")
    result = reach.GitHubAPI(runner=runner).get("repos/" + REPO + "/releases", paginated=True)
    assert result["data"] == [{"id": 1}, {"id": 2}]
    assert "--paginate" in calls[0] and "--slurp" in calls[0]
    assert calls[0][calls[0].index("--method") + 1] == "GET"
    assert calls[0][calls[0].index("--hostname") + 1] == "github.com"


@pytest.mark.parametrize("value", [True, -1, None, "4"])
def test_invalid_counts_are_unknown_not_coerced_to_zero(value):
    result = reach.normalize(observation({"full_name": REPO, "stargazers_count": value,
        "forks_count": 1}), lambda data: reach.metadata(data, REPO))
    assert result["status"] == "unavailable" and result["data"] is None


def test_wrong_repository_identity_does_not_enter_metrics():
    result = reach.normalize(observation({"full_name": "other/atman", "stargazers_count": 3,
        "forks_count": 1}), lambda data: reach.metadata(data, REPO))
    assert result["status"] == "unavailable"
    with pytest.raises(ValueError):
        reach.merge_snapshot({"v": 1, "repo": "other/atman"}, snapshot())


def test_missing_days_remain_missing_and_dates_are_utc():
    data = {"count": 2, "uniques": 1, "views": [
        {"timestamp": "2026-09-12T00:00:00Z", "count": 2, "uniques": 1}]}
    result = reach.traffic(data, "views")
    assert len(result["days"]) == 1
    assert result["window"]["reported_end_exclusive"] == "2026-09-13"
    item = snapshot()
    item["observations"]["views"]["data"] = result
    rendered = reach.report(reach.merge_snapshot({}, item))
    assert "2026-09-12 to 2026-09-13 (end exclusive)" in rendered
    data["views"][0]["timestamp"] = "2026-09-12T12:00:00Z"
    with pytest.raises(ValueError):
        reach.traffic(data, "views")


def test_private_snapshot_replay_and_report_only_need_no_network(tmp_path):
    item = snapshot()
    first = reach.save_snapshot(tmp_path, item)
    assert reach.save_snapshot(tmp_path, item) == first
    assert len(list((tmp_path / "snapshots").glob("*.json"))) == 1
    assert (tmp_path / "state.json").stat().st_mode & 0o777 == 0o600
    result = subprocess.run([sys.executable, str(SCRIPT), "--repo", REPO,
        "--store", str(tmp_path), "--report-only", "--gh", "/does/not/exist"],
        capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    assert "Stars | 3 | ok" in result.stdout


def test_error_collection_is_still_saved_with_unknown_values(tmp_path):
    def runner(command, **kwargs):
        raise FileNotFoundError()
    item = reach.collect(REPO, reach.GitHubAPI(runner=runner))
    state = reach.save_snapshot(tmp_path, item)
    assert all(value["status"] == "unavailable" for value in state["latest"].values())
    assert (tmp_path / "report.md").exists()


@pytest.mark.parametrize("converter", [reach.referrers, reach.release_list, reach.assets])
def test_malformed_empty_objects_are_not_successful_empty_lists(converter):
    result = reach.normalize(observation({}), converter)
    assert result["status"] == "unavailable" and result["data"] is None


def test_snapshot_store_cannot_contaminate_repo_or_ticket_board(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / ".git").mkdir()
    with pytest.raises(ValueError):
        reach.save_snapshot(repo / "private-stats", snapshot())
    assert not (repo / "private-stats").exists()
    with pytest.raises(ValueError):
        reach.save_snapshot(tmp_path / ".tickets", snapshot())
    assert not (tmp_path / ".tickets").exists()


def test_real_collection_pipeline_marks_partial_assets_and_discards_user_objects():
    def runner(command, **kwargs):
        endpoint = next(part for part in command if part.startswith("repos/"))
        if "/traffic/" in endpoint:
            if "referrers" in endpoint:
                data = []
            else:
                metric = "views" if "views" in endpoint else "clones"
                data = {"count": 0, "uniques": 0, metric: []}
        elif "/releases/11/assets" in endpoint:
            return subprocess.CompletedProcess(command, 1, stdout="private body", stderr="HTTP 403")
        elif "/releases/10/assets" in endpoint:
            data = [[{"id": 100, "name": "atman.zip", "download_count": 6,
                      "uploader": {"login": "private-user-not-retained"}}]]
        elif "/releases?" in endpoint:
            data = [[{"id": i, "tag_name": "v" + str(i), "draft": False,
                      "published_at": "2026-09-12T00:00:00Z"} for i in [10, 11]]]
        else:
            data = {"full_name": REPO, "stargazers_count": 1, "forks_count": 0,
                    "owner": {"login": "private-user-not-retained"}}
        return subprocess.CompletedProcess(command, 0, stdout=json.dumps(data), stderr="")
    item = reach.collect(REPO, reach.GitHubAPI(runner=runner))
    assert item["observations"]["releases"]["status"] == "partial"
    assert "private-user-not-retained" not in json.dumps(item)
    assert "cumulative | — | asset_collection_incomplete" in reach.report(reach.merge_snapshot({}, item))


def test_cli_retains_failed_snapshot_and_exits_nonzero(tmp_path):
    result = subprocess.run([sys.executable, str(SCRIPT), "--repo", REPO,
        "--store", str(tmp_path), "--gh", "/does/not/exist"], capture_output=True, text=True)
    assert result.returncode == 2
    assert "Stars | — | gh_not_installed" in result.stdout
    assert (tmp_path / "state.json").exists()
