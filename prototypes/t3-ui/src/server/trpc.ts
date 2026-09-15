import { initTRPC } from "@trpc/server";
import { defaultBoardDir } from "./board";

export type Context = { boardDir: string };

export function createContext(boardDir?: string): Context {
  return { boardDir: boardDir || defaultBoardDir() };
}

const t = initTRPC.context<Context>().create();
export const router = t.router;
export const publicProcedure = t.procedure;
