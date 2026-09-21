/**
 * The sidebar: the project switcher and the five phase-1 views.
 *
 * A project is a board, so switching projects switches the chat, the plan, the
 * fleet and the runs together — the switcher lists what the machine registry
 * holds, with the started board first, and says where each one came from.
 *
 * Under 900px this becomes the top bar (see styles.css); the nav stays a real
 * list of buttons so it is keyboard reachable either way.
 */

import type { Projects } from "../api/types";
import { Missing } from "./bits";

export const VIEWS = ["lead", "plan", "needs-you", "fleet", "runs"] as const;
export type ViewName = (typeof VIEWS)[number];

const LABEL: Record<ViewName, string> = {
  lead: "Lead",
  plan: "Plan",
  "needs-you": "Needs you",
  fleet: "Fleet",
  runs: "Runs",
};

export function Sidebar({
  projects,
  projectsError,
  project,
  onProject,
  view,
  onView,
  needsYouCount,
  seatCount,
  runningCount,
  clock,
}: {
  projects: Projects | null;
  projectsError: unknown;
  project: string;
  onProject: (slug: string) => void;
  view: ViewName;
  onView: (v: ViewName) => void;
  needsYouCount: number | null;
  seatCount: number | null;
  runningCount: number | null;
  clock: string;
}) {
  const badge: Record<ViewName, string> = {
    lead: "",
    plan: "",
    "needs-you": needsYouCount === null ? "" : String(needsYouCount),
    fleet: seatCount === null ? "" : String(seatCount),
    runs: runningCount === null ? "" : `${runningCount} live`,
  };

  return (
    <nav className="sidebar" aria-label="Project and views">
      <div className="brand">atman</div>

      <div className="switcher">
        <label htmlFor="project-switch" className="field-label">
          project
        </label>
        {projectsError ? (
          <p className="failure" role="alert">
            The project registry could not be read.
          </p>
        ) : null}
        {projects ? (
          <>
            <select
              id="project-switch"
              value={project}
              onChange={(e) => onProject(e.target.value)}
              data-testid="project-switch"
            >
              {projects.projects.map((p) => (
                <option key={p.slug} value={p.slug}>
                  {p.slug}
                </option>
              ))}
            </select>
            <ul className="project-list">
              {projects.projects.map((p) => {
                const open = p.counts.open;
                return (
                  <li key={p.slug} className={p.slug === project ? "project-current" : ""}>
                    <button type="button" className="btn-quiet" onClick={() => onProject(p.slug)} aria-current={p.slug === project}>
                      {p.slug}
                    </button>
                    <span className="muted">
                      {open === undefined ? <Missing what="counts unavailable" /> : `${open} open`}
                    </span>
                    <span className="muted">{p.lead ? `lead ${p.lead}` : "no lead picked"}</span>
                    <span className="muted src">{p.source}</span>
                  </li>
                );
              })}
            </ul>
          </>
        ) : (
          <p className="muted">Reading projects…</p>
        )}
      </div>

      <ul className="nav">
        {VIEWS.map((v) => (
          <li key={v}>
            <button
              type="button"
              className={`nav-btn${view === v ? " nav-current" : ""}`}
              aria-current={view === v ? "page" : undefined}
              onClick={() => onView(v)}
            >
              {LABEL[v]}
              {badge[v] ? <span className="nav-badge">{badge[v]}</span> : null}
            </button>
          </li>
        ))}
      </ul>

      <div className="sidebar-foot">
        <span className="clock" aria-label="local time">
          {clock}
        </span>
      </div>
    </nav>
  );
}
