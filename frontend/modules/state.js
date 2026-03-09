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
  voiceEnabled: localStorage.getItem("localmind_voice") === "on",
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
export const sendBtn = $("#sendBtn");
export const messageInput = $("#messageInput");
export const messagesContainer = $("#messagesArea");
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
export const imagePreview = $("#imagePreview");
export const previewImg = $("#previewImg");
export const removeImageBtn = $("#removeImageBtn");
export const micBtn = $("#micBtn");
export const editorPanel = document.getElementById("editorPanel");
export const panelDivider = document.getElementById("panelDivider");
export const editorToggle = document.getElementById("editorToggle");

// ── DOM utilities ───────────────────────────────────────────────
export function scrollToBottom() {
  messagesContainer.scrollTop = messagesContainer.scrollHeight;
}

export function autoResize() {
  messageInput.style.height = "auto";
  messageInput.style.height = Math.min(messageInput.scrollHeight, 150) + "px";
}
