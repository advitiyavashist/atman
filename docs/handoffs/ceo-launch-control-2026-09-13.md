# Launch control handoff — 2026-09-13

## Objective

By 2026-09-16 23:59 Asia/Singapore, ship a testable Atman app with `atm` onboarding, a coherent objective/work/team/messages interface, authenticated remote control, autonomous directed-message execution, and honest product metrics. Ship Steer with exact pack labels, a frozen evaluation protocol, measured per-pack results, and a coherent public API/product surface.

CEO owns objectives, product decisions, review and final approval. Canonical `cursor` is the board-only persistent chief of staff. Implementation workers need unique identities and separate worktrees.

## Reconnect

```sh
export TICKET_AGENT=atman-ceo-codex-0912
export TICKETS_DIR=/Users/kavana/Downloads/steer/.tickets
tickets master
tickets inbox
tickets graph
```

CEO role: `steer.ceo` → `atman-ceo-codex-0912`. CoS role: `steer.chief-of-staff` → `cursor`. Do not replace either role during a worker claim. Never edit board files by hand or delete foreign branches.

## Merged this pass

| Ticket | Change | Atman main receipt |
| --- | --- | --- |
| T-836 | Real Python 3.9 wheel; canonical packaged checkin | PR #115, `5275371` |
| T-837 | Repeated Transfer-Encoding fails closed; declared error | PR #111, `b2ed925` |
| T-784 | Probe → dependency graph → persist → reviewable SHA docs | PR #109, `3679053` |

T-836 passed an independent wheel build, isolated install, console smoke and checkin parity. T-837 passed 18 host-socket checks. T-784 passed 43 checks. Old PR #110 is closed as superseded. No branches were deleted.

## Live runtime and messaging

- CLI runtime: `/Users/kavana/Downloads/atman/.worktrees/atman-runtime-current`, now at `3679053`.
- Canonical CoS worktree: `/Users/kavana/Downloads/atman/.worktrees/cursor-cos-runtime-0912`; it must have no implementation ticket.
- CoS watcher was restarted from current runtime with a three-minute run cap. `--run-timeout` is measured in minutes; the old value of 90 allowed very long stalled runs.
- Cursor CLI reports `Not logged in`. Its browser login flow was started. Do not put credentials in board messages, docs or process arguments.
- Earlier Cursor → Codex delivery reached `watch-poked` and appeared without operator typing. Current execution acceptance is still incomplete; queued/displayed mail is not `woken`.
- The stale duplicate headless Codex master watcher was stopped; the active Codex app-server remains alive. A stale Claude test-abort limit was cleared.
- Official [Codex App Server contract](https://learn.chatgpt.com/docs/app-server): `thread/inject_items` adds history without starting a turn. T-818 must use `turn/start` or `turn/steer`, then observe generation events and a board ACK/work action.

## Concrete blockers and owners

| Ticket | Owner | End state |
| --- | --- | --- |
| T-809 | atman-identity-cursor-0912 | Rebase onto current main; isolated wheel exposes both `atm` and `tickets` from one implementation |
| T-828 | atman-transfer-wheel-fix-codex-0913 | Integrate accepted transfer behavior with packaged primitives; real installed-wheel rollback/lease tests |
| T-839 | cursor-onboard | Unique worker/session identity, root/wheel parity, identity-pinned hooks, target-worktree registration |
| T-814 | steer-corpus-fix-codex-0913 | Versioned regression + sealed holdout packet; coverage and omission gates; real independent labels |
| T-835 | steer-metrics-fix-codex-0913 | Exact gold citations, DLP-first threshold replay, fixed eligible denominator, no unlabeled violation delta |

T-841 accepted T-828 `c5945c7` in isolation: byte-identical rollback, endpoint/lease fencing, stale-limit handling, 64-way mixed joins. Integration remains necessary because it delegates packaged transfer to a root helper removed by T-836.

T-839 `498a4d4` failed independently and live: inherited session outranks explicit worker seat; installed CLI lacks parity; forged session ID reads another inbox; spawned Cursor worker took canonical CoS identity and T-404. Canonical CoS was restored. The T-404 verification candidate was reconciled back to review; do not turn it into a new launch engineering lane.

T-842 rejected T-835 `b57d9f4` with exact Go probes in `/Users/kavana/Downloads/t842_probe_test.go` and `t842_overlay.json`. T-843 was a duplicate review ticket and was discarded; its dependency was removed.

## File ownership and evaluation honesty

- T-835 owns Go backtest/runtime metric code and isolated mutation probes.
- T-814 owns Python scorer/eval corpus code, generators, manifests, protocols and corpus artifacts.
- Current UK regression gold omits some required overlays. T-814 must version and digest the correction; T-835 must not hide it with catalog/subset scoring.
- Holdout remains `NO_GO` until a real independent author and two reviewers/adjudication complete the packet. Never fabricate reviewer identities or label provenance.
- Steer packs: Singapore retail investment ad pre-send, Singapore recommendation evidence (shadow), UK consumer-credit promotion (sandbox). DLP separate; NER remains HOLD/local_rules. Runtime checks never establish legal compliance or suitability.

## Launch graph

Atman: package/CLI + atomic transfer + unique sessions → auth/reconnect → remote execution → OSS surface and coherent app → honest metrics/acceptance → exact-SHA Vercel deploy.

Steer: sealed corpus + correct metrics → measured per-pack run → product/README surface → exact-SHA deploy → independent launch verdict.

Public sites: https://atman-xi.vercel.app/ and https://steer-mauve.vercel.app/. They are not evidence that the final app or final measured pack release has shipped.

## Pending operator actions

1. Complete the Cursor CLI browser login, then verify `agent status` before restarting authenticated workers.
2. Steer PR #179 is a reviewed documentation contract for remote permissions and delivery states. Automatic approval review rejected its merge as potentially security-sensitive. Leave it unmerged until explicit user approval; do not use another route to merge it.

Keep each wake bounded to one outcome. Only add launch blockers that can falsify a shipped claim or break the launch path; route other debt after release.
