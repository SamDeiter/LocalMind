import { API } from "../state.js";
import { showToast } from "../utils.js";

export async function loadDigest() {
  const body = document.getElementById("brainDigestBody");
  if (!body) return;
  
  try {
    const r = await fetch(`${API}/api/autonomy/digest`);
    const d = await r.json();
    if (d.digest) {
      body.innerHTML = renderMarkdown(d.digest);
    } else {
      body.innerHTML = '<div class="text-outline/40 text-xs italic">Awaiting end-of-day synthesis...</div>';
    }
  } catch {
    body.innerHTML = '<div class="text-error/60 text-xs italic">Digest currently unavailable.</div>';
  }
}

export async function exportDigest() {
  try {
    const r = await fetch(`${API}/api/autonomy/digest`);
    const d = await r.json();
    if (!d.digest) {
      showToast("⚠️ No digest available to export", "warning");
      return;
    }
    const blob = new Blob([d.digest], { type: "text/markdown" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = `localmind-digest-${new Date().toISOString().slice(0, 10)}.md`;
    a.click();
    showToast("📑 Digest Exported", "info");
  } catch {
    showToast("❌ Export failed", "error");
  }
}

function renderMarkdown(text) {
  // Simple markdown renderer for digest (titles, lists, bold)
  return text
    .replace(/^# (.*$)/gim, '<h1 class="text-primary font-bold text-lg mb-4">$1</h1>')
    .replace(/^## (.*$)/gim, '<h2 class="text-primary/80 font-bold text-md mt-6 mb-2">$1</h2>')
    .replace(/^\* (.*$)/gim, '<li class="ml-4 text-outline/80">$1</li>')
    .replace(/\*\*(.*)\*\*/gim, '<b class="text-on-surface">$1</b>')
    .replace(/\n/g, '<br>');
}
