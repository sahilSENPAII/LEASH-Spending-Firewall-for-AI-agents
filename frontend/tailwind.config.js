/** @type {import('tailwindcss').Config} */
export default {
  content: ["./index.html", "./src/**/*.{js,ts,jsx,tsx}"],
  theme: {
    extend: {
      colors: {
        canvas: "#141415",
        sunken: "#0f0f10",
        card: "#19191b",
        raised: "#232325",
        line: "rgba(237, 237, 237, 0.08)",
        "line-strong": "rgba(237, 237, 237, 0.14)",
        fg: {
          DEFAULT: "#ededed",
          2: "#a1a1a6",
          3: "#85858b",
        },
        accent: "#7ea6f2",
        allow: "#4cc38a",
        stepup: "#d9a441",
        block: "#e5484d",
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
