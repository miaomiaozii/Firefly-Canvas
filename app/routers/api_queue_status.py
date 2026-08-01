"""Router: /api/queue_status domain (auto-split from main.py)."""
from __future__ import annotations
import sys as _sys
import os as _os
_app_main = _sys.modules.get(_os.environ.get('FIREFLY_MAIN_MODULE', '')) or _sys.modules.get('main') or _sys.modules['__main__']
from fastapi import APIRouter, BackgroundTasks, File, Form, Header, HTTPException, Query, Request, Response, UploadFile, WebSocket, WebSocketDisconnect
from app.core import (QUEUE, QUEUE_LOCK, _os, _sys)
router = APIRouter()

@router.get('/api/queue_status')
async def get_queue_status(client_id: str):
    with QUEUE_LOCK:
        total = len(QUEUE)
        positions = [i + 1 for i, t in enumerate(QUEUE) if t['client_id'] == client_id]
        position = positions[0] if positions else 0
    return {'total': total, 'position': position}