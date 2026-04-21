import json
import logging
from typing import List, Dict, Optional, Any
from dataclasses import dataclass, field
from enum import Enum

logger = logging.getLogger("localmind.autonomy.goal_planner")

class TaskStatus(Enum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETE = "complete"
    FAILED = "failed"
    BLOCKED = "blocked"

@dataclass
class PlanTask:
    id: str
    title: str
    description: str
    dependencies: List[str] = field(default_factory=list)
    tool_calls: List[Dict[str, Any]] = field(default_factory=list)
    status: TaskStatus = TaskStatus.PENDING
    result: Optional[Any] = None

    @classmethod
    def from_dict(cls, data: Dict[str, Any]):
        return cls(
            id=data["id"],
            title=data["title"],
            description=data["description"],
            dependencies=data.get("dependencies", []),
            status=TaskStatus(data.get("status", "pending")),
            result=data.get("result")
        )

@dataclass
class MissionPlan:
    goal: str
    tasks: List[PlanTask] = field(default_factory=list)
    needs_clarification: bool = False
    clarification_question: Optional[str] = None
    metadata: Dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]):
        tasks = [PlanTask.from_dict(t) for t in data.get("tasks", [])]
        return cls(
            goal=data["goal"],
            tasks=tasks,
            needs_clarification=data.get("needs_clarification", False),
            clarification_question=data.get("clarification_question"),
            metadata=data.get("metadata", {})
        )

    def to_dict(self):
        return {
            "goal": self.goal,
            "tasks": [
                {
                    "id": t.id,
                    "title": t.title,
                    "description": t.description,
                    "dependencies": t.dependencies,
                    "status": t.status.value,
                    "result": t.result
                }
                for t in self.tasks
            ],
            "needs_clarification": self.needs_clarification,
            "clarification_question": self.clarification_question,
            "metadata": self.metadata
        }

class GoalPlanner:
    """
    The brain that turns a user briefing into a structured mission plan.
    Enforces the 'One Question Rule'.
    """

    SYSTEM_PROMPT = """You are the LocalMind Goal Architect.
Your job is to take a user's mission briefing and turn it into a step-by-step task tree.

ONE QUESTION RULE (STRICT):
1. If the request is too vague to act on, ask EXACTLY ONE clarifying question.
2. BUNDLE EVERYTHING: If you need 3 details (e.g. Budget, Color, Size), ask for them all in ONE single question. 
3. NO PING-PONGING: Do not ask "what color?" then "what size?" then "what material?". 
4. PROACTIVE BIAS: If you can take even ONE initial research step to narrow it down yourself, start working instead of asking.
5. If you ask a question, the 'tasks' list should be empty.

OUTPUT FORMAT:
You must output a JSON object with this structure:
{
  "needs_clarification": boolean,
  "clarification_question": "string or null",
  "tasks": [
    {
      "id": "task_1",
      "title": "Short title",
      "description": "Detailed instruction",
      "dependencies": []
    }
  ]
}

Available Tools (for context):
- search_web: search the internet
- read_drive_file: read a file from Google Drive
- list_gmail: search emails
- write_doc: create a Google Doc report
- local_file_op: read/write local files

Example:
User: "Research watches."
Agent: "I can look into general trends, but to give you a 'LocalMind Deal Seal', do you have a specific budget range or preferred brands in mind?" (tasks: [])
"""

    def __init__(self, llm_client=None):
        self.llm_client = llm_client

    async def create_plan(self, briefing: str, context: Optional[Dict] = None) -> MissionPlan:
        """Analyze briefing and generate a MissionPlan."""
        logger.info(f"Architecting mission for: {briefing}")
        
        # In a real implementation, we would call the LLM here.
        # For the skeleton, we provide the logic.
        
        prompt = f"User Briefing: {briefing}\nContext: {json.dumps(context or {})}"
        
        try:
            # SHIM: In production, this calls backend.inference.llm_client
            # For now, we'll implement the logic assuming the LLM result
            plan_json = await self._call_llm(self.SYSTEM_PROMPT, prompt)
            
            data = json.loads(plan_json)
            
            plan = MissionPlan(
                goal=briefing,
                needs_clarification=data.get("needs_clarification", False),
                clarification_question=data.get("clarification_question"),
                metadata=data.get("metadata", {})
            )
            
            for t_data in data.get("tasks", []):
                plan.tasks.append(PlanTask(
                    id=t_data["id"],
                    title=t_data["title"],
                    description=t_data["description"],
                    dependencies=t_data.get("dependencies", [])
                ))
                
            return plan
            
        except Exception as e:
            logger.error(f"Failed to architect plan: {e}")
            raise

    async def _call_llm(self, system: str, user: str) -> str:
        """Call the router to get a plan. Defaulting to local_ultra for planning stability."""
        # This will be wired to actual LLM provider in the next step
        # Placeholder for building/testing
        return json.dumps({
            "needs_clarification": False,
            "tasks": [
                {
                    "id": "init_research",
                    "title": "Gather Initial Data",
                    "description": "Search for context related to the user's briefing.",
                    "dependencies": []
                }
            ]
        })
