import react from "@vitejs/plugin-react";
import { readdirSync, readFileSync, statSync, writeFileSync } from "node:fs";
import { join } from "node:path";
import { brotliCompressSync, constants, gzipSync } from "node:zlib";
import { defineConfig, type Plugin } from "vite";

// Precompress the built text assets (.br + .gz next to each file). The backend
// serves these directly, so the Pi never compresses on the fly and phones on a
// weak uplink download ~200 KB instead of ~750 KB.
function precompress(): Plugin {
  let outDir = "dist";
  return {
    name: "ogc-precompress",
    apply: "build",
    configResolved(config) {
      outDir = config.build.outDir;
    },
    closeBundle() {
      const walk = (dir: string) => {
        for (const name of readdirSync(dir)) {
          const path = join(dir, name);
          if (statSync(path).isDirectory()) walk(path);
          else if (/\.(js|css|html|svg|json|webmanifest)$/.test(name) && statSync(path).size > 1024) {
            const data = readFileSync(path);
            writeFileSync(`${path}.gz`, gzipSync(data, { level: 9 }));
            writeFileSync(
              `${path}.br`,
              brotliCompressSync(data, { params: { [constants.BROTLI_PARAM_QUALITY]: 11 } }),
            );
          }
        }
      };
      walk(outDir);
    },
  };
}

// During dev, proxy /api to the FastAPI backend on :8000.
export default defineConfig({
  plugins: [react(), precompress()],
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
