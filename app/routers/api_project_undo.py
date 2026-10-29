"""Router: /api/project-library/{project_id}/undo-stack（issue #251：undo 栈刷新即失忆）。

画布级 undo/redo 栈（static/js/canvas-undo.js）整栈持久化进工程 ops.db 的
undo_snapshots 租户（ADR 0021 租户之二）。GET 供页面加载恢复，PUT 为前端
防抖自动保存。与 #257 versions 端点是不同概念（会话级 undo 连续性 vs 版本树）。

租户隔离：每项目独立 ops.db 文件 + project_id 过滤（ADR 0021）；
无 ops.db = 空状态（ADR 0021 失败模型）。
"""
from __future__ import annotations
import os as _os
import sys as _sys

import asyncio
from typing import Any

from pydantic import BaseModel

_app_main = _sys.modules.get(_os.environ.get('FIREFLY_MAIN_MODULE', '')) or _sys.modules.get('main') or _sys.modules['__main__']

from fastapi import APIRouter, HTTPException

router = APIRouter()


class UndoStackPayload(BaseModel):
    # Any 而非 list：形状校验集中在 store（_sanitize_entries → ValueError → 400），
    # 与版本/用量租户「单一校验权威」口径一致（pydantic 提前拦截会变成 422）。
    undo: Any = []
    redo: Any = []


@router.get('/api/project-library/{project_id}/undo-stack')
async def get_project_undo_stack(project_id: str):
    """undo/redo 栈（页面加载恢复）。无 ops.db = 空栈。"""
    try:
        result = await asyncio.to_thread(
            _app_main.project_store_call,
            'load_undo_stack',
            project_id,
        )
    except KeyError:
        raise HTTPException(status_code=404, detail='项目不存在')
    return result


@router.put('/api/project-library/{project_id}/undo-stack')
async def put_project_undo_stack(project_id: str, payload: UndoStackPayload):
    """整栈 upsert（前端防抖自动保存）。超限裁剪到 40 条（UNDO_LIMIT 同口径）。"""
    try:
        result = await asyncio.to_thread(
            _app_main.project_store_call,
            'save_undo_stack',
            project_id,
            payload.undo,
            payload.redo,
        )
    except KeyError:
        raise HTTPException(status_code=404, detail='项目不存在')
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    if result is None:
        raise HTTPException(status_code=500, detail='undo 栈持久化失败')
    return result
