import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

// During dev, proxy /api to the FastAPI backend on :8000.
export default defineConfig({
  plugins: [react()],
  server: {
    proxy: {
      "/api": "http://localhost:8000",
    },
  },
  build: {
    outDir: "dist",
    // Vite 7+ defaults to "baseline-widely-available" (Safari 16.4+, Chrome
    // 111+). Field phones and tablets are often older, so keep the previous
    // Vite 6 "modules" baseline (Safari 14 / Chrome 87) explicitly.
    target: ["es2020", "edge88", "firefox78", "chrome87", "safari14"],
  },
});
