/**
 * Piper TTS — audio playback + voice controls for the chat UI.
 * Star-topology: imports from state.js / utils.js, exports initTTS + speakText + renderTTSButton.
 */

import { API } from "./state.js";
import { showToast } from "./utils.js";

// ── Module state ───────────────────────────────────────────────
let ttsAvailable = false;
let ttsEnabled = localStorage.getItem("localmind_tts_enabled") === "true";
let selectedVoice = localStorage.getItem("localmind_tts_voice") || null;
let voices = [];
let currentAudio = null;
let speakingIndicator = null;

// ── Helpers ────────────────────────────────────────────────────

/** Strip markdown / HTML so we send clean plaintext to the TTS engine. */
function stripForTTS(text) {
  return text
    .replace(/```[\s\S]*?```/g, "")          // fenced code blocks
    .replace(/`[^`]+`/g, "")                  // inline code
    .replace(/!\[[^\]]*\]\([^)]*\)/g, "")     // images
    .replace(/\[[^\]]*\]\([^)]*\)/g, "")      // links
    .replace(/<[^>]+>/g, "")                   // HTML tags
    .replace(/#{1,6}\s*/g, "")                 // headings
    .replace(/(\*{1,3}|_{1,3})/g, "")         // bold/italic markers
    .replace(/~~[^~]+~~/g, "")                // strikethrough
    .replace(/^[-*+]\s+/gm, "")               // list bullets
    .replace(/^\d+\.\s+/gm, "")               // numbered lists
    .replace(/^>\s+/gm, "")                   // blockquotes
    .replace(/---+/g, "")                      // horizontal rules
    .replace(/\n{2,}/g, "\n")                 // collapse blank lines
    .trim();
}

// ── Styles ─────────────────────────────────────────────────────

function _injectStyles() {
  if (document.getElementById("tts-styles")) return;
  const style = document.createElement("style");
  style.id = "tts-styles";
  style.textContent = `
    /* TTS toggle active glow */
    .tts-active {
      color: #818cf8 !important;
      filter: drop-shadow(0 0 6px rgba(99, 102, 241, 0.5));
    }

    /* Speaking indicator — pulsing dot */
    .tts-speaking {
      display: inline-flex;
      align-items: center;
      gap: 6px;
      font-size: 11px;
      color: #818cf8;
      padding: 2px 8px;
      border-radius: 9999px;
      background: rgba(99, 102, 241, 0.1);
      animation: tts-pulse 1.2s ease-in-out infinite;
    }
    .tts-speaking-dot {
      width: 6px;
      height: 6px;
      border-radius: 50%;
      background: #818cf8;
      animation: tts-pulse 1.2s ease-in-out infinite;
    }
    @keyframes tts-pulse {
      0%, 100% { opacity: 1; }
      50% { opacity: 0.4; }
    }

    /* Voice selector dropdown */
    .tts-voice-selector {
      position: absolute;
      bottom: calc(100% + 8px);
      left: 0;
      min-width: 220px;
      background: #1c1b1b;
      border: 1px solid rgba(68, 71, 72, 0.4);
      border-radius: 12px;
      padding: 8px;
      z-index: 100;
      box-shadow: 0 8px 24px rgba(0, 0, 0, 0.5);
    }
    .tts-voice-selector select {
      width: 100%;
      background: #2a2929;
      color: #c4c7c7;
      border: 1px solid rgba(68, 71, 72, 0.4);
      border-radius: 8px;
      padding: 6px 8px;
      font-size: 12px;
      outline: none;
    }
    .tts-voice-selector select:focus {
      border-color: rgba(99, 102, 241, 0.5);
    }
    .tts-voice-selector label {
      display: block;
      font-size: 10px;
      color: #888;
      margin-bottom: 4px;
      text-transform: uppercase;
      letter-spacing: 0.5px;
    }
    .tts-voice-settings-btn {
      position: relative;
    }

    /* Per-message play button */
    .tts-msg-play {
      display: inline-flex;
      align-items: center;
      justify-content: center;
      width: 26px;
      height: 26px;
      border-radius: 50%;
      background: transparent;
      border: 1px solid rgba(68, 71, 72, 0.3);
      color: #888;
      cursor: pointer;
      font-size: 14px;
      margin-left: 4px;
      vertical-align: middle;
      transition: all 0.15s ease;
      padding: 0;
      line-height: 1;
    }
    .tts-msg-play:hover {
      color: #818cf8;
      border-color: rgba(99, 102, 241, 0.4);
      background: rgba(99, 102, 241, 0.08);
    }
    .tts-msg-play .material-symbols-outlined {
      font-size: 16px;
    }
    .tts-msg-play.playing {
      color: #818cf8;
      border-color: rgba(99, 102, 241, 0.4);
      background: rgba(99, 102, 241, 0.12);
    }
  `;
  document.head.appendChild(style);
}

// ── UI Wiring ──────────────────────────────────────────────────

function updateToggleUI() {
  const btn = document.getElementById("ttsToggleBtn");
  if (!btn) return;
  const icon = btn.querySelector(".material-symbols-outlined");
  if (ttsEnabled) {
    btn.classList.add("tts-active");
    if (icon) icon.textContent = "volume_up";
    btn.title = "Text-to-speech ON (click to disable)";
  } else {
    btn.classList.remove("tts-active");
    if (icon) icon.textContent = "volume_off";
    btn.title = "Text-to-speech OFF (click to enable)";
  }
}

function createVoiceSelector() {
  const btn = document.getElementById("ttsToggleBtn");
  if (!btn) return;

  // Settings gear button next to toggle
  const settingsBtn = document.createElement("button");
  settingsBtn.className =
    "tts-voice-settings-btn p-2.5 text-outline hover:text-primary transition-colors hover:bg-surface-variant/50 rounded-xl";
  settingsBtn.innerHTML = '<span class="material-symbols-outlined text-[18px]">tune</span>';
  settingsBtn.title = "TTS voice settings";
  settingsBtn.id = "ttsSettingsBtn";

  // Dropdown panel
  const dropdown = document.createElement("div");
  dropdown.className = "tts-voice-selector";
  dropdown.style.display = "none";
  dropdown.innerHTML = `
    <label>Voice</label>
    <select id="ttsVoiceSelect"></select>
  `;

  settingsBtn.appendChild(dropdown);
  btn.parentElement.appendChild(settingsBtn);

  // Toggle dropdown
  settingsBtn.addEventListener("click", (e) => {
    e.stopPropagation();
    const isOpen = dropdown.style.display !== "none";
    dropdown.style.display = isOpen ? "none" : "block";
  });

  // Close on outside click
  document.addEventListener("click", (e) => {
    if (!settingsBtn.contains(e.target)) {
      dropdown.style.display = "none";
    }
  });

  populateVoiceDropdown();
}

function populateVoiceDropdown() {
  const select = document.getElementById("ttsVoiceSelect");
  if (!select) return;
  select.innerHTML = '<option value="">Default</option>';
  voices.forEach((v) => {
    const opt = document.createElement("option");
    opt.value = v.name;
    opt.textContent = `${v.name} (${v.language || "en"})`;
    if (v.name === selectedVoice) opt.selected = true;
    select.appendChild(opt);
  });

  select.addEventListener("change", () => {
    selectedVoice = select.value || null;
    if (selectedVoice) {
      localStorage.setItem("localmind_tts_voice", selectedVoice);
    } else {
      localStorage.removeItem("localmind_tts_voice");
    }
  });
}

function showSpeakingIndicator() {
  removeSpeakingIndicator();
  const container = document.getElementById("messagesContainer");
  if (!container) return;
  speakingIndicator = document.createElement("div");
  speakingIndicator.className = "tts-speaking";
  speakingIndicator.innerHTML = '<span class="tts-speaking-dot"></span> Speaking...';
  container.appendChild(speakingIndicator);
  container.scrollTop = container.scrollHeight;
}

function removeSpeakingIndicator() {
  if (speakingIndicator) {
    speakingIndicator.remove();
    speakingIndicator = null;
  }
}

// ── Core TTS ───────────────────────────────────────────────────

/**
 * Synthesize text via Piper TTS and play it.
 * @param {string} text — raw message text (may contain markdown/HTML)
 * @returns {Promise<void>}
 */
export async function speakText(text) {
  const clean = stripForTTS(text);
  if (!clean) return;

  // Stop any currently playing audio
  if (currentAudio) {
    currentAudio.pause();
    currentAudio = null;
    removeSpeakingIndicator();
  }

  try {
    const res = await fetch(`${API}/api/tts/synthesize`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        text: clean,
        voice: selectedVoice,
        speed: 1.0,
      }),
    });

    const data = await res.json();

    if (!data.ok) {
      showToast(data.error || "TTS synthesis failed", "error");
      return;
    }

    // Extract just the filename from audio_path
    const filename = data.audio_path.split("/").pop().split("\\").pop();
    const audioUrl = `${API}/api/tts/audio/${filename}`;

    currentAudio = new Audio(audioUrl);
    showSpeakingIndicator();

    currentAudio.addEventListener("ended", () => {
      removeSpeakingIndicator();
      currentAudio = null;
    });

    currentAudio.addEventListener("error", () => {
      removeSpeakingIndicator();
      currentAudio = null;
      showToast("Audio playback failed", "error");
    });

    await currentAudio.play();
  } catch (err) {
    console.error("[TTS] Synthesis error:", err);
    removeSpeakingIndicator();
    showToast("TTS unavailable", "error");
  }
}

// ── Per-message play button ────────────────────────────────────

/**
 * Returns an HTML string for a small play button to append to AI messages.
 * Attach a click handler via event delegation or inline onclick.
 * @param {string} text — the raw message text
 * @returns {string} HTML string
 */
export function renderTTSButton(text) {
  if (!ttsAvailable) return "";
  // Encode text as a data attribute (base64 to avoid escaping issues)
  const encoded = btoa(unescape(encodeURIComponent(text)));
  return `<button class="tts-msg-play" data-tts-text="${encoded}" title="Play with TTS" aria-label="Play with TTS">
    <span class="material-symbols-outlined">volume_up</span>
  </button>`;
}

// Delegate click handling for per-message play buttons
function wireMessagePlayButtons() {
  document.addEventListener("click", (e) => {
    const btn = e.target.closest(".tts-msg-play");
    if (!btn) return;
    const encoded = btn.dataset.ttsText;
    if (!encoded) return;

    try {
      const text = decodeURIComponent(escape(atob(encoded)));

      // Toggle playing state
      if (btn.classList.contains("playing") && currentAudio) {
        currentAudio.pause();
        currentAudio = null;
        btn.classList.remove("playing");
        removeSpeakingIndicator();
        return;
      }

      // Remove playing class from all other buttons
      document.querySelectorAll(".tts-msg-play.playing").forEach((b) => b.classList.remove("playing"));
      btn.classList.add("playing");

      speakText(text).finally(() => {
        btn.classList.remove("playing");
      });
    } catch (err) {
      console.error("[TTS] Play button error:", err);
    }
  });
}

// ── Init ───────────────────────────────────────────────────────

/**
 * Initialize the Piper TTS subsystem.
 * Checks backend status, loads voices, wires UI controls.
 */
export async function initTTS() {
  _injectStyles();

  const toggleBtn = document.getElementById("ttsToggleBtn");
  if (!toggleBtn) return;

  // Check TTS backend status
  try {
    const res = await fetch(`${API}/api/tts/status`);
    if (!res.ok) throw new Error("TTS status endpoint unreachable");
    const status = await res.json();

    if (!status.enabled || !status.available) {
      // Hide the TTS button entirely — backend has no TTS
      toggleBtn.style.display = "none";
      return;
    }

    ttsAvailable = true;
  } catch {
    // TTS not available — hide controls silently
    toggleBtn.style.display = "none";
    return;
  }

  // Load voices
  try {
    const res = await fetch(`${API}/api/tts/voices`);
    if (res.ok) {
      const data = await res.json();
      voices = data.voices || [];
    }
  } catch {
    // Non-fatal — use default voice
  }

  // Wire toggle button
  updateToggleUI();
  toggleBtn.addEventListener("click", () => {
    ttsEnabled = !ttsEnabled;
    localStorage.setItem("localmind_tts_enabled", ttsEnabled.toString());
    updateToggleUI();
    showToast(ttsEnabled ? "TTS enabled" : "TTS disabled", "info");
  });

  // Create voice selector
  createVoiceSelector();

  // Wire per-message play buttons (event delegation)
  wireMessagePlayButtons();
}

/**
 * Whether auto-TTS is currently enabled by the user.
 * Chat.js can check this to decide whether to call speakText on new AI responses.
 */
export function isTTSEnabled() {
  return ttsAvailable && ttsEnabled;
}
