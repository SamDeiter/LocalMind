/**
 * LocalMind — Service Worker (KILL SWITCH)
 *
 * One-shot self-destruct. The moment any browser fetches this file, the
 * resulting SW:
 *   1. Deletes every cache under this origin
 *   2. Takes control of all open clients
 *   3. Unregisters itself
 *   4. Reloads every controlled page once
 *
 * After that reload, there is no SW controlling the page — next navigation
 * will fetch the real sw.js fresh from disk and register a clean one.
 *
 * The real SW lives in sw.full.js. After every browser in the wild has
 * hit this file once, restore sw.js from sw.full.js.
 */

self.addEventListener("install", (event) => {
  self.skipWaiting();
});

self.addEventListener("activate", (event) => {
  event.waitUntil((async () => {
    // Wipe every cache
    const names = await caches.keys();
    await Promise.all(names.map((n) => caches.delete(n)));

    // Take control of all tabs
    await self.clients.claim();

    // Unregister self
    try {
      await self.registration.unregister();
    } catch (_) { /* ignore */ }

    // Tell every controlled page to reload, and wipe any remaining page-side
    // storage on the way out.
    const clientsList = await self.clients.matchAll({ includeUncontrolled: true });
    for (const client of clientsList) {
      try {
        client.postMessage({ type: "SW_KILL_RELOAD" });
        if ("navigate" in client) {
          await client.navigate(client.url);
        }
      } catch (_) { /* ignore */ }
    }
  })());
});

// Pass-through for everything — no caching, no interception
self.addEventListener("fetch", () => {});
