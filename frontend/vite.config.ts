import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";
import pkg from "./package.json";

// During dev, proxy /api to the FastAPI backend on :8000.
export default defineConfig({
  plugins: [react()],
  // Expose the app version to the client for the console banner.
  define: {
    __APP_VERSION__: JSON.stringify(pkg.version),
  },
  server: {
    proxy: {
      "/api": "http://localhost:8000",
    },
  },
  build: {
    outDir: "dist",
  },
});
