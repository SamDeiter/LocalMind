from dataclasses import dataclass, field
from typing import List, Optional, Any
import time

@dataclass
class CalibrationEntry:
    timestamp: float
    actual: Any
    predicted: Any

@dataclass
class UserPreference:
    key: str
    value: Any
    confidence: float
    source: str
    timestamp: float = field(default_factory=time.time)
    observations: List[str] = field(default_factory=list)
    session_ids: List[str] = field(default_factory=list)
    _last_session: Optional[str] = None
