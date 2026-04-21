"""
backend/routes/intelligence.py --- Unified Intelligence Graph API
===============================================================
Merges Memories (Nodes), Capabilities (Tools), and Learning Gaps (Amber).
"""

import logging
from collections import defaultdict
from fastapi import APIRouter
from backend.config import PROJECT_ROOT
from backend.security.integrity import minter

logger = logging.getLogger("localmind.routes.intelligence")
router = APIRouter(prefix="/api/intelligence", tags=["intelligence"])

@router.get("/map")
async def get_intelligence_map():
    """
    Returns a unified graph of EVERYTHING the AI knows.
    Nodes: Tools (Green), Memories (Blue), Gaps (Amber).
    """
    nodes = []
    edges = []
    
    # 1. ADD MEMORIES (Blue Nodes)
    try:
        from backend.tools.memory import _get_fts_store
        store = _get_fts_store()
        if store:
            conn = store._get_conn()
            mem_rows = conn.execute(
                "SELECT id, content, category, subcategory FROM memories LIMIT 100"
            ).fetchall()
            for row in mem_rows:
                content = row["content"] or ""
                nodes.append({
                    "id": f"mem_{row['id']}",
                    "label": content[:40] + "...",
                    "type": "memory",
                    "color": "#3B82F6", # Bright Blue
                    "group": row["subcategory"] or row["category"]
                })
    except Exception as e:
        logger.warning(f"Failed to fetch memories for map: {e}")

    # 2. ADD TOOLS (Green / Red Nodes)
    try:
        tools_dir = PROJECT_ROOT / "backend" / "tools"
        generated_dir = tools_dir / "generated"
        
        # Scan standard tools
        for py_file in tools_dir.glob("*.py"):
            if py_file.name.startswith("_") or py_file.name == "base.py": continue
            nodes.append({
                "id": f"tool_{py_file.stem}",
                "label": f"Tool: {py_file.stem}",
                "type": "tool",
                "color": "#10B981", # Emerald Green
                "verified": True
            })

        # Scan generated tools + check signatures
        if generated_dir.exists():
            for py_file in generated_dir.glob("*.py"):
                if py_file.name.startswith("_"): continue
                
                tool_name = py_file.stem
                sig_file = py_file.with_suffix(".sig")
                is_verified = False
                
                if sig_file.exists():
                    token = sig_file.read_text(encoding="utf-8")
                    code = py_file.read_text(encoding="utf-8")
                    is_verified = minter.verify_tool(tool_name, code, token)
                
                nodes.append({
                    "id": f"tool_gen_{tool_name}",
                    "label": f"Skill: {tool_name}",
                    "type": "skill",
                    "color": "#10B981" if is_verified else "#EF4444", # Green if OK, Red if bad
                    "verified": is_verified
                })
    except Exception as e:
        logger.warning(f"Failed to fetch tools for map: {e}")

    # 3. ADD LEARNING GAPS (Amber Nodes)
    try:
        from backend.autonomy.skill_learner import get_skill_learner
        learner = get_skill_learner()
        suggestions = await learner.suggest_topics()
        for i, s in enumerate(suggestions):
            nodes.append({
                "id": f"gap_{i}",
                "label": f"GAP: {s['topic']}",
                "type": "gap",
                "color": "#F59E0B", # Amber
                "reasoning": s.get("reasoning", "")
            })
            # Connect gap to at least one tool to show it's 'next to learn'
            if nodes:
                edges.append({"source": f"gap_{i}", "target": nodes[0]["id"]})
    except Exception as e:
        logger.warning(f"Failed to fetch gaps for map: {e}")

    return {
        "nodes": nodes,
        "edges": edges,
        "summary": {
            "memories": sum(1 for n in nodes if n["type"] == "memory"),
            "tools": sum(1 for n in nodes if n["type"] in ["tool", "skill"]),
            "gaps": sum(1 for n in nodes if n["type"] == "gap"),
            "unverified": sum(1 for n in nodes if n.get("verified") == False)
        }
    }
