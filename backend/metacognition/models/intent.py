from dataclasses import dataclass, field
from typing import List, Optional

@dataclass
class Assumption:
    text: str
    verified: bool = False

@dataclass
class ConfidenceLevel:
    score: float
    reason: str

@dataclass
class IntentState:
    intent: str
    confidence: float
    assumptions: List[Assumption] = field(default_factory=list)
