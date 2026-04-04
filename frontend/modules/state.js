/**
 * Shared application state, constants, and DOM references.
 * This module has ZERO external dependencies — it is the root of the import graph.
 *
 * State objects are wrapped in Proxy for lightweight pub/sub.
 * Existing code that mutates state directly (e.g. `state.streaming = true`)
 * continues to work unchanged. Modules can optionally subscribe to changes:
 *
 *   import { state, onStateChange } from "./state.js";
 *   onStateChange("streaming", (val, old) => console.log("streaming:", old, "→", val));
 */

let apiOrigin = window.location.origin;
// If running a separate dev server locally, override to the backend default port
if (window.location.hostname === "127.0.0.1" || window.location.hostname === "localhost") {
  const backendPort = document.querySelector('meta[name="localmind-api-port"]')?.content || "8000";
  apiOrigin = `http://${window.location.hostname}:${backendPort}`;
}
export const API = apiOrigin;

export const MODE_MODELS = {
  fast: "qwen2.5-coder:7b",
  deep: "qwen2.5-coder:32b",
  auto: "auto",
};

// ── Pub/Sub helpers ────────────────────────────────────────────
const _listeners = new Map();

/**
 * Subscribe to changes on a specific state property.
 * @param {string} key   – property name on `state` or `editorState`
 * @param {Function} cb  – called as cb(newValue, oldValue, key)
 */
export function onStateChange(key, cb) {
  if (!_listeners.has(key)) _listeners.set(key, []);
  _listeners.get(key).push(cb);
}

/**
 * Unsubscribe a previously-registered callback.
 */
export function offStateChange(key, cb) {
  const cbs = _listeners.get(key);
  if (cbs) _listeners.set(key, cbs.filter(fn => fn !== cb));
}

function _notify(prop, value, old) {
  const cbs = _listeners.get(prop);
  if (cbs && cbs.length) cbs.forEach(cb => cb(value, old, prop));
}

function _makeReactive(raw) {
  return new Proxy(raw, {
    set(target, prop, value) {
      const old = target[prop];
      target[prop] = value;
      if (old !== value) _notify(prop, value, old);
      return true;
    },
  });
}

export const state = _makeReactive({
  conversations: [],
  currentConvId: null,
  messages: [],
  streaming: false,
  model: "auto",
  mode: localStorage.getItem("localmind_mode") || "auto",
  voiceEnabled: false,  // Default OFF — user can toggle via speaker button
  capturedImage: null,
  abortController: null,
});

// Shared mutable editor state (lives here to avoid circular deps between chat ↔ editor)
export const editorState = _makeReactive({
  monacoEditor: null,
  currentPath: null,
});

// ── DOM helpers ─────────────────────────────────────────────────
export const $ = (s) => document.querySelector(s);

// ── DOM refs ────────────────────────────────────────────────────
export const sidebar = $("#sidebar");
export const sidebarToggle = $("#sidebarToggle");
export const newChatBtn = $("#newChatBtn");
export const conversationList = $("#conversationList");
export const learningToggle = $("#learningToggle");
export const modelSelect = $("#modelSelect");
export const systemPromptText = $("#systemPromptText");
export const sendBtn = $("#sendBtn");
export const messageInput = $("#messageInput");
export const messagesContainer = $("#messagesContainer");
export const welcomeScreen = $("#welcomeScreen");
export const loadingStatus = $("#loadingStatus");
export const voiceBtn = $("#voiceBtn");
export const voiceSelect = $("#voiceSelect");
export const cameraModal = $("#cameraModal");
export const cameraPreview = $("#cameraPreview");
export const openCameraBtn = $("#openCameraBtn");
export const closeCameraBtn = $("#closeCameraBtn");
export const snapBtn = $("#snapBtn");
export const captureCanvas = $("#captureCanvas");
export const imagePreview = $("#sidebarImagePreview");
export const previewImg = $("#sidebarPreviewImg");
export const removeImageBtn = $("#sidebarRemoveImageBtn");
export const micBtn = $("#micBtn");
export const uploadBtn = $("#sidebarUploadBtn");
export const cameraBtn = $("#sidebarCameraBtn");
export const getEditorPanel = () => document.getElementById("editorPanel");
export const getPanelDivider = () => document.getElementById("panelDivider");
export const getEditorToggle = () => document.getElementById("editorToggle");
export const priorityInput = document.getElementById("priorityInput");
export const priorityContainer = $("#priorityList");
export const addPriorityBtn = document.getElementById("addPriorityBtn");
export const insightContent = document.getElementById("insightContent");
export const brainDigest = document.getElementById("brainDigest");
export const homeBtn = document.getElementById("homeBtn");
export const modeSupervisedBtn = document.getElementById("modeSupervisedBtn");
export const modeAutonomousBtn = document.getElementById("modeAutonomousBtn");
export const chatScreen = document.getElementById("chatScreen");
export const overviewBtn = document.getElementById("overviewBtn");

// ── Smart Scroll ────────────────────────────────────────────────
let _userScrolledUp = false;

function _isNearBottom() {
  if (!messagesContainer) return true;
  const threshold = 80; // px from bottom
  return (messagesContainer.scrollHeight - messagesContainer.scrollTop - messagesContainer.clientHeight) < threshold;
}

// Track when user scrolls up manually during streaming
if (typeof document !== "undefined") {
  document.addEventListener("DOMContentLoaded", () => {
    const mc = document.getElementById("messagesContainer");
    if (mc) {
      mc.addEventListener("scroll", () => {
        _userScrolledUp = !_isNearBottom();
      });
    }
  });
}

export function scrollToBottom(force = false) {
  if (!messagesContainer) return;
  if (force || !_userScrolledUp) {
    messagesContainer.scrollTop = messagesContainer.scrollHeight;
    _userScrolledUp = false;
  }
}

export function resetAutoScroll() {
  _userScrolledUp = false;
}

export function autoResize() {
  if (messageInput) {
    messageInput.style.height = "auto";
    messageInput.style.height = Math.min(messageInput.scrollHeight, 150) + "px";
  }
}

