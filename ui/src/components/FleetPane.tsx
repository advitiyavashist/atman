/**
 * FLEET — this project's seats, from `GET /api/v1/board` (`agents[]`).
 *
 * State, harness, lifecycle, wake mode, limit, auth and reachability, per seat
 * as `seat@project`.
 *
 * **The harness column is not evidence, and this screen says so.** This route
 * still defaults `agents[].harness` for a seat with no workforce entry, so a
 * seat whose harness was never recorded arrives here already wearing a
 * provider's name. The app cannot recover the truth from that — there is no
 * "was it recorded" flag on this route — so it refuses to vouch for the value
 * instead of dressing it up. T-1106 (#267) fixes the default; the chat's
 * per-post badge is already honest because `post.harness.recorded` exists.
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
  // A snapshot whose lists are missing is reported, not walked.
  const agents = Array.isArray(board.agents) ? board.agents : null;
  const usage = Array.isArray(board.provider_usage) ? board.provider_usage : [];
  return (
    <section className="pane pane-fleet" aria-label="Fleet">
      <header className="pane-head">
        <h2>Fleet</h2>
        <span className="muted">{agents ? `${agents.length} seats` : "seat list unreadable"}</span>
      </header>
      <p className="missing" data-testid="fleet-harness-caveat">
        Do not read the harness column as a record. This route still fills it in for a seat that has no workforce
        entry at all, so a seat whose harness was never recorded can appear here as a provider name — T-1106
        (#267) is the fix, and until it lands the app cannot tell the two apart on this screen. The per-post badge
        in the chat is the honest one: it says when a harness was not recorded.
      </p>

      {usage.length ? (
        <ul className="usage" data-testid="fleet-usage">
          {usage.map((u, i) => {
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

      {agents === null ? (
        <p className="failure" role="alert">
          This snapshot carries no seat list, so no seats are shown. That is a shape the contract does not allow,
          not a board with no seats.
        </p>
      ) : agents.length === 0 ? (
        <Missing what="no seats registered on this board" />
      ) : (
        <ul className="seats" data-testid="seats">
          {agents.map((a) => {
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
