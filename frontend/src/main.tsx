import React from "react";
import ReactDOM from "react-dom/client";
import App from "./App.tsx";
import { printConsoleBanner } from "./console-banner.ts";
import "./index.css";
import "./retro.css";
import { applyTheme, watchSystemTheme } from "./theme";

// Pages are lazy chunks with content hashes. After a box update the old ones
// are gone, so a tab still running the previous build fails to open a page it
// hasn't loaded yet: reload once to pick up the new index.html instead of
// showing a blank screen. The flag stops a reload loop if the box is down.
window.addEventListener("vite:preloadError", (event) => {
  try {
    if (sessionStorage.getItem("ogc_chunk_reload")) return;
    sessionStorage.setItem("ogc_chunk_reload", "1");
  } catch {
    return;
  }
  event.preventDefault();
  window.location.reload();
});
window.addEventListener("load", () => {
  try {
    sessionStorage.removeItem("ogc_chunk_reload");
  } catch {
    /* storage blocked (private mode): nothing to clear */
  }
});

applyTheme();
watchSystemTheme();
void printConsoleBanner();

ReactDOM.createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    <App />
  </React.StrictMode>,
);

// Register the service worker (PWA: installable + offline app shell).
if ("serviceWorker" in navigator) {
  window.addEventListener("load", () => {
    navigator.serviceWorker.register("/sw.js").catch(() => {
      /* PWA is a progressive enhancement; ignore failures */
    });
  });
}
