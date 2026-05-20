/**
 * UX and Accessibility tests for modules/conversations.js
 */

import { jest } from "@jest/globals";
import * as conv from "../modules/conversations.js";
import { state } from "../modules/state.js";

describe("renderConversations", () => {
  let list;

  beforeEach(() => {
    document.body.innerHTML = '<div id="conversationList"></div>';
    list = document.getElementById("conversationList");
    state.conversations = [
      { id: "conv1", title: "Test Chat 1" },
      { id: "conv2", title: "Test Chat 2" }
    ];
    state.currentConvId = "conv1";
  });

  test("adds accessibility attributes and data-id to conversation items", () => {
    conv.renderConversations();

    const items = list.querySelectorAll(".conversation-item");
    expect(items.length).toBe(2);

    const firstItem = items[0];
    expect(firstItem.dataset.id).toBe("conv1");

    // Check for Load button
    const loadBtn = firstItem.querySelector(".load-btn");
    expect(loadBtn.getAttribute("aria-label")).toBe("Load conversation: Test Chat 1");
    expect(loadBtn.textContent.trim()).toBe("Test Chat 1");

    // Check for Material Symbols
    const deleteBtn = firstItem.querySelector(".delete-btn");
    expect(deleteBtn.getAttribute("aria-label")).toBe("Delete conversation");
    expect(deleteBtn.querySelector(".material-symbols-outlined").textContent).toBe("delete");

    const exportBtn = firstItem.querySelector(".export-btn");
    expect(exportBtn.getAttribute("aria-label")).toBe("Export conversation");
    expect(exportBtn.querySelector(".material-symbols-outlined").textContent).toBe("download");
  });
});

describe("deleteConversation", () => {
  let confirmSpy;
  let originalFetch;

  beforeEach(() => {
    confirmSpy = jest.spyOn(window, "confirm").mockImplementation(() => true);
    originalFetch = global.fetch;
    global.fetch = jest.fn().mockResolvedValue({ ok: true, json: () => Promise.resolve({ status: "ok" }) });
  });

  afterEach(() => {
    confirmSpy.mockRestore();
    global.fetch = originalFetch;
    jest.restoreAllMocks();
  });

  test("calls confirm and aborts if cancelled", async () => {
    confirmSpy.mockReturnValue(false);
    await conv.deleteConversation("conv1");
    expect(confirmSpy).toHaveBeenCalled();
    expect(global.fetch).not.toHaveBeenCalled();
  });

  test("proceeds with deletion if confirmed", async () => {
    await conv.deleteConversation("conv1");
    expect(confirmSpy).toHaveBeenCalled();
    expect(global.fetch).toHaveBeenCalledWith(expect.stringContaining("/api/conversations/conv1"), { method: "DELETE" });
  });
});
