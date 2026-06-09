/** @type {import('tailwindcss').Config} */
export default {
  content: ["./index.html", "./src/**/*.{ts,tsx}"],
  darkMode: "class",
  theme: {
    extend: {
      colors: {
        ogc: {
          teal: "#0EA5A4",
          blue: "#1D4ED8",
          indigo: "#312E81",
        },
      },
    },
  },
  plugins: [],
};
