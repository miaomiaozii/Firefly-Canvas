"""Router: /api/providers domain (auto-split from main.py)."""
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
from app.core import (AGNES_DEFAULT_VIDEO_MODELS, Any, ApiProviderPayload, BaseModel, CHAT_MODEL, CODEX_DEFAULT_CHAT_MODELS, CODEX_DEFAULT_IMAGE_MODELS, Dict, Field, GEMINI_CLI_DEFAULT_CHAT_MODELS, GEMINI_CLI_DEFAULT_IMAGE_MODELS, GLOBAL_CONFIG_LOCK, HTTPException, IMAGE_MODEL, JIMENG_DEFAULT_IMAGE_MODELS, JIMENG_DEFAULT_VIDEO_MODELS, List, MODELSCOPE_DEFAULT_CHAT_MODELS, Optional, REMOVED_PROVIDER_IDS, RUNNINGHUB_DEFAULT_BASE_URL, RUNNINGHUB_DEFAULT_IMAGE_MODELS, RUNNINGHUB_DEFAULT_VIDEO_MODELS, RUNNINGHUB_FALLBACK_CHAT_MODELS, RUNNINGHUB_THUMBNAIL_EXTS, STATIC_DIR, STATIC_RUNNINGHUB_API_PROVIDERS_FILE, STATIC_RUNNINGHUB_DIR, STATIC_RUNNINGHUB_THUMBNAIL_DIR, SUPPORTED_PROVIDER_PROTOCOLS, TestConnectionPayload, VOLCENGINE_DEFAULT_PROJECT_NAME, VOLCENGINE_DEFAULT_REGION, _os, _sys, apply_agnes_model_defaults, apply_locked_recommended_model_rules, apply_runninghub_system_thumbnails, bearer_auth_value, codex_models_payload, detect_image_request_mode, gemini_cli_models_payload, httpx, is_codex_provider, is_gemini_cli_provider, json, load_static_runninghub_provider, locked_recommended_provider_rule, looks_like_html_response, model_list, model_list_from_values, normalize_image_request_mode, normalize_provider, normalize_runninghub_entries, normalize_runninghub_entry, openai_compat_root_for_probe, os, parse_upstream_models, preserve_runninghub_hidden_overrides, probe_openai_compat_bearer_endpoint, probe_openai_models_endpoint, probe_volcengine_auto_detect, probe_volcengine_task_endpoint, protocol_from_payload, provider_key_env, provider_protocol, re, recommend_upstream_models, reload_env_globals, runninghub_entry_id, runninghub_model_display_name, runninghub_model_id, runninghub_openapi_url, runninghub_registry_payload, runninghub_wallet_key_env, static_runninghub_thumbnail_url, upstream_model_headers, upstream_models_url, urllib, volcengine_access_key_env, volcengine_default_model_payload, volcengine_secret_key_env, volcengine_task_probe_url)
_from_main_names = ['API_PROVIDERS_FILE', 'DATA_DIR', 'api_key_from_payload', 'fetch_models_from_upstream', 'fetch_runninghub_model_registry', 'get_api_provider_exact', 'provider_env_key_value', 'public_api_providers', 'public_provider', 'runninghub_models_payload', 'save_api_providers', 'update_env_values', 'volcengine_provider_api_key']
import app.services as _svc
for _nm in _from_main_names:
    _v = getattr(_app_main, _nm, None)
    if _v is None:
        _v = getattr(_svc, _nm, None)
    globals()[_nm] = _v

router = APIRouter()

def model_list(env_name, primary, defaults):
    configured = os.getenv(env_name, '')
    configured_values = [item.strip() for item in configured.split(',') if item.strip()]
    values = configured_values or [primary, *defaults]
    deduped = []
    for value in values:
        if value and value not in deduped:
            deduped.append(value)
    return deduped

def reload_env_globals():
    """保存 API 设置后，将 os.environ 里最新的值同步回模块级全局变量，
    避免保存后需要重启才能生效。"""
    global MODELSCOPE_API_KEY, AI_API_KEY, AI_BASE_URL
    global IMAGE_MODELS, CHAT_MODELS, VIDEO_MODELS, MODELSCOPE_CHAT_MODELS
    MODELSCOPE_API_KEY = os.getenv('MODELSCOPE_API_KEY', '')
    AI_API_KEY = os.getenv('COMFLY_API_KEY', '')
    _app_main.AI_BASE_URL = os.getenv('COMFLY_BASE_URL', 'https://ai.comfly.chat').rstrip('/')
    IMAGE_MODELS = model_list('IMAGE_MODELS', os.getenv('IMAGE_MODEL', IMAGE_MODEL), ['nano-banana-pro'])
    CHAT_MODELS = model_list('CHAT_MODELS', os.getenv('CHAT_MODEL', CHAT_MODEL), ['gpt-4o-mini', 'gemini-3.1-flash-image-preview-2k'])
    VIDEO_MODELS = model_list('VIDEO_MODELS', 'veo3-fast', ['veo2', 'veo2-fast', 'veo2-pro', 'veo3', 'veo3-fast', 'veo3-pro', 'veo3.1', 'veo3.1-fast', 'veo3.1-quality', 'veo3.1-lite', 'sora-2', 'sora-2-pro', 'wan2.6-t2v', 'wan2.6-i2v', 'wan2.5-t2v-preview', 'wan2.5-i2v-preview', 'wan2.2-t2v-plus', 'wan2.2-i2v-plus', 'wan2.2-i2v-flash', 'doubao-seedance-2-0-260128', 'doubao-seedance-2-0-fast-260128', 'doubao-seedance-1-5-pro-251215', 'doubao-seedance-1-0-pro-250528', 'doubao-seedance-1-0-lite-t2v-250428', 'doubao-seedance-1-0-lite-i2v-250428'])
    _configured = [m.strip() for m in os.getenv('MODELSCOPE_CHAT_MODELS', '').split(',') if m.strip()]
    MODELSCOPE_CHAT_MODELS = list(dict.fromkeys([m for m in [*MODELSCOPE_DEFAULT_CHAT_MODELS, *_configured] if m]))

def volcengine_provider_api_key(explicit_key: str='') -> str:
    explicit_key = str(explicit_key or '').strip()
    if explicit_key:
        return explicit_key
    return _app_main.provider_env_key_value('volcengine')

def static_runninghub_thumbnail_url(entry_id, kind):
    if kind != 'app':
        return ''
    entry_id = re.sub('[^0-9A-Za-z_-]', '', str(entry_id or '').strip())
    if not entry_id:
        return ''
    for name in (f'app-{entry_id}', entry_id):
        for ext in RUNNINGHUB_THUMBNAIL_EXTS:
            for root in (STATIC_RUNNINGHUB_THUMBNAIL_DIR, STATIC_RUNNINGHUB_DIR):
                path = os.path.abspath(os.path.join(root, f'{name}{ext}'))
                if not path.startswith(os.path.abspath(STATIC_RUNNINGHUB_DIR) + os.sep):
                    continue
                if os.path.isfile(path):
                    rel = os.path.relpath(path, STATIC_DIR).replace(os.sep, '/')
                    return f"/static/{urllib.parse.quote(rel, safe='/._-')}?v={int(os.path.getmtime(path))}"
    return ''

def apply_runninghub_system_thumbnails(entries, kind):
    result = []
    for entry in normalize_runninghub_entries(entries or [], kind):
        if not entry.get('thumbnail'):
            thumb = static_runninghub_thumbnail_url(runninghub_entry_id(entry, kind), kind)
            if thumb:
                entry['thumbnail'] = thumb
        result.append(entry)
    return result

def load_static_runninghub_provider():
    if not os.path.exists(STATIC_RUNNINGHUB_API_PROVIDERS_FILE):
        return None
    try:
        with open(STATIC_RUNNINGHUB_API_PROVIDERS_FILE, 'r', encoding='utf-8') as f:
            raw = json.load(f)
        candidates = raw if isinstance(raw, list) else raw.get('providers') if isinstance(raw, dict) else []
        if isinstance(raw, dict) and raw.get('id') == 'runninghub':
            candidates = [raw]
        for item in candidates or []:
            if isinstance(item, dict) and str(item.get('id') or '').strip().lower() == 'runninghub':
                provider = normalize_provider(item)
                provider['rh_apps'] = apply_runninghub_system_thumbnails(provider.get('rh_apps') or [], 'app')
                return provider
    except Exception as e:
        print(f'加载 static RunningHub 配置失败: {e}')
    return None

def preserve_runninghub_hidden_overrides(provider):
    if not isinstance(provider, dict) or provider.get('id') != 'runninghub':
        return provider
    static_provider = load_static_runninghub_provider()
    if not static_provider:
        return provider
    provider = dict(provider)
    current = normalize_runninghub_entries(provider.get('rh_apps') or [], 'app')
    current_ids = {runninghub_entry_id(item, 'app') for item in current}
    for static_entry in static_provider.get('rh_apps') or []:
        entry_id = runninghub_entry_id(static_entry, 'app')
        if entry_id and entry_id not in current_ids:
            current.append(normalize_runninghub_entry({**static_entry, 'enabled': False, 'hidden': True}, 'app'))
    provider['rh_apps'] = [item for item in current if item]
    return provider

def apply_locked_recommended_model_rules(base_url='', grouped=None):
    rule = locked_recommended_provider_rule('', '', base_url)
    if not rule or 'video_models' not in rule:
        return grouped
    grouped = {key: list(value or []) for key, value in (grouped or {}).items()}
    grouped.setdefault('image', [])
    grouped.setdefault('chat', [])
    grouped['video'] = list(rule.get('video_models') or [])
    return grouped

def save_api_providers(providers):
    os.makedirs(_app_main.DATA_DIR, exist_ok=True)
    with GLOBAL_CONFIG_LOCK:
        with open(_app_main.API_PROVIDERS_FILE, 'w', encoding='utf-8') as f:
            json.dump(providers, f, ensure_ascii=False, indent=2)

class ApiProviderPayload(BaseModel):
    id: str = ''
    name: str = ''
    base_url: str = ''
    protocol: str = 'openai'
    image_request_mode: str = 'openai'
    image_generation_endpoint: str = ''
    image_edit_endpoint: str = ''
    video_generation_endpoint: str = ''
    enabled: bool = True
    primary: bool = False
    image_models: List[str] = Field(default_factory=list)
    image_model_capabilities: Dict[str, Dict[str, Any]] = Field(default_factory=dict)
    chat_models: List[str] = []
    chat_audio_input: bool = False
    video_models: List[str] = []
    video_model_capabilities: Dict[str, Dict[str, Any]] = Field(default_factory=dict)
    recommended_models: Dict[str, str] = Field(default_factory=dict)
    available_models: Dict[str, List[str]] = Field(default_factory=dict)
    model_selection_version: int = 0
    model_names: Dict[str, str] = {}
    model_protocols: Dict[str, str] = {}
    ms_loras: List[Dict[str, Any]] = []
    ms_defaults_version: int = 0
    rh_apps: List[Dict[str, Any]] = []
    volcengine_project_name: str = VOLCENGINE_DEFAULT_PROJECT_NAME
    volcengine_region: str = VOLCENGINE_DEFAULT_REGION
    volcengine_access_key_id: Optional[str] = None
    volcengine_secret_access_key: Optional[str] = None
    api_key: Optional[str] = None
    wallet_api_key: Optional[str] = None
    clear_key: bool = False
    clear_wallet_key: bool = False
    clear_volcengine_access_key_id: bool = False
    clear_volcengine_secret_access_key: bool = False

def codex_models_payload(raw=None):
    all_models = [*CODEX_DEFAULT_IMAGE_MODELS, *CODEX_DEFAULT_CHAT_MODELS]
    return {'ok': True, 'protocol': 'codex', 'status': 200, 'message': 'OpenAI Codex CLI 可用，模型列表来自本机 CLI 默认配置。', 'model_count': len(all_models), 'total': len(all_models), 'image_models': CODEX_DEFAULT_IMAGE_MODELS, 'chat_models': CODEX_DEFAULT_CHAT_MODELS, 'video_models': [], 'all': all_models, 'raw': raw or {}}

def gemini_cli_models_payload(raw=None):
    all_models = [*GEMINI_CLI_DEFAULT_IMAGE_MODELS, *GEMINI_CLI_DEFAULT_CHAT_MODELS]
    all_models = model_list_from_values(all_models)
    return {'ok': True, 'protocol': 'gemini-cli', 'status': 200, 'message': 'Antigravity CLI 可用，模型列表使用 auto 默认模型。', 'model_count': len(all_models), 'total': len(all_models), 'image_models': GEMINI_CLI_DEFAULT_IMAGE_MODELS, 'chat_models': GEMINI_CLI_DEFAULT_CHAT_MODELS, 'video_models': [], 'all': all_models, 'raw': raw or {}}

def runninghub_model_display_name(item, model_id=''):
    if not isinstance(item, dict):
        return ''
    raw_id = str(model_id or runninghub_model_id(item) or '').strip()
    for key in ('name_cn', 'name_zh', 'zh_name', 'cn_name', 'display_name', 'displayName', 'title', 'label', 'nameCn', 'nameZh', 'chinese_name', 'chineseName'):
        value = re.sub('\\s+', ' ', str(item.get(key) or '').strip())
        if value and value != raw_id:
            return value[:160]
    name = re.sub('\\s+', ' ', str(item.get('name') or '').strip())
    if name and name != raw_id and (not re.fullmatch('[A-Za-z0-9_./:-]+', name)):
        return name[:160]
    return ''

def runninghub_registry_payload(items):
    grouped = {'image': [], 'chat': RUNNINGHUB_FALLBACK_CHAT_MODELS[:], 'video': []}
    model_names = {}
    all_ids = []
    for item in items or []:
        mid = runninghub_model_id(item)
        if not mid:
            continue
        display_name = runninghub_model_display_name(item, mid)
        if display_name:
            model_names[mid] = display_name
        output_type = str(item.get('output_type') or item.get('outputType') or '').strip().lower()
        if output_type in ('image', 'video'):
            grouped[output_type].append(mid)
            all_ids.append(mid)
    for model in RUNNINGHUB_DEFAULT_IMAGE_MODELS:
        if model not in grouped['image']:
            grouped['image'].append(model)
            all_ids.append(model)
    for model in RUNNINGHUB_DEFAULT_VIDEO_MODELS:
        if model not in grouped['video']:
            grouped['video'].append(model)
            all_ids.append(model)
    for model in RUNNINGHUB_FALLBACK_CHAT_MODELS:
        if model not in all_ids:
            all_ids.append(model)
    for key in grouped:
        grouped[key] = sorted(set(grouped[key]))
    return {'total': len(set(all_ids)), 'image_models': grouped['image'], 'chat_models': grouped['chat'], 'video_models': grouped['video'], 'all': sorted(set(all_ids)), 'model_names': model_names, 'protocol': 'runninghub'}

async def runninghub_models_payload(provider=None):
    registry, meta = await _app_main.fetch_runninghub_model_registry(provider, include_fallback=True, include_meta=True)
    payload = runninghub_registry_payload(registry)
    payload['raw'] = {'registry_count': len(registry), **meta}
    if meta.get('source') == 'fallback':
        payload['message'] = 'RunningHub 模型接口未返回完整列表，当前显示内置兜底模型。'
    else:
        payload['message'] = f"RunningHub 模型列表来自 {meta.get('source')}"
    return payload

@router.get('/api/providers')
async def api_providers():
    return {'providers': public_api_providers()}

@router.put('/api/providers')
async def save_providers(payload: List[ApiProviderPayload]):
    providers = []
    env_updates = {}
    for item in payload:
        if str(getattr(item, 'id', '') or '').strip().lower() in REMOVED_PROVIDER_IDS:
            continue
        provider_data = item.model_dump(exclude={'api_key'}) if hasattr(item, 'model_dump') else item.dict(exclude={'api_key'})
        provider = normalize_provider(provider_data)
        if provider['id'] == 'runninghub':
            provider = preserve_runninghub_hidden_overrides(provider)
        if any((existing['id'] == provider['id'] for existing in providers)):
            raise HTTPException(status_code=400, detail=f"API 平台 ID 重复：{provider['id']}")
        providers.append(provider)
        key_env = provider_key_env(provider['id'])
        if item.clear_key:
            env_updates[key_env] = ''
        elif item.api_key is not None and item.api_key.strip():
            env_updates[key_env] = item.api_key.strip()
        if provider['id'] == 'runninghub':
            wallet_env = runninghub_wallet_key_env()
            if item.clear_wallet_key:
                env_updates[wallet_env] = ''
            elif item.wallet_api_key is not None and item.wallet_api_key.strip():
                env_updates[wallet_env] = item.wallet_api_key.strip()
        if provider['id'] == 'volcengine':
            ak_env = volcengine_access_key_env()
            sk_env = volcengine_secret_key_env()
            if item.clear_volcengine_access_key_id:
                env_updates[ak_env] = ''
            elif item.volcengine_access_key_id is not None and item.volcengine_access_key_id.strip():
                env_updates[ak_env] = item.volcengine_access_key_id.strip()
            if item.clear_volcengine_secret_access_key:
                env_updates[sk_env] = ''
            elif item.volcengine_secret_access_key is not None and item.volcengine_secret_access_key.strip():
                env_updates[sk_env] = item.volcengine_secret_access_key.strip()
        if provider['id'] == 'comfly':
            env_updates['COMFLY_BASE_URL'] = provider['base_url']
            env_updates['IMAGE_MODELS'] = ','.join(provider['image_models'])
            env_updates['CHAT_MODELS'] = ','.join(provider['chat_models'])
            env_updates['VIDEO_MODELS'] = ','.join(provider.get('video_models') or [])
        if provider['id'] == 'volcengine':
            provider['protocol'] = 'volcengine'
    if not providers:
        raise HTTPException(status_code=400, detail='至少保留一个 API 平台')
    primary_indices = [i for i, provider in enumerate(providers) if provider.get('primary')]
    if primary_indices:
        winner = primary_indices[-1]
        for i, p in enumerate(providers):
            p['primary'] = i == winner
    save_api_providers(providers)
    if env_updates:
        _app_main.update_env_values(env_updates)
        reload_env_globals()
    return {'providers': [public_provider(p) for p in providers]}

class TestConnectionPayload(BaseModel):
    base_url: str = ''
    api_key: str = ''
    provider_id: str = ''
    protocol: str = 'openai'
    image_request_mode: str = 'openai'

def protocol_from_payload(payload):
    provider_id = str(getattr(payload, 'provider_id', '') or '').strip().lower()
    if provider_id == 'volcengine':
        return 'volcengine'
    if provider_id == 'runninghub':
        return 'runninghub'
    if provider_id == 'jimeng':
        return 'jimeng'
    base_url = str(getattr(payload, 'base_url', '') or '').strip().lower()
    if 'runninghub.cn' in base_url or 'runninghub.ai' in base_url:
        return 'runninghub'
    protocol = str(getattr(payload, 'protocol', '') or 'openai').strip().lower()
    return protocol if protocol in SUPPORTED_PROVIDER_PROTOCOLS else 'openai'

def api_key_from_payload(payload, protocol: str=''):
    explicit = str(getattr(payload, 'api_key', '') or '').strip()
    provider_id = str(getattr(payload, 'provider_id', '') or '').strip().lower()
    protocol = str(protocol or protocol_from_payload(payload) or '').strip().lower()
    if explicit:
        return explicit
    if provider_id:
        if provider_id == 'runninghub':
            value = os.getenv(runninghub_wallet_key_env(), '')
            if value:
                return value
        value = _app_main.provider_env_key_value(provider_id)
        if value:
            return value
    if protocol == 'volcengine':
        return volcengine_provider_api_key('')
    return ''

def upstream_models_url(base_url: str, protocol: str):
    if protocol == 'gemini':
        return f'{base_url}/models' if base_url.endswith('/v1beta') else f'{base_url}/v1beta/models'
    if protocol == 'volcengine':
        return f'{base_url}/models' if base_url.endswith('/api/v3') else f'{base_url}/api/v3/models'
    if protocol == 'runninghub':
        return runninghub_openapi_url({'base_url': base_url}, 'models')
    return f'{base_url}/models' if base_url.endswith('/v1') else f'{base_url}/v1/models'

def upstream_model_headers(api_key: str, protocol: str):
    if protocol == 'gemini':
        return {'x-goog-api-key': api_key, 'Accept': 'application/json'}
    if protocol == 'runninghub':
        return {'Authorization': bearer_auth_value(api_key), 'Accept': 'application/json'}
    return {'Authorization': bearer_auth_value(api_key), 'Accept': 'application/json'}

def volcengine_default_model_payload(status=200, message='', raw=None):
    return {'ok': True, 'protocol': 'volcengine', 'status': status, 'message': message or '方舟任务接口可用，模型列表接口未返回模型。请按实际方舟控制台模型名称手动填写视频模型。', 'model_count': 0, 'image_models': [], 'chat_models': [], 'video_models': [], 'recommended_models': {}, 'all': [], 'raw': raw}

def volcengine_task_probe_url(base_url: str):
    base = str(base_url or '').strip().rstrip('/')
    if not base:
        return ''
    if base.endswith('/api/v3'):
        return f'{base}/contents/generations/tasks/healthcheck_probe_do_not_submit'
    return f'{base}/api/v3/contents/generations/tasks/healthcheck_probe_do_not_submit'

async def probe_volcengine_task_endpoint(client, base_url: str, api_key: str):
    probe_url = volcengine_task_probe_url(base_url)
    if not probe_url:
        return (False, {'status': 0, 'message': 'Base URL 为空'})
    response = await client.get(probe_url, headers=upstream_model_headers(api_key, 'volcengine'))
    try:
        raw = response.json() if response.text else {}
    except Exception:
        raw = response.text[:500]
    if response.status_code in (401, 403):
        return (False, {'status': response.status_code, 'message': '方舟 API Key 无效或无权限', 'raw': raw})
    if looks_like_html_response(response.text):
        return (False, {'status': response.status_code, 'message': '任务接口返回 HTML，Base URL 可能不是 API 地址', 'raw': raw})
    if response.status_code < 500:
        return (True, {'status': response.status_code, 'message': '方舟任务查询端点可达', 'raw': raw})
    return (False, {'status': response.status_code, 'message': f'方舟任务接口服务端错误 {response.status_code}', 'raw': raw})

def openai_compat_root_for_probe(base_url: str):
    base = str(base_url or '').strip().rstrip('/')
    if base.endswith('/api/v3'):
        base = base[:-len('/api/v3')]
    if base.endswith('/v1'):
        return base
    return f'{base}/v1' if base else ''

async def probe_openai_compat_bearer_endpoint(client, base_url: str, api_key: str):
    root = openai_compat_root_for_probe(base_url)
    if not root:
        return (False, {'status': 0, 'message': 'Base URL 为空'})
    url = f'{root}/chat/completions'
    response = await client.post(url, headers={**upstream_model_headers(api_key, 'openai'), 'Content-Type': 'application/json'}, json={'messages': []})
    try:
        raw = response.json() if response.text else {}
    except Exception:
        raw = response.text[:500]
    if response.status_code in (401, 403):
        return (False, {'status': response.status_code, 'message': 'API Key 无效或无权限', 'raw': raw})
    if looks_like_html_response(response.text):
        return (False, {'status': response.status_code, 'message': 'OpenAI 兼容入口返回 HTML，Base URL 可能不是 API 地址', 'raw': raw})
    if response.status_code < 500:
        return (True, {'status': response.status_code, 'message': 'OpenAI 兼容 Bearer 鉴权入口可达', 'raw': raw})
    return (False, {'status': response.status_code, 'message': f'OpenAI 兼容入口服务端错误 {response.status_code}', 'raw': raw})

async def probe_openai_models_endpoint(client, base_url: str, api_key: str):
    url = upstream_models_url(base_url, 'openai')
    response = await client.get(url, headers=upstream_model_headers(api_key, 'openai'))
    try:
        raw = response.json() if response.text else {}
    except Exception:
        raw = response.text[:500]
    if response.status_code in (301, 302, 303, 307, 308):
        location = response.headers.get('Location') or response.headers.get('location') or ''
        suffix = f'：{location}' if location else ''
        return (False, {'status': response.status_code, 'message': f'OpenAI /v1/models 发生跳转{suffix}，请填写 API Base URL，不要填写网页登录地址', 'raw': raw})
    if response.status_code in (401, 403):
        return (False, {'status': response.status_code, 'message': 'OpenAI API Key 无效或无权限', 'raw': raw})
    if looks_like_html_response(response.text):
        return (False, {'status': response.status_code, 'message': 'OpenAI /v1/models 返回网页 HTML，请检查请求地址是否为 API Base URL', 'raw': raw})
    if response.status_code < 300:
        grouped, ids = parse_upstream_models(raw, 'openai') if isinstance(raw, dict) else ({'image': [], 'chat': [], 'video': []}, [])
        grouped, ids = apply_agnes_model_defaults(base_url, grouped, ids)
        grouped = apply_locked_recommended_model_rules(base_url, grouped)
        return (True, {'status': response.status_code, 'message': f"OpenAI 兼容模型列表端点可用{(f'，找到 {len(ids)} 个模型' if ids else '')}", 'raw': raw, 'model_count': len(ids), 'image_models': grouped['image'], 'chat_models': grouped['chat'], 'video_models': grouped['video'], 'all': ids})
    if 400 <= response.status_code < 500:
        return (False, {'status': response.status_code, 'message': f'OpenAI /v1/models 不可用 (HTTP {response.status_code})', 'raw': raw})
    return (False, {'status': response.status_code, 'message': f'OpenAI /v1/models 服务端错误 {response.status_code}', 'raw': raw})

async def probe_volcengine_auto_detect(client, base_url: str, api_key: str):
    task_ok, task_probe = await probe_volcengine_task_endpoint(client, base_url, api_key)
    if task_ok:
        return (True, {'status': task_probe.get('status') or 200, 'message': '检测到方舟/Ark 任务协议', 'raw': {'task_probe': task_probe.get('raw')}})
    compat_ok, compat_probe = await probe_openai_compat_bearer_endpoint(client, base_url, api_key)
    if compat_ok:
        return (True, {'status': compat_probe.get('status') or 200, 'message': '检测到方舟/Ark Bearer 鉴权入口（OpenAI 兼容透传）', 'raw': {'task_probe': task_probe, 'openai_compat_probe': compat_probe.get('raw')}})
    return (False, {'status': compat_probe.get('status') or task_probe.get('status') or 0, 'message': compat_probe.get('message') or task_probe.get('message') or '未检测到方舟/Ark 兼容入口', 'raw': {'task_probe': task_probe, 'openai_compat_probe': compat_probe.get('raw')}})

def recommend_upstream_models(grouped):
    """Choose one stable default per media kind for automatic routing.

    The upstream list is still returned in full; these values only tell the
    canvas which model to select first after a provider is fetched.
    """
    priorities = {'image': ('nano-banana-pro', 'nano-banana-2', 'nano-banana', 'gpt-image', 'imagen', 'seedream', 'qwen-image', 'dall-e', 'flux', 'image'), 'chat': ('gpt-5.4', 'chatgpt-5.4', 'gpt-5.', 'gpt-', 'claude', 'gemini', 'qwen', 'deepseek', 'glm', 'llama', 'chat'), 'video': ('veo', 'sora', 'seedance', 'wan', 'kling', 'hailuo', 'video')}
    result = {}
    for kind, values in (grouped or {}).items():
        models = [str(value or '').strip() for value in values or [] if str(value or '').strip()]
        if not models:
            continue
        selected = next((model for hint in priorities.get(kind, ()) for model in models if hint in model.lower()), models[0])
        result[kind] = selected
    return result

def apply_agnes_model_defaults(base_url, grouped, ids):
    if 'apihub.agnes-ai.com' not in str(base_url or '').strip().lower():
        return (grouped, ids)
    grouped = {key: list(value or []) for key, value in (grouped or {}).items()}
    ids = list(ids or [])
    for model in AGNES_DEFAULT_VIDEO_MODELS:
        if model not in ids:
            ids.append(model)
        if model not in grouped.setdefault('video', []):
            grouped['video'].append(model)
    ids = sorted(set(ids))
    grouped['video'] = sorted(set(grouped.get('video') or []))
    return (grouped, ids)

@router.post('/api/providers/test-connection')
async def test_provider_connection(payload: TestConnectionPayload):
    """测试请求地址是否可用：调上游 /v1/models。验证通过时同时把模型清单按类别返回，避免再调一次拉取接口。"""
    protocol = protocol_from_payload(payload)
    if protocol == 'codex':
        status = await _app_main.codex_status()
        payload_models = codex_models_payload(raw={'status': status})
        payload_models.update({'ok': bool(status.get('installed')), 'status': 200 if status.get('installed') else 0, 'message': status.get('message') or ('OpenAI Codex CLI 可用' if status.get('installed') else '未找到 OpenAI Codex CLI')})
        return payload_models
    if protocol == 'gemini-cli':
        status = await _app_main.gemini_cli_status()
        payload_models = gemini_cli_models_payload(raw={'status': status})
        payload_models.update({'ok': bool(status.get('installed')), 'status': 200 if status.get('installed') else 0, 'message': status.get('message') or ('Antigravity CLI 可用' if status.get('installed') else '未找到 Antigravity CLI')})
        return payload_models
    if protocol == 'jimeng':
        status = await _app_main.jimeng_status()
        return {'ok': bool(status.get('installed') and status.get('logged_in')), 'status': 200 if status.get('logged_in') else 0, 'message': status.get('message') or '即梦 CLI 已登录', 'model_count': len(JIMENG_DEFAULT_IMAGE_MODELS) + len(JIMENG_DEFAULT_VIDEO_MODELS), 'image_models': JIMENG_DEFAULT_IMAGE_MODELS, 'chat_models': [], 'video_models': JIMENG_DEFAULT_VIDEO_MODELS, 'all': [*JIMENG_DEFAULT_IMAGE_MODELS, *JIMENG_DEFAULT_VIDEO_MODELS], 'raw': status.get('raw')}
    if protocol == 'runninghub':
        provider = {'id': 'runninghub', 'name': 'RunningHub', 'base_url': (payload.base_url or RUNNINGHUB_DEFAULT_BASE_URL).strip().rstrip('/'), 'protocol': 'runninghub', 'api_key': api_key_from_payload(payload, protocol)}
        payload_models = await runninghub_models_payload(provider)
        return {'ok': True, 'status': 200, 'message': 'RunningHub OpenAPI 可用，已拉取官方直连模型注册表。', 'model_count': payload_models['total'], 'image_models': payload_models['image_models'], 'chat_models': payload_models['chat_models'], 'video_models': payload_models['video_models'], 'all': payload_models['all'], 'protocol': 'runninghub', 'raw': payload_models.get('raw')}
    base_url = (payload.base_url or '').strip().rstrip('/')
    if not base_url:
        raise HTTPException(status_code=400, detail='请先填写请求地址')
    if not re.match('^https?://', base_url):
        raise HTTPException(status_code=400, detail='请求地址必须以 http:// 或 https:// 开头')
    api_key = api_key_from_payload(payload, protocol)
    if not api_key:
        key_name = '方舟 API Key' if protocol == 'volcengine' else 'API Key'
        raise HTTPException(status_code=400, detail=f'请先填写或保存 {key_name}')
    url = upstream_models_url(base_url, protocol)
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.get(url, headers=upstream_model_headers(api_key, protocol))
            if resp.status_code in (301, 302, 303, 307, 308):
                location = resp.headers.get('Location') or resp.headers.get('location') or ''
                suffix = f'：{location}' if location else ''
                endpoint_label = '/v1beta/models' if protocol == 'gemini' else '/api/v3/models' if protocol == 'volcengine' else '/openapi/v2/models' if protocol == 'runninghub' else '/v1/models'
                return {'ok': False, 'status': resp.status_code, 'message': f'上游 {endpoint_label} 发生跳转{suffix}，请填写 API Base URL，不要填写网页登录地址'}
            if looks_like_html_response(resp.text):
                endpoint_label = '/v1beta/models' if protocol == 'gemini' else '/api/v3/models' if protocol == 'volcengine' else '/openapi/v2/models' if protocol == 'runninghub' else '/v1/models'
                return {'ok': False, 'status': resp.status_code, 'message': f'上游 {endpoint_label} 返回网页 HTML，请检查请求地址是否为 API Base URL'}
            if resp.status_code >= 400:
                if protocol == 'volcengine':
                    detected, probe = await probe_volcengine_auto_detect(client, base_url, api_key)
                    if detected:
                        message = f"{probe.get('message') or '方舟任务接口可达'}；但 /api/v3/models 不可用。请按实际方舟控制台模型名称手动填写视频模型。"
                        return volcengine_default_model_payload(status=probe.get('status') or resp.status_code, message=message, raw={'models_error': resp.text[:300], **(probe.get('raw') or {})})
                elif protocol == 'openai':
                    detected, probe = await probe_volcengine_auto_detect(client, base_url, api_key)
                    if detected:
                        message = f"{probe.get('message') or '检测到方舟/Ark 兼容入口'}；OpenAI /v1/models 不可用，已自动切换为方舟协议。请按实际方舟控制台模型名称手动填写视频模型。"
                        return volcengine_default_model_payload(status=probe.get('status') or resp.status_code, message=message, raw={'models_error': resp.text[:300], **(probe.get('raw') or {})})
                return {'ok': False, 'status': resp.status_code, 'message': resp.text[:300]}
            data = resp.json() if resp.text else {}
            grouped, ids = parse_upstream_models(data, protocol)
            grouped, ids = apply_agnes_model_defaults(base_url, grouped, ids)
            grouped = apply_locked_recommended_model_rules(base_url, grouped)
            if protocol == 'volcengine' and (not ids):
                detected, probe = await probe_volcengine_auto_detect(client, base_url, api_key)
                if detected:
                    return volcengine_default_model_payload(status=resp.status_code, raw=data)
            return {'ok': True, 'status': resp.status_code, 'model_count': len(ids), 'image_models': grouped['image'], 'chat_models': grouped['chat'], 'video_models': grouped['video'], 'recommended_models': recommend_upstream_models(grouped), 'all': ids, 'image_request_mode': detect_image_request_mode(base_url, ids) or normalize_image_request_mode(getattr(payload, 'image_request_mode', ''))}
    except httpx.HTTPError as e:
        if protocol == 'volcengine':
            try:
                async with httpx.AsyncClient(timeout=15) as client:
                    detected, probe = await probe_volcengine_auto_detect(client, base_url, api_key)
                    if detected:
                        message = f"{probe.get('message') or '方舟任务接口可达'}；但模型列表请求失败。请按实际方舟控制台模型名称手动填写视频模型。"
                        return volcengine_default_model_payload(status=probe.get('status') or 0, message=message, raw={'models_error': str(e)[:300], **(probe.get('raw') or {})})
            except Exception:
                pass
        return {'ok': False, 'status': 0, 'message': str(e)[:300]}

@router.post('/api/providers/probe-async')
async def probe_async_endpoint(payload: TestConnectionPayload):
    """验证异步协议：用假 task_id 请求 GET /v1/tasks/{fake_id}。
    收到 400 Invalid task ID = 端点存在且 Key 有效；401/403 = Key 无效；404/连接失败 = 不支持异步端点。"""
    base_url = (payload.base_url or '').strip().rstrip('/')
    protocol = protocol_from_payload(payload)
    if protocol == 'codex':
        status = await _app_main.codex_status()
        return {'ok': bool(status.get('installed')), 'protocol': 'codex', 'status_code': 200 if status.get('installed') else 0, 'message': status.get('message') or 'OpenAI Codex CLI 本机检测完成', 'raw': status}
    if protocol == 'gemini-cli':
        status = await _app_main.gemini_cli_status()
        return {'ok': bool(status.get('installed')), 'protocol': 'gemini-cli', 'status_code': 200 if status.get('installed') else 0, 'message': status.get('message') or 'Antigravity CLI 本机检测完成', 'raw': status}
    if not base_url:
        raise HTTPException(status_code=400, detail='请先填写请求地址')
    api_key = api_key_from_payload(payload, protocol)
    if not api_key:
        raise HTTPException(status_code=400, detail='请先填写或保存 API Key')
    if protocol == 'volcengine':
        try:
            async with httpx.AsyncClient(timeout=15) as client:
                task_ok, task_probe = await probe_volcengine_task_endpoint(client, base_url, api_key)
                if task_ok:
                    return {'ok': True, 'protocol': 'volcengine', 'status_code': task_probe.get('status') or 200, 'message': '方舟/Ark 任务协议可用', 'raw': task_probe.get('raw')}
                compat_ok, compat_probe = await probe_openai_compat_bearer_endpoint(client, base_url, api_key)
                if compat_ok:
                    return {'ok': True, 'protocol': 'volcengine', 'status_code': compat_probe.get('status') or 200, 'message': '方舟/Ark Bearer 鉴权入口可用（OpenAI 兼容透传）', 'raw': {'task_probe': task_probe, 'openai_compat_probe': compat_probe.get('raw')}}
                return {'ok': False, 'protocol': 'volcengine', 'status_code': compat_probe.get('status') or task_probe.get('status') or 0, 'message': compat_probe.get('message') or task_probe.get('message') or '方舟/Ark 任务协议不可用', 'raw': {'task_probe': task_probe, 'openai_compat_probe': compat_probe.get('raw')}}
        except httpx.HTTPError as e:
            raise HTTPException(status_code=502, detail=str(e)[:300])
    tasks_base = base_url if base_url.endswith('/v1') else f'{base_url}/v1'
    probe_url = f'{tasks_base}/tasks/healthcheck_probe_do_not_submit'
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.get(probe_url, headers={'Authorization': bearer_auth_value(api_key), 'Accept': 'application/json'})
            try:
                body = resp.json()
            except Exception:
                body = resp.text[:500]
            sc = resp.status_code
            err_msg = ''
            if isinstance(body, dict):
                err = body.get('error') or {}
                if isinstance(err, dict):
                    err_msg = str(err.get('message') or '').lower()
                else:
                    err_msg = str(err).lower()
            if sc == 400 and 'invalid task id' in err_msg:
                return {'ok': True, 'protocol': 'apimart', 'status_code': sc, 'message': 'APIMart 异步任务端点可用，API Key 已通过认证', 'raw': body}
            async_probe = {'status': sc, 'message': '', 'raw': body}
            if sc in (301, 302, 303, 307, 308):
                location = resp.headers.get('Location') or resp.headers.get('location') or ''
                async_probe['message'] = f"/v1/tasks/ 发生跳转{(f'：{location}' if location else '')}"
            elif looks_like_html_response(resp.text):
                async_probe['message'] = '/v1/tasks/ 返回网页 HTML'
            elif sc in (401, 403):
                async_probe['message'] = '/v1/tasks/ 返回鉴权失败'
            elif sc == 404:
                async_probe['message'] = '平台不支持 /v1/tasks/ 端点，可能不是 APIMart 异步协议'
            elif 400 <= sc < 500:
                async_probe['message'] = f'/v1/tasks/ 返回 {sc}'
            elif sc < 300:
                async_probe['message'] = f'/v1/tasks/ 返回 {sc}（意外成功）'
            else:
                async_probe['message'] = f'/v1/tasks/ 服务端错误 {sc}'
            if protocol == 'apimart':
                return {'ok': False, 'protocol': 'apimart', 'status_code': sc, 'message': async_probe['message'], 'raw': body}
            openai_ok, openai_probe = await probe_openai_models_endpoint(client, base_url, api_key)
            if not openai_ok and protocol == 'openai':
                compat_ok, compat_probe = await probe_openai_compat_bearer_endpoint(client, base_url, api_key)
                if compat_ok and (compat_probe.get('status') or 0) != 404:
                    return {'ok': True, 'protocol': 'openai', 'status_code': compat_probe.get('status') or openai_probe.get('status') or sc, 'message': 'OpenAI 兼容入口可达（该站未提供 /v1/models，模型请手动填写）', 'raw': {'async_probe': async_probe, 'openai_probe': openai_probe.get('raw'), 'openai_compat_probe': compat_probe.get('raw')}, 'model_count': 0, 'image_models': [], 'chat_models': [], 'video_models': [], 'all': []}
                detected, volc_probe = await probe_volcengine_auto_detect(client, base_url, api_key)
                if detected:
                    return {'ok': True, 'protocol': 'volcengine', 'status_code': volc_probe.get('status') or openai_probe.get('status') or sc, 'message': f"{volc_probe.get('message') or '检测到方舟/Ark 兼容入口'}，已自动切换为方舟/Ark 任务协议", 'raw': {'async_probe': async_probe, 'openai_probe': openai_probe.get('raw'), **(volc_probe.get('raw') or {})}}
            return {'ok': openai_ok, 'protocol': 'openai', 'status_code': openai_probe.get('status') or sc, 'message': openai_probe.get('message') or 'OpenAI 兼容验证完成', 'raw': {'async_probe': async_probe, 'openai_probe': openai_probe.get('raw')}, 'model_count': openai_probe.get('model_count') or 0, 'image_models': openai_probe.get('image_models') or [], 'chat_models': openai_probe.get('chat_models') or [], 'video_models': openai_probe.get('video_models') or [], 'all': openai_probe.get('all') or [], 'image_request_mode': detect_image_request_mode(base_url, openai_probe.get('all') or []) or normalize_image_request_mode(getattr(payload, 'image_request_mode', ''))}
    except httpx.HTTPError as e:
        raise HTTPException(status_code=502, detail=str(e)[:300])

async def fetch_models_from_upstream(base_url: str, api_key: str, protocol: str='openai', image_request_mode: str='openai'):
    """从上游模型列表端点拉取模型，并按名称做轻量分类。"""
    protocol = protocol if protocol in SUPPORTED_PROVIDER_PROTOCOLS else 'openai'
    if protocol == 'codex':
        status = await _app_main.codex_status()
        payload = codex_models_payload(raw={'status': status})
        payload['message'] = status.get('message') or payload['message']
        return payload
    if protocol == 'gemini-cli':
        status = await _app_main.gemini_cli_status()
        payload = gemini_cli_models_payload(raw={'status': status})
        payload['message'] = status.get('message') or payload['message']
        return payload
    if protocol == 'jimeng':
        return {'total': len(JIMENG_DEFAULT_IMAGE_MODELS) + len(JIMENG_DEFAULT_VIDEO_MODELS), 'image_models': JIMENG_DEFAULT_IMAGE_MODELS, 'chat_models': [], 'video_models': JIMENG_DEFAULT_VIDEO_MODELS, 'all': [*JIMENG_DEFAULT_IMAGE_MODELS, *JIMENG_DEFAULT_VIDEO_MODELS]}
    if protocol == 'runninghub':
        provider = {'id': 'runninghub', 'name': 'RunningHub', 'base_url': base_url or RUNNINGHUB_DEFAULT_BASE_URL, 'protocol': 'runninghub', 'api_key': api_key}
        return await runninghub_models_payload(provider)
    base_url = (base_url or '').strip().rstrip('/')
    if not base_url:
        raise HTTPException(status_code=400, detail='请先填写请求地址')
    if not re.match('^https?://', base_url):
        raise HTTPException(status_code=400, detail='请求地址必须以 http:// 或 https:// 开头')
    api_key = volcengine_provider_api_key(api_key) if protocol == 'volcengine' else (api_key or '').strip()
    if not api_key:
        key_name = '方舟 API Key' if protocol == 'volcengine' else 'API Key'
        raise HTTPException(status_code=400, detail=f'请先填写或保存 {key_name}')
    url = upstream_models_url(base_url, protocol)
    try:
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.get(url, headers=upstream_model_headers(api_key, protocol))
            endpoint_label = '/v1beta/models' if protocol == 'gemini' else '/api/v3/models' if protocol == 'volcengine' else '/openapi/v2/models' if protocol == 'runninghub' else '/v1/models'
            if resp.status_code in (301, 302, 303, 307, 308):
                location = resp.headers.get('Location') or resp.headers.get('location') or ''
                suffix = f'：{location}' if location else ''
                raise HTTPException(status_code=400, detail=f'上游 {endpoint_label} 发生跳转{suffix}，请填写 API Base URL，不要填写网页登录地址')
            if looks_like_html_response(resp.text):
                raise HTTPException(status_code=400, detail=f'上游 {endpoint_label} 返回网页 HTML，请检查请求地址是否为 API Base URL')
            if resp.status_code >= 400:
                if protocol == 'volcengine':
                    detected, probe = await probe_volcengine_auto_detect(client, base_url, api_key)
                    if detected:
                        payload = volcengine_default_model_payload(status=probe.get('status') or resp.status_code, message=f"{probe.get('message') or '方舟任务接口可达'}；但 /api/v3/models 不可用。请按实际方舟控制台模型名称手动填写视频模型。", raw={'models_error': resp.text[:300], **(probe.get('raw') or {})})
                        return {'total': payload['model_count'], 'protocol': payload['protocol'], 'image_models': payload['image_models'], 'chat_models': payload['chat_models'], 'video_models': payload['video_models'], 'all': payload['all'], 'message': payload['message'], 'raw': payload['raw']}
                elif protocol == 'openai':
                    detected, probe = await probe_volcengine_auto_detect(client, base_url, api_key)
                    if detected:
                        payload = volcengine_default_model_payload(status=probe.get('status') or resp.status_code, message=f"{probe.get('message') or '检测到方舟/Ark 兼容入口'}；OpenAI /v1/models 不可用，已自动切换为方舟协议。请按实际方舟控制台模型名称手动填写视频模型。", raw={'models_error': resp.text[:300], **(probe.get('raw') or {})})
                        return {'total': payload['model_count'], 'protocol': payload['protocol'], 'image_models': payload['image_models'], 'chat_models': payload['chat_models'], 'video_models': payload['video_models'], 'all': payload['all'], 'message': payload['message'], 'raw': payload['raw']}
                raise HTTPException(status_code=resp.status_code, detail=f'上游 {endpoint_label} 失败：{resp.text[:300]}')
            raw = resp.json()
    except httpx.HTTPError as e:
        if protocol == 'volcengine':
            try:
                async with httpx.AsyncClient(timeout=15) as client:
                    detected, probe = await probe_volcengine_auto_detect(client, base_url, api_key)
                    if detected:
                        payload = volcengine_default_model_payload(status=probe.get('status') or 0, message=f"{probe.get('message') or '方舟任务接口可达'}；但模型列表请求失败。请按实际方舟控制台模型名称手动填写视频模型。", raw={'models_error': str(e)[:300], **(probe.get('raw') or {})})
                        return {'total': payload['model_count'], 'protocol': payload['protocol'], 'image_models': payload['image_models'], 'chat_models': payload['chat_models'], 'video_models': payload['video_models'], 'all': payload['all'], 'message': payload['message'], 'raw': payload['raw']}
            except Exception:
                pass
        raise HTTPException(status_code=502, detail=f'请求上游模型列表失败：{e}')
    grouped, ids = parse_upstream_models(raw, protocol)
    grouped, ids = apply_agnes_model_defaults(base_url, grouped, ids)
    grouped = apply_locked_recommended_model_rules(base_url, grouped)
    if protocol == 'volcengine' and (not ids):
        payload = volcengine_default_model_payload(raw=raw)
        return {'total': payload['model_count'], 'image_models': payload['image_models'], 'chat_models': payload['chat_models'], 'video_models': payload['video_models'], 'all': payload['all'], 'message': payload['message'], 'raw': payload['raw']}
    return {'total': len(ids), 'image_models': grouped['image'], 'chat_models': grouped['chat'], 'video_models': grouped['video'], 'recommended_models': recommend_upstream_models(grouped), 'all': ids, 'image_request_mode': detect_image_request_mode(base_url, ids) or normalize_image_request_mode(image_request_mode)}

@router.post('/api/providers/fetch-models')
async def fetch_upstream_models_from_payload(payload: TestConnectionPayload):
    """按页面当前表单值拉取模型，支持新增平台未保存时直接使用临时 Base URL / Key。"""
    protocol = protocol_from_payload(payload)
    api_key = api_key_from_payload(payload, protocol)
    return await fetch_models_from_upstream(payload.base_url, api_key, protocol, payload.image_request_mode)

@router.get('/api/providers/{provider_id}/fetch-models')
async def fetch_upstream_models(provider_id: str):
    """从已保存的上游 OpenAI 兼容接口拉取 /v1/models 列表，按名称智能分类为 image/chat/video。"""
    provider = get_api_provider_exact(provider_id)
    if is_codex_provider(provider):
        return await fetch_models_from_upstream('', '', 'codex', provider.get('image_request_mode') or 'openai')
    if is_gemini_cli_provider(provider):
        return await fetch_models_from_upstream('', '', 'gemini-cli', provider.get('image_request_mode') or 'openai')
    api_key = os.getenv(runninghub_wallet_key_env(), '') if provider['id'] == 'runninghub' else ''
    if not api_key:
        api_key = _app_main.provider_env_key_value(provider['id'])
    if not api_key:
        raise HTTPException(status_code=400, detail=f"{provider.get('name') or provider_id} 未配置 API Key")
    return await fetch_models_from_upstream(provider.get('base_url') or '', api_key, provider_protocol(provider), provider.get('image_request_mode') or 'openai')