/** @type {import('tailwindcss').Config} */
module.exports = {
  content: [
    "./app/**/*.{js,ts,jsx,tsx}",
    "./components/**/*.{js,ts,jsx,tsx}",
  ],
  theme: {
    extend: {
      colors: {
        surface: {
          DEFAULT: "#0C0F14",
          secondary: "#131720",
          tertiary: "#1A1F2E",
          elevated: "#222839",
        },
        accent: {
          green: "#22C55E",
          red: "#EF4444",
          blue: "#3B82F6",
          amber: "#F59E0B",
        },
        muted: "#64748B",
      },
      fontFamily: {
        sans: ['"DM Sans"', "system-ui", "sans-serif"],
        mono: ['"JetBrains Mono"', "monospace"],
      },
    },
  },
  plugins: [],
};
