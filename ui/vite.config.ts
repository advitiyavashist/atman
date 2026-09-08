import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import { boardHost } from "./board-host";

export default defineConfig({
  // boardHost serves the dashboard on one origin with the board API under
  // /api, because the board sends no CORS headers and only accepts its own
  // Origin on writes. See ui/board-host.ts.
  plugins: [react(), boardHost()],
  server: {
    fs: { allow: [".."] },
  },
  test: {
    environment: "jsdom",
    globals: true,
    setupFiles: ["../tests/ui/setup.ts"],
    include: ["../tests/ui/**/*.test.{ts,tsx}"],
    // The integration suite spawns a real board server per file and drives it
    // over a real socket; running those files in parallel would race on the
    // port. Unit files stay parallel.
    poolOptions: { threads: { singleThread: false } },
    testTimeout: 20000,
  },
});
