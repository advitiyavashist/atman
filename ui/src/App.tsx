import { useEffect, useState } from "react";
import { NavBar, VIEWS, type View } from "./components/NavBar";
import { ToastHost } from "./components/Toast";
import { Overview } from "./screens/Overview";
import { Tickets } from "./screens/Tickets";
import { Agents } from "./screens/Agents";
import { Messages } from "./screens/Messages";
import { Activity } from "./screens/Activity";
import { ConnectDialog } from "./screens/ConnectDialog";
import { MasterPanel } from "./screens/MasterPanel";
import { BoardProvider, useBoard } from "./state/BoardProvider";
import { resolveSession, type BoardSession, type SessionResolution } from "./session";
import { recoveredGapCopy } from "./copy";
import { FormationMark } from "./components/FormationMark";

function currentViewFromHash(): View {
  const hash = window.location.hash.replace("#", "");
  return (VIEWS as readonly string[]).includes(hash) ? (hash as View) : "overview";
}

/**
 * Shown when the board's event history moved past this tab.
 *
 * `snapshot_required` is not a warning to ignore: the contract's answer to a
 * cursor that aged out is to re-read the screen, not to keep listening
 * (docs/api-notes.md #6, retention 1000 events). The provider does the re-read;
 * this says so, because an operator who looked away deserves to know the board
 * jumped rather than moved.
 */
function GapNotice() {
  const { recoveredGap } = useBoard();
  if (!recoveredGap) return null;
  return (
    <p className="notice" role="status" data-testid="gap-notice">
      {recoveredGapCopy(recoveredGap.at)}
    </p>
  );
}

function BoardShell({ session }: { session: BoardSession }) {
  const [view, setView] = useState<View>(currentViewFromHash());
  const [theme, setTheme] = useState<"dark" | "light">(() => {
    try {
      return (localStorage.getItem("ticket-board-ui-theme") as "dark" | "light") ?? "dark";
    } catch {
      return "dark";
    }
  });
  const [connectOpen, setConnectOpen] = useState(false);
  const [masterPanelOpen, setMasterPanelOpen] = useState(false);

  useEffect(() => {
    const onHashChange = () => setView(currentViewFromHash());
    window.addEventListener("hashchange", onHashChange);
    return () => window.removeEventListener("hashchange", onHashChange);
  }, []);

  useEffect(() => {
    document.body.classList.toggle("light", theme === "light");
    try {
      localStorage.setItem("ticket-board-ui-theme", theme);
    } catch {
      // Private browsing or storage disabled — theme just resets next visit.
    }
  }, [theme]);

  return (
    <>
      <header className="app-header">
        <a className="brand" href="#overview">
          <FormationMark />
          <span className="wordmark">atman</span>
        </a>
        <span className="tag" data-testid="board-label">
          {session.boardLabel}
        </span>
        <span className="spacer" />
        <button
          type="button"
          aria-pressed={theme === "light"}
          onClick={() => setTheme((t) => (t === "dark" ? "light" : "dark"))}
        >
          {theme === "dark" ? "Light mode" : "Dark mode"}
        </button>
        <button type="button" className="primary" onClick={() => setConnectOpen(true)}>
          Connect agent
        </button>
      </header>

      <div className="app-shell">
        <NavBar current={view} />
        <main className="screen">
          <GapNotice />
          {view === "overview" && (
            <Overview
              onNavigate={(v) => (window.location.hash = v)}
              onOpenMasterPanel={() => setMasterPanelOpen(true)}
            />
          )}
          {view === "tickets" && <Tickets />}
          {view === "agents" && <Agents onConnect={() => setConnectOpen(true)} />}
          {view === "messages" && <Messages />}
          {view === "activity" && <Activity />}
        </main>
      </div>

      <ConnectDialog open={connectOpen} onClose={() => setConnectOpen(false)} />
      <MasterPanel open={masterPanelOpen} onClose={() => setMasterPanelOpen(false)} />
      <ToastHost />
    </>
  );
}

/**
 * No session, no board — and no pretending otherwise.
 *
 * An unconfigured dashboard renders this instead of empty screens. An empty
 * board and an unreachable one look identical if you render zeros for both,
 * and "0 tickets, nothing needs attention" is a dangerous thing to show an
 * operator whose board they cannot reach.
 */
function Unconfigured({ reason }: { reason: string }) {
  return (
    <div className="app-shell">
      <main className="screen">
        <div className="heading">
          <h1>Not connected</h1>
        </div>
        <section className="card" role="alert" data-testid="unconfigured">
          <p>{reason}</p>
          <p className="tag">
            No board data is shown here. This is not an empty board — it is a board this tab cannot reach.
          </p>
        </section>
      </main>
    </div>
  );
}

export function App({
  /** Test seam: skip discovery and use a known session. */
  session: provided,
  reconnectDelayMs,
}: {
  session?: BoardSession;
  reconnectDelayMs?: number;
} = {}) {
  const [resolution, setResolution] = useState<SessionResolution | null>(
    provided ? { status: "ready", session: provided } : null,
  );

  useEffect(() => {
    if (provided) return;
    let cancelled = false;
    resolveSession().then((result) => {
      if (!cancelled) setResolution(result);
    });
    return () => {
      cancelled = true;
    };
  }, [provided]);

  if (!resolution) {
    return (
      <div className="app-shell">
        <main className="screen">
          <p data-testid="session-loading">Looking for a board session…</p>
        </main>
      </div>
    );
  }

  if (resolution.status === "unconfigured") return <Unconfigured reason={resolution.reason} />;

  return (
    <BoardProvider session={resolution.session} reconnectDelayMs={reconnectDelayMs}>
      <BoardShell session={resolution.session} />
    </BoardProvider>
  );
}
