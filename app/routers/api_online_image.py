"""Router: /api/online-image domain (auto-split from main.py)."""
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
_from_main_names = ['build_online_image_result']
for _nm in _from_main_names:
    globals()[_nm] = getattr(_app_main, _nm)
router = APIRouter()

@router.post('/api/online-image')
async def online_image(payload: OnlineImageRequest):
    return await build_online_image_result(payload)