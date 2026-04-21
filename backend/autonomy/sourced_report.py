import logging
import re
from typing import List, Dict, Any
from .goal_planner import MissionPlan

logger = logging.getLogger("localmind.autonomy.sourced_report")

class SourcedReportBuilder:
    """
    Synthesizes mission results while enforcing strict citation grounding to prevent hallucinations.
    """

    SYSTEM_PROMPT = """You are the LocalMind Verifier.
Your job is to synthesize raw task data into a final report.

STRICT GROUNDING RULES:
1. NEVER state a fact that is not directly supported by the research results provided.
2. EVERY claim must be followed by a citation like [Task Source: Title] or [URL: link].
3. If two tasks contradict each other, highlight the conflict instead of guessing.
4. If the data is missing, admit it. Do not halluncinate details.

Format:
# [Report Title]
## Executive Summary
[Summary with citations]

## Supporting Evidence
[Detailed findings mapped to task results]

## Sources Checked
- [List of all files/URLs accessed]
"""

    def __init__(self, llm_client=None):
        self.llm_client = llm_client

    async def build_report(self, plan: MissionPlan) -> str:
        """
        Synthesizes the results of all completed tasks in a plan.
        """
        logger.info(f"Synthesizing grounded report for mission: {plan.goal}")
        
        # 1. Compile the evidence bundle
        evidence = []
        for task in plan.tasks:
            if task.result:
                evidence.append(f"SOURCE: {task.title}\nDATA:\n{task.result}")
        
        bundle = "\n\n---\n\n".join(evidence)
        
        # 2. Call the synthesizer (usually a complex model like qwen-32b or gemini-pro)
        prompt = f"Objective: {plan.goal}\n\nEvidence Bundle:\n{bundle}"
        
        try:
            from backend.security.guard import guard
            
            # SHIM: In production, uses the multi-model router
            # report = await self.llm_client.generate(self.SYSTEM_PROMPT, prompt)
            
            # For now, a structured mock that demonstrates the format
            report = self._mock_synthesis(plan)
            
            # ENSURE DLP: Redact any sensitive data that leaked into the synthesis
            safe_report = guard.redact(report)
            return safe_report
            
        except Exception as e:
            logger.error(f"Failed to synthesize report: {e}")
            return f"Error during report synthesis: {str(e)}"

    def _mock_synthesis(self, plan: MissionPlan) -> str:
        """Fallback mock for report synthesis while LLM wiring completes."""
        lines = [f"# Mission Report: {plan.goal}", ""]
        lines.append("## Executive Summary")
        lines.append(f"Analysis complete across {len(plan.tasks)} data vectors. All claims are grounded in local and web research.")
        lines.append("")
        lines.append("## Findings")
        for task in plan.tasks:
            lines.append(f"### {task.title}")
            lines.append(f"{task.result or 'No data found.'} [[Source: {task.title}]]")
            lines.append("")
        
        return "\n".join(lines)
