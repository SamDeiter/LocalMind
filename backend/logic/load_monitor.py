"""
load_monitor.py — GPU Load-Aware Model Routing
================================================
Queries Ollama's /api/ps endpoint to determine what models are currently
loaded in VRAM. Advises the router to reuse loaded models when possible,
avoiding expensive model swap cold-starts (10-20s delays).
"""

import logging
import httpx
from typing import Dict, Any, List, Optional

from backend import config
from backend.utils.http_client import get_async_client

logger = logging.getLogger("localmind.logic.load_monitor")


class LoadMonitor:
    """Monitors GPU load via Ollama and recommends optimal model routing."""

    def __init__(self, ollama_url: str = config.OLLAMA_BASE_URL):
        self.ollama_url = ollama_url.rstrip("/")
        self._timeout = httpx.Timeout(3.0, connect=2.0)

    async def get_gpu_state(self) -> Dict[str, Any]:
        """Query Ollama /api/ps for currently loaded models and VRAM usage.

        Returns:
            {
                "loaded_models": [{"name": "gemma4:26b", "size_gb": 17.0, "vram_gb": 10.0}],
                "total_vram_used_gb": 10.0,
                "gpu_busy": True/False,
            }
        """
        try:
            client = get_async_client()
            resp = await client.get(f"{self.ollama_url}/api/ps", timeout=self._timeout)
            if resp.status_code != 200:
                logger.warning(f"Ollama /api/ps returned {resp.status_code}")
                return self._empty_state()

            data = resp.json()
            loaded = []
            total_vram = 0.0

            for m in data.get("models", []):
                vram_gb = round(m.get("size_vram", 0) / (1024**3), 1)
                size_gb = round(m.get("size", 0) / (1024**3), 1)
                loaded.append({
                    "name": m.get("name", "unknown"),
                    "size_gb": size_gb,
                    "vram_gb": vram_gb,
                })
                total_vram += vram_gb

            return {
                "loaded_models": loaded,
                "total_vram_used_gb": round(total_vram, 1),
                "gpu_busy": len(loaded) > 0,
            }

        except Exception as e:
            logger.warning(f"Failed to query GPU state: {e}")
            return self._empty_state()

    @staticmethod
    def can_handle_tier(model_name: str, tier: str) -> bool:
        """Check if a model is capable enough for the requested tier.

        Uses MODEL_CAPABILITIES from config. If the model isn't in the map,
        we conservatively assume it can only handle 'light'.
        Handles Ollama name variants like ':latest' suffix.
        """
        # Try exact match first, then strip :latest
        caps = config.MODEL_CAPABILITIES.get(model_name)
        if caps is None:
            stripped = model_name.replace(":latest", "")
            caps = config.MODEL_CAPABILITIES.get(stripped, ["light"])
        return tier in caps

    @staticmethod
    def pick_best_model(
        tier: str,
        loaded_models: List[Dict[str, Any]],
    ) -> Optional[str]:
        """If a currently loaded model can handle the requested tier, return it.

        Prefers the smallest capable loaded model to leave VRAM headroom.
        Returns None if no loaded model can handle the tier.
        """
        candidates = []
        for m in loaded_models:
            name = m["name"]
            if LoadMonitor.can_handle_tier(name, tier):
                candidates.append((name, m.get("size_gb", 0)))

        if not candidates:
            return None

        # Pick the smallest capable model (least VRAM waste)
        candidates.sort(key=lambda x: x[1])
        chosen = candidates[0][0]
        logger.info(f"Load-aware routing: reusing loaded model '{chosen}' for tier '{tier}'")
        return chosen

    @staticmethod
    def _empty_state() -> Dict[str, Any]:
        return {
            "loaded_models": [],
            "total_vram_used_gb": 0.0,
            "gpu_busy": False,
        }
