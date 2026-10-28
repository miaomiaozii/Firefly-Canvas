"""Router: /api/project-library/{project_id}/usage（issue #258；LLM 用量/成本遥测聚合查询）。

租户隔离：每项目独立 ops.db 文件 + project_id 过滤（ADR 0021）；
无 ops.db = 空状态（ADR 0021 失败模型）。展示层归 issue #262，本端点只提供聚合数据。
"""
from __future__ import annotations
import os as _os
import sys as _sys

import asyncio

_app_main = _sys.modules.get(_os.environ.get('FIREFLY_MAIN_MODULE', '')) or _sys.modules.get('main') or _sys.modules['__main__']

from fastapi import APIRouter, HTTPException

router = APIRouter()


@router.get('/api/project-library/{project_id}/usage')
async def get_project_usage_summary(project_id: str, days: int = 0):
    """LLM 用量聚合：total + by_day + by_model（每 turn 一行汇总）。days>0 时只统计最近 N 天。"""
    try:
        summary = await asyncio.to_thread(
            _app_main.project_store_call,
            'list_usage_summary',
            project_id,
            days if days > 0 else None,
        )
    except KeyError:
        raise HTTPException(status_code=404, detail='项目不存在')
    return summary
