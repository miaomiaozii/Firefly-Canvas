"""Router: /api/canvas-llm domain (auto-split from main.py)."""
from __future__ import annotations
import sys as _sys
import os as _os
_app_main = _sys.modules.get(_os.environ.get('FIREFLY_MAIN_MODULE', '')) or _sys.modules.get('main') or _sys.modules['__main__']
from fastapi import APIRouter, BackgroundTasks, File, Form, Header, HTTPException, Query, Request, Response, UploadFile, WebSocket, WebSocketDisconnect
from app.core import (AI_REQUEST_TIMEOUT, CODEX_DEFAULT_CHAT_MODELS, CanvasLLMRequest, GEMINI_CLI_DEFAULT_CHAT_MODELS, HTTPException, MAX_HISTORY_MESSAGES, PROJECT_MEDIA_ADAPTER_PROJECT, _os, _sys, chat_provider_supports_audio, friendly_chat_error_detail, httpx, is_apimart_provider, is_codex_provider, is_gemini_cli_provider, project_media_adapter_reference_from_url, require_project_media_values_scope, selected_model, text_from_chat_response, text_from_responses, unwrap_apimart_response)
from app.services.conversation import chat_request_latest, chat_text_from_raw
_from_main_names = ['codex_chat_text', 'gemini_cli_chat_text', 'get_api_provider', 'is_image_reference_value', 'is_video_reference_value', 'media_reference_to_url', 'resolve_chat_provider', 'video_reference_to_frame_data_urls']
import app.services as _svc
for _nm in _from_main_names:
    _v = getattr(_app_main, _nm, None)
    if _v is None:
        _v = getattr(_svc, _nm, None)
    globals()[_nm] = _v

router = APIRouter()

@router.post('/api/canvas-llm')
async def canvas_llm(payload: CanvasLLMRequest):
    media_values = [str(getattr(item, 'url', item) or '') for item in payload.images or []] + [str(item or '') for item in payload.videos or []] + [str(item or '') for item in payload.audios or []] + [str(item.get('url') or '') for item in payload.audio_references or [] if isinstance(item, dict)]
    project_id = str(payload.project_id or '').strip()
    project_scope = PROJECT_MEDIA_ADAPTER_PROJECT.set(project_id) if project_id else None
    try:
        require_project_media_values_scope(media_values)
        if project_scope is not None:
            def resolve_image(value: str) -> str:
                if not project_media_adapter_reference_from_url(value):
                    return value
                return _svc._main('reference_to_data_url')(
                    {'url': value},
                    max_size=1536,
                    max_bytes=_svc._main('PROJECT_CONVERSATION_MEDIA_MAX_FILE_BYTES'),
                )

            def resolve_media(value: str, kind: str) -> str:
                if not project_media_adapter_reference_from_url(value):
                    return value
                return _svc.media_adapter_data_url(
                    value,
                    kind,
                    max_bytes=_svc._main('PROJECT_CONVERSATION_MEDIA_MAX_FILE_BYTES'),
                )

            payload.images = [resolve_image(value) for value in payload.images or []]
            payload.videos = [resolve_media(value, 'video') for value in payload.videos or []]
            payload.audios = [resolve_media(value, 'audio') for value in payload.audios or []]
            payload.audio_references = [
                {**item, 'url': resolve_media(str(item.get('url') or ''), 'audio')}
                if isinstance(item, dict) and item.get('url') else item
                for item in payload.audio_references or []
            ]
    finally:
        if project_scope is not None:
            PROJECT_MEDIA_ADAPTER_PROJECT.reset(project_scope)
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
    _need_vision = bool((payload.images or []) or (payload.videos or []))
    chat_base, chat_hdrs, model = _app_main.resolve_chat_provider(payload.provider, payload.model, payload.ms_model, need_vision=_need_vision)
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
    fmt = 'chat'
    try:
        async with httpx.AsyncClient(timeout=AI_REQUEST_TIMEOUT) as client:
            raw, fmt = await chat_request_latest(client, chat_base, chat_hdrs, model, upstream_messages, stream=False, provider_cfg=_llm_provider)
            if not raw:
                raise HTTPException(status_code=502, detail='上游接口返回了空响应')
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
        text = chat_text_from_raw(raw, fmt).strip() if isinstance(raw, dict) else ''
        text = text or '接口返回了空回复。'
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f'解析回复内容失败：{exc}') from exc
    raw_data = unwrap_apimart_response(raw) if isinstance(raw, dict) else {}
    return {'text': text, 'model': model, 'raw_usage': raw_data.get('usage')}
