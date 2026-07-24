/** @type {import('tailwindcss').Config} */
export default {
  content: ["./index.html", "./src/**/*.{js,jsx}"],
  darkMode: "class",
  theme: {
    extend: {
      // Enterprise AI Command Center palette. We lean almost entirely on
      // Tailwind's stock slate / indigo / purple / emerald / rose / amber /
      // cyan / yellow scales so every shade already has a full 50-950 ramp;
      // the few custom tokens below are the ones Tailwind doesn't ship.
      colors: {
        brand: {
          50: "#EEF2FF",
          100: "#E0E7FF",
          200: "#C7D2FE",
          300: "#A5B4FC",
          400: "#818CF8",
          500: "#6366F1",
          600: "#4F46E5",
          700: "#4338CA",
        },
      },
      fontFamily: {
        // Inter + JetBrains Mono: the Stripe / Vercel / Linear default duo —
        // a neutral, highly-legible UI face paired with a tabular mono for
        // case IDs, timestamps and extracted data.
        sans: ["Inter", "system-ui", "-apple-system", "Segoe UI", "sans-serif"],
        display: ["Inter", "system-ui", "-apple-system", "Segoe UI", "sans-serif"],
        mono: ["JetBrains Mono", "ui-monospace", "SFMono-Regular", "Menlo", "monospace"],
      },
      boxShadow: {
        glow: "0 0 0 1px rgba(79,70,229,0.06), 0 12px 32px -10px rgba(79,70,229,0.45)",
        "glow-lg": "0 0 0 1px rgba(79,70,229,0.08), 0 24px 48px -12px rgba(79,70,229,0.4)",
      },
      keyframes: {
        "gradient-x": {
          "0%, 100%": { backgroundPosition: "0% 50%" },
          "50%": { backgroundPosition: "100% 50%" },
        },
      },
      animation: {
        "gradient-x": "gradient-x 3s ease infinite",
      },
    },
  },
  plugins: [],
};
