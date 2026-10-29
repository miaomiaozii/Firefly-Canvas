"""Router: /api/project-library/{project_id}/annotations（issue #272：画布批注=可持久画布对象）。

#259 产品决策：批注=画布可持久对象（存 ops.db 新租户 anno_*，additive 迁移
anno_001；可撤销、刷新恢复、随画板走）。批注锚定节点（node_id）+ 节点内
归一化坐标（x/y ∈ [0,1]）；status 状态机 pending→converted（converted 由
runAll 批注→任务转化写入，下一票）。

端点（CRUD，前端 canvas-annotations.js 消费）：
- GET    /api/project-library/{id}/annotations            → {project_id, items}（刷新恢复）
- POST   /api/project-library/{id}/annotations            → 201 创建回执
- PATCH  /api/project-library/{id}/annotations/{anno_id}  → 200 编辑回执
- DELETE /api/project-library/{id}/annotations/{anno_id}  → 200 删除回执

租户隔离：每项目独立 ops.db 文件 + project_id 过滤（ADR 0021）；
无 ops.db = 空状态（GET 返回空列表）。非存在项目 404；非存在批注 404；
字段非法（空文本/空 node_id/越界 x,y/非法 status/超长文本）400——
形状校验集中在 store（ValueError → 400），与 versions/undo/usage 租户
「单一校验权威」口径一致（pydantic 提前拦截会变成 422，故 body 用 Any）。
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


class AnnotationCreatePayload(BaseModel):
    node_id: Any = None
    text: Any = None
    x: Any = 0.5
    y: Any = 0.2
    status: Any = 'pending'


class AnnotationUpdatePayload(BaseModel):
    text: Any = None
    x: Any = None
    y: Any = None
    status: Any = None


@router.get('/api/project-library/{project_id}/annotations')
async def list_project_annotations(project_id: str):
    """批注列表（创建序）。无 ops.db = 空列表（刷新恢复入口）。"""
    try:
        items = await asyncio.to_thread(
            _app_main.project_store_call,
            'list_project_annotations',
            project_id,
        )
    except KeyError:
        raise HTTPException(status_code=404, detail='项目不存在')
    return {'project_id': project_id, 'items': items}


@router.post('/api/project-library/{project_id}/annotations', status_code=201)
async def create_project_annotation(project_id: str, payload: AnnotationCreatePayload):
    """新建批注（默认 pending）。字段非法 400；非存在项目 404。"""
    try:
        created = await asyncio.to_thread(
            _app_main.project_store_call,
            'create_project_annotation',
            project_id,
            node_id=payload.node_id,
            text=payload.text,
            x=payload.x,
            y=payload.y,
            status=payload.status,
        )
    except KeyError:
        raise HTTPException(status_code=404, detail='项目不存在')
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return created


@router.patch('/api/project-library/{project_id}/annotations/{anno_id}')
async def update_project_annotation(project_id: str, anno_id: int, payload: AnnotationUpdatePayload):
    """编辑批注（增量：只改传入字段）。字段非法 400；非存在批注 404。"""
    try:
        updated = await asyncio.to_thread(
            _app_main.project_store_call,
            'update_project_annotation',
            project_id,
            anno_id,
            text=payload.text,
            x=payload.x,
            y=payload.y,
            status=payload.status,
        )
    except KeyError:
        raise HTTPException(status_code=404, detail='项目或批注不存在')
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return updated


@router.delete('/api/project-library/{project_id}/annotations/{anno_id}')
async def delete_project_annotation(project_id: str, anno_id: int):
    """删除批注。非存在批注 404。"""
    try:
        result = await asyncio.to_thread(
            _app_main.project_store_call,
            'delete_project_annotation',
            project_id,
            anno_id,
        )
    except KeyError:
        raise HTTPException(status_code=404, detail='项目或批注不存在')
    return result
