"""Router: /api/global-settings domain (auto-split from main.py)."""
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
_from_main_names = ['FIREFLY_PROJECT_STORE', 'GLOBAL_SETTINGS_FILE', 'load_global_preferences', 'manager', 'photoshop_connection_status', 'project_library_status', 'public_api_providers', 'save_global_preferences', 'switch_project_library', 'update_env_values']
import app.services as _svc
for _nm in _from_main_names:
    _v = getattr(_app_main, _nm, None)
    if _v is None:
        _v = getattr(_svc, _nm, None)
    globals()[_nm] = _v

router = APIRouter()

def load_global_preferences():
    defaults = {'theme': 'dark', 'language': 'zh', 'autosave_interval_minutes': 20}
    try:
        with open(_app_main.GLOBAL_SETTINGS_FILE, 'r', encoding='utf-8-sig') as handle:
            raw = json.load(handle)
    except (OSError, ValueError, json.JSONDecodeError):
        return defaults
    theme = raw.get('theme') if isinstance(raw, dict) else None
    language = raw.get('language') if isinstance(raw, dict) else None
    autosave_interval = raw.get('autosave_interval_minutes') if isinstance(raw, dict) else None
    try:
        autosave_interval = int(autosave_interval)
    except (TypeError, ValueError):
        autosave_interval = defaults['autosave_interval_minutes']
    return {'theme': theme if theme in {'light', 'dark'} else defaults['theme'], 'language': language if language in {'zh', 'en'} else defaults['language'], 'autosave_interval_minutes': autosave_interval if 1 <= autosave_interval <= 240 else defaults['autosave_interval_minutes']}

def save_global_preferences(theme: str, language: str, autosave_interval_minutes: Optional[int]=None):
    current = load_global_preferences()
    interval = current['autosave_interval_minutes'] if autosave_interval_minutes is None else int(autosave_interval_minutes)
    preferences = {'theme': theme if theme in {'light', 'dark'} else 'dark', 'language': language if language in {'zh', 'en'} else 'zh', 'autosave_interval_minutes': interval if 1 <= interval <= 240 else 20}
    os.makedirs(os.path.dirname(_app_main.GLOBAL_SETTINGS_FILE), exist_ok=True)
    temporary = f'{_app_main.GLOBAL_SETTINGS_FILE}.{uuid.uuid4().hex}.tmp'
    try:
        with open(temporary, 'w', encoding='utf-8', newline='\n') as handle:
            json.dump(preferences, handle, ensure_ascii=False, indent=2)
            handle.write('\n')
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, _app_main.GLOBAL_SETTINGS_FILE)
    finally:
        try:
            os.remove(temporary)
        except FileNotFoundError:
            pass
    return preferences

class GlobalPreferencesPayload(BaseModel):
    theme: str = 'dark'
    language: str = 'zh'
    autosave_interval_minutes: Optional[int] = Field(default=None, ge=1, le=240)

class ProjectLibraryChangeRequest(BaseModel):
    path: str = Field(min_length=1, max_length=1024)
    move_existing: bool = False

def photoshop_connection_status():
    connected_clients = [client_id for client_id in manager.connection_clients.values() if str(client_id or '').lower().startswith('ps_')]
    connected = bool(connected_clients)
    return {'connected': connected, 'connection_count': len(connected_clients), 'message': 'Photoshop 连接器已连接' if connected else 'Photoshop 连接器未连接'}

def switch_project_library(path: str, move_existing: bool):
    global FIREFLY_PROJECT_STORE, PROJECT_LIBRARY_MIGRATION_ACTIVE
    with PROJECT_LIBRARY_SWITCH_LOCK:
        PROJECT_LIBRARY_MIGRATION_ACTIVE = True
        old_store = _app_main.FIREFLY_PROJECT_STORE
        new_store = None
        migration = None
        completed = False
        try:
            with PROJECT_MEDIA_IMPORT_TASKS_LOCK:
                active_imports = [future for future in PROJECT_MEDIA_IMPORT_FUTURES.values() if not future.done()]
            if active_imports:
                raise ProjectStoreError('仍有项目媒体正在导入，请等待完成后再移动项目库')
            projects = old_store.list_projects()
            if projects and (not move_existing):
                raise ProjectStoreError('当前项目库不为空，必须显式选择移动已有项目')
            target = Path(path).expanduser()
            if move_existing:
                migration = old_store.copy_library_to(target)
            new_store = ProjectStore(target, snapshot_delay=old_store.snapshot_delay)
            project_count = len(new_store.list_projects())
            if move_existing and project_count != len(projects):
                raise ProjectStoreError('新项目库项目数量校验失败')
            _app_main.update_env_values({'FIREFLY_PROJECTS_ROOT': str(new_store.root)})
            _app_main.FIREFLY_PROJECT_STORE = new_store
            new_store = None
            completed = True
            try:
                old_store.close()
            except Exception as exc:
                print(f'关闭原项目库失败（新项目库已安全切换）: {exc}')
            return {'project_library': {'path': str(_app_main.FIREFLY_PROJECT_STORE.root), 'project_count': project_count}, 'migration': migration}
        finally:
            if new_store is not None:
                try:
                    new_store.close()
                except Exception as exc:
                    print(f'关闭候选项目库失败: {exc}')
            if not completed and migration:
                shutil.rmtree(migration['target'], ignore_errors=True)
                if migration.get('target_preexisted'):
                    os.makedirs(migration['target'], exist_ok=True)
            PROJECT_LIBRARY_MIGRATION_ACTIVE = False

def project_library_status():
    with PROJECT_LIBRARY_SWITCH_LOCK:
        store = _app_main.FIREFLY_PROJECT_STORE
        projects = store.list_projects()
        return {'path': str(store.root), 'project_count': len(projects), 'migration_active': PROJECT_LIBRARY_MIGRATION_ACTIVE}

@router.get('/api/global-settings')
async def global_settings():
    statuses = await asyncio.gather(_app_main.codex_status(), _app_main.gemini_cli_status(), _app_main.jimeng_status(), return_exceptions=True)

    def safe_cli_status(label, value):
        if isinstance(value, BaseException):
            return {'installed': False, 'logged_in': False, 'message': f'{label} 状态读取失败：{value}'}
        if isinstance(value, dict):
            return value
        return {'installed': False, 'logged_in': False, 'message': f'{label} 状态不可用'}
    codex = safe_cli_status('Codex', statuses[0])
    gemini = safe_cli_status('Antigravity', statuses[1])
    jimeng = safe_cli_status('即梦', statuses[2])
    library = await asyncio.to_thread(project_library_status)
    return {'providers': public_api_providers(), 'cli': {'codex': codex, 'gemini': gemini, 'jimeng': jimeng}, 'project_library': library, 'preferences': load_global_preferences(), 'photoshop': photoshop_connection_status()}

@router.patch('/api/global-settings/preferences')
async def update_global_preferences(payload: GlobalPreferencesPayload):
    if payload.theme not in {'light', 'dark'} or payload.language not in {'zh', 'en'}:
        raise HTTPException(status_code=400, detail='主题或语言无效')
    return {'preferences': await asyncio.to_thread(save_global_preferences, payload.theme, payload.language, payload.autosave_interval_minutes)}

@router.get('/api/global-settings/preferences')
async def get_global_preferences():
    return {'preferences': load_global_preferences()}

@router.post('/api/global-settings/project-library')
async def update_project_library(payload: ProjectLibraryChangeRequest):
    try:
        return await asyncio.to_thread(switch_project_library, payload.path, payload.move_existing)
    except ProjectStoreError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except OSError as exc:
        raise HTTPException(status_code=500, detail=f'项目库切换失败: {exc}') from exc