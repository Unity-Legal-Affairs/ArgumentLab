import type { Config } from "tailwindcss";

const config: Config = {
  content: ["./app/**/*.{ts,tsx}", "./components/**/*.{ts,tsx}", "./lib/**/*.{ts,tsx}"],
  theme: {
    extend: {
      colors: {
        ink: "rgb(var(--color-ink) / <alpha-value>)",
        paper: "rgb(var(--color-paper) / <alpha-value>)",
        panel: "rgb(var(--color-panel) / <alpha-value>)",
        surface: "rgb(var(--color-surface) / <alpha-value>)",
        surface2: "rgb(var(--color-surface-2) / <alpha-value>)",
        line: "rgb(var(--color-line) / <alpha-value>)",
        risk: "rgb(var(--color-risk) / <alpha-value>)",
        riskSoft: "rgb(var(--color-risk-soft) / <alpha-value>)",
        riskHover: "rgb(var(--color-risk-hover) / <alpha-value>)",
        amber: "rgb(var(--color-amber) / <alpha-value>)",
        docket: "rgb(var(--color-docket) / <alpha-value>)",
        sage: "rgb(var(--color-sage) / <alpha-value>)",
        badge: "rgb(var(--color-badge) / <alpha-value>)",
        sidebar: "rgb(var(--color-sidebar) / <alpha-value>)",
        inkHover: "rgb(var(--color-ink-hover) / <alpha-value>)",
        okSoft: "rgb(var(--color-ok-soft) / <alpha-value>)"
      },
      boxShadow: {
        warroom: "var(--shadow-warroom)"
      }
    }
  },
  plugins: []
};

export default config;
