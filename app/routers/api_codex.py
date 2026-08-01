"""Router: /api/codex domain (auto-split from main.py)."""
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
from app.core import (BASE_DIR, BaseModel, CodexHelpRequest, HTTPException, _os, _sys, asyncio, codex_decode_output)
_from_main_names = ['codex_cli_executable', 'gpt_image_2_skill_executable']
import app.services as _svc
for _nm in _from_main_names:
    _v = getattr(_app_main, _nm, None)
    if _v is None:
        _v = getattr(_svc, _nm, None)
    globals()[_nm] = _v

router = APIRouter()

class CodexHelpRequest(BaseModel):
    command: str = ''

@router.get('/api/codex/status')
async def codex_status():
    exe = _app_main.codex_cli_executable()
    image2_exe = _app_main.gpt_image_2_skill_executable()
    if not exe:
        return {'installed': False, 'logged_in': False, 'image2_helper_installed': bool(image2_exe), 'image2_helper_path': image2_exe, 'message': '未找到 OpenAI Codex CLI，请先安装。'}
    try:
        proc = await asyncio.create_subprocess_exec(exe, '--version', cwd=BASE_DIR, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=10)
        out_text, err_text = codex_decode_output(stdout, stderr)
        ok = proc.returncode == 0
        logged_in = False
        login_check_error = ''
        if ok:
            try:
                login_proc = await asyncio.create_subprocess_exec(exe, 'login', 'status', cwd=BASE_DIR, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
                await asyncio.wait_for(login_proc.communicate(), timeout=10)
                logged_in = login_proc.returncode == 0
            except Exception as exc:
                login_check_error = str(exc)
        helper_message = 'GPT Image 2 helper 已安装，OpenAI CLI 生图会使用 GPT Image 2。' if image2_exe else '未找到 GPT Image 2 helper，OpenAI CLI 生图不可用；已禁用 Codex 内置 $imagegen 回退。'
        return {'installed': ok, 'logged_in': logged_in, 'version': out_text or err_text, 'path': exe, 'image2_helper_installed': bool(image2_exe), 'image2_helper_path': image2_exe, 'message': f"OpenAI Codex CLI 已安装，{('登录状态检查失败' if login_check_error else '已登录' if logged_in else '未登录')}。{helper_message}" if ok else err_text or out_text or 'Codex CLI 检测失败', 'raw': {'stdout': out_text, 'stderr': err_text, 'returncode': proc.returncode, 'login_status_checked': not bool(login_check_error)}}
    except Exception as exc:
        return {'installed': False, 'logged_in': False, 'path': exe, 'image2_helper_installed': bool(image2_exe), 'image2_helper_path': image2_exe, 'message': f'Codex CLI 检测失败：{exc}'}

@router.post('/api/codex/help')
async def codex_help(payload: CodexHelpRequest):
    exe = _app_main.codex_cli_executable()
    if not exe:
        raise HTTPException(status_code=400, detail='未找到 OpenAI Codex CLI。')
    allowed = {'', 'exec', 'login', 'logout', 'doctor', 'mcp', 'app', 'update'}
    command = str(payload.command or '').strip()
    if command not in allowed:
        raise HTTPException(status_code=400, detail='不允许的 Codex CLI 命令')
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