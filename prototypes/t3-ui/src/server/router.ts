import { publicProcedure, router } from "./trpc";
import { objectiveView, teamView, workView } from "./board";

/**
 * Screens call these procedures. They never receive raw ticket JSON.
 * Honesty lives in honesty.ts; this router only forwards judged views.
 */
export const appRouter = router({
  objective: publicProcedure.query(({ ctx }) => objectiveView(ctx.boardDir)),
  work: publicProcedure.query(({ ctx }) => workView(ctx.boardDir)),
  team: publicProcedure.query(({ ctx }) => teamView(ctx.boardDir)),
});

export type AppRouter = typeof appRouter;
