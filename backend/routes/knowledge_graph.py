"""
routes/knowledge_graph.py — Brain Knowledge Graph API
=====================================================
Returns memory data as a node/edge graph for the D3.js visualization.
Nodes = memories, grouped by category/subcategory.
Edges = shared subcategory (memories in the same cluster).
"""

import logging
from collections import defaultdict

from fastapi import APIRouter

logger = logging.getLogger("localmind.routes.knowledge_graph")

router = APIRouter(prefix="/api", tags=["knowledge_graph"])


@router.get("/memories/graph")
async def get_memory_graph():
    """Return memories as a force-directed graph structure.

    Returns:
        {
            nodes: [{id, label, category, subcategory, group}],
            edges: [{source, target}],
            stats: {total, by_category: {cat: count}}
        }
    """
    try:
        from backend.tools.memory import _get_fts_store
        store = _get_fts_store()
        if not store:
            return {"nodes": [], "edges": [], "stats": {"total": 0, "by_category": {}}}

        conn = store._get_conn()
        rows = conn.execute(
            "SELECT id, content, category, subcategory, created_at, access_count "
            "FROM memories ORDER BY created_at DESC LIMIT 200"
        ).fetchall()

        if not rows:
            return {"nodes": [], "edges": [], "stats": {"total": 0, "by_category": {}}}

        # Build nodes
        nodes = []
        by_subcategory = defaultdict(list)
        by_category = defaultdict(int)

        for row in rows:
            content = row["content"] or ""
            label = content[:60] + ("..." if len(content) > 60 else "")
            cat = row["category"] or "unknown"
            subcat = row["subcategory"] or cat
            node_id = str(row["id"])

            nodes.append({
                "id": node_id,
                "label": label,
                "category": cat,
                "subcategory": subcat,
                "group": subcat,
                "access_count": row["access_count"] or 0,
            })
            by_subcategory[subcat].append(node_id)
            by_category[cat] += 1

        # Build edges: connect memories within the same subcategory cluster
        edges = []
        seen = set()
        for subcat, ids in by_subcategory.items():
            if len(ids) < 2:
                continue
            # Star topology: connect each node to the first node in the cluster
            hub = ids[0]
            for spoke in ids[1:]:
                key = (hub, spoke)
                if key not in seen:
                    edges.append({"source": hub, "target": spoke})
                    seen.add(key)

        return {
            "nodes": nodes,
            "edges": edges,
            "stats": {
                "total": len(nodes),
                "by_category": dict(by_category),
            },
        }

    except Exception as e:
        logger.warning(f"Knowledge graph generation failed: {e}")
        return {"nodes": [], "edges": [], "stats": {"total": 0, "by_category": {}}, "error": str(e)}
