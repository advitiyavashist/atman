/**
 * The one-screen operator view (docs/design/app-v3.md).
 *
 * Top to bottom it answers four questions: what are we trying to do
 * (Objective), what is happening now (the four counts, presence, Working),
 * what is stuck (Blocked), what do I need to decide (Needs you, with its
 * buttons). Talk sits beside it; a ticket's thread opens as a panel.
 * Everything else is behind one Details toggle.
 *
 * Every section is a read of `atm ui`'s JSON API, polled. The writes are the
 * composer (`POST /msg`, as the operator), the lead picker, and the three
 * verbs that are the CLI's own functions: accept, reject, assign. With no
 * operator configured every verb is disabled and says why.
 */

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { AtmanApi } from "./api/client";
import type { NeedsYouItem, PlanNode, Post } from "./api/types";
import { Boundary } from "./components/Boundary";
import { type ResultLine } from "./components/Buttons";
import { BlockedSection, DoneSection, NeedsYouSection, UpNextSection, WorkingSection, groupNodes } from "./components/Cards";
import { AcceptDialog, AssignDialog, CommandSheet, RejectDialog, StartSheet, assignable } from "./components/Dialogs";
import { asPost } from "./components/Message";
import { PresenceStrip } from "./components/Presence";
import { Talk } from "./components/Talk";
import { TicketPanel } from "./components/TicketPanel";
import { ActionButton } from "./components/Buttons";
import { Command, Failure } from "./components/bits";
import { ago } from "./lib/format";
import { projectWorkloadLine } from "./lib/map";
import { defaultRecipient, nodeIndex, progressOf, short, stripOf, type NeedsFilter } from "./v3/model";
import { headOf, type DialogSpec, type Screen } from "./v3/screen";
import { DONE_OPEN_KEY, storeGet, storeSet } from "./v3/store";
import { useResource } from "./state/useResource";

const POLL_MS = 8000;

function useClock(everyMs: number): Date {
  const [now, setNow] = useState(() => new Date());
  useEffect(() => {
    const id = window.setInterval(() => setNow(new Date()), everyMs);
    return () => window.clearInterval(id);
  }, [everyMs]);
  return now;
}

/** `#T-001` in the URL opens that ticket's panel (§9 decision 5); no other routing. */
function hashTicket(): string {
  const raw = typeof location === "undefined" ? "" : location.hash.replace(/^#/, "");
  return /^T-\d{1,7}$/.test(raw) ? raw : "";
}

function phone(): boolean {
  return typeof window !== "undefined" && typeof window.matchMedia === "function" && window.matchMedia("(max-width: 960px)").matches;
}

interface Kept extends ResultLine {
  /** The card as it was when the line was written; the line goes when the board redraws it. */
  phase: string;
  owner: string;
  label: string;
}

/**
 * What `main.tsx` mounts: the app inside a boundary of its own, so a throw in
 * the shell still leaves a page that says something rather than nothing.
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
  const now = useClock(30_000);
  const [project, setProject] = useState("");
  const [panel, setPanel] = useState<string>(() => hashTicket());
  const [dialog, setDialog] = useState<DialogSpec | null>(null);
  const [recipient, setRecipient] = useState<string | null>(null);
  const [about, setAbout] = useState<string | null>(null);
  const [filter, setFilter] = useState<NeedsFilter>("all");
  const [details, setDetails] = useState(false);
  const [tab, setTab] = useState<"board" | "talk">("board");
  const [doneOpen, setDoneOpen] = useState<boolean>(() => storeGet<boolean>(DONE_OPEN_KEY, false));
  const [results, setResults] = useState<Map<string, Kept>>(() => new Map());
  const [focusToken, setFocusToken] = useState(0);
  const [older, setOlder] = useState<{ key: string; posts: Post[] }>({ key: "", posts: [] });
  const seenTalk = useRef("");
  const seenNeeds = useRef(-1);

  const session = useResource(() => api.session(), [api]);
  const projects = useResource(() => api.projects(), [api], POLL_MS * 4);

  useEffect(() => {
    if (project) return;
    const slug = session.data?.project || projects.data?.current || "";
    if (slug) setProject(slug);
  }, [project, projects.data?.current, session.data?.project]);

  const on = project || undefined;
  const ready = !!project;
  const board = useResource(() => api.board(on), [api, on], POLL_MS, ready);
  const plan = useResource(() => api.plan(on), [api, on], POLL_MS, ready);
  const needsYou = useResource(() => api.needsYou(on), [api, on], POLL_MS, ready);

  // Records are only ever shown under the project they were read from.
  const boardData = board.data && (!board.data.project || board.data.project === project) ? board.data : null;
  const planData = plan.data && (!plan.data.project || plan.data.project === project) ? plan.data : null;
  const needsData = needsYou.data;

  const operator = session.data?.operator ?? boardData?.app?.operator ?? "";
  const operatorNote = session.data?.operator_note ?? boardData?.app?.operator_note ?? "";
  const readOnly = !operator;
  const lead = boardData?.app?.lead || "";
  const agents = Array.isArray(boardData?.agents) ? boardData.agents : null;
  const nodesList: PlanNode[] = Array.isArray(planData?.nodes) ? planData.nodes : [];
  const nodes = useMemo(() => nodeIndex(nodesList), [nodesList]);

  const talkWith = recipient ?? defaultRecipient(lead, agents || [], nodesList);
  const talkSeat = talkWith === "everyone" ? "" : talkWith;
  const thread = useResource(() => api.thread({ project: on, with: talkSeat }), [api, on, talkSeat], POLL_MS, ready && !!talkSeat);
  const threadData = thread.data && (!thread.data.project || thread.data.project === project) && thread.data.with === talkSeat ? thread.data : null;
  // The snapshot window is newest first; the thread reads oldest first, newest visible.
  const broadcast = useMemo(
    () => (Array.isArray(boardData?.messages) ? boardData.messages.map(asPost).sort((a, b) => (a.at < b.at ? -1 : a.at > b.at ? 1 : 0)) : null),
    [boardData],
  );

  const panelTicket = useResource(() => api.ticket(panel, on), [api, on, panel], POLL_MS, ready && !!panel);
  const panelThread = useResource(() => api.thread({ project: on, re: panel }), [api, on, panel], POLL_MS, ready && !!panel);
  const ticketData = panelTicket.data && panelTicket.data.id === panel && (!panelTicket.data.project || panelTicket.data.project === project) ? panelTicket.data : null;
  const aboutData = panelThread.data && panelThread.data.re === panel ? panelThread.data.messages : null;

  // The about control defaults to the ticket the recipient is running (§5.6).
  const runningOf = (seat: string) => nodesList.find((n) => n.owner === seat && n.phase === "working")?.id || "";
  const aboutId = about ?? (talkSeat ? runningOf(talkSeat) : "");

  useEffect(() => {
    const onHash = () => setPanel(hashTicket());
    window.addEventListener("hashchange", onHash);
    return () => window.removeEventListener("hashchange", onHash);
  }, []);

  // A result line stays until the next board read redraws the card (§6.1).
  useEffect(() => {
    if (!planData && !needsData) return;
    setResults((prev) => {
      let changed = false;
      const next = new Map(prev);
      for (const [id, kept] of prev) {
        const node = nodes.get(id);
        if (node) {
          if (node.phase !== kept.phase || (node.owner || "") !== kept.owner || (node.review?.label || "") !== kept.label) {
            next.delete(id);
            changed = true;
          }
        } else if (needsData && !needsData.items.some((it) => it.id === id)) {
          next.delete(id);
          changed = true;
        }
      }
      return changed ? next : prev;
    });
  }, [planData, needsData, nodes]);

  const open = useCallback((id: string) => {
    setPanel(id);
    if (typeof location !== "undefined") location.hash = id;
  }, []);
  const close = useCallback(() => {
    setPanel("");
    if (typeof location !== "undefined" && location.hash) history.replaceState(null, "", location.pathname + location.search);
  }, []);
  const message = useCallback((to: string, re: string) => {
    setPanel("");
    setRecipient(to || "everyone");
    setAbout(re || "");
    setTab("talk");
    setFocusToken((n) => n + 1);
  }, []);

  function keep(id: string, line: ResultLine) {
    const node = nodes.get(id);
    setResults((prev) => new Map(prev).set(id, { ...line, phase: node?.phase || "", owner: node?.owner || "", label: node?.review?.label || "" }));
  }

  /**
   * After a verb runs, the result line stands in for the buttons until the
   * next board read redraws the card in its new state (§6.1). That read is
   * the regular poll, not a forced one: the line is what the API returned,
   * and the operator gets to see it. The open panel re-reads at once, since
   * its verdict list is the record the line summarises.
   */
  function afterAction() {
    if (panel) {
      panelTicket.reload();
      panelThread.reload();
    }
  }

  const checkHead = (id: string) => async () => (await api.ticket(id, on)).review.head.toLowerCase();

  async function doAccept(id: string, head: string, notes: string) {
    const res = await api.accept(id, head, notes, on);
    keep(id, { text: `Accepted · by ${res.by}`, proof: short(res.sha) });
    setDialog(null);
    afterAction();
  }
  async function doReject(id: string, head: string, reason: string) {
    const res = await api.reject(id, head, reason, on);
    keep(id, { text: `Rejected · by ${res.by}`, proof: short(res.sha), tail: res.returned_to ? `back with ${res.returned_to}` : "" });
    setDialog(null);
    afterAction();
  }
  async function doAssign(id: string, seat: string, why: string) {
    const res = await api.assign(id, seat, why, on);
    const reserved = res.changed.some((c) => c.startsWith("reserved for"));
    keep(id, { text: `${reserved ? "Reserved" : "Assigned"} · ${reserved ? "for" : "to"} ${res.owner || seat} · by ${res.by}` });
    setDialog(null);
    afterAction();
  }
  async function send(to: string, re: string, text: string) {
    await api.postMessage({ from: operator, text, to: to === "everyone" ? undefined : to, re: re || undefined });
    thread.reload();
    board.reload();
    if (panel) panelThread.reload();
    needsYou.reload();
    return { to, about: re, posted: "posted" };
  }
  async function setLead(seat: string) {
    await api.setLead(seat, on);
    board.reload();
  }

  const screen: Screen = {
    project,
    operator,
    readOnly,
    nodes,
    agents: agents || [],
    details,
    now,
    open,
    message,
    act: setDialog,
    results,
  };

  // Phone: the segment shows an unread dot for the side that is hidden.
  const latest = (talkSeat ? threadData?.messages : broadcast)?.slice(-1)[0];
  const latestKey = latest ? `${latest.id}|${latest.at}` : "";
  const latestOther = !!latest && latest.from.toLowerCase() !== operator.toLowerCase();
  useEffect(() => {
    if (tab === "talk") seenTalk.current = latestKey;
  }, [tab, latestKey]);
  useEffect(() => {
    if (tab === "board" && needsData) seenNeeds.current = needsData.count;
  }, [tab, needsData]);
  const talkDot = tab === "board" && latestOther && seenTalk.current !== latestKey;
  const boardDot = tab === "talk" && !!needsData && seenNeeds.current >= 0 && needsData.count > seenNeeds.current;

  const failedReads = [
    board.error ? "board" : "",
    plan.error ? "plan" : "",
    needsYou.error ? "needs you" : "",
    talkSeat && thread.error ? "thread" : "",
    panel && panelTicket.error ? "ticket" : "",
  ].filter(Boolean);

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
        <p>Nothing below this would be real, so nothing is drawn. Start the board server, then reload. From a dev server it also has to be told this origin:</p>
        <Command cmd="atm ui --dev-origin http://localhost:5173" />
        <p className="muted">Reading {api.origin.api || "(no API base)"}.</p>
      </main>
    );
  }

  const objective = (planData?.objective || {}) as Record<string, unknown>;
  const objText = String(objective.text || "").trim();
  const objExit = String(objective.exit_criterion || "").trim();
  const objClosed = String(objective.state || "") === "closed";
  const progress = planData ? progressOf(planData) : null;
  const strip = planData ? stripOf(planData, needsData?.count ?? 0) : null;
  const groups = groupNodes(nodesList);
  const items: NeedsYouItem[] | null = needsData ? needsData.items : needsYou.error ? null : [];
  const boardEmpty = !!planData && planData.available && nodesList.length === 0;
  const projectRows = Array.isArray(projects.data?.projects) ? projects.data.projects : [];
  const youLine = (testId?: string) =>
    operator ? (
      <>
        You are <b>{operator}</b>
      </>
    ) : (
      <span className="readonly" data-testid={testId}>
        Read-only. Start with <code>atm ui --operator &lt;name&gt;</code> to act.
        {operatorNote ? <span className="sr-only"> {operatorNote}</span> : null}
      </span>
    );
  const currentProject = projectRows.find((p) => p.slug === project);
  const workload = currentProject ? projectWorkloadLine(currentProject.counts) : null;

  const dialogNode = dialog && "id" in dialog && dialog.id ? nodes.get(dialog.id) || null : null;

  return (
    <div className={`app${details ? " show-details" : ""}`} data-testid="app">
      <header className="top">
        <span className="wordmark">atman</span>
        <span className="project" title="Project switcher: every board in ~/.config/atman/board.json">
          <label className="sr-only" htmlFor="project-switch">
            Project
          </label>
          <select
            id="project-switch"
            value={project}
            onChange={(e) => {
              setProject(e.target.value);
              setPanel("");
              setRecipient(null);
              setAbout(null);
              setResults(new Map());
              setOlder({ key: "", posts: [] });
            }}
            data-testid="project-switch"
          >
            {projectRows.length ? null : <option value={project}>{project || "…"}</option>}
            {projectRows.map((p) => {
              const line = projectWorkloadLine(p.counts);
              return (
                <option key={p.slug} value={p.slug} title={line || undefined}>
                  {p.slug}
                  {p.lead ? ` · lead ${p.lead}` : ""}
                </option>
              );
            })}
          </select>
          <span className="chev" aria-hidden="true">
            ▾
          </span>
        </span>
        {workload ? (
          <span className="project-count muted" data-testid="project-count" title="this board's workload, by the plan's words">
            {workload}
          </span>
        ) : null}
        {projects.error && !projects.data ? <span className="missing">projects unreadable</span> : null}
        <span className="spacer" />
        <span className="you" data-testid="you">
          {youLine("read-only")}
        </span>
        <button type="button" className="switch" role="switch" aria-checked={details} onClick={() => setDetails((d) => !d)} title="Show paths, transcripts, receipts, limits and phases">
          <i aria-hidden="true" />
          <span>{details ? "Details on" : "Details"}</span>
        </button>
      </header>

      <div className="objective" data-testid="objective">
        <div className="row">
          <div className="grow">
            {!planData && plan.error ? (
              <Failure what="The plan" error={plan.error} />
            ) : !planData ? (
              <h1 className="quiet">Reading the board…</h1>
            ) : !objText ? (
              <>
                <h1 className="quiet">No objective yet. Say what this board is for.</h1>
                <div className="actions">
                  <ActionButton v={{ label: "Set objective…", onClick: () => setDialog({ kind: "objective" }), kind: "" }} readOnly={readOnly} />
                </div>
              </>
            ) : (
              <>
                <h1>{objText}</h1>
                {objClosed ? (
                  <p className="when">Objective closed{objective.closed_at ? ` ${ago(String(objective.closed_at))}` : ""}.</p>
                ) : objExit ? (
                  <p className="when">Done when {objExit}</p>
                ) : (
                  <p className="when warn">
                    No "done when" set yet.
                    <span className="details inline muted">
                      {" "}
                      · <code>atm objective --exit "…"</code>
                    </span>
                  </p>
                )}
              </>
            )}
            <p className="you-mobile">{youLine()}</p>
          </div>
          {progress && objText ? (
            <div className="progress" data-testid="progress">
              <span className="n">
                {progress.accepted} of {progress.total} accepted
              </span>
              {progress.unaccepted ? <span className="muted"> · {progress.unaccepted} done, not accepted</span> : null}
              <div className="bar" aria-hidden="true">
                <i style={{ width: `${progress.total ? Math.round((100 * progress.accepted) / progress.total) : 0}%` }} />
              </div>
            </div>
          ) : null}
        </div>
      </div>

      <div className="layout" data-tab={tab}>
        <div className="seg" role="tablist" aria-label="Board or Talk">
          <button type="button" role="tab" aria-selected={tab === "board"} aria-pressed={tab === "board"} onClick={() => setTab("board")}>
            Board{boardDot ? <span className="unread" aria-label="new needs-you item" /> : null}
          </button>
          <button type="button" role="tab" aria-selected={tab === "talk"} aria-pressed={tab === "talk"} onClick={() => setTab("talk")}>
            Talk{talkDot ? <span className="unread" aria-label="new message" /> : null}
          </button>
        </div>

        <main className="main" aria-label="Board">
          <nav className="strip" aria-label="Now" data-testid="strip">
            {strip ? (
              (
                [
                  ["working", strip.working, "working"],
                  ["needs", strip.needs, "needs you"],
                  ["blocked", strip.blocked, "blocked"],
                  ["done", strip.done, "done"],
                ] as Array<[string, number, string]>
              ).map(([k, n, label]) => (
                <a key={k} className={`pill ${k}${n ? "" : " zero"}${k === "needs" && n ? " hot" : ""}`} href={`#sec-${k}`} onClick={(e) => { e.preventDefault(); document.getElementById(`sec-${k}`)?.scrollIntoView({ block: "start" }); }} data-testid={`pill-${k}`}>
                  <i aria-hidden="true" />
                  {n} {label}
                  {k === "done" && strip.unaccepted ? <span className="muted"> · {strip.unaccepted} not accepted</span> : null}
                </a>
              ))
            ) : (
              <span className="pill unavailable" data-testid="pill-unavailable">
                <i aria-hidden="true" />
                Board unavailable
              </span>
            )}
          </nav>
          <PresenceStrip agents={agents} nodes={nodesList} operator={operator} details={details} onTalk={(seat) => message(seat, "")} />
          <p className="fresh muted" data-testid="freshness">
            <span className="details inline">
              {board.readAt ? `Board read ${ago(board.readAt.toISOString(), now)}` : "reading the board…"}
              {boardData?.generated ? ` · plan generated ${boardData.generated}` : ""}
              {projectRows.find((p) => p.slug === project)?.board ? ` · ${projectRows.find((p) => p.slug === project)!.board}` : ""}
              {operator ? ` · operator ${operator}` : " · no operator"}
            </span>
            {failedReads.length ? <span className="missing"> · last read failed: {failedReads.join(", ")}</span> : null}
            {!planData && !plan.error ? <span className="details inline"> · reading the plan…</span> : null}
          </p>

          {planData && !planData.available ? (
            <Failure what="The plan" error={new Error("the board's Work payload could not be built; nothing is drawn in its place")} />
          ) : null}
          {boardEmpty ? (
            <div className="empty" data-testid="board-empty">
              <span className="grow">
                This board has no tickets. Add work with <code>atm plan</code> or <code>atm create</code>.
              </span>
              <ActionButton v={{ label: "Create ticket…", onClick: () => setDialog({ kind: "create" }), kind: "" }} readOnly={readOnly} />
            </div>
          ) : planData ? (
            <Boundary what="The sections">
              <NeedsYouSection items={items} reviews={groups.needs} filter={filter} onFilter={setFilter} s={screen} />
              <WorkingSection nodes={groups.working} s={screen} />
              <BlockedSection nodes={groups.blocked} s={screen} />
              <UpNextSection nodes={groups.next} s={screen} />
              <DoneSection
                nodes={groups.done}
                s={screen}
                open={doneOpen}
                onToggle={() => {
                  setDoneOpen((o) => {
                    storeSet(DONE_OPEN_KEY, !o);
                    return !o;
                  });
                }}
              />
            </Boundary>
          ) : !plan.error ? (
            <p className="muted">Reading the plan…</p>
          ) : null}
        </main>

        <Boundary what="Talk">
          <Talk
            s={screen}
            recipient={talkWith}
            onRecipient={(seat) => {
              setRecipient(seat);
              setAbout(null);
              setOlder({ key: "", posts: [] });
            }}
            lead={lead}
            onSetLead={setLead}
            thread={threadData}
            threadError={thread.error}
            broadcast={broadcast}
            olderPages={older.key === `${project}|${talkSeat}` ? older.posts : []}
            onLoadOlder={(before) => {
              void api.thread({ project: on, with: talkSeat, before }).then((page) => {
                setOlder((prev) => ({ key: `${project}|${talkSeat}`, posts: [...page.messages, ...(prev.key === `${project}|${talkSeat}` ? prev.posts : [])] }));
              });
            }}
            composer={{
              about: aboutId,
              onAbout: (id) => setAbout(id),
              send: (text) => send(talkWith, aboutId, text),
              focusToken,
            }}
          />
        </Boundary>
      </div>

      {panel ? (
        <Boundary what="The ticket panel">
          <TicketPanel
            id={panel}
            node={nodes.get(panel) || null}
            ticket={ticketData}
            ticketError={ticketData ? null : panelTicket.error || (panelTicket.data && !panelTicket.loading ? new Error(`that read answered for ${panelTicket.data.id}, not ${panel}`) : null)}
            loading={panelTicket.loading}
            about={aboutData}
            aboutError={panelThread.error}
            s={screen}
            composer={{
              send: (text) => {
                const owner = (ticketData?.owner || nodes.get(panel)?.owner || "").trim();
                const to = owner && owner.toLowerCase() !== operator.toLowerCase() ? owner : "everyone";
                return send(to, panel, text);
              },
            }}
            onClose={close}
          />
        </Boundary>
      ) : null}

      {dialog?.kind === "accept" && dialogNode ? (
        <AcceptDialog node={dialogNode} s={screen} liveHead={headOf(nodes.get(dialogNode.id)).toLowerCase()} checkHead={checkHead(dialogNode.id)} submit={(head, notes) => doAccept(dialogNode.id, head, notes)} onClose={() => setDialog(null)} />
      ) : null}
      {dialog?.kind === "reject" && dialogNode ? (
        <RejectDialog node={dialogNode} s={screen} liveHead={headOf(nodes.get(dialogNode.id)).toLowerCase()} checkHead={checkHead(dialogNode.id)} submit={(head, reason) => doReject(dialogNode.id, head, reason)} onClose={() => setDialog(null)} />
      ) : null}
      {dialog?.kind === "assign" ? <AssignDialog id={dialog.id} s={screen} candidates={assignable(nodesList)} submit={doAssign} onClose={() => setDialog(null)} /> : null}
      {dialog?.kind === "start" ? <StartSheet s={screen} seat={dialog.seat} onClose={() => setDialog(null)} /> : null}
      {dialog?.kind === "objective" || dialog?.kind === "create" ? <CommandSheet kind={dialog.kind} onClose={() => setDialog(null)} /> : null}
      {phone() && panel ? null : null}
    </div>
  );
}
