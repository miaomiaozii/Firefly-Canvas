"""Router: /api/conversations domain (auto-split from main.py)."""
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
from app.core import (BaseModel, ConversationCreateRequest, Header, Request, _os, _sys, json, os, safe_user_id)
_from_main_names = ['conversation_path', 'list_conversations', 'load_conversation', 'new_conversation', 'user_dir']
import app.services as _svc
for _nm in _from_main_names:
    _v = getattr(_app_main, _nm, None)
    if _v is None:
        _v = getattr(_svc, _nm, None)
    globals()[_nm] = _v

router = APIRouter()

class ConversationCreateRequest(BaseModel):
    title: str = '新对话'

def list_conversations(user_id):
    records = []
    for filename in os.listdir(user_dir(user_id)):
        if not filename.endswith('.json'):
            continue
        path = os.path.join(user_dir(user_id), filename)
        try:
            with open(path, 'r', encoding='utf-8') as f:
                data = json.load(f)
        except Exception:
            continue
        messages = data.get('messages', [])
        last_message = next((m for m in reversed(messages) if m.get('role') != 'system'), None)
        records.append({'id': data.get('id'), 'title': data.get('title', '新对话'), 'created_at': data.get('created_at', 0), 'updated_at': data.get('updated_at', 0), 'last_message': (last_message or {}).get('content', '')})
    return sorted(records, key=lambda item: item['updated_at'], reverse=True)

@router.get('/api/conversations')
async def conversations(request: Request, x_user_id: str=Header(default='')):
    user_id = safe_user_id(x_user_id, request)
    return {'user_id': user_id, 'conversations': list_conversations(user_id)}

@router.post('/api/conversations')
async def create_conversation(payload: ConversationCreateRequest, request: Request, x_user_id: str=Header(default='')):
    user_id = safe_user_id(x_user_id, request)
    return {'conversation': new_conversation(user_id, payload.title)}

@router.get('/api/conversations/{conversation_id}')
async def get_conversation(conversation_id: str, request: Request, x_user_id: str=Header(default='')):
    user_id = safe_user_id(x_user_id, request)
    return {'conversation': load_conversation(user_id, conversation_id)}

@router.delete('/api/conversations/{conversation_id}')
async def delete_conversation(conversation_id: str, request: Request, x_user_id: str=Header(default='')):
    user_id = safe_user_id(x_user_id, request)
    path = conversation_path(user_id, conversation_id)
    if os.path.exists(path):
        os.remove(path)
    return {'ok': True}