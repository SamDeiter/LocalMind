/**
 * LocalMind — Service Worker
 *
 * Enables PWA installation and basic offline caching.
 * Caches the app shell (HTML, CSS, JS) so the UI loads instantly.
 * API calls always go to the network (no offline AI inference).
 */

const CACHE_NAME = "localmind-v3.1";

// Files to cache for instant loading — must match actual filenames
const SHELL_FILES = [
  "/",
  "/styles.css",
  "/app.js",
  "/manifest.json",
  "/icon.png",
  "/modules/chat.js",
  "/modules/conversations.js",
  "/modules/media.js",
  "/modules/sidebar.js",
  "/modules/editor.js",
  "/modules/events.js",
  "/modules/research_ui.js",
  "/modules/settings_ui.js",
  "/modules/dashboard.js",
  "/modules/live_reload.js",
  "/modules/swarm_ui.js",
  "/modules/jobs_ui.js",
  "/modules/templates_ui.js",
  "/modules/approvals_ui.js",
  "/modules/task_creation.js",
  "/modules/onboarding.js",
  "/modules/brain_graph.js",
  "/modules/time_machine.js",
  "/modules/hub.js",
  "/modules/state.js",
  "/modules/streaming.js",
  "/modules/tools.js",
  "/modules/utils.js",
  "/modules/proposals_ui.js",
  "/modules/pwa.js",
];

// Simple offline fallback page
const OFFLINE_HTML = `<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8"/>
  <meta name="viewport" content="width=device-width, initial-scale=1.0"/>
  <title>LocalMind — Offline</title>
  <style>
    body {
      margin: 0; display: flex; align-items: center; justify-content: center;
      min-height: 100vh; background: #0f172a; color: #cbd5e1;
      font-family: Inter, system-ui, sans-serif; text-align: center;
    }
    .box { max-width: 400px; padding: 2rem; }
    h1 { font-size: 1.5rem; color: #f1f5f9; margin-bottom: 0.5rem; }
    p { font-size: 0.95rem; line-height: 1.6; color: #94a3b8; }
    button {
      margin-top: 1.5rem; padding: 0.6rem 1.5rem; border: none; border-radius: 0.5rem;
      background: #6366f1; color: #fff; font-size: 0.95rem; cursor: pointer;
    }
    button:hover { background: #4f46e5; }
  </style>
</head>
<body>
  <div class="box">
    <h1>You're offline</h1>
    <p>LocalMind needs a network connection to talk to your local AI backend. Please reconnect and try again.</p>
    <button onclick="location.reload()">Retry</button>
  </div>
</body>
</html>`;

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

// ── Fetch: Cache-first for static/CDN, Network-only for API ──────
self.addEventListener("fetch", (event) => {
  const url = new URL(event.request.url);

  // Only handle http/https — skip chrome-extension://, data:, etc.
  if (url.protocol !== "http:" && url.protocol !== "https:") {
    return;
  }

  // API calls: Network-only, no caching
  if (url.pathname.startsWith("/api/")) {
    return;
  }

  // Navigation requests: network-first with offline fallback
  if (event.request.mode === "navigate") {
    event.respondWith(
      fetch(event.request)
        .then((response) => {
          // Cache a copy of successful navigations
          if (response && response.ok) {
            const copy = response.clone();
            caches.open(CACHE_NAME).then((cache) => cache.put(event.request, copy));
          }
          return response;
        })
        .catch(() => {
          // Try cache first, then offline page
          return caches.match(event.request).then((cached) => {
            return cached || new Response(OFFLINE_HTML, {
              status: 200,
              headers: { "Content-Type": "text/html; charset=utf-8" },
            });
          });
        })
    );
    return;
  }

  // Static/CDN: Stale-While-Revalidate strategy
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

// ── Push: show notification when server sends a push message ──────
self.addEventListener("push", (event) => {
  let data = { title: "LocalMind", body: "You have a new notification." };
  if (event.data) {
    try {
      data = event.data.json();
    } catch {
      data.body = event.data.text();
    }
  }

  const options = {
    body: data.body || "You have a new notification.",
    icon: "/icon.png",
    badge: "/icon.png",
    tag: data.tag || "localmind-notification",
    data: {
      url: data.url || "/",
    },
    actions: data.actions || [],
  };

  event.waitUntil(
    self.registration.showNotification(data.title || "LocalMind", options),
  );
});

// ── Notification click: open or focus the app ─────────────────────
self.addEventListener("notificationclick", (event) => {
  event.notification.close();

  const targetUrl = event.notification.data?.url || "/";

  event.waitUntil(
    clients.matchAll({ type: "window", includeUncontrolled: true }).then((windowClients) => {
      // Focus an existing tab if one is open
      for (const client of windowClients) {
        if (new URL(client.url).pathname === targetUrl && "focus" in client) {
          return client.focus();
        }
      }
      // Otherwise open a new window
      return clients.openWindow(targetUrl);
    }),
  );
});
