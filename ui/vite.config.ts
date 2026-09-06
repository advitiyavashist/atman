import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

export default defineConfig({
  plugins: [react()],
  server: {
    fs: { allow: [".."] },
  },
  test: {
    environment: "jsdom",
    globals: true,
    setupFiles: ["../tests/ui/setup.ts"],
    include: ["../tests/ui/**/*.test.{ts,tsx}"],
  },
});
