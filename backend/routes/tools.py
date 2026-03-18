"""
routes/tools.py — Tool Execution & Approval Router
====================================================
Handles endpoints for:
- Code execution (from the built-in editor's Run button)
- Approval flow (approve/deny AI-proposed actions)
- Dependency lifecycle management (list, idle detection, pinning)

The approval flow is a critical safety feature: before the AI can
perform destructive actions (file writes, shell commands, etc.), it
must propose the action and wait for user approval. This router
provides the API for the frontend to approve/deny those requests.

The dependency manager tracks which Python packages the AI has installed,
when they were last used, and flags idle ones for potential removal.
"""

import logging

from fastapi import APIRouter, Request

logger = logging.getLogger("localmind.routes.tools")

# Create router — all endpoints are tool/approval-related
router = APIRouter(prefix="/api", tags=["tools"])


# ── Code Execution ────────────────────────────────────────────────────

@router.post("/tools/run")
async def run_code_api(request: Request):
    """Execute Python code from the editor's Run button.
    
    This provides a sandboxed Python execution environment.
    Code is run via the RunCodeTool which captures stdout/stderr
    and returns the output. The tool has built-in safety limits
    (timeout, restricted imports, etc.).
    """
    body = await request.json()
    code = body.get("code", "")
    if not code.strip():
        return {"success": False, "error": "No code provided"}
    try:
        from backend.tools.run_code import RunCodeTool
        tool = RunCodeTool()
        result = await tool.execute(code=code)
        return result
    except Exception as e:
        logger.error(f"Code execution failed: {e}")
        return {"success": False, "error": str(e)}


# ── Approval Flow ─────────────────────────────────────────────────────
# The approval system ensures the AI can't take destructive actions
# without explicit user consent. The AI calls `propose_action` which
# creates an approval request. The frontend shows an approval card,
# and the user clicks approve/deny. These endpoints handle that flow.

@router.post("/approve/{request_id}")
async def approve_action(request_id: str, request: Request):
    """Approve or deny a pending action request from the AI.
    
    The request_id comes from the propose_action tool call.
    When approved, the AI proceeds with the action.
    When denied, the AI receives a "denied" result and moves on.
    
    All decisions are logged to the audit trail for accountability.
    """
    from backend.tools.propose_action import resolve_approval
    body = await request.json()
    approved = body.get("approved", False)
    success = resolve_approval(request_id, approved)
    if not success:
        return {"success": False, "error": "Request not found or already resolved"}
    logger.info(f"Approval {request_id}: {'APPROVED' if approved else 'DENIED'}")
    return {"success": True, "approved": approved}


@router.get("/approvals")
async def list_approvals():
    """List all approval requests (pending and resolved) for the audit trail.
    
    This powers the Audit Trail panel in the frontend, showing every
    action the AI has proposed, whether it was approved/denied, and when.
    Critical for transparency and accountability.
    """
    from backend.tools.propose_action import _load_approval_log
    return {"approvals": _load_approval_log()}


@router.get("/approvals/pending")
async def list_pending_approvals():
    """List only pending (unresolved) approval requests.
    
    Used by the frontend to show a badge count of pending approvals
    and to render approval cards in the chat stream.
    """
    from backend.tools.propose_action import get_pending_requests
    return {"pending": get_pending_requests()}


# ── Dependency Lifecycle Manager ──────────────────────────────────────
# Tracks packages the AI installs, detects unused ones, and allows
# users to pin packages they want to keep regardless of usage.

@router.get("/dependencies")
async def list_dependencies():
    """List all tracked dependencies with install date, usage, and status.
    
    Each dependency includes:
    - name: package name
    - installed_at: when it was installed
    - last_used: last time it was imported/used
    - status: ACTIVE, IDLE, or PINNED
    """
    try:
        from backend.tools.dependency_manager import get_all_dependencies
        deps = get_all_dependencies()
        return {"dependencies": deps, "count": len(deps)}
    except Exception as e:
        return {"dependencies": [], "count": 0, "error": str(e)}


@router.get("/dependencies/idle")
async def list_idle_dependencies():
    """List dependencies that haven't been used in >7 days.
    
    These are candidates for removal to keep the environment clean.
    Users can review and either remove or pin them.
    """
    try:
        from backend.tools.dependency_manager import get_idle_dependencies
        idle = get_idle_dependencies()
        return {"idle": idle, "count": len(idle)}
    except Exception as e:
        return {"idle": [], "count": 0, "error": str(e)}


@router.post("/dependencies/{package}/pin")
async def pin_dependency_api(package: str):
    """Pin a dependency so it is never suggested for removal.
    
    Pinned packages are marked as 'PINNED' and excluded from
    idle detection. Use this for packages the user knows they need
    but may not use frequently (e.g., testing libraries).
    """
    try:
        from backend.tools.dependency_manager import pin_dependency
        success = pin_dependency(package)
        if not success:
            return {"success": False, "error": f"Package '{package}' not found"}
        logger.info(f"Pinned dependency: {package}")
        return {"success": True, "package": package, "status": "PINNED"}
    except Exception as e:
        return {"success": False, "error": str(e)}
