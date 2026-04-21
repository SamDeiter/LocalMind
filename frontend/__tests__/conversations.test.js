/**
 * Tests for modules/conversations.js
 */

import { jest } from "@jest/globals";
import * as conversations from "../modules/conversations.js";
import { state } from "../modules/state.js";

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

    await conversations.loadConversations();

    expect(global.fetch).toHaveBeenCalledWith(expect.stringContaining("/api/conversations"));
    expect(state.conversations).toEqual(mockConversations);
    expect(renderSpy).toHaveBeenCalledTimes(1);
  });

  test("empty list: updates state.conversations to empty array", async () => {
    global.fetch = jest.fn().mockResolvedValue({
      json: jest.fn().mockResolvedValue({ conversations: [] })
    });

    await conversations.loadConversations();

    expect(state.conversations).toEqual([]);
    expect(renderSpy).toHaveBeenCalledTimes(1);
  });

  test("missing key: falls back to empty array if response is missing conversations key", async () => {
    global.fetch = jest.fn().mockResolvedValue({
      json: jest.fn().mockResolvedValue({})
    });

    await conversations.loadConversations();

    expect(state.conversations).toEqual([]);
    expect(renderSpy).toHaveBeenCalledTimes(1);
  });

  test("null response: handles null by catching TypeError and not mutating state", async () => {
    global.fetch = jest.fn().mockResolvedValue({
      json: jest.fn().mockResolvedValue(null)
    });

    await conversations.loadConversations();
    // Assuming `d` is null, `d.conversations` will throw an error,
    // leading to the catch block where state.conversations remains untouched (or whatever value it was)

    expect(warnSpy).toHaveBeenCalledTimes(1);
    expect(state.conversations).toBeNull(); // It was initialized to null in beforeEach
    expect(renderSpy).not.toHaveBeenCalled();
  });

  test("error path: logs a warning and does not alter state.conversations", async () => {
    global.fetch = jest.fn().mockRejectedValue(new Error("Network Error"));
    state.conversations = [{ id: 99, title: "Old Chat" }]; // Set some pre-existing state

    await conversations.loadConversations();

    expect(warnSpy).toHaveBeenCalledWith("Failed to load conversations:", expect.any(Error));
    // State should remain unchanged
    expect(state.conversations).toEqual([{ id: 99, title: "Old Chat" }]);
    expect(renderSpy).not.toHaveBeenCalled();
  });
});

describe("deleteConversation", () => {
  let originalFetch;
  let originalConfirm;

  beforeAll(() => {
    originalFetch = global.fetch;
    originalConfirm = global.confirm;
  });

  afterAll(() => {
    global.fetch = originalFetch;
    global.confirm = originalConfirm;
  });

  beforeEach(() => {
    jest.clearAllMocks();
  });

  test("cancels deletion if user denies confirm", async () => {
    global.confirm = jest.fn().mockReturnValue(false);
    global.fetch = jest.fn();

    await conversations.deleteConversation(123);

    expect(global.confirm).toHaveBeenCalled();
    expect(global.fetch).not.toHaveBeenCalled();
  });

  test("proceeds with deletion if user approves confirm", async () => {
    global.confirm = jest.fn().mockReturnValue(true);
    global.fetch = jest.fn().mockImplementation((url) => {
      if (url.includes("/api/conversations/123")) {
        return Promise.resolve({
          ok: true,
          json: () => Promise.resolve({ status: "ok" }),
        });
      }
      // Mock for loadConversations
      return Promise.resolve({
        ok: true,
        json: () => Promise.resolve({ conversations: [] }),
      });
    });

    await conversations.deleteConversation(123);

    expect(global.confirm).toHaveBeenCalled();
    expect(global.fetch).toHaveBeenCalledWith(expect.stringContaining("/api/conversations/123"), {
      method: "DELETE",
    });
  });
});
