
import asyncio
import json
import logging
import time
from pathlib import Path
from backend.code_editor import identify_target_files, edit_single_file
from backend.git_ops import run_tests, git_run, revert_file

logger = logging.getLogger("localmind.autonomy.execution")

async def execute_proposal_cycle(engine) -> bool:
    """Logic for executing the next proposal."""
    proposals = engine.proposals.list_proposals("approved")
    if not proposals: return False

    priority_order = {"critical": 0, "high": 1, "medium": 2, "low": 3}
    proposals.sort(key=lambda p: priority_order.get(p.get("priority", "medium"), 2))

    proposal = None
    for p in proposals:
        if engine.proposals.is_prerequisite_met(p):
            proposal = p
            break
    if not proposal: return False

    try:
        engine._emit_activity("executing", f"Starting: {proposal['title']}", proposal_id=proposal["id"])
        
        target_result = await identify_target_files(proposal, engine.ollama_url, engine.editing_model, emit_activity=engine._emit_activity)
        targets = target_result[0] if isinstance(target_result, tuple) else []
        
        if not targets:
            engine.proposals.mark_failed(proposal, "No target files found")
            return True

        branch_name = f"self-improve/{proposal['id']}"
        git_run(["checkout", "-b", branch_name])

        edits_applied = []
        for target_file in targets[:3]:
            success, _ = await edit_single_file(target_file, proposal, engine.ollama_url, engine.editing_model, emit_activity=engine._emit_activity)
            if success: edits_applied.append(target_file)

        if not edits_applied:
            git_run(["checkout", "main"])
            git_run(["branch", "-D", branch_name])
            engine.proposals.mark_failed(proposal, "No edits applied")
            return True

        test_passed, test_output = await run_tests()
        if test_passed:
            git_run(["add", "-A"])
            git_run(["commit", "-m", f"[autonomy] {proposal['title']}"])
            git_run(["checkout", "main"])
            git_run(["merge", branch_name])
            
            proposal["status"] = "completed"
            engine.status["execution"]["proposals_executed"] += 1
            engine.success_tracker.record_outcome(proposal, success=True)
            return True
        else:
            for f in edits_applied: revert_file(f)
            git_run(["checkout", "main"])
            git_run(["branch", "-D", branch_name])
            engine.proposals.mark_failed(proposal, f"Tests failed: {test_output[:100]}")
            engine.failure_analyzer.analyze_failure(proposal, test_output)
            engine.success_tracker.record_outcome(proposal, success=False)
            return True

    except Exception as exc:
        logger.error(f"Execution failed: {exc}")
        return False
