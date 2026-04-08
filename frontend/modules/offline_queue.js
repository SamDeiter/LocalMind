/**
 * Offline action queue — IndexedDB-backed queue for replaying API calls.
 *
 * When the browser is offline, callers use `queueAction()` to persist a
 * fetch descriptor.  When connectivity returns the queue is automatically
 * drained via `syncQueue()`, which replays each action as a fetch() call
 * and removes it on success.
 *
 * Exports: initOfflineQueue, queueAction, syncQueue, getQueueSize
 */

const DB_NAME = "localmind_offline";
const DB_VERSION = 1;
const STORE_NAME = "actions";

/** @type {IDBDatabase|null} */
let _db = null;

// ---------------------------------------------------------------------------
// Init
// ---------------------------------------------------------------------------

/**
 * Open (or create) the IndexedDB database used for offline queueing.
 * Safe to call multiple times — returns the cached handle after the first.
 *
 * @returns {Promise<IDBDatabase>}
 */
export function initOfflineQueue() {
  if (_db) return Promise.resolve(_db);

  return new Promise((resolve, reject) => {
    const req = indexedDB.open(DB_NAME, DB_VERSION);

    req.onupgradeneeded = (e) => {
      const db = e.target.result;
      if (!db.objectStoreNames.contains(STORE_NAME)) {
        const store = db.createObjectStore(STORE_NAME, {
          keyPath: "id",
          autoIncrement: true,
        });
        // Index by timestamp so we replay in order.
        store.createIndex("timestamp", "timestamp", { unique: false });
      }
    };

    req.onsuccess = (e) => {
      _db = e.target.result;
      resolve(_db);
    };

    req.onerror = (e) => {
      console.error("[offline_queue] IndexedDB open failed:", e.target.error);
      reject(e.target.error);
    };
  });
}

// ---------------------------------------------------------------------------
// Queue an action
// ---------------------------------------------------------------------------

/**
 * Persist a pending API action for later replay.
 *
 * @param {Object} action
 * @param {string} action.type   — descriptive label (e.g. "send_message")
 * @param {string} action.url    — full URL to fetch
 * @param {string} action.method — HTTP method (GET, POST, etc.)
 * @param {*}      [action.body] — JSON-serialisable request body (optional)
 * @param {number} [action.timestamp] — epoch ms (defaults to Date.now())
 * @returns {Promise<number>} The auto-generated key of the stored record.
 */
export async function queueAction(action) {
  const db = await initOfflineQueue();
  const record = {
    type: action.type || "unknown",
    url: action.url,
    method: (action.method || "POST").toUpperCase(),
    body: action.body ?? null,
    timestamp: action.timestamp || Date.now(),
  };

  return new Promise((resolve, reject) => {
    const tx = db.transaction(STORE_NAME, "readwrite");
    const store = tx.objectStore(STORE_NAME);
    const req = store.add(record);

    req.onsuccess = () => {
      console.info("[offline_queue] Queued action:", record.type, record.url);
      resolve(req.result);
    };
    req.onerror = (e) => {
      console.error("[offline_queue] Failed to queue action:", e.target.error);
      reject(e.target.error);
    };
  });
}

// ---------------------------------------------------------------------------
// Sync (replay) the queue
// ---------------------------------------------------------------------------

/**
 * Replay all queued actions in timestamp order.  Each action is sent as a
 * fetch() call; successful responses (2xx) cause the record to be deleted.
 * Failed actions remain in the queue for the next sync attempt.
 *
 * @returns {Promise<{synced: number, failed: number}>}
 */
export async function syncQueue() {
  const db = await initOfflineQueue();

  const actions = await new Promise((resolve, reject) => {
    const tx = db.transaction(STORE_NAME, "readonly");
    const store = tx.objectStore(STORE_NAME);
    const idx = store.index("timestamp");
    const req = idx.getAll();

    req.onsuccess = () => resolve(req.result);
    req.onerror = (e) => reject(e.target.error);
  });

  if (actions.length === 0) return { synced: 0, failed: 0 };

  console.info(`[offline_queue] Syncing ${actions.length} queued action(s)...`);

  let synced = 0;
  let failed = 0;

  for (const action of actions) {
    try {
      const fetchOpts = { method: action.method };
      if (action.body != null) {
        fetchOpts.headers = { "Content-Type": "application/json" };
        fetchOpts.body = JSON.stringify(action.body);
      }

      const resp = await fetch(action.url, fetchOpts);

      if (resp.ok) {
        // Remove from queue on success.
        await _deleteAction(db, action.id);
        synced++;
      } else {
        console.warn(
          `[offline_queue] Replay failed (${resp.status}):`,
          action.type,
          action.url,
        );
        failed++;
      }
    } catch (err) {
      // Network still down or other transient error — keep in queue.
      console.warn("[offline_queue] Replay error:", action.type, err);
      failed++;
    }
  }

  console.info(
    `[offline_queue] Sync complete: ${synced} synced, ${failed} failed`,
  );
  return { synced, failed };
}

// ---------------------------------------------------------------------------
// Queue size
// ---------------------------------------------------------------------------

/**
 * Return the number of pending actions in the queue.
 *
 * @returns {Promise<number>}
 */
export async function getQueueSize() {
  const db = await initOfflineQueue();

  return new Promise((resolve, reject) => {
    const tx = db.transaction(STORE_NAME, "readonly");
    const store = tx.objectStore(STORE_NAME);
    const req = store.count();

    req.onsuccess = () => resolve(req.result);
    req.onerror = (e) => reject(e.target.error);
  });
}

// ---------------------------------------------------------------------------
// Auto-sync on reconnect
// ---------------------------------------------------------------------------

if (typeof window !== "undefined") {
  window.addEventListener("online", () => {
    console.info("[offline_queue] Browser back online — syncing queue...");
    syncQueue().catch((err) => {
      console.error("[offline_queue] Auto-sync failed:", err);
    });
  });
}

// ---------------------------------------------------------------------------
// Internal helpers
// ---------------------------------------------------------------------------

/**
 * Delete a single action record by its auto-incremented key.
 *
 * @param {IDBDatabase} db
 * @param {number} id
 * @returns {Promise<void>}
 */
function _deleteAction(db, id) {
  return new Promise((resolve, reject) => {
    const tx = db.transaction(STORE_NAME, "readwrite");
    const store = tx.objectStore(STORE_NAME);
    const req = store.delete(id);

    req.onsuccess = () => resolve();
    req.onerror = (e) => reject(e.target.error);
  });
}
