/**
 * chat_ux.js -- Chat UX enhancements
 * ====================================
 * Rotating placeholder examples for the message input.
 * Only animates when the input is empty and unfocused.
 *
 * Exports:
 *   initPlaceholderRotation()  -- start cycling placeholder text every 3s
 */

import { messageInput } from "./state.js";

const PLACEHOLDERS = [
  "Research the latest AI news...",
  "Create a presentation about...",
  "Analyze this document...",
  "Write a Python script that...",
  "Summarize this codebase...",
  "Debug this error message...",
  "Draft an email about...",
  "Explain how RAG works...",
];

let _intervalId = null;
let _index = 0;

export function initPlaceholderRotation() {
  if (!messageInput) return;

  // Set initial placeholder
  _index = 0;
  messageInput.placeholder = PLACEHOLDERS[_index];

  // Only rotate when input is empty and not focused
  _intervalId = setInterval(() => {
    if (document.activeElement === messageInput) return;
    if (messageInput.value.trim() !== "") return;

    _index = (_index + 1) % PLACEHOLDERS.length;

    // Respect reduced-motion: skip fade animation
    const prefersReduced = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
    if (prefersReduced) {
      messageInput.placeholder = PLACEHOLDERS[_index];
      return;
    }

    // Fade transition via class
    messageInput.classList.add("placeholder-fade-out");
    setTimeout(() => {
      messageInput.placeholder = PLACEHOLDERS[_index];
      messageInput.classList.remove("placeholder-fade-out");
      messageInput.classList.add("placeholder-fade-in");
      setTimeout(() => {
        messageInput.classList.remove("placeholder-fade-in");
      }, 200);
    }, 200);
  }, 3000);

  // Restore default when focused
  messageInput.addEventListener("focus", () => {
    messageInput.placeholder = "Message LocalMind...";
  });

  // Resume rotation placeholders when blurred and empty
  messageInput.addEventListener("blur", () => {
    if (messageInput.value.trim() === "") {
      messageInput.placeholder = PLACEHOLDERS[_index];
    }
  });
}
