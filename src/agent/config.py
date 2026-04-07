"""
Agent Configuration — Model registry, hardware detection, and serving config.

Design decisions informed by:
- arXiv:2510.03847 — SLM-default/LLM-fallback routing, model tier recommendations
- arXiv:2506.02153 — NVIDIA's 6-step SLM transition: start with the smallest model
  that meets quality bar, escalate only when needed
- arXiv:2512.15943 — Tool-calling SLMs: 7B models match larger models on structured
  output when fine-tuned; Qwen2.5 family excels at tool calling

Hardware tiers (from paper recommendations):
  Minimum:     8 GB RAM, CPU-only  → 7B Q4 quantized
  Recommended: 16 GB RAM, any GPU  → 14B Q4 quantized
  Optimal:     32 GB+ RAM / 24 GB VRAM → 32B model
"""

from __future__ import annotations

import logging
import os
import platform
import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

logger = logging.getLogger("agent.config")

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
WORKSPACE_DIR = Path(os.getenv("AGENT_WORKSPACE", str(Path.home() / "LocalMind_Workspace")))
MEMORY_DB_PATH = WORKSPACE_DIR / "agent_memory.db"
WORKSPACE_DIR.mkdir(parents=True, exist_ok=True)

# ---------------------------------------------------------------------------
# Serving backend
# ---------------------------------------------------------------------------
OLLAMA_BASE_URL = os.getenv("OLLAMA_URL", "http://127.0.0.1:11434")
BACKEND = os.getenv("AGENT_BACKEND", "ollama")  # "ollama" | "llamacpp"
LLAMACPP_URL = os.getenv("LLAMACPP_URL", "http://127.0.0.1:8080")

# ---------------------------------------------------------------------------
# Agent loop settings
# ---------------------------------------------------------------------------
MAX_ITERATIONS = int(os.getenv("AGENT_MAX_ITERATIONS", "10"))
MAX_BACKTRACKS = int(os.getenv("AGENT_MAX_BACKTRACKS", "2"))
MAX_CONTEXT_TOKENS = int(os.getenv("AGENT_MAX_CONTEXT", "8192"))
TOOL_TIMEOUT_SECONDS = int(os.getenv("AGENT_TOOL_TIMEOUT", "30"))


# ---------------------------------------------------------------------------
# Model registry
# ---------------------------------------------------------------------------
# Paper-informed model selection (arXiv:2510.03847, Table 3):
#   - Qwen2.5 family: best tool-calling accuracy among open SLMs
#   - Phi-4: strong reasoning for its size, good structured output
#   - Llama-3.x-8B: excellent general capability, wide Ollama support
#   - DeepSeek-R1-7B: strong chain-of-thought, weaker at tool calling
#   - Gemma4: fast inference, good for light tasks

@dataclass(frozen=True)
class ModelSpec:
    """Specification for a model in the registry."""
    name: str                        # Ollama model tag (e.g., "qwen2.5-coder:7b")
    param_billions: float            # Parameter count in billions
    min_ram_gb: int                  # Minimum system RAM needed
    min_vram_gb: int                 # Minimum GPU VRAM (0 = CPU-only ok)
    quantization: str                # "Q4_K_M", "Q8", "FP16", etc.
    strengths: list[str] = field(default_factory=list)
    tiers: list[str] = field(default_factory=list)  # ["light", "medium", "heavy"]
    tool_calling_score: float = 0.0  # 0-1, from paper benchmarks


MODEL_REGISTRY: dict[str, ModelSpec] = {
    # --- 4B tier (CPU-friendly, fast) ---
    "gemma4:e4b": ModelSpec(
        name="gemma4:e4b",
        param_billions=4.0,
        min_ram_gb=6,
        min_vram_gb=0,
        quantization="Q4_K_M",
        strengths=["fast inference", "conversation", "summarization"],
        tiers=["light"],
        tool_calling_score=0.35,
    ),
    # --- 3.8B tier (best-in-class function calling per arXiv:2510.03847) ---
    "phi-4-mini": ModelSpec(
        name="phi-4-mini",
        param_billions=3.8,
        min_ram_gb=6,
        min_vram_gb=0,
        quantization="Q4_K_M",
        strengths=["function calling", "structured output", "tool calling"],
        tiers=["light", "medium"],
        tool_calling_score=0.97,  # >=97% on BFCL-v4, matching 70B models
    ),
    # --- 7B tier (minimum viable for tool calling) ---
    "qwen2.5-coder:7b": ModelSpec(
        name="qwen2.5-coder:7b",
        param_billions=7.6,
        min_ram_gb=8,
        min_vram_gb=0,
        quantization="Q4_K_M",
        strengths=["code generation", "tool calling", "structured output"],
        tiers=["light", "medium"],
        tool_calling_score=0.72,  # arXiv:2512.15943 — competitive with 70B on BFCL
    ),
    "llama3.1:8b": ModelSpec(
        name="llama3.1:8b",
        param_billions=8.0,
        min_ram_gb=8,
        min_vram_gb=0,
        quantization="Q4_K_M",
        strengths=["general reasoning", "instruction following", "multilingual"],
        tiers=["light", "medium"],
        tool_calling_score=0.65,
    ),
    "deepseek-r1:7b": ModelSpec(
        name="deepseek-r1:7b",
        param_billions=7.6,
        min_ram_gb=8,
        min_vram_gb=0,
        quantization="Q4_K_M",
        strengths=["chain-of-thought", "math", "reasoning"],
        tiers=["light", "medium"],
        tool_calling_score=0.45,  # Weaker at structured tool calling
    ),
    # --- 14B tier (recommended for agentic use) ---
    "qwen2.5-coder:14b": ModelSpec(
        name="qwen2.5-coder:14b",
        param_billions=14.7,
        min_ram_gb=16,
        min_vram_gb=8,
        quantization="Q4_K_M",
        strengths=["code generation", "tool calling", "agentic workflows"],
        tiers=["light", "medium", "heavy"],
        tool_calling_score=0.84,
    ),
    "phi-4:14b": ModelSpec(
        name="phi-4:14b",
        param_billions=14.0,
        min_ram_gb=16,
        min_vram_gb=8,
        quantization="Q4_K_M",
        strengths=["reasoning", "structured output", "math"],
        tiers=["light", "medium", "heavy"],
        tool_calling_score=0.78,
    ),
    # --- 32B tier (optimal quality) ---
    "qwen2.5-coder:32b": ModelSpec(
        name="qwen2.5-coder:32b",
        param_billions=32.5,
        min_ram_gb=32,
        min_vram_gb=16,
        quantization="Q4_K_M",
        strengths=["code generation", "complex reasoning", "tool calling", "agentic"],
        tiers=["light", "medium", "heavy"],
        tool_calling_score=0.91,
    ),
    # --- Phi-4 Reasoning (strong chain-of-thought + tool calling) ---
    "phi4-reasoning": ModelSpec(
        name="phi4-reasoning",
        param_billions=14.0,
        min_ram_gb=16,
        min_vram_gb=8,
        quantization="Q4_K_M",
        strengths=["chain-of-thought", "reasoning", "math", "tool calling", "structured output"],
        tiers=["light", "medium", "heavy"],
        tool_calling_score=0.82,
    ),
    # --- Gemma4 variants (from existing LocalMind config) ---
    "gemma4:26b": ModelSpec(
        name="gemma4:26b",
        param_billions=26.0,
        min_ram_gb=24,
        min_vram_gb=12,
        quantization="Q4_K_M",
        strengths=["fast inference", "conversation", "general reasoning"],
        tiers=["light", "medium"],
        tool_calling_score=0.60,
    ),
}


# ---------------------------------------------------------------------------
# Hardware detection
# ---------------------------------------------------------------------------
# arXiv:2506.02153, S1 step: "Profile your hardware first, then select the
# largest model that fits comfortably with headroom for KV cache."

@dataclass
class HardwareProfile:
    """Detected hardware capabilities."""
    total_ram_gb: float
    available_ram_gb: float
    gpu_name: str
    gpu_vram_gb: float
    cpu_cores: int
    os_name: str

    @property
    def tier(self) -> str:
        """Classify hardware into minimum/recommended/optimal."""
        if self.gpu_vram_gb >= 16 or self.total_ram_gb >= 32:
            return "optimal"
        if self.gpu_vram_gb >= 6 or self.total_ram_gb >= 16:
            return "recommended"
        return "minimum"


def detect_hardware() -> HardwareProfile:
    """Detect available RAM, GPU, and CPU resources.

    Uses cross-platform approaches:
    - psutil for RAM if available, otherwise platform-specific fallbacks
    - nvidia-smi for NVIDIA GPUs
    - os.cpu_count() for CPU cores
    """
    total_ram = _detect_ram()
    available_ram = total_ram * 0.7  # Conservative estimate
    gpu_name, gpu_vram = _detect_gpu()
    cpu_cores = os.cpu_count() or 4

    profile = HardwareProfile(
        total_ram_gb=total_ram,
        available_ram_gb=available_ram,
        gpu_name=gpu_name,
        gpu_vram_gb=gpu_vram,
        cpu_cores=cpu_cores,
        os_name=platform.system(),
    )
    logger.info(f"Hardware: {profile.total_ram_gb:.0f}GB RAM, "
                f"{profile.gpu_name} ({profile.gpu_vram_gb:.0f}GB VRAM), "
                f"{profile.cpu_cores} cores → tier={profile.tier}")
    return profile


def _detect_ram() -> float:
    """Detect total system RAM in GB."""
    try:
        import psutil
        return psutil.virtual_memory().total / (1024 ** 3)
    except ImportError:
        pass

    system = platform.system()
    try:
        if system == "Windows":
            result = subprocess.run(
                ["wmic", "ComputerSystem", "get", "TotalPhysicalMemory"],
                capture_output=True, text=True, timeout=5,
            )
            for line in result.stdout.strip().splitlines():
                line = line.strip()
                if line.isdigit():
                    return int(line) / (1024 ** 3)
        elif system == "Darwin":
            result = subprocess.run(
                ["sysctl", "-n", "hw.memsize"],
                capture_output=True, text=True, timeout=5,
            )
            return int(result.stdout.strip()) / (1024 ** 3)
        else:  # Linux
            with open("/proc/meminfo") as f:
                for line in f:
                    if line.startswith("MemTotal:"):
                        return int(line.split()[1]) / (1024 ** 2)
    except Exception as e:
        logger.warning(f"RAM detection failed: {e}")

    return 8.0  # Conservative fallback


def _detect_gpu() -> tuple[str, float]:
    """Detect GPU name and VRAM in GB. Returns ("CPU-only", 0.0) if none found."""
    nvidia_smi = shutil.which("nvidia-smi")
    if nvidia_smi:
        try:
            result = subprocess.run(
                [nvidia_smi, "--query-gpu=name,memory.total", "--format=csv,noheader,nounits"],
                capture_output=True, text=True, timeout=5,
            )
            if result.returncode == 0:
                line = result.stdout.strip().splitlines()[0]
                parts = [p.strip() for p in line.split(",")]
                name = parts[0]
                vram_mb = float(parts[1])
                return name, vram_mb / 1024
        except Exception as e:
            logger.warning(f"nvidia-smi failed: {e}")

    # Check for AMD ROCm
    rocm_smi = shutil.which("rocm-smi")
    if rocm_smi:
        try:
            result = subprocess.run(
                [rocm_smi, "--showmeminfo", "vram", "--csv"],
                capture_output=True, text=True, timeout=5,
            )
            if result.returncode == 0 and "Total" in result.stdout:
                return "AMD GPU", 8.0  # Rough estimate
        except Exception:
            pass

    return "CPU-only", 0.0


# ---------------------------------------------------------------------------
# Model selection logic
# ---------------------------------------------------------------------------
# arXiv:2510.03847 — "SLM-default/LLM-fallback": always try the smallest
# capable model first. Only escalate when tool-call schema validity < 80%
# or when the task requires multi-step reasoning beyond model capacity.

def select_model(
    hardware: Optional[HardwareProfile] = None,
    task_type: str = "general",
    prefer_tool_calling: bool = True,
) -> ModelSpec:
    """Select the best model for the given hardware and task.

    Strategy (from arXiv:2506.02153, S2 "Task-Aware Selection"):
    1. Filter models that fit in available hardware
    2. If tool calling needed, sort by tool_calling_score
    3. Otherwise sort by param count (bigger = better reasoning)
    4. Return the best that fits

    Args:
        hardware: Detected hardware profile (auto-detected if None)
        task_type: "general", "code", "reasoning", "tool_calling"
        prefer_tool_calling: Prioritize tool-calling accuracy over size
    """
    if hardware is None:
        hardware = detect_hardware()

    # Check which models are actually available in Ollama
    available = set(list_ollama_models())
    # Normalize names (Ollama may return "model:latest" or "model:tag")
    available_normalized = set()
    for m in available:
        available_normalized.add(m)
        if ":" in m:
            available_normalized.add(m.split(":")[0])

    # Filter models that fit hardware AND are installed
    candidates = []
    for spec in MODEL_REGISTRY.values():
        fits_ram = spec.min_ram_gb <= hardware.total_ram_gb
        fits_vram = spec.min_vram_gb <= hardware.gpu_vram_gb or spec.min_vram_gb == 0
        is_available = spec.name in available_normalized or not available  # skip check if Ollama is down
        if fits_ram and fits_vram and is_available:
            candidates.append(spec)

    if not candidates:
        # Fallback to smallest model
        logger.warning("No model fits hardware — falling back to smallest available")
        return min(MODEL_REGISTRY.values(), key=lambda s: s.param_billions)

    # Sort strategy
    if prefer_tool_calling:
        # Primary: tool calling score, secondary: smaller is better (faster)
        candidates.sort(key=lambda s: (-s.tool_calling_score, s.param_billions))
    else:
        # Bigger model = better general reasoning
        candidates.sort(key=lambda s: -s.param_billions)

    chosen = candidates[0]
    logger.info(f"Model selected: {chosen.name} (tool_score={chosen.tool_calling_score}, "
                f"params={chosen.param_billions}B) for hardware tier={hardware.tier}")
    return chosen


def select_model_for_tier(tier: str) -> str:
    """Quick lookup: return the best Ollama model name for a task tier.

    Compatible with existing LocalMind config.MODEL_TIERS pattern.
    """
    tier_defaults = {
        "light": "gemma4:e4b",
        "medium": "qwen2.5-coder:14b",
        "heavy": "qwen2.5-coder:32b",
        "ultra": "qwen2.5-coder:32b",
    }
    return tier_defaults.get(tier, "qwen2.5-coder:7b")


# ---------------------------------------------------------------------------
# Ollama helpers
# ---------------------------------------------------------------------------

def check_ollama_running() -> bool:
    """Check if Ollama is accessible."""
    try:
        import httpx
        r = httpx.get(f"{OLLAMA_BASE_URL}/api/tags", timeout=3.0)
        return r.status_code == 200
    except Exception:
        return False


def list_ollama_models() -> list[str]:
    """List models currently available in Ollama."""
    try:
        import httpx
        r = httpx.get(f"{OLLAMA_BASE_URL}/api/tags", timeout=5.0)
        if r.status_code == 200:
            return [m["name"] for m in r.json().get("models", [])]
    except Exception:
        pass
    return []
