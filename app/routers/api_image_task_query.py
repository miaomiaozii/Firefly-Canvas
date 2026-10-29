"""Router: /api/image-task-query domain (auto-split from main.py)."""
from __future__ import annotations
import sys as _sys
import os as _os
_app_main = _sys.modules.get(_os.environ.get('FIREFLY_MAIN_MODULE', '')) or _sys.modules.get('main') or _sys.modules['__main__']
import asyncio
import time
from fastapi import APIRouter, BackgroundTasks, File, Form, Header, HTTPException, Query, Request, Response, UploadFile, WebSocket, WebSocketDisconnect
from pydantic import BaseModel, Field
from app.core import (BaseModel, Field, GLOBAL_LOOP, HTTPException, IMAGE_TASK_FAILED_STATUSES, ImageTaskQueryRequest, _os, _sys, asyncio, extract_images, httpx, image_task_fail_reason, image_task_status, is_runninghub_provider, log_net_error, rewrite_runninghub_file_url, runninghub_endpoint_url, runninghub_extract_outputs, runninghub_fail_reason, time)
_from_main_names = ['fetch_image_task_payload', 'get_api_provider', 'image_output_meta', 'manager', 'runninghub_api_key', 'runninghub_app_headers', 'runninghub_store_remote_output', 'save_ai_image_to_output', 'save_to_history']
import app.services as _svc
for _nm in _from_main_names:
    _v = getattr(_app_main, _nm, None)
    if _v is None:
        _v = getattr(_svc, _nm, None)
    globals()[_nm] = _v

router = APIRouter()


async def query_upstream_image_task(provider, task_id, *, record_history: bool = True):
    """查询上游生图任务的成品（全局库 /api/image-task-query 与项目任务 GET 主动取回共用）。

    record_history=False 时不写在线历史/广播（项目任务取回由项目侧自己落画布，
    避免同一成品被记两次账）。
    """
    if is_runninghub_provider(provider):
        api_key = runninghub_api_key(provider)
        url = runninghub_endpoint_url(provider, '/task/openapi/outputs')
        try:
            async with httpx.AsyncClient(timeout=httpx.Timeout(connect=20.0, read=240.0, write=30.0, pool=20.0)) as client:
                response = await client.post(url, headers=runninghub_app_headers(True), json={'apiKey': api_key, 'taskId': task_id})
                response.raise_for_status()
                raw = response.json()
                code = raw.get('code') if isinstance(raw, dict) else None
                if code in (0, '0'):
                    local_urls = []
                    local_items = []
                    for remote in runninghub_extract_outputs(raw.get('data')):
                        try:
                            local_url = await runninghub_store_remote_output(client, remote)
                        except Exception:
                            local_url = rewrite_runninghub_file_url(remote)
                        if local_url:
                            local_urls.append(local_url)
                            local_items.append(image_output_meta(local_url))
                    result = {'status': 'succeeded', 'prompt': '', 'images': local_urls, 'image_items': local_items, 'timestamp': time.time(), 'type': 'online', 'model': '', 'provider_id': provider['id'], 'provider_name': provider.get('name') or provider['id'], 'task_id': task_id, 'request_id': '', 'params': {'provider_id': provider['id']}, 'raw': raw}
                    if record_history:
                        save_to_history(result)
                        if GLOBAL_LOOP:
                            asyncio.run_coroutine_threadsafe(manager.broadcast_new_image(result), GLOBAL_LOOP)
                    return result
                if code in (805, '805'):
                    return {'status': 'failed', 'task_id': task_id, 'provider_id': provider['id'], 'provider_name': provider.get('name') or provider['id'], 'error': runninghub_fail_reason(raw), 'raw': raw}
                return {'status': 'running', 'task_id': task_id, 'provider_id': provider['id'], 'provider_name': provider.get('name') or provider['id'], 'message': 'RunningHub 任务仍在生成中', 'raw': raw}
        except httpx.HTTPStatusError as exc:
            text = exc.response.text or ''
            raise HTTPException(status_code=exc.response.status_code, detail=f'查询 RunningHub 任务失败：{text[:300]}') from exc
        except httpx.HTTPError as exc:
            raise HTTPException(status_code=502, detail=f'查询 RunningHub 任务失败：{exc}') from exc
    timeout = httpx.Timeout(connect=20.0, read=300.0, write=60.0, pool=20.0)
    try:
        async with httpx.AsyncClient(timeout=timeout, follow_redirects=True) as client:
            raw = await fetch_image_task_payload(client, task_id, provider)
    except httpx.HTTPStatusError as exc:
        log_net_error(f"查询生图任务 HTTP状态错误 provider={provider.get('id')} task_id={task_id}", exc)
        text = exc.response.text or ''
        raise HTTPException(status_code=exc.response.status_code, detail=f'查询上游生图任务失败：{text[:300]}') from exc
    except httpx.HTTPError as exc:
        log_net_error(f"查询生图任务 网络/TLS错误 provider={provider.get('id')} task_id={task_id}", exc)
        raise HTTPException(status_code=502, detail=f'查询上游生图任务失败：{exc}') from exc
    status = image_task_status(raw)
    image_items = []
    try:
        image_items = extract_images(raw)
    except HTTPException:
        image_items = []
    if image_items:
        local_urls = []
        local_items = []
        for item in image_items:
            local_url = await save_ai_image_to_output(item, prefix='online_')
            if local_url:
                local_urls.append(local_url)
                local_items.append(image_output_meta(local_url, item))
        result = {'status': 'succeeded', 'prompt': '', 'images': local_urls, 'image_items': local_items, 'timestamp': time.time(), 'type': 'online', 'model': '', 'provider_id': provider['id'], 'provider_name': provider.get('name') or provider['id'], 'task_id': task_id, 'request_id': raw.get('id') if isinstance(raw, dict) else '', 'params': {'provider_id': provider['id']}, 'raw': raw}
        if record_history:
            save_to_history(result)
            if GLOBAL_LOOP:
                asyncio.run_coroutine_threadsafe(manager.broadcast_new_image(result), GLOBAL_LOOP)
        return result
    if status in IMAGE_TASK_FAILED_STATUSES:
        return {'status': 'failed', 'task_id': task_id, 'provider_id': provider['id'], 'provider_name': provider.get('name') or provider['id'], 'error': image_task_fail_reason(raw), 'raw': raw}
    return {'status': 'running', 'task_id': task_id, 'provider_id': provider['id'], 'provider_name': provider.get('name') or provider['id'], 'message': '任务仍在生成中', 'raw': raw}

@router.post('/api/image-task-query')
async def query_image_task(payload: ImageTaskQueryRequest):
    provider = _app_main.get_api_provider(payload.provider_id)
    return await query_upstream_image_task(provider, str(payload.task_id or '').strip())
