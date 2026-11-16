/** @type {import('tailwindcss').Config} */
export default {
  content: ["./index.html", "./src/**/*.{ts,tsx}"],
  theme: {
    extend: {
      colors: {
        bg: "var(--bg)", panel: "var(--panel)", panel2: "var(--panel2)", line: "var(--line)", line2: "var(--line2)",
        fg: "var(--fg)", dim: "var(--dim)", mute: "var(--mute)", accent: "var(--accent)", accent2: "var(--accent2)",
        ok: "var(--ok)", warn: "var(--warn)", err: "var(--err)",
      },
      fontFamily: { sans: ["Inter", "system-ui", "sans-serif"], mono: ["JetBrains Mono", "ui-monospace", "monospace"] },
      fontSize: { xxs: ["10.5px", "14px"] },
    },
  },
  plugins: [],
};
