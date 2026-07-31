"""Router: /api/projects domain (auto-split from main.py)."""
from __future__ import annotations
import sys as _sys
import os as _os
_app_main = _sys.modules.get(_os.environ.get('FIREFLY_MAIN_MODULE', '')) or _sys.modules.get('main') or _sys.modules['__main__']
import asyncio
import os
import json
import re
import time
import base64
import shutil
import uuid
import hashlib
import mimetypes
from datetime import datetime
from typing import Any, Optional
from fastapi import APIRouter, BackgroundTasks, File, Form, Header, HTTPException, Query, Request, Response, UploadFile, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse, StreamingResponse
from pydantic import BaseModel, Field
from app.core import *
_from_main_names = ['project_store_call']
for _nm in _from_main_names:
    globals()[_nm] = getattr(_app_main, _nm)
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