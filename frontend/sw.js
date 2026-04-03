/**
 * LocalMind — Service Worker
 *
 * Enables PWA installation and basic offline caching.
 * Caches the app shell (HTML, CSS, JS) so the UI loads instantly.
 * API calls always go to the network (no offline AI inference).
 */

const CACHE_NAME = "localmind-v1";

// Files to cache for instant loading
const SHELL_FILES = [
  "/",
  "/style.css",
  "/app.js",
  "/manifest.json",
];

// ── Install: cache the app shell ──────────────────────────────────
self.addEventListener("install", (event) => {
  event.waitUntil(
    caches.open(CACHE_NAME).then((cache) => {
      return cache.addAll(SHELL_FILES);
    }),
  );
  self.skipWaiting(); // Activate immediately
});

// ── Activate: clean up old caches ─────────────────────────────────
self.addEventListener("activate", (event) => {
  event.waitUntil(
    caches.keys().then((names) => {
      return Promise.all(
        names
          .filter((name) => name !== CACHE_NAME)
          .map((name) => caches.delete(name)),
      );
    }),
  );
  self.clients.claim();
});

// ── Fetch: network-first for API, cache-first for static ─────────

// ── Fetch: Cache-first for static/CDN, Network-only (or fallback) for API ─────────
self.addEventListener("fetch", (event) => {
  const url = new URL(event.request.url);

  // API calls: Network-only, No caching
  if (url.pathname.startsWith("/api/")) {
    return;
  }

  // Static/CDN: Cache-first strategy (Stale-While-Revalidate)
  event.respondWith(
    caches.match(event.request).then((cachedResponse) => {
      const fetchPromise = fetch(event.request).then((networkResponse) => {
        if (networkResponse && networkResponse.ok && event.request.method === "GET") {
          const cacheCopy = networkResponse.clone();
          caches.open(CACHE_NAME).then((cache) => {
            cache.put(event.request, cacheCopy);
          });
        }
        return networkResponse;
      }).catch(() => cachedResponse);

      return cachedResponse || fetchPromise;
    })
  );
});
