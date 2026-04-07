/**
 * Tool-call card rendering — creating and updating tool result cards in the chat.
 */

import { escapeHtml, getLang, getFileExtension, extToLang } from "./utils.js";

// ── Syntax Highlighting Helper ─────────────────────────────────
export function highlightCode() {
  try {
    document.querySelectorAll("pre code:not(.hljs)").forEach((block) => {
      hljs.highlightElement(block);
    });
  } catch {
    /* hljs not loaded */
  }
}

// ── Tool Call Card ─────────────────────────────────────────────
export function createToolCallCard(tc) {
  const card = document.createElement("div");
  card.className = "tool-call-card";
  card.dataset.toolCallId = tc.id || tc.tool_call_id || "";

  const iconMap = {
    web_search: "🔍",
    run_code: "💻",
    read_file: "📖",
    write_file: "✏️",
    list_files: "📂",
    android_emulator: "📱",
    gmail: "📧",
    browser: "🌐",
    take_screenshot: "📸",
    analyze_image: "👁️",
    save_memory: "🧠",
    recall_memories: "🧠",
  };
  const icon = iconMap[tc.name] || "🔧";

  card.innerHTML = `
    <div class="tool-call-header">
      <span class="tool-icon">${icon}</span>
      <span class="tool-name">${escapeHtml(tc.name)}</span>
      <span class="tool-status">⏳ Running...</span>
    </div>
    <div class="tool-call-args">
      <pre>${escapeHtml(JSON.stringify(tc.arguments || tc.args || {}, null, 2))}</pre>
    </div>
  `;
  return card;
}

// ── Tool Result Update ─────────────────────────────────────────
export function updateToolResult(container, result) {
  if (!container) return;
  const id = result.tool_call_id || result.id;
  const card = container.querySelector(`.tool-call-card[data-tool-call-id="${id}"]`);

  if (card) {
    const status = card.querySelector(".tool-status");
    const success = result.success !== false;
    if (status) {
      status.textContent = success ? "✅ Done" : "❌ Failed";
      status.className = `tool-status ${success ? "success" : "error"}`;
    }

    // Show result
    const resultDiv = document.createElement("div");
    resultDiv.className = "tool-result";

    // Handle image results (emulator screenshots, etc.)
    if (result.image_base64) {
      const mime = result.mime_type || "image/png";
      const isEmulator = result.name === "android_emulator";
      const img = document.createElement("img");
      img.src = `data:${mime};base64,${result.image_base64}`;
      img.className = "tool-result-image";
      img.alt = isEmulator ? "Emulator Screen" : "Screenshot";
      img.addEventListener("click", () => {
        const w = window.open();
        w.document.write(`<img src="${img.src}" style="max-width:100%;background:#111">`);
        w.document.title = img.alt;
      });

      if (isEmulator) {
        // Wrap in a phone frame for emulator screenshots
        const frame = document.createElement("div");
        frame.className = "phone-frame";
        const notch = document.createElement("div");
        notch.className = "phone-frame-notch";
        frame.appendChild(notch);
        frame.appendChild(img);
        resultDiv.appendChild(frame);
      } else {
        resultDiv.appendChild(img);
      }

      if (result.result) {
        const caption = document.createElement("div");
        caption.className = "tool-output";
        caption.style.cssText = "font-size: 0.85em; opacity: 0.7; margin-top: 4px;";
        caption.textContent = typeof result.result === "object" ? JSON.stringify(result.result) : String(result.result);
        resultDiv.appendChild(caption);
      }
      card.appendChild(resultDiv);
      highlightCode();
      return;
    }

    const output = result.result || result.output || result.error || "";
    const outputStr = typeof output === "object" ? JSON.stringify(output, null, 2) : String(output);

    // Check if output looks like file content
    if (result.name === "read_file" && result.path) {
      const ext = getFileExtension(result.path);
      const lang = extToLang(ext);
      resultDiv.innerHTML = `
        <div class="code-viewer">
          <div class="code-viewer-header">
            <span class="code-viewer-filename">${escapeHtml(result.path)}</span>
            <button class="code-copy-btn" onclick="navigator.clipboard.writeText(this.closest('.code-viewer').querySelector('code').textContent)">Copy</button>
          </div>
          <pre><code class="language-${lang}">${escapeHtml(outputStr)}</code></pre>
        </div>`;
    } else if (outputStr.length > 200) {
      resultDiv.innerHTML = `
        <div class="code-viewer">
          <div class="code-viewer-header">
            <span class="code-viewer-filename">Output</span>
            <button class="code-copy-btn" onclick="navigator.clipboard.writeText(this.closest('.code-viewer').querySelector('code').textContent)">Copy</button>
          </div>
          <pre><code>${escapeHtml(outputStr)}</code></pre>
        </div>`;
    } else {
      resultDiv.innerHTML = `<pre class="tool-output">${escapeHtml(outputStr)}</pre>`;
    }

    card.appendChild(resultDiv);
    highlightCode();
  }
}
