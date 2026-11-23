"""Router: /ws/stats domain (auto-split from main.py)."""
from __future__ import annotations
import sys as _sys
import os as _os
import logging
import asyncio
import anyio

logger = logging.getLogger(__name__)
_app_main = _sys.modules.get(_os.environ.get('FIREFLY_MAIN_MODULE', '')) or _sys.modules.get('main') or _sys.modules['__main__']
import json
from fastapi import APIRouter, BackgroundTasks, File, Form, Header, HTTPException, Query, Request, Response, UploadFile, WebSocket, WebSocketDisconnect
from app.core import (HTTPException, WebSocket, WebSocketDisconnect, _ensure_photoshop_loopback, ensure_agent_same_origin, _os, _sys, json)
from app.core import _ensure_photoshop_loopback
from app.project_activity import ProjectCleanupActive
from app.services import canvas_viewport
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
    # WebSocket upgrades bypass the HTTP origin middleware; guard before accept.
    ensure_agent_same_origin(websocket)
    if str(client_id or '').lower().startswith('ps_'):
        try:
            _ensure_photoshop_loopback(websocket)
        except HTTPException as exc:
            await websocket.close(code=1008, reason=str(exc.detail))
            return
    await manager.connect(websocket, client_id)
    watcher = asyncio.create_task(canvas_viewport.watch(websocket, lambda: _app_main.FIREFLY_PROJECT_STORE))
    try:
        if str(client_id or '').lower().startswith('canvas_') and project_id:
            register_project_session(websocket, project_id)
        while True:
            frame = await websocket.receive()
            if frame['type'] == 'websocket.disconnect':
                break
            data = frame.get('text')
            if not isinstance(data, str):
                continue
            if data == 'ping':
                await websocket.send_text(json.dumps({'type': 'pong'}))
            else:
                await canvas_viewport.receive(websocket, data, lambda: _app_main.FIREFLY_PROJECT_STORE)
    except ProjectCleanupActive as exc:
        await websocket.close(code=1013, reason=str(exc))
    except WebSocketDisconnect:
        pass
    except Exception as e:
        logger.warning(f'WS Error: {e}')
    finally:
        # Release authority before the first cancellable teardown await.
        canvas_viewport.disconnect(websocket)
        unregister_project_session(websocket)
        watcher.cancel()
        with anyio.CancelScope(shield=True):
            await asyncio.gather(watcher, return_exceptions=True)
            await manager.disconnect(websocket, client_id)