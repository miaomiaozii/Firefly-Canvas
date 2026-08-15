"""Router: /ws/stats domain (auto-split from main.py)."""
from __future__ import annotations
import sys as _sys
import os as _os
_app_main = _sys.modules.get(_os.environ.get('FIREFLY_MAIN_MODULE', '')) or _sys.modules.get('main') or _sys.modules['__main__']
import json
from fastapi import APIRouter, BackgroundTasks, File, Form, Header, HTTPException, Query, Request, Response, UploadFile, WebSocket, WebSocketDisconnect
from app.core import (HTTPException, WebSocket, WebSocketDisconnect, _ensure_photoshop_loopback, _os, _sys, json)
from app.core import _ensure_photoshop_loopback
from app.project_activity import ProjectCleanupActive
from app.project_sessions import register_project_session, unregister_project_session
_from_main_names = ['manager']
import app.services as _svc
for _nm in _from_main_names:
    _v = getattr(_app_main, _nm, None)
    if _v is None:
        _v = getattr(_svc, _nm, None)
    globals()[_nm] = _v

router = APIRouter()

@router.websocket('/ws/stats')
async def websocket_endpoint(
    websocket: WebSocket,
    client_id: str = None,
    project_id: str = None,
):
    if str(client_id or '').lower().startswith('ps_'):
        try:
            _ensure_photoshop_loopback(websocket)
        except HTTPException as exc:
            await websocket.close(code=1008, reason=str(exc.detail))
            return
    await manager.connect(websocket, client_id)
    try:
        if str(client_id or '').lower().startswith('canvas_') and project_id:
            register_project_session(websocket, project_id)
        while True:
            data = await websocket.receive_text()
            if data == 'ping':
                await websocket.send_text(json.dumps({'type': 'pong'}))
    except ProjectCleanupActive as exc:
        await websocket.close(code=1013, reason=str(exc))
    except WebSocketDisconnect:
        pass
    except Exception as e:
        print(f'WS Error: {e}')
    finally:
        unregister_project_session(websocket)
        await manager.disconnect(websocket, client_id)