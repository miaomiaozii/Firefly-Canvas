"""Router: /api/providers domain (auto-split from main.py)."""
from __future__ import annotations
import sys as _sys
import os as _os
import logging

logger = logging.getLogger(__name__)
_app_main = _sys.modules.get(_os.environ.get('FIREFLY_MAIN_MODULE', '')) or _sys.modules.get('main') or _sys.modules['__main__']
import os
import json
import re
import firefly_json_io
from typing import Any, Optional
from fastapi import APIRouter, BackgroundTasks, File, Form, Header, HTTPException, Query, Request, Response, UploadFile, WebSocket, WebSocketDisconnect
from pydantic import BaseModel, Field
from app.core import (AGNES_DEFAULT_VIDEO_MODELS, COMFYUI_DEFAULT_BASE_URL, Any, ApiProviderPayload, BaseModel, CHAT_MODEL, CODEX_DEFAULT_CHAT_MODELS, CODEX_DEFAULT_IMAGE_MODELS, Dict, Field, GEMINI_CLI_DEFAULT_CHAT_MODELS, GEMINI_CLI_DEFAULT_IMAGE_MODELS, GLOBAL_CONFIG_LOCK, HTTPException, IMAGE_MODEL, List, MODELSCOPE_DEFAULT_CHAT_MODELS, Optional, REMOVED_PROVIDER_IDS, RUNNINGHUB_DEFAULT_BASE_URL, RUNNINGHUB_DEFAULT_IMAGE_MODELS, RUNNINGHUB_DEFAULT_VIDEO_MODELS, RUNNINGHUB_FALLBACK_CHAT_MODELS, RUNNINGHUB_THUMBNAIL_EXTS, STATIC_DIR, STATIC_RUNNINGHUB_API_PROVIDERS_FILE, STATIC_RUNNINGHUB_DIR, STATIC_RUNNINGHUB_THUMBNAIL_DIR, SUPPORTED_PROVIDER_PROTOCOLS, TestConnectionPayload, VOLCENGINE_DEFAULT_PROJECT_NAME, VOLCENGINE_DEFAULT_REGION, _os, _sys, apply_agnes_model_defaults, apply_locked_recommended_model_rules, apply_runninghub_system_thumbnails, bearer_auth_value, codex_models_payload, detect_image_request_mode, ensure_same_origin_request, gemini_cli_models_payload, httpx, is_codex_provider, is_gemini_cli_provider, json, load_static_runninghub_provider, locked_recommended_provider_rule, looks_like_html_response, model_list, model_list_from_values, normalize_available_models, normalize_image_request_mode, normalize_provider, normalize_runninghub_entries, normalize_runninghub_entry, openai_compat_root_for_probe, os, parse_upstream_models, preserve_runninghub_hidden_overrides, probe_openai_compat_bearer_endpoint, probe_openai_models_endpoint, probe_volcengine_auto_detect, probe_volcengine_task_endpoint, protocol_from_payload, provider_key_env, provider_protocol, re, recommend_upstream_models, reload_env_globals, runninghub_entry_id, runninghub_model_display_name, runninghub_model_id, runninghub_openapi_url, runninghub_registry_payload, runninghub_wallet_key_env, static_runninghub_thumbnail_url, upstream_model_headers, upstream_models_url, urllib, volcengine_access_key_env, volcengine_default_model_payload, volcengine_secret_key_env, volcengine_task_probe_url)
_from_main_names = ['API_PROVIDERS_FILE', 'DATA_DIR', 'fetch_models_from_upstream', 'fetch_runninghub_model_registry', 'get_api_provider_exact', 'provider_env_key_value', 'public_api_providers', 'public_provider', 'save_api_providers', 'update_env_values']
import app.services as _svc
from app.services.providers import (
    api_key_from_payload,
    load_upstream_direct,
    runninghub_models_payload,
    volcengine_provider_api_key,
)
from app.services.upstream_http import create_upstream_async_client as _create_upstream_async_client
for _nm in _from_main_names:
    _v = getattr(_app_main, _nm, None)
    if _v is None:
        _v = getattr(_svc, _nm, None)
    globals()[_nm] = _v

router = APIRouter()

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
        logger.warning(f'加载 static RunningHub 配置失败: {e}')
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

def save_api_providers(providers):
    providers_file = _app_main.API_PROVIDERS_FILE
    _svc.write_guard.ensure_writable_config(providers_file)
    with GLOBAL_CONFIG_LOCK:
        firefly_json_io.read_global_json(providers_file, list)
        firefly_json_io.write_json_atomic(providers_file, [_encrypt_provider_secrets(item) for item in providers])

def _encrypt_provider_secrets(provider):
    provider = dict(provider or {})
    for key in ('api_key', 'wallet_api_key', 'volcengine_secret_access_key'):
        if key in provider and provider.get(key):
            provider[key] = _svc.secret_store.encrypt_secret(provider[key])
    return provider


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


async def comfyui_test_connection(payload):
    """探测可达性和生图能力，不修改服务商配置（#472）。"""
    api_key = api_key_from_payload(payload, 'comfyui').strip()
    headers = {'Authorization': bearer_auth_value(api_key)} if api_key.lower() not in ('', 'bearer') else {}
    base_url = str(payload.base_url or COMFYUI_DEFAULT_BASE_URL or '').strip().rstrip('/')
    if not base_url:
        raise HTTPException(status_code=400, detail='请先填写服务商请求地址')
    if not re.match('^https?://', base_url):
        raise HTTPException(status_code=400, detail='请求地址必须以 http:// 或 https:// 开头')
    # httpx 会将 URL 中的 userinfo 自动转换为 Basic，不能绕过 Bearer-only 合同。
    if urllib.parse.urlsplit(base_url).username is not None:
        raise HTTPException(status_code=400, detail='请求地址不能包含用户名或密码，请使用 API Key（Bearer）认证。')

    def response_error(response, endpoint):
        status = response.status_code
        # 上游正文和跳转地址都可能回显密钥，诊断只包含端点和状态。
        if status in (401, 403):
            message = f'服务商 {endpoint} 认证失败（HTTP {status}），请检查 Bearer API Key。'
        elif status >= 500:
            message = f'服务商 {endpoint} 服务暂不可用（HTTP {status}）。'
        elif 300 <= status < 400:
            message = f'服务商 {endpoint} 发生跳转（HTTP {status}），请填写服务商 API 地址。'
        elif status >= 400 and not (status == 404 and endpoint.startswith('/object_info/')):
            message = f'服务商 {endpoint} 不可用（HTTP {status}），请检查服务商 API 地址。'
        elif 'text/html' in response.headers.get('content-type', '').lower() or looks_like_html_response(response.text):
            message = f'服务商 {endpoint} 返回 HTML，不是有效的服务商 API 响应。'
        else:
            return None
        return {'ok': False, 'status': status, 'message': message}

    try:
        async with httpx.AsyncClient(timeout=15) as client:
            endpoint = '/system_stats'
            stats = await client.get(f'{base_url}{endpoint}', headers=headers)
            error = response_error(stats, '/system_stats')
            if error:
                return error
            try:
                stats_data = stats.json()
            except ValueError:
                stats_data = None
            if not (isinstance(stats_data, dict) and isinstance(stats_data.get('system'), dict)
                    and isinstance(stats_data.get('devices'), list)):
                return {'ok': False, 'status': stats.status_code, 'message': '服务商 /system_stats 未返回有效的服务商状态 JSON，请检查请求地址。'}
            # 只支持无认证/Bearer，不把上游返回的登录 Cookie 用作后续认证。
            client.cookies.clear()
            endpoint = '/object_info/MiniMaxH3ImageConditioning'
            nodes = await client.get(f'{base_url}{endpoint}', headers=headers)
            error = response_error(nodes, '/object_info/MiniMaxH3ImageConditioning')
            if error:
                return error
    except httpx.ConnectTimeout:
        return {'ok': False, 'status': 0, 'message': '服务商连接超时，请检查地址与网络。'}
    except httpx.ReadTimeout:
        return {'ok': False, 'status': 0, 'message': f'服务商 {endpoint} 读取超时，请稍后重试。'}
    except httpx.ConnectError:
        return {'ok': False, 'status': 0, 'message': '服务商连接失败，请检查地址与网络。'}
    except httpx.HTTPError:
        return {'ok': False, 'status': 0, 'message': '服务商 HTTP 请求失败，请检查服务商 API 服务。'}
    try:
        node_data = nodes.json()
    except ValueError:
        node_data = {}
    node = node_data.get('MiniMaxH3ImageConditioning') if isinstance(node_data, dict) else None
    # 显示名、版本等字段不是能力合同；仅检查节点输入/输出结构。
    valid_node = (isinstance(node, dict) and isinstance(node.get('input'), dict)
                  and all(isinstance(node['input'].get(group, {}), dict) for group in ('required', 'optional'))
                  and isinstance(node.get('output'), list)
                  and all(isinstance(output, str) for output in node['output']))
    if nodes.status_code != 200 or not valid_node:
        return {'ok': False, 'status': nodes.status_code, 'protocol': 'comfyui', 'model_count': 0, 'message': '服务可达，但当前无法生图：未找到有效的 MiniMaxH3ImageConditioning 节点信息。'}
    return {'ok': True, 'status': 200, 'protocol': 'comfyui', 'model_count': 0, 'message': '服务可用，已检测到 MiniMaxH3ImageConditioning 节点。'}


@router.post('/api/providers/test-connection')
async def test_provider_connection(payload: TestConnectionPayload):
    """测试请求地址是否可用：调上游 /v1/models。验证通过时同时把模型清单按类别返回，避免再调一次拉取接口。"""
    protocol = protocol_from_payload(payload)
    if protocol == 'codex':
        status = await _app_main.codex_status_core()
        payload_models = codex_models_payload(raw={'status': status})
        payload_models.update({'ok': bool(status.get('installed')), 'status': 200 if status.get('installed') else 0, 'message': status.get('message') or ('OpenAI Codex CLI 可用' if status.get('installed') else '未找到 OpenAI Codex CLI')})
        return payload_models
    if protocol == 'gemini-cli':
        status = await _app_main.gemini_cli_status_core()
        payload_models = gemini_cli_models_payload(raw={'status': status})
        payload_models.update({'ok': bool(status.get('installed')), 'status': 200 if status.get('installed') else 0, 'message': status.get('message') or ('Antigravity CLI 可用' if status.get('installed') else '未找到 Antigravity CLI')})
        return payload_models
    if protocol == 'runninghub':
        provider = {'id': 'runninghub', 'name': 'RunningHub', 'base_url': (payload.base_url or RUNNINGHUB_DEFAULT_BASE_URL).strip().rstrip('/'), 'protocol': 'runninghub', 'api_key': api_key_from_payload(payload, protocol)}
        payload_models = await runninghub_models_payload(provider)
        return {'ok': True, 'status': 200, 'message': 'RunningHub OpenAPI 可用，已拉取官方直连模型注册表。', 'model_count': payload_models['total'], 'image_models': payload_models['image_models'], 'chat_models': payload_models['chat_models'], 'video_models': payload_models['video_models'], 'all': payload_models['all'], 'protocol': 'runninghub', 'raw': payload_models.get('raw')}
    if protocol == 'comfyui':
        return await comfyui_test_connection(payload)
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
        async with _create_upstream_async_client((url, base_url), direct=load_upstream_direct(), timeout=15) as client:
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
                async with _create_upstream_async_client(base_url, direct=load_upstream_direct(), timeout=15) as client:
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
        status = await _app_main.codex_status_core()
        return {'ok': bool(status.get('installed')), 'protocol': 'codex', 'status_code': 200 if status.get('installed') else 0, 'message': status.get('message') or 'OpenAI Codex CLI 本机检测完成', 'raw': status}
    if protocol == 'gemini-cli':
        status = await _app_main.gemini_cli_status_core()
        return {'ok': bool(status.get('installed')), 'protocol': 'gemini-cli', 'status_code': 200 if status.get('installed') else 0, 'message': status.get('message') or 'Antigravity CLI 本机检测完成', 'raw': status}
    if not base_url:
        raise HTTPException(status_code=400, detail='请先填写请求地址')
    api_key = api_key_from_payload(payload, protocol)
    if not api_key:
        raise HTTPException(status_code=400, detail='请先填写或保存 API Key')
    if protocol == 'volcengine':
        try:
            async with _create_upstream_async_client(base_url, direct=load_upstream_direct(), timeout=15) as client:
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
        async with _create_upstream_async_client(probe_url, direct=load_upstream_direct(), timeout=15) as client:
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
        status = await _app_main.codex_status_core()
        payload = codex_models_payload(raw={'status': status})
        payload['message'] = status.get('message') or payload['message']
        return payload
    if protocol == 'gemini-cli':
        status = await _app_main.gemini_cli_status_core()
        payload = gemini_cli_models_payload(raw={'status': status})
        payload['message'] = status.get('message') or payload['message']
        return payload
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
        async with _create_upstream_async_client((url, base_url), direct=load_upstream_direct(), timeout=30) as client:
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
                async with _create_upstream_async_client(base_url, direct=load_upstream_direct(), timeout=15) as client:
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

FETCH_MODELS_INCOMPLETE_HINT = '模型清单可能不完整，可手动补充模型。'

def merge_fetched_models_with_saved(provider_id: str, fetched: dict) -> dict:
    """#275：拉取结果与已保存服务商清单合并去重，不覆盖手填模型。

    顺序：已保存选择（image_models 等）→ 已保存 available_models → 拉取结果；
    平台未保存时不合并，仅提示清单可能不完整。
    """
    target = (provider_id or '').strip().lower()
    result = dict(fetched or {})
    saved = next((p for p in public_api_providers() if str(p.get('id') or '').strip().lower() == target), None) if target else None
    if saved:
        saved_available = normalize_available_models(saved.get('available_models'))
        for fetched_key in ('image_models', 'chat_models', 'video_models'):
            values = []
            for source in (saved.get(fetched_key) or [], saved_available.get(fetched_key) or [], result.get(fetched_key) or []):
                for value in source:
                    value = str(value or '').strip()
                    if value and value not in values:
                        values.append(value)
            result[fetched_key] = values
        result['recommended_models'] = recommend_upstream_models({'image': result['image_models'], 'chat': result['chat_models'], 'video': result['video_models']})
    base = str(result.get('message') or '').strip()
    result['message'] = (base + ' ' if base else '') + FETCH_MODELS_INCOMPLETE_HINT
    return result

@router.post('/api/providers/fetch-models')
async def fetch_upstream_models_from_payload(payload: TestConnectionPayload):
    """按页面当前表单值拉取模型，支持新增平台未保存时直接使用临时 Base URL / Key。"""
    protocol = protocol_from_payload(payload)
    api_key = api_key_from_payload(payload, protocol)
    fetched = await fetch_models_from_upstream(payload.base_url, api_key, protocol, payload.image_request_mode)
    return merge_fetched_models_with_saved(payload.provider_id, fetched)

@router.get('/api/providers/{provider_id}/fetch-models')
async def fetch_upstream_models(provider_id: str, request: Request):
    """从已保存的上游 OpenAI 兼容接口拉取 /v1/models 列表，按名称智能分类为 image/chat/video。"""
    # #342：本 GET 会 spawn codex/gemini CLI 子进程或对上游发探测请求，属副作用 GET，
    # 要求显式同源头（Origin/Referer）。
    ensure_same_origin_request(request)
    provider = get_api_provider_exact(provider_id)
    if is_codex_provider(provider):
        fetched = await fetch_models_from_upstream('', '', 'codex', provider.get('image_request_mode') or 'openai')
        return merge_fetched_models_with_saved(provider['id'], fetched)
    if is_gemini_cli_provider(provider):
        fetched = await fetch_models_from_upstream('', '', 'gemini-cli', provider.get('image_request_mode') or 'openai')
        return merge_fetched_models_with_saved(provider['id'], fetched)
    api_key = os.getenv(runninghub_wallet_key_env(), '') if provider['id'] == 'runninghub' else ''
    if not api_key:
        api_key = _app_main.provider_env_key_value(provider['id'])
    if not api_key:
        raise HTTPException(status_code=400, detail=f"{provider.get('name') or provider_id} 未配置 API Key")
    fetched = await fetch_models_from_upstream(provider.get('base_url') or '', api_key, provider_protocol(provider), provider.get('image_request_mode') or 'openai')
    return merge_fetched_models_with_saved(provider['id'], fetched)