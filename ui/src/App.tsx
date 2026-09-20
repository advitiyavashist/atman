/**
 * The shell: sidebar, chat with the lead on the left, the plan in the centre,
 * the drill-down on the right.
 *
 * At 1440px all three read at once, which is the whole point of the app — you
 * talk to the lead while the plan is in view and a step's evidence is one
 * click away. Under 900px the drill-down becomes a sheet over the layout;
 * at 390px the sidebar is a top bar and chat and the centre view are two tabs.
 *
 * Every screen here is a read of `atm ui`'s JSON API, polled. The one write
 * this phase has is the composer (`POST /msg`, as the operator) and the lead
 * picker (`POST /api/v1/lead`). With no operator configured the app says so
 * and stays read-only rather than failing at the moment you press Send.
 */

import { useEffect, useMemo, useState } from "react";
import { AtmanApi } from "./api/client";
import { FleetPane } from "./components/FleetPane";
import { LeadPane } from "./components/LeadPane";
import { NeedsYouPane } from "./components/NeedsYouPane";
import { PlanPane } from "./components/PlanPane";
import { RunsPane } from "./components/RunsPane";
import { Boundary } from "./components/Boundary";
import { Sidebar, VIEWS, type ViewName } from "./components/Sidebar";
import { TicketPane } from "./components/TicketPane";
import { Command, Failure } from "./components/bits";
import { ago } from "./lib/format";
import { ticketIdsOf } from "./lib/map";
import { useResource } from "./state/useResource";

const POLL_MS = 8000;

function useClock(): string {
  const [now, setNow] = useState(() => new Date());
  useEffect(() => {
    const id = window.setInterval(() => setNow(new Date()), 1000);
    return () => window.clearInterval(id);
  }, []);
  return now.toLocaleTimeString();
}

/**
 * The URL hash is `#<view>` or `#<view>/<ticket>`, so a view and an open
 * drill-down can both be linked, reloaded and shared with whoever is looking
 * at the same board.
 */
function routeFromHash(): { view: ViewName; ticket: string } {
  const raw = (typeof location === "undefined" ? "" : location.hash.replace(/^#/, "")).split("/");
  const view = (VIEWS as readonly string[]).includes(raw[0]) ? (raw[0] as ViewName) : "lead";
  const ticket = /^T-\d{1,7}$/.test(raw[1] || "") ? raw[1] : "";
  return { view, ticket };
}

/**
 * What `main.tsx` mounts: the app inside a boundary of its own.
 *
 * The three column boundaries cannot catch a throw in the shell above them,
 * and without this one such a throw leaves `#root` empty with nothing but a
 * console line — the least honest failure this app could have. This one is the
 * floor: whatever happens, the page says something.
 */
export function Root({ api }: { api?: AtmanApi } = {}) {
  return (
    <Boundary what="The app">
      <App api={api} />
    </Boundary>
  );
}

export function App({ api: injected }: { api?: AtmanApi } = {}) {
  const api = useMemo(() => injected ?? new AtmanApi(), [injected]);
  const clock = useClock();
  const [view, setView] = useState<ViewName>(() => routeFromHash().view);
  const [project, setProject] = useState<string>("");
  const [selected, setSelected] = useState<string>(() => routeFromHash().ticket);
  const [tab, setTab] = useState<"chat" | "centre">("chat");

  const session = useResource(() => api.session(), [api]);
  const projects = useResource(() => api.projects(), [api], POLL_MS * 4);

  // The started board is the default; the operator can switch from the sidebar.
  useEffect(() => {
    if (project) return;
    const slug = session.data?.project || projects.data?.current || "";
    if (slug) setProject(slug);
  }, [project, projects.data?.current, session.data?.project]);

  const on = project || undefined;
  const board = useResource(() => api.board(on), [api, on], POLL_MS);
  const plan = useResource(() => api.plan(on), [api, on], POLL_MS);
  const lead = useResource(() => api.lead(on), [api, on], POLL_MS);
  const leadSeat = lead.data?.lead || "";
  const thread = useResource(
    () => api.thread({ project: on, with: leadSeat || undefined }),
    [api, on, leadSeat],
    POLL_MS,
  );
  const needsYou = useResource(() => api.needsYou(on), [api, on], POLL_MS);
  const ticket = useResource(
    () => (selected ? api.ticket(selected, on) : Promise.resolve(null)),
    [api, on, selected],
    selected ? POLL_MS : 0,
  );

  useEffect(() => {
    const onHash = () => {
      const route = routeFromHash();
      setView(route.view);
      setSelected(route.ticket);
    };
    window.addEventListener("hashchange", onHash);
    return () => window.removeEventListener("hashchange", onHash);
  }, []);

  function goTo(v: ViewName, ticket: string) {
    setView(v);
    setSelected(ticket);
    if (typeof location !== "undefined") location.hash = ticket ? `${v}/${ticket}` : v;
  }

  function goView(v: ViewName) {
    goTo(v, selected);
    setTab(v === "lead" ? "chat" : "centre");
  }

  function openTicket(id: string) {
    goTo(view, id);
  }

  // Shapes are checked before they are walked, because everything from here
  // up to <Root> is outside the per-column boundaries: a throw here blanks the
  // page, and a blank page looks exactly like an empty board.
  /**
   * A failed refresh does not erase what is already on screen.
   *
   * A pane is handed its error only when it has nothing to show instead:
   * otherwise the last good read stays up and the freshness line carries the
   * failure, because wiping a screen an operator is reading is both less
   * useful and less honest than saying "this is what we last saw, and the last
   * read failed".
   */
  const stale = (r: { data: unknown; error: unknown }) => (r.data ? null : r.error);
  const failedReads = [
    board.error ? "board" : "",
    plan.error ? "plan" : "",
    lead.error ? "lead" : "",
    thread.error ? "thread" : "",
    needsYou.error ? "needs you" : "",
  ].filter(Boolean);

  const seats = Array.isArray(board.data?.agents) ? board.data.agents : null;
  const runGroups = Array.isArray(board.data?.agent_map?.groups) ? board.data.agent_map.groups : null;
  const knownTickets = useMemo(
    () => ticketIdsOf(Array.isArray(plan.data?.nodes) ? plan.data.nodes : []),
    [plan.data],
  );
  const operator = session.data?.operator ?? lead.data?.operator ?? "";
  const operatorNote = session.data?.operator_note ?? lead.data?.operator_note ?? "";

  if (api.origin.refused) {
    return (
      <main className="boot">
        <h1>atman</h1>
        <Failure what="The API origin" error={new Error(api.origin.refused)} />
        <p>Point the app at the local board server, or open the bundle it serves.</p>
        <Command cmd="atm ui --dev-origin http://localhost:5173" />
      </main>
    );
  }

  if (session.error) {
    return (
      <main className="boot">
        <h1>atman</h1>
        <Failure what="The session" error={session.error} />
        <p>
          Nothing below this would be real, so nothing is drawn. Start the board server, then reload. From a dev
          server it also has to be told this origin:
        </p>
        <Command cmd="atm ui --dev-origin http://localhost:5173" />
        <p className="muted">Reading {api.origin.api || "(no API base)"}.</p>
      </main>
    );
  }

  const centre =
    view === "needs-you" ? (
      <NeedsYouPane data={needsYou.data} error={stale(needsYou)} onTicket={openTicket} />
    ) : view === "fleet" ? (
      <FleetPane board={board.data} error={stale(board)} project={project} />
    ) : view === "runs" ? (
      <RunsPane board={board.data} error={stale(board)} project={project} onTicket={openTicket} />
    ) : (
      <PlanPane plan={plan.data} error={stale(plan)} project={project} selected={selected} onSelect={openTicket} />
    );

  return (
    <div className={`shell${selected ? " shell-drilled" : ""}`} data-view={view}>
      <Sidebar
        projects={projects.data}
        projectsError={projects.error}
        project={project}
        onProject={(slug) => {
          setProject(slug);
          goTo(view, "");
        }}
        view={view}
        onView={goView}
        needsYouCount={typeof needsYou.data?.count === "number" ? needsYou.data.count : null}
        seatCount={seats ? seats.length : null}
        runningCount={runGroups ? runGroups.reduce((n, g) => n + (Number(g?.running) || 0), 0) : null}
        clock={clock}
      />

      <div className="topbar">
        <div className="topbar-who">
          {operator ? (
            <span>
              you are <b>{operator}</b> (operator)
            </span>
          ) : (
            <span className="missing" data-testid="read-only">
              read-only: no operator configured
            </span>
          )}
          {operator ? null : <span className="muted"> · {operatorNote}</span>}
        </div>
        <div className="topbar-fresh muted" data-testid="freshness">
          {board.readAt ? `board read ${ago(board.readAt.toISOString())}` : "reading the board…"}
          {failedReads.length ? (
            <span className="missing"> · last read failed: {failedReads.join(", ")}</span>
          ) : null}
        </div>
        <div className="tabs" role="tablist" aria-label="Panes">
          <button
            type="button"
            role="tab"
            aria-selected={tab === "chat"}
            className={tab === "chat" ? "tab tab-on" : "tab"}
            onClick={() => setTab("chat")}
          >
            Chat
          </button>
          <button
            type="button"
            role="tab"
            aria-selected={tab === "centre"}
            className={tab === "centre" ? "tab tab-on" : "tab"}
            onClick={() => setTab("centre")}
          >
            {view === "lead" ? "Plan" : view.replace("-", " ")}
          </button>
        </div>
      </div>

      <main className="cols" data-tab={tab}>
        <div className="col col-chat">
          <Boundary what="The chat">
            <LeadPane
              api={api}
              project={project}
              lead={lead.data}
              leadError={stale(lead)}
              thread={thread.data}
              threadError={stale(thread)}
              knownTickets={knownTickets}
              onTicket={openTicket}
              onReload={() => {
                thread.reload();
                lead.reload();
              }}
            />
          </Boundary>
        </div>
        <div className="col col-centre">
          <Boundary what="This view">{centre}</Boundary>
        </div>
        {selected ? (
          <div className="col col-detail">
            <Boundary what="The ticket detail">
              <TicketPane
                ticket={ticket.data}
                error={stale(ticket)}
                loading={ticket.loading}
                onClose={() => goTo(view, "")}
                onTicket={openTicket}
              />
            </Boundary>
          </div>
        ) : null}
      </main>
    </div>
  );
}
