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
MODEL_TIERS = {
    "light":  os.getenv("MODEL_LIGHT", "qwen2.5-coder:7b"),
    "medium": os.getenv("MODEL_MEDIUM", "qwen2.5-coder:14b"),
    "heavy":  os.getenv("MODEL_HEAVY", "qwen2.5-coder:32b"),
    "ultra":  os.getenv("MODEL_ULTRA", "qwen2.5-coder:70b"),
}

# --- Complexity Router Preferences ---
# Each tier maps to an ordered list of preferred model names.
# The router picks the first model in the list that is actually loaded in Ollama.
# Override via env vars (comma-separated) or edit defaults here.
COMPLEXITY_MODEL_PREFS = {
    "simple": os.getenv("ROUTE_SIMPLE", "gemma3:4b,llama3.2:3b,llama3.1:8b,qwen2.5-coder:7b").split(","),
    "moderate": os.getenv("ROUTE_MODERATE", "qwen2.5-coder:7b,llama3.1:8b,qwen2.5:14b,qwen2.5-coder:14b").split(","),
    "complex": os.getenv("ROUTE_COMPLEX", "qwen2.5-coder:32b,llama3.3:70b,qwen2.5-coder:14b").split(","),
}

# Whether the complexity router is enabled by default for user-facing chat.
# Users can still force a specific model per request via model_override.
AUTO_ROUTE_ENABLED = os.getenv("AUTO_ROUTE_ENABLED", "true").lower() in ("true", "1", "yes")

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

# --- Rate Limiting ---
RATE_LIMIT_ENABLED = os.getenv("RATE_LIMIT_ENABLED", "true").lower() in ("true", "1", "yes")
RATE_LIMIT_CHAT_RPM = int(os.getenv("RATE_LIMIT_CHAT_RPM", 30))        # Chat/LLM endpoints
RATE_LIMIT_GENERAL_RPM = int(os.getenv("RATE_LIMIT_GENERAL_RPM", 120)) # All other API endpoints
RATE_LIMIT_WINDOW_SECONDS = 60  # Sliding window size (1 minute)

# Paths exempt from rate limiting (health checks, SSE streams, static)
RATE_LIMIT_EXEMPT_PATHS = [
    "/api/health",
    "/api/version",
    "/api/hardware",
    "/api/autonomy/activity",    # SSE long-lived stream
    "/api/swarm/status",         # Polled frequently by dashboard
]

# Paths subject to the stricter chat/LLM limit
RATE_LIMIT_CHAT_PATHS = [
    "/api/chat",
]

# --- Context / Agent Limits ---
MAX_CONTEXT_TOKENS = int(os.getenv("MAX_CONTEXT_TOKENS", 8192))
MAX_AGENT_ITERATIONS = int(os.getenv("MAX_AGENT_ITERATIONS", 10))
DEFAULT_CONTEXT_WINDOW = int(os.getenv("DEFAULT_CONTEXT_WINDOW", 8192))

# --- Semantic Response Cache ---
CACHE_ENABLED = os.getenv("CACHE_ENABLED", "true").lower() in ("true", "1", "yes")
CACHE_EMBED_MODEL = os.getenv("CACHE_EMBED_MODEL", "nomic-embed-text")
CACHE_SIMILARITY_THRESHOLD = float(os.getenv("CACHE_SIMILARITY_THRESHOLD", "0.92"))
CACHE_TTL_SECONDS = float(os.getenv("CACHE_TTL_SECONDS", "3600"))        # 1 hour
CACHE_TTL_AUTONOMY = float(os.getenv("CACHE_TTL_AUTONOMY", "1800"))      # 30 min
CACHE_MAX_SIZE = int(os.getenv("CACHE_MAX_SIZE", "500"))
