"""Router: /api/projects domain (auto-split from main.py)."""
from __future__ import annotations
import sys as _sys
import os as _os
_app_main = _sys.modules.get(_os.environ.get('FIREFLY_MAIN_MODULE', '')) or _sys.modules.get('main') or _sys.modules['__main__']
import asyncio
from typing import Any, Optional
from fastapi import APIRouter, BackgroundTasks, File, Form, Header, HTTPException, Query, Request, Response, UploadFile, WebSocket, WebSocketDisconnect
from pydantic import BaseModel, Field
from app.core import (BaseModel, HTTPException, Optional, ProjectCreateRequest, ProjectUpdateRequest, Request, _os, _sys, asyncio, ensure_same_origin_request)
_from_main_names = ['project_store_call']
import app.services as _svc
for _nm in _from_main_names:
    _v = getattr(_app_main, _nm, None)
    if _v is None:
        _v = getattr(_svc, _nm, None)
    globals()[_nm] = _v

router = APIRouter()

class ProjectCreateRequest(BaseModel):
    name: str = '新项目'

class ProjectUpdateRequest(BaseModel):
    name: Optional[str] = None
    order: Optional[int] = None

@router.get('/api/projects')
async def get_projects(request: Request):
    ensure_same_origin_request(request)
    return {'projects': await asyncio.to_thread(_app_main.project_store_call, 'list_projects')}

@router.post('/api/projects')
async def create_project(payload: ProjectCreateRequest):
    raise HTTPException(status_code=410, detail='旧项目入口已停用，请使用 /api/project-library')

@router.post('/api/projects/{project_id}')
async def update_project(project_id: str, payload: ProjectUpdateRequest):
    raise HTTPException(status_code=410, detail='旧项目入口已停用，请使用 /api/project-library')

@router.delete('/api/projects/{project_id}')
async def delete_project(project_id: str):
    raise HTTPException(status_code=410, detail='旧项目入口已停用；项目回收站由后续项目生命周期接口提供')