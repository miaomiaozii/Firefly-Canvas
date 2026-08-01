"""Router: /api/gemini-cli domain (auto-split from main.py)."""
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
from app.core import (BASE_DIR, BaseModel, GeminiCliHelpRequest, HTTPException, _os, _sys, asyncio, codex_decode_output, is_antigravity_cli, os)
_from_main_names = ['gemini_cli_display_name', 'gemini_cli_executable']
import app.services as _svc
for _nm in _from_main_names:
    _v = getattr(_app_main, _nm, None)
    if _v is None:
        _v = getattr(_svc, _nm, None)
    globals()[_nm] = _v

router = APIRouter()

class GeminiCliHelpRequest(BaseModel):
    command: str = ''

@router.get('/api/gemini-cli/status')
async def gemini_cli_status():
    exe = _app_main.gemini_cli_executable()
    display_name = gemini_cli_display_name(exe)
    if not exe:
        return {'installed': False, 'logged_in': False, 'provider': 'antigravity', 'message': '未找到 Antigravity CLI，请先安装。'}
    try:
        proc = await asyncio.create_subprocess_exec(exe, '--version', cwd=BASE_DIR, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=10)
        out_text, err_text = codex_decode_output(stdout, stderr)
        ok = proc.returncode == 0
        is_agy = is_antigravity_cli(exe)
        login_check_error = ''
        if is_agy and ok:
            try:
                auth_proc = await asyncio.create_subprocess_exec(exe, 'models', cwd=BASE_DIR, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
                await asyncio.wait_for(auth_proc.communicate(), timeout=15)
                logged_in = auth_proc.returncode == 0
            except Exception as exc:
                logged_in = False
                login_check_error = str(exc)
        else:
            gemini_home = os.path.join(os.path.expanduser('~'), '.gemini')
            credential_files = (os.path.join(gemini_home, 'oauth_creds.json'), os.path.join(gemini_home, 'google_accounts.json'))
            logged_in = ok and bool(os.getenv('GEMINI_API_KEY') or os.getenv('GOOGLE_API_KEY') or any((os.path.isfile(path) for path in credential_files)))
        return {'installed': ok, 'logged_in': logged_in, 'version': out_text or err_text, 'path': exe, 'provider': 'antigravity' if is_agy else 'gemini', 'message': f"{display_name} 已安装，{('登录状态检查失败' if login_check_error else '已登录' if logged_in else '未登录')}。" if ok else err_text or out_text or f'{display_name} 检测失败', 'raw': {'stdout': out_text, 'stderr': err_text, 'returncode': proc.returncode, 'login_status_checked': not bool(login_check_error)}}
    except Exception as exc:
        return {'installed': False, 'logged_in': False, 'path': exe, 'provider': 'antigravity' if is_antigravity_cli(exe) else 'gemini', 'message': f'{display_name} 检测失败：{exc}'}

@router.post('/api/gemini-cli/help')
async def gemini_cli_help(payload: GeminiCliHelpRequest):
    exe = _app_main.gemini_cli_executable()
    if not exe:
        raise HTTPException(status_code=400, detail='未找到 Antigravity CLI。')
    is_agy = is_antigravity_cli(exe)
    allowed = {'', 'help', 'install', 'models', 'plugin', 'plugins', 'update', 'changelog'} if is_agy else {'', 'help', 'mcp', 'extensions'}
    command = str(payload.command or '').strip()
    if command not in allowed:
        raise HTTPException(status_code=400, detail=f'不允许的 {gemini_cli_display_name(exe)} 命令')
    args = [exe]
    if command:
        args.append(command)
    args.append('--help')
    proc = await asyncio.create_subprocess_exec(*args, cwd=BASE_DIR, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
    stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=20)
    out_text, err_text = codex_decode_output(stdout, stderr)
    if proc.returncode != 0:
        raise HTTPException(status_code=502, detail=(err_text or out_text or f'exit={proc.returncode}')[:1000])
    return {'text': out_text or err_text, 'raw': {'stdout': out_text, 'stderr': err_text}}