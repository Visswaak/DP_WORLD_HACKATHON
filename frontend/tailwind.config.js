/** @type {import('tailwindcss').Config} */
export default {
  content: ["./index.html", "./src/**/*.{js,jsx}"],
  theme: {
    extend: {
      colors: {
        ink: {
          950: "#08111f",
          900: "#0d1a2d",
          800: "#12233c",
        },
        ember: "#ff8f3c",
        mint: "#7ef7c5",
        sand: "#f4e7cf",
        sky: "#86c7ff",
      },
      boxShadow: {
        glow: "0 18px 50px rgba(134, 199, 255, 0.16)",
      },
      fontFamily: {
        display: ["Georgia", "serif"],
        body: ["'Trebuchet MS'", "sans-serif"],
      },
    },
  },
  plugins: [],
};
