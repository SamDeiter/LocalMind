/**
 * Shared application state, constants, and DOM references.
 * This module has ZERO external dependencies — it is the root of the import graph.
 */

let apiOrigin = window.location.origin;
// If running dev server on a different port locally, force backend port 8001
// (8000 is used by TradeCommander — LocalMind.bat launches on 8001)
if (window.location.hostname === "127.0.0.1" || window.location.hostname === "localhost") {
  apiOrigin = `http://${window.location.hostname}:8001`;
}
export const API = apiOrigin;

export const MODE_MODELS = {
  fast: "qwen2.5-coder:7b",
  deep: "qwen2.5-coder:32b",
  auto: "auto",
};

export const state = {
  conversations: [],
  currentConvId: null,
  messages: [],
  streaming: false,
  model: "auto",
  mode: localStorage.getItem("localmind_mode") || "auto",
  voiceEnabled: false, // Default OFF — user can toggle via speaker button
  capturedImage: null,
  abortController: null,
};

// Shared mutable editor state (lives here to avoid circular deps between chat ↔ editor)
export const editorState = {
  monacoEditor: null,
  currentPath: null,
};

// ── DOM helpers ─────────────────────────────────────────────────
export const $ = (s) => document.querySelector(s);

// ── DOM refs ────────────────────────────────────────────────────
// Tabs
export const tabWork = document.getElementById("tabWork");
export const tabChat = document.getElementById("tabChat");
export const tabSystem = document.getElementById("tabSystem");
// Work tab inputs
export const priorityInput = document.getElementById("priorityInput");
export const priorityContainer = $("#priorityList");
export const addPriorityBtn = document.getElementById("addPriorityBtn");
export const workAttachBtn = document.getElementById("workAttachBtn");
export const workCameraBtn = document.getElementById("workCameraBtn");
export const workImagePreview = document.getElementById("workImagePreview");
export const workPreviewImg = document.getElementById("workPreviewImg");
export const workRemoveImageBtn = document.getElementById("workRemoveImageBtn");
// Chat elements
export const newChatBtn = $("#newChatInlineBtn");
export const conversationList = $("#conversationList");
export const sendBtn = $("#sendBtn");
export const messageInput = $("#messageInput");
export const messagesContainer = $("#messagesContainer");
export const welcomeScreen = $("#welcomeScreen");
export const chatScreen = document.getElementById("chatPanel");
// Voice / camera
export const micBtn = $("#micBtn");
export const voiceBtn = $("#voiceBtn");
export const voiceSelect = $("#voiceSelect");
export const openCameraBtn = $("#openCameraBtn");
export const closeCameraBtn = $("#closeCameraBtn");
export const snapBtn = $("#snapBtn");
export const captureCanvas = $("#captureCanvas");
export const cameraModal = $("#cameraModal");
export const cameraPreview = $("#cameraPreview");
// Misc / settings
export const modelSelect = $("#modelSelect");
export const systemPromptText = $("#systemPromptText");
export const learningToggle = $("#learningToggle");
export const loadingStatus = $("#loadingStatus");
// Editor (now in System accordion — panelDivider still exists)
export const editorPanel = null; // removed — editor now in System accordion
export const panelDivider = document.getElementById("panelDivider");
export const editorToggle = document.getElementById("editorToggle");
// Sidebar compatibility shims (point to hidden shim elements)
export const sidebar = null;
export const sidebarToggle = null;
export const imagePreview = $("#sidebarImagePreview");
export const previewImg = $("#sidebarPreviewImg");
export const removeImageBtn = $("#sidebarRemoveImageBtn");
export const uploadBtn = $("#sidebarUploadBtn");
export const cameraBtn = $("#sidebarCameraBtn");
// Removed refs (null for back-compat)
export const insightContent = null;
export const brainDigest = null;
export const homeBtn = null;
export const modeSupervisedBtn = null;
export const modeAutonomousBtn = null;
export const overviewBtn = null;

// ── Smart Scroll ────────────────────────────────────────────────
let _userScrolledUp = false;

function _isNearBottom() {
  if (!messagesContainer) return true;
  const threshold = 80; // px from bottom
  return (
    messagesContainer.scrollHeight - messagesContainer.scrollTop - messagesContainer.clientHeight <
    threshold
  );
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
