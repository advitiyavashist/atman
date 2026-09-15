import { createContext } from "@/server/trpc";
import { appRouter } from "@/server/router";
import { Freshness } from "@/components/Freshness";

export const dynamic = "force-dynamic";

export default async function ObjectivePage() {
  const data = await appRouter.createCaller(createContext()).objective();
  const o = data.objective;
  return (
    <main>
      <Freshness freshness={data.freshness} />
      <p className="text-xs uppercase tracking-wide text-mute">Objective</p>
      <h2 className="text-2xl font-semibold" data-testid="objective-text">{o.text}</h2>
      <p className="text-sm text-mute" data-testid="objective-state">
        {o.state}
        {o.exitCriterion ? ` · Done when: ${o.exitCriterion}` : ""}
        {o.exitMissing ? " · exit criterion missing" : ""}
      </p>

      <dl className="mt-4 grid grid-cols-3 gap-2 text-sm" data-testid="objective-counts">
        <div className="border border-rule p-2">
          <dt className="text-mute">Verified done</dt>
          <dd data-testid="count-done-verified">{data.counts.doneVerified}</dd>
        </div>
        <div className="border border-rule p-2">
          <dt className="text-mute">Done, no ACCEPT</dt>
          <dd data-testid="count-unverified-done">{data.counts.unverifiedDone}</dd>
        </div>
        <div className="border border-rule p-2">
          <dt className="text-mute">In review</dt>
          <dd>{data.counts.review}</dd>
        </div>
      </dl>

      <section className="mt-6">
        <h3 className="font-medium">Finishing</h3>
        {o.finishing.length === 0 && <p className="text-sm text-mute">Nothing finishing.</p>}
        <ul>
          {o.finishing.map((row) => (
            <li key={row.id} data-testid={`finishing-${row.id}`}>
              {row.id} {row.title} · @{row.who || "unclaimed"}
            </li>
          ))}
        </ul>
      </section>
      <section className="mt-4">
        <h3 className="font-medium">Blocked</h3>
        {o.blocked.length === 0 && <p className="text-sm text-mute">Nothing blocked.</p>}
        <ul>
          {o.blocked.map((row) => (
            <li key={row.id} data-testid={`blocked-${row.id}`}>
              {row.id} {row.title} · {row.text}
            </li>
          ))}
        </ul>
      </section>
      <section className="mt-4">
        <h3 className="font-medium">Next</h3>
        {o.next ? (
          <p data-testid="next-ticket">
            {o.next.id} {o.next.title} · {o.next.evidence}
          </p>
        ) : (
          <p className="text-sm text-mute">No next ticket.</p>
        )}
      </section>
    </main>
  );
}
