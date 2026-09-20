/**
 * A stand-in for `AtmanApi` whose answers are fixtures generated from the
 * schemas.
 *
 * The component tests mount the real screens against this, so what they assert
 * is what an operator would see given a board in that shape. Nothing here
 * invents a field: each route's answer starts as `fixture("<route>.json")` and
 * a test overrides only the part it is about.
 */

import type { AtmanApi, Origin } from "../../../ui/src/api/client";
import type { Board, Lead, NeedsYou, Plan, Projects, Session, Thread, Ticket } from "../../../ui/src/api/types";
import { fixture } from "./schema";

export interface FakeParts {
  session?: Partial<Session>;
  projects?: Partial<Projects>;
  board?: Partial<Board>;
  plan?: Partial<Plan>;
  lead?: Partial<Lead>;
  thread?: Partial<Thread>;
  needsYou?: Partial<NeedsYou>;
  tickets?: Record<string, Partial<Ticket>>;
  /** Routes that should fail, so a screen's failure path can be tested. */
  fail?: Partial<Record<"session" | "projects" | "board" | "plan" | "lead" | "thread" | "needs-you" | "ticket", string>>;
  origin?: Partial<Origin>;
  writeToken?: string;
  /** Older pages, keyed by the `before` cursor the app sends. */
  pages?: Record<string, Partial<Thread>>;
  /**
   * Answers that bypass the fixture generator entirely.
   *
   * Everything else here is schema-validated, which is the point — but a
   * payload the contract *forbids* cannot be built that way, and the app still
   * has to survive one. `raw` is how a test hands a screen a `nodes` that is
   * not a list, or a `board` with no `agents` at all, to prove the page shows
   * an error rather than nothing.
   */
  raw?: Partial<Record<"session" | "projects" | "board" | "plan" | "lead" | "thread" | "needs-you", unknown>>;
  /** Routes that fail only after their first successful read, for the poll path. */
  failAfterFirst?: Partial<Record<"board" | "plan" | "lead" | "thread" | "needs-you", string>>;
  /**
   * Routes that fail for one project only: `{ plan: { demo: "no answer" } }`.
   * This is how a project switch whose read fails is reproduced — the case
   * where stale data must never be relabelled with the new project's slug.
   */
  failByProject?: Partial<Record<"board" | "plan" | "lead" | "thread" | "needs-you", Record<string, string>>>;
}

export interface Fake {
  api: AtmanApi;
  posted: Array<{ from: string; text: string; to?: string; re?: string }>;
  leadSet: string[];
  calls: string[];
}

const PROJECT = "alpha";

export function fakeApi(parts: FakeParts = {}): Fake {
  const posted: Fake["posted"] = [];
  const leadSet: string[] = [];
  const calls: string[] = [];

  const seen: Record<string, number> = {};

  function boom(route: keyof NonNullable<FakeParts["fail"]>, project?: string): Promise<never> | null {
    const why = parts.fail?.[route];
    if (why) return Promise.reject(new Error(why));
    const perProject = (parts.failByProject as Record<string, Record<string, string>> | undefined)?.[route];
    const forThis = perProject?.[project || PROJECT];
    if (forThis) return Promise.reject(new Error(forThis));
    seen[route] = (seen[route] || 0) + 1;
    const later = (parts.failAfterFirst as Record<string, string> | undefined)?.[route];
    if (later && seen[route] > 1) return Promise.reject(new Error(later));
    return null;
  }

  function raw(route: string): Promise<never> | Promise<unknown> | null {
    const value = (parts.raw as Record<string, unknown> | undefined)?.[route];
    return value === undefined ? null : (Promise.resolve(value) as Promise<unknown>);
  }

  const session = fixture<Session>("session.json", {
    project: PROJECT,
    token: "t".repeat(32),
    operator: "ada",
    lead: "planner",
    ...parts.session,
  });

  const api = {
    origin: { api: "http://127.0.0.1:8765/api/v1", server: "http://127.0.0.1:8765", sameOrigin: true, ...parts.origin } as Origin,
    writeToken: parts.writeToken ?? "t".repeat(32),
    session() {
      calls.push("session");
      return boom("session") ?? Promise.resolve(session);
    },
    projects() {
      calls.push("projects");
      return (
        boom("projects") ??
        Promise.resolve(
          fixture<Projects>("projects.json", {
            current: PROJECT,
            projects: [
              { slug: PROJECT, board: "/tmp/alpha/.tickets", repos: [], source: "started", counts: { open: 12 }, lead: "planner", current: true },
              { slug: "demo", board: "/tmp/demo/.tickets", repos: [], source: "registry", counts: { open: 4 }, lead: "", current: false },
            ],
            ...parts.projects,
          }),
        )
      );
    },
    board(project?: string) {
      calls.push(`board:${project ?? ""}`);
      return (
        boom("board", project) ??
        (raw("board") as never) ??
        Promise.resolve(fixture<Board>("board.json", { project: project || PROJECT, app: { project: project || PROJECT, operator: "ada", operator_note: "", lead: "planner", lead_note: "" }, ...parts.board }))
      );
    },
    plan(project?: string) {
      calls.push(`plan:${project ?? ""}`);
      return (
        boom("plan", project) ??
        (raw("plan") as never) ??
        Promise.resolve(fixture<Plan>("plan.json", { project: project || PROJECT, available: true, ...parts.plan }))
      );
    },
    lead(project?: string) {
      calls.push(`lead:${project ?? ""}`);
      return (
        boom("lead", project) ??
        (raw("lead") as never) ??
        Promise.resolve(
          fixture<Lead>("lead.json", {
            project: project || PROJECT,
            operator: "ada",
            lead: "planner",
            needs_lead: false,
            ...parts.lead,
          }),
        )
      );
    },
    thread(opts: { project?: string; with?: string; before?: string }) {
      calls.push(`thread:${opts.project ?? ""}:${opts.with ?? ""}:${opts.before ?? ""}`);
      const failed = boom("thread", opts.project);
      if (failed) return failed;
      const given = raw("thread");
      if (given) return given as Promise<Thread>;
      if (opts.before) {
        const page = parts.pages?.[opts.before];
        return Promise.resolve(
          fixture<Thread>("thread.json", {
            project: opts.project || PROJECT,
            operator: "ada",
            lead: "planner",
            with: opts.with || "planner",
            has_more: false,
            ...page,
          }),
        );
      }
      return Promise.resolve(
        fixture<Thread>("thread.json", {
          project: opts.project || PROJECT,
          operator: "ada",
          lead: "planner",
          with: opts.with || "planner",
          ...parts.thread,
        }),
      );
    },
    needsYou(project?: string) {
      calls.push(`needs-you:${project ?? ""}`);
      return (
        boom("needs-you", project) ??
        Promise.resolve(fixture<NeedsYou>("needs-you.json", { operator: "ada", ...parts.needsYou }))
      );
    },
    ticket(id: string, project?: string) {
      calls.push(`ticket:${id}:${project ?? ""}`);
      const failed = boom("ticket");
      if (failed) return failed;
      const over = parts.tickets?.[id];
      if (!over && parts.tickets) return Promise.reject(new Error(`no such ticket ${id}`));
      return Promise.resolve(
        fixture<Ticket>("ticket.json", { id, project: project || PROJECT, title: `${id} title`, ...over }),
      );
    },
    postMessage(body: { from: string; text: string; to?: string; re?: string }) {
      posted.push(body);
      return Promise.resolve({ ok: true });
    },
    setLead(seat: string) {
      leadSet.push(seat);
      return Promise.resolve({ ok: true, lead: seat, harness: "unknown", capability: "answers on its next turn", project: PROJECT });
    },
  };

  return { api: api as unknown as AtmanApi, posted, leadSet, calls };
}
