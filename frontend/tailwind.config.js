
/** @type {import('tailwindcss').Config} */
export default {
  content: [
    "./index.html",
    "./modules/**/*.{js,ts,jsx,tsx}",
    "./app.js"
  ],
  darkMode: "class",
  theme: {
    extend: {
      colors: {
        "surface-container-lowest": "#020617",
        "on-surface-variant": "#94a3b8",
        "background": "#020617",
        "surface": "#0f172a",
        "surface-container-low": "#0f172a",
        "surface-container": "#1e293b",
        "surface-container-high": "#334155",
        "surface-container-highest": "#475569",
        "primary": "#6366f1",
        "secondary": "#10b981",
        "tertiary": "#8b5cf6",
        "on-background": "#f8fafc",
        "on-surface": "#f1f5f9",
        "on-primary": "#ffffff",
        "outline": "#64748b",
        "outline-variant": "#334155",
        "error": "#ef4444",
        "surface-variant": "#1e293b",
        "surface-bright": "#334155",
      },
      fontFamily: {
        "headline": ["Space Grotesk", "sans-serif"],
        "body": ["Inter", "sans-serif"],
        "mono": ["JetBrains Mono", "monospace"]
      },
    },
  },
  plugins: [
    require('@tailwindcss/forms'),
    require('@tailwindcss/container-queries'),
  ],
}
