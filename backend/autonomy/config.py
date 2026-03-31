
from pathlib import Path

LOG_FILE = Path.home() / "LocalMind_Workspace" / "autonomy_log.jsonl"
PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent

# Engine Constants
CHAT_COOLDOWN = 30
MAX_ACTIVE_PROPOSALS = 10
CIRCUIT_BREAKER_THRESHOLD = 3
CIRCUIT_BREAKER_COOLDOWN = 1800
BACKOFF_BASE = 180
BACKOFF_MAX = 1800
REFLECTION_FUTILITY_MAX = 5
REFLECTION_BACKOFF_MAX = 1800
AUTO_APPROVE_RISKS = {"low", "medium", "high", "critical"}
