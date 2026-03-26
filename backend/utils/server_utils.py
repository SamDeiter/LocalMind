import re
import logging
import subprocess
from backend.config import MODEL_TIERS

logger = logging.getLogger("localmind.utils.server")

def kill_existing_server(port: int = 8000):
    """Kill any process currently using the given port (Windows)."""
    try:
        result = subprocess.run(["netstat", "-ano"], capture_output=True, text=True)
        for line in result.stdout.splitlines():
            if f":{port}" in line and "LISTENING" in line:
                parts = line.split()
                pid = parts[-1]
                if pid != "0":
                    subprocess.run(["taskkill", "/F", "/PID", pid], capture_output=True)
                    logger.info(f"Killed existing server on port {port} (PID {pid})")
    except Exception as e:
        logger.warning(f"Port guard check failed: {e}")

def estimate_task_complexity(message: str, history_len: int = 0) -> dict:
    """Score message complexity 0-10 and pick the right model tier."""
    msg = message.lower().strip()
    score = 3
    
    # Lighter signals
    greetings = r'^(hi|hey|hello|yo|sup|thanks|thank you|ok|cool|got it|bye|gm|gn)\b'
    if re.match(greetings, msg): score -= 2
    if len(msg) < 30: score -= 1
    
    # Heavier signals
    heavy_code = ["write a", "implement", "build a", "create a", "generate", "design a"]
    if any(k in msg for k in heavy_code) and len(msg) > 40: score += 2
    deep_analysis = ["refactor", "debug", "optimize", "review", "analyze", "architecture"]
    if any(k in msg for k in deep_analysis): score += 3
    
    score = max(0, min(10, score))
    
    if score <= 3: tier, model = "light", MODEL_TIERS["light"]
    elif score <= 5: tier, model = "medium", MODEL_TIERS["medium"]
    elif score <= 8: tier, model = "heavy", MODEL_TIERS["heavy"]
    else: tier, model = "ultra", MODEL_TIERS["ultra"]

    return {
        "score": score,
        "tier": tier,
        "model": model,
        "reason": "complex" if score > 5 else "simple"
    }
