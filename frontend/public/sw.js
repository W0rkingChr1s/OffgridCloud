// Minimal service worker: makes OffgridCloud installable and serves a cached
// app shell when offline. API and SSE requests are never cached.
const CACHE = "ogc-shell-v2";

// Precache the app shell. This doubles as a probe: if the worker itself can't
// reach the box — e.g. Safari doesn't apply the user's exception for the
// self-signed `tls internal` certificate to service-worker fetches — every
// request it intercepted would fail. In that case unregister (this also drops
// an older, already active version) so the browser talks to the network
// directly again, and fail the install.
self.addEventListener("install", (event) => {
  event.waitUntil(
    caches
      .open(CACHE)
      .then((cache) => cache.add("/"))
      .then(
        () => self.skipWaiting(),
        (err) => self.registration.unregister().then(() => Promise.reject(err)),
      ),
  );
});

self.addEventListener("activate", (event) => {
  event.waitUntil(
    caches
      .keys()
      .then((keys) => Promise.all(keys.filter((k) => k !== CACHE).map((k) => caches.delete(k))))
      .then(() => self.clients.claim()),
  );
});

// Focus (or open) the app when a notification is tapped.
self.addEventListener("notificationclick", (event) => {
  event.notification.close();
  event.waitUntil(
    self.clients.matchAll({ type: "window", includeUncontrolled: true }).then((clients) => {
      for (const client of clients) {
        if ("focus" in client) return client.focus();
      }
      return self.clients.openWindow ? self.clients.openWindow("/") : undefined;
    }),
  );
});

self.addEventListener("fetch", (event) => {
  const url = new URL(event.request.url);
  if (event.request.method !== "GET" || url.origin !== self.location.origin) return;
  if (url.pathname.startsWith("/api")) return; // live data + uploads: always network

  // Network-first, fall back to cache (or the cached shell for navigations).
  // Never resolve with undefined: Safari then shows "Returned response is null"
  // instead of the page.
  event.respondWith(
    fetch(event.request)
      .then((response) => {
        if (response.ok) {
          const copy = response.clone();
          caches.open(CACHE).then((c) => c.put(event.request, copy));
        }
        return response;
      })
      .catch(async () => {
        const cached = await caches.match(event.request);
        if (cached) return cached;
        if (event.request.mode === "navigate") {
          const shell = await caches.match("/");
          if (shell) return shell;
        }
        return Response.error();
      }),
  );
});
