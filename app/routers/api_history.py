"""Router: /api/history domain (auto-split from main.py)."""
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
_from_main_names = ['HISTORY_FILE', 'output_file_from_url']
for _nm in _from_main_names:
    globals()[_nm] = getattr(_app_main, _nm)
router = APIRouter()

class DeleteHistoryRequest(BaseModel):
    timestamp: float

@router.get('/api/history')
async def get_history_api(type: str=None):
    if os.path.exists(_app_main.HISTORY_FILE):
        try:
            with open(_app_main.HISTORY_FILE, 'r', encoding='utf-8') as f:
                data = json.load(f)
                if type:
                    data = [item for item in data if item.get('type', 'zimage') == type]
                data = [item for item in data if item.get('images') and len(item['images']) > 0]

                def sort_key(item):
                    ts = item.get('timestamp', 0)
                    if isinstance(ts, (int, float)):
                        return float(ts)
                    return 0
                data.sort(key=sort_key, reverse=True)
                return data
        except Exception as e:
            print(f'读取历史文件失败: {e}')
            return []
    return []

@router.post('/api/history/delete')
async def delete_history(req: DeleteHistoryRequest):
    if not os.path.exists(_app_main.HISTORY_FILE):
        return {'success': False, 'message': 'History file not found'}
    try:
        with HISTORY_LOCK:
            with open(_app_main.HISTORY_FILE, 'r', encoding='utf-8') as f:
                history = json.load(f)
            target_record = None
            new_history = []
            for item in history:
                is_match = False
                item_ts = item.get('timestamp', 0)
                if isinstance(req.timestamp, (int, float)) and isinstance(item_ts, (int, float)):
                    if abs(float(item_ts) - float(req.timestamp)) < 0.001:
                        is_match = True
                elif str(item_ts) == str(req.timestamp):
                    is_match = True
                if is_match:
                    target_record = item
                else:
                    new_history.append(item)
            if target_record:
                with open(_app_main.HISTORY_FILE, 'w', encoding='utf-8') as f:
                    json.dump(new_history, f, ensure_ascii=False, indent=4)
        if target_record:
            for img_url in target_record.get('images', []):
                file_path = _app_main.output_file_from_url(img_url)
                if file_path and os.path.exists(file_path):
                    try:
                        os.remove(file_path)
                    except Exception as e:
                        print(f'Failed to delete file {file_path}: {e}')
            return {'success': True}
        else:
            return {'success': False, 'message': 'Record not found'}
    except Exception as e:
        print(f'Delete history error: {e}')
        return {'success': False, 'message': str(e)}