
import json
import logging
import time
from dataclasses import dataclass, field
from typing import List, Dict, Any, Optional
from pathlib import Path
from .base import ValidationResult, ValidationReport
from backend.config import OLLAMA_BASE_URL

logger = logging.getLogger("localmind.validation.root_cause")

@dataclass
class TraceEntry:
    stage: str  # e.g., "proposal", "critique", "edit", "verify"
    validator_name: str
    result: ValidationResult
    timestamp: float = field(default_factory=time.time)

@dataclass
class FailureTrace:
    trace_id: str
    target_proposal_id: Optional[str]
    entries: List[TraceEntry] = field(default_factory=list)
    final_disposition: str = "failed"  # "failed", "partially_recovered", "fixed"
    causal_factor: Optional[str] = None
    confidence: float = 0.0

class TraceAnalyzer:
    """Analyzes a sequence of validation failures to find the 'root cause'."""

    def __init__(self, ollama_url: str = OLLAMA_BASE_URL):
        self.ollama_url = ollama_url
        self.trace_history: List[FailureTrace] = []

    def log_failure(self, trace_id: str, entry: TraceEntry):
        """Accumulate a validation failure into an active trace."""
        trace = next((t for t in self.trace_history if t.trace_id == trace_id), None)
        if not trace:
            trace = FailureTrace(trace_id=trace_id, target_proposal_id=None)
            self.trace_history.append(trace)
        
        trace.entries.append(entry)
        logger.info(f"Trace {trace_id}: Added {entry.validator_name} failure in {entry.stage}")

    async def analyze(self, trace_id: str) -> Dict[str, Any]:
        """Ask the model to identify the root cause of the failures in this trace."""
        trace = next((t for t in self.trace_history if t.trace_id == trace_id), None)
        if not trace or not trace.entries:
            return {"error": "No trace found for this ID"}

        # Summarize the failing sequence
        summary_lines = [f"{e.stage}: {e.validator_name}: {e.result.error}" for e in trace.entries]
        failure_log = "\n".join(summary_lines)

        prompt = (
            f"Review the failing sequence of a software agent's task:\n\n"
            f"{failure_log}\n\n"
            f"Based on the AgentFixer validation paper, identify the most likely ROOT CAUSE:\n"
            f"1. MODEL_FAILURE: Hallucination or inability to follow instructions.\n"
            f"1.1. PARSE_FAILURE: Model output format is correct but data is corrupt.\n"
            f"2. TOOL_FAILURE: Tool integration error or missing data hooks.\n"
            f"3. LOGIC_FAILURE: Faulty reasoning or flawed task approach.\n\n"
            f"Return JSON: 'causal_factor', 'explanation', 'recovery_strategy'."
        )

        import httpx
        async with httpx.AsyncClient(timeout=60.0) as client:
            try:
                resp = await client.post(
                    f"{self.ollama_url}/api/chat",
                    json={
                        "model": "qwen2.5-coder:7b",
                        "messages": [{"role": "user", "content": prompt}],
                        "stream": False,
                        "format": "json"
                    }
                )
                if resp.status_code == 200:
                    raw_data = resp.json().get("message", {}).get("content", "{}")
                    data = json.loads(raw_data)
                    trace.causal_factor = data.get("causal_factor", "UNKNOWN")
                    return data
            except Exception as exc:
                logger.error(f"RCA Analysis failed: {exc}")
        
        return {"causal_factor": "UNKNOWN", "explanation": "Failed to query the LLM analyzer."}
