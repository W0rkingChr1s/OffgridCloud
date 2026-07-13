import React from "react";
import ReactDOM from "react-dom/client";
import App from "./App.tsx";
import { printConsoleBanner } from "./console-banner.ts";
import "./index.css";
import "./retro.css";

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
