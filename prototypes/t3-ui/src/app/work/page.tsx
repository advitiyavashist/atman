import { createContext } from "@/server/trpc";
import { appRouter } from "@/server/router";
import { Freshness } from "@/components/Freshness";

export const dynamic = "force-dynamic";

export default async function WorkPage() {
  const data = await appRouter.createCaller(createContext()).work();
  return (
    <main>
      <Freshness freshness={data.freshness} />
      <h2 className="text-2xl font-semibold">Work graph</h2>
      <p className="text-sm text-mute" data-testid="work-counts">
        verified done {data.counts.doneVerified} · unverified done {data.counts.unverifiedDone} ·
        review {data.counts.review} · working {data.counts.working}
      </p>
      <ul className="mt-4 space-y-3">
        {data.nodes.map((n) => (
          <li key={n.id} className="border border-rule p-3" data-testid={`node-${n.id}`} data-phase={n.phase}>
            <div className="flex justify-between gap-2">
              <strong>{n.id} {n.title}</strong>
              <span className="text-xs uppercase text-mute">{n.phase}</span>
            </div>
            {n.review.label && (
              <p data-testid={`review-${n.id}`} data-review-kind={n.review.kind} className="text-sm">
                {n.review.label}
              </p>
            )}
            {!n.review.label && n.evidence && (
              <p data-testid={`evidence-${n.id}`} className="text-sm text-mute">{n.evidence}</p>
            )}
          </li>
        ))}
      </ul>
    </main>
  );
}
