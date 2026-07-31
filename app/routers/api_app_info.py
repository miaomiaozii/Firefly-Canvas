"""Router: /api/app-info domain (auto-split from main.py)."""
from __future__ import annotations
import sys as _sys
import os as _os
_app_main = _sys.modules.get(_os.environ.get('FIREFLY_MAIN_MODULE', 'main'), None) or _sys.modules['main']
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
router = APIRouter()

def current_app_version():
    version_file = os.path.join(BASE_DIR, 'VERSION')
    try:
        if os.path.exists(version_file):
            with open(version_file, 'r', encoding='utf-8') as f:
                version = (f.read().strip().splitlines() or [''])[0].strip()
                if version:
                    return version
    except Exception:
        pass
    try:
        return time.strftime('%Y.%m.%d', time.localtime())
    except Exception:
        return ''

@router.get('/api/app-info')
def app_info():
    version = current_app_version()
    return {'name': '萤火画布', 'version': version, 'repo_url': FIREFLY_REPO_URL, 'repository': {'label': '萤火画布', 'url': FIREFLY_REPO_URL}}