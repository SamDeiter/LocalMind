/**
 * errors.js — Centralized Error Boundary
 * Global error handling, deduplication, toast notifications, and error log buffer.
 * This module should be initialized FIRST, before other modules.
 */

// ── Error Log Buffer ───────────────────────────────────────────
const MAX_LOG_SIZE = 50;
const _errorLog = [];

// ── Deduplication ──────────────────────────────────────────────
const DEDUP_WINDOW_MS = 5000;
const _recentKeys = new Map(); // key -> timestamp

// ── Toast Container ────────────────────────────────────────────
let _toastContainer = null;

/**
 * Inject scoped CSS for error toasts.
 * Uses a dedicated container so it does not conflict with the existing
 * showToast() in utils.js (which uses #toast-container).
 */
function _injectStyles() {
  const style = document.createElement("style");
  style.textContent = `
    #error-toast-container {
      position: fixed;
      bottom: 2rem;
      left: 2rem;
      display: flex;
      flex-direction: column;
      gap: 0.75rem;
      z-index: 10000;
      pointer-events: none;
      max-width: 420px;
    }
    .error-toast {
      background: rgba(15, 23, 42, 0.96);
      border: 1px solid rgba(51, 65, 85, 0.6);
      border-left: 3px solid #ef4444;
      border-radius: 0.5rem;
      padding: 0.75rem 1rem;
      color: #f1f5f9;
      font-family: Inter, sans-serif;
      font-size: 0.825rem;
      line-height: 1.4;
      display: flex;
      align-items: flex-start;
      gap: 0.5rem;
      pointer-events: auto;
      transform: translateX(-120%);
      opacity: 0;
      transition: transform 0.3s ease, opacity 0.3s ease;
      box-shadow: 0 4px 24px rgba(0,0,0,0.4);
    }
    .error-toast.visible {
      transform: translateX(0);
      opacity: 1;
    }
    .error-toast.warning {
      border-left-color: #f59e0b;
    }
    .error-toast-icon {
      flex-shrink: 0;
      font-size: 1rem;
      margin-top: 1px;
    }
    .error-toast-body {
      flex: 1;
      min-width: 0;
    }
    .error-toast-ctx {
      display: block;
      color: #94a3b8;
      font-size: 0.7rem;
      font-family: "JetBrains Mono", monospace;
      margin-top: 2px;
      white-space: nowrap;
      overflow: hidden;
      text-overflow: ellipsis;
    }
    .error-toast-msg {
      word-break: break-word;
    }
    .error-toast-close {
      background: none;
      border: none;
      color: #64748b;
      cursor: pointer;
      font-size: 1rem;
      line-height: 1;
      padding: 0;
      flex-shrink: 0;
      transition: color 0.15s;
    }
    .error-toast-close:hover {
      color: #f1f5f9;
    }
  `;
  document.head.appendChild(style);
}

function _getContainer() {
  if (!_toastContainer) {
    _toastContainer = document.createElement("div");
    _toastContainer.id = "error-toast-container";
    document.body.appendChild(_toastContainer);
  }
  return _toastContainer;
}

// ── Dedup helpers ──────────────────────────────────────────────
function _dedupKey(message, context) {
  return `${context || ""}::${message || ""}`;
}

function _isDuplicate(key) {
  const prev = _recentKeys.get(key);
  if (prev && Date.now() - prev < DEDUP_WINDOW_MS) {
    return true;
  }
  _recentKeys.set(key, Date.now());
  return false;
}

// Periodically clean up old dedup entries
setInterval(() => {
  const now = Date.now();
  for (const [key, ts] of _recentKeys) {
    if (now - ts > DEDUP_WINDOW_MS) _recentKeys.delete(key);
  }
}, 10000);

// ── Toast display ──────────────────────────────────────────────
function _showErrorToast(message, context, level = "error") {
  const container = _getContainer();

  const toast = document.createElement("div");
  toast.className = `error-toast${level === "warning" ? " warning" : ""}`;

  const icon = level === "warning" ? "warning" : "error_outline";
  toast.innerHTML = `
    <span class="error-toast-icon material-symbols-outlined">${icon}</span>
    <div class="error-toast-body">
      <span class="error-toast-msg">${_escapeHtml(message)}</span>
      ${context ? `<span class="error-toast-ctx">${_escapeHtml(context)}</span>` : ""}
    </div>
    <button class="error-toast-close" title="Dismiss">&times;</button>
  `;

  container.appendChild(toast);

  // Animate in
  requestAnimationFrame(() => {
    toast.classList.add("visible");
  });

  const dismiss = () => {
    toast.classList.remove("visible");
    setTimeout(() => toast.remove(), 300);
  };

  toast.querySelector(".error-toast-close").addEventListener("click", dismiss);

  // Auto-dismiss after 5 seconds
  setTimeout(dismiss, 5000);
}

function _escapeHtml(str) {
  const div = document.createElement("div");
  div.textContent = str;
  return div.innerHTML;
}

// ── Public API ─────────────────────────────────────────────────

/**
 * Report an error with optional context string.
 * Deduplicates rapid-fire identical errors and logs to the buffer.
 *
 * @param {Error|string} error  - The error object or message string
 * @param {string} [context]    - Where the error occurred (e.g. "chat.sendMessage")
 * @param {object} [opts]       - Options: { silent: bool, level: "error"|"warning" }
 */
export function reportError(error, context = "", opts = {}) {
  const message = error instanceof Error ? error.message : String(error);
  const stack = error instanceof Error ? error.stack : undefined;
  const level = opts.level || "error";

  // Build log entry
  const entry = {
    message,
    context,
    stack,
    level,
    timestamp: new Date().toISOString(),
  };

  // Push to buffer (ring buffer, cap at MAX_LOG_SIZE)
  _errorLog.push(entry);
  if (_errorLog.length > MAX_LOG_SIZE) {
    _errorLog.shift();
  }

  // Console output
  const prefix = context ? `[${context}]` : "[error]";
  if (level === "warning") {
    console.warn(prefix, message, stack || "");
  } else {
    console.error(prefix, message, stack || "");
  }

  // Dedup check — skip toast if same error was just shown
  const key = _dedupKey(message, context);
  if (!opts.silent && !_isDuplicate(key)) {
    _showErrorToast(message, context, level);
  }
}

/**
 * Get the error log buffer (most recent 50 entries).
 * Useful for debugging: `window.__errorLog()` in the console.
 *
 * @returns {Array<{message, context, stack, level, timestamp}>}
 */
export function getErrorLog() {
  return [..._errorLog];
}

/**
 * Clear the error log buffer.
 */
export function clearErrorLog() {
  _errorLog.length = 0;
}

// ── Global handlers ────────────────────────────────────────────

function _handleGlobalError(event) {
  // event: ErrorEvent
  const message = event.message || "Unknown error";
  const source = event.filename
    ? `${event.filename}:${event.lineno}:${event.colno}`
    : "unknown source";
  reportError(message, source);
}

function _handleUnhandledRejection(event) {
  const reason = event.reason;
  let message;
  if (reason instanceof Error) {
    message = reason.message;
  } else if (typeof reason === "string") {
    message = reason;
  } else {
    message = "Unhandled promise rejection";
  }
  reportError(reason instanceof Error ? reason : message, "unhandledrejection");
}

// ── Init ───────────────────────────────────────────────────────

/**
 * Initialize the error boundary. Call this once, early in boot.
 * Installs global handlers and injects styles.
 */
export function initErrorBoundary() {
  _injectStyles();

  window.addEventListener("error", _handleGlobalError);
  window.addEventListener("unhandledrejection", _handleUnhandledRejection);

  // Expose helpers on window for console debugging
  window.__errorLog = getErrorLog;
  window.__clearErrorLog = clearErrorLog;
  window.__reportError = reportError;

  console.log("[errors] Error boundary active");
}
