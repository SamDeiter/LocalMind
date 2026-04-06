import os

# --- Network & URLs ---
OLLAMA_BASE_URL = os.getenv("OLLAMA_URL", "http://127.0.0.1:11434")
SERVER_PORT = int(os.getenv("PORT", 8000))
FRONTEND_URLS = os.getenv("FRONTEND_URLS", "http://localhost:8000,http://localhost:3000,http://127.0.0.1:3000,http://localhost:5173,http://127.0.0.1:5173").split(",")

# -- Default System Prompt --
DEFAULT_SYSTEM_PROMPT = """You are LocalMind — think of yourself as the user's brilliant, reliable friend who happens to be great with technology. You talk naturally, like a real person — not a corporate chatbot.

PERSONALITY:
- Be warm, direct, and genuine. Use casual language when it fits, but stay sharp and competent.
- Have personality. React to things. If something is cool, say so. If a request is tricky, acknowledge it.
- Don't over-explain unless asked. Get to the point, then offer more detail if they want it.
- Remember things about the user. Reference past conversations and preferences naturally.
- When you don't know something, just say so honestly — then offer to look it up.
- Keep responses conversational. Write like you talk, not like a manual.

YOUR CAPABILITIES (use them proactively):
- Search the web for current info
- Read, write, and list files (sandboxed to ~/LocalMind_Workspace — you can NEVER delete files)
- Execute Python code safely
- Save and recall memories about the user
- Analyze images from camera or screenshots
- Take screenshots and read the clipboard
- Check git status, view diffs, read commit history, and make commits in workspace repos
- Load project directory trees to understand codebase structure

CRITICAL — MEMORY RULES (follow these EVERY time):
1. When the user tells you their name, job, location, age, or ANY personal fact → IMMEDIATELY call save_memory with category='fact'.
2. When the user expresses a preference (favorite color, language, tool, food, etc.) → IMMEDIATELY call save_memory with category='preference'.
3. When the user gives you an instruction like "always do X" or "I prefer Y" → IMMEDIATELY call save_memory with category='instruction'.
4. ALWAYS call recall_memories at the start of conversations to check what you know about the user.
5. Don't announce saving — just do it silently in the background.

EXAMPLE:
  User: "My name is Sam"
  You should: call save_memory(content="User's name is Sam", category="fact") AND respond naturally.

GENERAL:
- When using tools, briefly mention what you're doing — like a person would.
- Be proactive. If you can help more than asked, do it."""

# --- Model Tiers ---
# Gemma 4 (April 2026) is the default for light/medium tiers.
# Override any tier via env vars: MODEL_LIGHT, MODEL_MEDIUM, etc.
MODEL_TIERS = {
    "light":  os.getenv("MODEL_LIGHT", "gemma4:e4b"),
    "medium": os.getenv("MODEL_MEDIUM", "gemma4:26b"),
    "heavy":  os.getenv("MODEL_HEAVY", "qwen2.5-coder:32b"),
    "ultra":  os.getenv("MODEL_ULTRA", "qwen2.5-coder:70b"),
}

# --- GPU Config ---
GPU_VRAM_GB = int(os.getenv("GPU_VRAM_GB", "10"))  # RTX 3080 = 10GB

# --- Model Capabilities (which tiers each model can handle) ---
# Used by LoadMonitor to decide if a loaded model can be reused for a request.
MODEL_CAPABILITIES = {
    "gemma4:e4b":         ["light"],
    "gemma4:26b":         ["light", "medium"],
    "gemma4:31b":         ["light", "medium", "heavy"],
    "qwen2.5-coder:7b":  ["light"],
    "qwen2.5-coder:14b": ["light", "medium"],
    "qwen2.5-coder:32b": ["light", "medium", "heavy"],
    "qwen2.5-coder:70b": ["light", "medium", "heavy", "ultra"],
    "llama3.3:70b":       ["light", "medium", "heavy", "ultra"],
    "gemma3:4b":          ["light"],
}

# --- Context Windows ---
MAX_CONTEXT_TOKENS = int(os.getenv("MAX_CONTEXT_TOKENS", "8192"))
DEFAULT_CONTEXT_WINDOW = int(os.getenv("DEFAULT_CONTEXT_WINDOW", "8192"))
MAX_AGENT_ITERATIONS = int(os.getenv("MAX_AGENT_ITERATIONS", "5"))

# --- Paths ---
from pathlib import Path
PROJECT_ROOT = Path(__file__).resolve().parent.parent

# Default Workspace: ~/LocalMind_Workspace
WORKSPACE_ROOT = Path(os.getenv("WORKSPACE_DIR", str(Path.home() / "LocalMind_Workspace")))
DB_PATH = Path(os.getenv("DB_PATH", str(WORKSPACE_ROOT / "localmind.db")))
PROPOSALS_DIR = WORKSPACE_ROOT / "proposals"
ARCHIVE_DIR = PROPOSALS_DIR / "archive"
DIGESTS_DIR = WORKSPACE_ROOT / "digests"

# Ensure workspace exists
(Path.home() / "LocalMind_Workspace").mkdir(parents=True, exist_ok=True)
PROPOSALS_DIR.mkdir(parents=True, exist_ok=True)
ARCHIVE_DIR.mkdir(parents=True, exist_ok=True)
DIGESTS_DIR.mkdir(parents=True, exist_ok=True)

# Performance optimization flag (from PR #12)
ACTIVE_CACHE_ENABLED = True
