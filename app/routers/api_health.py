"""Router: /api/healthz runtime projection (read-only, no persistence).

Wayfinder fog-of-war "scout wiring": expose a small set of live runtime
metrics on the health endpoint — open canvas project sessions and agent
subprocess state — reported faithfully from the already-wired registries.

Boundary (fog-of-war map conclusion): no metrics subsystem, no ops.db
writes, no metrics persistence layer, no network egress. Pure in-memory
read-only projection.
"""
from __future__ import annotations

from fastapi import APIRouter

from app.project_sessions import open_project_ids
from app.services.agent_host import AGENT_STATUSES, agent_host_manager

router = APIRouter()


@router.get('/api/healthz')
async def healthz():
    try:
        agents = await agent_host_manager.snapshot_sessions()
    except Exception:
        agents = []
    by_status = {status: 0 for status in AGENT_STATUSES}
    for agent in agents:
        by_status[agent["status"]] = by_status.get(agent["status"], 0) + 1
    open_ids = open_project_ids()
    return {
        'status': 'ok',
        'open_project_count': len(open_ids),
        'open_project_ids': open_ids,
        'agents': {
            'total': len(agents),
            'by_status': by_status,
            'sessions': agents,
        },
    }
