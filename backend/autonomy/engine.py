import asyncio
import logging
import uuid
from typing import Dict, List, Any
from datetime import datetime

from .goal_planner import MissionPlan, TaskStatus, PlanTask
from backend.swarm.task_queue import TaskType
from backend.agent import agent_chat_streaming

logger = logging.getLogger("localmind.autonomy.engine")

class AutonomyEngine:
    """
    The engine that drives the MissionPlan to completion.
    Manages the task queue, dependency resolution, and synthesized reporting.
    """

    def __init__(self, coordinator=None):
        self.coordinator = coordinator
        self.active_missions: Dict[str, MissionPlan] = {}

    async def execute_mission(self, plan_data: Dict[str, Any]):
        """
        Main execution loop for a mission.
        """
        mission_id = plan_data.get("mission_id", str(uuid.uuid4()))
        plan = MissionPlan.from_dict(plan_data["plan"])
        
        logger.info(f"🚀 Mission {mission_id} engaged: {plan.goal}")
        self.active_missions[mission_id] = plan

        while any(t.status in [TaskStatus.PENDING, TaskStatus.RUNNING] for t in plan.tasks):
            # 1. Find ready tasks (all dependencies complete)
            ready_tasks = [
                t for t in plan.tasks 
                if t.status == TaskStatus.PENDING and 
                all(self._get_task(plan, dep).status == TaskStatus.COMPLETE for dep in t.dependencies)
            ]

            if not ready_tasks and any(t.status == TaskStatus.RUNNING for t in plan.tasks):
                # Wait for some tasks to finish
                await asyncio.sleep(1)
                continue
            
            if not ready_tasks:
                # Deadlock detection or FAILED dependency?
                break
                
            # 2. Parallel execute ready tasks
            execution_slots = [self._run_task_with_agent(mission_id, plan, task) for task in ready_tasks]
            await asyncio.gather(*execution_slots)

        logger.info(f"✅ Mission {mission_id} complete.")
        report = await self._synthesize_report(plan)
        
        # ── Minting (Integrity Seal) ───────────────────────
        from backend.security.integrity import minter
        mint_token = minter.mint(
            request_data=plan.goal,
            result_data=report,
            metadata={
                "mission_id": mission_id,
                "model": "swarm-orchestrated",
                "task_count": len(plan.tasks)
            }
        )
        logger.info(f"🪙 Mission {mission_id} minted: {mint_token}")
        
        return {
            "report": report,
            "mint_token": mint_token,
            "mission_id": mission_id
        }

    def _get_task(self, plan: MissionPlan, task_id: str) -> PlanTask:
        for t in plan.tasks:
            if t.id == task_id:
                return t
        raise ValueError(f"Task {task_id} not found in plan")

    async def _run_task_with_agent(self, mission_id: str, plan: MissionPlan, task: PlanTask):
        """
        Wraps the standard agent_chat_streaming loop for a specific autonomous task.
        """
        task.status = TaskStatus.RUNNING
        logger.info(f"  ↳ Running Task: {task.title}")

        # Construct prompt for the agent
        prompt = f"Objective: {task.description}\nContext from dependencies: {self._get_dependency_results(plan, task)}"
        
        try:
            if self.coordinator:
                # Delegate to Swarm
                task_id = self.coordinator.submit_llm(
                    prompt=prompt,
                    task_type=TaskType.LLM_GENERATE,
                    priority=2
                )
                
                # Wait for result from swarm
                results = await self.coordinator.wait_for_results([task_id], timeout=300.0)
                if results and results[0].success:
                    task.result = results[0].payload.get("content", "No content returned")
                else:
                    raise Exception(results[0].error if results else "Timeout")
            else:
                # Fallback to direct streaming if no coordinator
                full_content = ""
                async for chunk in agent_chat_streaming(prompt, history=[], conversation_id=f"autonomy_{mission_id}_{task.id}"):
                    if chunk["type"] == "content":
                        full_content += chunk["content"]
                task.result = full_content

            task.status = TaskStatus.COMPLETE
            logger.info(f"  ✓ Task Complete: {task.title}")
            
        except Exception as e:
            logger.error(f"  ✗ Task Failed: {task.title} - {e}")
            task.status = TaskStatus.FAILED

    def _get_dependency_results(self, plan: MissionPlan, task: PlanTask) -> str:
        results = []
        for dep_id in task.dependencies:
            dep = self._get_task(plan, dep_id)
            results.append(f"Result from {dep.title}: {dep.result}")
        return "\n\n".join(results)

    async def _synthesize_report(self, plan: MissionPlan) -> str:
        """
        Final synthesis step. Takes all task results and creates a source-cited report.
        Enforces grounding using SourcedReportBuilder.
        """
        from .sourced_report import SourcedReportBuilder
        builder = SourcedReportBuilder()
        return await builder.build_report(plan)
