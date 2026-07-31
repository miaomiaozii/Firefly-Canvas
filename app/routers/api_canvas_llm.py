"""Router: /api/canvas-llm domain (auto-split from main.py)."""
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
_from_main_names = ['codex_chat_text', 'gemini_cli_chat_text', 'get_api_provider', 'is_image_reference_value', 'is_video_reference_value', 'media_reference_to_url', 'resolve_chat_provider', 'video_reference_to_frame_data_urls']
for _nm in _from_main_names:
    globals()[_nm] = getattr(_app_main, _nm)
router = APIRouter()

@router.post('/api/canvas-llm')
async def canvas_llm(payload: CanvasLLMRequest):
    media_values = [str(getattr(item, 'url', item) or '') for item in payload.images or []] + [str(item or '') for item in payload.videos or []] + [str(item or '') for item in payload.audios or []] + [str(item.get('url') or '') for item in payload.audio_references or [] if isinstance(item, dict)]
    require_project_media_values_scope(media_values)
    _provider = _app_main.get_api_provider(payload.provider)
    audio_references = [item for item in payload.audio_references or [] if isinstance(item, dict) and str(item.get('asset_id') or item.get('url') or '').strip()]
    if (payload.audios or audio_references) and (not chat_provider_supports_audio(_provider)):
        raise HTTPException(status_code=400, detail={'code': 'unsupported_audio_input', 'message': '当前对话适配器不支持音频参考输入'})
    if is_codex_provider(_provider):
        model = selected_model(payload.model, (_provider.get('chat_models') or CODEX_DEFAULT_CHAT_MODELS)[0])
        payload.model = model
        text, raw = await codex_chat_text(payload, payload.messages)
        return {'text': text, 'model': model, 'raw_usage': None, 'raw': raw}
    if is_gemini_cli_provider(_provider):
        model = selected_model(payload.model, (_provider.get('chat_models') or GEMINI_CLI_DEFAULT_CHAT_MODELS)[0])
        payload.model = model
        text, raw = await gemini_cli_chat_text(payload, payload.messages)
        return {'text': text, 'model': model, 'raw_usage': None, 'raw': raw}
    chat_base, chat_hdrs, model = _app_main.resolve_chat_provider(payload.provider, payload.model, payload.ms_model)
    _llm_provider = _app_main.get_api_provider(payload.provider) if payload.provider not in ('modelscope',) else {}
    _is_apimart = is_apimart_provider(_llm_provider)
    system_prompt = (payload.system_prompt or '').strip()
    upstream_messages = [{'role': 'system', 'content': system_prompt}] if system_prompt else []
    for item in payload.messages[-MAX_HISTORY_MESSAGES:]:
        role = item.get('role')
        content = item.get('content')
        if role in {'user', 'assistant'} and content:
            upstream_messages.append({'role': role, 'content': content})
    image_inputs = [img for img in payload.images or [] if is_image_reference_value(img)]
    video_inputs = [video for video in payload.videos or [] if is_video_reference_value(video)]
    audio_inputs = audio_references or [{'url': str(value).strip(), 'asset_id': '', 'kind': 'audio'} for value in payload.audios or [] if str(value or '').strip()]
    if image_inputs or video_inputs or audio_inputs:
        content_parts = [{'type': 'text', 'text': payload.message}]
        ok_imgs = 0
        for img in image_inputs[:8]:
            if not img or not isinstance(img, str):
                continue
            ref_url = media_reference_to_url(img, max_image_size=1024)
            if not ref_url:
                continue
            content_parts.append({'type': 'image_url', 'image_url': {'url': ref_url}})
            ok_imgs += 1
        ok_videos = 0
        for video in video_inputs[:3]:
            if not video or not isinstance(video, str):
                continue
            frame_urls = await video_reference_to_frame_data_urls(video, max_frames=6, max_size=768)
            if frame_urls:
                ok_videos += 1
                content_parts.append({'type': 'text', 'text': f'以下是视频 {ok_videos} 按时间顺序抽取的关键帧，请结合这些画面理解视频内容。'})
                for frame_url in frame_urls:
                    content_parts.append({'type': 'image_url', 'image_url': {'url': frame_url}})
            else:
                ref_url = media_reference_to_url(video)
                if not ref_url:
                    continue
                content_parts.append({'type': 'video_url', 'video_url': {'url': ref_url}})
                ok_videos += 1
        for audio in audio_inputs[:3]:
            content_parts.append({'type': 'audio_reference', 'audio_reference': {'asset_id': str(audio.get('asset_id') or ''), 'kind': 'audio', 'name': str(audio.get('name') or '音频参考'), 'url': str(audio.get('url') or '')}})
        print(f'[canvas-llm] model={model} provider={payload.provider} text_len={len(payload.message)} images={ok_imgs}/{len(payload.images)} videos={ok_videos}/{len(payload.videos)} audios={len(audio_inputs)}/{len(payload.audios)}')
        upstream_messages.append({'role': 'user', 'content': content_parts})
    else:
        upstream_messages.append({'role': 'user', 'content': payload.message})
    raw = None
    try:
        async with httpx.AsyncClient(timeout=AI_REQUEST_TIMEOUT) as client:
            req_body = {'model': model, 'messages': upstream_messages}
            if _is_apimart:
                req_body['stream'] = False
            response = await client.post(f'{chat_base}/chat/completions', headers=chat_hdrs, json=req_body)
            response.raise_for_status()
            if not response.content:
                raise HTTPException(status_code=502, detail='上游接口返回了空响应')
            raw = response.json()
    except httpx.HTTPStatusError as exc:
        body = exc.response.text or ''
        friendly = friendly_chat_error_detail(body, model, _llm_provider)
        raise HTTPException(status_code=exc.response.status_code, detail=friendly or f'上游接口错误：{body}') from exc
    except httpx.HTTPError as exc:
        raise HTTPException(status_code=502, detail=f'请求上游接口失败：{exc}') from exc
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f'解析上游响应失败：{exc}') from exc
    try:
        text = text_from_chat_response(raw).strip() if isinstance(raw, dict) else ''
        text = text or '接口返回了空回复。'
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f'解析回复内容失败：{exc}') from exc
    raw_data = unwrap_apimart_response(raw) if isinstance(raw, dict) else {}
    return {'text': text, 'model': model, 'raw_usage': raw_data.get('usage')}