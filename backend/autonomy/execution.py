
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

    # Load user priorities to boost matching proposals
    priority_order = {"critical": 0, "high": 1, "medium": 2, "low": 3}
    user_priorities = []
    try:
        prio_file = Path(__file__).parent.parent.parent / "data" / "priorities.json"
        if prio_file.exists():
            import json as _json
            prios = _json.loads(prio_file.read_text(encoding="utf-8"))
            user_priorities = [p.get("description", "").lower() for p in prios if p.get("status") == "active"]
    except Exception:
        pass

    def proposal_score(p):
        base = priority_order.get(p.get("priority", "medium"), 2)
        # Boost proposals that match user priorities (-1 = higher priority)
        title_lower = p.get("title", "").lower() + " " + p.get("description", "").lower()
        if any(kw in title_lower for kw in user_priorities if kw):
            base -= 1
        return base

    proposals.sort(key=proposal_score)

    proposal = None
    for p in proposals:
        if engine.proposals.is_prerequisite_met(p):
            proposal = p
            break
    if not proposal: return False

    try:
        # Route non-code tasks to the general task executor
        task_type = proposal.get("task_type", "code_edit")
        category = proposal.get("category", "")
        if task_type != "code_edit" or category in ("research", "documentation", "data", "spreadsheet"):
            from backend.autonomy.task_executor import _execute_with_agent
            mode = "research" if category in ("research", "documentation") else "general"
            return await _execute_with_agent(engine, proposal, mode)

        engine._emit_activity("executing", f"Starting: {proposal['title']}", proposal_id=proposal["id"])
        
        target_result = await identify_target_files(proposal, engine.ollama_url, engine.editing_model, emit_activity=engine._emit_activity)
        targets = target_result[0] if isinstance(target_result, tuple) else []
        
        if not targets:
            engine.proposals.mark_failed(proposal, "No target files found")
            return True

        branch_name = f"self-improve/{proposal['id']}"
        git_run(["checkout", "-B", branch_name])

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
        if proposal:
            try:
                engine.proposals.mark_failed(proposal, f"Crashed during execution: {str(exc)[:150]}")
            except Exception as e2:
                logger.error(f"Failed to update proposal status after crash: {e2}")
        return False
