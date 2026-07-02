/**
 * Tests for chat accessibility enhancements.
 */

import { addTypingIndicator } from "../modules/chat.js";

describe("Chat Accessibility", () => {
  test("addTypingIndicator adds ARIA attributes for screen readers", () => {
    // Set up a mock message element
    const el = document.createElement("div");
    const content = document.createElement("div");
    content.className = "message-content";
    el.appendChild(content);

    // Add indicator
    addTypingIndicator(el);

    const dots = content.querySelector(".typing-dots");
    expect(dots).not.toBeNull();
    expect(dots.getAttribute("role")).toBe("status");
    expect(dots.getAttribute("aria-label")).toBe("AI is thinking");
  });
});
