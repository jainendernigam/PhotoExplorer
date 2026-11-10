/** @type {import('tailwindcss').Config} */
// Colours the app themes change (Dark, Light, Warm sand) come from CSS
// variables set in src/index.css; everything else is plain Tailwind.
const v = (name) => `rgb(var(--${name}) / <alpha-value>)`;
const scale = (p, stops) => Object.fromEntries(stops.map((s) => [s, v(`${p}-${s}`)]));

export default {
  content: ["./index.html", "./src/**/*.{ts,tsx}"],
  theme: {
    extend: {
      colors: {
        neutral: scale("n", [50, 100, 200, 300, 400, 500, 600, 700, 800, 900, 950]),
        white: v("white"),
        sky: scale("sky", [200, 300, 400, 500, 600, 700, 800, 900, 950]),
        amber: scale("amber", [200, 300, 400]),
        emerald: scale("emerald", [200, 300, 400]),
        red: scale("red", [300, 400]),
      },
    },
  },
  plugins: [],
};
