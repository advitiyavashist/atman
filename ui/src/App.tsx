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

function currentViewFromHash(): View {
  const hash = window.location.hash.replace("#", "");
  return (VIEWS as readonly string[]).includes(hash) ? (hash as View) : "overview";
}

export function App() {
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
          ↗ Ticket Board
        </a>
        <span className="fixture-badge">FIXTURE MODE · NO LIVE DATA</span>
        <span className="spacer" />
        <button
          aria-pressed={theme === "light"}
          onClick={() => setTheme((t) => (t === "dark" ? "light" : "dark"))}
        >
          {theme === "dark" ? "Light mode" : "Dark mode"}
        </button>
        <button className="primary" onClick={() => setConnectOpen(true)}>
          Connect agent
        </button>
      </header>

      <div className="app-shell">
        <NavBar current={view} />
        <main className="screen">
          {view === "overview" && (
            <Overview onNavigate={(v) => (window.location.hash = v)} onOpenMasterPanel={() => setMasterPanelOpen(true)} />
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
