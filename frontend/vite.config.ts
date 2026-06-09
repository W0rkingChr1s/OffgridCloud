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
  },
});
