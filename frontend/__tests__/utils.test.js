/**
 * Tests for modules/utils.js — pure functions.
 */

import {
  escapeHtml,
  getLang,
  getFileIcon,
  getFileExtension,
  extToLang,
  EXT_LANG,
} from "../modules/utils.js";

describe("escapeHtml", () => {
  test("escapes angle brackets", () => {
    expect(escapeHtml("<script>alert('xss')</script>")).not.toContain("<script>");
  });

  test("returns empty string for empty input", () => {
    expect(escapeHtml("")).toBe("");
  });

  test("preserves normal text", () => {
    expect(escapeHtml("hello world")).toBe("hello world");
  });
});

describe("getLang", () => {
  test("maps .py to python", () => {
    expect(getLang("main.py")).toBe("python");
  });

  test("maps .js to javascript", () => {
    expect(getLang("app.js")).toBe("javascript");
  });

  test("unknown extension returns plaintext", () => {
    expect(getLang("file.xyz")).toBe("plaintext");
  });

  test("handles uppercase extension", () => {
    expect(getLang("README.MD")).toBe("markdown");
  });
});

describe("getFileIcon", () => {
  test("python files get snake emoji", () => {
    expect(getFileIcon("main.py")).toBe("🐍");
  });

  test("javascript files get scroll emoji", () => {
    expect(getFileIcon("app.js")).toBe("📜");
  });

  test("unknown files get document emoji", () => {
    expect(getFileIcon("data.xyz")).toBe("📄");
  });
});

describe("getFileExtension", () => {
  test("returns extension from filename", () => {
    expect(getFileExtension("main.py")).toBe("py");
  });

  test("returns extension from path", () => {
    expect(getFileExtension("src/utils/helpers.js")).toBe("js");
  });

  test("returns empty string for no extension", () => {
    expect(getFileExtension("Makefile")).toBe("");
  });
});

describe("extToLang", () => {
  test("maps py to python", () => {
    expect(extToLang("py")).toBe("python");
  });

  test("maps ts to typescript", () => {
    expect(extToLang("ts")).toBe("typescript");
  });

  test("unknown ext returns the ext itself", () => {
    expect(extToLang("xyz")).toBe("xyz");
  });

  test("empty ext returns plaintext", () => {
    expect(extToLang("")).toBe("plaintext");
  });
});

describe("EXT_LANG map", () => {
  test("contains expected entries", () => {
    expect(EXT_LANG.js).toBe("javascript");
    expect(EXT_LANG.py).toBe("python");
    expect(EXT_LANG.html).toBe("html");
    expect(EXT_LANG.css).toBe("css");
  });
});
