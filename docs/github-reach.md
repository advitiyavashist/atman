# GitHub reach

Collect repository acquisition evidence once a day, without agent turns. This
macOS/Linux utility uses Python 3.9+ and an existing authenticated `gh` CLI.
It is separate from the ticket board and product efficacy metrics.

From a checkout containing this script:

```sh
python3 scripts/github_reach.py \
  --repo advitiyavashist/atman \
  --store "$HOME/.cache/atman/acquisition"
```

The output is a Markdown acquisition view. The private store contains immutable
content-addressed snapshots, `state.json` and `report.md`. New snapshot/state
files have owner-only permissions. Keep the store outside the repository;
these files are not public landing-page assets. Stores inside Git repositories
or ticket-board directories are refused. No token, user list, raw API
response body or authentication error output is saved. The command uses only
GitHub GET endpoints, with explicit repository identity and API version.

Read retained evidence without another API call:

```sh
python3 scripts/github_reach.py \
  --repo advitiyavashist/atman \
  --store "$HOME/.cache/atman/acquisition" --report-only
```

Exit `0` means all requested sources were collected; exit `2` means the snapshot
and report were retained with at least one unavailable or incomplete source.
An invalid repository or mismatched store is refused. Missing credentials,
permission failures, malformed responses and missing `gh` produce unavailable
observations rather than zeros. Earlier snapshots remain available, but the
report does not pass an older successful observation off as a fresh one.

## Authentication and daily scheduling

Use the existing `gh` account. If it is not connected, run `gh auth login
--hostname github.com` interactively. For a fine-grained token restricted to
this repository, traffic requires **Administration: read**; release and asset
listing requires **Contents: read**, with repository metadata access. API
permissions can be narrower than access to the repository's web interface.
Do not put tokens in the scheduler command. [Traffic API permissions](https://docs.github.com/en/rest/metrics/traffic),
[release listing](https://docs.github.com/en/rest/releases/releases),
[asset listing](https://docs.github.com/en/rest/releases/assets).

Add a daily entry to your own scheduler after verifying one successful run.
For example, a crontab entry with absolute paths:

```cron
17 2 * * * /usr/bin/python3 /absolute/atman/scripts/github_reach.py --gh /absolute/path/to/gh --repo advitiyavashist/atman --store /absolute/private/acquisition >> /absolute/private/acquisition/collector.log 2>&1
```

Create the private directory first. The time is in the scheduler's local
timezone; observations and traffic dates are UTC. The Mac must be awake and
the scheduler must have access to the same GitHub credential store. A missed
run does not create an inferred zero. No scheduler or global settings are
installed by this utility. API calls are bounded by per-request timeouts and
paginated release/asset listing; failed calls are not retried with model turns.

## What the numbers mean

| Source | Interpretation | Accounting |
| --- | --- | --- |
| Repository metadata | Stars and forks at the observation time | Latest point-in-time count; not additions across snapshots |
| Views / clones | GitHub's rolling 14-day totals and window-level uniques | Retain each window; newest observation replaces each reported UTC day's earlier value |
| Referrers | Top ten sources in the rolling API window | Snapshot, not lifetime acquisition attribution |
| Release assets | Cumulative downloads by release ID, tag and asset ID | Latest published asset counts; no summing successive cumulative observations |
| npm / PyPI | Ownership/publication unverified | Unknown; the local project name is not proof of package ownership |

GitHub traffic dates align to UTC midnight and only the recent 14 days are
available from the API. Daily snapshots preserve observations beyond that
retention window. Missing days stay missing; the current day and other counts
can be revised by a later response. Never add rolling windows or sum daily
unique visitors into lifetime distinct people. Reported date boundaries are
retained in JSON; they are not an invented complete window for empty data.
[GitHub traffic reference](https://docs.github.com/en/rest/metrics/traffic).

An empty release list means **no published releases**, not zero downloads.
Published releases without assets mean **no published release assets**. Asset
download totals exclude GitHub's automatically generated source archives,
package managers, clones and other distribution paths. Partial asset listing
cannot produce a complete total; deleted or replaced assets retain their own
historical snapshot identity. [Release asset reference](https://docs.github.com/en/rest/releases/assets).

These sources cannot distinguish customers from bots or internal activity.
They do not establish installations, successful onboarding, first-win
conversion, productive model turns or accepted changes. Those require the
separate product metric contracts and observed funnel labels in T-811/T-831
and T-908. No collector or vendor token count establishes Atman cost savings.
