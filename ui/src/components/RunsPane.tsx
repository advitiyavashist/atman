/**
 * RUNS — seat runs grouped by ticket, from `GET /api/v1/board`
 * (`agent_map.groups`).
 *
 * `board.json` makes only `agent_map.groups` part of the contract and says the
 * rest of the snapshot may change, so every field of a row is treated as
 * possibly absent: a missing elapsed time or token count renders "not
 * recorded" or `unknown` rather than 0. If these rows are ever wanted as
 * contract, they need their own `$defs` in the schema (noted in the PR).
 */

import type { Board, RunGroup } from "../api/types";
import { UNKNOWN, durationLabel, exactTime, localTime } from "../lib/format";
import { runTokens, verdictChip } from "../lib/map";
import { Chip, Failure, Missing } from "./bits";

function Group({ g, project, onTicket }: { g: RunGroup; project: string; onTicket: (id: string) => void }) {
  const rows = Array.isArray(g.rows) ? g.rows : [];
  const groupTokens = runTokens({
    tokens: g.tokens ?? null,
    tokens_in: null,
    tokens_out: null,
    tokens_label: "",
  });
  return (
    <li className="run-group" data-testid="run-group">
      <div className="run-group-head">
        {g.ticket ? (
          <button type="button" className="link-ticket" onClick={() => onTicket(g.ticket as string)}>
            {g.ticket}
          </button>
        ) : (
          <Missing what="no ticket on this run group" />
        )}
        <span className="muted">{g.title || <Missing what="untitled" />}</span>
        <Chip tone={g.running ? "running" : "neutral"}>
          {g.running ? `${g.running} running` : "nothing running"}
        </Chip>
        <span className="muted">
          {rows.length} run{rows.length === 1 ? "" : "s"} · {durationLabel(g.elapsed_s ?? null)}
        </span>
        <span className={groupTokens.known ? "" : "missing"}>{groupTokens.label}</span>
        {g.tokens_unknown ? (
          <span className="missing">{g.tokens_unknown} run(s) with no token record</span>
        ) : null}
      </div>
      <ul className="runs">
        {rows.map((r, i) => {
          const tok = runTokens({
            tokens: r.tokens ?? null,
            tokens_in: r.tokens_in ?? null,
            tokens_out: r.tokens_out ?? null,
            tokens_label: "",
          });
          return (
            <li key={i} className="run">
              <span className="run-seat">
                {r.seat ? `${r.seat}@${project}` : <Missing what="seat not recorded" />}
              </span>
              <span className="harness">{r.harness || UNKNOWN}</span>
              <span className="run-role">{r.role || <Missing what="role not recorded" />}</span>
              <Chip tone={r.state === "running" ? "running" : "neutral"}>{r.state || UNKNOWN}</Chip>
              <span className="muted">{durationLabel(r.elapsed_s ?? null)}</span>
              <span className={tok.known ? "run-tokens" : "run-tokens missing"}>{tok.label}</span>
              {(() => {
                const v = verdictChip(r.verdict);
                if (!v) return null;
                return (
                  <Chip tone={v.label === "accept" ? "accepted" : "unverified"} title={v.sha ? `recorded at ${v.sha}` : undefined}>
                    {v.label}
                  </Chip>
                );
              })()}
              <time className="muted" title={exactTime(r.started)}>
                {localTime(r.started)}
              </time>
            </li>
          );
        })}
      </ul>
    </li>
  );
}

export function RunsPane({
  board,
  error,
  project,
  onTicket,
}: {
  board: Board | null;
  error: unknown;
  project: string;
  onTicket: (id: string) => void;
}) {
  if (error) return <Failure what="Runs" error={error} />;
  if (!board) return <p className="muted">Reading the runs…</p>;
  const groups = Array.isArray(board.agent_map?.groups) ? board.agent_map.groups : [];
  return (
    <section className="pane pane-runs" aria-label="Runs">
      <header className="pane-head">
        <h2>Runs</h2>
        <span className="muted">{groups.length} tickets with runs</span>
      </header>
      {board.agent_map === null ? (
        <Missing what="this board has no run map" />
      ) : groups.length === 0 ? (
        <p className="muted">No recent runs on this board.</p>
      ) : (
        <ul className="run-groups">
          {groups.map((g, i) => (
            <Group key={g.ticket || i} g={g} project={project} onTicket={onTicket} />
          ))}
        </ul>
      )}
    </section>
  );
}
