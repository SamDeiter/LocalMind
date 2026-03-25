/**
 * Shared application state, constants, and DOM references.
 * This module has ZERO external dependencies — it is the root of the import graph.
 */

export const API = window.location.origin;

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
  voiceEnabled: false,  // Default OFF — user can toggle via speaker button
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
export const sidebar = $("#sidebar");
export const sidebarToggle = $("#sidebarToggle");
export const newChatBtn = $("#newChatBtn");
export const conversationList = $("#conversationList");
export const learningToggle = $("#learningToggle");
export const modelSelect = $("#modelSelect");
export const systemPromptText = $("#systemPromptText");
export const sendBtn = $("#addPriorityBtn");
export const messageInput = $("#priorityInput");
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
export const editorPanel = document.getElementById("editorPanel");
export const panelDivider = document.getElementById("panelDivider");
export const editorToggle = document.getElementById("editorToggle");
export const priorityInput = document.getElementById("priorityInput");
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

