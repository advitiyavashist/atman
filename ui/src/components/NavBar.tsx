export const VIEWS = ["overview", "tickets", "agents", "messages", "activity"] as const;
export type View = (typeof VIEWS)[number];

const VIEW_LABEL: Record<View, string> = {
  overview: "Overview",
  tickets: "Tickets",
  agents: "Agents",
  messages: "Messages",
  activity: "Activity",
};

export function NavBar({ current }: { current: View }) {
  return (
    <nav className="main-nav" aria-label="Main">
      {VIEWS.map((v) => (
        <a key={v} href={`#${v}`} aria-current={v === current ? "page" : undefined}>
          {VIEW_LABEL[v]}
        </a>
      ))}
    </nav>
  );
}
