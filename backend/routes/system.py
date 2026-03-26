import json
import logging
import httpx
import psutil
from pathlib import Path
from fastapi import APIRouter
from backend.config import OLLAMA_BASE_URL

router = APIRouter(prefix="/api")
logger = logging.getLogger("localmind.routes.system")

@router.get("/health")
async def health_check():
    """Check server and Ollama connectivity."""
    try:
        async with httpx.AsyncClient() as client:
            resp = await client.get(f"{OLLAMA_BASE_URL}/api/tags", timeout=5.0)
            ollama_ok = resp.status_code == 200
    except Exception:
        ollama_ok = False
    return {"server": True, "ollama": ollama_ok}

@router.get("/version")
async def get_version():
    """Return the current build version."""
    version_file = Path(__file__).parent.parent.parent / "version.json"
    if version_file.exists():
        try:
            with open(version_file) as f:
                return json.load(f)
        except Exception:
            pass
    return {"version": "unknown", "build": 0}

@router.get("/hardware")
async def hardware_status():
    """Get system and Ollama hardware usage."""
    cpu_pct = psutil.cpu_percent(interval=0.1)
    mem = psutil.virtual_memory()
    system = {
        "cpu_percent": cpu_pct,
        "ram_used_gb": round(mem.used / (1024**3), 1),
        "ram_total_gb": round(mem.total / (1024**3), 1),
        "ram_percent": mem.percent,
    }

    models = []
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            r = await client.get(f"{OLLAMA_BASE_URL}/api/ps")
            data = r.json()
            for m in data.get("models", []):
                models.append({
                    "name": m.get("name", "unknown"),
                    "size_gb": round(m.get("size", 0) / (1024**3), 1),
                    "vram_gb": round(m.get("size_vram", 0) / (1024**3), 1),
                    "processor": m.get("details", {}).get("quantization_level", ""),
                })
    except Exception:
        pass

    return {"loaded": len(models) > 0, "models": models, "system": system}

@router.get("/models")
async def list_models():
    """List available Ollama models."""
    try:
        async with httpx.AsyncClient() as client:
            resp = await client.get(f"{OLLAMA_BASE_URL}/api/tags", timeout=10.0)
            data = resp.json()
            models = [
                {"name": m["name"], "size": m.get("size", 0)}
                for m in data.get("models", [])
            ]
            return {"models": models}
    except Exception as e:
        return {"models": [], "error": str(e)}
