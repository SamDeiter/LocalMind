from dataclasses import dataclass, field
from typing import List, Optional

@dataclass
class SessionState:
    session_id: str
    user_id: str
    history: List[dict] = field(default_factory=list)
