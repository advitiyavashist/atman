import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

export default defineConfig({
  plugins: [react()],
  // `atm ui` serves the bundle under /app/, so asset URLs have to be relative
  // to the page rather than rooted at /. The same relative base works on the
  // dev server, which serves the app at /.
  base: "./",
  server: {
    // The schemas in docs/api/schemas are imported by the dev-time response
    // check, which lives above ui/.
    fs: { allow: [".."] },
    // Loopback only. The API refuses any origin it was not told about, and
    // this server has no business listening anywhere else.
    host: "127.0.0.1",
  },
  build: {
    // No sourcemap in the shipped bundle: nothing here is minified secrets,
    // but the bundle is served by a local process and should stay small.
    sourcemap: false,
  },
  test: {
    environment: "jsdom",
    globals: true,
    setupFiles: ["../tests/ui/setup.ts"],
    include: ["../tests/ui/**/*.test.{ts,tsx}"],
    testTimeout: 20000,
  },
});
