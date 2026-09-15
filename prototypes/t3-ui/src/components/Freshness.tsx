import type { BoardFreshness } from "@/server/honesty";

export function Freshness({ freshness }: { freshness: BoardFreshness }) {
  if (freshness.phase === "loading") {
    return <p data-testid="freshness-loading" className="text-sm text-mute">Reading the board…</p>;
  }
  if (freshness.phase === "offline") {
    return (
      <div data-testid="freshness-offline" className="mb-4 border border-bad bg-red-50 p-3 text-sm">
        <p className="font-medium">OFFLINE · {freshness.label}</p>
        <p>Work is not lost. {freshness.lastSnapshotKept ? "Last snapshot kept on screen." : "No snapshot yet."}</p>
        <p>
          Recovery is a restart, not a magic button: <code data-testid="recovery-cmd">{freshness.recoveryCmd}</code>
        </p>
      </div>
    );
  }
  return (
    <p data-testid="freshness-live" className="mb-4 text-xs uppercase tracking-wide text-ok">
      Board live · {freshness.asOf}
    </p>
  );
}
