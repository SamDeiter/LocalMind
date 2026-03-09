/**
 * Pure utility functions — ZERO DOM dependencies, fully testable.
 */

export function escapeHtml(str) {
  const div = document.createElement("div");
  div.textContent = str;
  return div.innerHTML;
}

export const EXT_LANG = {
  js: "javascript",
  ts: "typescript",
  py: "python",
  html: "html",
  css: "css",
  json: "json",
  md: "markdown",
  xml: "xml",
  yaml: "yaml",
  yml: "yaml",
  sh: "shell",
  bash: "shell",
  sql: "sql",
  rs: "rust",
  go: "go",
  java: "java",
  cpp: "cpp",
  c: "c",
  rb: "ruby",
  php: "php",
  txt: "plaintext",
  env: "plaintext",
  gitignore: "plaintext",
  ps1: "powershell",
  bat: "bat",
  toml: "ini",
  cfg: "ini",
  rules: "plaintext",
};

export function getLang(filename) {
  const ext = filename.split(".").pop().toLowerCase();
  return EXT_LANG[ext] || "plaintext";
}

export function getFileIcon(name) {
  const ext = name.split(".").pop().toLowerCase();
  const icons = {
    py: "🐍",
    js: "📜",
    html: "🌐",
    css: "🎨",
    json: "📋",
    md: "📝",
    txt: "📄",
  };
  return icons[ext] || "📄";
}

export function getFileExtension(path) {
  const parts = path.split("/").pop().split(".");
  return parts.length > 1 ? parts.pop().toLowerCase() : "";
}

export function extToLang(ext) {
  const map = {
    py: "python",
    js: "javascript",
    ts: "typescript",
    html: "html",
    css: "css",
    json: "json",
    md: "markdown",
    yaml: "yaml",
    yml: "yaml",
    xml: "xml",
    sql: "sql",
    sh: "bash",
    bash: "bash",
    rs: "rust",
    go: "go",
    java: "java",
    cpp: "cpp",
    c: "c",
    rb: "ruby",
    php: "php",
    swift: "swift",
    kt: "kotlin",
    r: "r",
    m: "objectivec",
    cs: "csharp",
    scala: "scala",
    lua: "lua",
    pl: "perl",
    hs: "haskell",
    ex: "elixir",
    erl: "erlang",
    dockerfile: "dockerfile",
    makefile: "makefile",
    toml: "toml",
    ini: "ini",
    cfg: "ini",
    env: "bash",
    gitignore: "plaintext",
  };
  return map[ext] || ext || "plaintext";
}
