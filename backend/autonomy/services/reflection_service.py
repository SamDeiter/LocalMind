import logging
import json
import time
from typing import List, Dict, Any, Optional
from backend.validation.root_cause import TraceAnalyzer, TraceEntry
from backend.validation.base import ValidationResult

logger = logging.getLogger("localmind.autonomy.reflection")

class ReflectionService:
    """
    Centralized service for managing agent reflection, failure analysis,
    and action proposals. Phase 3 of AgentFixer integration.
    """
    
    def __init__(self, ollama_url: str):
        self.analyzer = TraceAnalyzer(ollama_url=ollama_url)
        self.active_traces: Dict[str, str] = {} # task_id -> trace_id

    def log_step_failure(self, task_id: str, stage: str, validator: str, error_msg: str):
        """Log a validation failure for later RCA."""
        trace_id = self.active_traces.get(task_id)
        if not trace_id:
            trace_id = f"trace_{task_id}_{int(time.time())}"
            self.active_traces[task_id] = trace_id
            
        entry = TraceEntry(
            stage=stage,
            validator_name=validator,
            result=ValidationResult(
                validator_name=validator,
                passed=False,
                message=error_msg
            )
        )
        self.analyzer.log_failure(trace_id, entry)

    async def analyze_failure(self, task_id: str) -> Dict[str, Any]:
        """Perform Root Cause Analysis on the recorded failures for a task."""
        trace_id = self.active_traces.get(task_id)
        if not trace_id:
            return {"error": "No trace found for this task"}
            
        return await self.analyzer.analyze(trace_id)

    def clear_trace(self, task_id: str):
        """Cleanup memory after a task is finished or recovered."""
        if task_id in self.active_traces:
            del self.active_traces[task_id]
