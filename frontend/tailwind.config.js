/** @type {import('tailwindcss').Config} */
export default {
  content: ["./index.html", "./src/**/*.{js,ts,jsx,tsx}"],
  theme: {
    extend: {
      colors: {
        canvas: "#f2f7f6",
        sunken: "#e9f2f0",
        card: "#ffffff",
        raised: "#e3efed",
        line: "rgba(12, 43, 42, 0.1)",
        "line-strong": "rgba(12, 43, 42, 0.2)",
        fg: {
          DEFAULT: "#0c2b2a",
          2: "#3f5e5b",
          3: "#607b78",
        },
        accent: "#0f766e",
        band: "#0b4f4a",
        "accent-strong": "#0b5c56",
        allow: "#0f8a7e",
        stepup: "#b86a0b",
        block: "#c93a3a",
      },
      fontFamily: {
        sans: ['"IBM Plex Sans"', "ui-sans-serif", "system-ui", "sans-serif"],
        mono: ['"IBM Plex Mono"', "ui-monospace", "SFMono-Regular", "monospace"],
      },
      transitionTimingFunction: {
        out: "cubic-bezier(0.22, 1, 0.36, 1)",
      },
    },
  },
  plugins: [],
};
