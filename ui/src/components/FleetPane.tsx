/**
 * FLEET — this project's seats, from `GET /api/v1/board` (`agents[]`).
 *
 * State, harness, lifecycle, wake mode, limit, auth and reachability, per seat
 * as `seat@project`. A seat whose harness the board never recorded reads
 * `unknown`: the snapshot no longer defaults it to a provider, and this screen
 * would show the honest value either way.
 *
 * Read-only, like the CLI's own view. Recovery is a command, shown to copy.
 */

import type { Board } from "../api/types";
import { UNKNOWN } from "../lib/format";
import { usageLine } from "../lib/map";
import { Chip, Command, Failure, Missing } from "./bits";

export function FleetPane({ board, error, project }: { board: Board | null; error: unknown; project: string }) {
  if (error) return <Failure what="The fleet" error={error} />;
  if (!board) return <p className="muted">Reading the fleet…</p>;
  return (
    <section className="pane pane-fleet" aria-label="Fleet">
      <header className="pane-head">
        <h2>Fleet</h2>
        <span className="muted">{board.agents.length} seats</span>
      </header>
      <p className="muted">
        The harness here is the seat's current value in the board's workforce record, not a stamp on a run or a
        post. A post's badge (in the chat) is the one that says whether it was recorded at the time.
      </p>

      {board.provider_usage.length ? (
        <ul className="usage" data-testid="fleet-usage">
          {board.provider_usage.map((u, i) => {
            // provider_usage rows in the snapshot carry no checked_at, so the
            // age is unknown here and says so rather than implying "now".
            const line = usageLine({
              provider: u.provider,
              status: "",
              level: u.level,
              text: u.text,
              remaining_pct: u.remaining_pct,
              reset: "",
              checked_at: "",
              age: "age unknown",
            });
            return (
              <li key={i}>
                <span className="usage-provider">{line.provider}</span>
                <span className={line.headline.includes(UNKNOWN) ? "missing" : ""}>{line.headline}</span>
                <span className="missing">{line.age}</span>
                {u.text ? <span className="muted">{u.text}</span> : null}
              </li>
            );
          })}
        </ul>
      ) : null}

      {board.agents.length === 0 ? (
        <Missing what="no seats registered on this board" />
      ) : (
        <ul className="seats" data-testid="seats">
          {board.agents.map((a) => {
            const recovery = (a.auth_surface as { recovery?: { cmd?: string } }).recovery?.cmd || "";
            return (
              <li key={a.name} className="seat">
                <span className="seat-name">
                  {a.name}@{project}
                </span>
                <Chip tone={a.state === "DOWN" ? "blocked" : "neutral"}>{a.state || UNKNOWN}</Chip>
                <span className="harness">{a.harness || UNKNOWN}</span>
                <span className="muted">{a.lifecycle || <Missing />}</span>
                <span className="muted">wake {a.wake_mode || UNKNOWN}</span>
                <span className="muted">adapter {a.adapter_state || UNKNOWN}</span>
                <span className={a.reachable ? "muted" : "missing"}>{a.reachable ? "reachable" : "not reachable"}</span>
                {a.limit ? <Chip tone="blocked">limited until {a.limit_until || UNKNOWN}</Chip> : null}
                <span className={a.auth_surface.state === "ok" ? "muted" : "missing"}>
                  auth {a.auth_surface.label || a.auth_surface.state || UNKNOWN}
                </span>
                {recovery ? <Command cmd={recovery} /> : null}
              </li>
            );
          })}
        </ul>
      )}
    </section>
  );
}
