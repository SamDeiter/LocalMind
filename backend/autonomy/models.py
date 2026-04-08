"""Data models for the Autonomy Engine."""

import time
from dataclasses import dataclass, field
from typing import Any, Dict, Optional


@dataclass
class HealthCheckStatus:
    last_run: Optional[float] = None
    ollama_ok: bool = False
    model_loaded: bool = False


@dataclass
class ReflectionStatus:
    last_run: Optional[float] = None
    proposals_logged: int = 0


@dataclass
class ExecutionStatus:
    last_run: Optional[float] = None
    proposals_executed: int = 0
    last_result: Optional[str] = None


@dataclass
class AutoTestStatus:
    last_run: Optional[float] = None
    passed: int = 0
    failed: int = 0


@dataclass
class ResearchStatus:
    last_run: float = 0


@dataclass
class AgentLoopStatus:
    active: bool = False
    current_agent: Optional[str] = None


@dataclass
class EngineStatus:
    enabled: bool = True
    mode: str = "autonomous"
    started_at: float = field(default_factory=time.time)
    health: str = "unknown"
    current_activity: Optional[Dict[str, Any]] = None
    health_check: HealthCheckStatus = field(default_factory=HealthCheckStatus)
    reflection: ReflectionStatus = field(default_factory=ReflectionStatus)
    execution: ExecutionStatus = field(default_factory=ExecutionStatus)
    auto_test: AutoTestStatus = field(default_factory=AutoTestStatus)
    research: ResearchStatus = field(default_factory=ResearchStatus)
    agent_loop: AgentLoopStatus = field(default_factory=AgentLoopStatus)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "enabled": self.enabled,
            "mode": self.mode,
            "started_at": self.started_at,
            "health": self.health,
            "current_activity": self.current_activity,
            "health_check": {
                "last_run": self.health_check.last_run,
                "ollama_ok": self.health_check.ollama_ok,
                "model_loaded": self.health_check.model_loaded,
            },
            "reflection": {
                "last_run": self.reflection.last_run,
                "proposals_logged": self.reflection.proposals_logged,
            },
            "execution": {
                "last_run": self.execution.last_run,
                "proposals_executed": self.execution.proposals_executed,
                "last_result": self.execution.last_result,
            },
            "auto_test": {
                "last_run": self.auto_test.last_run,
                "passed": self.auto_test.passed,
                "failed": self.auto_test.failed,
            },
            "research": {"last_run": self.research.last_run},
            "agent_loop": {
                "active": self.agent_loop.active,
                "current_agent": self.agent_loop.current_agent,
            },
        }
