import type { ReactNode } from "react";
import Link from "next/link";

export function Shell({ children }: { children: ReactNode }) {
  return (
    <div className="mx-auto max-w-3xl px-4 py-6">
      <header className="mb-6 border-b border-rule pb-4">
        <p className="text-xs uppercase tracking-wide text-mute">T-1019 throwaway · not the launch UI</p>
        <h1 className="text-xl font-semibold">Atman T3 prototype</h1>
        <p className="text-sm text-mute">Same board files as <code>tickets ui</code>. Honesty judged once in tRPC.</p>
        <nav className="mt-3 flex gap-4 text-sm">
          <Link className="underline" href="/">Objective</Link>
          <Link className="underline" href="/work">Work</Link>
          <Link className="underline" href="/team">Team</Link>
        </nav>
      </header>
      {children}
    </div>
  );
}
