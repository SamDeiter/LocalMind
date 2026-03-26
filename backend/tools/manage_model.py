"""
manage_model.py — LocalMind Resource Management Tool
=====================================================
Gives the AI the ability to load and unload models from VRAM/RAM via the Ollama API.
"""

import httpx
import logging
from typing import Any
from .base import BaseTool

logger = logging.getLogger("localmind.tools.manage_model")

OLLAMA_BASE_URL = "http://127.0.0.1:11434"

class ManageModelTool(BaseTool):
    """Tool to load or unload Ollama models dynamically."""
    
    @property
    def name(self) -> str:
        return "manage_model"

    @property
    def description(self) -> str:
        return "Load or unload AI models from memory. Use 'unload' to free up RAM/VRAM."

    @property
    def parameters(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "model_name": {
                    "type": "string",
                    "description": "The name of the model (e.g., 'qwen2.5-coder:70b')"
                },
                "action": {
                    "type": "string",
                    "enum": ["load", "unload", "pull"],
                    "description": "Whether to pre-warm the model, free its resources, or download it."
                },
                "keep_alive": {
                    "type": "string",
                    "description": "Time to keep model in memory (e.g. '5m', '1h'). Use '0' for immediate unload.",
                    "default": "5m"
                }
            },
            "required": ["model_name", "action"]
        }

    async def execute(self, model_name: str, action: str, keep_alive: str = "5m") -> dict[str, Any]:
        try:
            async with httpx.AsyncClient(timeout=120.0 if action == "pull" else 60.0) as client:
                if action == "pull":
                    logger.info(f"Ollama: Pulling model {model_name}...")
                    resp = await client.post(f"{OLLAMA_BASE_URL}/api/pull", json={"name": model_name, "stream": False})
                    if resp.status_code == 200:
                        return {"success": True, "result": f"Model {model_name} pull initiated/completed."}
                    else:
                        return {"success": False, "error": f"Ollama pull failed: {resp.text}"}

                if action == "unload":
                    keep_alive = "0"
                    
                logger.info(f"Ollama: {action.capitalize()}ing model {model_name} with keep_alive={keep_alive}")
                
                # We send a dummy request to /api/generate with keep_alive to trigger load/unload
                payload = {
                    "model": model_name,
                    "prompt": "",  # Empty prompt for loading
                    "keep_alive": keep_alive
                }
                
                resp = await client.post(f"{OLLAMA_BASE_URL}/api/generate", json=payload)
                if resp.status_code == 200:
                    return {
                        "success": True, 
                        "result": f"Model {model_name} {action}ed successfully."
                    }
                else:
                    return {
                        "success": False, 
                        "error": f"Ollama returned status {resp.status_code}: {resp.text}"
                    }
                    
        except Exception as e:
            logger.error(f"Failed to manage model {model_name}: {e}")
            return {"success": False, "error": str(e)}
