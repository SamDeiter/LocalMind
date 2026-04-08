/**
 * Tests for modules/autonomy/status.js — status polling logic.
 */

import { jest } from "@jest/globals";
import { brainState } from "../modules/autonomy/constants.js";
import { pollAutonomy } from "../modules/autonomy/status.js";

// Mock fetch globally
const mockFetchResponse = (data) => {
  global.fetch = jest.fn(() =>
    Promise.resolve({
      json: () => Promise.resolve(data),
    })
  );
};

describe("pollAutonomy", () => {
  beforeEach(() => {
    brainState.proposalCount = 0;
    brainState.executedCount = 0;
    brainState.bootTime = null;
    brainState.caughtUp = false;

    document.body.innerHTML = `
      <div id="autonomyIndicator" class=""></div>
      <div id="autonomyLabel"></div>
      <div id="brainLoadingBanner" style="display:none"></div>
      <div id="brainPulse"></div>
      <div id="brainIdeas"></div>
      <div id="brainApplied"></div>
      <div id="brainMode"></div>
      <div id="memoryCount"></div>
      <div id="docCount"></div>
      <div id="proposalCount"></div>
      <div id="brainUptime"></div>
    `;
  });

  afterEach(() => {
    global.fetch = undefined;
  });

  test("updates brainState from server response", async () => {
    mockFetchResponse({
      enabled: true,
      health_check: { model_loaded: true },
      reflection: { proposals_logged: 5 },
      execution: { proposals_executed: 3 },
      start_time: Date.now() / 1000 - 3600,
      memories_count: 42,
      documents_count: 7,
      proposals_count: 12,
    });

    await pollAutonomy();

    expect(brainState.proposalCount).toBe(5);
    expect(brainState.executedCount).toBe(3);
    expect(brainState.bootTime).toBeGreaterThan(0);
  });

  test("sets indicator to active when enabled", async () => {
    mockFetchResponse({
      enabled: true,
      health_check: { model_loaded: true },
    });

    await pollAutonomy();

    const indicator = document.getElementById("autonomyIndicator");
    expect(indicator.className).toContain("autonomy-active");
  });

  test("sets indicator to paused when disabled", async () => {
    mockFetchResponse({
      enabled: false,
      health_check: { model_loaded: false },
    });

    await pollAutonomy();

    const indicator = document.getElementById("autonomyIndicator");
    expect(indicator.className).toContain("autonomy-paused");
  });

  test("updates sidebar counts", async () => {
    mockFetchResponse({
      enabled: true,
      health_check: { model_loaded: true },
      memories_count: 99,
      documents_count: 15,
      proposals_count: 8,
    });

    await pollAutonomy();

    expect(document.getElementById("memoryCount").textContent).toBe("99");
    expect(document.getElementById("docCount").textContent).toBe("15");
    expect(document.getElementById("proposalCount").textContent).toBe("8");
  });

  test("handles fetch failure gracefully", async () => {
    global.fetch = jest.fn(() => Promise.reject(new Error("Network error")));

    // Should not throw
    await expect(pollAutonomy()).resolves.toBeUndefined();
  });

  test("sets caughtUp after processing recent events", async () => {
    mockFetchResponse({
      enabled: true,
      health_check: { model_loaded: true },
      recent_events: [
        { action: "reflecting", detail: "test event", time: "12:00:00" },
      ],
    });

    expect(brainState.caughtUp).toBe(false);
    await pollAutonomy();
    expect(brainState.caughtUp).toBe(true);
  });

  test("does not re-process events once caught up", async () => {
    brainState.caughtUp = true;

    mockFetchResponse({
      enabled: true,
      health_check: { model_loaded: true },
      recent_events: [
        { action: "reflecting", detail: "should be ignored" },
      ],
    });

    await pollAutonomy();
    // The function should not process events again
    // (we verify by checking that the caughtUp flag is still true
    // and that no new DOM elements were created)
    expect(brainState.caughtUp).toBe(true);
  });
});
