/**
 * Tests for modules/sidebar.js
 */

import { toggleMemoryList } from "../modules/sidebar.js";

describe("toggleMemoryList", () => {
  beforeEach(() => {
    // Clear the document body before each test
    document.body.innerHTML = "";
  });

  test("toggles the 'open' class on memoryList element", () => {
    // Arrange
    document.body.innerHTML = '<div id="memoryList"></div>';
    const list = document.getElementById("memoryList");
    expect(list.classList.contains("open")).toBe(false);

    // Act
    toggleMemoryList();

    // Assert
    expect(list.classList.contains("open")).toBe(true);

    // Act again
    toggleMemoryList();

    // Assert again
    expect(list.classList.contains("open")).toBe(false);
  });

  test("does nothing and does not throw if memoryList element does not exist", () => {
    // Arrange
    expect(document.getElementById("memoryList")).toBeNull();

    // Act & Assert
    expect(() => {
      toggleMemoryList();
    }).not.toThrow();
  });
});
