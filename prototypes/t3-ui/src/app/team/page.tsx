import { createContext } from "@/server/trpc";
import { appRouter } from "@/server/router";
import { Freshness } from "@/components/Freshness";

export const dynamic = "force-dynamic";

export default async function TeamPage() {
  const data = await appRouter.createCaller(createContext()).team();
  return (
    <main>
      <Freshness freshness={data.freshness} />
      <h2 className="text-2xl font-semibold">Team</h2>
      <ul className="mt-4 space-y-3">
        {data.members.map((m) => (
          <li
            key={m.reach.name}
            className="border border-rule p-3"
            data-testid={`agent-${m.reach.name}`}
            data-phase={m.reach.phase}
            data-connected={String(m.reach.connected)}
          >
            <div className="flex justify-between gap-2">
              <strong>@{m.reach.name}</strong>
              <span data-testid={`reach-${m.reach.name}`}>{m.reach.label}</span>
            </div>
            <p className="text-sm text-mute">{m.reach.detail}</p>
            {!m.reach.connected && m.reach.loginCmd && (
              <p className="text-sm">
                Recovery command: <code data-testid={`login-${m.reach.name}`}>{m.reach.loginCmd}</code>
              </p>
            )}
            {m.ticket && <p className="text-sm">Ticket {m.ticket} (claim ≠ reach)</p>}
          </li>
        ))}
      </ul>
    </main>
  );
}
