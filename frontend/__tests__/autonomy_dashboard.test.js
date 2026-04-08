/**
 * Tests for modules/autonomy/dashboard.js — brain dashboard rendering.
 */

import { brainState } from "../modules/autonomy/constants.js";
import { updateBrainUptime, updateSuccessRate } from "../modules/autonomy/dashboard.js";

describe("updateBrainUptime", () => {
  beforeEach(() => {
    brainState.bootTime = null;
    document.body.innerHTML = '<span id="brainUptime"></span>';
  });

  test("does nothing when bootTime is null", () => {
    updateBrainUptime();
    expect(document.getElementById("brainUptime").textContent).toBe("");
  });

  test("displays uptime when bootTime is set", () => {
    // Set boot time to 2 hours ago
    brainState.bootTime = Date.now() - 2 * 3600000;
    updateBrainUptime();
    const text = document.getElementById("brainUptime").textContent;
    expect(text).toMatch(/^\d+\.\dh$/);
    // Should be approximately 2.0h
    const hours = parseFloat(text);
    expect(hours).toBeGreaterThanOrEqual(1.9);
    expect(hours).toBeLessThanOrEqual(2.1);
  });

  test("does nothing when element is missing", () => {
    document.body.innerHTML = "";
    brainState.bootTime = Date.now();
    // Should not throw
    expect(() => updateBrainUptime()).not.toThrow();
  });
});

describe("updateSuccessRate", () => {
  beforeEach(() => {
    brainState.proposalCount = 0;
    brainState.executedCount = 0;
    document.body.innerHTML = '<span id="brainSuccessRate"></span>';
  });

  test("shows 100% when no proposals", () => {
    updateSuccessRate();
    expect(document.getElementById("brainSuccessRate").textContent).toBe("100%");
  });

  test("calculates correct percentage", () => {
    brainState.proposalCount = 10;
    brainState.executedCount = 7;
    updateSuccessRate();
    expect(document.getElementById("brainSuccessRate").textContent).toBe("70%");
  });

  test("rounds percentage", () => {
    brainState.proposalCount = 3;
    brainState.executedCount = 1;
    updateSuccessRate();
    expect(document.getElementById("brainSuccessRate").textContent).toBe("33%");
  });

  test("does nothing when element is missing", () => {
    document.body.innerHTML = "";
    brainState.proposalCount = 5;
    expect(() => updateSuccessRate()).not.toThrow();
  });
});
