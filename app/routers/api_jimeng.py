"""Router: /api/jimeng domain (auto-split from main.py)."""
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
_from_main_names = ['jimeng_cli_executable', 'jimeng_cli_version', 'jimeng_command', 'jimeng_login_reader', 'jimeng_query_result', 'jimeng_store_outputs', 'jimeng_use_wsl', 'run_jimeng_cli']
for _nm in _from_main_names:
    globals()[_nm] = getattr(_app_main, _nm)
router = APIRouter()

class JimengHelpRequest(BaseModel):
    command: str = ''

class JimengQueryMediaRequest(BaseModel):
    submit_id: str = ''
    kind: str = 'image'

def jimeng_login_text():
    parts = []
    for key in ('stdout', 'stderr'):
        value = str(JIMENG_LOGIN_SESSION.get(key) or '').strip()
        if value:
            parts.append(value)
    return '\n'.join(parts).strip()

def jimeng_login_qr_from_text(text):
    text = str(text or '')
    candidates = []
    patterns = ['(https?://[^\\s\\"\'<>]+)', '(dreamina://[^\\s\\"\'<>]+)', '(data:image/[^\\s\\"\'<>]+)']
    for pattern in patterns:
        candidates.extend(re.findall(pattern, text))
    for value in candidates:
        if 'login' in value.lower() or 'qr' in value.lower() or value.startswith(('data:image', 'dreamina://')):
            return value
    return candidates[0] if candidates else ''

async def jimeng_login_reader(proc):

    async def read_stream(stream, key):
        while True:
            chunk = await stream.readline()
            if not chunk:
                break
            text = decode_wsl_output(chunk) if jimeng_use_wsl() else chunk.decode('utf-8', errors='replace')
            if key == 'stderr':
                text = jimeng_clean_wsl_stderr(text)
            if text:
                JIMENG_LOGIN_SESSION[key] = str(JIMENG_LOGIN_SESSION.get(key) or '') + text
    await asyncio.gather(read_stream(proc.stdout, 'stdout'), read_stream(proc.stderr, 'stderr'))

@router.get('/api/jimeng/status')
async def jimeng_status():
    exe = jimeng_cli_executable()
    if not exe:
        return {'installed': False, 'logged_in': False, 'message': '未找到 dreamina CLI'}
    version, version_text = await jimeng_cli_version()
    version_str = '.'.join((str(part) for part in version)) if version else None
    version_ok = version >= JIMENG_MIN_CLI_VERSION if version else None
    min_version_str = '.'.join((str(part) for part in JIMENG_MIN_CLI_VERSION))
    try:
        raw = await run_jimeng_cli(['user_credit'], timeout=30)
        return {'installed': True, 'logged_in': True, 'raw': raw, 'cli_version': version_str, 'version_ok': version_ok, 'min_version': min_version_str}
    except HTTPException as exc:
        return {'installed': True, 'logged_in': False, 'message': str(exc.detail), 'cli_version': version_str, 'version_ok': version_ok, 'min_version': min_version_str}

@router.get('/api/jimeng/credit')
async def jimeng_credit():
    raw = await run_jimeng_cli(['user_credit'], timeout=30)
    return {'success': True, 'raw': raw}

@router.post('/api/jimeng/logout')
async def jimeng_logout():
    raw = await run_jimeng_cli(['logout'], timeout=30)
    return {'success': True, 'raw': raw}

@router.post('/api/jimeng/login/start')
async def jimeng_login_start():
    old_proc = JIMENG_LOGIN_SESSION.get('proc')
    if old_proc and getattr(old_proc, 'returncode', None) is None:
        try:
            old_proc.terminate()
        except Exception:
            pass
    exe = jimeng_cli_executable()
    if not exe:
        raise HTTPException(status_code=400, detail='未找到 dreamina CLI')
    JIMENG_LOGIN_SESSION.update({'proc': None, 'stdout': '', 'stderr': '', 'started_at': time.time()})
    args = ['login', '--headless']
    command = jimeng_command(args, exe)
    try:
        proc = await asyncio.create_subprocess_exec(*command, cwd=BASE_DIR, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=400, detail=f'未找到即梦 CLI：{exe}') from exc
    JIMENG_LOGIN_SESSION['proc'] = proc
    asyncio.create_task(jimeng_login_reader(proc))
    await asyncio.sleep(2)
    text = jimeng_login_text()
    if proc.returncode not in (None, 0) and ('unknown' in text.lower() or 'no such option' in text.lower()):
        JIMENG_LOGIN_SESSION.update({'proc': None, 'stdout': '', 'stderr': '', 'started_at': time.time()})
        proc = await asyncio.create_subprocess_exec(*jimeng_command(['login', '--debug'], exe), cwd=BASE_DIR, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
        JIMENG_LOGIN_SESSION['proc'] = proc
        asyncio.create_task(jimeng_login_reader(proc))
        await asyncio.sleep(2)
        text = jimeng_login_text()
    return {'success': True, 'running': JIMENG_LOGIN_SESSION.get('proc') is not None and JIMENG_LOGIN_SESSION['proc'].returncode is None, 'text': text, 'qr_url': jimeng_login_qr_from_text(text), 'started_at': JIMENG_LOGIN_SESSION.get('started_at') or 0}

@router.get('/api/jimeng/login/status')
async def jimeng_login_status():
    proc = JIMENG_LOGIN_SESSION.get('proc')
    text = jimeng_login_text()
    running = proc is not None and getattr(proc, 'returncode', None) is None
    logged_in = False
    credit_raw = None
    if not running:
        try:
            credit_raw = await run_jimeng_cli(['user_credit'], timeout=20)
            logged_in = True
        except HTTPException:
            logged_in = False
    return {'success': True, 'running': running, 'logged_in': logged_in, 'text': text, 'qr_url': jimeng_login_qr_from_text(text), 'raw': credit_raw}

@router.post('/api/jimeng/help')
async def jimeng_help(payload: JimengHelpRequest):
    command = str(payload.command or '').strip()
    allowed = {'', 'login', 'logout', 'user_credit', 'text2image', 'image2image', 'image_upscale', 'text2video', 'image2video', 'multimodal2video', 'frames2video', 'multiframe2video', 'list_task', 'query_result'}
    if command not in allowed:
        raise HTTPException(status_code=400, detail='不支持的帮助命令')
    args = [command, '-h'] if command else ['-h']
    raw = await run_jimeng_cli(args, timeout=30, raw_text=True)
    text = raw.get('_stdout') or ''
    if raw.get('_stderr'):
        text = f"{text}\n{raw.get('_stderr')}".strip()
    return {'success': True, 'command': command, 'text': text, 'raw': raw}

@router.post('/api/jimeng/query-media')
async def jimeng_query_media(payload: JimengQueryMediaRequest):
    """按 submit_id 续查即梦任务：出图返回 succeeded+urls；仍排队返回 pending+queue_info；失败返回 failed。
    供画布「排队中」卡片自动轮询与手动查询复用。"""
    submit_id = str(payload.submit_id or '').strip()
    if not submit_id:
        raise HTTPException(status_code=400, detail='缺少 submit_id')
    kind = str(payload.kind or 'image').strip().lower()
    if kind not in ('image', 'video', 'audio'):
        kind = 'image'
    queried = await jimeng_query_result(submit_id, kind)
    try:
        urls = await jimeng_store_outputs(queried, kind, allow_query=False)
        return {'status': 'succeeded', 'submit_id': submit_id, 'kind': kind, 'urls': urls}
    except JimengPendingError as exc:
        return {'status': 'pending', 'submit_id': submit_id, 'kind': kind, 'queue_info': exc.queue_info, 'message': jimeng_pending_payload(exc)['message']}
    except HTTPException as exc:
        return {'status': 'failed', 'submit_id': submit_id, 'kind': kind, 'error': str(getattr(exc, 'detail', '') or exc)}