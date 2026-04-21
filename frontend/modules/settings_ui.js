/**
 * settings_ui.js — LocalMind Configuration
 * Handles fetching, saving, and testing Twilio (SMS) and Gemini (Cloud) settings.
 */

import { API } from "./state.js";
import { showToast } from "./utils.js";

/** Current settings state */
let _notificationSettings = {
  twilio_sid: "",
  twilio_auth_token: "",
  twilio_phone_number: "",
  target_phone_number: "",
  enabled: false,
};

let _cloudSettings = {
  api_key: "",
};

/** Load all settings from backend and populate the UI */

/** Google Workspace state */
let _googleStatus = {
  authenticated: false,
  scopes: [],
  needs_reauth: true,
  missing_scopes: []
};

/** Refresh Google Auth status from backend */
export async function updateGoogleStatus() {
  const badge = document.getElementById("googleStatusBadge");
  const actions = document.getElementById("googleConnectedActions");
  const connectBtn = document.getElementById("connectGoogleBtn");

  try {
    const r = await fetch(`${API}/api/google/status`);
    const data = await r.json();
    _googleStatus = data;

    if (badge) {
      if (data.authenticated) {
        badge.innerText = "Connected";
        badge.className = "px-2 py-0.5 rounded text-[10px] font-bold uppercase bg-emerald-500/20 text-emerald-400";
        if (connectBtn) connectBtn.classList.add("hidden");
        if (actions) actions.classList.remove("hidden");
      } else {
        badge.innerText = "Not Connected";
        badge.className = "px-2 py-0.5 rounded text-[10px] font-bold uppercase bg-slate-800 text-slate-500";
        if (connectBtn) connectBtn.classList.remove("hidden");
        if (actions) actions.classList.add("hidden");
      }
    }
  } catch (e) {
    console.error("Failed to check Google status:", e);
    if (badge) badge.innerText = "Status Error";
  }
}

/** Start Google OAuth flow */
export function connectGoogle() {
  window.open(`${API}/api/google/auth`, "_blank");
}

/** Revoke Google access */
export async function revokeGoogle() {
  if (!confirm("Are you sure you want to disconnect Google Workspace?")) return;
  
  try {
    showToast("Disconnecting Google...", "info");
    const r = await fetch(`${API}/api/google/revoke`, { method: "POST" });
    const d = await r.json();
    if (d.ok) {
      showToast("✅ Google Disconnected", "info");
      updateGoogleStatus();
    }
  } catch (e) {
    showToast("❌ Disconnection Failed", "error");
  }
}

/** Refresh Google token */
export async function refreshGoogle() {
  try {
    showToast("Refreshing token...", "info");
    const r = await fetch(`${API}/api/google/refresh`, { method: "POST" });
    const d = await r.json();
    if (d.ok) {
        showToast("✅ Token Refreshed", "info");
        updateGoogleStatus();
    } else {
        showToast(`❌ Refresh Failed: ${d.detail || "Unknown"}`, "error");
    }
  } catch {
    showToast("❌ Token Refresh Failed", "error");
  }
}

export async function loadSettings() {
  try {
    // Load Notifications
    const r1 = await fetch(`${API}/api/settings/notifications`);
    const data1 = await r1.json();
    _notificationSettings = data1 || _notificationSettings;

    // Load Cloud
    const r2 = await fetch(`${API}/api/settings/cloud`);
    const data2 = await r2.json();
    _cloudSettings = data2 || _cloudSettings;

    populateSettingsUI();
    updateGoogleStatus();
  } catch (e) {
    console.error("Failed to load settings:", e);
  }
}

/** Populate form fields with current settings */
function populateSettingsUI() {
  // SMS Fields
  const sidInput = document.getElementById("twilioSid");
  const tokenInput = document.getElementById("twilioToken");
  const twilioPhoneInput = document.getElementById("twilioPhone");
  const targetPhoneInput = document.getElementById("targetPhone");
  const enabledToggle = document.getElementById("smsEnabled");

  if (sidInput) sidInput.value = _notificationSettings.twilio_sid || "";
  if (tokenInput) tokenInput.value = _notificationSettings.twilio_auth_token || "";
  if (twilioPhoneInput) twilioPhoneInput.value = _notificationSettings.twilio_phone_number || "";
  if (targetPhoneInput) targetPhoneInput.value = _notificationSettings.target_phone_number || "";
  if (enabledToggle) enabledToggle.checked = !!_notificationSettings.enabled;

  // Gemini Fields
  const geminiInput = document.getElementById("geminiKey");
  if (geminiInput) geminiInput.value = _cloudSettings.api_key || "";
}

/** Save all settings to backend */
export async function saveAllSettings() {
  // Collect Notifications
  const sidInput = document.getElementById("twilioSid");
  const tokenInput = document.getElementById("twilioToken");
  const twilioPhoneInput = document.getElementById("twilioPhone");
  const targetPhoneInput = document.getElementById("targetPhone");
  const enabledToggle = document.getElementById("smsEnabled");

  const newNotifSettings = {
    twilio_sid: sidInput?.value.trim() || "",
    twilio_auth_token: tokenInput?.value.trim() || "",
    twilio_phone_number: twilioPhoneInput?.value.trim() || "",
    target_phone_number: targetPhoneInput?.value.trim() || "",
    enabled: enabledToggle?.checked || false,
  };

  // Collect Cloud
  const geminiInput = document.getElementById("geminiKey");
  const newCloudSettings = {
    api_key: geminiInput?.value.trim() || "",
  };

  try {
    // Save Notifications
    const r1 = await fetch(`${API}/api/settings/notifications`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(newNotifSettings),
    });

    // Save Cloud
    const r2 = await fetch(`${API}/api/settings/cloud`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(newCloudSettings),
    });

    const d1 = await r1.json();
    const d2 = await r2.json();

    if (d1.status === "ok" && d2.status === "ok") {
      _notificationSettings = newNotifSettings;
      _cloudSettings = newCloudSettings;
      showToast("✅ Settings Saved", "info");
      return true;
    } else {
      showToast("❌ Save Failed", "error");
    }
  } catch (e) {
    console.error("Save error:", e);
    showToast("❌ Save Failed", "error");
  }
  return false;
}

/** Send a test SMS */
export async function sendTestSms() {
  try {
    showToast("Sending test SMS...", "info");
    const r = await fetch(`${API}/api/notifications/test`, { method: "POST" });
    const d = await r.json();
    if (d.success) {
      showToast("✅ Test SMS Sent", "info");
    } else {
      showToast("❌ Test Failed (Check Credentials)", "error");
    }
  } catch {
    showToast("❌ Test Failed", "error");
  }
}

/** Test Gemini Connection */
export async function verifyGemini() {
  try {
    showToast("Verifying Gemini connection...", "info");
    const r = await fetch(`${API}/api/cloud/test`, { method: "POST" });
    const d = await r.json();
    if (d.success) {
      showToast("✅ Gemini Connection Verified", "info");
    } else {
      showToast(`❌ Connection Failed: ${d.error || "Unknown Error"}`, "error");
    }
  } catch {
    showToast("❌ Cloud Test Failed", "error");
  }
}

/** Toggle the settings modal visibility */
export function toggleSettingsModal(show) {
  const modal = document.getElementById("settingsModal");
  if (!modal) return;

  if (show) {
    loadSettings();
    modal.classList.remove("hidden");
    modal.classList.add("flex");
  } else {
    modal.classList.add("hidden");
    modal.classList.remove("flex");
  }
}

/** Initialize settings modal listeners */
export function initSettingsUI() {
  const saveBtn = document.getElementById("saveSettingsBtn");
  const testSmsBtn = document.getElementById("testSmsBtn");
  const testGeminiBtn = document.getElementById("testGeminiBtn");
  const closeBtn = document.getElementById("closeSettingsBtn");

  saveBtn?.addEventListener("click", saveAllSettings);
  testSmsBtn?.addEventListener("click", sendTestSms);
  testGeminiBtn?.addEventListener("click", verifyGemini);
  closeBtn?.addEventListener("click", () => toggleSettingsModal(false));
  document.getElementById("connectGoogleBtn")?.addEventListener("click", connectGoogle);
  document.getElementById("revokeGoogleBtn")?.addEventListener("click", revokeGoogle);
  document.getElementById("refreshGoogleBtn")?.addEventListener("click", refreshGoogle);
}
