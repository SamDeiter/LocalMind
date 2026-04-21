from fastapi import APIRouter, Body, HTTPException, Request
from backend.autonomy import GoalPlanner, AutonomyEngine
import uuid

router = APIRouter(prefix="/api/autonomy")
logger = logging.getLogger("localmind.routes.autonomy")

from backend.autonomy.intelligence_monitor import IntelligenceMonitor

# Singletons (Refactored to check app state)
_planner = GoalPlanner()
_monitor = IntelligenceMonitor()

@router.get("/briefing")
async def get_briefing(request: Request):
    """
    Get the proactive daily briefing for the Coworker experience.
    """
    try:
        data = await _monitor.get_proactive_briefing()
        return data
    except Exception as e:
        logger.error(f"Failed to get briefing: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@router.post("/mission/planning")
async def plan_mission(request: Request, body: dict = Body(...)):
    """
    Start an autonomous mission based on a briefing.
    """
    briefing = body.get("briefing")
    if not briefing:
        raise HTTPException(status_code=400, detail="Missing 'briefing' in request body")
    
    context = body.get("context", {})
    mission_id = str(uuid.uuid4())
    
    try:
        # Create the plan
        plan = await _planner.create_plan(briefing, context)
        
        if plan.needs_clarification:
            return {
                "mission_id": mission_id,
                "status": "needs_clarification",
                "question": plan.clarification_question
            }
        
        return {
            "mission_id": mission_id,
            "status": "planned",
            "plan": plan.to_dict()
        }
        
    except Exception as e:
        logger.exception("Failed to start mission planning")
        raise HTTPException(status_code=500, detail=str(e))

@router.post("/mission/{mission_id}/execute")
@router.post("/mission/active/execute")
async def engage_mission(request: Request, mission_id: Optional[str] = "active", body: dict = Body(...)):
    """
    Formally engage the mission plan.
    """
    # Engages the AutonomyEngine on app.state
    engine = getattr(request.app.state, "autonomy_engine", None)
    if not engine:
         raise HTTPException(status_code=503, detail="Autonomy Engine not initialized")

    plan_data = body.get("plan")
    if not plan_data:
         raise HTTPException(status_code=400, detail="No plan provided for execution")

    # In a real impl, we'd convert plan_data back to objects or handle the dict
    # For Phase F, we'll trigger the engine's execution loop in a background task
    import asyncio
    
    # We pass the full body (which contains the plan) to the engine
    asyncio.create_task(engine.execute_mission(body))

    return {"status": "engaged", "mission_id": mission_id}
