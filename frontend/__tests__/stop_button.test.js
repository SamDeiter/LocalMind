import { jest } from "@jest/globals";
import { state } from "../modules/state.js";
import { bindEvents } from "../modules/events.js";

describe("Stop Button Logic", () => {
  beforeEach(() => {
    document.body.innerHTML = `
      <button id="stopBtn"></button>
    `;
  });

  test("clicking stopBtn calls abort on state.abortController if active", () => {
    state.abortController = {
      abort: jest.fn(),
    };

    bindEvents();

    const stopBtn = document.getElementById("stopBtn");
    expect(stopBtn).not.toBeNull();

    stopBtn.click();

    expect(state.abortController.abort).toHaveBeenCalledTimes(1);
  });

  test("clicking stopBtn does not throw if state.abortController is null", () => {
    state.abortController = null;

    bindEvents();

    const stopBtn = document.getElementById("stopBtn");
    expect(stopBtn).not.toBeNull();

    expect(() => {
      stopBtn.click();
    }).not.toThrow();
  });
});
