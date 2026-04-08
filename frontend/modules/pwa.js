/**
 * LocalMind — PWA utilities
 *
 * Install banner, push-notification subscription, and offline indicator.
 */

let deferredInstallPrompt = null;

// ── Install banner ───────────────────────────────────────────────

function createInstallBanner() {
  const banner = document.createElement("div");
  banner.id = "pwa-install-banner";
  banner.className =
    "fixed bottom-4 left-1/2 -translate-x-1/2 z-[9999] flex items-center gap-3 " +
    "rounded-xl px-5 py-3 shadow-lg border border-slate-700 " +
    "bg-slate-900 text-slate-300 transition-all duration-300 " +
    "opacity-0 translate-y-4 pointer-events-none";
  banner.innerHTML = `
    <span class="material-symbols-outlined text-indigo-400" style="font-size:28px">download</span>
    <div class="flex flex-col text-sm leading-tight">
      <span class="font-semibold text-slate-100">Install LocalMind</span>
      <span class="text-slate-400">Add to home screen for quick access</span>
    </div>
    <button id="pwa-install-btn"
      class="ml-3 rounded-lg bg-indigo-600 hover:bg-indigo-500 text-white text-sm font-medium px-4 py-1.5 transition">
      Install
    </button>
    <button id="pwa-install-dismiss"
      class="ml-1 text-slate-500 hover:text-slate-300 transition"
      aria-label="Dismiss">
      <span class="material-symbols-outlined" style="font-size:20px">close</span>
    </button>`;
  document.body.appendChild(banner);
  return banner;
}

function showInstallBanner() {
  if (!deferredInstallPrompt) return;
  // Don't show if user previously dismissed (respect for 7 days)
  const dismissed = localStorage.getItem("localmind_pwa_dismissed");
  if (dismissed && Date.now() - Number(dismissed) < 7 * 24 * 60 * 60 * 1000) return;
  // Don't show if already installed (standalone mode)
  if (window.matchMedia("(display-mode: standalone)").matches) return;

  const banner = document.getElementById("pwa-install-banner") || createInstallBanner();

  // Reveal
  requestAnimationFrame(() => {
    banner.classList.remove("opacity-0", "translate-y-4", "pointer-events-none");
    banner.classList.add("opacity-100", "translate-y-0", "pointer-events-auto");
  });

  document.getElementById("pwa-install-btn").addEventListener("click", async () => {
    if (!deferredInstallPrompt) return;
    deferredInstallPrompt.prompt();
    const { outcome } = await deferredInstallPrompt.userChoice;
    deferredInstallPrompt = null;
    hideInstallBanner();
    if (outcome === "accepted") {
      console.log("[PWA] App installed");
    }
  });

  document.getElementById("pwa-install-dismiss").addEventListener("click", () => {
    localStorage.setItem("localmind_pwa_dismissed", String(Date.now()));
    deferredInstallPrompt = null;
    hideInstallBanner();
  });
}

function hideInstallBanner() {
  const banner = document.getElementById("pwa-install-banner");
  if (!banner) return;
  banner.classList.add("opacity-0", "translate-y-4", "pointer-events-none");
  banner.classList.remove("opacity-100", "translate-y-0", "pointer-events-auto");
}

// ── Offline indicator ────────────────────────────────────────────

function createOfflineIndicator() {
  const bar = document.createElement("div");
  bar.id = "pwa-offline-bar";
  bar.className =
    "fixed top-0 left-0 w-full z-[9999] flex items-center justify-center gap-2 " +
    "py-1.5 text-xs font-medium transition-transform duration-300 -translate-y-full " +
    "bg-amber-600/90 text-white backdrop-blur";
  bar.innerHTML = `
    <span class="material-symbols-outlined" style="font-size:16px">cloud_off</span>
    <span>You are offline — some features may be unavailable</span>`;
  document.body.appendChild(bar);
  return bar;
}

export function showOfflineIndicator() {
  const update = () => {
    const bar = document.getElementById("pwa-offline-bar") || createOfflineIndicator();
    if (navigator.onLine) {
      bar.classList.add("-translate-y-full");
    } else {
      bar.classList.remove("-translate-y-full");
    }
  };

  window.addEventListener("online", update);
  window.addEventListener("offline", update);
  // Check initial state
  update();
}

// ── Push notification subscription ───────────────────────────────

export async function subscribeToNotifications() {
  if (!("Notification" in window) || !("serviceWorker" in navigator)) {
    console.warn("[PWA] Push notifications not supported in this browser");
    return null;
  }

  const permission = await Notification.requestPermission();
  if (permission !== "granted") {
    console.warn("[PWA] Notification permission denied");
    return null;
  }

  try {
    const registration = await navigator.serviceWorker.ready;

    // Check for existing subscription first
    let subscription = await registration.pushManager.getSubscription();
    if (subscription) {
      console.log("[PWA] Existing push subscription found");
      return subscription;
    }

    // Fetch the VAPID public key from the backend
    const res = await fetch("/api/push/vapid-key");
    if (!res.ok) {
      console.warn("[PWA] Backend does not expose /api/push/vapid-key — push disabled");
      return null;
    }
    const { publicKey } = await res.json();

    subscription = await registration.pushManager.subscribe({
      userVisibleOnly: true,
      applicationServerKey: urlBase64ToUint8Array(publicKey),
    });

    // Send subscription to backend
    await fetch("/api/push/subscribe", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(subscription),
    });

    console.log("[PWA] Push subscription created");
    return subscription;
  } catch (err) {
    console.error("[PWA] Push subscription failed:", err);
    return null;
  }
}

// Convert VAPID key from URL-safe base64 to Uint8Array
function urlBase64ToUint8Array(base64String) {
  const padding = "=".repeat((4 - (base64String.length % 4)) % 4);
  const base64 = (base64String + padding).replace(/-/g, "+").replace(/_/g, "/");
  const raw = atob(base64);
  const arr = new Uint8Array(raw.length);
  for (let i = 0; i < raw.length; i++) {
    arr[i] = raw.charCodeAt(i);
  }
  return arr;
}

// ── Init ─────────────────────────────────────────────────────────

export function initPWA() {
  // Listen for the browser's install prompt
  window.addEventListener("beforeinstallprompt", (e) => {
    e.preventDefault();
    deferredInstallPrompt = e;
    showInstallBanner();
  });

  // Detect when app was installed
  window.addEventListener("appinstalled", () => {
    console.log("[PWA] App installed successfully");
    deferredInstallPrompt = null;
    hideInstallBanner();
  });

  // Offline indicator
  showOfflineIndicator();
}
