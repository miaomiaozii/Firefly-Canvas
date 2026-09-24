"""Router: /api/project-library domain (auto-split from main.py)."""
from __future__ import annotations
import sys as _sys
import os as _os
import logging

logger = logging.getLogger(__name__)
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
from typing import Any, Optional
from fastapi import APIRouter, BackgroundTasks, File, Form, Header, HTTPException, Query, Request, Response, UploadFile, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse, StreamingResponse
from pydantic import BaseModel, Field
from app.core import (AIReference, Any, BackgroundTask, BaseModel, CANVAS_TASKS, CANVAS_TASK_LOCK, CanvasMutationBatch, CanvasVideoRequest, Dict, FLY_IMPORT_UPLOAD_LOCK, Field, FileResponse, FireflyProjectCreateRequest, Future, GLOBAL_LOOP, HTTPException, Image, ImageGenerationRequest, InvalidMutation, JSONResponse, LLM_MESSAGE_MAX_LENGTH, LimitedFlyMultipartParser, List, Lock, Mapping, ModelRoutingError, MultiPartException, MultiPartParser, ONLINE_IMAGE_PROMPT_MAX_LENGTH, ONLINE_IMAGE_REFERENCE_MAX, ONLINE_VIDEO_REFERENCE_MAX, Optional, PHOTOSHOP_ROUND_TRIP_LOCK, PHOTOSHOP_ROUND_TRIP_MAX_MEDIA_BYTES, PHOTOSHOP_ROUND_TRIP_MAX_REQUESTS, PHOTOSHOP_ROUND_TRIP_REQUESTS, PHOTOSHOP_ROUND_TRIP_RETENTION_MS, PROJECT_AGENT_ADMISSION_LOCK, PROJECT_AGENT_EXECUTION_LOCKS, PROJECT_AGENT_EXECUTION_LOCKS_GUARD, PROJECT_AGENT_FUTURES, PROJECT_AGENT_MAX_PER_PROJECT, PROJECT_AGENT_RUNNING_IDS, PROJECT_AGENT_TASKS, PROJECT_AGENT_TASKS_LOCK, PROJECT_CONVERSATION_ADMISSION_LOCK, PROJECT_CONVERSATION_MAX_AUDIO_REFERENCES, PROJECT_CONVERSATION_MAX_VIDEO_REFERENCES, PROJECT_CONVERSATION_TASKS, PROJECT_CONVERSATION_TASKS_LOCK, PROJECT_IMAGE_GENERATION_FUTURES, PROJECT_IMAGE_GENERATION_MAX_PENDING, PROJECT_IMAGE_GENERATION_MAX_PER_PROJECT, PROJECT_IMAGE_GENERATION_ORPHANS, PROJECT_LIBRARY_SWITCH_LOCK, PROJECT_MEDIA_ADAPTER_PROJECT, PROJECT_MEDIA_IMPORT_FUTURES, PROJECT_MEDIA_IMPORT_MAX_PENDING, PROJECT_MEDIA_IMPORT_TASKS, PROJECT_MEDIA_IMPORT_TASKS_LOCK, PROJECT_VIDEO_CANDIDATE_INCOMPATIBLE_KEY, PROJECT_VIDEO_GENERATION_FUTURES, PROJECT_VIDEO_GENERATION_ORPHANS, Path, PhotoshopRoundTripCreateRequest, PhotoshopRoundTripReturnRequest, ProjectConversationMessageRequest, ProjectHistoryDeleteRequest, ProjectImageGenerationRequest, ProjectLocalEditRequest, ProjectMediaImportActive, ProjectMediaImportRequest, ProjectNotFound, ProjectOriginalDeleteRequest, ProjectStore, ProjectStoreError, ProjectVideoGenerationRequest, Request, RevisionConflict, StarletteUploadFile, Tuple, VIDEO_PROMPT_MAX_LENGTH, VideoGenerationRequest, _AGENT_FORBIDDEN_VALUE_TOKENS, _AGENT_IMAGE_PARAM_SCHEMA, _AGENT_PROMPT_PARAM_SCHEMA, _AGENT_TASK_INPUT_KEYS, _AGENT_VIDEO_PARAM_SCHEMA, _agent_clean_params, _agent_execution_tasks_from_plan, _agent_find_task_node, _agent_forbidden_value, _agent_memory_task_active, _agent_nodes_by_id, _agent_normalize_task, _agent_plan_from_node, _agent_plan_hash, _agent_plan_response, _agent_public_task, _agent_recovery_payload, _agent_task_canonical, _agent_task_layout, _agent_task_specs, _ensure_photoshop_loopback, _local_upload_kind_ext, _os, _photoshop_protocol_error, _photoshop_public_request, _photoshop_reset_processing, _photoshop_validate_display_name, _photoshop_validate_media_type, _sys, acquire_fly_import_slot, apply_angle_preset, as_rgba, asyncio, base64, canvas_has_pending_conversation, composite_preserve, composite_rect, content_type_for_path, conversation_node_by_id, conversation_task_response, conversation_task_snapshot, copy, encode_png, ensure_project_image_generation_executor, ensure_project_media_import_executor, ensure_same_origin_request, extract_images, forget_project_conversation_ready_result, forget_project_conversation_task, functools, hashlib, httpx, inspect, is_agnes_provider, is_apimart_provider, is_jimeng_provider, is_runninghub_provider, is_volcengine_provider, is_yuli_provider, json, mimetypes, nullcontext, os, parse_size_pair, project_agent_execution_lock, project_agent_node, project_canvas_video_payload, project_has_active_image_generation, project_has_active_media_import, project_has_active_video_generation, project_has_recoverable_conversation_sidecars, project_lifecycle_lock, project_media_adapter_reference, project_video_candidate_preflight_incompatible_parameters, project_video_mark_candidate_incompatible, public_project_media_import_task, rasterize_preserve_mask, re, require_project_media_adapter_scope, reserve_project_image_task, reserve_project_video_task, runninghub_configured_video_model_definition, runninghub_is_image_to_video, runninghub_video_incompatible_parameters, selection_json, shutil, tempfile, time, urllib, uuid, validate_generated_image, validate_generated_video, validate_project_media_source)
from app.core import _AGENT_FORBIDDEN_VALUE_TOKENS, _AGENT_IMAGE_PARAM_SCHEMA, _AGENT_PROMPT_PARAM_SCHEMA, _AGENT_TASK_INPUT_KEYS, _AGENT_VIDEO_PARAM_SCHEMA, _agent_clean_params, _agent_execution_tasks_from_plan, _agent_find_task_node, _agent_forbidden_value, _agent_memory_task_active, _agent_nodes_by_id, _agent_normalize_task, _agent_plan_from_node, _agent_plan_hash, _agent_plan_response, _agent_public_task, _agent_recovery_payload, _agent_task_canonical, _agent_task_layout, _agent_task_specs, _ensure_photoshop_loopback, _local_upload_kind_ext, _photoshop_protocol_error, _photoshop_public_request, _photoshop_reset_processing, _photoshop_validate_display_name, _photoshop_validate_media_type, image_generation_task_timeout_seconds, ProjectOriginalProjectWideRequest, ProjectOriginalRestoreRequest
from app.project_activity import (
    ProjectCleanupActive,
    assert_project_owner_admissible,
    project_cleanup_lease,
    project_has_active_download,
    project_owner_admission,
)
from app.project_downloads import ProjectMediaFileResponse
from app.project_sessions import project_has_open_session
from firefly_projects import CleanupDrift
_from_main_names = ['FIREFLY_PROJECT_STORE', 'FLY_MAX_PACKAGE_BYTES', 'LOCAL_UPLOAD_DIR', 'PROJECT_AGENT_MAX_PENDING', 'PROJECT_AGENT_MAX_PER_NODE', 'PROJECT_CONVERSATION_MAX_IMAGE_REFERENCES', 'PROJECT_CONVERSATION_RUNNER', 'PROJECT_MEDIA_IMPORT_MAX_PER_PROJECT', 'PROJECT_VIDEO_REFERENCE_INLINE_MAX_BYTES', '_agent_active_counts', '_agent_apply_mutations', '_agent_mark_recovered_uncertain', '_agent_result_node', '_agent_submit_task', '_agent_task_node', '_local_upload_item', '_photoshop_mark_returned', '_photoshop_prune_requests_locked', '_photoshop_request_snapshot', 'archive_project_generated_image', 'archive_project_generated_video', 'build_project_media_preview', 'cleanup_project_generation_orphans', 'conversation_pending_task_keys', 'copy_project_media_to_global_library', 'execute_canvas_video', 'execute_project_video_generation', 'export_project_package', 'finalize_visible_project_conversation_terminal', 'forget_project_conversation_durable', 'generate_ai_image', 'generate_project_video', 'generate_video_preview_image', 'load_api_providers', 'local_edit_adapter', 'manager', 'merge_project_conversation_result', 'now_ms', 'output_file_from_url', 'persist_project_conversation_pending', 'preflight_project_video_providers', 'project_agent_cancel_task', 'project_agent_connected_inputs', 'project_agent_retry_task', 'project_conversation_connected_references', 'project_has_active_conversation', 'project_image_references', 'project_lifecycle_store_call', 'project_media_adapter_path', 'project_media_source_path', 'project_store_call', 'project_video_adapter_incompatible_parameters', 'project_video_all_references', 'project_video_candidate_execution_incompatible_parameters', 'project_video_candidate_inline_reference_kinds', 'project_video_references', 'provider_env_key_value', 'reserve_project_media_import', 'route_image_generation', 'route_video_generation', 'run_project_image_task', 'run_project_video_task', 'runninghub_api_key', 'runninghub_model_definition', 'runninghub_wallet_key_value', 'schedule_project_conversation_task', 'video_adapter_incompatible_parameters', 'video_api_root']
import app.services as _svc
for _nm in _from_main_names:
    _v = getattr(_app_main, _nm, None)
    if _v is None:
        _v = getattr(_svc, _nm, None)
    globals()[_nm] = _v

router = APIRouter()

PROJECT_EXPORTS_LOCK = Lock()
PROJECT_ACTIVE_EXPORTS: Dict[str, int] = {}

async def acquire_fly_import_slot() -> None:
    while not FLY_IMPORT_UPLOAD_LOCK.acquire(blocking=False):
        await asyncio.sleep(0.05)

def public_project_media_import_task(task: Dict[str, Any]) -> Dict[str, Any]:
    return {key: value for key, value in task.items() if key not in {'project_id', 'source_key'}}


class ProjectTrashRestoreRequest(BaseModel):
    asset_id: str
    base_revision: int = Field(ge=0)


def _normalize_generation_size_params(payload: Any, *, code: str, message: str) -> tuple:
    """Normalize empty size/resolution/aspect_ratio strings to None so they're treated
    as "not specified" rather than invalid values. The frontend sends empty strings for
    size/resolution/aspect_ratio when outpainting (扩图), and the routing logic only
    accepts None as "unspecified"."""
    size = (payload.size or '').strip() or None
    width, height = (payload.width, payload.height)
    parsed_width, parsed_height = parse_size_pair(size or '')
    if width is None:
        width = parsed_width or None
    if height is None:
        height = parsed_height or None
    if parsed_width and width != parsed_width or (parsed_height and height != parsed_height):
        raise HTTPException(status_code=400, detail={'code': code, 'message': message, 'incompatible_parameters': ['size', 'width', 'height']})
    aspect_ratio = (payload.aspect_ratio or '').strip() or None
    resolution = (payload.resolution or '').strip() or None
    return (size, width, height, aspect_ratio, resolution)

def project_image_request(payload: ProjectImageGenerationRequest) -> ImageGenerationRequest:
    if payload.media_type != 'image':
        raise HTTPException(status_code=400, detail={'code': 'unsupported_media_type', 'message': '图片生成任务的媒体类型必须是 image', 'incompatible_parameters': ['media_type']})
    if payload.routing_mode not in {'auto', 'manual'}:
        raise HTTPException(status_code=400, detail='路由模式必须是 auto 或 manual')
    if payload.routing_mode == 'manual' and (not (payload.provider_id or payload.model)):
        raise HTTPException(status_code=400, detail='手动路由至少需要固定服务商或模型')
    if payload.n < 1 or payload.n > 8:
        raise HTTPException(status_code=400, detail='单次生图数量必须在 1 到 8 之间')
    if len(payload.reference_asset_ids) > ONLINE_IMAGE_REFERENCE_MAX:
        raise HTTPException(status_code=400, detail={'code': 'no_compatible_image_model', 'message': '参考图片数量超过应用安全上限', 'incompatible_parameters': ['reference_count']})
    size, width, height, aspect_ratio, resolution = _normalize_generation_size_params(
        payload, code='inconsistent_image_dimensions', message='size 与 width/height 不一致'
    )
    return ImageGenerationRequest(prompt=payload.prompt, size=size, width=width, height=height, aspect_ratio=aspect_ratio, resolution=resolution, reference_asset_ids=tuple(payload.reference_asset_ids), media_type=payload.media_type, quality=payload.quality, count=payload.n)

def project_video_request(payload: ProjectVideoGenerationRequest) -> VideoGenerationRequest:
    if payload.media_type != 'video':
        raise HTTPException(status_code=400, detail={'code': 'unsupported_media_type', 'message': '视频生成任务的媒体类型必须是 video', 'incompatible_parameters': ['media_type']})
    if payload.routing_mode not in {'auto', 'manual'}:
        raise HTTPException(status_code=400, detail='路由模式必须是 auto 或 manual')
    if payload.routing_mode == 'manual' and (not (payload.provider_id or payload.model)):
        raise HTTPException(status_code=400, detail='手动路由至少需要固定服务商或模型')
    if payload.n < 1 or payload.n > 4:
        raise HTTPException(status_code=400, detail='单次视频数量必须在 1 到 4 之间')
    reference_groups = (('image_reference_count', payload.image_reference_asset_ids), ('video_reference_count', payload.video_reference_asset_ids), ('audio_reference_count', payload.audio_reference_asset_ids))
    for parameter, asset_ids in reference_groups:
        if len(asset_ids) > ONLINE_VIDEO_REFERENCE_MAX:
            raise HTTPException(status_code=400, detail={'code': 'no_compatible_video_model', 'message': '参考素材数量超过应用安全上限', 'incompatible_parameters': [parameter]})
    size, width, height, aspect_ratio, resolution = _normalize_generation_size_params(
        payload, code='inconsistent_video_dimensions', message='size 与 width/height 不一致'
    )
    return VideoGenerationRequest(prompt=payload.prompt, size=size, width=width, height=height, aspect_ratio=aspect_ratio, resolution=resolution, duration=payload.duration, image_reference_asset_ids=tuple(payload.image_reference_asset_ids), video_reference_asset_ids=tuple(payload.video_reference_asset_ids), audio_reference_asset_ids=tuple(payload.audio_reference_asset_ids), media_type=payload.media_type, quality=payload.quality, count=payload.n, seed=payload.seed, enhance_prompt=payload.enhance_prompt, enable_upsample=payload.enable_upsample, watermark=payload.watermark, camera_fixed=payload.camera_fixed, return_last_frame=payload.return_last_frame, generate_audio=payload.generate_audio)

def project_image_references(project_id: str, asset_ids: List[str]) -> List[Dict[str, Any]]:
    references = []
    for index, asset_id in enumerate(asset_ids):
        record = _app_main.project_store_call('media_record', project_id, asset_id)
        if record.get('kind') != 'image':
            raise ProjectStoreError(f'参考资源不是图片: {asset_id}')
        references.append({'url': f"/api/project-library/{urllib.parse.quote(project_id, safe='')}/media/{urllib.parse.quote(asset_id, safe='')}", 'name': record.get('name') or f'reference-{index + 1}.png', 'kind': 'image', 'mime': record.get('mime_type') or 'image/png', 'asset_id': asset_id})
    return references

def project_video_references(project_id: str, asset_ids: List[str], kind: str) -> List[Dict[str, Any]]:
    references = []
    for index, asset_id in enumerate(asset_ids):
        try:
            record = _app_main.project_store_call('media_record', project_id, asset_id)
        except ProjectNotFound as exc:
            raise ProjectStoreError(f'参考资源不属于当前项目: {asset_id}') from exc
        if record.get('kind') != kind:
            raise ProjectStoreError(f"参考资源不是{ {'image': '图片', 'video': '视频', 'audio': '音频'}[kind]}: {asset_id}")
        source_path = Path(str(record.get('path') or '')).resolve()
        if not source_path.is_file():
            raise ProjectNotFound(f'项目媒体不存在: {asset_id}')
        references.append({'asset_id': asset_id, 'kind': kind, 'name': record.get('name') or f'reference-{index + 1}', 'mime': record.get('mime_type') or 'application/octet-stream', 'path': str(source_path), 'adapter_url': project_media_adapter_reference(project_id, asset_id)})
    return references

def project_video_all_references(project_id: str, payload: ProjectVideoGenerationRequest) -> List[Dict[str, Any]]:
    return [*project_video_references(project_id, payload.image_reference_asset_ids, 'image'), *project_video_references(project_id, payload.video_reference_asset_ids, 'video'), *project_video_references(project_id, payload.audio_reference_asset_ids, 'audio')]

def project_agent_execution_lock(project_id: str, node_id: str) -> Lock:
    key = (str(project_id), str(node_id))
    with PROJECT_AGENT_EXECUTION_LOCKS_GUARD:
        return PROJECT_AGENT_EXECUTION_LOCKS.setdefault(key, Lock())

def project_agent_connected_inputs(project_id: str, canvas: Mapping[str, Any], node_id: str) -> Dict[str, Any]:
    """Read only prompt/media nodes on direct incoming input edges.

    In particular, this intentionally does not walk a graph, inspect the
    project's other nodes, or trust asset URLs supplied by the client.  Every
    media identifier is resolved through the owning ProjectStore before it is
    admitted to the plan.
    """
    nodes = _agent_nodes_by_id(canvas)
    source_ids: List[str] = []
    for edge in canvas.get('connections') or []:
        if not isinstance(edge, dict):
            continue
        target = edge.get('to') or edge.get('target') or edge.get('toNode') or edge.get('targetNodeId')
        source = edge.get('from') or edge.get('source') or edge.get('fromNode') or edge.get('sourceNodeId')
        kind = str(edge.get('kind') or 'flow')
        if str(target or '') == str(node_id) and source and (kind in {'input', 'flow'}):
            source_ids.append(str(source))
    prompts: List[str] = []
    prompt_node_ids: List[str] = []
    media: List[Dict[str, Any]] = []
    seen_assets: set[str] = set()
    for source_id in source_ids:
        source = nodes.get(source_id)
        if not isinstance(source, dict):
            continue
        source_type = str(source.get('type') or '')
        if source_type == 'smart-prompt':
            text = str(source.get('text') or source.get('prompt') or '').strip()
            if text:
                prompts.append(text[:ONLINE_IMAGE_PROMPT_MAX_LENGTH])
                prompt_node_ids.append(source_id)
            continue
        if source_type not in {'', 'image', 'smart-image'}:
            continue
        for item in source.get('images') or []:
            if not isinstance(item, dict):
                continue
            asset_id = str(item.get('asset_id') or '').strip()
            if not asset_id or asset_id in seen_assets:
                continue
            try:
                record = _app_main.project_store_call('media_record', project_id, asset_id)
            except ProjectNotFound as exc:
                raise HTTPException(status_code=400, detail={'code': 'agent_input_media_missing', 'message': f'Agent 的输入媒体不属于当前项目: {asset_id}'}) from exc
            except ProjectStoreError as exc:
                raise HTTPException(status_code=400, detail=str(exc)) from exc
            expected_kind = str(item.get('kind') or record.get('kind') or '').strip().lower()
            actual_kind = str(record.get('kind') or '').strip().lower()
            if expected_kind not in {'image', 'video', 'audio'} or expected_kind != actual_kind:
                raise HTTPException(status_code=400, detail={'code': 'agent_input_media_kind_mismatch', 'message': f'Agent 输入媒体类型不匹配: {asset_id}'})
            seen_assets.add(asset_id)
            media.append({'asset_id': asset_id, 'kind': actual_kind, 'name': str(record.get('name') or item.get('name') or asset_id)[:160]})
    return {'prompt': '\n\n'.join(prompts).strip(), 'prompts': prompts, 'prompt_node_ids': prompt_node_ids, 'media': media}

def _agent_forbidden_value(value: Any) -> bool:
    """Reject operation/path-like values recursively, including nested lists."""
    if isinstance(value, dict):
        for key, item in value.items():
            key_text = str(key or '').strip().lower().replace('-', '_')
            if key_text in {'operation', 'action', 'command', 'target', 'engine', 'projectid', 'project_id', 'workspace_id', 'canvas_id'}:
                return True
            if any((token in key_text for token in ('api_key', 'apikey', 'secret', 'password', 'token', 'url', 'uri', 'path', 'file', 'href', 'locator', 'project_library', 'photoshop', 'comfy', 'local_runtime'))):
                return True
            if _agent_forbidden_value(item):
                return True
    elif isinstance(value, list):
        return any((_agent_forbidden_value(item) for item in value))
    elif isinstance(value, str):
        text = value.strip().lower()
        if any((token in text for token in _AGENT_FORBIDDEN_VALUE_TOKENS)) or text.startswith(('file:', '\\\\', '/', '../', '..\\', 'http://', 'https://')) or re.match('^[a-z]:[\\\\/]', text) or ('\\' in text) or ('://' in text):
            return True
    return False

def _agent_clean_params(value: Any, kind: str='') -> Dict[str, Any]:
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise HTTPException(status_code=400, detail='Agent 任务参数必须是对象')
    if _agent_forbidden_value(value):
        raise HTTPException(status_code=400, detail={'code': 'agent_forbidden_operation', 'message': 'Agent 参数包含被禁止的删除、全局、跨项目、PS、本地运行时或路径操作'})
    schema = _AGENT_PROMPT_PARAM_SCHEMA if kind == 'prompt' else _AGENT_VIDEO_PARAM_SCHEMA if kind == 'video' else _AGENT_IMAGE_PARAM_SCHEMA
    params: Dict[str, Any] = {}
    for key, item in value.items():
        key_text = str(key)
        if key_text not in schema:
            raise HTTPException(status_code=400, detail={'code': 'agent_parameter_unknown', 'message': f'Agent 不允许参数字段: {key_text}'})
        expected_types = schema[key_text]
        if not isinstance(item, expected_types) or (int in expected_types and isinstance(item, bool)):
            raise HTTPException(status_code=400, detail={'code': 'agent_parameter_type', 'message': f'Agent 参数 {key_text} 类型无效'})
        if isinstance(item, str) and len(item) > 2000:
            raise HTTPException(status_code=400, detail=f'Agent 参数 {key_text} 过长')
        if key_text in {'width', 'height'} and (not 1 <= int(item) <= 16384):
            raise HTTPException(status_code=400, detail=f'Agent 参数 {key_text} 超出范围')
        if key_text == 'duration' and (not 1 <= int(item) <= 60):
            raise HTTPException(status_code=400, detail='Agent 视频时长必须在 1 到 60 秒之间')
        if key_text == 'n' and (not 1 <= int(item) <= 8):
            raise HTTPException(status_code=400, detail='Agent 参数 n 超出范围')
        if key_text == 'reference_asset_ids':
            if any((not isinstance(asset_id, str) or not re.fullmatch('[A-Za-z0-9._-]{1,200}', asset_id) for asset_id in item)):
                raise HTTPException(status_code=400, detail='Agent 参考素材标识无效')
        params[key_text] = copy.deepcopy(item)
    try:
        encoded = json.dumps(params, ensure_ascii=False)
    except (TypeError, ValueError) as exc:
        raise HTTPException(status_code=400, detail='Agent 任务参数必须可序列化') from exc
    if len(encoded.encode('utf-8')) > 64 * 1024:
        raise HTTPException(status_code=400, detail='Agent 任务参数超过 64KB 限制')
    return params

def _agent_public_task(task: Mapping[str, Any]) -> Dict[str, Any]:
    return {key: copy.deepcopy(value) for key, value in task.items() if key not in {'adapter_request', 'raw_result'}}

def _agent_active_counts(project_id: str, agent_id: str='') -> Tuple[int, int, int]:
    global_count = project_count = node_count = 0
    with PROJECT_AGENT_TASKS_LOCK:
        for task in PROJECT_AGENT_TASKS.values():
            if task.get('status') not in {'queued', 'running'}:
                continue
            global_count += 1
            if str(task.get('project_id') or '') == str(project_id):
                project_count += 1
                if str(task.get('agent_id') or '') == str(agent_id):
                    node_count += 1
    try:
        projects = _app_main.project_store_call('list_projects')
    except Exception:
        projects = []
    for project in projects:
        candidate_project = str(project.get('id') or '')
        try:
            opened = _app_main.project_store_call('open_project', candidate_project)
        except Exception:
            continue
        for node in opened['canvas'].get('nodes') or []:
            if not isinstance(node, dict) or str(node.get('type') or '') != 'agent-task':
                continue
            if str(node.get('status') or '') not in {'queued', 'running'}:
                continue
            task_id = str(node.get('task_id') or node.get('id') or '')
            with PROJECT_AGENT_TASKS_LOCK:
                if task_id in PROJECT_AGENT_TASKS:
                    continue
            global_count += 1
            if candidate_project == str(project_id):
                project_count += 1
                if str(node.get('agent_id') or '') == str(agent_id):
                    node_count += 1
    return (global_count, project_count, node_count)

async def generate_project_video(request_snapshot: VideoGenerationRequest, route, references: List[Dict[str, Any]], provider: Optional[Mapping[str, Any]]=None, on_provider_task=None) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
    """Adapt project-owned references to the existing online video adapters."""
    legacy_payload = project_canvas_video_payload(request_snapshot, route.provider_id, route.model, references)
    requested_count = max(1, int(request_snapshot.count or 1))
    candidates = []
    raw_results = []
    for _ in range(requested_count):
        result = await _app_main.execute_canvas_video(legacy_payload, provider, on_provider_task=on_provider_task) if provider is not None else await _app_main.canvas_video(legacy_payload)
        videos = [str(value or '').strip() for value in result.get('videos') or [] if str(value or '').strip()]
        if not videos:
            raise ProjectStoreError('在线模型没有返回视频')
        candidates.extend(({'type': 'url', 'value': value, 'mime_type': content_type_for_path(value)} for value in videos))
        if isinstance(result, dict):
            raw_results.append(result.get('raw') if isinstance(result.get('raw'), dict) else {})
    raw = raw_results[0] if requested_count == 1 else {'results': raw_results}
    return (candidates, raw)

async def execute_project_video_generation(project_id: str, request_snapshot: VideoGenerationRequest, route, references: List[Dict[str, Any]], provider: Optional[Mapping[str, Any]]=None, on_provider_task=None) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
    """Run one adapter call inside a task-owned project-media capability scope."""
    clean_project_id = str(project_id or '').strip()
    if not clean_project_id:
        raise ProjectStoreError('在线视频任务缺少项目标识')
    scope_token = PROJECT_MEDIA_ADAPTER_PROJECT.set(clean_project_id)
    try:
        for item in references:
            adapter_url = item.get('adapter_url') or ''
            require_project_media_adapter_scope(adapter_url)
            project_media_adapter_path(adapter_url, item.get('kind') or '')
        if provider is None:
            return await _app_main.generate_project_video(request_snapshot, route, references, on_provider_task=on_provider_task)
        return await _app_main.generate_project_video(request_snapshot, route, references, provider=provider, on_provider_task=on_provider_task)
    finally:
        PROJECT_MEDIA_ADAPTER_PROJECT.reset(scope_token)

PROJECT_IMAGE_PROGRESS_PHASES = {
    'queued': (0, '排队'),
    'preparing_reference': (1, '准备参考图'),
    'uploading': (2, '上传'),
    'provider_generating': (3, '模型生成'),
    'receiving': (4, '接收结果'),
    'saving': (5, '写入画布'),
    'completed': (6, '已完成'),
    'cancelled': (6, '已取消'),
    'timeout': (6, '已超时'),
}


def update_project_image_task_progress(task_id: str, phase: str, **details):
    phase_value = str(phase or '').strip()
    if phase_value not in PROJECT_IMAGE_PROGRESS_PHASES:
        return
    now = _app_main.now_ms()
    phase_index, label = PROJECT_IMAGE_PROGRESS_PHASES[phase_value]
    with CANVAS_TASK_LOCK:
        task = CANVAS_TASKS.get(task_id)
        if not isinstance(task, dict):
            return
        previous = task.get('progress') if isinstance(task.get('progress'), dict) else {}
        previous_index = int(previous.get('phase_index') or 0)
        if phase_index < previous_index:
            # 单相前进只约束“阶段”本身；candidate_phases 是各候选的并发实时状态，
            # 一张已完成（completed=6）后其他候选仍在推进，若这里直接 return，
            # 后续候选的阶段更新会被全部丢弃，前端每格进度就不动了。
            for key in ('bytes_done', 'bytes_total', 'current', 'total'):
                if details.get(key) is not None:
                    previous[key] = max(0, int(details[key]))
            if details.get('candidate_phases') is not None:
                previous['candidate_phases'] = [str(item) for item in details['candidate_phases']][:32]
            if details.get('failed'):
                previous['failed'] = True
            task['progress'] = previous
            task['updated_at'] = time.time()
            return
        started_at = int(previous.get('started_at') or round(float(task.get('created_at') or time.time()) * 1000))
        progress = {
            'phase': phase_value,
            'label': label,
            'phase_index': phase_index,
            'phase_count': 6,
            'started_at': started_at,
            'phase_started_at': int(previous.get('phase_started_at') or now) if phase_index == previous_index else now,
            'elapsed_ms': max(0, now - started_at),
        }
        for key in ('bytes_done', 'bytes_total', 'current', 'total'):
            if details.get(key) is not None:
                progress[key] = max(0, int(details[key]))
            elif key in previous:
                # 重建时继承上一版细节：不带 candidate_phases/字节的 update（如
                # 参考图上传进度）不能把已写入的每格阶段抹掉。
                progress[key] = previous[key]
        if details.get('candidate_phases') is not None:
            progress['candidate_phases'] = [str(phase) for phase in details['candidate_phases']][:32]
        elif previous.get('candidate_phases') is not None:
            progress['candidate_phases'] = list(previous['candidate_phases'])
        if details.get('failed'):
            progress['failed'] = True
        elif previous.get('failed'):
            progress['failed'] = True
        task['progress'] = progress
        task['updated_at'] = time.time()


def fail_project_image_task_progress(task_id: str):
    with CANVAS_TASK_LOCK:
        task = CANVAS_TASKS.get(task_id)
        if not isinstance(task, dict):
            return
        progress = dict(task.get('progress') or {})
        started_at = int(progress.get('started_at') or round(float(task.get('created_at') or time.time()) * 1000))
        progress.update({'failed': True, 'elapsed_ms': max(0, _app_main.now_ms() - started_at)})
        task['progress'] = progress


def durable_project_image_task(canvas: Mapping[str, Any], task_id: str) -> Optional[Dict[str, Any]]:
    clean_task_id = str(task_id or '').strip()
    # A terminal history record is the durable commit marker.  Read it before a
    # stale pending node so a crash between the two snapshot writes cannot turn
    # an already-paid success into a synthetic restart failure.
    for record in reversed(canvas.get('history') or []):
        if not isinstance(record, dict) or str(record.get('task_id') or record.get('taskId') or '') != clean_task_id:
            continue
        return {
            'id': clean_task_id,
            'task_id': clean_task_id,
            'type': 'project-online-image',
            'project_id': canvas.get('project_id') or canvas.get('project') or '',
            'result_node_id': record.get('result_node_id') or '',
            'status': record.get('status') or 'failed',
            'provider_id': record.get('actual_provider_id') or '',
            'model': record.get('actual_model') or record.get('model') or '',
            'route': copy.deepcopy(record.get('route') or {}),
            'request_payload': copy.deepcopy(record.get('request_payload') or {}),
            'idempotency_key': record.get('idempotency_key') or clean_task_id,
            'error': record.get('error') or '',
            'history_record': copy.deepcopy(record),
        }
    for node in canvas.get('nodes') or []:
        if not isinstance(node, dict):
            continue
        for pending in node.get('pendingTasks') or []:
            if not isinstance(pending, dict) or str(pending.get('taskId') or '') != clean_task_id:
                continue
            return {
                'id': clean_task_id,
                'task_id': clean_task_id,
                'type': 'project-online-image',
                'project_id': canvas.get('project_id') or canvas.get('project') or '',
                'result_node_id': node.get('id') or '',
                'status': 'pending',
                'provider_id': pending.get('providerId') or '',
                'model': pending.get('model') or '',
                'route': copy.deepcopy(pending.get('route') or {}),
                'request_payload': copy.deepcopy(pending.get('requestPayload') or {}),
                'idempotency_key': pending.get('idempotencyKey') or clean_task_id,
                'created_at': float(pending.get('createdAt') or pending.get('created_at') or time.time()),
            }
    return None


def durable_project_image_task_by_idempotency(
    canvas: Mapping[str, Any],
    idempotency_key: str,
) -> Optional[Dict[str, Any]]:
    clean_key = str(idempotency_key or '').strip()
    if not clean_key:
        return None
    for node in canvas.get('nodes') or []:
        if not isinstance(node, dict):
            continue
        for pending in node.get('pendingTasks') or []:
            if not isinstance(pending, dict) or str(pending.get('idempotencyKey') or '') != clean_key:
                continue
            return durable_project_image_task(canvas, str(pending.get('taskId') or ''))
    for record in canvas.get('history') or []:
        if not isinstance(record, dict) or str(record.get('idempotency_key') or '') != clean_key:
            continue
        return durable_project_image_task(canvas, str(record.get('task_id') or record.get('taskId') or ''))
    return None


def canvas_has_project_image_pending(canvas: Mapping[str, Any], task_id: str) -> bool:
    clean_task_id = str(task_id or '').strip()
    return any(
        str(pending.get('taskId') or '') == clean_task_id
        for node in canvas.get('nodes') or []
        if isinstance(node, dict)
        for pending in node.get('pendingTasks') or []
        if isinstance(pending, dict)
    )


def durable_project_image_result(project_id: str, canvas: Mapping[str, Any], durable: Mapping[str, Any]) -> Dict[str, Any]:
    history_record = copy.deepcopy(durable.get('history_record') or {})
    base = f"/api/project-library/{urllib.parse.quote(project_id, safe='')}/media/"
    image_items = [
        {
            **copy.deepcopy(item),
            'url': base + urllib.parse.quote(str(item.get('asset_id') or ''), safe=''),
        }
        for item in history_record.get('outputs') or []
        if isinstance(item, dict) and item.get('asset_id')
    ]
    final_node = next(
        (item for item in canvas.get('nodes') or [] if item.get('id') == durable.get('result_node_id')),
        None,
    )
    return {
        'prompt': history_record.get('prompt') or '',
        'images': [item['url'] for item in image_items],
        'image_items': image_items,
        'history_recorded': bool(history_record),
        'history_record': history_record,
        'project_id': project_id,
        'project_revision': int(canvas.get('revision') or 0),
        'result_node_id': durable.get('result_node_id') or '',
        'final_node': final_node,
    }


def recover_project_image_task(
    project_id: str,
    task_id: str,
    *,
    termination_reason: str = 'service_restart_unknown',
    termination_error: str = '服务重启后无法确认任务是否已调用在线模型；为避免重复计费，任务已终止，请显式重试',
) -> tuple[Dict[str, Any], Optional[Dict[str, Any]]]:
    """Reconcile one durable task under the project lock after registry loss."""
    with project_lifecycle_lock(project_id):
        opened = _app_main.project_store_call('open_project', project_id)
        canvas = opened.get('canvas') or {}
        durable = durable_project_image_task(canvas, task_id)
        if durable is None:
            return opened, None
        if durable.get('status') == 'pending':
            request_payload = copy.deepcopy(durable.get('request_payload') or {})
            failed_at = _app_main.now_ms()
            failure_record = {
                'id': f'generation-{uuid.uuid4().hex}',
                'task_id': task_id,
                'idempotency_key': durable.get('idempotency_key') or task_id,
                'type': 'online-image',
                'status': 'failed',
                'prompt': request_payload.get('prompt') or '',
                'created_at': failed_at,
                'createdAt': failed_at,
                'completed_at': failed_at,
                'runMs': 0,
                'request_payload': request_payload,
                'route': copy.deepcopy(durable.get('route') or {}),
                'result_node_id': durable.get('result_node_id') or '',
                'termination_reason': termination_reason,
                'error': termination_error,
                'outputs': [],
            }
            canvas = _app_main.project_store_call(
                'fail_image_generation',
                project_id,
                durable.get('result_node_id') or '',
                task_id,
                failure_record,
                require_pending=True,
            )
            _app_main.project_store_call('flush', project_id)
            durable = durable_project_image_task(canvas, task_id)
            return {'canvas': canvas}, durable
        if canvas_has_project_image_pending(canvas, task_id):
            history_record = copy.deepcopy(durable.get('history_record') or {})
            if durable.get('status') == 'succeeded':
                canvas = _app_main.project_store_call(
                    'complete_image_generation',
                    project_id,
                    durable.get('result_node_id') or '',
                    copy.deepcopy(history_record.get('outputs') or []),
                    history_record,
                    task_id=task_id,
                    require_pending=True,
                )
            elif durable.get('status') == 'cancelled':
                canvas = _app_main.project_store_call(
                    'cancel_image_generation',
                    project_id,
                    durable.get('result_node_id') or '',
                    task_id,
                    history_record,
                )
            else:
                canvas = _app_main.project_store_call(
                    'fail_image_generation',
                    project_id,
                    durable.get('result_node_id') or '',
                    task_id,
                    history_record,
                    require_pending=True,
                )
            _app_main.project_store_call('flush', project_id)
            durable = durable_project_image_task(canvas, task_id)
            return {'canvas': canvas}, durable
        return opened, durable


def project_image_idempotency_signature(payload: Any) -> str:
    if isinstance(payload, BaseModel):
        data = payload.model_dump()
    elif isinstance(payload, Mapping):
        data = copy.deepcopy(dict(payload))
    else:
        data = {}
    data.pop('idempotency_key', None)
    data.pop('base_revision', None)
    return json.dumps(data, ensure_ascii=False, sort_keys=True, separators=(',', ':'))


def ensure_project_image_idempotency_match(existing: Mapping[str, Any], payload: ProjectImageGenerationRequest) -> None:
    existing_payload = existing.get('request_payload')
    if existing_payload and project_image_idempotency_signature(existing_payload) != project_image_idempotency_signature(payload):
        raise HTTPException(status_code=409, detail={
            'code': 'image_generation_idempotency_conflict',
            'message': '同一幂等键已用于不同的生成请求，请创建新的操作',
        })


def project_image_task_cancel_requested(task_id: str) -> bool:
    with CANVAS_TASK_LOCK:
        task = CANVAS_TASKS.get(task_id)
        return bool(
            isinstance(task, dict)
            and (task.get('cancel_requested') or task.get('status') == 'cancelled')
        )


def raise_if_project_image_task_cancelled(task_id: str) -> None:
    if project_image_task_cancel_requested(task_id):
        raise asyncio.CancelledError()


class ImageGenerationTaskTimeout(Exception):
    """任务级硬超时：provider 挂起时终止任务并落 timeout 终态。"""


class VideoGenerationTaskTimeout(Exception):
    """视频任务级硬超时：与图片同一约束——provider 挂起必须到 timeout 终态（#80）。"""


def project_video_task_cancel_requested(task_id: str) -> bool:
    with CANVAS_TASK_LOCK:
        task = CANVAS_TASKS.get(task_id)
        return bool(
            isinstance(task, dict)
            and (task.get('cancel_requested') or task.get('status') == 'cancelled')
        )


class _CandidateWriteFailure(Exception):
    """候选在写回阶段（下载/归档）失败，区别于 provider 生成失败。"""

    def __init__(self, cause):
        super().__init__(str(getattr(cause, 'detail', None) or cause or '未知错误'))
        self.cause = cause


async def run_project_image_task(task_id: str, project_id: str, payload: ProjectImageGenerationRequest, request_snapshot: ImageGenerationRequest, route, registered_revision: int):
    started_at = _app_main.now_ms()
    generated_items = []
    preexisting_asset_ids = set()
    with CANVAS_TASK_LOCK:
        task = CANVAS_TASKS.get(task_id)
        if not isinstance(task, dict) or task.get('cancel_requested') or task.get('status') == 'cancelled':
            return
        task['status'] = 'running'
        task['updated_at'] = time.time()
    update_project_image_task_progress(task_id, 'preparing_reference')
    try:
        preexisting_asset_ids = {item.get('asset_id') for item in await asyncio.to_thread(_app_main.project_store_call, 'list_media', project_id) if item.get('asset_id')}
        references = await asyncio.to_thread(project_image_references, project_id, payload.reference_asset_ids)
        executed_prompt = apply_angle_preset(payload.prompt, payload.angle_preset)
        image_parameters = {'width': request_snapshot.width, 'height': request_snapshot.height, 'aspect_ratio': request_snapshot.aspect_ratio, 'resolution': request_snapshot.resolution, 'media_type': request_snapshot.media_type}
        if not references:
            update_project_image_task_progress(task_id, 'provider_generating')
        scope_token = PROJECT_MEDIA_ADAPTER_PROJECT.set(project_id)
        def progress_callback(phase, **details):
            update_project_image_task_progress(task_id, phase, **details)
        # 每张候选各自的状态（前端按格显示真实进度）：同一任务级 phase 单调前进，
        # candidate_phases 通过 details 携带，每次更新时重新计算“最高阶段”作为任务级 phase。
        candidate_phases = ['queued'] * payload.n
        candidate_lock = asyncio.Lock()

        async def set_candidate_phase(index, phase):
            if not (isinstance(index, int) and 0 <= index < len(candidate_phases)):
                return
            async with candidate_lock:
                candidate_phases[index] = phase
                # 任务级 phase 取“最慢”的非 completed 候选：一张完成后 top 若直接取
                # max，completed(6) 会压过其他仍在生成的候选，任务面板会提前显示
                # “已完成”，与仍在转的「生成中」胶囊矛盾。
                active_phases = [p for p in candidate_phases if p != 'completed'] or ['queued']
                top_phase = max(active_phases, key=lambda name: PROJECT_IMAGE_PROGRESS_PHASES.get(name, (0, ''))[0])
                update_project_image_task_progress(task_id, top_phase, candidate_phases=list(candidate_phases))
        try:
            # #41: count=8 连发——受控并发（每 provider 信号量 + 429 退避，见
            # run_provider_generation_with_backoff）+ 任务级硬超时。顺序串行会让用户
            # 看到「间隔很久一张一张传」；无 deadline 则 provider 挂起时任务永远
            # running、前端永远收不到终态。
            timeout_seconds = image_generation_task_timeout_seconds()
            # partial 逐张写回共用的 history id：running 进度记录与最终 succeeded 记录同 id 覆盖。
            progress_history_id = f'generation-{uuid.uuid4().hex}'

            async def generate_one_candidate(candidate_index):
                candidate_start = _app_main.now_ms()
                raise_if_project_image_task_cancelled(task_id)
                await set_candidate_phase(candidate_index, 'uploading' if references else 'provider_generating')
                image_data, raw = await _app_main.run_provider_generation_with_backoff(
                    route.provider_id,
                    _app_main.generate_ai_image,
                    executed_prompt,
                    payload.size,
                    payload.quality,
                    route.model,
                    references,
                    route.provider_id,
                    preserve_model=True,
                    image_parameters=image_parameters,
                    progress_callback=progress_callback,
                )
                raise_if_project_image_task_cancelled(task_id)
                try:
                    candidates = extract_images(raw) if isinstance(raw, dict) else []
                except HTTPException:
                    candidates = []
                if not candidates:
                    candidates = [image_data]
                provider_elapsed = _app_main.now_ms() - candidate_start
                await set_candidate_phase(candidate_index, 'receiving')
                update_project_image_task_progress(task_id, 'saving')
                for candidate in candidates:
                    raise_if_project_image_task_cancelled(task_id)
                    await set_candidate_phase(candidate_index, 'saving')
                    archive_start = _app_main.now_ms()
                    try:
                        item = await archive_project_generated_image(project_id, candidate, len(generated_items))
                    except BaseException as exc:
                        raise _CandidateWriteFailure(exc) from exc
                    logger.info(f'[gen-task {task_id[:8]}] provider={provider_elapsed}ms archive={_app_main.now_ms() - archive_start}ms item={len(generated_items) + 1}/{payload.n}')
                    generated_items.append(item)
                    update_project_image_task_progress(task_id, 'saving', current=len(generated_items), total=payload.n)
                    # 逐张写回：图片一归档就落到节点上（保留 pendingTasks 让前端继续显示剩余占位），
                    # 前端可在任务运行中逐个看到已完成的结果；单张失败不中断整体任务（final 会带上全量）。
                    partial_start = _app_main.now_ms()
                    partial_timestamp = partial_start
                    partial_record = {'id': progress_history_id, 'task_id': task_id, 'idempotency_key': payload.idempotency_key or task_id, 'type': 'online-image', 'status': 'running', 'prompt': payload.prompt, 'executed_prompt': executed_prompt, 'angle_preset': payload.angle_preset, 'created_at': started_at, 'createdAt': started_at, 'updated_at': partial_timestamp, 'completed_at': partial_timestamp, 'runMs': max(0, partial_timestamp - started_at), 'routing_mode': payload.routing_mode, 'requested_provider_id': payload.provider_id, 'requested_model': payload.model, 'actual_provider_id': route.provider_id, 'actual_provider_name': route.provider_name, 'actual_model': route.model, 'model': route.model, 'route': route.to_dict(), 'route_reason': route.reason, 'params': request_snapshot.to_dict(), 'request_payload': payload.model_dump(), 'outputs': [{'asset_id': item['asset_id'], 'kind': 'image'}], 'result_node_id': payload.result_node_id}
                    partial_canvas = None
                    try:
                        partial_canvas = await asyncio.to_thread(
                            _app_main.project_store_call,
                            'complete_image_generation',
                            project_id,
                            payload.result_node_id,
                            [item],
                            partial_record,
                            task_id=task_id,
                            require_pending=True,
                            partial=True,
                        )
                    except Exception:
                        # 单张 partial 写回失败不中断任务：final complete 仍会携带全量
                        # media（store 层按 history id 去重合并，不会重复）。
                        pass
                    logger.info(f'[gen-task {task_id[:8]}] partial complete={_app_main.now_ms() - partial_start}ms')
                    if partial_canvas is not None:
                        try:
                            flush_start = _app_main.now_ms()
                            await asyncio.to_thread(_app_main.project_store_call, 'flush', project_id)
                            await manager.broadcast_canvas_updated(project_id, int(partial_canvas.get('updated_at') or partial_timestamp))
                            logger.info(f'[gen-task {task_id[:8]}] flush+broadcast={_app_main.now_ms() - flush_start}ms')
                        except Exception:
                            pass
                await set_candidate_phase(candidate_index, 'completed')
            try:
                candidate_results = await asyncio.wait_for(
                    asyncio.gather(*(generate_one_candidate(i) for i in range(payload.n)), return_exceptions=True),
                    timeout=timeout_seconds,
                )
            except (TimeoutError, asyncio.TimeoutError):
                raise ImageGenerationTaskTimeout(f'生成任务执行超时（{timeout_seconds:g}s），已终止') from None
            # 候选级隔离：个别候选失败不再拖垮整批。只对写回失败（下载/归档）补偿重试，
            # provider 生成失败不补偿（退避重试已在适配器内做完），避免把测试/上游的
            # 一次性失败（fail_then_succeed）错误地“救活”成成功。
            candidate_failures = [(index, result) for index, result in enumerate(candidate_results) if isinstance(result, BaseException)]
            hard_failures = [(index, failure) for index, failure in candidate_failures if not isinstance(failure, _CandidateWriteFailure)]
            retryable = [(index, failure) for index, failure in candidate_failures if isinstance(failure, _CandidateWriteFailure)]
            if retryable and not project_image_task_cancel_requested(task_id):
                for index, failure in retryable:
                    logger.warning(f'[gen-task {task_id[:8]}] candidate #{index} write-back failed ({type(failure).__name__}: {str(failure)[:140]}), compensating')
                retry_results = await asyncio.gather(
                    *(generate_one_candidate(index) for index, _ in retryable[:4]),
                    return_exceptions=True,
                )
                for (index, _), result in zip(retryable[:4], retry_results):
                    if isinstance(result, BaseException):
                        hard_failures.append((index, result))
            candidate_failures = hard_failures
            if candidate_failures:
                def _unwrap(failure):
                    return failure.cause if isinstance(failure, _CandidateWriteFailure) else failure
                if not generated_items:
                    raise _unwrap(candidate_failures[0][1])
                failure_text = '；'.join(dict.fromkeys(str(getattr(_unwrap(failure), 'detail', None) or _unwrap(failure) or '未知错误')[:160] for _, failure in candidate_failures))
                raise ProjectStoreError(f'{len(candidate_failures)} 张图片写回失败（{len(generated_items)}/{payload.n} 已写回画布）：{failure_text}')
        finally:
            PROJECT_MEDIA_ADAPTER_PROJECT.reset(scope_token)
        if not generated_items:
            raise ProjectStoreError('在线模型没有返回图片')
        timestamp = _app_main.now_ms()
        history_record = {'id': progress_history_id, 'task_id': task_id, 'idempotency_key': payload.idempotency_key or task_id, 'type': 'online-image', 'status': 'succeeded', 'prompt': payload.prompt, 'executed_prompt': executed_prompt, 'angle_preset': payload.angle_preset, 'created_at': started_at, 'createdAt': started_at, 'completed_at': timestamp, 'runMs': max(0, timestamp - started_at), 'routing_mode': payload.routing_mode, 'requested_provider_id': payload.provider_id, 'requested_model': payload.model, 'actual_provider_id': route.provider_id, 'actual_provider_name': route.provider_name, 'actual_model': route.model, 'model': route.model, 'route': route.to_dict(), 'route_reason': route.reason, 'params': request_snapshot.to_dict(), 'request_payload': payload.model_dump(), 'outputs': [{'asset_id': item['asset_id'], 'kind': 'image'} for item in generated_items], 'result_node_id': payload.result_node_id}
        raise_if_project_image_task_cancelled(task_id)
        canvas = None
        last_complete_error = None
        for complete_attempt in range(3):
            try:
                canvas = await asyncio.to_thread(_app_main.project_store_call, 'complete_image_generation', project_id, payload.result_node_id, generated_items, history_record, task_id=task_id, require_pending=True)
                break
            except Exception as exc:
                last_complete_error = exc
                await asyncio.sleep(min(2.0, 0.4 * (complete_attempt + 1)))
        if canvas is None:
            raise last_complete_error
        base = f"/api/project-library/{urllib.parse.quote(project_id, safe='')}/media/"
        image_items = [{**item, 'url': base + urllib.parse.quote(item['asset_id'], safe='')} for item in generated_items]
        result = {'prompt': payload.prompt, 'executed_prompt': executed_prompt, 'images': [item['url'] for item in image_items], 'image_items': image_items, 'provider_id': route.provider_id, 'provider_name': route.provider_name, 'model': route.model, 'route': route.to_dict(), 'history_id': history_record['id'], 'history_recorded': True, 'history_record': history_record, 'project_id': project_id, 'project_revision': canvas['revision'], 'registered_revision': registered_revision, 'result_node_id': payload.result_node_id, 'final_node': next((item for item in canvas.get('nodes') or [] if item.get('id') == payload.result_node_id), None)}
        with CANVAS_TASK_LOCK:
            CANVAS_TASKS[task_id].update({'status': 'succeeded', 'result': result, 'error': '', 'updated_at': time.time()})
        update_project_image_task_progress(task_id, 'completed')
        # The Store commit above decides the business terminal state.  Snapshot,
        # cleanup, and websocket failures must never rewrite that success as a
        # failed task; restart reconciliation uses the terminal history marker.
        try:
            await asyncio.to_thread(_app_main.project_store_call, 'flush', project_id)
        except Exception:
            pass
        try:
            await cleanup_project_generation_orphans(project_id)
            await manager.broadcast_canvas_updated(project_id, int(canvas.get('updated_at') or _app_main.now_ms()))
        except Exception:
            pass
        return
    except asyncio.CancelledError:
        if not project_image_task_cancel_requested(task_id):
            raise
        with CANVAS_TASK_LOCK:
            orphan_ids = PROJECT_IMAGE_GENERATION_ORPHANS.setdefault(project_id, set())
            orphan_ids.update((item.get('asset_id') for item in generated_items if item.get('asset_id') and item.get('asset_id') not in preexisting_asset_ids))
        await cleanup_project_generation_orphans(project_id)
    except Exception as exc:
        import traceback
        traceback.print_exc()
        if project_image_task_cancel_requested(task_id):
            with CANVAS_TASK_LOCK:
                orphan_ids = PROJECT_IMAGE_GENERATION_ORPHANS.setdefault(project_id, set())
                orphan_ids.update((item.get('asset_id') for item in generated_items if item.get('asset_id') and item.get('asset_id') not in preexisting_asset_ids))
            await cleanup_project_generation_orphans(project_id)
            return
        detail = getattr(exc, 'detail', None) or str(exc)
        failure_time = _app_main.now_ms()
        fail_project_image_task_progress(task_id)
        with CANVAS_TASK_LOCK:
            failure_progress = copy.deepcopy((CANVAS_TASKS.get(task_id) or {}).get('progress') or {})
        failure_result = None
        resolved_status = 'timeout' if isinstance(exc, ImageGenerationTaskTimeout) else 'failed'
        # 取回兑底：已归档成功的图写进失败记录 outputs，客户端可凭 task_id 重新取回。
        failed_outputs = [{'asset_id': item['asset_id'], 'kind': 'image'} for item in generated_items]
        failure_record = {'id': f'generation-{uuid.uuid4().hex}', 'task_id': task_id, 'idempotency_key': payload.idempotency_key or task_id, 'type': 'online-image', 'status': resolved_status, 'prompt': payload.prompt, 'executed_prompt': apply_angle_preset(payload.prompt, payload.angle_preset), 'angle_preset': payload.angle_preset, 'created_at': started_at, 'createdAt': started_at, 'completed_at': failure_time, 'runMs': max(0, failure_time - started_at), 'routing_mode': payload.routing_mode, 'requested_provider_id': payload.provider_id, 'requested_model': payload.model, 'actual_provider_id': route.provider_id, 'actual_provider_name': route.provider_name, 'actual_model': route.model, 'model': route.model, 'route': route.to_dict(), 'route_reason': route.reason, 'params': request_snapshot.to_dict(), 'request_payload': payload.model_dump(), 'progress': failure_progress, 'outputs': failed_outputs, 'result_node_id': payload.result_node_id, 'error': str(detail)}
        try:
            failed_canvas = await asyncio.to_thread(
                _app_main.project_store_call,
                'fail_image_generation',
                project_id,
                payload.result_node_id,
                task_id,
                failure_record,
                require_pending=True,
            )
            failure_result = {'history_recorded': True, 'history_record': failure_record, 'project_id': project_id, 'project_revision': failed_canvas['revision'], 'registered_revision': registered_revision, 'result_node_id': payload.result_node_id, 'final_node': next((item for item in failed_canvas.get('nodes') or [] if item.get('id') == payload.result_node_id), None)}
            try:
                await asyncio.to_thread(_app_main.project_store_call, 'flush', project_id)
            except Exception:
                pass
            try:
                await manager.broadcast_canvas_updated(project_id, int(failed_canvas.get('updated_at') or failure_time))
            except Exception:
                pass
        except Exception:
            try:
                opened, durable = await asyncio.to_thread(
                    recover_project_image_task,
                    project_id,
                    task_id,
                    termination_reason='failure_commit_unknown',
                    termination_error=f'失败终态提交结果未知：{detail}',
                )
                if durable is not None and durable.get('status') != 'pending':
                    resolved_status = str(durable.get('status') or 'failed')
                    failure_record = copy.deepcopy(durable.get('history_record') or failure_record)
                    failure_result = durable_project_image_result(project_id, opened.get('canvas') or {}, durable)
                    detail = str(durable.get('error') or '')
            except Exception:
                pass
        with CANVAS_TASK_LOCK:
            if resolved_status != 'succeeded':
                orphan_ids = PROJECT_IMAGE_GENERATION_ORPHANS.setdefault(project_id, set())
                orphan_ids.update((item.get('asset_id') for item in generated_items if item.get('asset_id') and item.get('asset_id') not in preexisting_asset_ids))
            CANVAS_TASKS[task_id].update({
                'status': resolved_status,
                'error': '' if resolved_status == 'succeeded' else str(detail),
                'status_code': 200 if resolved_status == 'succeeded' else getattr(exc, 'status_code', 500),
                'result': failure_result,
                'updated_at': time.time(),
            })
        if resolved_status == 'succeeded':
            update_project_image_task_progress(task_id, 'completed')
        elif resolved_status == 'cancelled':
            update_project_image_task_progress(task_id, 'cancelled')
        elif resolved_status == 'timeout':
            update_project_image_task_progress(task_id, 'timeout')
        else:
            fail_project_image_task_progress(task_id)
        await cleanup_project_generation_orphans(project_id)

def _build_video_history_record(task_id: str, payload: ProjectVideoGenerationRequest, request_snapshot: VideoGenerationRequest, route, references: Optional[List[Dict[str, Any]]], generated_items: List[Dict[str, Any]], started_at: int, completed_at: int, status: str, error: str = '') -> Dict[str, Any]:
    record = {'id': f'generation-{uuid.uuid4().hex}', 'task_id': task_id, 'type': 'online-video', 'status': status, 'prompt': payload.prompt, 'executed_prompt': payload.prompt, 'created_at': started_at, 'createdAt': started_at, 'completed_at': completed_at, 'runMs': max(0, completed_at - started_at), 'routing_mode': payload.routing_mode, 'requested_provider_id': payload.provider_id, 'requested_model': payload.model, 'actual_provider_id': route.provider_id, 'actual_provider_name': route.provider_name, 'actual_model': route.model, 'model': route.model, 'route_reason': route.reason, 'params': request_snapshot.to_dict(), 'references': [{'asset_id': item['asset_id'], 'kind': item['kind']} for item in (references or [])], 'outputs': [{'asset_id': item['asset_id'], 'kind': 'video'} for item in generated_items], 'result_node_id': payload.result_node_id}
    if error:
        record['error'] = str(error)
    return record

async def run_project_video_task(task_id: str, project_id: str, payload: ProjectVideoGenerationRequest, request_snapshot: VideoGenerationRequest, route, registered_revision: int, references: Optional[List[Dict[str, Any]]]=None, provider: Optional[Mapping[str, Any]]=None):
    started_at = _app_main.now_ms()
    generated_items = []
    references_seen: List[Dict[str, Any]] = []
    preexisting_asset_ids = set()
    with CANVAS_TASK_LOCK:
        current = CANVAS_TASKS.get(task_id)
        if isinstance(current, dict) and (current.get('cancel_requested') or current.get('status') == 'cancelled'):
            return  # 取消路由已写入 cancelled 终态；worker 不再调用 provider（避免重复计费）
        CANVAS_TASKS[task_id]['status'] = 'running'
        CANVAS_TASKS[task_id]['updated_at'] = time.time()

    async def on_provider_task(info: Dict[str, Any]):
        # #82：提交成功即持久化服务商任务标识（内存 + canvas pendingTasks），重启后可按 task_id 重轮询。
        with CANVAS_TASK_LOCK:
            task = CANVAS_TASKS.get(task_id)
            if isinstance(task, dict):
                task['provider_task'] = copy.deepcopy(info)
        try:
            await asyncio.to_thread(_app_main.project_store_call, 'attach_media_generation_provider_task', project_id, task_id, copy.deepcopy(info))
        except Exception:
            pass  # 元数据持久化失败不阻断生成主流程

    try:
        preexisting_asset_ids = {item.get('asset_id') for item in await asyncio.to_thread(_app_main.project_store_call, 'list_media', project_id) if item.get('asset_id')}
        references = copy.deepcopy(references) if references is not None else await asyncio.to_thread(project_video_all_references, project_id, payload)
        references_seen = references
        # #80：任务级硬超时——provider 挂起时必须到 timeout 终态，不能让节点无限「生成中」。
        timeout_seconds = float(_app_main.video_generation_task_timeout_seconds())
        try:
            candidates, raw = await asyncio.wait_for(
                _app_main.execute_project_video_generation(project_id, request_snapshot, route, references, provider, on_provider_task=on_provider_task),
                timeout=timeout_seconds,
            )
        except asyncio.TimeoutError:
            raise VideoGenerationTaskTimeout(f'视频生成任务超时：服务商 {int(timeout_seconds)} 秒内未返回结果') from None
        for candidate in candidates:
            generated_items.append(await archive_project_generated_video(project_id, candidate, len(generated_items)))
        if not generated_items:
            raise ProjectStoreError('在线模型没有返回视频')
        completed_at = _app_main.now_ms()
        history_record = _build_video_history_record(task_id, payload, request_snapshot, route, references_seen, generated_items, started_at, completed_at, 'succeeded')
        canvas = await asyncio.to_thread(_app_main.project_store_call, 'complete_media_generation', project_id, payload.result_node_id, generated_items, history_record, media_kind='video', task_id=task_id, require_pending=True)
        await asyncio.to_thread(_app_main.project_store_call, 'flush', project_id)
        base = f"/api/project-library/{urllib.parse.quote(project_id, safe='')}/media/"
        video_items = [{**item, 'url': base + urllib.parse.quote(item['asset_id'], safe='')} for item in generated_items]
        result = {'prompt': payload.prompt, 'videos': [item['url'] for item in video_items], 'video_items': video_items, 'media_items': video_items, 'provider_id': route.provider_id, 'provider_name': route.provider_name, 'model': route.model, 'route': route.to_dict(), 'history_id': history_record['id'], 'history_recorded': True, 'history_record': history_record, 'project_id': project_id, 'project_revision': canvas['revision'], 'registered_revision': registered_revision, 'result_node_id': payload.result_node_id, 'final_node': next((item for item in canvas.get('nodes') or [] if item.get('id') == payload.result_node_id), None), 'raw': raw if isinstance(raw, dict) else {}}
        with CANVAS_TASK_LOCK:
            CANVAS_TASKS[task_id].update({'status': 'succeeded', 'result': result, 'error': '', 'updated_at': time.time()})
        await cleanup_project_generation_orphans(project_id)
        await manager.broadcast_canvas_updated(project_id, int(canvas.get('updated_at') or completed_at))
    except Exception as exc:
        # #80/#83：timeout 与 failed 分开落终态；已下载的视频写入终态 outputs（#83 语义），
        # 不再以孤儿身份被清理（#79 改进后孤儿也进回收区，双保险）。
        terminal_status = 'timeout' if isinstance(exc, VideoGenerationTaskTimeout) else 'failed'
        detail = getattr(exc, 'detail', None) or str(exc)
        failure_time = _app_main.now_ms()
        failure_record = _build_video_history_record(task_id, payload, request_snapshot, route, references_seen, generated_items, started_at, failure_time, terminal_status, error=detail)
        failure_result = None
        try:
            failed_canvas = await asyncio.to_thread(_app_main.project_store_call, 'fail_media_generation', project_id, payload.result_node_id, task_id, failure_record)
            await asyncio.to_thread(_app_main.project_store_call, 'flush', project_id)
            await manager.broadcast_canvas_updated(project_id, int(failed_canvas.get('updated_at') or failure_time))
            failure_result = {'history_recorded': True, 'history_record': failure_record, 'project_id': project_id, 'project_revision': failed_canvas['revision'], 'registered_revision': registered_revision, 'result_node_id': payload.result_node_id, 'final_node': next((item for item in failed_canvas.get('nodes') or [] if item.get('id') == payload.result_node_id), None)}
        except Exception:
            pass
        with CANVAS_TASK_LOCK:
            orphan_ids = PROJECT_VIDEO_GENERATION_ORPHANS.setdefault(project_id, set())
            orphan_ids.update((item.get('asset_id') for item in generated_items if item.get('asset_id') and item.get('asset_id') not in preexisting_asset_ids))
            CANVAS_TASKS[task_id].update({'status': terminal_status, 'error': str(detail), 'status_code': getattr(exc, 'status_code', 500), 'result': failure_result, 'updated_at': time.time()})
        await cleanup_project_generation_orphans(project_id)

async def shutdown_project_generation_tasks() -> None:
    """Persist one explicit terminal for active project generation before shutdown."""
    with CANVAS_TASK_LOCK:
        active_tasks = []
        for task_id, task in CANVAS_TASKS.items():
            if task.get('type') not in {'project-online-image', 'project-online-video'}:
                continue
            if task.get('status') not in {'registering', 'queued', 'running'}:
                continue
            task['cancel_requested'] = True
            task['updated_at'] = time.time()
            active_tasks.append((str(task_id), copy.deepcopy(task)))

    for task_id, task in active_tasks:
        project_id = str(task.get('project_id') or '')
        result_node_id = str(task.get('result_node_id') or '')
        if not project_id or not result_node_id:
            continue
        request_payload = copy.deepcopy(task.get('request_payload') or {})
        route = copy.deepcopy(task.get('route') or {})
        completed_at = _app_main.now_ms()
        created_value = float(task.get('created_at') or 0)
        created_at = int(created_value * 1000) if 0 < created_value < 10_000_000_000 else int(created_value or completed_at)
        history_record = {
            'id': f'generation-{uuid.uuid4().hex}',
            'task_id': task_id,
            'idempotency_key': str(task.get('idempotency_key') or request_payload.get('idempotency_key') or ''),
            'type': 'online-image' if task.get('type') == 'project-online-image' else 'online-video',
            'status': 'cancelled',
            'prompt': str(request_payload.get('prompt') or ''),
            'executed_prompt': str(request_payload.get('prompt') or ''),
            'created_at': created_at,
            'createdAt': created_at,
            'completed_at': completed_at,
            'runMs': max(0, completed_at - created_at),
            'routing_mode': request_payload.get('routing_mode'),
            'requested_provider_id': request_payload.get('provider_id'),
            'requested_model': request_payload.get('model'),
            'actual_provider_id': route.get('provider_id') or task.get('provider_id'),
            'actual_provider_name': route.get('provider_name') or '',
            'actual_model': route.get('model') or task.get('model'),
            'model': route.get('model') or task.get('model'),
            'route_reason': route.get('reason') or '',
            'request_payload': request_payload,
            'params': request_payload,
            'outputs': [],
            'result_node_id': result_node_id,
            'error': '应用关闭，生成任务已明确终止',
            'termination_reason': 'service_shutdown',
        }
        try:
            canvas = await asyncio.to_thread(
                _app_main.project_store_call,
                'cancel_media_generation',
                project_id,
                result_node_id,
                task_id,
                history_record,
            )
            try:
                await asyncio.to_thread(_app_main.project_store_call, 'flush', project_id)
            except Exception:
                pass
            with CANVAS_TASK_LOCK:
                current = CANVAS_TASKS.get(task_id)
                if current is not None:
                    current.update({
                        'status': 'cancelled',
                        'error': history_record['error'],
                        'termination_reason': 'service_shutdown',
                        'result': {
                            'history_recorded': True,
                            'history_record': history_record,
                            'project_id': project_id,
                            'project_revision': canvas.get('revision'),
                            'result_node_id': result_node_id,
                            'final_node': next((item for item in canvas.get('nodes') or [] if item.get('id') == result_node_id), None),
                        },
                        'updated_at': time.time(),
                    })
        except InvalidMutation:
            # A worker may have won the terminal CAS immediately before shutdown.
            continue
        except Exception:
            # Store stays open until workers stop; restart recovery will
            # explicitly terminate any durable pending record that remains.
            continue

async def cleanup_project_generation_orphans(project_id: str):

    def cleanup():
        with project_lifecycle_lock(project_id):
            with CANVAS_TASK_LOCK:
                if any((item.get('type') in {'project-online-image', 'project-online-video'} and item.get('project_id') == project_id and (item.get('status') in {'queued', 'running'}) for item in CANVAS_TASKS.values())):
                    return
                candidates = PROJECT_IMAGE_GENERATION_ORPHANS.pop(project_id, set()) | PROJECT_VIDEO_GENERATION_ORPHANS.pop(project_id, set())
            for asset_id in candidates:
                try:
                    # #79/D3: 系统回收默认进回收区（7 天可恢复），不再静默硬删。
                    # 记录层修复（#83）后，取消/失败的已归档图会被历史引用而留在资源库；
                    # 这里只处理真正的孤儿（记录层故障/重启竞态），移入回收区供用户找回。
                    _app_main.project_store_call('trash_unreferenced_generation_media', project_id, asset_id)
                except Exception:
                    continue
    await asyncio.to_thread(cleanup)

def reserve_project_image_task(project_id: str, task_id: str, payload: ProjectImageGenerationRequest, route) -> Optional[Dict[str, Any]]:
    idempotency_key = str(payload.idempotency_key or '').strip()
    with project_lifecycle_lock(project_id):
        with CANVAS_TASK_LOCK:
            expiry = time.time() - 24 * 60 * 60
            for expired_id, expired_task in list(CANVAS_TASKS.items()):
                if expired_task.get('type') == 'project-online-image' and expired_task.get('status') in {'succeeded', 'failed', 'cancelled'} and (float(expired_task.get('updated_at') or 0) < expiry):
                    CANVAS_TASKS.pop(expired_id, None)
                    PROJECT_IMAGE_GENERATION_FUTURES.pop(expired_id, None)
            if idempotency_key:
                existing = next(
                    (
                        task for task in CANVAS_TASKS.values()
                        if task.get('type') == 'project-online-image'
                        and str(task.get('project_id') or '') == str(project_id)
                        and str(task.get('idempotency_key') or '') == idempotency_key
                    ),
                    None,
                )
                if existing is not None:
                    ensure_project_image_idempotency_match(existing, payload)
                    return copy.deepcopy(existing)
            active_tasks = [task for task in CANVAS_TASKS.values() if task.get('type') == 'project-online-image' and task.get('status') in {'registering', 'queued', 'running'}]
            project_active = [task for task in active_tasks if task.get('project_id') == project_id]
            if len(active_tasks) >= PROJECT_IMAGE_GENERATION_MAX_PENDING:
                raise HTTPException(status_code=429, detail='在线生图队列已满，请等待已有任务完成')
            if len(project_active) >= PROJECT_IMAGE_GENERATION_MAX_PER_PROJECT:
                raise HTTPException(status_code=429, detail='当前项目同时最多运行 8 个在线生图任务')
            CANVAS_TASKS[task_id] = {'id': task_id, 'type': 'project-online-image', 'project_id': project_id, 'result_node_id': payload.result_node_id, 'status': 'registering', 'created_at': time.time(), 'updated_at': time.time(), 'result': None, 'error': '', 'provider_id': route.provider_id, 'model': route.model, 'route': route.to_dict(), 'request_payload': payload.model_dump(), 'idempotency_key': idempotency_key}
    # 初始就把每张候选的「排队」阶段写入进度，前端首屏即可让每个占位格显示
    # 各自的阶段标签（此前只有编号，用户看不出逐格进度）。
    update_project_image_task_progress(task_id, 'queued', candidate_phases=['queued'] * int(payload.n))
    return None


async def wait_for_project_image_registration(project_id: str, task_id: str, *, timeout: float = 15) -> Dict[str, Any]:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        with CANVAS_TASK_LOCK:
            current = copy.deepcopy(CANVAS_TASKS.get(str(task_id)) or {})
        if not current:
            raise HTTPException(status_code=409, detail='幂等任务注册失败，请重新发起操作')
        if str(current.get('project_id') or '') != str(project_id):
            raise HTTPException(status_code=404, detail='项目生图任务不存在')
        if current.get('status') != 'registering':
            return current
        await asyncio.sleep(0.01)
    raise HTTPException(status_code=409, detail='幂等任务注册超时，请稍后查询')


@router.get('/api/project-library/{project_id}/image-generation-tasks/{task_id}')
async def get_project_image_task(project_id: str, task_id: str):
    with CANVAS_TASK_LOCK:
        current = copy.deepcopy(CANVAS_TASKS.get(str(task_id)) or {})
    if current:
        if current.get('type') != 'project-online-image' or str(current.get('project_id') or '') != str(project_id):
            raise HTTPException(status_code=404, detail='项目生图任务不存在')
        current['task_id'] = str(task_id)
        return current

    try:
        opened, durable = await asyncio.to_thread(recover_project_image_task, project_id, str(task_id))
    except ProjectNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    if durable is None:
        raise HTTPException(status_code=404, detail='项目生图任务不存在')
    canvas = opened.get('canvas') or {}
    result = durable_project_image_result(project_id, canvas, durable)
    with CANVAS_TASK_LOCK:
        CANVAS_TASKS[str(task_id)] = {
            **copy.deepcopy(durable),
            'project_id': project_id,
            'result': result,
            'updated_at': time.time(),
        }
    await manager.broadcast_canvas_updated(project_id, int(canvas.get('updated_at') or _app_main.now_ms()))
    return {
        **durable,
        'project_id': project_id,
        'task_id': str(task_id),
        'result': result,
    }


@router.post('/api/project-library/{project_id}/image-generation-tasks/{task_id}/cancel')
async def cancel_project_image_task(project_id: str, task_id: str, request: Request):
    ensure_same_origin_request(request)
    with CANVAS_TASK_LOCK:
        current = CANVAS_TASKS.get(str(task_id))
        if (
            not isinstance(current, dict)
            or current.get('type') != 'project-online-image'
            or str(current.get('project_id') or '') != str(project_id)
        ):
            raise HTTPException(status_code=404, detail='项目生图任务不存在')
        if current.get('status') == 'cancelled':
            return {'task_id': task_id, 'status': 'cancelled'}
        if current.get('status') not in {'queued', 'running'}:
            raise HTTPException(status_code=409, detail='任务已结束，无法取消')
        current['cancel_requested'] = True
        task = copy.deepcopy(current)

    payload = ProjectImageGenerationRequest(**task.get('request_payload', {}))
    route = dict(task.get('route') or {})
    completed_at = _app_main.now_ms()
    created_at = round(float(task.get('created_at') or time.time()) * 1000)
    history_record = {
        'id': f'generation-{uuid.uuid4().hex}',
        'task_id': task_id,
        'idempotency_key': task.get('idempotency_key') or task_id,
        'type': 'online-image',
        'status': 'cancelled',
        'prompt': payload.prompt,
        'executed_prompt': apply_angle_preset(payload.prompt, payload.angle_preset),
        'angle_preset': payload.angle_preset,
        'created_at': created_at,
        'createdAt': created_at,
        'completed_at': completed_at,
        'runMs': max(0, completed_at - created_at),
        'routing_mode': payload.routing_mode,
        'requested_provider_id': payload.provider_id,
        'requested_model': payload.model,
        'actual_provider_id': route.get('provider_id') or task.get('provider_id') or '',
        'actual_provider_name': route.get('provider_name') or '',
        'actual_model': route.get('model') or task.get('model') or '',
        'model': route.get('model') or task.get('model') or '',
        'route': route,
        'route_reason': route.get('reason') or '',
        'params': project_image_request(payload).to_dict(),
        'request_payload': payload.model_dump(),
        'outputs': [],
        'result_node_id': payload.result_node_id,
        'error': '任务已取消',
    }
    try:
        canvas = await asyncio.to_thread(
            _app_main.project_store_call,
            'cancel_image_generation',
            project_id,
            payload.result_node_id,
            task_id,
            history_record,
        )
        await asyncio.to_thread(_app_main.project_store_call, 'flush', project_id)
    except Exception as exc:
        opened_after_failure = None
        durable_after_failure = None
        try:
            opened_after_failure, durable_after_failure = await asyncio.to_thread(
                recover_project_image_task,
                project_id,
                task_id,
                termination_reason='cancel_persistence_failed',
                termination_error=f'取消结果持久化失败，任务已明确终止：{exc}',
            )
        except Exception:
            durable_after_failure = None
        recovered_result = None
        if durable_after_failure is not None:
            recovered_result = durable_project_image_result(
                project_id,
                (opened_after_failure or {}).get('canvas') or {},
                durable_after_failure,
            )
        with CANVAS_TASK_LOCK:
            current = CANVAS_TASKS.get(str(task_id))
            if isinstance(current, dict):
                if durable_after_failure is not None:
                    current.update({
                        'status': durable_after_failure.get('status') or 'failed',
                        'error': durable_after_failure.get('error') or str(exc),
                        'result': recovered_result,
                        'updated_at': time.time(),
                    })
                else:
                    current.update({
                        'status': 'failed',
                        'error': f'取消结果持久化失败，任务已停止：{exc}',
                        'updated_at': time.time(),
                    })
                current.pop('cancel_requested', None)
            future = PROJECT_IMAGE_GENERATION_FUTURES.get(str(task_id))
        if future is not None:
            future.cancel()
        if durable_after_failure is not None and durable_after_failure.get('status') == 'cancelled':
            canvas = (opened_after_failure or {}).get('canvas') or {}
            history_record = durable_after_failure
        else:
            status_code = 409 if isinstance(exc, (ProjectStoreError, InvalidMutation)) else 500
            raise HTTPException(status_code=status_code, detail=str(exc)) from exc

    with CANVAS_TASK_LOCK:
        current = CANVAS_TASKS.get(str(task_id))
        if isinstance(current, dict):
            current.update({
                'status': 'cancelled',
                'error': '任务已取消',
                'result': {
                    'history_recorded': True,
                    'history_record': history_record,
                    'project_id': project_id,
                    'project_revision': canvas['revision'],
                    'result_node_id': payload.result_node_id,
                },
                'updated_at': time.time(),
            })
            current.pop('cancel_requested', None)
        future = PROJECT_IMAGE_GENERATION_FUTURES.get(str(task_id))
    update_project_image_task_progress(task_id, 'cancelled')
    if future is not None:
        future.cancel()
    try:
        await cleanup_project_generation_orphans(project_id)
    except Exception:
        pass
    try:
        await manager.broadcast_canvas_updated(project_id, int(canvas.get('updated_at') or completed_at))
    except Exception:
        pass
    return {
        'task_id': task_id,
        'status': 'cancelled',
        'project_revision': canvas['revision'],
        'result_node_id': payload.result_node_id,
        'result': current.get('result') if isinstance(current, dict) else None,
    }


@router.post('/api/project-library/{project_id}/image-generation-tasks/{task_id}/retry')
async def retry_project_image_task(project_id: str, task_id: str, request: Request):
    ensure_same_origin_request(request)
    with CANVAS_TASK_LOCK:
        current = copy.deepcopy(CANVAS_TASKS.get(str(task_id)) or {})
    if current and (
        current.get('type') != 'project-online-image'
        or str(current.get('project_id') or '') != str(project_id)
    ):
        raise HTTPException(status_code=404, detail='项目生图任务不存在')
    if not current:
        try:
            opened, current = await asyncio.to_thread(recover_project_image_task, project_id, str(task_id))
        except ProjectNotFound as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        if current is None:
            raise HTTPException(status_code=404, detail='项目生图任务不存在')
    else:
        opened = await asyncio.to_thread(_app_main.project_store_call, 'open_project', project_id)
    if current.get('status') != 'failed':
        raise HTTPException(status_code=409, detail='只有失败的任务可以重试')
    request_payload = copy.deepcopy(current.get('request_payload') or {})

    request_payload['base_revision'] = int(opened.get('canvas', {}).get('revision') or 0)
    request_payload['idempotency_key'] = f'retry:{task_id}'
    retry_payload = ProjectImageGenerationRequest(**request_payload)
    response = await create_project_image_task(project_id, retry_payload, request)
    content = json.loads(bytes(response.body).decode('utf-8'))
    retry_task_id = str(content.get('task_id') or '')
    with CANVAS_TASK_LOCK:
        previous = CANVAS_TASKS.get(str(task_id))
        if isinstance(previous, dict):
            previous['retry_task_id'] = retry_task_id
        retry_task = CANVAS_TASKS.get(retry_task_id)
        if isinstance(retry_task, dict):
            retry_task['retry_of_task_id'] = str(task_id)
    content['retry_of_task_id'] = str(task_id)
    return JSONResponse(status_code=202, content=content)


@router.post('/api/project-library/{project_id}/image-generation-tasks')
async def create_project_image_task(project_id: str, payload: ProjectImageGenerationRequest, request: Request):
    ensure_same_origin_request(request)
    try:
        opened = await asyncio.to_thread(_app_main.project_store_call, 'open_project', project_id)
    except ProjectNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    idempotency_key = str(payload.idempotency_key or '').strip()
    if idempotency_key:
        with CANVAS_TASK_LOCK:
            existing = next(
                (
                    copy.deepcopy(task) for task in CANVAS_TASKS.values()
                    if task.get('type') == 'project-online-image'
                    and str(task.get('project_id') or '') == str(project_id)
                    and str(task.get('idempotency_key') or '') == idempotency_key
                ),
                None,
            )
        canvas = opened.get('canvas') or {}
        if existing is None:
            existing = durable_project_image_task_by_idempotency(canvas, idempotency_key)
        if existing is not None:
            if existing.get('status') == 'registering':
                existing = await wait_for_project_image_registration(project_id, str(existing.get('id') or ''))
                opened = await asyncio.to_thread(_app_main.project_store_call, 'open_project', project_id)
                canvas = opened.get('canvas') or {}
            ensure_project_image_idempotency_match(existing, payload)
            return JSONResponse(status_code=202, content={
                'task_id': existing['id'],
                'idempotency_key': idempotency_key,
                'status': existing.get('status') or 'queued',
                'route': existing.get('route') or {},
                'project_revision': int(canvas.get('revision') or 0),
                'registered_node': next((item for item in canvas.get('nodes') or [] if item.get('id') == existing.get('result_node_id')), None),
            })
    try:
        request_snapshot = project_image_request(payload)
        for asset_id in payload.reference_asset_ids:
            await asyncio.to_thread(_app_main.project_store_call, 'media_record', project_id, asset_id)
        route = _app_main.route_image_generation(request_snapshot, _app_main.load_api_providers(), provider_id=payload.provider_id if payload.routing_mode == 'manual' else '', model=payload.model if payload.routing_mode == 'manual' else '')
        apply_angle_preset(payload.prompt, payload.angle_preset)
    except ModelRoutingError as exc:
        raise HTTPException(status_code=400, detail=exc.to_dict()) from exc
    except ProjectNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except (ProjectStoreError, ValueError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    task_id = f'project_img_{uuid.uuid4().hex}'
    raced_existing = reserve_project_image_task(project_id, task_id, payload, route)
    if raced_existing is not None:
        if raced_existing.get('status') == 'registering':
            raced_existing = await wait_for_project_image_registration(project_id, str(raced_existing.get('id') or ''))
        opened = await asyncio.to_thread(_app_main.project_store_call, 'open_project', project_id)
        canvas = opened.get('canvas') or {}
        return JSONResponse(status_code=202, content={
            'task_id': raced_existing['id'],
            'idempotency_key': idempotency_key,
            'status': raced_existing.get('status') or 'queued',
            'route': raced_existing.get('route') or {},
            'project_revision': int(canvas.get('revision') or 0),
            'registered_node': next((item for item in canvas.get('nodes') or [] if item.get('id') == raced_existing.get('result_node_id')), None),
        })
    stable_idempotency_key = idempotency_key or task_id
    durable_request_payload = payload.model_dump()
    durable_request_payload['idempotency_key'] = stable_idempotency_key
    with CANVAS_TASK_LOCK:
        CANVAS_TASKS[task_id]['idempotency_key'] = stable_idempotency_key
        CANVAS_TASKS[task_id]['request_payload'] = copy.deepcopy(durable_request_payload)
    payload = ProjectImageGenerationRequest(**durable_request_payload)

    async def compensate_registration_failure(
        detail: str,
        *,
        termination_reason: str = 'registration_failed',
        termination_error: str = '任务注册未完成，未调用在线模型',
    ) -> None:
        durable = None
        opened_after_failure = None
        try:
            opened_after_failure, durable = await asyncio.to_thread(
                recover_project_image_task,
                project_id,
                task_id,
                termination_reason=termination_reason,
                termination_error=f'{termination_error}：{detail}',
            )
        except Exception:
            try:
                opened_after_failure = await asyncio.to_thread(_app_main.project_store_call, 'open_project', project_id)
                durable = durable_project_image_task(opened_after_failure.get('canvas') or {}, task_id)
            except Exception:
                durable = None
        with CANVAS_TASK_LOCK:
            if durable is None:
                CANVAS_TASKS.pop(task_id, None)
            else:
                current = CANVAS_TASKS.get(task_id)
                if isinstance(current, dict):
                    current.update({
                        'status': durable.get('status') if durable.get('status') != 'pending' else 'failed',
                        'error': durable.get('error') or str(detail),
                        'result': durable_project_image_result(project_id, (opened_after_failure or {}).get('canvas') or {}, durable),
                        'updated_at': time.time(),
                    })

    try:
        task_record = {'taskId': task_id, 'kind': 'image', 'count': payload.n, 'providerId': route.provider_id, 'model': route.model, 'prompt': payload.prompt, 'size': payload.size, 'referenceAssetIds': list(payload.reference_asset_ids), 'idempotencyKey': stable_idempotency_key, 'requestPayload': copy.deepcopy(durable_request_payload), 'route': route.to_dict(), 'createdAt': time.time()}
        try:
            registered_canvas = await asyncio.to_thread(_app_main.project_store_call, 'begin_image_generation', project_id, payload.result_node_id, task_record, base_revision=payload.base_revision)
        except RevisionConflict as conflict:
            latest_canvas = conflict.canvas or {}
            latest_revision = int(latest_canvas.get('revision') or 0)
            if not latest_revision:
                raise
            registered_canvas = await asyncio.to_thread(_app_main.project_store_call, 'begin_image_generation', project_id, payload.result_node_id, task_record, base_revision=latest_revision)
        await asyncio.to_thread(_app_main.project_store_call, 'flush', project_id)
        logger.info(f'[gen-task {task_id[:8]}] register payload.size={payload.size!r} record_size={task_record.get("size")!r} rp_size={task_record.get("requestPayload", {}).get("size")!r}')
    except RevisionConflict as exc:
        await compensate_registration_failure(str(exc))
        await cleanup_project_generation_orphans(project_id)
        raise HTTPException(status_code=409, detail={'message': str(exc), 'canvas': exc.canvas}) from exc
    except (ProjectStoreError, ValueError) as exc:
        await compensate_registration_failure(str(exc))
        await cleanup_project_generation_orphans(project_id)
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except Exception as exc:
        await compensate_registration_failure(str(exc))
        await cleanup_project_generation_orphans(project_id)
        raise
    with CANVAS_TASK_LOCK:
        current = CANVAS_TASKS.get(task_id)
        if isinstance(current, dict):
            current['status'] = 'queued'
            current['updated_at'] = time.time()
    future = None
    try:
        current_loop = asyncio.get_running_loop()
        if _app_main.GLOBAL_LOOP is current_loop:
            future = current_loop.create_task(run_project_image_task(task_id, project_id, payload, request_snapshot, route, registered_canvas['revision']))
        else:
            executor = ensure_project_image_generation_executor()
            future = executor.submit(lambda: asyncio.run(run_project_image_task(task_id, project_id, payload, request_snapshot, route, registered_canvas['revision'])))
        with CANVAS_TASK_LOCK:
            PROJECT_IMAGE_GENERATION_FUTURES[task_id] = future

        def forget_project_image_future(done_future: Future):
            with CANVAS_TASK_LOCK:
                if PROJECT_IMAGE_GENERATION_FUTURES.get(task_id) is done_future:
                    PROJECT_IMAGE_GENERATION_FUTURES.pop(task_id, None)
        future.add_done_callback(forget_project_image_future)
    except Exception as exc:
        if future is not None:
            future.cancel()
        with CANVAS_TASK_LOCK:
            PROJECT_IMAGE_GENERATION_FUTURES.pop(task_id, None)
        await compensate_registration_failure(
            str(exc),
            termination_reason='scheduling_failed',
            termination_error='任务已注册但无法调度，未调用在线模型',
        )
        try:
            await cleanup_project_generation_orphans(project_id)
        except Exception:
            pass
        raise HTTPException(
            status_code=503,
            detail={
                'code': 'image_generation_scheduling_failed',
                'message': '任务已经明确终止，未调用在线模型；请稍后显式重试',
                'task_id': task_id,
            },
        ) from exc
    try:
        await manager.broadcast_canvas_updated(project_id, int(registered_canvas.get('updated_at') or _app_main.now_ms()))
    except Exception:
        pass
    return JSONResponse(status_code=202, content={'task_id': task_id, 'idempotency_key': stable_idempotency_key, 'status': 'queued', 'route': route.to_dict(), 'project_revision': registered_canvas['revision'], 'registered_node': next((item for item in registered_canvas.get('nodes') or [] if item.get('id') == payload.result_node_id), None)})

def reserve_project_video_task(project_id: str, task_id: str, payload: ProjectVideoGenerationRequest, route):
    with project_lifecycle_lock(project_id):
        with CANVAS_TASK_LOCK:
            expiry = time.time() - 24 * 60 * 60
            for expired_id, expired_task in list(CANVAS_TASKS.items()):
                if expired_task.get('type') == 'project-online-video' and expired_task.get('status') in {'succeeded', 'failed'} and (float(expired_task.get('updated_at') or 0) < expiry):
                    CANVAS_TASKS.pop(expired_id, None)
            active_tasks = [task for task in CANVAS_TASKS.values() if task.get('type') == 'project-online-video' and task.get('status') in {'queued', 'running'}]
            project_active = [task for task in active_tasks if task.get('project_id') == project_id]
            if len(active_tasks) >= PROJECT_IMAGE_GENERATION_MAX_PENDING:
                raise HTTPException(status_code=429, detail='在线视频队列已满，请等待已有任务完成')
            if len(project_active) >= PROJECT_IMAGE_GENERATION_MAX_PER_PROJECT:
                raise HTTPException(status_code=429, detail='当前项目同时最多运行 8 个在线视频任务')
            CANVAS_TASKS[task_id] = {'id': task_id, 'type': 'project-online-video', 'kind': 'video', 'project_id': project_id, 'result_node_id': payload.result_node_id, 'status': 'queued', 'created_at': time.time(), 'updated_at': time.time(), 'result': None, 'error': '', 'provider_id': route.provider_id, 'model': route.model, 'route': route.to_dict(), 'request_payload': payload.model_dump()}

@router.post('/api/project-library/{project_id}/video-generation-tasks')
async def create_project_video_task(project_id: str, payload: ProjectVideoGenerationRequest, request: Request):
    ensure_same_origin_request(request)
    try:
        await asyncio.to_thread(_app_main.project_store_call, 'open_project', project_id)
        request_snapshot = project_video_request(payload)
        references = await asyncio.to_thread(project_video_all_references, project_id, payload)
        providers = await _app_main.preflight_project_video_providers(request_snapshot, references, _app_main.load_api_providers(), provider_id=payload.provider_id if payload.routing_mode == 'manual' else '', model=payload.model if payload.routing_mode == 'manual' else '')
        route = _app_main.route_video_generation(request_snapshot, providers, provider_id=payload.provider_id if payload.routing_mode == 'manual' else '', model=payload.model if payload.routing_mode == 'manual' else '', adapter_incompatible_parameters=functools.partial(project_video_adapter_incompatible_parameters, references=references))
        provider_snapshot = copy.deepcopy(next((provider for provider in providers if str(provider.get('id') or '').strip().lower() == route.provider_id)))
        if is_runninghub_provider(provider_snapshot):
            runninghub_api_key(provider_snapshot, use_wallet=True)
    except ModelRoutingError as exc:
        raise HTTPException(status_code=400, detail=exc.to_dict()) from exc
    except ProjectNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except (ProjectStoreError, ValueError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    task_id = f'project_vid_{uuid.uuid4().hex}'
    reserve_project_video_task(project_id, task_id, payload, route)
    try:
        registered_canvas = await asyncio.to_thread(_app_main.project_store_call, 'begin_media_generation', project_id, payload.result_node_id, {'taskId': task_id, 'kind': 'video', 'count': payload.n, 'providerId': route.provider_id, 'model': route.model, 'referenceAssetIds': list(dict.fromkeys([*payload.image_reference_asset_ids, *payload.video_reference_asset_ids, *payload.audio_reference_asset_ids])), 'requestPayload': payload.model_dump()}, base_revision=payload.base_revision, media_kind='video')
        await asyncio.to_thread(_app_main.project_store_call, 'flush', project_id)
    except RevisionConflict as exc:
        with CANVAS_TASK_LOCK:
            CANVAS_TASKS.pop(task_id, None)
        await cleanup_project_generation_orphans(project_id)
        raise HTTPException(status_code=409, detail={'message': str(exc), 'canvas': exc.canvas}) from exc
    except (ProjectStoreError, ValueError) as exc:
        with CANVAS_TASK_LOCK:
            CANVAS_TASKS.pop(task_id, None)
        await cleanup_project_generation_orphans(project_id)
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except Exception:
        with CANVAS_TASK_LOCK:
            CANVAS_TASKS.pop(task_id, None)
        await cleanup_project_generation_orphans(project_id)
        raise
    current_loop = asyncio.get_running_loop()
    if _app_main.GLOBAL_LOOP is current_loop:
        future = current_loop.create_task(run_project_video_task(task_id, project_id, payload, request_snapshot, route, registered_canvas['revision'], copy.deepcopy(references), provider_snapshot))
    else:
        executor = ensure_project_image_generation_executor()
        future = executor.submit(asyncio.run, run_project_video_task(task_id, project_id, payload, request_snapshot, route, registered_canvas['revision'], copy.deepcopy(references), provider_snapshot))
    with CANVAS_TASK_LOCK:
        PROJECT_VIDEO_GENERATION_FUTURES[task_id] = future

    def forget_project_video_future(done_future: Future):
        with CANVAS_TASK_LOCK:
            if PROJECT_VIDEO_GENERATION_FUTURES.get(task_id) is done_future:
                PROJECT_VIDEO_GENERATION_FUTURES.pop(task_id, None)
    future.add_done_callback(forget_project_video_future)
    return JSONResponse(status_code=202, content={'task_id': task_id, 'status': 'queued', 'kind': 'video', 'route': route.to_dict(), 'project_revision': registered_canvas['revision'], 'registered_node': next((item for item in registered_canvas.get('nodes') or [] if item.get('id') == payload.result_node_id), None)})

class _VideoRouteView:
    """重建 route 信息的最小视图（取消路由/重启重轮询共用；durable 记录里只存了 provider 三元组）。"""

    def __init__(self, data: Mapping[str, Any]):
        self.provider_id = str(data.get('provider_id') or '')
        self.provider_name = str(data.get('provider_name') or '')
        self.model = str(data.get('model') or '')
        self.reason = str(data.get('reason') or '')

    def to_dict(self) -> Dict[str, Any]:
        return {'provider_id': self.provider_id, 'provider_name': self.provider_name, 'model': self.model, 'reason': self.reason}


def durable_project_video_task(canvas: Mapping[str, Any], task_id: str) -> Optional[Dict[str, Any]]:
    """在 canvas 中查找视频任务：先终态历史（durable commit 标记），再 pending 节点。

    与图片同一约定：crash 在两次快照写之间时，不能把已付费成功合成成重启失败。
    """
    clean_task_id = str(task_id or '').strip()
    for record in reversed(canvas.get('history') or []):
        if not isinstance(record, dict) or str(record.get('task_id') or record.get('taskId') or '') != clean_task_id:
            continue
        if str(record.get('type') or '') not in {'online-video', 'video'}:
            continue
        return {
            'id': clean_task_id,
            'task_id': clean_task_id,
            'type': 'project-online-video',
            'project_id': canvas.get('project_id') or canvas.get('project') or '',
            'result_node_id': record.get('result_node_id') or '',
            'status': record.get('status') or 'failed',
            'provider_id': record.get('actual_provider_id') or '',
            'model': record.get('actual_model') or record.get('model') or '',
            'error': record.get('error') or '',
            'history_record': copy.deepcopy(record),
        }
    for node in canvas.get('nodes') or []:
        if not isinstance(node, dict):
            continue
        for pending in node.get('pendingTasks') or []:
            if not isinstance(pending, dict) or str(pending.get('taskId') or '') != clean_task_id:
                continue
            if str(pending.get('kind') or 'video') != 'video':
                continue
            return {
                'id': clean_task_id,
                'task_id': clean_task_id,
                'type': 'project-online-video',
                'project_id': canvas.get('project_id') or canvas.get('project') or '',
                'result_node_id': node.get('id') or '',
                'status': 'pending',
                'provider_id': pending.get('providerId') or '',
                'model': pending.get('model') or '',
                'request_payload': copy.deepcopy(pending.get('requestPayload') or {}),
                'provider_task': copy.deepcopy(pending.get('provider_task') or {}),
                'created_at': float(pending.get('createdAt') or pending.get('created_at') or time.time()),
            }
    return None


async def _resume_project_video_generation(task_id: str, project_id: str, durable: Mapping[str, Any]) -> None:
    """#82：重启后对已持久化 provider task id 的 pending 视频任务重新轮询（不再向服务商重复提交）。

    成功→归档+complete；服务商侧失败/不存在/轮询超时→fail 终态（错误文案带 provider task id 供人工查询）。
    """
    provider_task = durable.get('provider_task') or {}
    request_payload = copy.deepcopy(durable.get('request_payload') or {})
    provider_id = str(provider_task.get('provider_id') or durable.get('provider_id') or '')
    result_node_id = str(durable.get('result_node_id') or '')
    try:
        payload = ProjectVideoGenerationRequest(**request_payload) if request_payload else ProjectVideoGenerationRequest(prompt='（重启恢复的视频任务）', result_node_id=result_node_id, provider_id=provider_id, model=str(provider_task.get('model') or durable.get('model') or ''))
        request_snapshot = project_video_request(payload)
    except Exception as exc:
        payload = None
        request_snapshot = None
    if payload is None:
        try:
            await asyncio.to_thread(_app_main.project_store_call, 'fail_media_generation', project_id, result_node_id, task_id, {'id': f'generation-{uuid.uuid4().hex}', 'task_id': task_id, 'type': 'online-video', 'status': 'failed', 'prompt': str(request_payload.get('prompt') or ''), 'created_at': _app_main.now_ms(), 'completed_at': _app_main.now_ms(), 'outputs': [], 'result_node_id': result_node_id, 'error': f'重启恢复失败：无法重建原任务参数：{exc}'})
            await asyncio.to_thread(_app_main.project_store_call, 'flush', project_id)
        except Exception:
            pass
        with CANVAS_TASK_LOCK:
            current = CANVAS_TASKS.get(task_id)
            if isinstance(current, dict):
                current.update({'status': 'failed', 'error': '重启恢复失败：无法重建原任务参数', 'updated_at': time.time()})
        return
    with CANVAS_TASK_LOCK:
        current = CANVAS_TASKS.get(task_id)
        if isinstance(current, dict):
            current.update({'status': 'running', 'resuming': True, 'updated_at': time.time()})
    started_at = int(float(durable.get('created_at') or time.time()) * 1000)
    try:
        providers = await _app_main.load_api_providers()
        provider = next((p for p in providers if str(p.get('id') or '') == provider_id), None)
        if provider is None:
            raise ProjectStoreError(f'重启恢复失败：服务商 {provider_id} 不在当前 API 设置中，请在 API 设置恢复该服务商后手动重试')
        provider_task_id = str(provider_task.get('task_id') or '')
        if not provider_task_id:
            raise ProjectStoreError('重启恢复失败：没有记录到服务商任务 ID（专属适配器或提交前中断），无法重轮询，请手动重试')
        async with httpx.AsyncClient(timeout=_app_main.VIDEO_POLL_TIMEOUT) as client:
            raw = await _app_main.wait_for_video_task(client, provider, provider_task_id, str(provider_task.get('task_url') or ''))
        urls = [str(url or '').strip() for url in _app_main.video_output_urls(raw) if str(url or '').strip()]
        if not urls:
            raise ProjectStoreError('重启恢复失败：服务商任务已完成但没有返回视频')
        candidates = [{'type': 'url', 'value': url, 'mime_type': content_type_for_path(url)} for url in urls]
        generated_items = []
        for candidate in candidates:
            generated_items.append(await archive_project_generated_video(project_id, candidate, len(generated_items)))
        route = _VideoRouteView({'provider_id': provider_id, 'provider_name': str(provider.get('name') or provider_task.get('provider_name') or ''), 'model': str(provider_task.get('model') or payload.model or ''), 'reason': 'restart-resume'})
        completed_at = _app_main.now_ms()
        history_record = _build_video_history_record(task_id, payload, request_snapshot, route, [], generated_items, started_at, completed_at, 'succeeded')
        canvas = await asyncio.to_thread(_app_main.project_store_call, 'complete_media_generation', project_id, payload.result_node_id, generated_items, history_record, media_kind='video', task_id=task_id, require_pending=True)
        await asyncio.to_thread(_app_main.project_store_call, 'flush', project_id)
        base = f"/api/project-library/{urllib.parse.quote(project_id, safe='')}/media/"
        video_items = [{**item, 'url': base + urllib.parse.quote(item['asset_id'], safe='')} for item in generated_items]
        with CANVAS_TASK_LOCK:
            current = CANVAS_TASKS.get(task_id)
            if isinstance(current, dict):
                current.update({'status': 'succeeded', 'result': {'prompt': payload.prompt, 'videos': [item['url'] for item in video_items], 'video_items': video_items, 'history_recorded': True, 'history_record': history_record, 'project_id': project_id, 'project_revision': canvas['revision'], 'result_node_id': payload.result_node_id}, 'error': '', 'updated_at': time.time()})
        await manager.broadcast_canvas_updated(project_id, int(canvas.get('updated_at') or completed_at))
    except Exception as exc:
        detail = str(getattr(exc, 'detail', None) or exc)
        failure_time = _app_main.now_ms()
        route = _VideoRouteView({'provider_id': provider_id, 'provider_name': str(provider_task.get('provider_name') or ''), 'model': str(provider_task.get('model') or ''), 'reason': 'restart-resume'})
        failure_record = _build_video_history_record(task_id, payload, request_snapshot, route, [], [], started_at, failure_time, 'failed', error=f'重启恢复失败：{detail}')
        try:
            failed_canvas = await asyncio.to_thread(_app_main.project_store_call, 'fail_media_generation', project_id, result_node_id, task_id, failure_record)
            await asyncio.to_thread(_app_main.project_store_call, 'flush', project_id)
            await manager.broadcast_canvas_updated(project_id, int(failed_canvas.get('updated_at') or failure_time))
        except Exception:
            pass
        with CANVAS_TASK_LOCK:
            current = CANVAS_TASKS.get(task_id)
            if isinstance(current, dict):
                current.update({'status': 'failed', 'error': f'重启恢复失败：{detail}', 'updated_at': time.time()})


def recover_project_video_task(project_id: str, task_id: str) -> tuple[Dict[str, Any], Optional[Dict[str, Any]]]:
    """Registry 丢失后从 canvas 重建视频任务状态（与图片 recover 同构，#80/#82）。"""
    with project_lifecycle_lock(project_id):
        opened = _app_main.project_store_call('open_project', project_id)
        canvas = opened.get('canvas') or {}
        durable = durable_project_video_task(canvas, task_id)
        if durable is None:
            return {'canvas': canvas}, None
        if durable.get('status') == 'pending' and not durable.get('provider_task'):
            # 没有服务商任务 ID（提交前中断/专属适配器）：明确终止，提示重试（与图片同一文案口径）。
            failure_time = _app_main.now_ms()
            created_value = float(durable.get('created_at') or time.time())
            created_at = int(created_value * 1000) if 0 < created_value < 10_000_000_000 else int(created_value or failure_time)
            request_payload = durable.get('request_payload') or {}
            failure_record = {'id': f'generation-{uuid.uuid4().hex}', 'task_id': task_id, 'type': 'online-video', 'status': 'failed', 'prompt': str(request_payload.get('prompt') or ''), 'created_at': created_at, 'createdAt': created_at, 'completed_at': failure_time, 'runMs': max(0, failure_time - created_at), 'provider_id': durable.get('provider_id') or '', 'model': durable.get('model') or '', 'outputs': [], 'result_node_id': durable.get('result_node_id') or '', 'error': '服务重启后无法确认视频任务状态；为避免重复计费，任务已终止，请显式重试'}
            try:
                _app_main.project_store_call('fail_media_generation', project_id, durable.get('result_node_id') or '', task_id, failure_record)
                _app_main.project_store_call('flush', project_id)
            except Exception:
                pass
            durable = {**durable, 'status': 'failed', 'error': failure_record['error'], 'history_record': failure_record}
        return opened, durable


def durable_project_video_result(project_id: str, canvas: Mapping[str, Any], durable: Mapping[str, Any]) -> Dict[str, Any]:
    history_record = copy.deepcopy(durable.get('history_record') or {})
    base = f"/api/project-library/{urllib.parse.quote(project_id, safe='')}/media/"
    video_items = [
        {**copy.deepcopy(item), 'url': base + urllib.parse.quote(str(item.get('asset_id') or ''), safe='')}
        for item in history_record.get('outputs') or []
        if isinstance(item, dict) and item.get('asset_id')
    ]
    final_node = next((item for item in canvas.get('nodes') or [] if item.get('id') == durable.get('result_node_id')), None)
    return {
        'prompt': history_record.get('prompt') or '',
        'videos': [item['url'] for item in video_items],
        'video_items': video_items,
        'media_items': video_items,
        'history_recorded': bool(history_record),
        'history_record': history_record,
        'project_id': project_id,
        'project_revision': int(canvas.get('revision') or 0),
        'result_node_id': durable.get('result_node_id') or '',
        'final_node': final_node,
    }


@router.get('/api/project-library/{project_id}/video-generation-tasks/{task_id}')
async def get_project_video_task(project_id: str, task_id: str):
    with CANVAS_TASK_LOCK:
        current = copy.deepcopy(CANVAS_TASKS.get(str(task_id)) or {})
    if current:
        if current.get('type') != 'project-online-video' or str(current.get('project_id') or '') != str(project_id):
            raise HTTPException(status_code=404, detail='项目生视频任务不存在')
        current['task_id'] = str(task_id)
        return current
    try:
        opened, durable = await asyncio.to_thread(recover_project_video_task, project_id, str(task_id))
    except ProjectNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    if durable is None:
        raise HTTPException(status_code=404, detail='项目生视频任务不存在')
    canvas = opened.get('canvas') or {}
    result = durable_project_video_result(project_id, canvas, durable)
    if durable.get('status') == 'pending' and durable.get('provider_task'):
        # #82：有服务商任务 ID → 后台重轮询（不重复提交）。resuming 标志在锁内先置位，防并发 GET 双起重启。
        with CANVAS_TASK_LOCK:
            existing = CANVAS_TASKS.get(str(task_id))
            if isinstance(existing, dict) and existing.get('resuming'):
                return existing
            entry = {
                **copy.deepcopy(durable),
                'project_id': project_id,
                'status': 'running',
                'resuming': True,
                'result': None,
                'updated_at': time.time(),
            }
            CANVAS_TASKS[str(task_id)] = entry
        asyncio.get_running_loop().create_task(_resume_project_video_generation(str(task_id), str(project_id), copy.deepcopy(durable)))
        with CANVAS_TASK_LOCK:
            current = copy.deepcopy(CANVAS_TASKS.get(str(task_id)) or {})
        current['task_id'] = str(task_id)
        return current
    with CANVAS_TASK_LOCK:
        CANVAS_TASKS[str(task_id)] = {
            **copy.deepcopy(durable),
            'project_id': project_id,
            'result': result,
            'updated_at': time.time(),
        }
    await manager.broadcast_canvas_updated(project_id, int(canvas.get('updated_at') or _app_main.now_ms()))
    return {**durable, 'project_id': project_id, 'task_id': str(task_id), 'result': result}


@router.post('/api/project-library/{project_id}/video-generation-tasks/{task_id}/cancel')
async def cancel_project_video_task(project_id: str, task_id: str, request: Request):
    """#80：视频任务取消（与图片同构）。立即写 cancelled 终态；worker 若已下载完成，store CAS 仲裁谁赢。"""
    ensure_same_origin_request(request)
    with CANVAS_TASK_LOCK:
        current = CANVAS_TASKS.get(str(task_id))
        if (
            not isinstance(current, dict)
            or current.get('type') != 'project-online-video'
            or str(current.get('project_id') or '') != str(project_id)
        ):
            raise HTTPException(status_code=404, detail='项目生视频任务不存在')
        if current.get('status') == 'cancelled':
            return {'task_id': task_id, 'status': 'cancelled'}
        if current.get('status') not in {'queued', 'running'}:
            raise HTTPException(status_code=409, detail='任务已结束，无法取消')
        current['cancel_requested'] = True
        task = copy.deepcopy(current)
    raw_payload = task.get('request_payload') or {}
    try:
        payload = ProjectVideoGenerationRequest(**raw_payload)
    except Exception:
        payload = ProjectVideoGenerationRequest(prompt='（任务已取消）', result_node_id=str(task.get('result_node_id') or 'x'), provider_id=str(task.get('provider_id') or ''), model=str(task.get('model') or ''))
    route = _VideoRouteView({'provider_id': task.get('provider_id') or '', 'provider_name': '', 'model': task.get('model') or '', 'reason': ''})
    completed_at = _app_main.now_ms()
    created_at = round(float(task.get('created_at') or time.time()) * 1000)
    history_record = {'id': f'generation-{uuid.uuid4().hex}', 'task_id': task_id, 'type': 'online-video', 'status': 'cancelled', 'prompt': payload.prompt, 'created_at': created_at, 'createdAt': created_at, 'completed_at': completed_at, 'runMs': max(0, completed_at - created_at), 'provider_id': route.provider_id, 'model': route.model, 'outputs': [], 'result_node_id': payload.result_node_id, 'error': '任务已取消'}
    canvas = None
    try:
        canvas = await asyncio.to_thread(_app_main.project_store_call, 'cancel_media_generation', project_id, payload.result_node_id, task_id, history_record)
        await asyncio.to_thread(_app_main.project_store_call, 'flush', project_id)
    except Exception as exc:
        status_code = 409 if isinstance(exc, (ProjectStoreError, InvalidMutation)) else 500
        with CANVAS_TASK_LOCK:
            current = CANVAS_TASKS.get(str(task_id))
            if isinstance(current, dict):
                current.update({'status': 'failed', 'error': f'取消结果持久化失败，任务已停止：{exc}', 'updated_at': time.time()})
                current.pop('cancel_requested', None)
        raise HTTPException(status_code=status_code, detail=str(exc)) from exc
    with CANVAS_TASK_LOCK:
        current = CANVAS_TASKS.get(str(task_id))
        if isinstance(current, dict):
            current.update({'status': 'cancelled', 'error': '任务已取消', 'result': {'history_recorded': True, 'history_record': history_record, 'project_id': project_id, 'project_revision': (canvas or {}).get('revision'), 'result_node_id': payload.result_node_id}, 'updated_at': time.time()})
            current.pop('cancel_requested', None)
        future = PROJECT_VIDEO_GENERATION_FUTURES.get(str(task_id))
    if future is not None:
        future.cancel()
    try:
        await cleanup_project_generation_orphans(project_id)
    except Exception:
        pass
    try:
        await manager.broadcast_canvas_updated(project_id, int((canvas or {}).get('updated_at') or completed_at))
    except Exception:
        pass
    return {'task_id': task_id, 'status': 'cancelled'}


def project_canvas_video_payload(request: VideoGenerationRequest, provider_id: str, model: str, references: List[Dict[str, Any]]) -> CanvasVideoRequest:
    image_refs = [AIReference(url=item['adapter_url'], name=item.get('name') or 'reference', kind='image', mime=item.get('mime') or '') for item in references if item.get('kind') == 'image']
    video_refs = [item['adapter_url'] for item in references if item.get('kind') == 'video']
    audio_refs = [item['adapter_url'] for item in references if item.get('kind') == 'audio']
    # T8 归一化把未指定的 size/aspect_ratio/resolution 置为 None（供路由识别「未指定」），
    # 但 CanvasVideoRequest 这三个字段是 str 契约（空串=未指定，与归一化前行为一致）——None 会触发 pydantic 400。
    return CanvasVideoRequest(prompt=request.prompt, provider_id=provider_id, model=model, duration=request.duration, width=request.width, height=request.height, aspect_ratio=request.aspect_ratio or '', resolution=request.resolution or '', size=request.size or '', quality=request.quality, media_type=request.media_type, images=image_refs, videos=video_refs, audios=audio_refs, enhance_prompt=request.enhance_prompt, enable_upsample=request.enable_upsample, watermark=request.watermark, seed=request.seed, camera_fixed=request.camera_fixed, return_last_frame=request.return_last_frame, generate_audio=request.generate_audio, multimodal=bool(video_refs or audio_refs or len(image_refs) > 1))

def project_video_mark_candidate_incompatible(provider: Dict[str, Any], model: str, parameters: List[str]) -> None:
    if not parameters:
        return
    candidates = provider.setdefault(PROJECT_VIDEO_CANDIDATE_INCOMPATIBLE_KEY, {})
    current = candidates.setdefault(str(model or ''), [])
    current.extend((str(name).strip() for name in parameters if str(name).strip()))
    candidates[str(model or '')] = list(dict.fromkeys(current))

def project_video_candidate_preflight_incompatible_parameters(provider: Mapping[str, Any], model: str) -> List[str]:
    candidates = provider.get(PROJECT_VIDEO_CANDIDATE_INCOMPATIBLE_KEY)
    if not isinstance(candidates, Mapping):
        return []
    values = candidates.get(str(model or ''))
    return [str(name).strip() for name in values or [] if str(name).strip()]

def project_video_candidate_inline_reference_kinds(provider: Mapping[str, Any], model: str) -> set[str]:
    if is_runninghub_provider(provider) or is_jimeng_provider(provider) or is_apimart_provider(provider) or is_yuli_provider(provider) or is_agnes_provider(provider, model):
        return set()
    if is_volcengine_provider(provider):
        base_url = video_api_root(provider)
        is_proxy = bool(urllib.parse.urlparse(base_url).path.rstrip('/'))
        return {'video', 'audio'} if is_proxy else {'audio'}
    return {'video', 'audio'}

def project_video_candidate_execution_incompatible_parameters(provider: Mapping[str, Any], model: str, references: List[Dict[str, Any]]) -> List[str]:
    """Return synchronous execution blockers for one concrete route candidate."""
    incompatible: List[str] = []

    def add(name: str) -> None:
        if name not in incompatible:
            incompatible.append(name)
    if is_runninghub_provider(provider):
        wallet_key = str(provider.get('wallet_api_key') or '').strip() or _app_main.runninghub_wallet_key_value()
        if not wallet_key:
            add('api_key')
    elif not is_jimeng_provider(provider):
        if not video_api_root(provider):
            add('base_url')
        if not _app_main.provider_env_key_value(str(provider.get('id') or '')):
            add('api_key')
    inline_kinds = project_video_candidate_inline_reference_kinds(provider, model)
    if inline_kinds:
        for reference in references:
            if str(reference.get('kind') or '').strip().lower() not in inline_kinds:
                continue
            path = Path(str(reference.get('path') or ''))
            try:
                if path.stat().st_size > _app_main.PROJECT_VIDEO_REFERENCE_INLINE_MAX_BYTES:
                    add('reference_size')
                    break
            except OSError:
                add('reference_size')
                break
    return incompatible

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
            execution_incompatible = project_video_candidate_execution_incompatible_parameters(provider, current_model, references)
            project_video_mark_candidate_incompatible(provider, current_model, execution_incompatible)
            if video_adapter_incompatible_parameters(payload, provider) or execution_incompatible:
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
                    definition = await runninghub_model_definition(provider, current_model)
                except (HTTPException, httpx.HTTPError, OSError, ValueError, KeyError, TypeError):
                    remote_schema_failed = True
                    project_video_mark_candidate_incompatible(provider, current_model, ['adapter_schema'])
                    continue
            provider.setdefault('_runninghub_video_model_definitions', {})[current_model] = copy.deepcopy(definition)
        provider.pop('_runninghub_model_registry', None)
        if not pinned_provider:
            try:
                _app_main.route_video_generation(request, snapshots[:provider_index + 1], model=pinned_model, adapter_incompatible_parameters=functools.partial(project_video_adapter_incompatible_parameters, references=references))
            except ModelRoutingError:
                pass
            else:
                return snapshots
    return snapshots

def project_video_adapter_incompatible_parameters(request: VideoGenerationRequest, provider: Mapping[str, Any], model: str, *, references: List[Dict[str, Any]]) -> List[str]:
    """Evaluate the real adapter and resolved project references before registration."""
    payload = project_canvas_video_payload(request, str(provider.get('id') or ''), model, references)
    incompatible = video_adapter_incompatible_parameters(payload, provider)
    incompatible.extend(project_video_candidate_preflight_incompatible_parameters(provider, model))
    if incompatible or not is_runninghub_provider(provider):
        return list(dict.fromkeys(incompatible))
    model_definition = runninghub_configured_video_model_definition(provider, model) or {}
    endpoint = str(model_definition.get('endpoint') or model)
    image_to_video = runninghub_is_image_to_video(model) or runninghub_is_image_to_video(endpoint)
    params = model_definition.get('params') if isinstance(model_definition.get('params'), list) else []
    incompatible.extend(runninghub_video_incompatible_parameters(payload, params, image_to_video=image_to_video))
    return list(dict.fromkeys(incompatible))

@router.get('/api/project-library')
async def get_project_library(request: Request):
    ensure_same_origin_request(request)
    projects = await asyncio.to_thread(_app_main.project_store_call, 'list_projects')
    for project in projects:
        project['cleanup_preview_available'] = not project_has_open_session(
            str(project.get('id') or '')
        )
    return {'projects': projects}

@router.post('/api/project-library')
async def create_firefly_project(payload: FireflyProjectCreateRequest):
    project = await asyncio.to_thread(_app_main.project_store_call, 'create_project', payload.name)
    return {'project': project}

@router.post('/api/project-library/import')
async def import_firefly_project(request: Request):
    ensure_same_origin_request(request)
    form = None
    await acquire_fly_import_slot()
    try:
        parser = LimitedFlyMultipartParser(request.headers, request.stream(), max_files=1, max_fields=0, max_file_size=_app_main.FLY_MAX_PACKAGE_BYTES)
        form = await parser.parse()
        file = form.get('file')
        if not isinstance(file, StarletteUploadFile):
            raise HTTPException(status_code=400, detail='缺少 Fly 项目包')
        package_handle = getattr(file.file, '_file', file.file)
        project = await asyncio.to_thread(_app_main.project_store_call, 'import_fly', package_handle)
    except (KeyError, MultiPartException, ValueError) as exc:
        status_code = 413 if '20GB' in str(exc) else 400
        raise HTTPException(status_code=status_code, detail=str(exc)) from exc
    except ProjectStoreError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    finally:
        try:
            if form is not None:
                await form.close()
        finally:
            FLY_IMPORT_UPLOAD_LOCK.release()
    return {'project': project}

def project_media_source_path(source_url: str) -> Optional[str]:
    text = str(source_url or '').strip()
    if text.startswith(('/output/', '/assets/', '/api/storage-files/')):
        return _app_main.output_file_from_url(text)
    return None

def reserve_project_media_import(project_id: str, source_key: str):
    with project_lifecycle_lock(project_id):
        _app_main.project_store_call('open_project', project_id)
        executor = ensure_project_media_import_executor()
        with PROJECT_MEDIA_IMPORT_TASKS_LOCK:
            cutoff = _app_main.now_ms() - 60 * 60 * 1000
            for stale_id in [key for key, value in PROJECT_MEDIA_IMPORT_TASKS.items() if value.get('status') in {'completed', 'failed'} and int(value.get('updated_at') or 0) < cutoff]:
                PROJECT_MEDIA_IMPORT_TASKS.pop(stale_id, None)
                PROJECT_MEDIA_IMPORT_FUTURES.pop(stale_id, None)
            duplicate = next((value for value in PROJECT_MEDIA_IMPORT_TASKS.values() if value.get('project_id') == project_id and value.get('source_key') == source_key and (value.get('status') == 'pending')), None)
            if duplicate is not None:
                return (executor, duplicate, False)
            pending = [value for value in PROJECT_MEDIA_IMPORT_TASKS.values() if value.get('status') == 'pending']
            if len(pending) >= PROJECT_MEDIA_IMPORT_MAX_PENDING:
                raise HTTPException(status_code=429, detail='媒体导入队列已满，请稍后重试')
            if sum((1 for value in pending if value.get('project_id') == project_id)) >= _app_main.PROJECT_MEDIA_IMPORT_MAX_PER_PROJECT:
                raise HTTPException(status_code=429, detail='当前项目的媒体导入队列已满')
            task_id = uuid.uuid4().hex
            task = {'id': task_id, 'project_id': project_id, 'source_key': source_key, 'status': 'pending', 'created_at': _app_main.now_ms(), 'updated_at': _app_main.now_ms()}
            PROJECT_MEDIA_IMPORT_TASKS[task_id] = task
            return (executor, task, True)

def validate_project_media_source(source_path: str, expected_kind: str, declared_mime: str) -> str:
    """Return a verified MIME for media that has an explicit expected kind."""
    kind = str(expected_kind or '').strip().lower()
    if not kind:
        return declared_mime
    if kind == 'image':
        _width, _height, verified_mime = validate_generated_image(source_path)
        return verified_mime
    if kind == 'video':
        _width, _height, verified_mime = validate_generated_video(source_path)
        return verified_mime
    if kind == 'audio':
        mime = str(declared_mime or content_type_for_path(source_path)).split(';', 1)[0].strip().lower()
        if not mime.startswith('audio/'):
            raise ProjectStoreError('媒体内容不是有效音频')
        return mime
    raise ProjectStoreError('媒体预期类型不受支持')

@router.post('/api/project-library/{project_id}/media/import')
async def import_project_media(project_id: str, payload: ProjectMediaImportRequest, request: Request):
    ensure_same_origin_request(request)
    expected_kind = str(payload.expected_kind or '').strip().lower()
    if expected_kind and expected_kind not in {'image', 'video', 'audio'}:
        raise HTTPException(status_code=400, detail='媒体预期类型不受支持')
    source_path = _app_main.project_media_source_path(payload.source_url)
    if not source_path:
        raise HTTPException(status_code=400, detail='媒体必须先成为本机输出或上传文件')
    source_key = f'{os.path.normcase(os.path.realpath(source_path))}\n{expected_kind}'
    try:
        executor, task, created = await asyncio.to_thread(reserve_project_media_import, project_id, source_key)
    except ProjectNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    if not created:
        return JSONResponse(status_code=202, content={'task': public_project_media_import_task(task)})
    task_id = task['id']
    media_name = payload.name or os.path.basename(source_path)
    media_type = payload.mime_type or content_type_for_path(source_path)
    source_locator = str(payload.source_locator or payload.source_url or '').strip()
    source_type = 'remote_url' if source_locator.startswith(('http://', 'https://')) else 'external_path' if source_locator.startswith('file:') else 'local_output'

    def archive_media():
        try:
            verified_media_type = validate_project_media_source(source_path, expected_kind, media_type)
            record = store.import_media_file(project_id, source_path, media_name, verified_media_type, source_info={'locator': source_locator, 'name': media_name, 'type': source_type})
            update = {'status': 'completed', 'media': record}
        except ProjectStoreError as exc:
            update = {'status': 'failed', 'error': str(exc)}
        except Exception:
            update = {'status': 'failed', 'error': '项目媒体归档失败'}
        with PROJECT_MEDIA_IMPORT_TASKS_LOCK:
            current = PROJECT_MEDIA_IMPORT_TASKS.get(task_id)
            if current is not None:
                current.update(update)
                current['updated_at'] = _app_main.now_ms()
            PROJECT_MEDIA_IMPORT_FUTURES.pop(task_id, None)
    with PROJECT_LIBRARY_SWITCH_LOCK:
        store = _app_main.FIREFLY_PROJECT_STORE
        try:
            future = executor.submit(archive_media)
        except RuntimeError as exc:
            with PROJECT_MEDIA_IMPORT_TASKS_LOCK:
                PROJECT_MEDIA_IMPORT_TASKS.pop(task_id, None)
            raise HTTPException(status_code=503, detail='媒体导入服务正在关闭') from exc
        with PROJECT_MEDIA_IMPORT_TASKS_LOCK:
            PROJECT_MEDIA_IMPORT_FUTURES[task_id] = future

    def forget_future(done_future: Future):
        with PROJECT_MEDIA_IMPORT_TASKS_LOCK:
            if PROJECT_MEDIA_IMPORT_FUTURES.get(task_id) is done_future:
                PROJECT_MEDIA_IMPORT_FUTURES.pop(task_id, None)
    future.add_done_callback(forget_future)
    return JSONResponse(status_code=202, content={'task': public_project_media_import_task(task)})

@router.get('/api/project-library/{project_id}/media/import/{task_id}')
async def project_media_import_status(project_id: str, task_id: str):
    with PROJECT_MEDIA_IMPORT_TASKS_LOCK:
        task = PROJECT_MEDIA_IMPORT_TASKS.get(task_id)
        if not task or task.get('project_id') != project_id:
            raise HTTPException(status_code=404, detail='媒体导入任务不存在')
        return {'task': public_project_media_import_task(task)}

@router.get('/api/project-library/{project_id}/media')
async def list_project_media(project_id: str):
    try:
        records = await asyncio.to_thread(_app_main.project_store_call, 'list_media', project_id)
    except ProjectNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    items = []
    for record in records:
        asset_id = record['asset_id']
        base = f"/api/project-library/{urllib.parse.quote(project_id, safe='')}/media/{urllib.parse.quote(asset_id, safe='')}"
        item = dict(record)
        item['original_url'] = base
        item['preview_url'] = f'{base}/preview' if item.get('kind') in {'image', 'video'} else ''
        items.append(item)
    return {'items': items}

def build_project_media_preview(project_id: str, asset_id: str, width: int) -> str:
    record = _app_main.project_store_call('media_record', project_id, asset_id)
    if record.get('kind') not in {'image', 'video'}:
        raise ProjectStoreError('该媒体没有图像预览')
    source = Path(record['path'])
    preview_root = source.parent.parent / 'previews'
    preview_root.mkdir(parents=True, exist_ok=True)
    preview_path = preview_root / f'{asset_id}-{width}.webp'
    if preview_path.is_file():
        return str(preview_path)
    image = _app_main.generate_video_preview_image(str(source), width) if record.get('kind') == 'video' else Image.open(source)
    try:
        image.thumbnail((width, width), Image.Resampling.LANCZOS)
        if image.mode not in {'RGB', 'RGBA'}:
            converted = image.convert('RGBA' if 'transparency' in image.info else 'RGB')
            image.close()
            image = converted
        temporary = preview_root / f'.{asset_id}-{width}.{uuid.uuid4().hex}.tmp'
        try:
            image.save(temporary, format='WEBP', quality=82, method=4)
            os.replace(temporary, preview_path)
        finally:
            temporary.unlink(missing_ok=True)
    finally:
        image.close()
    return str(preview_path)

@router.get('/api/project-library/{project_id}/media/{asset_id}/preview')
async def get_project_media_preview(project_id: str, asset_id: str, w: int=512):
    width = max(64, min(2048, int(w or 512)))
    try:
        path = await asyncio.to_thread(build_project_media_preview, project_id, asset_id, width)
    except ProjectNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except (ProjectStoreError, OSError, ValueError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return FileResponse(path, media_type='image/webp')

def copy_project_media_to_global_library(project_id: str, asset_id: str) -> Dict[str, Any]:
    record = _app_main.project_store_call('media_record', project_id, asset_id)
    kind, extension = _local_upload_kind_ext(str(record.get('name') or record.get('filename') or ''), str(record.get('mime_type') or ''))
    if kind not in {'image', 'video', 'audio'}:
        raise ProjectStoreError('该媒体类型不能保存到全局资源库')
    base = os.path.splitext(os.path.basename(str(record.get('name') or 'project-media')))[0]
    base = re.sub('[^0-9A-Za-z一-鿿._-]+', '_', base).strip('_') or 'project-media'
    filename = f'up_{uuid.uuid4().hex[:12]}_{base[:60]}{extension}'
    os.makedirs(_app_main.LOCAL_UPLOAD_DIR, exist_ok=True)
    destination = Path(_app_main.LOCAL_UPLOAD_DIR) / filename
    temporary = Path(_app_main.LOCAL_UPLOAD_DIR) / f'.{filename}.{uuid.uuid4().hex}.tmp'
    try:
        with open(record['path'], 'rb') as source_handle, temporary.open('wb') as destination_handle:
            shutil.copyfileobj(source_handle, destination_handle, length=1024 * 1024)
            destination_handle.flush()
            os.fsync(destination_handle.fileno())
        os.replace(temporary, destination)
    finally:
        temporary.unlink(missing_ok=True)
    return _local_upload_item(filename)

@router.post('/api/project-library/{project_id}/media/{asset_id}/copy-to-global')
async def copy_project_media_to_global(project_id: str, asset_id: str):
    try:
        item = await asyncio.to_thread(copy_project_media_to_global_library, project_id, asset_id)
    except ProjectNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except (ProjectStoreError, OSError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {'item': item, 'files': [item], 'count': 1}

def project_has_active_media_import(project_id: str) -> bool:
    with PROJECT_MEDIA_IMPORT_TASKS_LOCK:
        return any((task.get('project_id') == project_id and task.get('status') == 'pending' for task in PROJECT_MEDIA_IMPORT_TASKS.values()))

def project_has_active_image_generation(project_id: str) -> bool:
    with CANVAS_TASK_LOCK:
        return any((task.get('type') == 'project-online-image' and task.get('project_id') == project_id and (task.get('status') in {'registering', 'queued', 'running'}) for task in CANVAS_TASKS.values()))

def project_has_active_video_generation(project_id: str) -> bool:
    with CANVAS_TASK_LOCK:
        return any((task.get('type') == 'project-online-video' and task.get('project_id') == project_id and (task.get('status') in {'queued', 'running'}) for task in CANVAS_TASKS.values()))

def project_has_pending_generation_handoff(project_id: str) -> bool:
    with CANVAS_TASK_LOCK:
        return bool(
            PROJECT_IMAGE_GENERATION_ORPHANS.get(str(project_id))
            or PROJECT_VIDEO_GENERATION_ORPHANS.get(str(project_id))
        )

def canvas_has_pending_conversation(canvas: Dict[str, Any]) -> bool:
    return any((node.get('type') == 'smart-conversation' and any((isinstance(message, dict) and message.get('role') == 'user' and (message.get('status') == 'pending') for message in node.get('messages') or [])) for node in canvas.get('nodes') or [] if isinstance(node, dict)))

def project_has_recoverable_conversation_sidecars(store: ProjectStore, project_dir: Path, project_id: str, canvas: Dict[str, Any]) -> bool:
    sidecar_root = store._safe_project_path(project_dir, '.conversation-tasks')
    if not sidecar_root.is_dir():
        return False
    active = False
    for entry in list(sidecar_root.iterdir()):
        try:
            path = store._safe_project_path(project_dir, f'.conversation-tasks/{entry.name}')
            if entry.name.lower().endswith('.tmp'):
                if path.is_file():
                    path.unlink()
                elif path.exists():
                    active = True
                continue
            if not path.is_file():
                active = True
                continue
            payload = json.loads(path.read_text(encoding='utf-8'))
            if not isinstance(payload, dict) or str(payload.get('project_id') or '') != str(project_id):
                active = True
                continue
            node_id = str(payload.get('node_id') or '')
            request_id = str(payload.get('request_id') or '')
            ready = payload.get('ready')
            if payload.get('state') != 'ready' or not node_id or (not request_id) or (not isinstance(ready, dict)):
                active = True
                continue
            snapshot = conversation_task_snapshot(canvas, node_id, request_id)
            if not snapshot or snapshot.get('status') not in {'completed', 'failed'}:
                active = True
                continue
            expected_user_id = str(ready.get('user_message_id') or '')
            actual_user_id = str((snapshot.get('message') or {}).get('id') or '')
            if expected_user_id and expected_user_id != actual_user_id:
                active = True
                continue
            ready_assistant = ready.get('assistant')
            actual_assistant = snapshot.get('assistant')
            if isinstance(ready_assistant, dict):
                if not isinstance(actual_assistant, dict) or any((str(ready_assistant.get(key) or '') != str(actual_assistant.get(key) or '') for key in ('id', 'request_id'))):
                    active = True
                    continue
            elif not ready.get('error') or snapshot.get('status') != 'failed':
                active = True
                continue
            path.unlink()
            forget_project_conversation_ready_result(project_id, node_id, request_id)
        except (ProjectStoreError, OSError, ValueError, json.JSONDecodeError):
            active = True
    try:
        sidecar_root.rmdir()
    except OSError:
        pass
    return active

def project_has_active_conversation(project_id: str, *, trashed: bool=False) -> bool:
    """Conservatively block lifecycle moves while a paid turn can still be recovered."""
    if not trashed:
        with PROJECT_CONVERSATION_TASKS_LOCK:
            if any((key[0] == str(project_id) and (not handle.done()) for key, handle in PROJECT_CONVERSATION_TASKS.items())):
                return True
    with PROJECT_LIBRARY_SWITCH_LOCK:
        store = _app_main.FIREFLY_PROJECT_STORE
    if store is None:
        return False
    project_dir = (Path(store.project_trash_root) if trashed else Path(store.root)) / str(project_id)
    if not project_dir.is_dir():
        return False
    try:
        if trashed:
            canvas = json.loads(store._safe_project_path(project_dir, 'canvas.json').read_text(encoding='utf-8'))
        else:
            canvas = store.open_project(project_id)['canvas']
        return project_has_recoverable_conversation_sidecars(store, project_dir, project_id, canvas) or canvas_has_pending_conversation(canvas)
    except (ProjectStoreError, OSError, ValueError, json.JSONDecodeError):
        return True

def project_has_cleanup_blocking_conversation(project_id: str) -> bool:
    """Inspect conversation owners without pruning or reconciling durable state."""
    with PROJECT_CONVERSATION_TASKS_LOCK:
        if any(
            key[0] == str(project_id) and not handle.done()
            for key, handle in PROJECT_CONVERSATION_TASKS.items()
        ):
            return True
    with PROJECT_LIBRARY_SWITCH_LOCK:
        store = _app_main.FIREFLY_PROJECT_STORE
    if store is None:
        return False
    project_dir = Path(store.root) / str(project_id)
    if not project_dir.is_dir():
        return False
    try:
        with store._lock:
            loaded_canvas = store._canvases.get(str(project_id))
            canvas = copy.deepcopy(loaded_canvas) if isinstance(loaded_canvas, dict) else None
        if canvas is None:
            canvas = json.loads(
                store._safe_project_path(project_dir, 'canvas.json').read_text(encoding='utf-8')
            )
        if not isinstance(canvas, dict) or canvas_has_pending_conversation(canvas):
            return True
        sidecar_root = store._safe_project_path(project_dir, '.conversation-tasks')
        if not sidecar_root.exists():
            return False
        if not sidecar_root.is_dir():
            return True
        for entry in sidecar_root.iterdir():
            path = store._safe_project_path(project_dir, f'.conversation-tasks/{entry.name}')
            if not path.is_file() or entry.name.lower().endswith('.tmp'):
                return True
            payload = json.loads(path.read_text(encoding='utf-8'))
            if not isinstance(payload, dict) or str(payload.get('project_id') or '') != str(project_id):
                return True
            node_id = str(payload.get('node_id') or '')
            request_id = str(payload.get('request_id') or '')
            ready = payload.get('ready')
            if payload.get('state') != 'ready' or not node_id or not request_id or not isinstance(ready, dict):
                return True
            snapshot = conversation_task_snapshot(canvas, node_id, request_id)
            if not snapshot or snapshot.get('status') not in {'completed', 'failed'}:
                return True
            expected_user_id = str(ready.get('user_message_id') or '')
            actual_user_id = str((snapshot.get('message') or {}).get('id') or '')
            if expected_user_id and expected_user_id != actual_user_id:
                return True
            ready_assistant = ready.get('assistant')
            actual_assistant = snapshot.get('assistant')
            if isinstance(ready_assistant, dict):
                if not isinstance(actual_assistant, dict) or any(
                    str(ready_assistant.get(key) or '') != str(actual_assistant.get(key) or '')
                    for key in ('id', 'request_id')
                ):
                    return True
            elif not ready.get('error') or snapshot.get('status') != 'failed':
                return True
        return False
    except (ProjectStoreError, OSError, ValueError, json.JSONDecodeError):
        return True


def project_has_active_agent_task(project_id: str) -> bool:
    """Block lifecycle moves while any runtime or durable Agent task is active."""
    with PROJECT_AGENT_TASKS_LOCK:
        if any(
            str(task.get('project_id') or '') == str(project_id)
            and task.get('status') in {'queued', 'running'}
            for task in PROJECT_AGENT_TASKS.values()
        ):
            return True
    with PROJECT_LIBRARY_SWITCH_LOCK:
        store = _app_main.FIREFLY_PROJECT_STORE
    if store is None:
        return False
    project_dir = Path(store.root) / str(project_id)
    if not project_dir.is_dir():
        return False
    try:
        with store._lock:
            loaded_canvas = store._canvases.get(str(project_id))
            canvas = copy.deepcopy(loaded_canvas) if isinstance(loaded_canvas, dict) else None
        if canvas is None:
            canvas = json.loads(
                store._safe_project_path(project_dir, 'canvas.json').read_text(encoding='utf-8')
            )
        if not isinstance(canvas, dict) or not isinstance(canvas.get('nodes'), list):
            return True
        return any(
            isinstance(node, dict)
            and node.get('type') == 'agent-task'
            and node.get('status') in {'queued', 'running'}
            for node in canvas['nodes']
        )
    except (ProjectStoreError, OSError, ValueError, json.JSONDecodeError):
        return True

def export_project_package(project_id: str, path: str):
    with project_lifecycle_lock(project_id):
        if project_has_active_media_import(project_id):
            raise ProjectMediaImportActive('项目仍有媒体正在导入，请稍后再导出')
        if project_has_active_image_generation(project_id):
            raise ProjectMediaImportActive('项目仍有图片正在生成，请稍后再导出')
        if project_has_active_video_generation(project_id):
            raise ProjectMediaImportActive('项目仍有视频正在生成，请稍后再导出')
        if project_has_active_conversation(project_id):
            raise ProjectMediaImportActive('项目仍有对话任务待完成或待恢复，请稍后再导出')
        with PROJECT_LIBRARY_SWITCH_LOCK:
            store = _app_main.FIREFLY_PROJECT_STORE
        return store.export_fly_to(project_id, path)

def duplicate_project_snapshot(project_id: str):
    with project_lifecycle_lock(project_id):
        if project_has_active_media_import(project_id):
            raise ProjectMediaImportActive('项目仍有媒体正在导入，请稍后再复制')
        if project_has_active_image_generation(project_id):
            raise ProjectMediaImportActive('项目仍有图片正在生成，请稍后再复制')
        if project_has_active_video_generation(project_id):
            raise ProjectMediaImportActive('项目仍有视频正在生成，请稍后再复制')
        if project_has_active_conversation(project_id):
            raise ProjectMediaImportActive('项目仍有对话任务待完成或待恢复，请稍后再复制')
        if project_has_active_agent_task(project_id):
            raise ProjectMediaImportActive('项目仍有 Agent 任务正在执行，请稍后再复制')
        with PROJECT_LIBRARY_SWITCH_LOCK:
            store = _app_main.FIREFLY_PROJECT_STORE
        return store.duplicate_project(project_id)

def project_lifecycle_store_call(method_name: str, project_id: str, *, block_active_import: bool=False):
    with project_lifecycle_lock(project_id):
        if block_active_import and project_has_active_media_import(project_id):
            raise ProjectMediaImportActive('项目仍有媒体正在导入，请等待完成后再删除')
        if block_active_import and project_has_active_image_generation(project_id):
            raise ProjectMediaImportActive('项目仍有图片正在生成，请等待完成后再删除')
        if block_active_import and project_has_active_video_generation(project_id):
            raise ProjectMediaImportActive('项目仍有视频正在生成，请等待完成后再删除')
        if block_active_import and project_has_active_conversation(project_id, trashed=method_name == 'purge_trashed_project'):
            raise ProjectMediaImportActive('项目仍有对话任务待完成或待恢复，不能删除')
        if block_active_import and project_has_active_agent_task(project_id):
            raise ProjectMediaImportActive('项目仍有 Agent 任务正在执行，请等待完成后再删除')
        return _app_main.project_store_call(method_name, project_id)

def project_has_active_photoshop_round_trip(project_id: str) -> bool:
    now = _app_main.now_ms()
    with PHOTOSHOP_ROUND_TRIP_LOCK:
        return any(
            str(record.get('project_id') or '') == str(project_id)
            and record.get('state') in {'pending', 'processing'}
            and (
                record.get('state') == 'processing'
                or now < int(record.get('expires_at_ms') or 0)
            )
            for record in PHOTOSHOP_ROUND_TRIP_REQUESTS.values()
            if isinstance(record, dict)
        )


def project_has_active_export(project_id: str) -> bool:
    with PROJECT_EXPORTS_LOCK:
        return int(PROJECT_ACTIVE_EXPORTS.get(str(project_id), 0)) > 0


def _begin_project_export(project_id: str) -> None:
    with project_owner_admission(project_id):
        with PROJECT_EXPORTS_LOCK:
            key = str(project_id)
            PROJECT_ACTIVE_EXPORTS[key] = int(PROJECT_ACTIVE_EXPORTS.get(key, 0)) + 1


def _finish_project_export(project_id: str, path: str = '') -> None:
    if path:
        Path(path).unlink(missing_ok=True)
    with PROJECT_EXPORTS_LOCK:
        key = str(project_id)
        remaining = int(PROJECT_ACTIVE_EXPORTS.get(key, 0)) - 1
        if remaining > 0:
            PROJECT_ACTIVE_EXPORTS[key] = remaining
        else:
            PROJECT_ACTIVE_EXPORTS.pop(key, None)


class ProjectCleanupConfirmationInvalid(RuntimeError):
    pass


def _assert_cleanup_owners_clear(project_id: str, *, action: str) -> None:
    """Fail closed while any runtime owner can still touch this project."""
    if project_has_active_export(project_id):
        raise ProjectMediaImportActive(f'项目正在导出，不能{action}永久清理')
    if project_has_open_session(project_id):
        raise ProjectMediaImportActive(f'项目仍在打开，不能{action}永久清理')
    if project_has_active_download(project_id):
        raise ProjectMediaImportActive(f'项目媒体正在下载，不能{action}永久清理')
    if project_has_active_media_import(project_id):
        raise ProjectMediaImportActive(f'项目仍有媒体正在导入，不能{action}永久清理')
    if project_has_active_image_generation(project_id):
        raise ProjectMediaImportActive(f'项目仍有图片正在生成，不能{action}永久清理')
    if project_has_active_video_generation(project_id):
        raise ProjectMediaImportActive(f'项目仍有视频正在生成，不能{action}永久清理')
    if project_has_pending_generation_handoff(project_id):
        raise ProjectMediaImportActive(f'项目仍有生成媒体待处理，不能{action}永久清理')
    if project_has_cleanup_blocking_conversation(project_id):
        raise ProjectMediaImportActive(f'项目仍有对话任务待完成或待恢复，不能{action}永久清理')
    if project_has_active_agent_task(project_id):
        raise ProjectMediaImportActive(f'项目仍有 Agent 任务正在执行，不能{action}永久清理')
    if project_has_active_photoshop_round_trip(project_id):
        raise ProjectMediaImportActive(f'项目仍有 Photoshop 往返待完成，不能{action}永久清理')


def preview_project_cleanup_snapshot(project_id: str) -> Dict[str, Any]:
    _assert_cleanup_owners_clear(project_id, action='预览')
    with project_lifecycle_lock(project_id):
        _assert_cleanup_owners_clear(project_id, action='预览')
        with PROJECT_LIBRARY_SWITCH_LOCK:
            store = _app_main.FIREFLY_PROJECT_STORE
        if store is None:
            raise ProjectStoreError('项目库不可用')
        preview = store.preview_project_cleanup(project_id)
        _assert_cleanup_owners_clear(project_id, action='预览')
        return preview


def execute_project_cleanup_snapshot(project_id: str, confirmation: Dict[str, Any]) -> Dict[str, Any]:
    """Validate a bound confirmation, then permanently delete its exact candidates."""
    if not isinstance(confirmation, dict):
        raise ProjectCleanupConfirmationInvalid('永久清理确认必须是对象')
    allowed_fields = {'revision', 'ownership_proof', 'candidate_asset_ids'}
    if set(confirmation) - allowed_fields:
        raise ProjectCleanupConfirmationInvalid('永久清理确认包含未知字段')
    revision = confirmation.get('revision')
    ownership_proof = confirmation.get('ownership_proof')
    candidate_asset_ids = confirmation.get('candidate_asset_ids')
    if isinstance(revision, bool) or not isinstance(revision, int) or revision < 0:
        raise ProjectCleanupConfirmationInvalid('永久清理确认包含无效版本')
    if not isinstance(ownership_proof, str) or not re.fullmatch(r'[0-9a-f]{64}', ownership_proof):
        raise ProjectCleanupConfirmationInvalid('永久清理确认包含无效所有权证明')
    if not isinstance(candidate_asset_ids, list) or any(
        not isinstance(item, str) or not re.fullmatch(r'[0-9a-f]{64}', item)
        for item in candidate_asset_ids
    ):
        raise ProjectCleanupConfirmationInvalid('永久清理确认包含无效候选')
    _assert_cleanup_owners_clear(project_id, action='执行')
    with project_cleanup_lease(project_id):
        _assert_cleanup_owners_clear(project_id, action='执行')
        with PROJECT_LIBRARY_SWITCH_LOCK:
            store = _app_main.FIREFLY_PROJECT_STORE
        if store is None:
            raise ProjectStoreError('项目库不可用')
        return store.execute_project_cleanup(
            project_id,
            revision=revision,
            ownership_proof=ownership_proof,
            candidate_asset_ids=candidate_asset_ids,
        )

@router.get('/api/project-library/{project_id}/cleanup-preview')
async def preview_firefly_project_cleanup(project_id: str, request: Request):
    ensure_same_origin_request(request)
    try:
        preview = await asyncio.to_thread(preview_project_cleanup_snapshot, project_id)
    except ProjectMediaImportActive as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except ProjectNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except (ProjectStoreError, OSError) as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    base = f"/api/project-library/{urllib.parse.quote(project_id, safe='')}/media/"
    for candidate in preview.get('candidates') or []:
        if isinstance(candidate, dict):
            candidate['thumbnail_url'] = base + urllib.parse.quote(
                str(candidate.get('asset_id') or ''),
                safe='',
            )
    return {'preview': preview}

@router.post('/api/project-library/{project_id}/cleanup-execute')
async def execute_firefly_project_cleanup(project_id: str, request: Request):
    ensure_same_origin_request(request)
    try:
        confirmation = await request.json()
    except (ValueError, json.JSONDecodeError):
        raise HTTPException(status_code=400, detail='永久清理确认格式无效')
    if not isinstance(confirmation, dict):
        raise HTTPException(status_code=400, detail='永久清理确认必须是对象')
    try:
        result = await asyncio.to_thread(execute_project_cleanup_snapshot, project_id, confirmation)
    except ProjectCleanupConfirmationInvalid as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except CleanupDrift as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except (ProjectMediaImportActive, ProjectCleanupActive) as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except ProjectNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except (ProjectStoreError, OSError) as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return {'result': result}

@router.post('/api/project-library/{project_id}/duplicate')
async def duplicate_firefly_project(project_id: str, request: Request):
    ensure_same_origin_request(request)
    try:
        project = await asyncio.to_thread(duplicate_project_snapshot, project_id)
    except ProjectMediaImportActive as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except ProjectNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except (ProjectStoreError, OSError) as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return {'project': project}

@router.get('/api/project-library/{project_id}/export')
async def export_firefly_project(project_id: str, request: Request):
    ensure_same_origin_request(request)
    descriptor, path = tempfile.mkstemp(prefix='firefly-export-', suffix='.fly')
    os.close(descriptor)
    try:
        _begin_project_export(project_id)
        exported = await asyncio.to_thread(export_project_package, project_id, path)
    except (ProjectMediaImportActive, ProjectCleanupActive) as exc:
        _finish_project_export(project_id, path)
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except ProjectNotFound as exc:
        _finish_project_export(project_id, path)
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except (ProjectStoreError, OSError) as exc:
        _finish_project_export(project_id, path)
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    filename = f"{str(exported.get('project_name') or 'firefly-project')[:60]}.fly"
    return FileResponse(
        path,
        media_type='application/zip',
        filename=filename,
        background=BackgroundTask(_finish_project_export, project_id, path),
    )

@router.get('/api/project-library/trash')
async def list_project_trash(request: Request):
    ensure_same_origin_request(request)
    return {'projects': await asyncio.to_thread(_app_main.project_store_call, 'list_trashed_projects')}

@router.post('/api/project-library/trash/{project_id}/restore')
async def restore_trashed_project(project_id: str, request: Request):
    ensure_same_origin_request(request)
    try:
        project = await asyncio.to_thread(project_lifecycle_store_call, 'restore_project', project_id)
    except ProjectStoreError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return {'project': project}

@router.delete('/api/project-library/trash/{project_id}')
async def purge_trashed_project(project_id: str, request: Request):
    ensure_same_origin_request(request)
    try:
        return await asyncio.to_thread(project_lifecycle_store_call, 'purge_trashed_project', project_id, block_active_import=True)
    except ProjectMediaImportActive as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except ProjectStoreError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

@router.delete('/api/project-library/{project_id}')
async def trash_firefly_project(project_id: str, request: Request):
    ensure_same_origin_request(request)
    try:
        return await asyncio.to_thread(project_lifecycle_store_call, 'trash_project', project_id, block_active_import=True)
    except ProjectMediaImportActive as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except ProjectNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ProjectStoreError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc

@router.get('/api/project-library/{project_id}/media/unused')
async def list_project_media_unused(project_id: str):
    """检测项目内未被任何画布节点/历史引用的媒体资源（issue #56）。

    引用规则与删除清理（_collect_asset_ids）和自动清理
    （delete_media_if_unreferenced）一致；回收区（trash）内的
    资源本就不在媒体索引中，不会被列出。
    """
    try:
        assert_project_owner_admissible(project_id)
        def build_unused() -> List[Dict[str, Any]]:
            project_data = _app_main.project_store_call('open_project', project_id) or {}
            referenced = ProjectStore._collect_asset_ids(project_data)
            items = []
            for record in _app_main.project_store_call('list_media', project_id) or []:
                asset_id = str(record.get('asset_id') or '').strip()
                if not asset_id or asset_id in referenced:
                    continue
                items.append({
                    'asset_id': asset_id,
                    'name': str(record.get('name') or record.get('filename') or ''),
                    'kind': str(record.get('kind') or ''),
                    'mime_type': str(record.get('mime_type') or ''),
                    'size': record.get('size'),
                    'created_at': record.get('created_at'),
                })
            return items
        return {'items': await asyncio.to_thread(build_unused)}
    except ProjectNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ProjectStoreError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

class ProjectMediaArchiveRequest(BaseModel):
    reason: str = Field(default='node-delete', max_length=64)

@router.post('/api/project-library/{project_id}/media/{asset_id}/archive')
async def archive_project_media_asset(project_id: str, asset_id: str, payload: ProjectMediaArchiveRequest):
    """级联归档（issue #74 / D1）：节点/单图删除后，无引用的媒体移入回收区（7 天可恢复）。

    仍被画布节点或历史记录引用 → 409（前端保留在库并继续）；项目不存在 → 404。
    """
    try:
        assert_project_owner_admissible(project_id)
        await asyncio.to_thread(
            _app_main.project_store_call,
            'archive_unreferenced_media',
            project_id,
            asset_id,
            reason=str(payload.reason or 'node-delete')[:64] or 'node-delete',
        )
    except ProjectNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ProjectStoreError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    await manager.broadcast_canvas_updated(project_id, int(_app_main.now_ms()))
    return {'ok': True, 'archived': asset_id}

@router.get('/api/project-library/{project_id}/media/{asset_id}')
async def get_project_media(project_id: str, asset_id: str):
    def build_response() -> ProjectMediaFileResponse:
        with project_lifecycle_lock(project_id):
            record = _app_main.project_store_call('media_record', project_id, asset_id)
            return ProjectMediaFileResponse(
                record['path'],
                project_id=project_id,
                media_type=record.get('mime_type') or 'application/octet-stream',
            )

    try:
        assert_project_owner_admissible(project_id)
        return await asyncio.to_thread(build_response)
    except ProjectNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ProjectStoreError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except ProjectCleanupActive as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc

@router.get('/api/project-library/{project_id}/media/{asset_id}/references')
async def get_project_media_references(project_id: str, asset_id: str):
    """列出直接引用该媒体资源的画布节点（与删除清理使用同一引用收集规则）。"""
    try:
        assert_project_owner_admissible(project_id)
        def build_references() -> List[Dict[str, Any]]:
            project_data = _app_main.project_store_call('open_project', project_id)
            if not project_data:
                return []
            canvas_data = project_data.get('canvas') or {}
            references = []
            seen = set()
            for node in canvas_data.get('nodes') or []:
                if asset_id not in ProjectStore._collect_asset_ids(node):
                    continue
                node_id = str(node.get('id') or '')
                if node_id in seen:
                    continue
                seen.add(node_id)
                references.append({
                    'node_id': node_id,
                    'node_type': str(node.get('type') or node.get('kind') or ''),
                    'node_title': str(node.get('title') or node.get('label') or ''),
                })
            return references
        return {'references': await asyncio.to_thread(build_references)}
    except ProjectNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ProjectStoreError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


class ProjectMediaDeleteRequest(BaseModel):
    """用户主动删除资源库媒体（硬删除 + 清理项目内直接引用，见 ADR 0015）。"""
    base_revision: int = Field(default=0, ge=0)

@router.delete('/api/project-library/{project_id}/media/{asset_id}')
async def delete_project_media(
    project_id: str,
    asset_id: str,
    payload: ProjectMediaDeleteRequest = ProjectMediaDeleteRequest(),
) -> JSONResponse:
    """删除项目媒体资源：硬删除文件并清理项目内全部直接引用。"""
    try:
        assert_project_owner_admissible(project_id)
        result = await asyncio.to_thread(
            _app_main.project_store_call,
            'delete_project_media_asset',
            project_id,
            asset_id,
            base_revision=int(payload.base_revision or 0),
        )
    except ProjectNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except RevisionConflict as exc:
        raise HTTPException(status_code=409, detail={'message': str(exc), 'canvas': exc.canvas}) from exc
    except ProjectStoreError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except ProjectCleanupActive as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    canvas = result.get('canvas') or {}
    await _app_main.manager.broadcast_canvas_updated(project_id, int(canvas.get('updated_at') or _app_main.now_ms()))
    return JSONResponse({'ok': True, 'canvas': canvas, 'deleted': result.get('deleted')})

@router.get('/api/project-library/{project_id}')
async def open_firefly_project(project_id: str):
    try:
        return await asyncio.to_thread(_app_main.project_store_call, 'open_project', project_id)
    except ProjectNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ProjectStoreError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc

def _photoshop_prune_requests_locked(now: Optional[int]=None) -> None:
    now_value = int(now if now is not None else _app_main.now_ms())
    changed_projects = set()
    for request_id, record in list(PHOTOSHOP_ROUND_TRIP_REQUESTS.items()):
        expires_at = int(record.get('expires_at_ms') or 0)
        if record.get('state') == 'pending' and now_value >= expires_at:
            record['state'] = 'expired'
            changed_projects.add(str(record.get('project_id') or ''))
        terminal_at = int(record.get('returned_at') or 0) * 1000 or expires_at
        if record.get('state') in {'returned', 'expired'} and now_value - terminal_at > PHOTOSHOP_ROUND_TRIP_RETENTION_MS:
            PHOTOSHOP_ROUND_TRIP_REQUESTS.pop(request_id, None)
            changed_projects.add(str(record.get('project_id') or ''))
    overflow = max(0, len(PHOTOSHOP_ROUND_TRIP_REQUESTS) - PHOTOSHOP_ROUND_TRIP_MAX_REQUESTS)
    if overflow > 0:
        terminal = sorted(((int(record.get('returned_at') or 0) * 1000 or int(record.get('expires_at_ms') or 0), request_id) for request_id, record in PHOTOSHOP_ROUND_TRIP_REQUESTS.items() if record.get('state') in {'returned', 'expired'}))
        for _, request_id in terminal[:overflow]:
            removed = PHOTOSHOP_ROUND_TRIP_REQUESTS.pop(request_id, None)
            if removed:
                changed_projects.add(str(removed.get('project_id') or ''))
    # issue #95：状态变化后同步落盘，避免重启后状态回退。
    for project_id in sorted(p for p in changed_projects if p):
        _app_main._photoshop_persist_table_locked(project_id)

def _photoshop_request_snapshot(request_id: str) -> Optional[Dict[str, Any]]:
    with PHOTOSHOP_ROUND_TRIP_LOCK:
        _photoshop_prune_requests_locked()
        record = PHOTOSHOP_ROUND_TRIP_REQUESTS.get(str(request_id or ''))
        if not record:
            return None
        result = copy.deepcopy(record)
        if result.get('state') == 'pending' and _app_main.now_ms() >= int(result.get('expires_at_ms') or 0):
            record['state'] = 'expired'
            result['state'] = 'expired'
        if result.get('state') == 'processing':
            result['state'] = 'pending'
        result.pop('expires_at_ms', None)
        return result

def _photoshop_public_request(record: Dict[str, Any]) -> Dict[str, Any]:
    result = copy.deepcopy(record)
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
        current.update({'state': 'returned', 'returned_at': _app_main.now_ms() // 1000, 'result_node_id': committed['result_node']['id'], 'result_media_id': committed['media']['asset_id']})
        # issue #95：终态落盘，重启后 PS 端重试会得到明确的 DUPLICATE_RETURN 而不是 404。
        _app_main._photoshop_persist_table_locked(str(current.get('project_id') or ''))
        return _photoshop_public_request(current)

def _photoshop_reset_processing(request_id: str) -> None:
    with PHOTOSHOP_ROUND_TRIP_LOCK:
        current = PHOTOSHOP_ROUND_TRIP_REQUESTS.get(str(request_id or ''))
        if current and current.get('state') == 'processing':
            current['state'] = 'pending'

def _photoshop_validate_display_name(value: str) -> str:
    name = str(value or '').strip()
    if not name or name in {'.', '..'} or any((char in name for char in ('/', '\\', '\x00', ':'))):
        raise _photoshop_protocol_error(400, 'INVALID_RETURN_PATH', '返回文件名必须是纯显示名称')
    if Path(name).name != name:
        raise _photoshop_protocol_error(400, 'INVALID_RETURN_PATH', '返回文件名不能包含路径')
    return name[:240]

def _photoshop_validate_media_type(value: str) -> str:
    mime_type = str(value or '').split(';', 1)[0].strip().lower()
    if not re.fullmatch('image/[a-z0-9.+-]+', mime_type):
        raise _photoshop_protocol_error(400, 'INVALID_IDENTIFIER', 'Photoshop 返回必须是图片媒体')
    return mime_type

@router.post('/api/project-library/{project_id}/photoshop/round-trips')
@router.post('/api/project-library/{project_id}/photoshop-round-trips')
async def create_photoshop_round_trip(project_id: str, payload: PhotoshopRoundTripCreateRequest, request: Request):
    _ensure_photoshop_loopback(request)
    try:
        opened = await asyncio.to_thread(_app_main.project_store_call, 'open_project', project_id)
        source = conversation_node_by_id(opened['canvas'], payload.source_node_id)
        if not source:
            raise _photoshop_protocol_error(404, 'SOURCE_NODE_NOT_FOUND', '来源节点不存在')
        source_assets = {str(image.get('asset_id') or '') for image in source.get('images') or [] if isinstance(image, dict) and image.get('asset_id')}
        if payload.source_media_id not in source_assets:
            raise _photoshop_protocol_error(409, 'SOURCE_NODE_MISMATCH', '来源媒体不属于来源节点')
        media = await asyncio.to_thread(_app_main.project_store_call, 'media_record', project_id, payload.source_media_id)
        if media.get('kind') != 'image':
            raise _photoshop_protocol_error(400, 'INVALID_IDENTIFIER', 'Photoshop 往返来源必须是图片')
        created_at_ms = _app_main.now_ms()
        request_id = uuid.uuid4().hex
        record = {'request_id': request_id, 'project_id': str(project_id), 'source_node_id': payload.source_node_id, 'source_media_id': payload.source_media_id, 'created_at': created_at_ms // 1000, 'expires_at': (created_at_ms + payload.ttl_seconds * 1000) // 1000, 'expires_at_ms': created_at_ms + payload.ttl_seconds * 1000, 'state': 'pending'}
        with project_owner_admission(project_id):
            with PHOTOSHOP_ROUND_TRIP_LOCK:
                _photoshop_prune_requests_locked(created_at_ms)
                PHOTOSHOP_ROUND_TRIP_REQUESTS[request_id] = copy.deepcopy(record)
                # issue #95：新建请求立即落盘，服务重启后 PS 端仍可继续取回/返回。
                _app_main._photoshop_persist_table_locked(project_id)
        await manager.broadcast_photoshop_round_trip(_photoshop_public_request(record))
        return _photoshop_public_request(record)
    except HTTPException:
        raise
    except ProjectCleanupActive as exc:
        raise _photoshop_protocol_error(409, 'PROJECT_CLEANUP_ACTIVE', str(exc)) from exc
    except ProjectNotFound as exc:
        raise _photoshop_protocol_error(404, 'PROJECT_NOT_FOUND', str(exc)) from exc
    except ProjectStoreError as exc:
        raise _photoshop_protocol_error(400, 'INVALID_IDENTIFIER', str(exc)) from exc

@router.get('/api/project-library/{project_id}/photoshop/round-trips/{request_id}')
@router.get('/api/project-library/{project_id}/photoshop-round-trips/{request_id}')
async def get_photoshop_round_trip(project_id: str, request_id: str, request: Request):
    _ensure_photoshop_loopback(request)
    record = _photoshop_request_snapshot(request_id)
    if record is None:
        raise _photoshop_protocol_error(404, 'REQUEST_NOT_FOUND', 'Photoshop 往返请求不存在')
    if str(record.get('project_id')) != str(project_id):
        raise _photoshop_protocol_error(409, 'PROJECT_MISMATCH', 'Photoshop 往返项目不匹配')
    return record

@router.get('/api/project-library/{project_id}/photoshop/round-trips/{request_id}/source')
@router.get('/api/project-library/{project_id}/photoshop-round-trips/{request_id}/source')
async def get_photoshop_round_trip_source(project_id: str, request_id: str, request: Request):
    _ensure_photoshop_loopback(request)
    record = _photoshop_request_snapshot(request_id)
    if record is None:
        raise _photoshop_protocol_error(404, 'REQUEST_NOT_FOUND', 'Photoshop 往返请求不存在')
    if str(record.get('project_id')) != str(project_id):
        raise _photoshop_protocol_error(409, 'PROJECT_MISMATCH', 'Photoshop 往返项目不匹配')
    if record.get('state') == 'expired':
        raise _photoshop_protocol_error(410, 'REQUEST_EXPIRED', 'Photoshop 往返请求已过期')
    try:
        media = await asyncio.to_thread(_app_main.project_store_call, 'media_record', project_id, record['source_media_id'])
        if Path(media['path']).stat().st_size > PHOTOSHOP_ROUND_TRIP_MAX_MEDIA_BYTES:
            raise _photoshop_protocol_error(413, 'MEDIA_TOO_LARGE', 'Photoshop 往返图片超过 128MB 限制')
        content = await asyncio.to_thread(Path(media['path']).read_bytes)
        actual_sha256 = hashlib.sha256(content).hexdigest()
        if media.get('sha256') and str(media.get('sha256')).lower() != actual_sha256:
            raise _photoshop_protocol_error(409, 'SOURCE_NODE_MISMATCH', '来源媒体校验失败')
        return {**{key: record[key] for key in ('project_id', 'source_node_id', 'source_media_id', 'request_id', 'created_at', 'expires_at')}, 'name': media.get('name') or media.get('filename') or record['source_media_id'], 'mime_type': media.get('mime_type') or 'image/png', 'sha256': actual_sha256, 'bytes_base64': base64.b64encode(content).decode('ascii')}
    except (ProjectNotFound, OSError) as exc:
        raise _photoshop_protocol_error(404, 'SOURCE_NODE_NOT_FOUND', str(exc)) from exc
    except ProjectStoreError as exc:
        raise _photoshop_protocol_error(400, 'INVALID_IDENTIFIER', str(exc)) from exc

@router.post('/api/project-library/{project_id}/photoshop/round-trips/{request_id}/return')
@router.post('/api/project-library/{project_id}/photoshop-round-trips/{request_id}/return')
async def return_photoshop_round_trip(project_id: str, request_id: str, payload: PhotoshopRoundTripReturnRequest, request: Request):
    _ensure_photoshop_loopback(request)
    now = _app_main.now_ms()
    with PHOTOSHOP_ROUND_TRIP_LOCK:
        _photoshop_prune_requests_locked(now)
        record = PHOTOSHOP_ROUND_TRIP_REQUESTS.get(str(request_id or ''))
        if not record:
            raise _photoshop_protocol_error(404, 'REQUEST_NOT_FOUND', 'Photoshop 往返请求不存在')
        if str(record.get('project_id')) != str(project_id) or payload.project_id != str(project_id):
            raise _photoshop_protocol_error(409, 'PROJECT_MISMATCH', 'Photoshop 往返项目不匹配')
        if payload.request_id != request_id:
            raise _photoshop_protocol_error(409, 'REQUEST_CONFLICT', '返回请求标识不匹配')
        if payload.source_node_id != str(record.get('source_node_id')):
            raise _photoshop_protocol_error(409, 'SOURCE_NODE_MISMATCH', 'Photoshop 往返来源节点不匹配')
        if record.get('state') == 'returned' or record.get('state') == 'processing':
            raise _photoshop_protocol_error(409, 'DUPLICATE_RETURN', 'Photoshop 往返请求已经消费')
        if now >= int(record.get('expires_at_ms') or 0):
            record['state'] = 'expired'
            _app_main._photoshop_persist_table_locked(str(record.get('project_id') or ''))
            raise _photoshop_protocol_error(410, 'REQUEST_EXPIRED', 'Photoshop 往返请求已过期')
    name = _photoshop_validate_display_name(payload.name)
    mime_type = _photoshop_validate_media_type(payload.mime_type)
    expected_suffix = mimetypes.guess_extension(mime_type) or ''
    if expected_suffix and Path(name).suffix.lower() != expected_suffix.lower():
        name = f'{Path(name).stem}{expected_suffix}'
    with PHOTOSHOP_ROUND_TRIP_LOCK:
        record = PHOTOSHOP_ROUND_TRIP_REQUESTS.get(str(request_id or ''))
        if not record:
            raise _photoshop_protocol_error(404, 'REQUEST_NOT_FOUND', 'Photoshop 往返请求不存在')
        if record.get('state') == 'returned' or record.get('state') == 'processing':
            raise _photoshop_protocol_error(409, 'DUPLICATE_RETURN', 'Photoshop 往返请求已经消费')
        if _app_main.now_ms() >= int(record.get('expires_at_ms') or 0):
            record['state'] = 'expired'
            _app_main._photoshop_persist_table_locked(str(record.get('project_id') or ''))
            raise _photoshop_protocol_error(410, 'REQUEST_EXPIRED', 'Photoshop 往返请求已过期')
        record['state'] = 'processing'
    encoded = str(payload.bytes_base64 or payload.data or '').strip()
    if not encoded:
        with PHOTOSHOP_ROUND_TRIP_LOCK:
            PHOTOSHOP_ROUND_TRIP_REQUESTS[request_id]['state'] = 'pending'
        raise _photoshop_protocol_error(422, 'EMPTY_MEDIA', '未提供返回媒体')
    try:
        content = base64.b64decode(encoded, validate=True)
    except (ValueError, TypeError, base64.binascii.Error) as exc:
        with PHOTOSHOP_ROUND_TRIP_LOCK:
            PHOTOSHOP_ROUND_TRIP_REQUESTS[request_id]['state'] = 'pending'
        raise _photoshop_protocol_error(422, 'EMPTY_MEDIA', '返回媒体编码无效') from exc
    if not content:
        with PHOTOSHOP_ROUND_TRIP_LOCK:
            PHOTOSHOP_ROUND_TRIP_REQUESTS[request_id]['state'] = 'pending'
        raise _photoshop_protocol_error(422, 'EMPTY_MEDIA', '返回媒体为空')
    if len(content) > PHOTOSHOP_ROUND_TRIP_MAX_MEDIA_BYTES:
        with PHOTOSHOP_ROUND_TRIP_LOCK:
            PHOTOSHOP_ROUND_TRIP_REQUESTS[request_id]['state'] = 'pending'
        raise _photoshop_protocol_error(413, 'MEDIA_TOO_LARGE', 'Photoshop 返回图片超过 128MB 限制')
    declared = str(payload.sha256 or '').lower()
    if not re.fullmatch('[0-9a-f]{64}', declared) or hashlib.sha256(content).hexdigest() != declared:
        with PHOTOSHOP_ROUND_TRIP_LOCK:
            PHOTOSHOP_ROUND_TRIP_REQUESTS[request_id]['state'] = 'pending'
        raise _photoshop_protocol_error(422, 'MEDIA_HASH_MISMATCH', '返回媒体 SHA-256 校验失败')
    with PHOTOSHOP_ROUND_TRIP_LOCK:
        current = PHOTOSHOP_ROUND_TRIP_REQUESTS.get(request_id)
        if not current or _app_main.now_ms() >= int(current.get('expires_at_ms') or 0):
            if current:
                current['state'] = 'expired'
                _app_main._photoshop_persist_table_locked(str(current.get('project_id') or ''))
            raise _photoshop_protocol_error(410, 'REQUEST_EXPIRED', 'Photoshop 往返请求已过期')
    commit_task = asyncio.create_task(asyncio.to_thread(_app_main.project_store_call, 'complete_photoshop_round_trip', project_id, str(record['source_node_id']), str(record['source_media_id']), request_id, content, name, mime_type))
    try:
        committed = await asyncio.shield(commit_task)
    except asyncio.CancelledError:
        try:
            committed = await asyncio.shield(commit_task)
        except asyncio.CancelledError:

            def reconcile_cancelled_commit(task):
                if task.cancelled():
                    _photoshop_reset_processing(request_id)
                    return
                try:
                    completed = task.result()
                    _photoshop_mark_returned(request_id, completed)
                    asyncio.create_task(manager.broadcast_canvas_updated(project_id, int(completed['canvas'].get('updated_at') or _app_main.now_ms())))
                except BaseException:
                    _photoshop_reset_processing(request_id)
            commit_task.add_done_callback(reconcile_cancelled_commit)
            raise
        except Exception:
            _photoshop_reset_processing(request_id)
            raise
        else:
            public_record = _photoshop_mark_returned(request_id, committed)
            await manager.broadcast_canvas_updated(project_id, int(committed['canvas'].get('updated_at') or _app_main.now_ms()))
            raise
    except ProjectNotFound as exc:
        _photoshop_reset_processing(request_id)
        raise _photoshop_protocol_error(404, 'SOURCE_NODE_NOT_FOUND', str(exc)) from exc
    except (ProjectStoreError, ValueError, TypeError) as exc:
        _photoshop_reset_processing(request_id)
        raise _photoshop_protocol_error(400, 'INVALID_IDENTIFIER', str(exc)) from exc
    except Exception as exc:
        _photoshop_reset_processing(request_id)
        raise _photoshop_protocol_error(500, 'ROUND_TRIP_FAILED', 'Photoshop 返回暂未写入项目') from exc
    public_record = _photoshop_mark_returned(request_id, committed)
    await manager.broadcast_canvas_updated(project_id, int(committed['canvas'].get('updated_at') or _app_main.now_ms()))
    return {**public_record, 'media': committed['media'], 'result_node': committed['result_node'], 'edge': committed['edge'], 'canvas': committed['canvas']}

def project_conversation_connected_references(project_id: str, canvas: Dict[str, Any], node_id: str) -> List[Dict[str, Any]]:
    """Resolve only media nodes directly connected into one conversation node."""
    nodes_by_id = {str(node.get('id')): node for node in canvas.get('nodes') or [] if isinstance(node, dict) and str(node.get('id') or '')}
    source_ids = []
    for edge in canvas.get('connections') or []:
        if not isinstance(edge, dict):
            continue
        target = edge.get('to') or edge.get('target') or edge.get('toNode') or edge.get('targetNodeId')
        kind = str(edge.get('kind') or 'flow')
        source = edge.get('from') or edge.get('source') or edge.get('fromNode') or edge.get('sourceNodeId')
        if str(target or '') == node_id and source and (kind in {'input', 'flow'}):
            source_ids.append(str(source))
    candidates: List[Tuple[str, Dict[str, Any]]] = []
    seen_assets = set()
    for source_id in source_ids:
        source = nodes_by_id.get(source_id)
        if not isinstance(source, dict):
            continue
        source_type = str(source.get('type') or '')
        if source_type not in {'', 'image', 'smart-image'}:
            continue
        for item in source.get('images') or []:
            if not isinstance(item, dict) or not str(item.get('asset_id') or '').strip():
                continue
            asset_id = str(item['asset_id']).strip()
            if asset_id in seen_assets:
                continue
            seen_assets.add(asset_id)
            candidates.append((asset_id, item))
    maximum_total = _app_main.PROJECT_CONVERSATION_MAX_IMAGE_REFERENCES + PROJECT_CONVERSATION_MAX_VIDEO_REFERENCES + PROJECT_CONVERSATION_MAX_AUDIO_REFERENCES
    if len(candidates) > maximum_total:
        raise HTTPException(status_code=400, detail={'code': 'conversation_media_count_exceeded', 'message': f'对话参考素材总数量超过上限 {maximum_total}'})
    hint_counts = {'image': 0, 'video': 0, 'audio': 0}
    hint_limits = {'image': _app_main.PROJECT_CONVERSATION_MAX_IMAGE_REFERENCES, 'video': PROJECT_CONVERSATION_MAX_VIDEO_REFERENCES, 'audio': PROJECT_CONVERSATION_MAX_AUDIO_REFERENCES}
    for _asset_id, item in candidates:
        kind = str(item.get('kind') or '').strip().lower()
        if kind in hint_counts:
            hint_counts[kind] += 1
            if hint_counts[kind] > hint_limits[kind]:
                raise HTTPException(status_code=400, detail={'code': 'conversation_media_count_exceeded', 'message': f'对话{kind}参考素材数量超过上限 {hint_limits[kind]}'})
    references: List[Dict[str, Any]] = []
    actual_counts = {'image': 0, 'video': 0, 'audio': 0}
    for asset_id, item in candidates:
        record = _app_main.project_store_call('media_record', project_id, asset_id)
        kind = str(record.get('kind') or item.get('kind') or '').strip().lower()
        if kind not in actual_counts:
            raise HTTPException(status_code=400, detail={'code': 'conversation_media_invalid', 'message': '直连对话参考素材类型不受支持'})
        actual_counts[kind] += 1
        if actual_counts[kind] > hint_limits[kind]:
            raise HTTPException(status_code=400, detail={'code': 'conversation_media_count_exceeded', 'message': f'对话{kind}参考素材数量超过上限 {hint_limits[kind]}'})
        references.append({'asset_id': asset_id, 'kind': kind, 'name': str(item.get('name') or record.get('name') or record.get('filename') or asset_id)[:160]})
    return references

async def persist_project_conversation_pending(project_id: str, node_id: str, user_message: Dict[str, Any], *, node_adapter: Optional[Dict[str, str]]=None, enforce_admission: bool=False) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    request_id = str(user_message.get('request_id') or '')
    for _attempt in range(8):
        try:

            def persist_once():
                with PROJECT_CONVERSATION_ADMISSION_LOCK if enforce_admission else nullcontext():
                    with project_lifecycle_lock(str(project_id)):
                        opened = _app_main.project_store_call('open_project', project_id)
                        current_canvas = opened['canvas']
                        conversation = conversation_node_by_id(current_canvas, node_id)
                        if not isinstance(conversation, dict) or conversation.get('type') != 'smart-conversation':
                            raise HTTPException(status_code=400, detail='目标节点不是对话节点')
                        existing = conversation_task_snapshot(current_canvas, node_id, request_id)
                        if existing:
                            return (current_canvas, existing)
                        if enforce_admission:
                            runner = _app_main.PROJECT_CONVERSATION_RUNNER
                            if runner is not None:
                                pending_keys = _app_main.conversation_pending_task_keys(project_id, canvas=current_canvas, include_all_projects=True)
                                admission_error = runner.admission_error(project_id, node_id, request_id, canvas=current_canvas, pending_keys=pending_keys)
                                if admission_error:
                                    raise HTTPException(status_code=429, detail=admission_error)
                        updated_node = copy.deepcopy(conversation)
                        messages = [item for item in updated_node.get('messages') or [] if isinstance(item, dict)]
                        updated_node['messages'] = (messages + [copy.deepcopy(user_message)])[-500:]
                        if node_adapter:
                            updated_node['conversation_adapter'] = copy.deepcopy(node_adapter)
                        _app_main.project_store_call('apply_mutations', project_id, [{'op': 'upsert_node', 'node': updated_node}], base_revision=int(current_canvas.get('revision') or 0))
                        _app_main.project_store_call('flush', project_id)
                        saved = _app_main.project_store_call('open_project', project_id)
                        snapshot = conversation_task_snapshot(saved['canvas'], node_id, request_id)
                        if not snapshot:
                            raise ProjectStoreError('对话任务登记后无法读取')
                        return (saved['canvas'], snapshot)
            return await asyncio.to_thread(persist_once)
        except RevisionConflict:
            continue
    raise HTTPException(status_code=409, detail='项目持续被修改，对话任务尚未登记')

def conversation_task_response(canvas: Dict[str, Any], snapshot: Dict[str, Any]) -> Dict[str, Any]:
    return {'ok': True, 'canvas': canvas, 'status': snapshot['status'], 'request_id': snapshot['request_id'], 'message': snapshot['message'], 'assistant': snapshot.get('assistant'), 'saved': True}

async def finalize_visible_project_conversation_terminal(project_id: str, node_id: str, request_id: str) -> None:
    """Make terminal API visibility include all recovery-state cleanup."""
    forget_project_conversation_task(project_id, node_id, request_id)
    forget_project_conversation_ready_result(project_id, node_id, request_id)
    await asyncio.to_thread(_app_main.forget_project_conversation_durable, project_id, node_id, request_id)

@router.post('/api/project-library/{project_id}/conversation/{node_id}/messages')
async def append_project_conversation_message(project_id: str, node_id: str, payload: ProjectConversationMessageRequest):
    text = str(payload.text or '').strip()
    if not text:
        raise HTTPException(status_code=400, detail='对话消息不能为空')
    request_id = str(payload.request_id or '').strip() or uuid.uuid4().hex
    runner = _app_main.PROJECT_CONVERSATION_RUNNER
    if runner is None or not runner.accepting:
        raise HTTPException(status_code=503, detail={'code': 'conversation_queue_unavailable', 'message': '对话任务服务尚未就绪'})
    user_message = {'id': uuid.uuid4().hex, 'role': 'user', 'status': 'pending', 'request_id': request_id, 'text': text, 'references': [], 'created_at': _app_main.now_ms()}
    try:
        opened = await asyncio.to_thread(_app_main.project_store_call, 'open_project', project_id)
        current_canvas = opened['canvas']
        conversation = conversation_node_by_id(current_canvas, node_id)
        if not isinstance(conversation, dict) or conversation.get('type') != 'smart-conversation':
            raise HTTPException(status_code=400, detail='目标节点不是对话节点')
        stored_adapter = conversation.get('conversation_adapter') if isinstance(conversation.get('conversation_adapter'), dict) else {}
        node_adapter = {'provider': str(payload.provider if payload.provider is not None else stored_adapter.get('provider') or 'comfly'), 'model': str(payload.model if payload.model is not None else stored_adapter.get('model') or ''), 'ms_model': str(payload.ms_model if payload.ms_model is not None else stored_adapter.get('ms_model') or ''), 'system_prompt': str(payload.system_prompt if payload.system_prompt is not None else stored_adapter.get('system_prompt') or '')}
        existing = conversation_task_snapshot(current_canvas, node_id, request_id)
        if existing:
            if existing['status'] == 'pending':
                schedule_project_conversation_task(project_id, node_id, request_id, recover_fifo=True)
            else:
                await finalize_visible_project_conversation_terminal(project_id, node_id, request_id)
            return conversation_task_response(current_canvas, existing)
        persisted_pending_keys = await asyncio.to_thread(_app_main.conversation_pending_task_keys, project_id, canvas=current_canvas, include_all_projects=True)
        admission_error = runner.admission_error(project_id, node_id, request_id, canvas=current_canvas, pending_keys=persisted_pending_keys)
        if admission_error:
            raise HTTPException(status_code=429, detail=admission_error)
        references = await asyncio.to_thread(project_conversation_connected_references, project_id, current_canvas, node_id)
        user_message['references'] = copy.deepcopy(references)
        saved_canvas, snapshot = await persist_project_conversation_pending(project_id, node_id, user_message, node_adapter=node_adapter, enforce_admission=True)
    except ProjectNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ProjectStoreError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if str(snapshot.get('message', {}).get('id') or '') == str(user_message.get('id') or ''):
        if not schedule_project_conversation_task(project_id, node_id, request_id, canvas=saved_canvas):
            await _app_main.merge_project_conversation_result(project_id, node_id, request_id, user_message_id=str(user_message.get('id') or ''), error=RuntimeError('对话队列已满，任务未调用服务商'))
            raise HTTPException(status_code=429, detail={'code': 'conversation_queue_full', 'message': '对话队列已满，任务未调用服务商'})
    return conversation_task_response(saved_canvas, snapshot)

@router.get('/api/project-library/{project_id}/conversation/{node_id}/messages/{request_id}')
async def get_project_conversation_message_task(project_id: str, node_id: str, request_id: str):
    clean_request_id = str(request_id or '').strip()
    try:
        opened = await asyncio.to_thread(_app_main.project_store_call, 'open_project', project_id)
        snapshot = conversation_task_snapshot(opened['canvas'], node_id, clean_request_id)
        if snapshot is None:
            raise HTTPException(status_code=404, detail='对话任务不存在')
        if snapshot['status'] == 'pending':
            schedule_project_conversation_task(project_id, node_id, clean_request_id, recover_fifo=True)
        else:
            await finalize_visible_project_conversation_terminal(project_id, node_id, clean_request_id)
        return conversation_task_response(opened['canvas'], snapshot)
    except ProjectNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ProjectStoreError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

@router.patch('/api/project-library/{project_id}/canvas')
async def mutate_firefly_canvas(project_id: str, payload: CanvasMutationBatch):
    try:
        result = await asyncio.to_thread(_app_main.project_store_call, 'apply_mutations', project_id, payload.mutations, base_revision=payload.base_revision, force_empty=bool(payload.force_empty))
    except ProjectNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except InvalidMutation as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except RevisionConflict as exc:
        raise HTTPException(status_code=409, detail={'message': str(exc), 'canvas': exc.canvas}) from exc
    await manager.broadcast_canvas_updated(project_id, int(result.get('updated_at') or _app_main.now_ms()), payload.client_id)
    deadline = time.monotonic() + 1.9
    status = await asyncio.to_thread(_app_main.project_store_call, 'save_status', project_id)
    while not status.get('saved') and time.monotonic() < deadline:
        await asyncio.sleep(0.05)
        status = await asyncio.to_thread(_app_main.project_store_call, 'save_status', project_id)
    result['saved'] = bool(status.get('saved'))
    result['saved_revision'] = int(status.get('saved_revision') or 0)
    return result

@router.post('/api/project-library/{project_id}/local-edit')
@router.post('/api/project-library/{project_id}/local-edit-tasks')
async def create_project_local_edit(project_id: str, payload: ProjectLocalEditRequest):
    """Run one local-edit operation through an injectable offline/online adapter."""
    mode = str(payload.mode or '').strip().lower()
    if mode not in {'modify', 'retain', '局部修改', '局部保留'}:
        raise HTTPException(status_code=400, detail='局部编辑模式必须是 modify 或 retain')
    mode = 'modify' if mode in {'modify', '局部修改'} else 'retain'
    started_at = _app_main.now_ms()
    try:
        opened = await asyncio.to_thread(_app_main.project_store_call, 'open_project', project_id)
        target = next((item for item in opened['canvas'].get('nodes', []) if str(item.get('id')) == payload.result_node_id), None)
        if not target or target.get('type') != 'local-edit':
            raise ProjectStoreError('局部编辑结果节点必须先存在于当前项目')
        incoming = [edge for edge in opened['canvas'].get('connections', []) if str(edge.get('to')) == payload.result_node_id and edge.get('kind', 'flow') == 'input']
        role_edges = {role: [edge for edge in incoming if edge.get('role') == role] for role in ('original', 'references', 'prompt')}
        if len(role_edges['original']) != 1:
            raise ProjectStoreError('局部编辑必须连接恰好一个原图输入')
        connected_original = next((item for item in opened['canvas'].get('nodes', []) if str(item.get('id')) == str(role_edges['original'][0].get('from'))), None)
        connected_assets = [str(image.get('asset_id')) for image in (connected_original or {}).get('images', []) if image.get('asset_id')]
        if len(connected_assets) != 1:
            raise ProjectStoreError('原图角色必须来自恰好一个图片资源')
        if payload.original_asset_id not in connected_assets:
            raise ProjectStoreError('原图资源必须来自局部编辑的原图角色连线')
        connected_reference_assets = []
        for edge in role_edges['references']:
            source = next((item for item in opened['canvas'].get('nodes', []) if str(item.get('id')) == str(edge.get('from'))), None)
            connected_reference_assets.extend((str(image.get('asset_id')) for image in (source or {}).get('images', []) if image.get('asset_id')))
        if set(payload.reference_asset_ids) - set(connected_reference_assets):
            raise ProjectStoreError('参考资源必须来自局部编辑的参考角色连线')
        prompt_sources = [next((item for item in opened['canvas'].get('nodes', []) if str(item.get('id')) == str(edge.get('from'))), None) for edge in role_edges['prompt']]
        prompt_texts = [str(item.get('text') or item.get('prompt') or '').strip() for item in prompt_sources if isinstance(item, dict)]
        prompt_texts = [text for text in prompt_texts if text]
        composed_prompt = '\n\n'.join(prompt_texts)
        if composed_prompt and payload.prompt.strip() != composed_prompt:
            raise ProjectStoreError('提示词必须按直接连接的提示词节点顺序组合，或在没有提示词连线时显式输入')
        original = await asyncio.to_thread(_app_main.project_store_call, 'media_record', project_id, payload.original_asset_id)
        if original.get('kind') != 'image':
            raise ProjectStoreError('原图必须是项目图片媒体')
        original_bytes = await asyncio.to_thread(Path(original['path']).read_bytes)
        references: List[Tuple[str, bytes]] = []
        for asset_id in payload.reference_asset_ids:
            record = await asyncio.to_thread(_app_main.project_store_call, 'media_record', project_id, asset_id)
            if record.get('kind') != 'image':
                raise ProjectStoreError('参考输入必须是项目图片媒体')
            references.append((asset_id, await asyncio.to_thread(Path(record['path']).read_bytes)))
        source_image = as_rgba(original_bytes)
        selection_asset: Dict[str, Any]
        model_input_asset: Dict[str, Any]
        persisted_selection: Any = payload.selection
        if mode == 'modify':
            raw_rect = payload.selection.get('rect') if isinstance(payload.selection, dict) and payload.selection.get('rect') is not None else payload.selection
            from local_edit import crop_rect, validate_rect
            rect = validate_rect(raw_rect, source_image.size, payload.aspect_ratio or None)
            persisted_selection = list(rect)
            model_input = encode_png(crop_rect(source_image, rect))
            model_input_asset = await asyncio.to_thread(_app_main.project_store_call, 'import_media', project_id, model_input, 'local-edit-input.png', 'image/png', {'type': 'local_edit_model_input', 'mode': mode})
            selection_asset = await asyncio.to_thread(_app_main.project_store_call, 'import_media', project_id, selection_json({'type': 'rect', 'rect': list(rect), 'aspect_ratio': payload.aspect_ratio}), 'selection.json', 'application/json', {'type': 'local_edit_selection', 'mode': mode})
        else:
            preserve_mask = rasterize_preserve_mask(payload.selection, source_image.size, 0)
            if preserve_mask.getbbox() is None:
                raise ValueError('局部保留选区不能为空')
            model_input = encode_png(source_image)
            model_input_asset = original
            selection_asset = await asyncio.to_thread(_app_main.project_store_call, 'import_media', project_id, encode_png(preserve_mask), 'preserve-mask.png', 'image/png', {'type': 'local_edit_mask', 'mode': mode})
        request_data = {'project_id': project_id, 'input_asset_id': model_input_asset['asset_id'], 'reference_asset_ids': [model_input_asset['asset_id'], *[asset_id for asset_id, _content in references]], 'mode': mode, 'image': model_input, 'references': [content for _asset_id, content in references], 'prompt': payload.prompt, 'selection': persisted_selection, 'aspect_ratio': payload.aspect_ratio, 'size': payload.size, 'resolution': payload.resolution, 'source_size': list(source_image.size)}
        if inspect.iscoroutinefunction(_app_main.local_edit_adapter):
            adapter_result = _app_main.local_edit_adapter(request_data)
            adapter_result = await adapter_result
        else:
            adapter_result = await asyncio.to_thread(_app_main.local_edit_adapter, request_data)
            if inspect.isawaitable(adapter_result):
                adapter_result = await adapter_result
        model_image = as_rgba(adapter_result)
        if mode == 'modify':
            result_image = composite_rect(source_image, model_image, rect, payload.feather_radius)
        else:
            result_image = composite_preserve(source_image, model_image, preserve_mask, payload.feather_radius)
        model_asset = await asyncio.to_thread(_app_main.project_store_call, 'import_media', project_id, encode_png(model_image), 'model-result.png', 'image/png', {'type': 'local_edit_model_result', 'mode': mode})
        output_asset = await asyncio.to_thread(_app_main.project_store_call, 'import_media', project_id, encode_png(result_image), 'local-edit-output.png', 'image/png', {'type': 'local_edit_composite', 'mode': mode})
        node = copy.deepcopy(target)
        node.update({'id': payload.result_node_id, 'type': 'local-edit', 'mode': mode, 'inputs': {'original': {'asset_id': payload.original_asset_id}, 'references': [{'asset_id': item} for item, _content in references], 'prompt': payload.prompt}, 'input_roles': {'original': {'asset_id': payload.original_asset_id}, 'references': [{'asset_id': item} for item, _content in references], 'prompt': payload.prompt}, 'original_asset_id': payload.original_asset_id, 'reference_asset_ids': [item for item, _content in references], 'model_input_asset_id': model_input_asset['asset_id'], 'model_result_asset_id': model_asset['asset_id'], 'selection_asset_id': selection_asset['asset_id'] if mode == 'modify' else '', 'mask_asset_id': selection_asset['asset_id'] if mode == 'retain' else '', 'selection': persisted_selection, 'feather_radius': payload.feather_radius, 'generation_size': payload.size, 'generation_resolution': payload.resolution, 'images': [{'asset_id': output_asset['asset_id'], 'kind': 'image', 'name': 'local-edit-output.png'}], 'output_asset_id': output_asset['asset_id'], 'outputKind': 'image'})
        completed_at = _app_main.now_ms()
        history_record = {'id': f'local-edit-{uuid.uuid4().hex}', 'type': 'local-edit', 'status': 'succeeded', 'mode': mode, 'prompt': payload.prompt, 'selection': persisted_selection, 'aspect_ratio': payload.aspect_ratio, 'feather_radius': payload.feather_radius, 'generation_size': payload.size, 'generation_resolution': payload.resolution, 'created_at': started_at, 'createdAt': started_at, 'completed_at': completed_at, 'runMs': max(0, completed_at - started_at), 'platform': 'Local Edit', 'model': mode, 'original_asset_id': payload.original_asset_id, 'reference_asset_ids': [item for item, _content in references], 'model_input_asset_id': model_input_asset['asset_id'], 'model_result_asset_id': model_asset['asset_id'], 'selection_asset_id': selection_asset['asset_id'] if mode == 'modify' else '', 'mask_asset_id': selection_asset['asset_id'] if mode == 'retain' else '', 'output_asset_id': output_asset['asset_id'], 'result_node_id': payload.result_node_id}
        existing_history = opened['canvas'].get('history') if isinstance(opened.get('canvas'), dict) else []
        history_records = [item for item in existing_history or [] if isinstance(item, dict)][-4999:] + [history_record]
        changed = await asyncio.to_thread(_app_main.project_store_call, 'apply_mutations', project_id, [{'op': 'upsert_node', 'node': node}, {'op': 'set_canvas_meta', 'history': history_records}], base_revision=payload.base_revision)
        await asyncio.to_thread(_app_main.project_store_call, 'flush', project_id)
        await manager.broadcast_canvas_updated(project_id, int(changed.get('updated_at') or _app_main.now_ms()))
        return {'ok': True, 'mode': mode, 'node': node, 'assets': {'original': payload.original_asset_id, 'model_input': model_input_asset, 'model_result': model_asset, 'selection': selection_asset, 'output': output_asset}, 'canvas': await asyncio.to_thread(_app_main.project_store_call, 'open_project', project_id)}
    except RevisionConflict as exc:
        raise HTTPException(status_code=409, detail={'message': str(exc), 'canvas': exc.canvas}) from exc
    except ProjectNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except (ProjectStoreError, ValueError, TypeError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

@router.post('/api/project-library/{project_id}/flush')
async def flush_firefly_project(project_id: str):
    try:
        return await asyncio.to_thread(_app_main.project_store_call, 'flush', project_id)
    except ProjectNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

@router.get('/api/project-library/{project_id}/save-status')
async def firefly_project_save_status(project_id: str):
    try:
        return await asyncio.to_thread(_app_main.project_store_call, 'save_status', project_id)
    except ProjectNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

@router.post('/api/project-library/{project_id}/history/delete')
async def delete_project_history(project_id: str, payload: ProjectHistoryDeleteRequest):
    try:
        canvas = await asyncio.to_thread(_app_main.project_store_call, 'delete_history_record', project_id, payload.history_id, base_revision=payload.base_revision)
    except RevisionConflict as exc:
        raise HTTPException(status_code=409, detail={'message': str(exc), 'canvas': exc.canvas}) from exc
    except ProjectNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    await asyncio.to_thread(_app_main.project_store_call, 'flush', project_id)
    await manager.broadcast_canvas_updated(project_id, int(canvas.get('updated_at') or _app_main.now_ms()))
    return {'ok': True, 'canvas': canvas}

@router.post('/api/project-library/{project_id}/history/delete-with-media')
async def delete_project_history_with_media(project_id: str, payload: ProjectHistoryDeleteRequest):
    try:
        result = await asyncio.to_thread(
            _app_main.project_store_call,
            'delete_history_record_with_media',
            project_id,
            payload.history_id,
            base_revision=payload.base_revision,
        )
    except RevisionConflict as exc:
        raise HTTPException(status_code=409, detail={'message': str(exc), 'canvas': exc.canvas}) from exc
    except ProjectNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ProjectStoreError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    canvas = result['canvas']
    await asyncio.to_thread(_app_main.project_store_call, 'flush', project_id)
    await manager.broadcast_canvas_updated(project_id, int(canvas.get('updated_at') or _app_main.now_ms()))
    return {
        'ok': True,
        'canvas': canvas,
        'deleted': result.get('deleted', []),
        'removed_files': result.get('removed_files', []),
        'reset_node_ids': result.get('reset_node_ids', []),
    }

@router.post('/api/project-library/{project_id}/original/delete')
async def delete_project_generated_original(project_id: str, payload: ProjectOriginalDeleteRequest):
    try:
        result = await asyncio.to_thread(
            _app_main.project_store_call,
            'delete_generated_original',
            project_id,
            payload.asset_id,
            base_revision=payload.base_revision,
            history_id=payload.history_id,
            output_id=payload.output_id,
            output_index=payload.output_index,
        )
    except RevisionConflict as exc:
        raise HTTPException(status_code=409, detail={'message': str(exc), 'canvas': exc.canvas}) from exc
    except InvalidMutation as exc:
        try:
            canvas_data = _app_main.project_store_call('open_project', project_id)['canvas']
        except Exception:
            canvas_data = None
        raise HTTPException(status_code=409, detail={'message': str(exc), 'canvas': canvas_data}) from exc
    except ProjectNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ProjectStoreError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    canvas = result['canvas']
    await manager.broadcast_canvas_updated(project_id, int(canvas.get('updated_at') or _app_main.now_ms()))
    return {'ok': True, 'canvas': canvas, 'deleted': result['deleted']}

@router.post('/api/project-library/{project_id}/original/restore')
async def restore_project_generated_original(project_id: str, payload: ProjectOriginalRestoreRequest):
    try:
        result = await asyncio.to_thread(
            _app_main.project_store_call,
            'restore_deleted_original',
            project_id,
            payload.asset_id,
            base_revision=payload.base_revision,
            deletion_id=payload.deletion_id,
        )
    except RevisionConflict as exc:
        raise HTTPException(status_code=409, detail={'message': str(exc), 'canvas': exc.canvas}) from exc
    except ProjectNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ProjectStoreError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    canvas = result['canvas']
    await manager.broadcast_canvas_updated(project_id, int(canvas.get('updated_at') or _app_main.now_ms()))
    return {'ok': True, 'canvas': canvas, 'restored': result['restored']}

@router.post('/api/project-library/{project_id}/original/delete-project-wide')
async def delete_project_generated_original_project_wide(project_id: str, payload: ProjectOriginalProjectWideRequest):
    try:
        result = await asyncio.to_thread(
            _app_main.project_store_call,
            'delete_generated_original_project_wide',
            project_id,
            payload.asset_id,
            base_revision=payload.base_revision,
        )
    except RevisionConflict as exc:
        raise HTTPException(status_code=409, detail={'message': str(exc), 'canvas': exc.canvas}) from exc
    except ProjectNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ProjectStoreError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    canvas = result['canvas']
    await manager.broadcast_canvas_updated(project_id, int(canvas.get('updated_at') or _app_main.now_ms()))
    return {'ok': True, 'canvas': canvas, 'deleted': result['deleted']}

@router.post('/api/project-library/{project_id}/original/restore-project-wide')
async def restore_project_generated_original_project_wide(project_id: str, payload: ProjectOriginalProjectWideRequest):
    try:
        result = await asyncio.to_thread(
            _app_main.project_store_call,
            'restore_deleted_original_project_wide',
            project_id,
            payload.asset_id,
            base_revision=payload.base_revision,
        )
    except RevisionConflict as exc:
        raise HTTPException(status_code=409, detail={'message': str(exc), 'canvas': exc.canvas}) from exc
    except ProjectNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ProjectStoreError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    canvas = result['canvas']
    await manager.broadcast_canvas_updated(project_id, int(canvas.get('updated_at') or _app_main.now_ms()))
    return {'ok': True, 'canvas': canvas, 'restored': result['restored']}

@router.get('/api/project-library/{project_id}/trash/media')
async def list_project_media_trash(project_id: str):
    try:
        items = await asyncio.to_thread(_app_main.project_store_call, 'list_deleted_media', project_id)
    except ProjectNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return {'items': items}

@router.get('/api/project-library/{project_id}/trash/media/{asset_id}/preview')
async def get_project_trash_media_preview(project_id: str, asset_id: str):
    """#86：回收区条目预览（第一张预览图，无预览回退原文件）。"""
    def build_response() -> ProjectMediaFileResponse:
        with project_lifecycle_lock(project_id):
            record = _app_main.project_store_call('trash_media_preview_path', project_id, asset_id)
            return ProjectMediaFileResponse(
                record['path'],
                project_id=project_id,
                media_type=record.get('media_type') or 'application/octet-stream',
            )

    try:
        assert_project_owner_admissible(project_id)
        return await asyncio.to_thread(build_response)
    except ProjectNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ProjectStoreError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

@router.post('/api/project-library/{project_id}/trash/media/restore')
async def restore_project_trash_media(project_id: str, payload: ProjectTrashRestoreRequest):
    """通用回收区恢复（无撤销身份的条目：只移回文件与资源库，不恢复画布引用）。"""
    try:
        result = await asyncio.to_thread(
            _app_main.project_store_call,
            'restore_trash_media',
            project_id,
            payload.asset_id,
            base_revision=payload.base_revision,
        )
    except RevisionConflict as exc:
        raise HTTPException(status_code=409, detail={'message': str(exc), 'canvas': exc.canvas}) from exc
    except ProjectNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ProjectStoreError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    canvas = result['canvas']
    await manager.broadcast_canvas_updated(project_id, int(canvas.get('updated_at') or _app_main.now_ms()))
    return {'ok': True, 'canvas': canvas, 'restored': result['restored']}

@router.delete('/api/project-library/{project_id}/trash/media')
async def purge_project_media_trash(project_id: str, asset_id: str | None=None):
    try:
        removed = await asyncio.to_thread(_app_main.project_store_call, 'purge_deleted_media', project_id, asset_id)
    except ProjectNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ProjectStoreError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return {'ok': True, 'removed': removed}
