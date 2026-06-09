// Minimal service worker: makes OffgridCloud installable and serves a cached
// app shell when offline. API and SSE requests are never cached.
const CACHE = "ogc-shell-v1";

self.addEventListener("install", () => self.skipWaiting());
self.addEventListener("activate", (event) => event.waitUntil(self.clients.claim()));

self.addEventListener("fetch", (event) => {
  const url = new URL(event.request.url);
  if (event.request.method !== "GET" || url.origin !== self.location.origin) return;
  if (url.pathname.startsWith("/api")) return; // live data + uploads: always network

  // Network-first, fall back to cache (or the cached shell for navigations).
  event.respondWith(
    fetch(event.request)
      .then((response) => {
        const copy = response.clone();
        caches.open(CACHE).then((c) => c.put(event.request, copy));
        return response;
      })
      .catch(() =>
        caches.match(event.request).then((cached) => cached || caches.match("/")),
      ),
  );
});
