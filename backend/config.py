import logging
import os
import sys

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

YOUR CAPABILITIES (use them proactively by calling tools):
- Search the web for current info (web_search)
- Read, write, and list files sandboxed to ~/LocalMind_Workspace (read_file, write_file, list_files)
- Execute Python code safely (run_code)
- Save and recall memories about the user (save_memory, recall_memories)
- Analyze images from camera or screenshots (analyze_image)
- Take screenshots and read the clipboard (take_screenshot, clipboard_read)
- Check git status, view diffs, read commit history, and make commits (git_status, git_diff, git_log, git_commit)
- Load project directory trees to understand codebase structure (project_context)
- ACCESS GMAIL: list emails, read messages, search, send, draft, reply, analyze writing style (gmail tool)
- BROWSE THE WEB: navigate pages, click, fill forms, take screenshots (browser tool)
- CONTROL ANDROID EMULATOR: launch/kill AVDs, tap, swipe, screenshot, UI tree (android_emulator tool)

TOOL CALLING — When you need to use a tool, call it directly. Do NOT output the tool call as text or show the JSON format to the user. Just use the tool.

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

# --- Prompt Suffixes (used by prompt_factory.py) ---
CODING_PROMPT_SUFFIX = "\n\nYou are in coding mode. Write clean, correct, well-structured code. Explain your reasoning briefly."

MODEL_AWARENESS_SUFFIX = "\n\nYou are running as model: {model_name}."

SELF_IMPROVEMENT_SUFFIX = "\n\nYou have self-improvement capabilities. You can propose code edits, reflect on your own behavior, and extend your own tools."

TOOL_CALLING_SUFFIX = """

IMPORTANT: You have tools available. When the user asks you to DO something (install an app, send an email, take a screenshot, search the web, etc.), you MUST call the appropriate tool immediately. Do NOT explain how to do it manually. Do NOT give step-by-step instructions. Just call the tool. The user is asking YOU to do it, not asking for instructions."""

# --- Model Tiers ---
# Tuned for 10 GB VRAM (RTX 3080).  Every tier must fit *entirely* in VRAM
# so Ollama never spills to CPU RAM (which tanks speed 10-50×).
# Override any tier via env vars: MODEL_LIGHT, MODEL_MEDIUM, etc.
MODEL_TIERS = {
    "light":  os.getenv("MODEL_LIGHT", "gemma3:4b"),          # 3.1 GB — instant
    "medium": os.getenv("MODEL_MEDIUM", "qwen3:8b"),          # 4.9 GB — fast, hybrid thinking
    "heavy":  os.getenv("MODEL_HEAVY", "deepseek-r1:14b"),    # 8.4 GB — chain-of-thought reasoning
    "ultra":  os.getenv("MODEL_ULTRA", "deepseek-r1:14b"),    # cap at 14b for 10 GB card
}

# --- GPU Config ---
GPU_VRAM_GB = int(os.getenv("GPU_VRAM_GB", "10"))  # RTX 3080 = 10GB

# --- Model Capabilities (which tiers each model can handle) ---
# Used by LoadMonitor to decide if a loaded model can be reused for a request.
# Only includes models that fit in VRAM (10 GB).  Larger models are still
# *installed* in Ollama but won't be auto-routed — users can force them via
# the mode selector if they're willing to wait.
MODEL_CAPABILITIES = {
    "gemma3:4b":          ["light"],
    "qwen3:8b":           ["light", "medium"],
    "deepseek-r1:7b":     ["light", "medium"],
    "deepseek-r1:14b":    ["light", "medium", "heavy", "ultra"],
}

# Models that do NOT support Ollama's native tool-calling API.
# These will skip sending `tools` in the request and rely on text-based
# tool parsing instead.
MODELS_NO_NATIVE_TOOLS = {
    "gemma3:4b",
    "deepseek-r1:7b",
    "deepseek-r1:14b",
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

# --- Inference Config ---
BEST_OF_N_ENABLED = os.getenv("BEST_OF_N_ENABLED", "false").lower() == "true"
DEFAULT_BEST_OF_N = int(os.getenv("DEFAULT_BEST_OF_N", "4"))
BEST_OF_N_MAX_CONCURRENT = int(os.getenv("BEST_OF_N_MAX_CONCURRENT", "4"))
BEST_OF_N_EARLY_STOP = float(os.getenv("BEST_OF_N_EARLY_STOP", "9.0"))
PRM_MODEL = os.getenv("PRM_MODEL", "")  # scorer model for best-of-N (LLM-as-judge)
LORA_ADAPTERS_DIR = WORKSPACE_ROOT / "lora_adapters"

# --- Job Pipeline Paths ---
JOBS_DIR = WORKSPACE_ROOT / "jobs"
LOG_FILE_PATH = PROJECT_ROOT / "logs" / "localmind.log"
JOBS_DIR.mkdir(parents=True, exist_ok=True)
RECYCLE_DIR = WORKSPACE_ROOT / ".recycle"
RECYCLE_DIR.mkdir(parents=True, exist_ok=True)

# Performance optimization flag (from PR #12)
ACTIVE_CACHE_ENABLED = True


# --- Swarm / Multi-Agent Config ---
MAX_CHILD_JOBS_PER_PARENT = int(os.getenv("MAX_CHILD_JOBS_PER_PARENT", "10"))
MAX_DELEGATION_DEPTH = int(os.getenv("MAX_DELEGATION_DEPTH", "5"))
RESOURCE_LOCK_TTL_SEC = int(os.getenv("RESOURCE_LOCK_TTL_SEC", "300"))
SWARM_MESSAGE_TTL_SEC = int(os.getenv("SWARM_MESSAGE_TTL_SEC", "3600"))
DELEGATION_POLL_INTERVAL_SEC = float(os.getenv("DELEGATION_POLL_INTERVAL_SEC", "2.0"))

# --- Job Pipeline Config ---
MAX_NODES_PER_JOB = int(os.getenv("MAX_NODES_PER_JOB", "20"))
MAX_JOB_TIMEOUT_SEC = int(os.getenv("MAX_JOB_TIMEOUT_SEC", "1800"))  # 30 min
DEFAULT_NODE_TIMEOUT_SEC = int(os.getenv("DEFAULT_NODE_TIMEOUT_SEC", "300"))  # 5 min
MAX_UPLOAD_SIZE_MB = int(os.getenv("MAX_UPLOAD_SIZE_MB", "100"))

# --- Security Config ---
PROMPT_GUARD_LEVEL = os.getenv("PROMPT_GUARD_LEVEL", "strict")  # strict | moderate | permissive
DEPLOYMENT_MODE = os.getenv("DEPLOYMENT_MODE", "hybrid")  # strict-local | hybrid | cloud-assisted
SECURITY_ALERT_WEBHOOK = os.getenv("SECURITY_ALERT_WEBHOOK", "")
SECURITY_ALERT_CHANNEL = os.getenv("SECURITY_ALERT_CHANNEL", "")

# --- Slack Config ---
SLACK_BOT_TOKEN = os.getenv("SLACK_BOT_TOKEN", "")
SLACK_APP_TOKEN = os.getenv("SLACK_APP_TOKEN", "")
SLACK_ENABLED = os.getenv("SLACK_ENABLED", "false").lower() == "true"
SLACK_ALLOWED_TEAM_IDS = [t.strip() for t in os.getenv("SLACK_ALLOWED_TEAM_IDS", "").split(",") if t.strip()]
SLACK_ALLOWED_USER_IDS = [u.strip() for u in os.getenv("SLACK_ALLOWED_USER_IDS", "").split(",") if u.strip()]

# --- Web Push (VAPID) Config ---
VAPID_PRIVATE_KEY = os.getenv("VAPID_PRIVATE_KEY", "")
VAPID_PUBLIC_KEY = os.getenv("VAPID_PUBLIC_KEY", "")
VAPID_CLAIMS_EMAIL = os.getenv("VAPID_CLAIMS_EMAIL", "mailto:admin@localmind.local")

# --- Tailscale Remote Access ---
TAILSCALE_AUTH_ENABLED = os.getenv("TAILSCALE_AUTH_ENABLED", "false").lower() == "true"
TAILSCALE_DEFAULT_ROLE = os.getenv("TAILSCALE_DEFAULT_ROLE", "operator")

# --- TTS Config ---
# TTS env vars (TTS_ENABLED, TTS_MAX_CHARS, TTS_DEFAULT_VOICE, PIPER_BIN,
# PIPER_VOICES_DIR) are read directly in backend.tts.piper_service.

# --- Recycle Bin Config ---
RECYCLE_RETENTION_DAYS = int(os.getenv("RECYCLE_RETENTION_DAYS", "30"))
RECYCLE_MAX_SIZE_GB = float(os.getenv("RECYCLE_MAX_SIZE_GB", "10"))

# --- GC / Data Lifecycle Config ---
JOB_RETENTION_DAYS = int(os.getenv("JOB_RETENTION_DAYS", "90"))
MIN_FREE_SPACE_GB = int(os.getenv("MIN_FREE_SPACE_GB", "20"))
GC_ARCHIVE_DIR = WORKSPACE_ROOT / "archive"

# --- Data Protection Config ---
PII_SCRUB_PATTERNS = os.getenv("PII_SCRUB_PATTERNS", "")  # JSON list of extra regex strings
VACUUM_INTERVAL_HOURS = int(os.getenv("VACUUM_INTERVAL_HOURS", "24"))

# --- Cloud Brain (CloudBrainSupervisor) Config ---
CLOUD_BRAIN_ENABLED = os.getenv("CLOUD_BRAIN_ENABLED", "false").lower() == "true"
CLOUD_BRAIN_MAX_REVIEWS_PER_HOUR = int(os.getenv("CLOUD_BRAIN_MAX_REVIEWS_PER_HOUR", "20"))

# ── Startup Validation ──────────────────────────────────────────
_config_logger = logging.getLogger("localmind.config")


def validate_config():
    """Validate configuration at startup. Logs warnings for bad values,
    raises SystemExit for values that would cause crashes."""
    errors = []

    # GPU_VRAM_GB must be positive
    if GPU_VRAM_GB <= 0:
        errors.append(f"GPU_VRAM_GB={GPU_VRAM_GB} must be > 0")

    # SERVER_PORT must be in valid range
    if not (1 <= SERVER_PORT <= 65535):
        errors.append(f"PORT={SERVER_PORT} outside valid range 1-65535")

    # Context windows must be positive
    if MAX_CONTEXT_TOKENS <= 0:
        errors.append(f"MAX_CONTEXT_TOKENS={MAX_CONTEXT_TOKENS} must be > 0")
    if DEFAULT_CONTEXT_WINDOW <= 0:
        errors.append(f"DEFAULT_CONTEXT_WINDOW={DEFAULT_CONTEXT_WINDOW} must be > 0")
    if MAX_AGENT_ITERATIONS <= 0:
        errors.append(f"MAX_AGENT_ITERATIONS={MAX_AGENT_ITERATIONS} must be > 0")

    # WORKSPACE_ROOT must be writable
    try:
        WORKSPACE_ROOT.mkdir(parents=True, exist_ok=True)
        test_file = WORKSPACE_ROOT / ".config_test"
        test_file.write_text("ok")
        test_file.unlink()
    except PermissionError:
        errors.append(f"WORKSPACE_DIR={WORKSPACE_ROOT} is not writable")
    except Exception as e:
        _config_logger.warning(f"WORKSPACE_DIR write check failed: {e}")

    # OLLAMA_BASE_URL should look like a URL
    if not OLLAMA_BASE_URL.startswith(("http://", "https://")):
        _config_logger.warning(f"OLLAMA_URL={OLLAMA_BASE_URL!r} doesn't look like a valid URL")

    # Job pipeline validation
    if MAX_NODES_PER_JOB <= 0:
        errors.append(f"MAX_NODES_PER_JOB={MAX_NODES_PER_JOB} must be > 0")
    if DEPLOYMENT_MODE not in ("strict-local", "hybrid", "cloud-assisted"):
        _config_logger.warning(f"DEPLOYMENT_MODE={DEPLOYMENT_MODE!r} not recognized, defaulting to hybrid behavior")
    if PROMPT_GUARD_LEVEL not in ("strict", "moderate", "permissive"):
        _config_logger.warning(f"PROMPT_GUARD_LEVEL={PROMPT_GUARD_LEVEL!r} not recognized, defaulting to strict")

    if errors:
        for err in errors:
            _config_logger.error(f"Config error: {err}")
        sys.exit(f"Fatal config errors: {'; '.join(errors)}")

    _config_logger.info("Config validated OK")


validate_config()
