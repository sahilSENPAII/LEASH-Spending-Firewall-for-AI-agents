/** @type {import('tailwindcss').Config} */
export default {
  content: ["./index.html", "./src/**/*.{js,ts,jsx,tsx}"],
  theme: {
    extend: {
      colors: {
        canvas: "#06181b",
        sunken: "#041114",
        card: "#0b2226",
        raised: "#123036",
        line: "rgba(214, 240, 236, 0.09)",
        "line-strong": "rgba(214, 240, 236, 0.18)",
        fg: {
          DEFAULT: "#e9f5f3",
          2: "#a3c2be",
          3: "#7a9b97",
        },
        accent: "#2cc5b5",
        "accent-strong": "#5fe0d1",
        allow: "#2cc5b5",
        stepup: "#f0b54a",
        block: "#ff6f61",
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
