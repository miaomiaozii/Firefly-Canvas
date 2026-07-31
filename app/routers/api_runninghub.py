"""Router: /api/runninghub domain (auto-split from main.py)."""
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
_from_main_names = ['image_output_meta', 'runninghub_api_key', 'runninghub_app_headers', 'runninghub_local_asset_path', 'runninghub_provider', 'runninghub_store_remote_output']
for _nm in _from_main_names:
    globals()[_nm] = getattr(_app_main, _nm)
router = APIRouter()

class RunningHubSubmitRequest(BaseModel):
    webappId: str = ''
    nodeInfoList: List[Dict[str, Any]] = []
    instanceType: str = ''
    useWallet: bool = False

class RunningHubUploadAssetRequest(BaseModel):
    url: str = ''
    useWallet: bool = False

def normalize_seed_uint32(value):
    try:
        if isinstance(value, bool):
            return value
        raw = str(value).strip()
        if not raw:
            return value
        num = int(float(raw))
    except Exception:
        return value
    if 0 <= num <= SEED_UINT32_MAX:
        return value
    safe = (abs(num) - 1) % SEED_UINT32_MAX + 1
    return str(safe) if isinstance(value, str) else safe

def sanitize_runninghub_node_info_list(items):
    result = []
    for item in items or []:
        if not isinstance(item, dict):
            continue
        clean = dict(item)
        if rh_is_seed_like_name(clean.get('fieldName'), clean.get('label'), clean.get('note')):
            clean['fieldValue'] = normalize_seed_uint32(clean.get('fieldValue'))
        result.append(clean)
    return result

@router.get('/api/runninghub/app-info')
async def runninghub_app_info(webappId: str=''):
    webapp_id = str(webappId or '').strip()
    if not webapp_id:
        raise HTTPException(status_code=400, detail='webappId 必填')
    provider = runninghub_provider()
    api_key = runninghub_api_key(provider)
    url = runninghub_endpoint_url(provider, f'/api/webapp/apiCallDemo?apiKey={urllib.parse.quote(api_key)}&webappId={urllib.parse.quote(webapp_id)}')
    async with httpx.AsyncClient(timeout=httpx.Timeout(connect=20.0, read=120.0, write=30.0, pool=20.0)) as client:
        try:
            response = await client.get(url, headers=runninghub_app_headers(False))
            raw = response.json()
        except httpx.HTTPStatusError as exc:
            raise HTTPException(status_code=exc.response.status_code, detail=exc.response.text[:500]) from exc
        except Exception as exc:
            raise HTTPException(status_code=502, detail=f'请求 RunningHub 应用信息失败：{exc}') from exc
    if response.status_code >= 400:
        raise HTTPException(status_code=response.status_code, detail=json.dumps(raw, ensure_ascii=False)[:500])
    if isinstance(raw, dict) and raw.get('code') not in (0, '0', None):
        raise HTTPException(status_code=400, detail=raw.get('msg') or f"RunningHub 查询失败 code={raw.get('code')}")
    data = raw.get('data') if isinstance(raw, dict) else {}
    return {'success': True, 'data': data or {}}

@router.post('/api/runninghub/submit')
async def runninghub_submit(payload: RunningHubSubmitRequest):
    webapp_id = str(payload.webappId or '').strip()
    if not webapp_id:
        raise HTTPException(status_code=400, detail='webappId 必填')
    provider = runninghub_provider()
    api_key = runninghub_api_key(provider, use_wallet=payload.useWallet)
    body = {'apiKey': api_key, 'webappId': webapp_id, 'nodeInfoList': sanitize_runninghub_node_info_list(payload.nodeInfoList or [])}
    instance_type = str(payload.instanceType or '').strip()
    if instance_type:
        body['instanceType'] = instance_type
    url = runninghub_endpoint_url(provider, '/task/openapi/ai-app/run')
    async with httpx.AsyncClient(timeout=httpx.Timeout(connect=20.0, read=180.0, write=120.0, pool=20.0)) as client:
        try:
            response = await client.post(url, headers=runninghub_app_headers(True, payload.useWallet), json=body)
            raw = response.json()
        except Exception as exc:
            raise HTTPException(status_code=502, detail=f'提交 RunningHub 任务失败：{exc}') from exc
    if response.status_code >= 400:
        raise HTTPException(status_code=response.status_code, detail=json.dumps(raw, ensure_ascii=False)[:800])
    if isinstance(raw, dict) and raw.get('code') in (0, '0'):
        task_id = raw.get('data', {}).get('taskId') if isinstance(raw.get('data'), dict) else ''
        if not task_id:
            raise HTTPException(status_code=502, detail=f'RunningHub 未返回 taskId：{raw}')
        return {'success': True, 'data': {'taskId': task_id, 'raw': raw}}
    raise HTTPException(status_code=400, detail=(raw.get('msg') if isinstance(raw, dict) else '') or f'RunningHub 提交失败：{raw}')

@router.get('/api/runninghub/query')
async def runninghub_query(taskId: str='', useWallet: bool=False):
    task_id = str(taskId or '').strip()
    if not task_id:
        raise HTTPException(status_code=400, detail='taskId 必填')
    provider = runninghub_provider()
    api_key = runninghub_api_key(provider, use_wallet=useWallet)
    url = runninghub_endpoint_url(provider, '/task/openapi/outputs')
    async with httpx.AsyncClient(timeout=httpx.Timeout(connect=20.0, read=240.0, write=30.0, pool=20.0)) as client:
        try:
            response = await client.post(url, headers=runninghub_app_headers(True, useWallet), json={'apiKey': api_key, 'taskId': task_id})
            raw = response.json()
        except Exception as exc:
            raise HTTPException(status_code=502, detail=f'查询 RunningHub 任务失败：{exc}') from exc
        if response.status_code >= 400:
            raise HTTPException(status_code=response.status_code, detail=json.dumps(raw, ensure_ascii=False)[:800])
        code = raw.get('code') if isinstance(raw, dict) else None
        status = 'PENDING'
        urls = []
        image_items = []
        if code in (0, '0'):
            status = 'SUCCESS'
            for remote in runninghub_extract_outputs(raw.get('data')):
                try:
                    local_url = await runninghub_store_remote_output(client, remote)
                except Exception:
                    local_url = remote
                urls.append(local_url)
                image_items.append(image_output_meta(local_url))
        elif code in (804, '804'):
            status = 'RUNNING'
        elif code in (813, '813'):
            status = 'QUEUED'
        elif code in (805, '805'):
            status = 'FAILED'
        else:
            status = 'UNKNOWN'
        return {'success': True, 'data': {'status': status, 'urls': urls, 'image_items': image_items, 'failReason': runninghub_fail_reason(raw), 'code': code, 'raw': raw}}

@router.post('/api/runninghub/upload-asset')
async def runninghub_upload_asset(payload: RunningHubUploadAssetRequest):
    source_url = rewrite_runninghub_file_url(str(payload.url or '').strip())
    if not source_url:
        raise HTTPException(status_code=400, detail='url 必填')
    provider = runninghub_provider()
    api_key = runninghub_api_key(provider, use_wallet=payload.useWallet)
    filename = 'asset.bin'
    content_type = 'application/octet-stream'
    content = b''
    async with httpx.AsyncClient(timeout=httpx.Timeout(connect=20.0, read=240.0, write=240.0, pool=20.0), follow_redirects=True) as client:
        path = runninghub_local_asset_path(source_url)
        if path:
            filename = os.path.basename(path)
            content_type = content_type_for_path(path)
            with open(path, 'rb') as f:
                content = f.read()
        elif source_url.startswith(('http://', 'https://')):
            response = await client.get(source_url)
            if not response.is_success:
                raise HTTPException(status_code=400, detail=f'下载素材失败 HTTP {response.status_code}')
            content = response.content
            content_type = response.headers.get('content-type') or content_type
            filename = os.path.basename(urllib.parse.urlsplit(source_url).path) or filename
        else:
            raise HTTPException(status_code=400, detail=f'不支持的素材地址：{source_url}')
        if not content:
            raise HTTPException(status_code=400, detail='素材为空，无法上传到 RunningHub')
        upload_url = runninghub_endpoint_url(provider, '/task/openapi/upload')
        files = {'file': (filename, content, content_type)}
        data = {'apiKey': api_key, 'fileType': 'input'}
        try:
            response = await client.post(upload_url, headers=runninghub_app_headers(False, payload.useWallet), data=data, files=files)
            raw = response.json()
        except Exception as exc:
            raise HTTPException(status_code=502, detail=f'上传素材到 RunningHub 失败：{exc}') from exc
    if response.status_code >= 400:
        raise HTTPException(status_code=response.status_code, detail=json.dumps(raw, ensure_ascii=False)[:800])
    if isinstance(raw, dict) and raw.get('code') in (0, '0') and isinstance(raw.get('data'), dict) and raw['data'].get('fileName'):
        return {'success': True, 'data': {'fileName': raw['data']['fileName'], 'fileType': raw['data'].get('fileType') or content_type}}
    raise HTTPException(status_code=400, detail=(raw.get('msg') if isinstance(raw, dict) else '') or f'RunningHub 上传失败：{raw}')