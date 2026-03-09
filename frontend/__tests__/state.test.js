/**
 * Tests for modules/state.js — state initialization and defaults.
 */

import { API, state, MODE_MODELS, editorState } from "../modules/state.js";

describe("state defaults", () => {
  test("API is defined", () => {
    expect(API).toBeDefined();
  });

  test("state has expected shape", () => {
    expect(state).toHaveProperty("conversations");
    expect(state).toHaveProperty("currentConvId");
    expect(state).toHaveProperty("messages");
    expect(state).toHaveProperty("streaming");
    expect(state).toHaveProperty("model");
    expect(state).toHaveProperty("mode");
    expect(state).toHaveProperty("voiceEnabled");
    expect(state).toHaveProperty("capturedImage");
    expect(state).toHaveProperty("abortController");
  });

  test("state defaults are correct", () => {
    expect(Array.isArray(state.conversations)).toBe(true);
    expect(state.currentConvId).toBeNull();
    expect(state.streaming).toBe(false);
    expect(state.model).toBe("auto");
    expect(state.capturedImage).toBeNull();
  });

  test("MODE_MODELS has expected modes", () => {
    expect(MODE_MODELS.fast).toBeDefined();
    expect(MODE_MODELS.deep).toBeDefined();
    expect(MODE_MODELS.auto).toBe("auto");
  });

  test("editorState initialized", () => {
    expect(editorState.monacoEditor).toBeNull();
    expect(editorState.currentPath).toBeNull();
  });
});
