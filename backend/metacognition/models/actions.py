from dataclasses import dataclass, field
from typing import List, Optional, Any

@dataclass
class Action:
    name: str
    args: dict = field(default_factory=dict)

@dataclass
class ActionDecision:
    action: Action
    reason: str

@dataclass
class Response:
    content: str
    metadata: dict = field(default_factory=dict)

@dataclass
class CheckResult:
    valid: bool
    issues: List[str] = field(default_factory=list)

@dataclass
class UncertaintyScore:
    score: float
    factors: List[str] = field(default_factory=list)
