import type { Config } from "tailwindcss";

export default {
  content: ["./src/**/*.{ts,tsx}"],
  theme: {
    extend: {
      colors: {
        ink: "#1c1917",
        paper: "#faf7f2",
        rule: "#e7e0d6",
        mute: "#78716c",
        warn: "#b45309",
        bad: "#b91c1c",
        ok: "#3f6212",
      },
    },
  },
  plugins: [],
} satisfies Config;
