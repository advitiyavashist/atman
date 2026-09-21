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

  // Every read below is scoped to one board, so none of them fires until the
  // shell knows which board that is. Reading the started board first and the
  // chosen one a moment later would put one project's records on screen on the
  // way to another's, which is exactly the pairing this app must never make.
  const on = project || undefined;
  const ready = !!project;
  const board = useResource(() => api.board(on), [api, on], POLL_MS, ready);
  const plan = useResource(() => api.plan(on), [api, on], POLL_MS, ready);
  const lead = useResource(() => api.lead(on), [api, on], POLL_MS, ready);
  const leadSeat = lead.data?.lead || "";
  const thread = useResource(
    () => api.thread({ project: on, with: leadSeat || undefined }),
    [api, on, leadSeat],
    POLL_MS,
    ready,
  );
  const needsYou = useResource(() => api.needsYou(on), [api, on], POLL_MS, ready);
  const ticket = useResource(
    () => api.ticket(selected, on),
    [api, on, selected],
    POLL_MS,
    ready && !!selected,
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
   * Records are only ever shown under the project they were read from.
   *
   * `useResource` already withholds a value read for other deps, and this is
   * the second lock on the same door, because the failure it prevents is the
   * worst this app has: a payload from one board rendered beside another
   * board's slug. Every screen composes `seat@project` from the shell's
   * project, so one wrong pairing turns another project's work into this
   * project's work on screen. A payload whose own `project` is not the
   * selected one is therefore treated as absent, and the pane says it could
   * not be read.
   */
  function onThisProject<T extends { project?: string }>(r: { data: T | null }): T | null {
    const d = r.data;
    if (!d) return null;
    if (!project || !d.project) return d;
    return d.project === project ? d : null;
  }

  /**
   * A failed refresh does not erase what is already on screen — but only for
   * the same question.
   *
   * With data to show, the pane keeps it and the freshness line carries the
   * failure: wiping a screen an operator is reading is less useful and less
   * honest than saying "this is what we last saw, and the last read failed".
   * With nothing to show for *this* project, the pane gets the error, which is
   * what a read that failed after a project switch must do.
   */
  const stale = (data: unknown, error: unknown, mismatch = false) =>
    data ? null : error || (mismatch ? new Error(`that read was for another project, not ${project}`) : null);

  const boardData = onThisProject(board);
  const planData = onThisProject(plan);
  const leadData = onThisProject(lead);
  const threadData = onThisProject(thread);
  // needs-you carries no project of its own, so the deps gate in useResource is
  // the only thing standing between it and a mislabel; it is enough, because a
  // switch withholds the previous board's items outright.
  const needsYouData = needsYou.data;
  /** The drill-down must show the ticket that is open, or nothing. */
  const ticketData =
    ticket.data && ticket.data.id === selected && (!ticket.data.project || ticket.data.project === project)
      ? ticket.data
      : null;

  const failedReads = [
    board.error ? "board" : "",
    plan.error ? "plan" : "",
    lead.error ? "lead" : "",
    thread.error ? "thread" : "",
    needsYou.error ? "needs you" : "",
    ticket.error ? "ticket" : "",
  ].filter(Boolean);

  const seats = Array.isArray(boardData?.agents) ? boardData.agents : null;
  const runGroups = Array.isArray(boardData?.agent_map?.groups) ? boardData.agent_map.groups : null;
  const knownTickets = useMemo(
    () => ticketIdsOf(Array.isArray(planData?.nodes) ? planData.nodes : []),
    [planData],
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
      <NeedsYouPane data={needsYouData} error={stale(needsYouData, needsYou.error)} onTicket={openTicket} />
    ) : view === "fleet" ? (
      <FleetPane board={boardData} error={stale(boardData, board.error, !!board.data)} project={project} />
    ) : view === "runs" ? (
      <RunsPane board={boardData} error={stale(boardData, board.error, !!board.data)} project={project} onTicket={openTicket} />
    ) : (
      <PlanPane
        plan={planData}
        error={stale(planData, plan.error, !!plan.data)}
        project={project}
        selected={selected}
        onSelect={openTicket}
      />
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
              lead={leadData}
              leadError={stale(leadData, lead.error, !!lead.data)}
              thread={threadData}
              threadError={stale(threadData, thread.error, !!thread.data)}
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
                ticket={ticketData}
                // Whatever is open must be this ticket or an error: leaving the
                // previous ticket's evidence under a new id in the URL would
                // attribute one piece of work's runs, review and accept to
                // another.
                error={
                  ticketData
                    ? null
                    : ticket.error ||
                      (ticket.data && !ticket.loading
                        ? new Error(`that read answered for ${ticket.data.id}, not ${selected}`)
                        : null)
                }
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
