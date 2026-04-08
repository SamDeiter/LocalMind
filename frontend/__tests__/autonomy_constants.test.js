/**
 * Tests for modules/autonomy/constants.js — brain state and action icons.
 */

import { brainState, ACTION_ICONS, MAX_ACTIVITY_ITEMS } from "../modules/autonomy/constants.js";

describe("brainState", () => {
  test("has expected shape and defaults", () => {
    expect(brainState).toHaveProperty("proposalCount");
    expect(brainState).toHaveProperty("executedCount");
    expect(brainState).toHaveProperty("bootTime");
    expect(brainState).toHaveProperty("caughtUp");
  });

  test("defaults are correct", () => {
    expect(brainState.proposalCount).toBe(0);
    expect(brainState.executedCount).toBe(0);
    expect(brainState.bootTime).toBeNull();
    expect(brainState.caughtUp).toBe(false);
  });

  test("is mutable (module-scoped shared state)", () => {
    const original = brainState.proposalCount;
    brainState.proposalCount = 42;
    expect(brainState.proposalCount).toBe(42);
    brainState.proposalCount = original;
  });
});

describe("ACTION_ICONS", () => {
  test("has icons for core actions", () => {
    expect(ACTION_ICONS.idle).toBeDefined();
    expect(ACTION_ICONS.thinking).toBeDefined();
    expect(ACTION_ICONS.reflecting).toBeDefined();
    expect(ACTION_ICONS.executing).toBeDefined();
    expect(ACTION_ICONS.completed).toBeDefined();
    expect(ACTION_ICONS.error).toBeDefined();
  });

  test("all values are strings (emoji icons)", () => {
    for (const [key, value] of Object.entries(ACTION_ICONS)) {
      expect(typeof value).toBe("string");
    }
  });
});

describe("MAX_ACTIVITY_ITEMS", () => {
  test("is a positive number", () => {
    expect(typeof MAX_ACTIVITY_ITEMS).toBe("number");
    expect(MAX_ACTIVITY_ITEMS).toBeGreaterThan(0);
  });
});
