import type { Config } from "tailwindcss";

const config: Config = {
  content: ["./app/**/*.{js,ts,jsx,tsx,mdx}", "./components/**/*.{js,ts,jsx,tsx,mdx}"],
  theme: {
    extend: {
      colors: {
        ink: "#071018",
        panel: "#0c1721",
        panel2: "#111f2b",
        line: "#1e3341",
        cyanline: "#2dd4bf",
      },
    },
  },
  plugins: [],
};

export default config;

