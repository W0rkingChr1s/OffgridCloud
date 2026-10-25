// Minimal service worker: makes OffgridCloud installable and serves a cached
// app shell when offline. API and SSE requests are never cached.
const CACHE = "ogc-shell-v3";

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

// Last resort for a navigation with neither network nor cached shell: a real
// page instead of a browser error screen, with a button that drops this worker
// and its cache so the next load goes straight to the network (and Safari shows
// its certificate warning again instead of failing silently).
function unreachablePage() {
  const html = `<!doctype html><html lang="de"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Box nicht erreichbar</title>
<style>body{font:16px/1.5 system-ui,sans-serif;background:#0f172a;color:#e2e8f0;
margin:0;padding:24px;max-width:36rem}button{font:inherit;padding:10px 16px;
border:0;border-radius:8px;background:#f59e0b;color:#111;cursor:pointer}</style>
</head><body><h1>Box nicht erreichbar</h1>
<p>Die App-Seite konnte nicht geladen werden. Auf <code>https://&lt;name&gt;.local</code>
liegt das meist am selbst ausgestellten Zertifikat der Box, das sich regelm&auml;&szlig;ig
erneuert. Dauerhaft hilft das Root-Zertifikat der Box (System &rarr; Erweitert).</p>
<p><button id="r">Neu verbinden</button></p>
<script>document.getElementById("r").onclick=async()=>{try{
const r=await navigator.serviceWorker.getRegistrations();await Promise.all(r.map(x=>x.unregister()));
const k=await caches.keys();await Promise.all(k.map(x=>caches.delete(x)));}finally{location.reload();}};</script>
</body></html>`;
  return new Response(html, {
    status: 503,
    headers: { "Content-Type": "text/html; charset=utf-8", "Cache-Control": "no-store" },
  });
}

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
          return unreachablePage();
        }
        return Response.error();
      }),
  );
});
