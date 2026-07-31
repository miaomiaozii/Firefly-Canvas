# -*- coding: utf-8 -*-
"""Firefly Canvas service layer: pure business functions extracted from main.py (W1c step A).

Injected at runtime from main (dynamic state):
  API_ENV_FILE, ASSET_CLASSIFICATION_PROMPT_FILE, ASSET_LIBRARY_DIR, LOCAL_UPLOAD_DIR, PROJECT_AGENT_TASK_TIMEOUT_SECONDS, PROJECT_CONVERSATION_IMAGE_MAX_PIXELS, PROJECT_CONVERSATION_MEDIA_MAX_FILE_BYTES, PROJECT_VIDEO_REFERENCE_INLINE_MAX_BYTES, SHARED_FOLDERS_FILE, _agent_update_plan, _photoshop_prune_requests_locked, agent_image_adapter, agent_video_adapter, api_headers, attachment_embedded_image_data_urls, attachment_text_blocks, build_online_image_result, canvas_assets_index, canvas_llm, canvas_path, canvas_video, caption_image_with_provider, cleanup_expired_canvas_trash, cleanup_project_generation_orphans, codex_output_image_files, codex_output_url_from_path, codex_prepare_local_media, codex_status, conversation_path, conversation_pending_task_keys, convert_output_to_jpg, delete_media_preview_cache, ensure_runtime_config_files, execute_canvas_video, export_project_package, fetch_image_task_payload, fetch_runninghub_model_registry, forget_project_conversation_durable, gemini_cli_status, generate_ai_image, generate_codex_provider_image_via_gpt_image_2_skill, generate_gemini_cli_provider_image, generated_media_path_from_url, get_api_provider_exact, get_primary_provider_id, image_task_url_for_provider, iter_canvas_records, jimeng_local_output_url, jimeng_pending_exception_handler, jimeng_prepare_local_media, jimeng_query_result, jimeng_store_outputs, json_references_media_path, list_projects, load_api_providers, load_asset_library, load_global_preferences, load_projects, load_prompt_libraries, load_storage_settings, local_asset_public_url, local_media_adapter_path, local_media_file_by_basename, local_media_path_for_cloud_upload, local_media_path_from_url, media_preview_cache_paths, merge_project_conversation_result, now_ms, output_file_from_url, output_storage, output_url_for, persist_project_conversation_pending, persisted_json_references_media_path, photoshop_connection_status, project_conversation_durable_path, project_conversation_media_budget, project_has_active_conversation, project_library_status, project_media_adapter_path, project_store_call, project_video_candidate_execution_incompatible_parameters, project_video_references, protect_project_library_origin, provider_endpoint_url, prune_generation_history_for_media, public_base_url, request_validation_exception_handler, reserve_project_media_import, resolve_chat_provider, run_canvas_image_task, run_codex_cli, run_project_agent_task, run_project_image_task, run_project_video_task, runninghub_local_asset_path, runninghub_model_definition, runninghub_models_payload, runninghub_upload_local_to_filename, save_api_providers, save_asset_classification_prompt, save_asset_library, save_canvas, save_conversation, save_global_preferences, save_projects, save_prompt_libraries, save_remote_video_to_output, save_storage_settings, save_to_history, schedule_project_conversation_task, shared_folders_save, shutdown_event, smart_group_export_folder, startup_event, storage_kind_dir, switch_project_library, update_env_values, upload_audio_for_apimart, upload_image_for_apimart, upload_video_for_apimart, user_dir, video_adapter_incompatible_parameters, video_api_root, video_reference_to_frame_data_urls
"""
from __future__ import annotations

from __future__ import annotations
import sys as _sys
import os as _os
import json
import copy
import uuid
import base64
import binascii
import hashlib
import hmac
import datetime
import urllib.request
import urllib.parse
import urllib.error
import os
import re
import random
import sys
import subprocess
import time
import traceback
import shutil
import glob
import asyncio
import logging
import requests
import zipfile
import mimetypes
import tempfile
import math
import shlex
import functools
import html
import inspect
import ipaddress
import socket
import xml.etree.ElementTree as ET
from contextlib import nullcontext
from concurrent.futures import Future, ThreadPoolExecutor
from contextvars import ContextVar
from typing import List, Dict, Any, Mapping, Optional, Tuple
from threading import Lock, RLock, Thread
import httpx
from PIL import Image, ImageOps
from io import BytesIO
from pathlib import Path
from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect, UploadFile, File, Form, Header, Request
from fastapi.exceptions import RequestValidationError
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, Response, StreamingResponse, JSONResponse
from starlette.background import BackgroundTask
from starlette.datastructures import UploadFile as StarletteUploadFile
from starlette.formparsers import MultiPartException, MultiPartParser
from pydantic import BaseModel, Field
from fastapi.middleware.cors import CORSMiddleware
from firefly_projects import InvalidMutation, ProjectNotFound, ProjectStore, ProjectStoreError, RevisionConflict
from firefly_generation import ImageGenerationRequest, ModelRoutingError, VIDEO_EXECUTION_PROTOCOLS, VideoGenerationRequest, apply_angle_preset, normalize_image_model_capabilities, normalize_video_model_capabilities, route_image_generation, route_video_generation
from local_edit import as_rgba, composite_preserve, composite_rect, encode_png, rasterize_preserve_mask, selection_json

from app.core import *
from app.core import (_DOUBLE_EXT_MEDIA, _DOUBLE_EXT_RE, _local_upload_display_name, _local_upload_folder_node, _local_upload_kind_ext, _local_upload_rel_path, _photoshop_protocol_error, _photoshop_public_request, _safe_asset_tag, _sniff_image_ext)

_main_bindings = None

def _bind_main(mod):
    global _main_bindings
    _main_bindings = mod
    for _n in _DYN_NAMES:
        globals()[_n] = getattr(mod, _n, None)

_DYN_NAMES = ('API_ENV_FILE', 'ASSET_CLASSIFICATION_PROMPT_FILE', 'ASSET_LIBRARY_DIR', 'LOCAL_UPLOAD_DIR', 'PROJECT_AGENT_TASK_TIMEOUT_SECONDS', 'PROJECT_CONVERSATION_IMAGE_MAX_PIXELS', 'PROJECT_CONVERSATION_MEDIA_MAX_FILE_BYTES', 'PROJECT_VIDEO_REFERENCE_INLINE_MAX_BYTES', 'SHARED_FOLDERS_FILE', '_agent_update_plan', '_photoshop_prune_requests_locked', 'agent_image_adapter', 'agent_video_adapter', 'api_headers', 'attachment_embedded_image_data_urls', 'attachment_text_blocks', 'build_online_image_result', 'canvas_assets_index', 'canvas_llm', 'canvas_path', 'canvas_video', 'caption_image_with_provider', 'cleanup_expired_canvas_trash', 'cleanup_project_generation_orphans', 'codex_output_image_files', 'codex_output_url_from_path', 'codex_prepare_local_media', 'codex_status', 'conversation_path', 'conversation_pending_task_keys', 'convert_output_to_jpg', 'delete_media_preview_cache', 'ensure_runtime_config_files', 'execute_canvas_video', 'export_project_package', 'fetch_image_task_payload', 'fetch_runninghub_model_registry', 'forget_project_conversation_durable', 'gemini_cli_status', 'generate_ai_image', 'generate_codex_provider_image_via_gpt_image_2_skill', 'generate_gemini_cli_provider_image', 'generated_media_path_from_url', 'get_api_provider_exact', 'get_primary_provider_id', 'image_task_url_for_provider', 'iter_canvas_records', 'jimeng_local_output_url', 'jimeng_pending_exception_handler', 'jimeng_prepare_local_media', 'jimeng_query_result', 'jimeng_store_outputs', 'json_references_media_path', 'list_projects', 'load_api_providers', 'load_asset_library', 'load_global_preferences', 'load_projects', 'load_prompt_libraries', 'load_storage_settings', 'local_asset_public_url', 'local_media_adapter_path', 'local_media_file_by_basename', 'local_media_path_for_cloud_upload', 'local_media_path_from_url', 'media_preview_cache_paths', 'merge_project_conversation_result', 'now_ms', 'output_file_from_url', 'output_storage', 'output_url_for', 'persist_project_conversation_pending', 'persisted_json_references_media_path', 'photoshop_connection_status', 'project_conversation_durable_path', 'project_conversation_media_budget', 'project_has_active_conversation', 'project_library_status', 'project_media_adapter_path', 'project_store_call', 'project_video_candidate_execution_incompatible_parameters', 'project_video_references', 'protect_project_library_origin', 'provider_endpoint_url', 'prune_generation_history_for_media', 'public_base_url', 'request_validation_exception_handler', 'reserve_project_media_import', 'resolve_chat_provider', 'run_canvas_image_task', 'run_codex_cli', 'run_project_agent_task', 'run_project_image_task', 'run_project_video_task', 'runninghub_local_asset_path', 'runninghub_model_definition', 'runninghub_models_payload', 'runninghub_upload_local_to_filename', 'save_api_providers', 'save_asset_classification_prompt', 'save_asset_library', 'save_canvas', 'save_conversation', 'save_global_preferences', 'save_projects', 'save_prompt_libraries', 'save_remote_video_to_output', 'save_storage_settings', 'save_to_history', 'schedule_project_conversation_task', 'shared_folders_save', 'shutdown_event', 'smart_group_export_folder', 'startup_event', 'storage_kind_dir', 'switch_project_library', 'update_env_values', 'upload_audio_for_apimart', 'upload_image_for_apimart', 'upload_video_for_apimart', 'user_dir', 'video_adapter_incompatible_parameters', 'video_api_root', 'video_reference_to_frame_data_urls')

def _main(name):
    return getattr(_main_bindings, name)

lib = None  # dead-code placeholder (unreachable return)

def read_api_env_value(key: str) -> str:
    key = str(key or '').strip()
    if not key or not os.path.exists(_main('API_ENV_FILE')):
        return ''
    try:
        with open(_main('API_ENV_FILE'), 'r', encoding='utf-8-sig') as f:
            for raw_line in f.read().splitlines():
                line = raw_line.strip()
                if not line or line.startswith('#') or '=' not in line:
                    continue
                env_key, value = line.split('=', 1)
                if env_key.strip() == key:
                    return value.strip().strip('"').strip("'")
    except Exception:
        return ''
    return ''

def provider_env_key_value(provider_id: str) -> str:
    provider_id = str(provider_id or '').strip().lower()
    env_key = provider_key_env(provider_id)
    key = os.getenv(env_key, '') or read_api_env_value(env_key)
    if key:
        return key
    if provider_id == 'modelscope':
        return MODELSCOPE_API_KEY or ''
    return ''

def runninghub_wallet_key_value() -> str:
    env_key = runninghub_wallet_key_env()
    return os.getenv(env_key, '') or read_api_env_value(env_key)

def volcengine_access_key_value() -> str:
    env_key = volcengine_access_key_env()
    return os.getenv(env_key, '') or read_api_env_value(env_key)

def volcengine_secret_key_value() -> str:
    env_key = volcengine_secret_key_env()
    return os.getenv(env_key, '') or read_api_env_value(env_key)

def volcengine_provider_api_key(explicit_key: str='') -> str:
    explicit_key = str(explicit_key or '').strip()
    if explicit_key:
        return explicit_key
    return provider_env_key_value('volcengine')

def public_provider(provider):
    provider = dict(provider or {})
    key = provider_env_key_value(provider['id'])
    item = {**provider, 'has_key': bool(key), 'key_preview': mask_secret(key), 'key_env': provider_key_env(provider['id'])}
    if provider.get('id') == 'runninghub':
        wallet_key = runninghub_wallet_key_value()
        item.update({'has_wallet_key': bool(wallet_key), 'wallet_key_preview': mask_secret(wallet_key), 'wallet_key_env': runninghub_wallet_key_env()})
    if provider.get('id') == 'volcengine':
        ak = volcengine_access_key_value()
        sk = volcengine_secret_key_value()
        item.update({'has_volcengine_access_key': bool(ak), 'volcengine_access_key_preview': mask_secret(ak), 'volcengine_access_key_env': volcengine_access_key_env(), 'has_volcengine_secret_key': bool(sk), 'volcengine_secret_key_preview': mask_secret(sk), 'volcengine_secret_key_env': volcengine_secret_key_env(), 'volcengine_project_name': provider.get('volcengine_project_name') or VOLCENGINE_DEFAULT_PROJECT_NAME, 'volcengine_region': provider.get('volcengine_region') or VOLCENGINE_DEFAULT_REGION})
    return item

def modelscope_provider_config():
    return _main('get_api_provider_exact')('modelscope')

def modelscope_api_key(explicit_key: str=''):
    return strip_auth_scheme(explicit_key, 'Bearer') or strip_auth_scheme(provider_env_key_value('modelscope'), 'Bearer') or strip_auth_scheme(MODELSCOPE_API_KEY, 'Bearer')

def modelscope_api_root(provider=None):
    provider = provider or modelscope_provider_config()
    base_root = str((provider or {}).get('base_url') or MODELSCOPE_CHAT_BASE_URL).strip().rstrip('/')
    if not base_root:
        base_root = MODELSCOPE_CHAT_BASE_URL
    return base_root if base_root.endswith('/v1') else f'{base_root}/v1'

def _update_env_values_unlocked(updates):
    os.makedirs(os.path.dirname(_main('API_ENV_FILE')), exist_ok=True)
    lines = []
    if os.path.exists(_main('API_ENV_FILE')):
        with open(_main('API_ENV_FILE'), 'r', encoding='utf-8-sig') as f:
            lines = f.read().splitlines()
    seen = set()
    next_lines = []
    for line in lines:
        stripped = line.strip()
        if not stripped or stripped.startswith('#') or '=' not in line:
            next_lines.append(line)
            continue
        key = line.split('=', 1)[0].strip()
        if key in updates:
            next_lines.append(f'{key}={env_quote(updates[key])}')
            seen.add(key)
        else:
            next_lines.append(line)
    for key, value in updates.items():
        if key not in seen:
            next_lines.append(f'{key}={env_quote(value)}')
    content = '\n'.join(next_lines).rstrip() + '\n'
    temporary = f"{_main('API_ENV_FILE')}.{uuid.uuid4().hex}.tmp"
    try:
        with open(temporary, 'w', encoding='utf-8', newline='\n') as f:
            f.write(content)
            f.flush()
            os.fsync(f.fileno())
        os.replace(temporary, _main('API_ENV_FILE'))
        for key, value in updates.items():
            os.environ[key] = str(value or '')
    finally:
        try:
            os.remove(temporary)
        except FileNotFoundError:
            pass

def sync_static_html_versions():
    return None

def load_conversation(user_id, conversation_id):
    path = _main('conversation_path')(user_id, conversation_id)
    if not os.path.exists(path):
        raise HTTPException(status_code=404, detail='对话不存在')
    with open(path, 'r', encoding='utf-8') as f:
        return json.load(f)

def codex_env_value(key):
    return os.getenv(key, '') or read_api_env_value(key)

def codex_cli_executable():
    configured = str(codex_env_value('CODEX_BIN') or '').strip()
    if configured:
        return configured
    return shutil.which('codex') or shutil.which('codex.exe') or shutil.which('codex.cmd') or ''

def gpt_image_2_skill_executable():
    configured = str(codex_env_value('GPT_IMAGE_2_SKILL_BIN') or '').strip()
    if configured:
        return configured
    return shutil.which('gpt-image-2-skill') or shutil.which('gpt-image-2-skill.exe') or shutil.which('gpt-image-2-skill.cmd') or ''

def gpt_image_2_skill_auth_file():
    configured = str(codex_env_value('GPT_IMAGE_2_SKILL_AUTH_FILE') or codex_env_value('CODEX_AUTH_FILE') or '').strip()
    if configured:
        return configured
    project_auth = os.path.join(BASE_DIR, 'API', 'openai-gpt-account-auth.json')
    user_profile = os.getenv('USERPROFILE', '').strip()
    candidates = [project_auth, os.path.join(user_profile, '.codex', 'auth.json') if user_profile else '', os.path.join(os.path.expanduser('~'), '.codex', 'auth.json')]
    for path in candidates:
        if path and os.path.isfile(path):
            return path
    return candidates[0] if candidates and candidates[0] else ''

def gpt_image_2_skill_api_key(auth_data=None):
    for key in ('GPT_IMAGE_2_SKILL_API_KEY', 'OPENAI_API_KEY'):
        value = str(codex_env_value(key) or '').strip()
        if value:
            return value
    if isinstance(auth_data, dict):
        value = str(auth_data.get('OPENAI_API_KEY') or auth_data.get('api_key') or auth_data.get('apiKey') or '').strip()
        if value:
            return value
    user_profile = os.getenv('USERPROFILE', '').strip()
    user_auth = os.path.join(user_profile, '.codex', 'auth.json') if user_profile else ''
    if user_auth:
        user_data = gpt_image_2_skill_auth_json(user_auth)
        value = str(user_data.get('OPENAI_API_KEY') or user_data.get('api_key') or user_data.get('apiKey') or '').strip()
        if value:
            return value
    return ''

def gpt_image_2_skill_provider_args(auth_file=''):
    auth_data = gpt_image_2_skill_auth_json(auth_file)
    if gpt_image_2_skill_access_token(auth_data):
        return (['--provider', 'codex', '--auth-file', auth_file] if auth_file else ['--provider', 'codex'], 'codex')
    api_key = gpt_image_2_skill_api_key(auth_data)
    if api_key:
        return (['--provider', 'openai', '--api-key', api_key], 'openai')
    return (['--provider', 'codex', '--auth-file', auth_file] if auth_file else ['--provider', 'codex'], 'codex')

async def codex_reference_paths(reference_images=None):
    paths = []
    temp_paths = []
    try:
        for ref in (reference_images or [])[:ONLINE_IMAGE_REFERENCE_MAX]:
            url = ref.get('url') if isinstance(ref, dict) else getattr(ref, 'url', '')
            if not url:
                continue
            path, created = await _main('codex_prepare_local_media')(url)
            if path:
                paths.append(path)
            temp_paths.extend(created)
        return (paths, temp_paths)
    except Exception:
        for path in temp_paths:
            try:
                os.remove(path)
            except Exception:
                pass
        raise

def gemini_cli_env_value(key):
    return os.getenv(key, '') or read_api_env_value(key)

def gemini_cli_executable():
    for key in ('ANTIGRAVITY_BIN', 'AGY_BIN', 'GEMINI_BIN'):
        configured = str(gemini_cli_env_value(key) or '').strip().strip('"')
        if configured:
            return configured
    for name in ('agy', 'agy.exe'):
        found = shutil.which(name)
        if found:
            return found
    for candidate in antigravity_cli_winget_candidates():
        return candidate
    return shutil.which('gemini') or shutil.which('gemini.exe') or shutil.which('gemini.cmd') or ''

def gemini_cli_display_name(exe=None):
    return 'Antigravity CLI' if is_antigravity_cli(exe or gemini_cli_executable()) else 'Gemini CLI'

async def run_gemini_cli(prompt, model='', timeout=None, allow_tools=False):
    exe = gemini_cli_executable()
    if not exe:
        raise HTTPException(status_code=400, detail='未找到 Antigravity CLI。请先安装 Google Antigravity CLI，并完成 agy 登录。')
    timeout_seconds = timeout or gemini_cli_timeout()
    if is_antigravity_cli(exe):
        args = [exe, '--print-timeout', f'{int(timeout_seconds)}s']
        selected = gemini_cli_model(model)
        if selected and selected != 'auto':
            args.extend(['--model', selected])
        if allow_tools:
            args.append('--dangerously-skip-permissions')
        args.extend(['-p', str(prompt or '')])
    else:
        args = [exe, '--model', gemini_cli_model(model), '--output-format', 'json', '--skip-trust']
        if allow_tools:
            args.extend(['--approval-mode', 'yolo'])
        args.extend(['--prompt', str(prompt or '')])
    proc = None
    try:
        proc = await asyncio.create_subprocess_exec(*args, cwd=BASE_DIR, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout_seconds)
    except asyncio.TimeoutError as exc:
        if proc and proc.returncode is None:
            try:
                proc.kill()
                await proc.wait()
            except Exception:
                pass
        raise HTTPException(status_code=504, detail=f'{gemini_cli_display_name(exe)} 执行超时。可设置 GEMINI_CLI_TIMEOUT 增大等待时间。') from exc
    except FileNotFoundError as exc:
        raise HTTPException(status_code=400, detail=f'未找到 {gemini_cli_display_name(exe)}：{exe}') from exc
    out_text, err_text = codex_decode_output(stdout, stderr)
    raw, text = gemini_cli_parse_stdout(out_text)
    if proc.returncode != 0:
        message = err_text or out_text or f'exit={proc.returncode}'
        raise HTTPException(status_code=502, detail=f'{gemini_cli_display_name(exe)} 调用失败：{message[:1200]}')
    return {'text': text or out_text, 'raw': raw, '_stdout': out_text, '_stderr': err_text}

async def gemini_cli_reference_paths(reference_images=None):
    return await codex_reference_paths(reference_images)

async def gemini_cli_chat_text(payload, history_messages=None):
    temp_paths = []
    try:
        prompt, image_values = gemini_cli_chat_prompt(payload, history_messages)
        image_paths, temp_paths = await gemini_cli_reference_paths(image_values)
        if image_paths:
            prompt = f'{prompt}\n\n可参考的本地图片路径：\n' + '\n'.join(image_paths)
        prompt = f'{prompt}\n\n请直接回答用户，输出纯文本，不要修改项目文件。'
        raw = await run_gemini_cli(prompt, model=getattr(payload, 'model', '') or GEMINI_CLI_DEFAULT_CHAT_MODELS[0], timeout=gemini_cli_timeout(), allow_tools=False)
        text = str(raw.get('text') or '').strip()
        return (text or f'{gemini_cli_display_name()} 返回了空回复。', raw)
    finally:
        for path in temp_paths:
            try:
                os.remove(path)
            except Exception:
                pass

def jimeng_env_value(key):
    return os.getenv(key, '') or read_api_env_value(key)

def jimeng_use_wsl():
    value = str(jimeng_env_value('JIMENG_USE_WSL') or '').strip().lower()
    return value in {'1', 'true', 'yes', 'on', 'wsl'}

def jimeng_cli_executable():
    if jimeng_use_wsl():
        return shutil.which('wsl.exe') or shutil.which('wsl') or 'wsl.exe'
    configured = str(jimeng_env_value('JIMENG_BIN') or jimeng_env_value('DREAMINA_BIN') or '').strip()
    if configured:
        return configured
    return shutil.which('dreamina') or shutil.which('dreamina.exe') or shutil.which('dreamina.cmd') or ''

def jimeng_wsl_base_args(exe='wsl.exe'):
    configured = str(jimeng_env_value('JIMENG_WSL_DISTRO') or '').strip()
    names = []
    try:
        proc = subprocess.run([exe, '-l', '-q'], cwd=BASE_DIR, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, timeout=5, check=False)
        names = [line.replace('\x00', '').strip().lstrip('*').strip() for line in decode_wsl_output(proc.stdout).splitlines() if line.replace('\x00', '').strip()]
    except Exception:
        names = []
    if configured and (not names or configured in names):
        return ['-d', configured]
    if configured and names:
        print(f'JIMENG_WSL_DISTRO={configured} 不存在，已回退自动选择。可用发行版：{names}')
    try:
        ubuntu = next((name for name in names if re.match('^Ubuntu($|-)', name)), '')
        if ubuntu:
            return ['-d', ubuntu]
    except Exception:
        pass
    return []

def jimeng_cli_path_arg(path):
    return windows_path_to_wsl(path) if jimeng_use_wsl() else path

async def run_jimeng_cli(args, timeout=120, raw_text=False):
    exe = jimeng_cli_executable()
    if not exe:
        raise HTTPException(status_code=400, detail='未找到 dreamina CLI。请先安装：curl -fsSL https://jimeng.jianying.com/cli | bash，并完成 dreamina login。')
    clean_args = [str(arg) for arg in args if str(arg) != '']
    command = jimeng_command(clean_args, exe)
    try:
        proc = await asyncio.create_subprocess_exec(*command, cwd=BASE_DIR, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)
    except asyncio.TimeoutError as exc:
        raise HTTPException(status_code=504, detail=f"即梦 CLI 执行超时：{' '.join(command[:3])}") from exc
    except FileNotFoundError as exc:
        raise HTTPException(status_code=400, detail=f'未找到即梦 CLI：{exe}') from exc
    out_text, clean_err_text = jimeng_decode_cli_output(stdout, stderr)
    if proc.returncode != 0:
        message = clean_err_text or out_text or f'exit={proc.returncode}'
        raise HTTPException(status_code=502, detail=f'即梦 CLI 调用失败：{message[:1000]}')
    if raw_text:
        return {'_stdout': out_text, '_stderr': clean_err_text}
    raw = jimeng_extract_json(f'{out_text}\n{clean_err_text}'.strip())
    if isinstance(raw, dict):
        raw.setdefault('_stdout', out_text)
        if clean_err_text:
            raw.setdefault('_stderr', clean_err_text)
    return raw

async def jimeng_cli_version():
    for flag in ('--version', '-V', 'version'):
        try:
            raw = await run_jimeng_cli([flag], timeout=15)
        except HTTPException:
            continue
        text = raw if isinstance(raw, str) else raw.get('_stdout') or raw.get('_stderr') or '' if isinstance(raw, dict) else ''
        version = jimeng_parse_version(text)
        if version:
            return (version, str(text).strip())
    return (None, '')

def jimeng_command(clean_args, exe=None):
    exe = exe or jimeng_cli_executable()
    if jimeng_use_wsl():
        shell_line = '. ~/.profile >/dev/null 2>&1 || true; . ~/.bashrc >/dev/null 2>&1 || true; DREAMINA_BIN=$(command -v dreamina || find "$HOME" -maxdepth 4 -type f -name dreamina 2>/dev/null | head -n 1); if [ -z "$DREAMINA_BIN" ]; then echo \'dreamina CLI not found in WSL\' >&2; exit 127; fi; "$DREAMINA_BIN" ' + ' '.join((shlex.quote(arg) for arg in clean_args))
        return [exe, *jimeng_wsl_base_args(exe), '-e', 'sh', '-lc', shell_line]
    return [exe, *clean_args]

def jimeng_decode_cli_output(stdout, stderr):
    out_text = (decode_wsl_output(stdout) if jimeng_use_wsl() else stdout.decode('utf-8', errors='replace')).strip()
    err_text = (decode_wsl_output(stderr) if jimeng_use_wsl() else stderr.decode('utf-8', errors='replace')).strip()
    clean_err_text = jimeng_clean_wsl_stderr(err_text) if jimeng_use_wsl() else err_text
    return (out_text, clean_err_text)

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

async def generate_jimeng_provider_image(prompt, size, model, reference_images=None, provider=None):
    refs = [ref for ref in reference_images or [] if ref.get('url')]
    temp_paths = []
    try:
        args = []
        if refs:
            image_paths = []
            for ref in refs[:10]:
                image_path, created = await _main('jimeng_prepare_local_media')(ref.get('url'), 'image')
                image_paths.append(jimeng_cli_path_arg(image_path))
                temp_paths.extend(created)
            model_version = jimeng_image_model_version(model, 'image2image')
            args = ['image2image', f"--images={','.join(image_paths)}", f'--prompt={prompt}', f"--resolution_type={jimeng_image_resolution(model, size, 'image2image')}", f'--poll={jimeng_poll_seconds()}']
            if model_version:
                args.append(f'--model_version={model_version}')
        else:
            model_version = jimeng_image_model_version(model, 'text2image')
            args = ['text2image', f'--prompt={prompt}', f'--ratio={jimeng_ratio_from_size(size)}', f"--resolution_type={jimeng_image_resolution(model, size, 'text2image')}", f'--poll={jimeng_poll_seconds()}']
            if model_version:
                args.append(f'--model_version={model_version}')
        raw = await run_jimeng_cli(args, timeout=jimeng_poll_seconds() + 120)
        urls = await _main('jimeng_store_outputs')(raw, 'image')
        return ({'type': 'url', 'value': urls[0]}, raw)
    finally:
        for path in temp_paths:
            try:
                os.remove(path)
            except Exception:
                pass

async def generate_jimeng_video(payload: CanvasVideoRequest, provider):
    image_refs = [ref for ref in payload.images or [] if jimeng_video_ref_url(ref)]
    video_refs = [url for url in payload.videos or [] if str(url or '').strip()]
    audio_refs = [url for url in payload.audios or [] if str(url or '').strip()][:3]
    duration = jimeng_video_duration(payload.duration, payload.model)
    temp_paths = []
    try:
        if payload.multimodal or video_refs or audio_refs:
            image_paths = []
            video_paths = []
            audio_paths = []
            for ref in image_refs[:9]:
                image_path, created = await _main('jimeng_prepare_local_media')(jimeng_video_ref_url(ref), 'image')
                temp_paths.extend(created)
                image_paths.append(image_path)
            for ref_url in video_refs[:3]:
                video_path, created = await _main('jimeng_prepare_local_media')(ref_url, 'video')
                temp_paths.extend(created)
                video_paths.append(video_path)
            for ref_url in audio_refs:
                audio_path, created = await _main('jimeng_prepare_local_media')(ref_url, 'audio')
                temp_paths.extend(created)
                audio_paths.append(audio_path)
            args = ['multimodal2video', f'--prompt={payload.prompt}', f'--duration={duration}', f'--poll={jimeng_poll_seconds()}']
            ratio = jimeng_video_ratio_arg(payload.aspect_ratio)
            if ratio:
                args.append(f'--ratio={ratio}')
            jimeng_append_model_resolution_args(args, payload, include_model=True)
            for image_path in image_paths:
                args.append(f'--image={jimeng_cli_path_arg(image_path)}')
            for video_path in video_paths:
                args.append(f'--video={jimeng_cli_path_arg(video_path)}')
            for audio_path in audio_paths:
                args.append(f'--audio={jimeng_cli_path_arg(audio_path)}')
        elif len(image_refs) >= 2:
            first_frame = next((ref for ref in image_refs if jimeng_video_ref_role(ref) == 'first_frame'), None)
            last_frame = next((ref for ref in image_refs if jimeng_video_ref_role(ref) == 'last_frame'), None)
            if first_frame and last_frame:
                first_path, created = await _main('jimeng_prepare_local_media')(jimeng_video_ref_url(first_frame), 'image')
                temp_paths.extend(created)
                last_path, created = await _main('jimeng_prepare_local_media')(jimeng_video_ref_url(last_frame), 'image')
                temp_paths.extend(created)
                args = ['frames2video', f'--first={jimeng_cli_path_arg(first_path)}', f'--last={jimeng_cli_path_arg(last_path)}', f'--prompt={payload.prompt}', f'--duration={duration}', f'--poll={jimeng_poll_seconds()}']
                jimeng_append_model_resolution_args(args, payload, include_model=True)
            else:
                image_paths = []
                for ref in image_refs:
                    image_path, created = await _main('jimeng_prepare_local_media')(jimeng_video_ref_url(ref), 'image')
                    temp_paths.extend(created)
                    image_paths.append(image_path)
                args = ['multiframe2video', f"--images={','.join((jimeng_cli_path_arg(path) for path in image_paths))}", f'--prompt={payload.prompt}', f'--duration={duration}', f'--poll={jimeng_poll_seconds()}']
                jimeng_append_model_resolution_args(args, payload, include_model=True)
        elif image_refs:
            image_path, created = await _main('jimeng_prepare_local_media')(jimeng_video_ref_url(image_refs[0]), 'image')
            temp_paths.extend(created)
            ratio = jimeng_video_ratio_arg(payload.aspect_ratio)
            if ratio:
                args = ['multimodal2video', f'--image={jimeng_cli_path_arg(image_path)}', f'--prompt={payload.prompt}', f'--duration={duration}', f'--ratio={ratio}', f'--poll={jimeng_poll_seconds()}']
                jimeng_append_model_resolution_args(args, payload, include_model=True)
            else:
                args = ['image2video', f'--image={jimeng_cli_path_arg(image_path)}', f'--prompt={payload.prompt}', f'--duration={duration}', f'--poll={jimeng_poll_seconds()}']
                jimeng_append_model_resolution_args(args, payload, include_model=True)
        else:
            args = ['text2video', f'--prompt={payload.prompt}', f'--duration={duration}', f"--ratio={payload.aspect_ratio or '16:9'}", f'--video_resolution={jimeng_video_resolution(payload.model, payload.resolution)}', f'--poll={jimeng_poll_seconds()}']
            model_version = jimeng_video_model_version(payload.model)
            if model_version:
                args.append(f'--model_version={model_version}')
        raw = await run_jimeng_cli(args, timeout=jimeng_poll_seconds() + 180)
        urls = await _main('jimeng_store_outputs')(raw, 'video')
        return {'videos': urls, 'task_id': jimeng_submit_id(raw) or None, 'raw': raw}
    finally:
        for path in temp_paths:
            try:
                os.remove(path)
            except Exception:
                pass

async def wait_for_image_task(client, task_id, provider=None):
    is_apimart = is_apimart_provider(provider)
    timeout = APIMART_IMAGE_TASK_TIMEOUT if is_apimart else IMAGE_TASK_TIMEOUT
    interval = APIMART_IMAGE_POLL_INTERVAL if is_apimart else IMAGE_POLL_INTERVAL
    initial_delay = APIMART_IMAGE_INITIAL_POLL_DELAY if is_apimart else 0
    deadline = time.monotonic() + timeout
    last_payload = {}
    while time.monotonic() < deadline:
        if initial_delay:
            await asyncio.sleep(min(initial_delay, max(0.0, deadline - time.monotonic())))
            initial_delay = 0
            if time.monotonic() >= deadline:
                break
        last_payload = await _main('fetch_image_task_payload')(client, task_id, provider)
        status = image_task_status(last_payload)
        if not status:
            try:
                if extract_image(last_payload):
                    return last_payload
            except HTTPException:
                pass
        if status in IMAGE_TASK_SUCCESS_STATUSES:
            return last_payload
        if status in IMAGE_TASK_FAILED_STATUSES:
            raise HTTPException(status_code=502, detail=f'生图任务失败：{image_task_fail_reason(last_payload)}')
        await asyncio.sleep(min(interval, max(0.0, deadline - time.monotonic())))
    raw_text = json.dumps(last_payload, ensure_ascii=False)[:800] if last_payload else ''
    extra = f'，最后响应：{raw_text}' if raw_text else ''
    raise HTTPException(status_code=504, detail=f'生图任务超时（已等待 {int(timeout)} 秒），task_id={task_id}{extra}')

def smart_owned_result_items(images: List[Any], paths: List[str]) -> List[Any]:
    """Return generated results, including legacy results without the marker."""
    return [item for item in images if isinstance(item, dict) and item.get('loopInputPreview') is not True and (item.get('generatedResult') is True or any((_main('json_references_media_path')(item, path) for path in paths)))]

def reset_canvas_result_nodes_for_media(canvas: Dict[str, Any], paths: List[str]) -> List[str]:
    """Clear generated media while preserving prompts, references, settings and links."""
    reset_ids = []
    updated_nodes = []
    for node in list(canvas.get('nodes') or []):
        node = dict(node)
        node_type = str(node.get('type') or '').strip().lower()
        changed = False
        if isinstance(node.get('generatedOutputs'), list):
            outputs = list(node.get('generatedOutputs') or [])
            kept_outputs = [item for item in outputs if not any((_main('json_references_media_path')(item, path) for path in paths))]
            if len(kept_outputs) != len(outputs):
                node['generatedOutputs'] = kept_outputs
                changed = True
        if node_type in {'smart-image', 'output'} and isinstance(node.get('images'), list):
            images = list(node.get('images') or [])
            if node_type == 'smart-image':
                owned_items = smart_owned_result_items(images, paths)
            else:
                owned_items = images
            owns_target = any((_main('json_references_media_path')(item, path) for item in owned_items for path in paths))
            kept_images = [item for item in images if not (owns_target and item in owned_items and any((_main('json_references_media_path')(item, path) for path in paths)))]
            if len(kept_images) != len(images):
                node['images'] = kept_images
                changed = True
                if node_type == 'output':
                    node['_pending'] = []
                    node['imageComparisons'] = {}
                if node_type == 'smart-image':
                    node['pending'] = 0
                    node['running'] = False
                    node['queued'] = False
                    for key in ('jimengPending', 'pendingTasks', 'runStartedAt', 'runFinishedAt', 'runElapsedMs', 'runTimerHidden', 'outputKind', 'w', 'h'):
                        node.pop(key, None)
        elif node_type == 'image' and any((_main('json_references_media_path')(node.get('url'), path) for path in paths)):
            node['url'] = ''
            node['mediaKind'] = 'image'
            node['name'] = '空白图片'
            changed = True
        if changed and node.get('id'):
            reset_ids.append(str(node['id']))
        updated_nodes.append(node)
    if reset_ids:
        canvas['nodes'] = updated_nodes
    return reset_ids

def generate_video_preview_image(path: str, width: int) -> Image.Image:
    ffmpeg = shutil.which('ffmpeg')
    if not ffmpeg:
        raise RuntimeError('未找到 ffmpeg，无法生成视频预览图')
    fd, frame_path = tempfile.mkstemp(prefix='media_preview_frame_', suffix='.jpg')
    os.close(fd)
    try:
        cmd = [ffmpeg, '-hide_banner', '-loglevel', 'error', '-y', '-ss', '0.5', '-i', path, '-frames:v', '1', '-vf', f"scale='min({width},iw)':-2", frame_path]
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
        if proc.returncode != 0 or not os.path.exists(frame_path) or os.path.getsize(frame_path) <= 0:
            raise RuntimeError((proc.stderr or 'ffmpeg 未能抽取视频首帧').strip()[:300])
        with Image.open(frame_path) as frame:
            img = ImageOps.exif_transpose(frame).copy()
            img.thumbnail((width, width), Image.LANCZOS)
            return img.convert('RGB')
    finally:
        try:
            os.remove(frame_path)
        except OSError:
            pass

def default_asset_library():
    categories = [{'id': 'characters', 'name': '角色', 'type': 'image', 'items': []}, {'id': 'scenes', 'name': '场景', 'type': 'image', 'items': []}]
    return {'active_library_id': 'default', 'libraries': [{'id': 'default', 'name': '默认资产库', 'type': 'asset', 'categories': categories}], 'categories': categories, 'updated_at': _main('now_ms')()}

def normalize_asset_library(lib):
    if not isinstance(lib, dict):
        lib = default_asset_library()
    legacy_categories = lib.get('categories') if isinstance(lib.get('categories'), list) else None
    libraries = lib.get('libraries') if isinstance(lib.get('libraries'), list) else []
    if not libraries:
        libraries = [{'id': 'default', 'name': '默认资产库', 'type': 'asset', 'categories': legacy_categories or default_asset_library()['categories']}]
    for library in libraries:
        library['id'] = re.sub('[^A-Za-z0-9_-]+', '_', str(library.get('id') or f'lib_{uuid.uuid4().hex[:8]}'))[:40]
        library['name'] = sanitize_asset_name(library.get('name') or '资产库', '资产库')
        cats = library.get('categories') if isinstance(library.get('categories'), list) else []
        cats[:] = [cat for cat in cats if isinstance(cat, dict) and cat.get('type', 'image') != 'workflow']
        for cat in cats:
            for item in cat.get('items') or []:
                migrate_asset_item_registrations(item)
        library['categories'] = cats
    active = str(lib.get('active_library_id') or libraries[0].get('id') or 'default')
    if not any((item.get('id') == active for item in libraries)):
        active = libraries[0].get('id') or 'default'
    active_library = next((item for item in libraries if item.get('id') == active), libraries[0])
    lib['libraries'] = libraries
    lib['active_library_id'] = active
    lib['categories'] = active_library.get('categories') or []
    lib['updated_at'] = int(lib.get('updated_at') or _main('now_ms')())
    sort_asset_library_items(lib)
    return lib

def unique_asset_category_dir(library, base_name: str) -> str:
    """为资产库分组生成一个唯一、文件系统安全的子文件夹名（library/<dir>/）。
    以分组名为基础（保留中文），与同库其它分组的 dir 及磁盘上已存在的文件夹去重。"""
    base = sanitize_asset_name(base_name, '分组').strip(' .') or '分组'
    existing = {str(c.get('dir')) for c in library.get('categories') or [] if isinstance(c, dict) and c.get('dir')}
    candidate = base
    i = 2
    while candidate in existing or os.path.exists(os.path.join(_main('ASSET_LIBRARY_DIR'), candidate)):
        candidate = f'{base}_{i}'
        i += 1
    return candidate

def make_asset_library_item(src: str, name: str='', subdir: str='') -> Tuple[str, Dict[str, Any]]:
    kind = asset_library_media_kind(src)
    ext = asset_library_safe_extension(src, kind)
    safe_name = sanitize_asset_name(name or os.path.basename(src), 'asset')
    if not os.path.splitext(safe_name)[1]:
        safe_name += ext
    dest_name = f'lib_{uuid.uuid4().hex[:12]}_{safe_name}'
    subdir = str(subdir or '').strip('/').strip()
    if subdir:
        dest_dir = os.path.join(_main('ASSET_LIBRARY_DIR'), subdir)
        os.makedirs(dest_dir, exist_ok=True)
        dest_path = os.path.join(dest_dir, dest_name)
        rel = f'{subdir}/{dest_name}'
    else:
        dest_path = os.path.join(_main('ASSET_LIBRARY_DIR'), dest_name)
        rel = dest_name
    shutil.copy2(src, dest_path)
    item = {'id': f'asset_{uuid.uuid4().hex[:12]}', 'name': os.path.splitext(safe_name)[0][:120], 'url': '/assets/library/' + urllib.parse.quote(rel, safe='/'), 'kind': kind, 'created_at': _main('now_ms')()}
    return (dest_name, item)
    return lib

def load_asset_classification_prompt():
    try:
        if os.path.isfile(_main('ASSET_CLASSIFICATION_PROMPT_FILE')):
            with open(_main('ASSET_CLASSIFICATION_PROMPT_FILE'), 'r', encoding='utf-8-sig') as f:
                text = f.read().strip()
                if text:
                    return text
    except Exception as exc:
        print(f'读取素材分类规则失败: {exc}')
    return ASSET_CLASSIFICATION_PROMPT

def _local_upload_classification_path(filename):
    return os.path.splitext(os.path.join(_main('LOCAL_UPLOAD_DIR'), filename))[0] + '.classification.json'

def normalize_asset_classification(raw):
    if not isinstance(raw, dict):
        raw = {}
    categories = raw.get('categories') if isinstance(raw.get('categories'), dict) else {}
    clean_categories = {}
    flat = []
    for key, values in categories.items():
        norm_key = re.sub('[^A-Za-z0-9_-]+', '_', str(key or '').strip().lower())[:40]
        if not norm_key:
            continue
        if isinstance(values, str):
            values = re.split('[,，、/|;；\\n]+', values)
        if not isinstance(values, list):
            continue
        clean_values = []
        seen = set()
        for value in values:
            tag = _safe_asset_tag(value)
            if not tag or tag in seen:
                continue
            seen.add(tag)
            clean_values.append(tag)
            flat.append({'dimension': norm_key, 'label': ASSET_CLASSIFICATION_DIMENSION_NAMES.get(norm_key, norm_key), 'tag': tag})
            if len(clean_values) >= 8:
                break
        if clean_values:
            clean_categories[norm_key] = clean_values
    tags = raw.get('tags') if isinstance(raw.get('tags'), list) else []
    clean_tags = []
    seen_tags = set()
    for value in tags:
        tag = _safe_asset_tag(value)
        if not tag or tag in seen_tags:
            continue
        seen_tags.add(tag)
        clean_tags.append(tag)
        flat.append({'dimension': 'tags', 'label': '标签', 'tag': tag})
        if len(clean_tags) >= 20:
            break
    seen_flat = set()
    flat_unique = []
    for item in flat:
        key = f"{item['dimension']}::{item['tag']}"
        if key in seen_flat:
            continue
        seen_flat.add(key)
        flat_unique.append(item)
    return {'summary': str(raw.get('summary') or '').strip()[:240], 'categories': clean_categories, 'tags': clean_tags, 'flat': flat_unique, 'updated_at': _main('now_ms')()}

def parse_asset_classification_text(text):
    value = str(text or '').strip()
    if not value:
        return normalize_asset_classification({})
    value = re.sub('^```(?:json)?\\s*', '', value, flags=re.IGNORECASE).strip()
    value = re.sub('\\s*```$', '', value).strip()
    try:
        data = json.loads(value)
    except Exception:
        match = re.search('\\{.*\\}', value, re.S)
        data = json.loads(match.group(0)) if match else {}
    return normalize_asset_classification(data)

def _read_local_upload_classification(filename):
    path = _local_upload_classification_path(filename)
    if not os.path.isfile(path):
        return None
    try:
        with open(path, 'r', encoding='utf-8') as f:
            return normalize_asset_classification(json.load(f))
    except Exception:
        return None

def _write_local_upload_classification(filename, classification):
    path = _local_upload_classification_path(filename)
    with open(path, 'w', encoding='utf-8') as f:
        json.dump(normalize_asset_classification(classification), f, ensure_ascii=False, indent=2)

def asset_classification_prompt(extra_prompt=''):
    base = load_asset_classification_prompt()
    extra = str(extra_prompt or '').strip()
    if not extra:
        return base
    return base + '\n\n用户补充分类要求：\n' + extra[:4000]

async def classify_image_with_provider(abs_path, provider_id='', model='', ms_model='', prompt=''):
    text, resolved_model = await _main('caption_image_with_provider')(abs_path, asset_classification_prompt(prompt), provider_id or _main('get_primary_provider_id')(), model, ms_model)
    classification = parse_asset_classification_text(text)
    classification['model'] = resolved_model
    classification['provider'] = provider_id or _main('get_primary_provider_id')()
    return classification

async def classify_asset_image_best_effort(abs_path, provider_id='', model='', ms_model='', prompt=''):
    try:
        return await classify_image_with_provider(abs_path, provider_id, model, ms_model, prompt)
    except Exception as exc:
        print(f'素材智能分类失败: {exc}')
        return None

def find_asset_library(lib, library_id=''):
    lib = normalize_asset_library(lib)
    library_id = str(library_id or lib.get('active_library_id') or '').strip()
    return next((item for item in lib.get('libraries', []) if item.get('id') == library_id), None) or (lib.get('libraries') or [None])[0]

def find_asset_category_in_library(lib, category_id, library_id=''):
    library = find_asset_library(lib, library_id)
    if not library:
        return None
    for cat in library.get('categories', []):
        if cat.get('id') == category_id:
            return cat
    return None

def find_asset_category_with_library(lib, category_id, library_id=''):
    lib = normalize_asset_library(lib)
    preferred = str(library_id or '').strip()
    libraries = lib.get('libraries', []) or []
    if preferred:
        libraries = [item for item in libraries if item.get('id') == preferred]
    for library in libraries:
        for cat in library.get('categories', []) or []:
            if cat.get('id') == category_id:
                return (library, cat)
    return (None, None)

def shared_folders_load():
    try:
        with open(_main('SHARED_FOLDERS_FILE'), 'r', encoding='utf-8') as f:
            data = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        data = {}
    if not isinstance(data, dict):
        data = {}
    folders = data.get('folders')
    if not isinstance(folders, list):
        folders = []
    return {'folders': [f for f in folders if isinstance(f, dict)]}

def shared_folder_by_id(folder_id):
    for entry in shared_folders_load().get('folders', []):
        if entry.get('id') == folder_id:
            return entry
    return None

def normalize_prompt_library_item(item):
    if not isinstance(item, dict):
        item = {}
    name = sanitize_asset_name(item.get('name') or '提示词', '提示词')
    positive = str(item.get('positive') or item.get('text') or '').strip()
    return {'id': re.sub('[^A-Za-z0-9_-]+', '_', str(item.get('id') or item.get('item_id') or f'tpl_{uuid.uuid4().hex[:12]}'))[:60], 'name': name, 'category': normalize_prompt_category_id(item.get('category') or 'custom'), 'scene': str(item.get('scene') or '').strip()[:500], 'positive': positive, 'negative': str(item.get('negative') or '').strip(), 'params': item.get('params') if isinstance(item.get('params'), dict) else {}, 'created_at': int(item.get('created_at') or _main('now_ms')()), 'updated_at': int(item.get('updated_at') or item.get('created_at') or _main('now_ms')())}

def default_prompt_libraries():
    return {'active_library_id': 'system', 'libraries': [seed_system_prompt_library()], 'updated_at': _main('now_ms')()}

def normalize_prompt_libraries(data):
    if not isinstance(data, dict):
        data = default_prompt_libraries()
    raw_libraries = data.get('libraries') if isinstance(data.get('libraries'), list) else []
    raw_libraries = [lib for lib in raw_libraries if isinstance(lib, dict)]
    if not any((lib.get('id') == 'system' for lib in raw_libraries)):
        raw_libraries = [seed_system_prompt_library()] + raw_libraries
    libraries = []
    seen_lib_ids = set()
    for raw in raw_libraries:
        is_system = raw.get('id') == 'system'
        if is_system:
            lib_id = 'system'
        else:
            lib_id = re.sub('[^A-Za-z0-9_-]+', '_', str(raw.get('id') or f'lib_{uuid.uuid4().hex[:12]}'))[:60] or f'lib_{uuid.uuid4().hex[:12]}'
        if lib_id in seen_lib_ids:
            continue
        seen_lib_ids.add(lib_id)
        items = []
        seen_items = set()
        for raw_item in raw.get('items') if isinstance(raw.get('items'), list) else []:
            if not isinstance(raw_item, dict):
                continue
            item = normalize_prompt_library_item(raw_item)
            item_id = item.get('id') or f'tpl_{uuid.uuid4().hex[:12]}'
            if item_id in seen_items:
                continue
            seen_items.add(item_id)
            items.append(item)
        default_name = '系统提示词库' if is_system else '提示词库'
        raw_categories = raw.get('categories') if isinstance(raw.get('categories'), list) else []
        if not is_system:
            builtin_ids = {'view', 'storyboard', 'character', 'product', 'lighting', 'custom'}
            raw_categories = [c for c in raw_categories if isinstance(c, dict) and normalize_prompt_category_id(c.get('id') or c.get('name') or '') not in builtin_ids]
        libraries.append({'id': lib_id, 'name': sanitize_asset_name(raw.get('name') or default_name, default_name), 'type': 'prompt', 'readonly': False, 'system': is_system, 'categories': normalize_prompt_template_categories(raw_categories, include_defaults=is_system), 'items': items})
    active = str(data.get('active_library_id') or 'system')
    if not any((lib['id'] == active for lib in libraries)):
        active = 'system' if any((lib['id'] == 'system' for lib in libraries)) else libraries[0]['id'] if libraries else 'system'
    return {'active_library_id': active, 'libraries': libraries, 'updated_at': int(data.get('updated_at') or _main('now_ms')())}

def public_prompt_libraries(data=None):
    data = normalize_prompt_libraries(data or _main('load_prompt_libraries')())
    return {'active_library_id': data.get('active_library_id') or (data.get('libraries') or [{}])[0].get('id') or 'system', 'libraries': data.get('libraries') or [], 'updated_at': data.get('updated_at') or _main('now_ms')()}

def reference_to_data_url(ref, max_size=None, max_bytes=None):
    """把本地输出文件转为 data URL（base64）。max_size 限制最长边像素，避免 payload 过大。"""
    value = str(ref.get('url', '') or '').strip()
    path = _main('local_media_adapter_path')(value, 'image')
    if not path:
        return value
    file_size = os.path.getsize(path)
    if max_bytes is not None and file_size > int(max_bytes):
        raise HTTPException(status_code=400, detail={'code': 'conversation_media_file_too_large', 'message': '项目图片参考素材超过对话适配器单文件上限'})
    if max_size:
        try:
            with Image.open(path) as img:
                w, h = img.size
                if max_bytes is not None and w * h > _main('PROJECT_CONVERSATION_IMAGE_MAX_PIXELS'):
                    raise HTTPException(status_code=400, detail={'code': 'conversation_image_dimensions_exceeded', 'message': '项目图片像素尺寸超过对话适配器安全上限'})
                img.load()
                if max(w, h) > max_size:
                    img.thumbnail((max_size, max_size), Image.LANCZOS)
                if img.mode not in ('RGB', 'RGBA'):
                    img = img.convert('RGB')
                buf = BytesIO()
                fmt = 'PNG' if img.mode == 'RGBA' else 'JPEG'
                img.save(buf, format=fmt, quality=88 if fmt == 'JPEG' else None)
                encoded = base64.b64encode(buf.getvalue()).decode('ascii')
                mime = 'image/png' if fmt == 'PNG' else 'image/jpeg'
                return f'data:{mime};base64,{encoded}'
        except HTTPException:
            raise
        except Exception as e:
            print(f'reference resize failed, fallback to raw: {e}')
    with open(path, 'rb') as f:
        encoded = base64.b64encode(f.read()).decode('ascii')
    return f'data:{content_type_for_path(path)};base64,{encoded}'

def media_adapter_data_url(value: str, expected_kind: str, *, max_bytes: Optional[int]=None) -> str:
    """Encode one local adapter input without exposing an app/project URL upstream."""
    path = _main('local_media_adapter_path')(value, expected_kind)
    if not path:
        return str(value or '').strip()
    size = os.path.getsize(path)
    if max_bytes is not None and size > int(max_bytes):
        raise HTTPException(status_code=400, detail={'code': 'conversation_media_file_too_large', 'message': f'项目{expected_kind}参考素材超过对话适配器单文件上限'})
    if size > _main('PROJECT_VIDEO_REFERENCE_INLINE_MAX_BYTES'):
        raise HTTPException(status_code=400, detail=f'项目{expected_kind}参考素材超过内联适配上限，请配置公网媒体地址或使用支持文件上传的服务商')
    with open(path, 'rb') as handle:
        encoded = base64.b64encode(handle.read()).decode('ascii')
    return f'data:{content_type_for_path(path)};base64,{encoded}'

def media_reference_to_url(value, max_image_size=None):
    if not isinstance(value, str) or not value:
        return ''
    if value.startswith('/output/') or value.startswith('/assets/'):
        return reference_to_data_url({'url': value}, max_size=max_image_size)
    return value

def volcengine_media_reference_url(value, max_image_size=1536):
    if not isinstance(value, str):
        return ''
    value = value.strip()
    if not value:
        return ''
    if is_private_asset_url(value):
        return value
    if value.startswith('/api/project-library/'):
        value = project_media_adapter_reference_from_url(value)
        if not value:
            return ''
    if value.startswith(f'{PROJECT_MEDIA_ADAPTER_SCHEME}://'):
        path = _main('local_media_adapter_path')(value)
        if path and content_type_for_path(path).startswith('image/'):
            return reference_to_data_url({'url': value}, max_size=max_image_size)
        return value
    if value.startswith('/output/') or value.startswith('/assets/'):
        return reference_to_data_url({'url': value}, max_size=max_image_size)
    return value

def probe_local_audio_duration_seconds(value: str) -> Optional[float]:
    path = _main('local_media_adapter_path')(value)
    if not path or not os.path.isfile(path):
        return None
    ffprobe = shutil.which('ffprobe')
    if not ffprobe:
        return None
    try:
        proc = subprocess.run([ffprobe, '-v', 'error', '-show_entries', 'format=duration', '-of', 'default=noprint_wrappers=1:nokey=1', path], capture_output=True, text=True, timeout=20)
        if proc.returncode != 0:
            return None
        duration = float(str(proc.stdout or '').strip())
        return duration if math.isfinite(duration) and duration > 0 else None
    except Exception:
        return None

async def volcengine_video_reference_content_items(value, max_frames=4, max_size=768):
    text = str(value or '').strip()
    if not text:
        return []
    if is_private_asset_url(text):
        return [{'type': 'video_url', 'video_url': {'url': text}, 'role': 'reference_video'}]
    frame_urls = await _main('video_reference_to_frame_data_urls')(text, max_frames=max_frames, max_size=max_size)
    return [{'type': 'image_url', 'image_url': {'url': frame_url}, 'role': 'reference_image'} for frame_url in frame_urls if frame_url]

def modelscope_image_url(value, max_size=1536):
    if not value:
        return value
    if isinstance(value, str) and value.startswith(('/output/', '/assets/', '/api/project-library/')):
        return reference_to_data_url({'url': value}, max_size=max_size)
    return value

def normalize_apimart_video_reference(value: str) -> str:
    text = str(value or '').strip()
    if valid_apimart_video_image_input(text):
        return text
    return _main('local_asset_public_url')(text)

async def upload_media_for_apimart(client, provider, ref_url: str, kind: str) -> str:
    """按 kind 分派到对应的 APIMart 上传器，拿回上游可用的 http/https/asset:// URL。"""
    if kind == 'video':
        return await _main('upload_video_for_apimart')(client, provider, ref_url)
    if kind == 'audio':
        return await _main('upload_audio_for_apimart')(client, provider, ref_url)
    return await _main('upload_image_for_apimart')(client, provider, ref_url)

async def volcengine_ark_asset_call(client, action: str, body: Dict[str, Any]) -> Dict[str, Any]:
    """调用一次火山 Ark Assets OpenAPI，返回 Result 内容；出错抛 HTTPException。"""
    ak = volcengine_access_key_value()
    sk = volcengine_secret_key_value()
    if not ak or not sk:
        raise HTTPException(status_code=400, detail='未配置火山引擎 AK/SK，请在 API 设置中填写 Access Key ID / Secret Access Key。')
    body_str = json.dumps(body, ensure_ascii=False)
    headers = volcengine_sign_v4_headers(ak, sk, action, body_str)
    url = f"https://{VOLCENGINE_ARK_ASSET_HOST}/?Action={urllib.parse.quote(action, safe='')}&Version={urllib.parse.quote(VOLCENGINE_ARK_ASSET_VERSION, safe='')}"
    resp = await client.post(url, headers=headers, content=body_str.encode('utf-8'), timeout=120)
    try:
        payload = resp.json()
    except Exception:
        raise HTTPException(status_code=502, detail=f'火山 {action} 返回非 JSON（{resp.status_code}）：{resp.text[:300]}')
    meta = payload.get('ResponseMetadata') if isinstance(payload, dict) else None
    if isinstance(meta, dict) and isinstance(meta.get('Error'), dict):
        err = meta['Error']
        code = err.get('Code') or err.get('CodeN') or ''
        msg = err.get('Message') or ''
        raise HTTPException(status_code=502, detail=f'火山 {action} 失败：{code} {msg}'.strip())
    if resp.status_code not in (200, 201):
        raise HTTPException(status_code=502, detail=f'火山 {action} 失败（{resp.status_code}）：{resp.text[:300]}')
    result = payload.get('Result') if isinstance(payload, dict) and isinstance(payload.get('Result'), dict) else None
    return result if result is not None else payload if isinstance(payload, dict) else {}

async def volcengine_ensure_asset_group(client, project_name: str, group_name: str) -> str:
    """复用同名素材组合，没有则新建。返回 GroupId。"""
    name = (group_name or '可信素材').strip()[:60] or '可信素材'
    project_name = (project_name or 'default').strip() or 'default'
    try:
        listed = await volcengine_ark_asset_call(client, 'ListAssetGroups', {'Filter': {'Name': name, 'GroupType': 'AIGC'}, 'PageNumber': 1, 'PageSize': 10, 'ProjectName': project_name})
        for item in listed.get('Items') or []:
            if str(item.get('Name') or '').strip() == name and str(item.get('ProjectName') or 'default') == project_name:
                gid = str(item.get('Id') or '').strip()
                if gid:
                    return gid
    except HTTPException:
        pass
    created = await volcengine_ark_asset_call(client, 'CreateAssetGroup', {'Name': name, 'Description': name, 'ProjectName': project_name})
    gid = str(created.get('Id') or '').strip()
    if not gid:
        raise HTTPException(status_code=502, detail=f'火山 CreateAssetGroup 未返回 GroupId：{str(created)[:200]}')
    return gid

async def submit_volcengine_avatar_asset(public_url: str, name: str, kind: str, project_name: str='default', group_name: str='') -> str:
    """把公网可访问素材提交到火山 Ark 私域素材库（异步）。返回 Asset Id 作为任务 ID。"""
    async with httpx.AsyncClient(timeout=120) as client:
        group_id = await volcengine_ensure_asset_group(client, project_name, group_name)
        created = await volcengine_ark_asset_call(client, 'CreateAsset', {'GroupId': group_id, 'URL': public_url, 'AssetType': apimart_avatar_asset_type(kind), 'Name': (name or 'asset')[:60], 'ProjectName': (project_name or 'default').strip() or 'default'})
    asset_id = str(created.get('Id') or '').strip()
    if not asset_id:
        raise HTTPException(status_code=502, detail=f'火山 CreateAsset 未返回 Asset Id：{str(created)[:200]}')
    return asset_id

async def check_volcengine_avatar_task(asset_id: str, project_name: str='default') -> Dict[str, Any]:
    """查询一次火山素材状态。返回 {status: Active/Processing/Failed, asset_uri, detail}。"""
    async with httpx.AsyncClient(timeout=60) as client:
        info = await volcengine_ark_asset_call(client, 'GetAsset', {'Id': asset_id, 'ProjectName': (project_name or 'default').strip() or 'default'})
    status = str(info.get('Status') or '').strip()
    if status == 'Active':
        return {'status': 'Active', 'asset_uri': f'asset://{asset_id}', 'detail': ''}
    if status == 'Failed':
        return {'status': 'Failed', 'asset_uri': '', 'detail': '火山素材处理失败，无法用于推理。'}
    return {'status': 'Processing', 'asset_uri': '', 'detail': '火山素材处理中'}

def volcengine_public_asset_url(url: str) -> str:
    """火山 CreateAsset 要求 URL 公网可访问；本地文件需 PUBLIC_BASE_URL，否则返回 ERR:。"""
    text = str(url or '').strip()
    if text.startswith('http://') or text.startswith('https://'):
        return text
    public = _main('local_asset_public_url')(text)
    if public:
        return public
    return 'ERR:火山要求素材是公网可访问的 http/https URL；本地画布文件需配置 PUBLIC_BASE_URL/PUBLIC_MEDIA_BASE_URL 暴露为公网地址。'

def local_video_path_for_cloud_upload(ref_url: str) -> str:
    return _main('local_media_path_for_cloud_upload')(ref_url, ('video/',))

async def upload_local_video_to_cloud(ref_url: str, service: str='auto') -> Dict[str, str]:
    ref_url = str(ref_url or '').strip()
    if ref_url.startswith('http://') or ref_url.startswith('https://'):
        return {'url': ref_url, 'source': ref_url, 'service': 'existing'}
    path = _main('local_media_path_for_cloud_upload')(ref_url)
    service = str(service or os.getenv('CLOUD_VIDEO_UPLOAD_SERVICE', 'auto') or 'auto').strip().lower()
    if service in {'litterbox', 'catbox'}:
        return await upload_video_to_litterbox(path, ref_url)
    if service in {'temp', 'temp.sh', 'tempsh'}:
        return await upload_video_to_temp_sh(path, ref_url)
    errors = []
    for name, func in (('litterbox', upload_video_to_litterbox), ('temp.sh', upload_video_to_temp_sh)):
        try:
            return await func(path, ref_url)
        except HTTPException as exc:
            errors.append(f'{name}: {exc.detail}')
    raise HTTPException(status_code=502, detail='云端上传失败：' + '；'.join(errors))

async def upload_local_video_to_temp_sh(ref_url: str) -> Dict[str, str]:
    return await upload_local_video_to_cloud(ref_url, 'auto')

async def generate_modelscope_provider_image(prompt, size, model, reference_images=None, provider=None):
    clean_token = modelscope_api_key()
    if not clean_token:
        raise HTTPException(status_code=400, detail='未配置 ModelScope API Key，请在 API 设置中填写。')
    width, height = parse_size_pair(size)
    refs = []
    for ref in (reference_images or [])[:ONLINE_IMAGE_REFERENCE_MAX]:
        if not ref.get('url'):
            continue
        refs.append(modelscope_image_url(ref.get('url', ''), max_size=1536))
    headers = {'Authorization': f'Bearer {clean_token}', 'Content-Type': 'application/json', 'X-ModelScope-Async-Mode': 'true'}
    payload = {'model': selected_model(model, MODELSCOPE_DEFAULT_IMAGE_MODEL), 'prompt': prompt.strip()}
    if width and height:
        payload['width'] = width
        payload['height'] = height
        payload['size'] = f'{width}x{height}'
    if refs:
        payload['image_url'] = refs
    api_root = modelscope_image_api_root()
    async with httpx.AsyncClient(timeout=AI_REQUEST_TIMEOUT) as client:
        submit_res = await client.post(f'{api_root}/images/generations', headers=headers, json=payload)
        submit_res.raise_for_status()
        raw = submit_res.json()
        task_id = raw.get('task_id')
        if not task_id:
            try:
                return (extract_image(raw), raw)
            except HTTPException:
                raise HTTPException(status_code=502, detail=f'ModelScope 未返回 task_id：{raw}')
        deadline = time.monotonic() + AI_REQUEST_TIMEOUT
        last_payload = raw
        while time.monotonic() < deadline:
            await asyncio.sleep(IMAGE_POLL_INTERVAL)
            result = await client.get(f'{api_root}/tasks/{task_id}', headers={**headers, 'X-ModelScope-Task-Type': 'image_generation'})
            result.raise_for_status()
            data = result.json()
            last_payload = data
            status = str(data.get('task_status') or '').upper()
            if status == 'SUCCEED':
                images = data.get('output_images') or []
                if not images:
                    raise HTTPException(status_code=502, detail=f'ModelScope 成功但没有返回图片：{data}')
                return ({'type': 'url', 'value': images[0]}, data)
            if status in {'FAILED', 'FAIL', 'ERROR', 'CANCELED', 'CANCELLED', 'TIMEOUT', 'REVOKED'}:
                detail = data.get('error_info') or data.get('message') or data.get('detail') or str(data)
                raise HTTPException(status_code=502, detail=f'ModelScope 任务失败：{detail}')
        raise HTTPException(status_code=504, detail=f'ModelScope 生图任务超时：{last_payload}')

def gemini_reference_part(ref):
    value = reference_to_data_url(ref, max_size=1536)
    if not value:
        return None
    if isinstance(value, str) and value.startswith('data:image/') and (';base64,' in value):
        header, encoded = value.split(';base64,', 1)
        mime_type = header.replace('data:', '', 1) or 'image/png'
        return {'inlineData': {'mimeType': mime_type, 'data': encoded}}
    if isinstance(value, str) and value.startswith(('http://', 'https://')):
        return {'fileData': {'mimeType': 'image/png', 'fileUri': value}}
    return None

def volcengine_image_payload(ref):
    value = reference_to_data_url(ref, max_size=1536)
    if not value:
        return None
    return value

def runninghub_api_headers(provider, use_wallet=True):
    api_key = runninghub_api_key(provider, use_wallet=use_wallet)
    if not api_key:
        raise HTTPException(status_code=400, detail='未配置 RunningHub API Key，请在 API 设置中填写。')
    return {'Authorization': bearer_auth_value(api_key), 'Accept': 'application/json', 'Content-Type': 'application/json'}

def runninghub_json_headers(provider, use_wallet=True):
    return runninghub_api_headers(provider, use_wallet=use_wallet)

def runninghub_provider():
    return _main('get_api_provider_exact')('runninghub')

def runninghub_api_key(provider=None, use_wallet=False, prefer_wallet=False):
    provider = provider or runninghub_provider()
    provider_id = (provider or {}).get('id') or 'runninghub'
    free_key = str((provider or {}).get('api_key') or '').strip() or provider_env_key_value(provider_id)
    wallet_key = str((provider or {}).get('wallet_api_key') or '').strip() or runninghub_wallet_key_value()
    if use_wallet and (not wallet_key):
        raise HTTPException(status_code=400, detail='未配置 RunningHub 账户余额 API Key。标准模型接口只能走账户余额，请在 RH 设置中填写账户余额 Key。')
    api_key = wallet_key if (use_wallet or prefer_wallet) and wallet_key else free_key
    if not api_key:
        raise HTTPException(status_code=400, detail='未配置 RunningHub API Key，请在 RH 设置中填写。')
    return api_key

def runninghub_app_headers(json_body=True, use_wallet=False, provider=None):
    headers = {'Host': 'www.runninghub.cn'}
    provider = provider or runninghub_provider()
    if provider:
        api_key = runninghub_api_key(provider, use_wallet=use_wallet)
        if api_key:
            headers['Authorization'] = bearer_auth_value(api_key)
    if json_body:
        headers['Content-Type'] = 'application/json'
    return headers

async def fetch_runninghub_llm_models(provider=None):
    headers = runninghub_api_headers(provider)
    errors = []
    async with httpx.AsyncClient(timeout=20, follow_redirects=True) as client:
        for url in RUNNINGHUB_LLM_MODELS_URLS:
            try:
                resp = await client.get(url, headers=headers)
                if resp.status_code >= 400 or looks_like_html_response(resp.text):
                    errors.append(f'{url}: HTTP {resp.status_code} {resp.text[:180]}')
                    continue
                raw = resp.json() if resp.text else {}
                grouped, ids = parse_upstream_models(raw, 'openai')
                if ids:
                    return ([runninghub_registry_model_from_id(mid, 'chat') for mid in ids], {'source': url, 'count': len(ids)})
                errors.append(f'{url}: empty')
            except Exception as exc:
                errors.append(f'{url}: {str(exc)[:180]}')
    return ([], {'source': '', 'count': 0, 'errors': errors[-3:]})

async def runninghub_upload_reference(client, provider, ref):
    path = _main('local_media_adapter_path')(ref.get('url', ''))
    if not path:
        value = ref.get('url', '')
        return value if str(value).startswith(('http://', 'https://')) else ''
    upload_url = runninghub_openapi_url(provider, 'media/upload/binary')
    headers = {'Authorization': bearer_auth_value(runninghub_api_key(provider, use_wallet=True)), 'Accept': 'application/json'}
    with open(path, 'rb') as fh:
        files = {'file': (os.path.basename(path), fh, content_type_for_path(path))}
        response = await client.post(upload_url, headers=headers, files=files, timeout=120)
    response.raise_for_status()
    raw = response.json()
    data = raw.get('data') if isinstance(raw, dict) else None
    candidates = [raw, data] if isinstance(data, dict) else [raw]
    for item in candidates:
        if not isinstance(item, dict):
            continue
        value = item.get('download_url') or item.get('downloadUrl') or item.get('url') or item.get('fileUrl') or item.get('file_url')
        if value:
            return str(value)
    raise HTTPException(status_code=502, detail=f'RunningHub 上传图片未返回 download_url：{raw}')

async def wait_for_runninghub_image_task(client, provider, task_id):
    query_url = runninghub_openapi_url(provider, 'query')
    deadline = time.monotonic() + 1800
    last_payload = None
    while time.monotonic() < deadline:
        await asyncio.sleep(2)
        response = await client.post(query_url, headers=runninghub_api_headers(provider), json={'taskId': task_id})
        response.raise_for_status()
        raw = response.json()
        last_payload = raw
        status = runninghub_query_status(raw)
        if status in {'success', 'succeeded', 'completed', 'complete', 'finished', 'finish', 'done', '3'}:
            return raw
        if status in {'failed', 'fail', 'error', 'canceled', 'cancelled', '4'}:
            raise HTTPException(status_code=502, detail=f'RunningHub 任务失败：{raw}')
        try:
            return {'data': {'results': [runninghub_extract_image(raw)]}}
        except HTTPException:
            pass
    raise HTTPException(status_code=504, detail=f'RunningHub 生图任务超时：{last_payload}')

async def generate_runninghub_entry_image(prompt, size, model, reference_images, provider, entry, image_parameters=None):
    """运行 RunningHub 在线 AI 应用并返回首张图片结果。"""
    entry_id = entry['id']
    fields = rh_sort_fields([f for f in entry.get('fields') or [] if isinstance(f, dict) and f.get('enabled') is True])
    idx_map = rh_field_indexes(fields)
    use_wallet = False
    timeout = httpx.Timeout(connect=20.0, read=1800.0, write=240.0, pool=20.0)
    explicit = image_parameters if isinstance(image_parameters, dict) else {}
    aspect = str(explicit.get('aspect_ratio') or '') or runninghub_aspect_from_size(size, '')
    resolution = str(explicit.get('resolution') or '') or runninghub_resolution_from_size(size, '')
    parsed_width, parsed_height = parse_size_pair(size)
    width = explicit.get('width') if explicit.get('width') is not None else parsed_width
    height = explicit.get('height') if explicit.get('height') is not None else parsed_height

    def requested_size_field_value(field):
        names = {str(field.get('fieldName') or '').strip().lower(), str(field.get('fieldKey') or '').strip().lower(), str(field.get('label') or '').strip().lower()}
        if aspect and names & {'aspectratio', 'aspect_ratio', 'ratio'}:
            return runninghub_schema_value(field, aspect)
        if resolution and 'resolution' in names:
            return runninghub_schema_value(field, resolution)
        if width and 'width' in names:
            return width
        if height and 'height' in names:
            return height
        return None
    async with httpx.AsyncClient(timeout=timeout) as client:
        uploaded = []
        for ref in (reference_images or [])[:ONLINE_IMAGE_REFERENCE_MAX]:
            ref_url = ref.get('url') if isinstance(ref, dict) else ref
            if not ref_url:
                continue
            file_name = await _main('runninghub_upload_local_to_filename')(client, provider, ref_url, use_wallet)
            if file_name:
                uploaded.append(file_name)
        node_info_list = []
        prompt_text = str(prompt or '').strip()
        for field in fields:
            node_id = str(field.get('nodeId') or '').strip()
            field_name = str(field.get('fieldName') or '').strip()
            if not node_id or not field_name:
                continue
            kind_f = rh_field_kind(field)
            if kind_f in ('image', 'video', 'audio'):
                if kind_f != 'image':
                    continue
                index = idx_map.get((node_id, field_name), 0)
                value = uploaded[index] if index < len(uploaded) else ''
                if not value:
                    if field.get('required') is True:
                        value = rh_default_value(field)
                        if not value:
                            continue
                    else:
                        continue
                node_info_list.append({'nodeId': node_id, 'fieldName': field_name, 'fieldValue': value})
            elif rh_field_role(field) == 'prompt':
                value = prompt_text or rh_default_value(field)
                node_info_list.append({'nodeId': node_id, 'fieldName': field_name, 'fieldValue': value})
            elif kind_f == 'number' and field.get('random_enabled') is True:
                node_info_list.append({'nodeId': node_id, 'fieldName': field_name, 'fieldValue': rh_random_field_value(field)})
            else:
                value = requested_size_field_value(field)
                if value is None:
                    value = rh_default_value(field)
                node_info_list.append({'nodeId': node_id, 'fieldName': field_name, 'fieldValue': value})
        api_key = runninghub_api_key(provider, use_wallet=use_wallet)
        submit_url = runninghub_endpoint_url(provider, '/task/openapi/ai-app/run')
        body = {'apiKey': api_key, 'webappId': entry_id, 'nodeInfoList': node_info_list}
        response = await client.post(submit_url, headers=runninghub_app_headers(True, use_wallet), json=body)
        raw = response.json()
        if not (isinstance(raw, dict) and raw.get('code') in (0, '0')):
            raise HTTPException(status_code=502, detail=(raw.get('msg') if isinstance(raw, dict) else '') or f'RunningHub 提交失败：{raw}')
        task_id = raw.get('data', {}).get('taskId') if isinstance(raw.get('data'), dict) else ''
        if not task_id:
            raise HTTPException(status_code=502, detail=f'RunningHub 未返回 taskId：{raw}')
        query_url = runninghub_endpoint_url(provider, '/task/openapi/outputs')
        deadline = time.monotonic() + 1800
        last_payload = None
        while time.monotonic() < deadline:
            await asyncio.sleep(2.5)
            query_response = await client.post(query_url, headers=runninghub_app_headers(True), json={'apiKey': api_key, 'taskId': task_id})
            query_raw = query_response.json()
            last_payload = query_raw
            code = query_raw.get('code') if isinstance(query_raw, dict) else None
            if code in (0, '0'):
                outputs = runninghub_extract_outputs(query_raw.get('data'))
                for remote in outputs:
                    if str(remote or '').startswith(('http://', 'https://', '/output/', '/assets/')):
                        return ({'type': 'url', 'value': str(remote)}, query_raw)
                raise HTTPException(status_code=502, detail=f'RunningHub 任务无图片输出：{query_raw}')
            if code in (805, '805'):
                raise HTTPException(status_code=502, detail=f'RunningHub 任务失败：{runninghub_fail_reason(query_raw) or query_raw}')
        raise HTTPException(status_code=504, detail=f'RunningHub 任务超时：{last_payload}')

async def generate_runninghub_provider_image(prompt, size, model, reference_images=None, provider=None, image_parameters=None):
    entry = runninghub_entry_config_from_model(provider, model)
    if entry:
        return await generate_runninghub_entry_image(prompt, size, model, reference_images, provider, entry, image_parameters)
    model_def = await _main('runninghub_model_definition')(provider, model)
    endpoint = runninghub_task_endpoint(provider, model_def.get('endpoint') or model)
    params = model_def.get('params') if isinstance(model_def.get('params'), list) else []
    explicit = image_parameters if isinstance(image_parameters, dict) else {}
    aspect = str(explicit.get('aspect_ratio') or '') or runninghub_aspect_from_size(size, '1:1')
    resolution = str(explicit.get('resolution') or '') or runninghub_resolution_from_size(size, '2k')
    body = {'prompt': prompt}
    if runninghub_schema_field(params, 'aspectRatio'):
        field = runninghub_schema_field(params, 'aspectRatio')
        body['aspectRatio'] = runninghub_schema_value(field, aspect)
    elif runninghub_schema_field(params, 'ratio'):
        field = runninghub_schema_field(params, 'ratio')
        body['ratio'] = runninghub_schema_value(field, aspect)
    if runninghub_schema_field(params, 'resolution'):
        field = runninghub_schema_field(params, 'resolution')
        body['resolution'] = runninghub_schema_value(field, resolution)
    parsed_width, parsed_height = parse_size_pair(size)
    width = explicit.get('width') if explicit.get('width') is not None else parsed_width
    height = explicit.get('height') if explicit.get('height') is not None else parsed_height
    if width and height:
        if runninghub_schema_field(params, 'width'):
            body['width'] = width
        if runninghub_schema_field(params, 'height'):
            body['height'] = height
    quality_field = runninghub_schema_field(params, 'quality')
    if quality_field:
        body['quality'] = runninghub_schema_value(quality_field, 'medium')
    async with httpx.AsyncClient(timeout=httpx.Timeout(connect=20.0, read=1800.0, write=180.0, pool=20.0)) as client:
        image_urls = []
        for ref in (reference_images or [])[:ONLINE_IMAGE_REFERENCE_MAX]:
            url = await runninghub_upload_reference(client, provider, ref)
            if url:
                image_urls.append(url)
        if image_urls:
            image_field = runninghub_schema_field(params, 'imageUrls', 'imageUrl', 'images', 'image')
            key = str((image_field or {}).get('fieldKey') or 'imageUrls')
            if key.endswith('s') or (image_field or {}).get('multipleInputs') is True:
                body[key] = image_urls
            else:
                body[key] = image_urls[0]
        runninghub_apply_schema_defaults(body, params)
        response = await client.post(endpoint, headers=runninghub_json_headers(provider), json=body)
        response.raise_for_status()
        raw = response.json()
        try:
            return (runninghub_extract_image(raw), raw)
        except HTTPException:
            task_id = runninghub_extract_task_id(raw)
            if not task_id:
                raise HTTPException(status_code=502, detail=f'RunningHub 未返回 taskId 或图片结果：{raw}')
        result = await wait_for_runninghub_image_task(client, provider, task_id)
        return (runninghub_extract_image(result), result)

async def wait_for_runninghub_openapi_task(client, provider, task_id, output_kind=''):
    query_url = runninghub_openapi_url(provider, 'query')
    deadline = time.monotonic() + 1800
    last_payload = None
    while time.monotonic() < deadline:
        await asyncio.sleep(3)
        response = await client.post(query_url, headers=runninghub_json_headers(provider), json={'taskId': task_id})
        response.raise_for_status()
        raw = response.json()
        last_payload = raw
        status = runninghub_query_status(raw).upper()
        if status in {'SUCCESS', 'SUCCEEDED', 'COMPLETED', 'COMPLETE', 'FINISHED', 'DONE', '3'}:
            return raw
        if status in {'FAILED', 'FAIL', 'ERROR', 'CANCEL', 'CANCELED', 'CANCELLED', '4'}:
            raise HTTPException(status_code=502, detail=f'RunningHub 任务失败：{runninghub_fail_reason(raw) or raw}')
        if output_kind == 'video' and video_output_urls(raw):
            return raw
    raise HTTPException(status_code=504, detail=f'RunningHub 任务超时：{last_payload or task_id}')

async def generate_runninghub_video(payload, provider):
    model_def = await _main('runninghub_model_definition')(provider, payload.model)
    endpoint = runninghub_task_endpoint(provider, model_def.get('endpoint') or payload.model)
    image_to_video = runninghub_is_image_to_video(payload.model) or runninghub_is_image_to_video(model_def.get('endpoint')) or runninghub_is_image_to_video(endpoint)
    params = model_def.get('params') if isinstance(model_def.get('params'), list) else []
    incompatible = runninghub_video_incompatible_parameters(payload, params, image_to_video=image_to_video)
    if incompatible:
        raise HTTPException(status_code=400, detail={'code': 'unsupported_video_adapter_parameters', 'message': f"{provider.get('name') or provider.get('id') or 'RunningHub'} 不支持本次请求的全部参数", 'incompatible_parameters': incompatible})
    body = {'prompt': str(payload.prompt or '')}
    aspect = str(payload.aspect_ratio or '16:9').strip() or '16:9'
    if runninghub_schema_field(params, 'aspectRatio'):
        field = runninghub_schema_field(params, 'aspectRatio')
        body['aspectRatio'] = runninghub_schema_value(field, aspect)
    if runninghub_schema_field(params, 'ratio'):
        field = runninghub_schema_field(params, 'ratio')
        body['ratio'] = runninghub_schema_value(field, aspect)
    if runninghub_schema_field(params, 'size'):
        field = runninghub_schema_field(params, 'size')
        body['size'] = runninghub_schema_value(field, runninghub_size_for_aspect(aspect))
    if runninghub_schema_field(params, 'duration'):
        field = runninghub_schema_field(params, 'duration')
        body['duration'] = runninghub_schema_value(field, str(max(1, min(60, int(payload.duration or 5)))))
    if runninghub_schema_field(params, 'resolution'):
        field = runninghub_schema_field(params, 'resolution')
        body['resolution'] = runninghub_schema_value(field, str(payload.resolution or '720p').lower())
    if runninghub_schema_field(params, 'generateAudio'):
        body['generateAudio'] = bool(payload.generate_audio)
    if runninghub_schema_field(params, 'watermark'):
        body['watermark'] = bool(payload.watermark)
    async with httpx.AsyncClient(timeout=VIDEO_POLL_TIMEOUT) as client:
        image_refs = []
        for ref in (payload.images or [])[:10]:
            ref_url = getattr(ref, 'url', '') or ''
            if ref_url:
                up = await runninghub_upload_reference(client, provider, {'url': ref_url})
                if up:
                    image_refs.append({'url': up, 'role': str(getattr(ref, 'role', '') or '').strip().lower()})
        image_urls = [item['url'] for item in image_refs if item.get('url')]
        if image_urls:
            first_url = next((item['url'] for item in image_refs if item.get('role') in {'first_frame', 'first'}), image_urls[0])
            last_url = next((item['url'] for item in image_refs if item.get('role') in {'last_frame', 'last'}), image_urls[1] if len(image_urls) > 1 else '')
            first_field = runninghub_schema_field(params, 'firstFrameUrl', 'first_frame_url', 'firstFrameImage', 'first_frame_image')
            last_field = runninghub_schema_field(params, 'lastFrameUrl', 'last_frame_url', 'lastFrameImage', 'last_frame_image')
            if first_field and first_url:
                body[str(first_field.get('fieldKey'))] = first_url
            if last_field and last_url:
                body[str(last_field.get('fieldKey'))] = last_url
            image_field = runninghub_schema_field(params, 'imageUrls', 'image_urls', 'imageUrl', 'image_url', 'referenceImages', 'referenceImageUrls')
            key = str((image_field or {}).get('fieldKey') or '')
            if key and key in body:
                pass
            elif key and (key.endswith('s') or (image_field or {}).get('multipleInputs') is True):
                body[key] = image_urls
            elif key:
                body[key] = image_urls[0]
            elif image_to_video and 'firstFrameUrl' not in body:
                body['firstFrameUrl'] = first_url
        first_required = runninghub_schema_field(params, 'firstFrameUrl', 'first_frame_url', 'firstFrameImage', 'first_frame_image')
        if runninghub_schema_field_required(first_required) and (not body.get(str(first_required.get('fieldKey') or ''))):
            raise HTTPException(status_code=400, detail='当前 RunningHub 模型是图生视频，需要连接一张首帧图片后再生成。')
        if image_to_video and (not body.get('firstFrameUrl')) and (not image_urls):
            raise HTTPException(status_code=400, detail='当前 RunningHub 模型是图生视频，需要连接一张首帧图片后再生成。')
        runninghub_apply_schema_defaults(body, params)
        response = await client.post(endpoint, headers=runninghub_json_headers(provider), json=body)
        response.raise_for_status()
        raw = response.json()
        task_id = runninghub_extract_task_id(raw)
        if not task_id:
            fail_reason = runninghub_fail_reason(raw)
            if fail_reason:
                raise HTTPException(status_code=502, detail=f'RunningHub 视频接口错误：{fail_reason}')
        result = raw
        if task_id and (not video_output_urls(raw)):
            result = await wait_for_runninghub_openapi_task(client, provider, task_id, 'video')
        urls = video_output_urls(result)
        if not urls:
            outputs = runninghub_extract_outputs(result)
            urls = [url for url in outputs if str(url).startswith(('http://', 'https://', '/output/', '/assets/'))]
        if not urls:
            raise HTTPException(status_code=502, detail=f'RunningHub 视频生成成功但没有返回视频：{result}')
        local_urls = [await _main('save_remote_video_to_output')(url, prefix='rh_video_') for url in urls]
        return {'videos': local_urls, 'task_id': task_id, 'raw': result}

def upstream_message_from_record(item):
    role = item.get('role')
    if role not in {'user', 'assistant'} or item.get('type') == 'image':
        return None
    attachments = item.get('attachments') or []
    if attachments and role == 'user':
        text = item.get('content', '')
        blocks = _main('attachment_text_blocks')(attachments)
        if blocks:
            text = f'{text}\n\n以下是用户上传附件的可读内容，请在回答时参考：\n\n' + '\n\n---\n\n'.join(blocks)
        content = [{'type': 'text', 'text': text}]
        image_urls = []
        for ref in image_references(attachments[:CHAT_ATTACHMENT_MAX]):
            url = reference_to_data_url(ref)
            if url:
                image_urls.append(url)
        image_urls.extend(_main('attachment_embedded_image_data_urls')(attachments[:CHAT_ATTACHMENT_MAX], max_images=max(0, CHAT_ATTACHMENT_MAX - len(image_urls))))
        for url in image_urls[:CHAT_ATTACHMENT_MAX]:
            content.append({'type': 'image_url', 'image_url': {'url': url}})
        return {'role': role, 'content': content}
    return {'role': role, 'content': item.get('content', '')}

def _local_upload_abs(rel):
    rel_path = _local_upload_rel_path(rel)
    path = os.path.abspath(os.path.join(_main('LOCAL_UPLOAD_DIR'), rel_path))
    root = os.path.abspath(_main('LOCAL_UPLOAD_DIR'))
    try:
        common = os.path.commonpath([root, path])
    except ValueError:
        raise HTTPException(status_code=400, detail='非法路径')
    if common != root:
        raise HTTPException(status_code=400, detail='非法路径')
    return (rel_path, path)

def _local_upload_safe_path(name):
    filename, path = _local_upload_abs(name)
    if not filename:
        raise HTTPException(status_code=400, detail='文件名不能为空')
    return (filename, path)

def _local_upload_safe_folder(path_value):
    rel, path = _local_upload_abs(path_value)
    return (rel, path)

def _local_upload_caption_path(filename):
    return os.path.splitext(os.path.join(_main('LOCAL_UPLOAD_DIR'), filename))[0] + '.txt'

def _read_local_upload_caption(filename):
    caption_path = _local_upload_caption_path(filename)
    if not os.path.isfile(caption_path):
        return ('', '')
    try:
        with open(caption_path, 'r', encoding='utf-8-sig') as f:
            text = f.read()
    except UnicodeDecodeError:
        with open(caption_path, 'r', encoding='gb18030', errors='replace') as f:
            text = f.read()
    except OSError:
        return ('', '')
    return (text, os.path.basename(caption_path))

def _local_upload_item(filename):
    path = os.path.join(_main('LOCAL_UPLOAD_DIR'), filename)
    rel = _local_upload_rel_path(filename)
    try:
        stat = os.stat(path)
        size = stat.st_size
        created_at = stat.st_mtime
    except OSError:
        size = 0
        created_at = 0
    kind, _ = _local_upload_kind_ext(filename, '')
    item = {'id': rel, 'file': rel, 'name': _local_upload_display_name(rel), 'url': f"/api/storage-files/local/{urllib.parse.quote(rel, safe='/')}", 'kind': kind or 'image', 'size': size, 'created_at': created_at, 'folder': os.path.dirname(rel).replace('\\', '/')}
    if kind == 'image':
        try:
            with Image.open(path) as img:
                item['natural_w'], item['natural_h'] = img.size
                item['width'], item['height'] = img.size
        except Exception:
            pass
        caption, caption_file = _read_local_upload_caption(filename)
        item['caption'] = caption
        item['caption_file'] = caption_file
        classification = _read_local_upload_classification(filename)
        if classification:
            item['classification'] = classification
    return item

def _local_upload_tree_and_items():
    root_node = _local_upload_folder_node('', '全部上传')
    folder_map = {'': root_node}
    items = []
    for current, dirs, files in os.walk(_main('LOCAL_UPLOAD_DIR')):
        dirs[:] = sorted([d for d in dirs if not d.startswith('.') and (not d.startswith('._'))], key=str.lower)
        rel_dir = os.path.relpath(current, _main('LOCAL_UPLOAD_DIR')).replace('\\', '/')
        if rel_dir == '.':
            rel_dir = ''
        node = folder_map.get(rel_dir)
        if node is None:
            node = _local_upload_folder_node(rel_dir)
            folder_map[rel_dir] = node
        for dirname in dirs:
            child_rel = f'{rel_dir}/{dirname}'.lstrip('/')
            child = _local_upload_folder_node(child_rel)
            folder_map[child_rel] = child
            node['children'].append(child)
        for name in sorted(files, key=str.lower):
            if name.startswith('.') or name.startswith('._'):
                continue
            rel_file = f'{rel_dir}/{name}'.lstrip('/')
            kind, _ = _local_upload_kind_ext(name, '')
            if kind is None:
                continue
            item = _local_upload_item(rel_file)
            node['items'].append(item)
            items.append(item)

    def fill_counts(node):
        total = len(node.get('items') or [])
        for child in node.get('children') or []:
            total += fill_counts(child)
        node['count'] = total
        return total
    fill_counts(root_node)
    items.sort(key=lambda it: it.get('created_at') or 0, reverse=True)
    return (root_node, items)

def migrate_double_extension_uploads():
    """修复历史遗留的双重扩展名（如 foo.png.png）：去掉重复的一层，并同步重命名 caption/classification 旁车文件。
    旧版 URL 导入会把自带扩展名的 entry.name 又拼一次 ext，导致文件名重复后缀、URL 对不上而无法显示。"""
    if not os.path.isdir(_main('LOCAL_UPLOAD_DIR')):
        return
    renamed = 0
    for current, _dirs, files in os.walk(_main('LOCAL_UPLOAD_DIR')):
        for name in files:
            m = _DOUBLE_EXT_RE.search(name)
            if not m or m.group(1).lower() not in _DOUBLE_EXT_MEDIA:
                continue
            old_path = os.path.join(current, name)
            new_path = os.path.join(current, name[:-len(m.group(1))])
            if os.path.exists(new_path):
                continue
            try:
                os.rename(old_path, new_path)
            except OSError:
                continue
            renamed += 1
            old_base = os.path.splitext(old_path)[0]
            new_base = os.path.splitext(new_path)[0]
            for suffix in ('.classification.json', '.txt'):
                src_side, dst_side = (old_base + suffix, new_base + suffix)
                if os.path.exists(src_side) and (not os.path.exists(dst_side)):
                    try:
                        os.rename(src_side, dst_side)
                    except OSError:
                        pass
    if renamed:
        print(f'修复双重扩展名素材: {renamed} 个')

def migrate_mislabeled_image_extensions():
    """有些采集来的图片内容与扩展名不符（例如 WebP 内容却叫 .png），导致服务端按错误 content-type 返回、
    严格的客户端（PS UXP）解不出来。这里按真实魔数纠正扩展名，并同步重命名 caption/classification 旁车。"""
    if not os.path.isdir(_main('LOCAL_UPLOAD_DIR')):
        return
    img_exts = {'.png', '.jpg', '.jpeg', '.webp', '.gif', '.bmp'}
    fixed = 0
    for current, _dirs, files in os.walk(_main('LOCAL_UPLOAD_DIR')):
        for name in files:
            ext = os.path.splitext(name)[1].lower()
            if ext not in img_exts:
                continue
            path = os.path.join(current, name)
            real = _sniff_image_ext(path)
            if not real:
                continue
            if real == ext or (real == '.jpg' and ext == '.jpeg'):
                continue
            new_name = os.path.splitext(name)[0] + real
            new_path = os.path.join(current, new_name)
            if os.path.exists(new_path):
                continue
            try:
                os.rename(path, new_path)
            except OSError:
                continue
            fixed += 1
            old_base = os.path.splitext(path)[0]
            new_base = os.path.splitext(new_path)[0]
            for suffix in ('.classification.json', '.txt'):
                src_side, dst_side = (old_base + suffix, new_base + suffix)
                if os.path.isfile(src_side) and (not os.path.exists(dst_side)):
                    try:
                        os.rename(src_side, dst_side)
                    except OSError:
                        pass
    if fixed:
        print(f'纠正图片扩展名(内容与后缀不符): {fixed} 个')

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
        value = provider_env_key_value(provider_id)
        if value:
            return value
    if protocol == 'volcengine':
        return volcengine_provider_api_key('')
    return ''

async def fetch_models_from_upstream(base_url: str, api_key: str, protocol: str='openai', image_request_mode: str='openai'):
    """从上游模型列表端点拉取模型，并按名称做轻量分类。"""
    protocol = protocol if protocol in SUPPORTED_PROVIDER_PROTOCOLS else 'openai'
    if protocol == 'codex':
        status = await _main('codex_status')()
        payload = codex_models_payload(raw={'status': status})
        payload['message'] = status.get('message') or payload['message']
        return payload
    if protocol == 'gemini-cli':
        status = await _main('gemini_cli_status')()
        payload = gemini_cli_models_payload(raw={'status': status})
        payload['message'] = status.get('message') or payload['message']
        return payload
    if protocol == 'jimeng':
        return {'total': len(JIMENG_DEFAULT_IMAGE_MODELS) + len(JIMENG_DEFAULT_VIDEO_MODELS), 'image_models': JIMENG_DEFAULT_IMAGE_MODELS, 'chat_models': [], 'video_models': JIMENG_DEFAULT_VIDEO_MODELS, 'all': [*JIMENG_DEFAULT_IMAGE_MODELS, *JIMENG_DEFAULT_VIDEO_MODELS]}
    if protocol == 'runninghub':
        provider = {'id': 'runninghub', 'name': 'RunningHub', 'base_url': base_url or RUNNINGHUB_DEFAULT_BASE_URL, 'protocol': 'runninghub', 'api_key': api_key}
        return await _main('runninghub_models_payload')(provider)
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

def project_video_all_references(project_id: str, payload: ProjectVideoGenerationRequest) -> List[Dict[str, Any]]:
    return [*_main('project_video_references')(project_id, payload.image_reference_asset_ids, 'image'), *_main('project_video_references')(project_id, payload.video_reference_asset_ids, 'video'), *_main('project_video_references')(project_id, payload.audio_reference_asset_ids, 'audio')]

def _agent_task_node(task: Mapping[str, Any], *, agent_id: str, execution_id: str, status: str='queued', x: Optional[float]=None, y: Optional[float]=None, w: int=300, h: int=236) -> Dict[str, Any]:
    task_id = str(task.get('task_id') or '')
    return {'id': task_id, 'type': 'agent-task', 'title': f"Agent 任务 {int(task.get('index') or 0) + 1}", 'agent_id': agent_id, 'task_id': task_id, 'plan_id': str(task.get('plan_id') or ''), 'execution_id': execution_id, 'kind': str(task.get('kind') or 'image'), 'model_type': str(task.get('model_type') or ''), 'count': int(task.get('count') or 1), 'prompt': str(task.get('prompt') or ''), 'params': copy.deepcopy(task.get('params') or {}), 'reference_asset_ids': copy.deepcopy(task.get('reference_asset_ids') or []), 'status': status, 'created_at': int(task.get('created_at') or _main('now_ms')()), 'updated_at': _main('now_ms')(), 'result_node_id': str(task.get('result_node_id') or f'{task_id}-result'), 'idempotency_key': str(task.get('idempotency_key') or task_id), 'dispatch_state': str(task.get('dispatch_state') or 'queued'), 'x': float(x if x is not None else task.get('x') or 0), 'y': float(y if y is not None else task.get('y') or 0), 'w': int(w or task.get('w') or 300), 'h': int(h or task.get('h') or 236)}

def _agent_result_node(task: Mapping[str, Any], *, agent_id: str, execution_id: str, x: Optional[float]=None, y: Optional[float]=None, w: int=300, h: int=236) -> Dict[str, Any]:
    kind = str(task.get('kind') or 'image')
    result_node_id = str(task.get('result_node_id') or f"{task.get('task_id')}-result")
    if kind == 'prompt':
        return {'id': result_node_id, 'type': 'smart-prompt', 'title': 'Agent 提示词结果', 'x': 0, 'y': 0, 'text': '', 'source_agent_task_id': str(task.get('task_id') or ''), 'outputKind': 'prompt', 'generationState': 'queued', 'agent_id': agent_id, 'agent_task_id': str(task.get('task_id') or ''), 'execution_id': execution_id, 'created_at': _main('now_ms')(), 'x': float(x if x is not None else task.get('result_x') or 0), 'y': float(y if y is not None else task.get('result_y') or 0), 'w': int(w or task.get('result_w') or 300), 'h': int(h or task.get('result_h') or 236)}
    return {'id': result_node_id, 'type': 'smart-image', 'title': 'Agent 图片结果' if kind == 'image' else 'Agent 视频结果', 'x': 0, 'y': 0, 'images': [], 'outputKind': kind, 'pending': int(task.get('count') or 1), 'running': False, 'agent_id': agent_id, 'agent_task_id': str(task.get('task_id') or ''), 'execution_id': execution_id, 'created_at': _main('now_ms')(), 'x': float(x if x is not None else task.get('result_x') or 0), 'y': float(y if y is not None else task.get('result_y') or 0), 'w': int(w or task.get('result_w') or 300), 'h': int(h or task.get('result_h') or 236)}

async def _agent_call_adapter(task_request: Dict[str, Any], kind: str) -> Any:
    adapter = _main('agent_image_adapter') if kind == 'image' else _main('agent_video_adapter')
    if adapter is None:
        raise RuntimeError(f'Agent {kind} 在线适配器尚未配置')
    result = adapter(task_request)
    if inspect.isawaitable(result):
        try:
            return await asyncio.wait_for(result, timeout=max(1, float(_main('PROJECT_AGENT_TASK_TIMEOUT_SECONDS'))))
        except asyncio.TimeoutError as exc:
            raise RuntimeError('Agent 在线适配器超时，任务已安全失败') from exc
    return result

def _agent_mark_task_terminal(project_id: str, agent_id: str, plan_id: str, execution_id: str, task_id: str, status: str, **fields: Any) -> None:
    """Merge a task result and close the execution when every task is terminal."""
    plan = _main('_agent_update_plan')(project_id, agent_id, plan_id, task_updates={task_id: {'status': status, **copy.deepcopy(fields)}})
    if not plan:
        return
    execution = next((item for item in plan.get('executions') or [] if str(item.get('execution_id') or '') == str(execution_id)), None)
    if not isinstance(execution, dict):
        return
    task_statuses = []
    for item in execution.get('tasks') or []:
        item_id = str(item.get('task_id') or '')
        plan_item = next((candidate for candidate in plan.get('tasks') or [] if str(candidate.get('task_id') or '') == item_id), {})
        task_statuses.append({'task_id': item_id, 'result_node_id': str(plan_item.get('result_node_id') or item.get('result_node_id') or ''), 'kind': str(plan_item.get('kind') or item.get('kind') or ''), 'status': str(plan_item.get('status') or 'queued'), **({'error': str(plan_item.get('error') or '')[:1000]} if plan_item.get('error') else {})})
    terminal = all((item.get('status') in {'succeeded', 'failed', 'uncertain'} for item in task_statuses))
    execution_status = 'completed' if terminal and all((item.get('status') == 'succeeded' for item in task_statuses)) else 'failed' if terminal else 'running'
    _main('_agent_update_plan')(project_id, agent_id, plan_id, execution_update={'execution_id': execution_id, 'status': execution_status, 'updated_at': _main('now_ms')(), 'tasks': task_statuses}, status='completed' if terminal and all((item.get('status') == 'succeeded' for item in task_statuses)) else 'failed' if terminal else 'running')

def _agent_submit_task(task: Mapping[str, Any], project_id: str, agent_id: str, execution_id: str) -> Optional[Future]:
    """Register and submit one durable task exactly once per process."""
    task_id = str(task.get('task_id') or task.get('id') or '')
    if not task_id:
        return None
    with PROJECT_AGENT_TASKS_LOCK:
        current = PROJECT_AGENT_TASKS.get(task_id)
        if current and current.get('status') in {'succeeded', 'failed', 'uncertain'}:
            return None
        existing_future = PROJECT_AGENT_FUTURES.get(task_id)
        if task_id in PROJECT_AGENT_RUNNING_IDS or (existing_future is not None and (not existing_future.done())):
            return existing_future
        PROJECT_AGENT_TASKS[task_id] = {'id': task_id, 'task_id': task_id, 'project_id': str(project_id), 'agent_id': str(agent_id), 'execution_id': str(execution_id), 'status': 'queued', 'created_at': int(task.get('created_at') or _main('now_ms')()), 'updated_at': time.time(), 'result': None, 'error': ''}
    future = ensure_project_image_generation_executor().submit(asyncio.run, _main('run_project_agent_task')(task_id, project_id, agent_id, copy.deepcopy(dict(task)), execution_id))
    with PROJECT_AGENT_TASKS_LOCK:
        PROJECT_AGENT_FUTURES[task_id] = future

    def _forget(done_future: Future, task_id=task_id):
        with PROJECT_AGENT_TASKS_LOCK:
            if PROJECT_AGENT_FUTURES.get(task_id) is done_future:
                PROJECT_AGENT_FUTURES.pop(task_id, None)
            PROJECT_AGENT_RUNNING_IDS.discard(task_id)
            terminal_ids = [key for key, value in PROJECT_AGENT_TASKS.items() if value.get('status') in {'succeeded', 'failed', 'uncertain'}]
            if len(terminal_ids) > 512:
                terminal_ids.sort(key=lambda key: float(PROJECT_AGENT_TASKS[key].get('updated_at') or 0))
                for old_id in terminal_ids[:-512]:
                    PROJECT_AGENT_TASKS.pop(old_id, None)
    future.add_done_callback(_forget)
    return future

async def generate_project_video(request_snapshot: VideoGenerationRequest, route, references: List[Dict[str, Any]], provider: Optional[Mapping[str, Any]]=None) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
    """Adapt project-owned references to the existing online video adapters."""
    legacy_payload = project_canvas_video_payload(request_snapshot, route.provider_id, route.model, references)
    requested_count = max(1, int(request_snapshot.count or 1))
    candidates = []
    raw_results = []
    for _ in range(requested_count):
        result = await _main('execute_canvas_video')(legacy_payload, provider) if provider is not None else await _main('canvas_video')(legacy_payload)
        videos = [str(value or '').strip() for value in result.get('videos') or [] if str(value or '').strip()]
        if not videos:
            raise ProjectStoreError('在线模型没有返回视频')
        candidates.extend(({'type': 'url', 'value': value, 'mime_type': content_type_for_path(value)} for value in videos))
        if isinstance(result, dict):
            raw_results.append(result.get('raw') if isinstance(result.get('raw'), dict) else {})
    raw = raw_results[0] if requested_count == 1 else {'results': raw_results}
    return (candidates, raw)

async def execute_project_video_generation(project_id: str, request_snapshot: VideoGenerationRequest, route, references: List[Dict[str, Any]], provider: Optional[Mapping[str, Any]]=None) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
    """Run one adapter call inside a task-owned project-media capability scope."""
    clean_project_id = str(project_id or '').strip()
    if not clean_project_id:
        raise ProjectStoreError('在线视频任务缺少项目标识')
    scope_token = PROJECT_MEDIA_ADAPTER_PROJECT.set(clean_project_id)
    try:
        for item in references:
            adapter_url = item.get('adapter_url') or ''
            require_project_media_adapter_scope(adapter_url)
            _main('project_media_adapter_path')(adapter_url, item.get('kind') or '')
        if provider is None:
            return await generate_project_video(request_snapshot, route, references)
        return await generate_project_video(request_snapshot, route, references, provider=provider)
    finally:
        PROJECT_MEDIA_ADAPTER_PROJECT.reset(scope_token)

async def agnes_video_image_url(ref):
    url = str(getattr(ref, 'url', '') or '').strip()
    if not url:
        return ''
    if url.startswith('http://') or url.startswith('https://'):
        return url
    uploaded = await upload_local_video_to_cloud(url, 'auto')
    return uploaded.get('url') or ''

async def yuli_fetch_reference_bytes(client, ref_url):
    """把参考图（input_reference 垫图）取成 (filename, bytes, mime)，
    支持 /output、/assets 本地文件、data URL、http(s) URL。失败返回 None。"""
    ref_url = str(ref_url or '').strip()
    if not ref_url:
        return None
    if ref_url.startswith('data:'):
        header, _, b64 = ref_url.partition(',')
        mime = (header[5:].split(';')[0] or 'image/png').strip()
        try:
            raw = base64.b64decode(b64)
        except Exception:
            return None
        ext = (mime.split('/')[-1] or 'png').split('+')[0]
        return (f'input_reference.{ext}', raw, mime)
    path = _main('local_media_adapter_path')(ref_url, 'image')
    if path:
        try:
            with open(path, 'rb') as f:
                raw = f.read()
        except Exception:
            return None
        mime = content_type_for_path(path)
        return (os.path.basename(path) or 'input_reference', raw, mime)
    if ref_url.startswith('http://') or ref_url.startswith('https://'):
        try:
            resp = await client.get(ref_url)
            resp.raise_for_status()
            raw = resp.content
        except Exception:
            return None
        mime = (resp.headers.get('content-type') or 'image/png').split(';')[0].strip()
        ext = (mime.split('/')[-1] or 'png').split('+')[0]
        return (f'input_reference.{ext}', raw, mime)
    return None

async def preflight_project_video_providers(request: VideoGenerationRequest, references: List[Dict[str, Any]], providers: List[Dict[str, Any]], *, provider_id: str='', model: str='') -> List[Dict[str, Any]]:
    """Resolve candidate execution blockers before routing can register a task."""
    snapshots = copy.deepcopy(list(providers or []))
    pinned_provider = str(provider_id or '').strip().lower()
    pinned_model = str(model or '').strip()
    for provider_index, provider in enumerate(snapshots):
        current_provider_id = str(provider.get('id') or '').strip().lower()
        if not provider.get('enabled', True) or (pinned_provider and current_provider_id != pinned_provider):
            continue
        remote_schema_failed = False
        for current_model in provider.get('video_models') or []:
            current_model = str(current_model or '').strip()
            if not current_model or (pinned_model and current_model != pinned_model):
                continue
            payload = project_canvas_video_payload(request, current_provider_id, current_model, references)
            execution_incompatible = _main('project_video_candidate_execution_incompatible_parameters')(provider, current_model, references)
            project_video_mark_candidate_incompatible(provider, current_model, execution_incompatible)
            if _main('video_adapter_incompatible_parameters')(payload, provider) or execution_incompatible:
                continue
            if not is_runninghub_provider(provider):
                continue
            configured = runninghub_configured_video_model_definition(provider, current_model)
            if configured is not None:
                definition = configured
            elif remote_schema_failed:
                project_video_mark_candidate_incompatible(provider, current_model, ['adapter_schema'])
                continue
            else:
                try:
                    definition = await _main('runninghub_model_definition')(provider, current_model)
                except (HTTPException, httpx.HTTPError, OSError, ValueError, KeyError, TypeError):
                    remote_schema_failed = True
                    project_video_mark_candidate_incompatible(provider, current_model, ['adapter_schema'])
                    continue
            provider.setdefault('_runninghub_video_model_definitions', {})[current_model] = copy.deepcopy(definition)
        provider.pop('_runninghub_model_registry', None)
        if not pinned_provider:
            try:
                route_video_generation(request, snapshots[:provider_index + 1], model=pinned_model, adapter_incompatible_parameters=functools.partial(project_video_adapter_incompatible_parameters, references=references))
            except ModelRoutingError:
                pass
            else:
                return snapshots
    return snapshots

def project_video_adapter_incompatible_parameters(request: VideoGenerationRequest, provider: Mapping[str, Any], model: str, *, references: List[Dict[str, Any]]) -> List[str]:
    """Evaluate the real adapter and resolved project references before registration."""
    payload = project_canvas_video_payload(request, str(provider.get('id') or ''), model, references)
    incompatible = _main('video_adapter_incompatible_parameters')(payload, provider)
    incompatible.extend(project_video_candidate_preflight_incompatible_parameters(provider, model))
    if incompatible or not is_runninghub_provider(provider):
        return list(dict.fromkeys(incompatible))
    model_definition = runninghub_configured_video_model_definition(provider, model) or {}
    endpoint = str(model_definition.get('endpoint') or model)
    image_to_video = runninghub_is_image_to_video(model) or runninghub_is_image_to_video(endpoint)
    params = model_definition.get('params') if isinstance(model_definition.get('params'), list) else []
    incompatible.extend(runninghub_video_incompatible_parameters(payload, params, image_to_video=image_to_video))
    return list(dict.fromkeys(incompatible))

async def project_conversation_adapter(request: Dict[str, Any]) -> Dict[str, Any]:
    """Run one project conversation turn through the injectable chat seam.

    The project node owns the durable messages and references.  The adapter
    receives only the current node's messages plus media explicitly connected
    to it, so the legacy global conversation store is never consulted.
    """
    project_id = str(request.get('project_id') or '').strip()
    references = [item for item in request.get('references') or [] if isinstance(item, dict)]
    _main('project_conversation_media_budget')(project_id, references)
    messages = [item for item in request.get('messages') or [] if isinstance(item, dict)]
    current_message = str(request.get('message') or '')
    current_message_id = str(request.get('current_message_id') or '')
    history_messages = messages
    if current_message_id:
        current_index = next((index for index, item in enumerate(messages) if str(item.get('id') or '') == current_message_id), None)
        if current_index is not None:
            history_messages = messages[:current_index]
    elif messages and messages[-1].get('role') == 'user' and (str(messages[-1].get('text') or '') == current_message):
        history_messages = messages[:-1]
    llm_messages = [{'role': item.get('role'), 'content': item.get('text') or ''} for item in history_messages if item.get('role') in {'user', 'assistant'} and str(item.get('text') or '').strip()]
    scope_token = PROJECT_MEDIA_ADAPTER_PROJECT.set(project_id)
    try:
        images = [reference_to_data_url({'url': project_media_adapter_reference(project_id, item['asset_id'])}, max_size=1536, max_bytes=_main('PROJECT_CONVERSATION_MEDIA_MAX_FILE_BYTES')) for item in references if str(item.get('kind') or '').strip().lower() == 'image' and item.get('asset_id')]
        videos = [media_adapter_data_url(project_media_adapter_reference(project_id, item['asset_id']), 'video', max_bytes=_main('PROJECT_CONVERSATION_MEDIA_MAX_FILE_BYTES')) for item in references if str(item.get('kind') or '').strip().lower() == 'video' and item.get('asset_id')]
        audio_references = [{'asset_id': str(item['asset_id']).strip(), 'kind': 'audio', 'name': str(item.get('name') or item['asset_id']).strip()[:160], 'url': media_adapter_data_url(project_media_adapter_reference(project_id, item['asset_id']), 'audio', max_bytes=_main('PROJECT_CONVERSATION_MEDIA_MAX_FILE_BYTES'))} for item in references if str(item.get('kind') or '').strip().lower() == 'audio' and item.get('asset_id')]
    finally:
        PROJECT_MEDIA_ADAPTER_PROJECT.reset(scope_token)
    payload = CanvasLLMRequest(message=current_message, system_prompt=str(request.get('system_prompt') or ''), model=str(request.get('model') or ''), messages=llm_messages, provider=str(request.get('provider') or 'comfly'), ms_model=str(request.get('ms_model') or ''), images=images, videos=videos, audios=[item['url'] for item in audio_references], audio_references=audio_references)
    result = await _main('canvas_llm')(payload)
    return {'text': str(result.get('text') or '接口返回了空回复。'), 'model': result.get('model') or str(request.get('model') or ''), 'raw_usage': result.get('raw_usage')}

def _photoshop_request_snapshot(request_id: str) -> Optional[Dict[str, Any]]:
    with PHOTOSHOP_ROUND_TRIP_LOCK:
        _main('_photoshop_prune_requests_locked')()
        record = PHOTOSHOP_ROUND_TRIP_REQUESTS.get(str(request_id or ''))
        if not record:
            return None
        result = copy.deepcopy(record)
        if result.get('state') == 'pending' and _main('now_ms')() >= int(result.get('expires_at_ms') or 0):
            record['state'] = 'expired'
            result['state'] = 'expired'
        if result.get('state') == 'processing':
            result['state'] = 'pending'
        result.pop('expires_at_ms', None)
        return result

def _photoshop_mark_returned(request_id: str, committed: Dict[str, Any]) -> Dict[str, Any]:
    """Publish the one-time result after durable project commit."""
    with PHOTOSHOP_ROUND_TRIP_LOCK:
        current = PHOTOSHOP_ROUND_TRIP_REQUESTS.get(str(request_id or ''))
        if not current:
            raise _photoshop_protocol_error(404, 'REQUEST_NOT_FOUND', 'Photoshop 往返请求不存在')
        current.update({'state': 'returned', 'returned_at': _main('now_ms')() // 1000, 'result_node_id': committed['result_node']['id'], 'result_media_id': committed['media']['asset_id']})
        return _photoshop_public_request(current)

async def finalize_visible_project_conversation_terminal(project_id: str, node_id: str, request_id: str) -> None:
    """Make terminal API visibility include all recovery-state cleanup."""
    forget_project_conversation_task(project_id, node_id, request_id)
    forget_project_conversation_ready_result(project_id, node_id, request_id)
    await asyncio.to_thread(_main('forget_project_conversation_durable'), project_id, node_id, request_id)
