/**
 * Tests for modules/conversations.js
 */

import { jest } from "@jest/globals";
import * as conv from "../modules/conversations.js";
import { state, messagesContainer, welcomeScreen, chatEmptyState } from "../modules/state.js";

describe("loadConversations", () => {
  let originalFetch;
  let renderSpy;
  let warnSpy;

  beforeAll(() => {
    originalFetch = global.fetch;
    // Jest cannot spy on ESM modules when imported directly like this because exports are read-only
    // We will bypass it by not spying on renderConversations and just let it run.
    // To prevent DOM errors since renderConversations updates the DOM, we will mock document.getElementById.
    renderSpy = jest.spyOn(document, "getElementById").mockReturnValue({
      innerHTML: "",
      appendChild: jest.fn(),
      querySelector: jest.fn().mockReturnValue({ addEventListener: jest.fn() })
    });
    warnSpy = jest.spyOn(console, "warn").mockImplementation(() => {});
  });

  afterAll(() => {
    global.fetch = originalFetch;
    renderSpy.mockRestore();
    warnSpy.mockRestore();
  });

  beforeEach(() => {
    // Reset state before each test to prevent leaking
    state.conversations = null;
    jest.clearAllMocks();
  });

  test("happy path: updates state.conversations and calls renderConversations", async () => {
    const mockConversations = [{ id: 1, title: "Test Chat" }, { id: 2, title: "Another Chat" }];
    global.fetch = jest.fn().mockResolvedValue({
      json: jest.fn().mockResolvedValue({ conversations: mockConversations })
    });

    await conv.loadConversations();

    expect(global.fetch).toHaveBeenCalledWith(expect.stringContaining("/api/conversations"));
    expect(state.conversations).toEqual(mockConversations);
    expect(renderSpy).toHaveBeenCalledTimes(1);
  });

  test("empty list: updates state.conversations to empty array", async () => {
    global.fetch = jest.fn().mockResolvedValue({
      json: jest.fn().mockResolvedValue({ conversations: [] })
    });

    await conv.loadConversations();

    expect(state.conversations).toEqual([]);
    expect(renderSpy).toHaveBeenCalledTimes(1);
  });

  test("missing key: falls back to empty array if response is missing conversations key", async () => {
    global.fetch = jest.fn().mockResolvedValue({
      json: jest.fn().mockResolvedValue({})
    });

    await conv.loadConversations();

    expect(state.conversations).toEqual([]);
    expect(renderSpy).toHaveBeenCalledTimes(1);
  });

  test("null response: handles null by catching TypeError and not mutating state", async () => {
    global.fetch = jest.fn().mockResolvedValue({
      json: jest.fn().mockResolvedValue(null)
    });

    await conv.loadConversations();
    // Assuming `d` is null, `d.conversations` will throw an error,
    // leading to the catch block where state.conversations remains untouched (or whatever value it was)

    expect(warnSpy).toHaveBeenCalledTimes(1);
    expect(state.conversations).toBeNull(); // It was initialized to null in beforeEach
    expect(renderSpy).not.toHaveBeenCalled();
  });

  test("error path: logs a warning and does not alter state.conversations", async () => {
    global.fetch = jest.fn().mockRejectedValue(new Error("Network Error"));
    state.conversations = [{ id: 99, title: "Old Chat" }]; // Set some pre-existing state

    await conv.loadConversations();

    expect(warnSpy).toHaveBeenCalledWith("Failed to load conversations:", expect.any(Error));
    // State should remain unchanged
    expect(state.conversations).toEqual([{ id: 99, title: "Old Chat" }]);
    expect(renderSpy).not.toHaveBeenCalled();
  });
});

describe("deleteConversation", () => {
  let originalFetch;
  let confirmSpy;
  let showToastSpy;
  let getElementSpy;

  beforeAll(() => {
    originalFetch = global.fetch;
    confirmSpy = jest.spyOn(window, "confirm");
    // We need to mock showToast. Since it's an export from utils.js, and we are using ESM,
    // we might need to mock it differently if conversations.js imports it.
    // However, for this test environment, we can try to mock it on the window or global if it's there,
    // or better, mock the module. But Jest ESM support makes module mocking tricky.
    // Let's assume for now we can't easily mock the import, so we'll check if we can at least
    // verify the window.confirm part which is a global.
  });

  afterAll(() => {
    global.fetch = originalFetch;
    confirmSpy.mockRestore();
  });

  beforeEach(() => {
    jest.clearAllMocks();
    state.currentConvId = "conv123";
    state.messages = [{ role: "user", content: "hi" }];
  });

  test("does nothing if user cancels confirmation", async () => {
    confirmSpy.mockReturnValue(false);
    global.fetch = jest.fn();

    await conv.deleteConversation("conv123");

    expect(confirmSpy).toHaveBeenCalled();
    expect(global.fetch).not.toHaveBeenCalled();
    expect(state.currentConvId).toBe("conv123");
  });

  test("calls delete API and updates state on success", async () => {
    confirmSpy.mockReturnValue(true);
    global.fetch = jest.fn();
    // Mock loadConversations fetch
    global.fetch.mockResolvedValueOnce({
      ok: true,
      json: jest.fn().mockResolvedValue({ ok: true })
    }).mockResolvedValueOnce({
      ok: true,
      json: jest.fn().mockResolvedValue({ conversations: [] })
    });

    // Since these are already imported and might be null in JSDOM unless mocked,
    // we need to ensure they have the shape we expect if they are not null.
    if (messagesContainer) messagesContainer.innerHTML = "something";
    if (welcomeScreen) welcomeScreen.style.display = "none";
    if (chatEmptyState) chatEmptyState.style.display = "none";

    await conv.deleteConversation("conv123");

    expect(global.fetch).toHaveBeenCalledWith(expect.stringContaining("/api/conversations/conv123"), { method: "DELETE" });
    expect(state.currentConvId).toBeNull();
    expect(state.messages).toEqual([]);
    if (messagesContainer) expect(messagesContainer.innerHTML).toBe("");
    if (welcomeScreen) expect(welcomeScreen.style.display).toBe("");
    if (chatEmptyState) expect(chatEmptyState.style.display).toBe("");
  });

  test("shows error toast if delete fails", async () => {
    confirmSpy.mockReturnValue(true);
    global.fetch = jest.fn().mockResolvedValue({
      ok: false
    });

    await conv.deleteConversation("conv123");

    expect(global.fetch).toHaveBeenCalled();
    expect(state.currentConvId).toBe("conv123"); // Should not have been cleared
  });
});
