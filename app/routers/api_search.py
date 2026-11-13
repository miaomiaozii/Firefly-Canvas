"""Router: /api/search 跨项目搜索（fullnode 票 F / #324, ADR-0026）。

只读直扫 canvas.json（firefly_search.CrossProjectSearch），不触碰项目锁、
不写任何持久态。项目库切换会替换 store 实例——按 (store 身份, root) 重建。
"""
from __future__ import annotations
import sys as _sys
import os as _os
_app_main = _sys.modules.get(_os.environ.get('FIREFLY_MAIN_MODULE', '')) or _sys.modules.get('main') or _sys.modules['__main__']
import asyncio
from pathlib import Path

from fastapi import APIRouter, HTTPException, Query, Request

from app.core import ensure_same_origin_request
from firefly_search import MAX_RESULTS, CrossProjectSearch

router = APIRouter()

_search_state: dict = {}


def _searcher() -> CrossProjectSearch:
    store = getattr(_app_main, "FIREFLY_PROJECT_STORE", None)
    if store is None:
        raise HTTPException(status_code=503, detail="project store unavailable")
    root = Path(store.root)
    key = (id(store), str(root))
    if _search_state.get("key") != key:
        _search_state.clear()
        _search_state["key"] = key
        _search_state["inst"] = CrossProjectSearch(root)
    return _search_state["inst"]


@router.get('/api/search/projects')
async def search_projects(
    request: Request,
    q: str = Query(..., min_length=1, max_length=200),
    limit: int = Query(50, ge=1, le=MAX_RESULTS),
):
    ensure_same_origin_request(request)
    return await asyncio.to_thread(_searcher().search, q, limit=limit)
