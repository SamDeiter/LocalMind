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
  "/static/style.css",
  "/static/app.js",
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
self.addEventListener("fetch", (event) => {
  const url = new URL(event.request.url);

  // API calls always go to network (can't do AI offline)
  if (url.pathname.startsWith("/api/")) {
    return;
  }

  // Static files: try cache first, then network
  event.respondWith(
    caches.match(event.request).then((cached) => {
      return (
        cached ||
        fetch(event.request).then((response) => {
          // Cache successful responses for next time
          if (response.ok) {
            const clone = response.clone();
            caches.open(CACHE_NAME).then((cache) => {
              cache.put(event.request, clone);
            });
          }
          return response;
        })
      );
    }),
  );
});
