"""Router: /api/project-library domain (auto-split from main.py)."""
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
from typing import Any, Optional
from fastapi import APIRouter, BackgroundTasks, File, Form, Header, HTTPException, Query, Request, Response, UploadFile, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse, StreamingResponse
from pydantic import BaseModel, Field
from app.core import (AIReference, Any, BackgroundTask, BaseModel, CANVAS_TASKS, CANVAS_TASK_LOCK, CanvasMutationBatch, CanvasVideoRequest, Dict, FLY_IMPORT_UPLOAD_LOCK, Field, FileResponse, FireflyProjectCreateRequest, Future, GLOBAL_LOOP, HTTPException, Image, ImageGenerationRequest, InvalidMutation, JSONResponse, LLM_MESSAGE_MAX_LENGTH, LimitedFlyMultipartParser, List, Lock, Mapping, ModelRoutingError, MultiPartException, MultiPartParser, ONLINE_IMAGE_PROMPT_MAX_LENGTH, ONLINE_IMAGE_REFERENCE_MAX, ONLINE_VIDEO_REFERENCE_MAX, Optional, PHOTOSHOP_ROUND_TRIP_LOCK, PHOTOSHOP_ROUND_TRIP_MAX_MEDIA_BYTES, PHOTOSHOP_ROUND_TRIP_MAX_REQUESTS, PHOTOSHOP_ROUND_TRIP_REQUESTS, PHOTOSHOP_ROUND_TRIP_RETENTION_MS, PROJECT_AGENT_ADMISSION_LOCK, PROJECT_AGENT_EXECUTION_LOCKS, PROJECT_AGENT_EXECUTION_LOCKS_GUARD, PROJECT_AGENT_FUTURES, PROJECT_AGENT_MAX_PER_PROJECT, PROJECT_AGENT_MAX_TASKS_PER_PLAN, PROJECT_AGENT_RUNNING_IDS, PROJECT_AGENT_TASKS, PROJECT_AGENT_TASKS_LOCK, PROJECT_CONVERSATION_ADMISSION_LOCK, PROJECT_CONVERSATION_MAX_AUDIO_REFERENCES, PROJECT_CONVERSATION_MAX_VIDEO_REFERENCES, PROJECT_CONVERSATION_TASKS, PROJECT_CONVERSATION_TASKS_LOCK, PROJECT_IMAGE_GENERATION_FUTURES, PROJECT_IMAGE_GENERATION_MAX_PENDING, PROJECT_IMAGE_GENERATION_MAX_PER_PROJECT, PROJECT_IMAGE_GENERATION_ORPHANS, PROJECT_LIBRARY_SWITCH_LOCK, PROJECT_MEDIA_ADAPTER_PROJECT, PROJECT_MEDIA_IMPORT_FUTURES, PROJECT_MEDIA_IMPORT_MAX_PENDING, PROJECT_MEDIA_IMPORT_TASKS, PROJECT_MEDIA_IMPORT_TASKS_LOCK, PROJECT_VIDEO_CANDIDATE_INCOMPATIBLE_KEY, PROJECT_VIDEO_GENERATION_FUTURES, PROJECT_VIDEO_GENERATION_ORPHANS, Path, PhotoshopRoundTripCreateRequest, PhotoshopRoundTripReturnRequest, ProjectAgentExecuteRequest, ProjectAgentPlanRequest, ProjectConversationMessageRequest, ProjectHistoryDeleteRequest, ProjectImageGenerationRequest, ProjectLocalEditRequest, ProjectMediaImportActive, ProjectMediaImportRequest, ProjectNotFound, ProjectOriginalDeleteRequest, ProjectStore, ProjectStoreError, ProjectVideoGenerationRequest, Request, RevisionConflict, StarletteUploadFile, Tuple, VIDEO_PROMPT_MAX_LENGTH, VideoGenerationRequest, _AGENT_FORBIDDEN_VALUE_TOKENS, _AGENT_IMAGE_PARAM_SCHEMA, _AGENT_PROMPT_PARAM_SCHEMA, _AGENT_TASK_INPUT_KEYS, _AGENT_VIDEO_PARAM_SCHEMA, _agent_clean_params, _agent_execution_tasks_from_plan, _agent_find_task_node, _agent_forbidden_value, _agent_memory_task_active, _agent_nodes_by_id, _agent_normalize_task, _agent_plan_from_node, _agent_plan_hash, _agent_plan_response, _agent_public_task, _agent_recovery_payload, _agent_task_canonical, _agent_task_layout, _agent_task_specs, _ensure_photoshop_loopback, _local_upload_kind_ext, _os, _photoshop_protocol_error, _photoshop_public_request, _photoshop_reset_processing, _photoshop_validate_display_name, _photoshop_validate_media_type, _sys, acquire_fly_import_slot, apply_angle_preset, as_rgba, asyncio, base64, canvas_has_pending_conversation, composite_preserve, composite_rect, content_type_for_path, conversation_node_by_id, conversation_task_response, conversation_task_snapshot, copy, encode_png, ensure_project_image_generation_executor, ensure_project_media_import_executor, ensure_same_origin_request, extract_images, forget_project_conversation_ready_result, forget_project_conversation_task, functools, hashlib, httpx, inspect, is_agnes_provider, is_apimart_provider, is_jimeng_provider, is_runninghub_provider, is_volcengine_provider, is_yuli_provider, json, mimetypes, nullcontext, os, parse_size_pair, project_agent_execution_lock, project_agent_node, project_canvas_video_payload, project_has_active_image_generation, project_has_active_media_import, project_has_active_video_generation, project_has_recoverable_conversation_sidecars, project_image_request, project_lifecycle_lock, project_media_adapter_reference, project_video_candidate_preflight_incompatible_parameters, project_video_mark_candidate_incompatible, project_video_request, public_project_media_import_task, rasterize_preserve_mask, re, require_project_media_adapter_scope, reserve_project_image_task, reserve_project_video_task, runninghub_configured_video_model_definition, runninghub_is_image_to_video, runninghub_video_incompatible_parameters, selection_json, shutil, tempfile, time, urllib, uuid, validate_generated_image, validate_generated_video, validate_project_media_source)
from app.core import _AGENT_FORBIDDEN_VALUE_TOKENS, _AGENT_IMAGE_PARAM_SCHEMA, _AGENT_PROMPT_PARAM_SCHEMA, _AGENT_TASK_INPUT_KEYS, _AGENT_VIDEO_PARAM_SCHEMA, _agent_clean_params, _agent_execution_tasks_from_plan, _agent_find_task_node, _agent_forbidden_value, _agent_memory_task_active, _agent_nodes_by_id, _agent_normalize_task, _agent_plan_from_node, _agent_plan_hash, _agent_plan_response, _agent_public_task, _agent_recovery_payload, _agent_task_canonical, _agent_task_layout, _agent_task_specs, _ensure_photoshop_loopback, _local_upload_kind_ext, _photoshop_protocol_error, _photoshop_public_request, _photoshop_reset_processing, _photoshop_validate_display_name, _photoshop_validate_media_type
_from_main_names = ['FIREFLY_PROJECT_STORE', 'FLY_MAX_PACKAGE_BYTES', 'LOCAL_UPLOAD_DIR', 'PROJECT_AGENT_MAX_PENDING', 'PROJECT_AGENT_MAX_PER_NODE', 'PROJECT_CONVERSATION_MAX_IMAGE_REFERENCES', 'PROJECT_CONVERSATION_RUNNER', 'PROJECT_MEDIA_IMPORT_MAX_PER_PROJECT', 'PROJECT_VIDEO_REFERENCE_INLINE_MAX_BYTES', '_agent_active_counts', '_agent_admit_execution_sync', '_agent_apply_mutations', '_agent_mark_recovered_uncertain', '_agent_result_node', '_agent_submit_task', '_agent_task_node', '_local_upload_item', '_photoshop_mark_returned', '_photoshop_prune_requests_locked', '_photoshop_request_snapshot', 'archive_project_generated_image', 'archive_project_generated_video', 'build_project_media_preview', 'cleanup_project_generation_orphans', 'conversation_pending_task_keys', 'copy_project_media_to_global_library', 'execute_canvas_video', 'execute_project_video_generation', 'export_project_package', 'finalize_visible_project_conversation_terminal', 'forget_project_conversation_durable', 'generate_ai_image', 'generate_project_video', 'generate_video_preview_image', 'load_api_providers', 'local_edit_adapter', 'manager', 'merge_project_conversation_result', 'now_ms', 'output_file_from_url', 'persist_project_conversation_pending', 'preflight_project_video_providers', 'project_agent_connected_inputs', 'project_conversation_connected_references', 'project_has_active_conversation', 'project_image_references', 'project_lifecycle_store_call', 'project_media_adapter_path', 'project_media_source_path', 'project_store_call', 'project_video_adapter_incompatible_parameters', 'project_video_all_references', 'project_video_candidate_execution_incompatible_parameters', 'project_video_candidate_inline_reference_kinds', 'project_video_references', 'provider_env_key_value', 'reserve_project_media_import', 'route_image_generation', 'route_video_generation', 'run_project_image_task', 'run_project_video_task', 'runninghub_api_key', 'runninghub_model_definition', 'runninghub_wallet_key_value', 'schedule_project_conversation_task', 'video_adapter_incompatible_parameters', 'video_api_root']
import app.services as _svc
for _nm in _from_main_names:
    _v = getattr(_app_main, _nm, None)
    if _v is None:
        _v = getattr(_svc, _nm, None)
    globals()[_nm] = _v

router = APIRouter()

class ProjectMediaImportActive(RuntimeError):
    pass

class LimitedFlyMultipartParser(MultiPartParser):

    def __init__(self, *args, max_file_size: int, **kwargs):
        super().__init__(*args, **kwargs)
        self.max_file_size = max_file_size
        self._current_file_size = 0

    def on_part_begin(self) -> None:
        super().on_part_begin()
        self._current_file_size = 0

    def on_part_data(self, data: bytes, start: int, end: int) -> None:
        if self._current_part.file is not None:
            self._current_file_size += end - start
            if self._current_file_size > self.max_file_size:
                raise MultiPartException('Fly 项目包超过 20GB 安全限制')
        super().on_part_data(data, start, end)

async def acquire_fly_import_slot() -> None:
    while not FLY_IMPORT_UPLOAD_LOCK.acquire(blocking=False):
        await asyncio.sleep(0.05)

def public_project_media_import_task(task: Dict[str, Any]) -> Dict[str, Any]:
    return {key: value for key, value in task.items() if key not in {'project_id', 'source_key'}}

class ProjectImageGenerationRequest(BaseModel):
    prompt: str = Field(min_length=1, max_length=ONLINE_IMAGE_PROMPT_MAX_LENGTH)
    routing_mode: str = 'auto'
    provider_id: str = ''
    model: str = ''
    size: str = '1024x1024'
    width: Optional[int] = None
    height: Optional[int] = None
    aspect_ratio: str = '1:1'
    resolution: str = '1k'
    quality: str = 'auto'
    n: int = 1
    reference_asset_ids: List[str] = Field(default_factory=list)
    media_type: str = 'image'
    result_node_id: str = Field(min_length=1, max_length=200)
    angle_preset: str = 'none'
    base_revision: int = Field(ge=0)

class ProjectVideoGenerationRequest(BaseModel):
    prompt: str = Field(min_length=1, max_length=VIDEO_PROMPT_MAX_LENGTH)
    routing_mode: str = 'auto'
    provider_id: str = ''
    model: str = ''
    size: str = '1280x720'
    width: Optional[int] = None
    height: Optional[int] = None
    aspect_ratio: str = '16:9'
    resolution: str = '720p'
    duration: int = Field(ge=1, le=600)
    quality: str = 'auto'
    n: int = 1
    seed: Optional[int] = None
    enhance_prompt: bool = False
    enable_upsample: bool = False
    watermark: bool = False
    camera_fixed: bool = False
    return_last_frame: bool = False
    generate_audio: bool = False
    image_reference_asset_ids: List[str] = Field(default_factory=list)
    video_reference_asset_ids: List[str] = Field(default_factory=list)
    audio_reference_asset_ids: List[str] = Field(default_factory=list)
    media_type: str = 'video'
    result_node_id: str = Field(min_length=1, max_length=200)
    base_revision: int = Field(ge=0)

class ProjectAgentPlanRequest(BaseModel):
    """User-authored Agent goal and explicit task specification.

    The task list is intentionally an opaque JSON object at this boundary.  A
    plan is a reviewable contract, so the server normalizes and validates every
    field before persisting it rather than accepting a free-form tool call.
    ``task_specs``/``prompt`` are accepted as compatibility aliases for early
    clients; the persisted representation always uses ``tasks`` and ``goal``.
    """
    goal: str = Field(default='', max_length=VIDEO_PROMPT_MAX_LENGTH)
    prompt: str = Field(default='', max_length=VIDEO_PROMPT_MAX_LENGTH)
    tasks: List[Dict[str, Any]] = Field(default_factory=list)
    task_specs: List[Dict[str, Any]] = Field(default_factory=list)
    request_id: str = Field(default='', max_length=240)
    base_revision: int = Field(default=0, ge=0)

class ProjectAgentExecuteRequest(BaseModel):
    plan_id: str = Field(min_length=1, max_length=240)
    execution_id: str = Field(default='', max_length=240)
    plan_hash: str = Field(default='', max_length=128)
    tasks: List[Dict[str, Any]] = Field(default_factory=list)

class FireflyProjectCreateRequest(BaseModel):
    name: str = '新项目'

class ProjectMediaImportRequest(BaseModel):
    source_url: str
    source_locator: str = ''
    name: str = ''
    mime_type: str = ''
    expected_kind: str = ''

class PhotoshopRoundTripCreateRequest(BaseModel):
    source_node_id: str = Field(min_length=1, max_length=200)
    source_media_id: str = Field(min_length=1, max_length=240)
    ttl_seconds: int = Field(default=300, ge=1, le=3600)

class PhotoshopRoundTripReturnRequest(BaseModel):
    project_id: str = Field(min_length=1, max_length=200)
    source_node_id: str = Field(min_length=1, max_length=200)
    request_id: str = Field(min_length=1, max_length=200)
    name: str = Field(default='photoshop-edit.png', min_length=1, max_length=240)
    mime_type: str = Field(default='image/png', min_length=1, max_length=128)
    sha256: str = Field(min_length=64, max_length=64)
    bytes_base64: str = Field(default='', max_length=256 * 1024 * 1024)
    data: str = Field(default='', max_length=256 * 1024 * 1024)

class CanvasMutationBatch(BaseModel):
    mutations: List[Dict[str, Any]] = Field(default_factory=list)
    client_id: str = ''
    base_revision: int = Field(ge=0)

class ProjectLocalEditRequest(BaseModel):
    """Explicit local-edit node inputs and source-pixel selection contract."""
    original_asset_id: str = Field(min_length=1, max_length=240)
    reference_asset_ids: List[str] = Field(default_factory=list, max_length=16)
    prompt: str = Field(min_length=1, max_length=ONLINE_IMAGE_PROMPT_MAX_LENGTH)
    mode: str = 'modify'
    selection: Any
    aspect_ratio: str = ''
    size: str = Field(default='1024x1024', min_length=3, max_length=32)
    resolution: str = Field(default='1k', min_length=1, max_length=16)
    feather_radius: float = Field(default=0, ge=0, le=256)
    result_node_id: str = Field(min_length=1, max_length=200)
    base_revision: int = Field(ge=0)

class ProjectConversationMessageRequest(BaseModel):
    text: str = Field(min_length=1, max_length=LLM_MESSAGE_MAX_LENGTH)
    request_id: str = Field(default='', max_length=200)
    provider: Optional[str] = Field(default=None, max_length=80)
    model: Optional[str] = Field(default=None, max_length=200)
    ms_model: Optional[str] = Field(default=None, max_length=200)
    system_prompt: Optional[str] = Field(default=None, max_length=12000)

class ProjectHistoryDeleteRequest(BaseModel):
    log_id: str
    base_revision: int = Field(ge=0)

class ProjectOriginalDeleteRequest(BaseModel):
    asset_id: str
    base_revision: int = Field(ge=0)

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
    width, height = (payload.width, payload.height)
    parsed_width, parsed_height = parse_size_pair(payload.size)
    if width is None:
        width = parsed_width or None
    if height is None:
        height = parsed_height or None
    if parsed_width and width != parsed_width or (parsed_height and height != parsed_height):
        raise HTTPException(status_code=400, detail={'code': 'inconsistent_image_dimensions', 'message': 'size 与 width/height 不一致', 'incompatible_parameters': ['size', 'width', 'height']})
    return ImageGenerationRequest(prompt=payload.prompt, size=payload.size, width=width, height=height, aspect_ratio=payload.aspect_ratio, resolution=payload.resolution, reference_asset_ids=tuple(payload.reference_asset_ids), media_type=payload.media_type, quality=payload.quality, count=payload.n)

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
    width, height = (payload.width, payload.height)
    parsed_width, parsed_height = parse_size_pair(payload.size)
    if width is None:
        width = parsed_width or None
    if height is None:
        height = parsed_height or None
    if parsed_width and width != parsed_width or (parsed_height and height != parsed_height):
        raise HTTPException(status_code=400, detail={'code': 'inconsistent_video_dimensions', 'message': 'size 与 width/height 不一致', 'incompatible_parameters': ['size', 'width', 'height']})
    return VideoGenerationRequest(prompt=payload.prompt, size=payload.size, width=width, height=height, aspect_ratio=payload.aspect_ratio, resolution=payload.resolution, duration=payload.duration, image_reference_asset_ids=tuple(payload.image_reference_asset_ids), video_reference_asset_ids=tuple(payload.video_reference_asset_ids), audio_reference_asset_ids=tuple(payload.audio_reference_asset_ids), media_type=payload.media_type, quality=payload.quality, count=payload.n, seed=payload.seed, enhance_prompt=payload.enhance_prompt, enable_upsample=payload.enable_upsample, watermark=payload.watermark, camera_fixed=payload.camera_fixed, return_last_frame=payload.return_last_frame, generate_audio=payload.generate_audio)

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

def _agent_task_specs(payload: ProjectAgentPlanRequest) -> List[Dict[str, Any]]:
    values = payload.tasks if payload.tasks else payload.task_specs
    if not values:
        values = [{'kind': 'image', 'count': 1, 'params': {}}]
    if not isinstance(values, list) or len(values) > PROJECT_AGENT_MAX_TASKS_PER_PLAN:
        raise HTTPException(status_code=400, detail={'code': 'agent_plan_task_limit', 'message': f'Agent 单个计划最多包含 {PROJECT_AGENT_MAX_TASKS_PER_PLAN} 个任务'})
    if any((not isinstance(item, dict) for item in values)):
        raise HTTPException(status_code=400, detail='Agent 计划任务必须是对象')
    return [copy.deepcopy(item) for item in values]

def _agent_normalize_task(raw: Dict[str, Any], index: int, *, goal: str, inputs: Dict[str, Any]) -> Dict[str, Any]:
    unknown_task_keys = set(raw.keys()) - _AGENT_TASK_INPUT_KEYS
    if unknown_task_keys:
        raise HTTPException(status_code=400, detail={'code': 'agent_task_field_unknown', 'message': f'Agent 不允许任务字段: {sorted((str(key) for key in unknown_task_keys))[0]}'})
    kind = str(raw.get('kind') or raw.get('media_type') or raw.get('type') or '').strip().lower()
    if kind in {'prompt-create', 'prompt_creation', 'prompt-node', 'text-prompt'}:
        kind = 'prompt'
    if kind in {'image-generation', 'online-image', 'picture'}:
        kind = 'image'
    if kind in {'video-generation', 'online-video', 'movie'}:
        kind = 'video'
    if kind not in {'prompt', 'image', 'video'}:
        raise HTTPException(status_code=400, detail={'code': 'agent_task_kind_forbidden', 'message': 'Agent 第一阶段只允许创建提示词节点或在线图片/在线视频任务'})
    raw_count = raw.get('count', raw.get('quantity', raw.get('n', 1)))
    if isinstance(raw_count, bool):
        raise HTTPException(status_code=400, detail='Agent 任务数量无效')
    try:
        count = int(raw_count or 1)
    except (TypeError, ValueError) as exc:
        raise HTTPException(status_code=400, detail='Agent 任务数量无效') from exc
    maximum = 1 if kind == 'prompt' else 8 if kind == 'image' else 4
    if count < 1 or count > maximum:
        raise HTTPException(status_code=400, detail=f'Agent {kind} 任务数量必须在 1 到 {maximum} 之间')
    params = _agent_clean_params(raw.get('params') if 'params' in raw else raw.get('parameters'), kind)
    prompt = str(raw.get('prompt') or params.get('prompt') or inputs.get('prompt') or goal or '').strip()
    if not prompt:
        raise HTTPException(status_code=400, detail='Agent 任务缺少提示词输入')
    prompt_limit = VIDEO_PROMPT_MAX_LENGTH if kind == 'video' else ONLINE_IMAGE_PROMPT_MAX_LENGTH
    if len(prompt) > prompt_limit:
        raise HTTPException(status_code=400, detail='Agent 任务提示词超过安全限制')
    params.setdefault('prompt', prompt)
    params['media_type'] = kind
    params['n'] = count
    model_type = str(raw.get('model_type') or raw.get('modelType') or raw.get('model') or params.get('model') or ('agent-prompt-node' if kind == 'prompt' else 'online-image' if kind == 'image' else 'online-video')).strip()[:160]
    if not model_type:
        model_type = 'agent-prompt-node' if kind == 'prompt' else 'online-image' if kind == 'image' else 'online-video'
    model_type_lower = model_type.lower()
    if any((token in model_type_lower for token in ('comfy', 'local', 'photoshop', '_ps', 'ps-', 'filesystem', 'runtime'))):
        raise HTTPException(status_code=400, detail={'code': 'agent_model_forbidden', 'message': 'Agent 只允许在线图片/视频模型或本地提示词节点'})
    if kind == 'prompt':
        if model_type != 'agent-prompt-node':
            raise HTTPException(status_code=400, detail={'code': 'agent_model_forbidden', 'message': '提示词任务模型类型无效'})
    elif model_type in {'agent-prompt-node', 'prompt'}:
        raise HTTPException(status_code=400, detail={'code': 'agent_model_forbidden', 'message': '媒体任务必须使用在线模型'})
    if kind == 'prompt':
        references = []
    else:
        requested_ids = raw.get('reference_asset_ids')
        if requested_ids is None:
            requested_ids = raw.get('references')
        if requested_ids is None:
            allowed_kinds = {'image'} if kind == 'image' else {'image', 'video', 'audio'}
            references = [item for item in inputs.get('media') or [] if item.get('kind') in allowed_kinds]
        else:
            if not isinstance(requested_ids, list):
                raise HTTPException(status_code=400, detail='Agent 参考素材必须是数组')
            requested_ids = [str(item.get('asset_id') if isinstance(item, dict) else item).strip() for item in requested_ids]
            connected = {str(item.get('asset_id')) for item in inputs.get('media') or []}
            if any((item not in connected for item in requested_ids)):
                raise HTTPException(status_code=400, detail={'code': 'agent_reference_not_connected', 'message': 'Agent 只能使用直接连接到当前节点的项目媒体'})
            references = [item for item in inputs.get('media') or [] if item.get('asset_id') in requested_ids]
            order = {asset_id: idx for idx, asset_id in enumerate(requested_ids)}
            references.sort(key=lambda item: order.get(str(item.get('asset_id')), 10000))
    if kind == 'image' and any((item.get('kind') != 'image' for item in references)):
        raise HTTPException(status_code=400, detail='图片任务只能使用直接连接的图片参考')
    params['reference_asset_ids'] = [str(item['asset_id']) for item in references]
    if kind == 'video':
        params.setdefault('duration', 5)
    return {'index': index, 'kind': kind, 'model_type': model_type, 'count': count, 'prompt': prompt, 'params': params, 'references': copy.deepcopy(references), 'reference_asset_ids': [str(item['asset_id']) for item in references]}

def _agent_plan_hash(plan: Mapping[str, Any]) -> str:
    value = {'goal': plan.get('goal'), 'inputs': plan.get('inputs'), 'tasks': [{**{key: copy.deepcopy(item.get(key)) for key in ('task_id', 'result_node_id', 'kind', 'model_type', 'count', 'prompt', 'params', 'references', 'reference_asset_ids')}} for item in plan.get('tasks') or [] if isinstance(item, dict)]}
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':')).encode('utf-8')).hexdigest()

def _agent_task_canonical(task: Mapping[str, Any]) -> Dict[str, Any]:
    """Return the only task fields that are user-confirmed and executable."""
    return {key: copy.deepcopy(task.get(key)) for key in ('kind', 'model_type', 'count', 'prompt', 'params', 'references', 'reference_asset_ids')}

def _agent_public_task(task: Mapping[str, Any]) -> Dict[str, Any]:
    return {key: copy.deepcopy(value) for key, value in task.items() if key not in {'adapter_request', 'raw_result'}}

def _agent_task_node(task: Mapping[str, Any], *, agent_id: str, execution_id: str, status: str='queued', x: Optional[float]=None, y: Optional[float]=None, w: int=300, h: int=236) -> Dict[str, Any]:
    task_id = str(task.get('task_id') or '')
    return {'id': task_id, 'type': 'agent-task', 'title': f"Agent 任务 {int(task.get('index') or 0) + 1}", 'agent_id': agent_id, 'task_id': task_id, 'plan_id': str(task.get('plan_id') or ''), 'execution_id': execution_id, 'kind': str(task.get('kind') or 'image'), 'model_type': str(task.get('model_type') or ''), 'count': int(task.get('count') or 1), 'prompt': str(task.get('prompt') or ''), 'params': copy.deepcopy(task.get('params') or {}), 'reference_asset_ids': copy.deepcopy(task.get('reference_asset_ids') or []), 'status': status, 'created_at': int(task.get('created_at') or _app_main.now_ms()), 'updated_at': _app_main.now_ms(), 'result_node_id': str(task.get('result_node_id') or f'{task_id}-result'), 'idempotency_key': str(task.get('idempotency_key') or task_id), 'dispatch_state': str(task.get('dispatch_state') or 'queued'), 'x': float(x if x is not None else task.get('x') or 0), 'y': float(y if y is not None else task.get('y') or 0), 'w': int(w or task.get('w') or 300), 'h': int(h or task.get('h') or 236)}

def _agent_result_node(task: Mapping[str, Any], *, agent_id: str, execution_id: str, x: Optional[float]=None, y: Optional[float]=None, w: int=300, h: int=236) -> Dict[str, Any]:
    kind = str(task.get('kind') or 'image')
    result_node_id = str(task.get('result_node_id') or f"{task.get('task_id')}-result")
    if kind == 'prompt':
        return {'id': result_node_id, 'type': 'smart-prompt', 'title': 'Agent 提示词结果', 'x': 0, 'y': 0, 'text': '', 'source_agent_task_id': str(task.get('task_id') or ''), 'outputKind': 'prompt', 'generationState': 'queued', 'agent_id': agent_id, 'agent_task_id': str(task.get('task_id') or ''), 'execution_id': execution_id, 'created_at': _app_main.now_ms(), 'x': float(x if x is not None else task.get('result_x') or 0), 'y': float(y if y is not None else task.get('result_y') or 0), 'w': int(w or task.get('result_w') or 300), 'h': int(h or task.get('result_h') or 236)}
    return {'id': result_node_id, 'type': 'smart-image', 'title': 'Agent 图片结果' if kind == 'image' else 'Agent 视频结果', 'x': 0, 'y': 0, 'images': [], 'outputKind': kind, 'pending': int(task.get('count') or 1), 'running': False, 'agent_id': agent_id, 'agent_task_id': str(task.get('task_id') or ''), 'execution_id': execution_id, 'created_at': _app_main.now_ms(), 'x': float(x if x is not None else task.get('result_x') or 0), 'y': float(y if y is not None else task.get('result_y') or 0), 'w': int(w or task.get('result_w') or 300), 'h': int(h or task.get('result_h') or 236)}

def _agent_task_layout(canvas: Mapping[str, Any], agent: Mapping[str, Any], count: int) -> List[Dict[str, float]]:
    """Place task/result pairs next to the Agent without covering canvas nodes."""
    task_w = result_w = 300
    node_h = 236
    gap = 24
    agent_x = float(agent.get('x') or 0)
    agent_y = float(agent.get('y') or 0)
    agent_w = float(agent.get('w') or 330)
    agent_h = float(agent.get('h') or 360)
    occupied = []
    for node in canvas.get('nodes') or []:
        if not isinstance(node, dict) or str(node.get('id') or '') == str(agent.get('id') or ''):
            continue
        node_type = str(node.get('type') or '')
        width = float(node.get('w') or (300 if node_type == 'agent-task' else 340 if node_type == 'smart-agent' else 226))
        height = float(node.get('h') or (236 if node_type == 'agent-task' else 360 if node_type == 'smart-agent' else 180))
        occupied.append((float(node.get('x') or 0), float(node.get('y') or 0), width, height))
    x_candidates = [agent_x, agent_x - (task_w + result_w + gap * 2), agent_x + agent_w + gap]
    layouts: List[Dict[str, float]] = []
    for index in range(max(0, int(count))):
        placed = None
        for row in range(0, 128):
            y = agent_y + agent_h + gap + row * (node_h + gap)
            for x in x_candidates:
                pair = [(x, y, task_w, node_h), (x + task_w + gap, y, result_w, node_h)]
                all_rects = [*occupied, *[(item['x'], item['y'], task_w, node_h) for item in layouts], *[(item['result_x'], item['y'], result_w, node_h) for item in layouts]]
                if all((not (candidate[0] < existing[0] + existing[2] and candidate[0] + candidate[2] > existing[0] and (candidate[1] < existing[1] + existing[3]) and (candidate[1] + candidate[3] > existing[1])) for candidate in pair for existing in all_rects)):
                    placed = {'x': x, 'y': y, 'result_x': x + task_w + gap, 'result_y': y}
                    break
            if placed:
                break
        if placed is None:
            placed = {'x': agent_x, 'y': agent_y + agent_h + gap + index * (node_h + gap), 'result_x': agent_x + task_w + gap, 'result_y': agent_y + agent_h + gap + index * (node_h + gap)}
        layouts.append(placed)
    return layouts

def _agent_memory_task_active(task_id: str) -> bool:
    """Return whether this process still owns a live task future.

    A durable ``running`` node by itself is not evidence that the provider
    call is still ours: the process may have restarted after dispatch.  Only
    a live in-memory future/running marker lets an idempotent execute retry
    safely reuse that call.
    """
    task_id = str(task_id or '')
    if not task_id:
        return False
    with PROJECT_AGENT_TASKS_LOCK:
        memory = PROJECT_AGENT_TASKS.get(task_id) or {}
        future = PROJECT_AGENT_FUTURES.get(task_id)
        return str(memory.get('status') or '') in {'queued', 'running'} and (task_id in PROJECT_AGENT_RUNNING_IDS or (future is not None and (not future.done())))

def _agent_plan_response(project_id: str, canvas: Dict[str, Any], agent: Dict[str, Any], plan: Dict[str, Any]) -> Dict[str, Any]:
    return {'ok': True, 'project_id': project_id, 'agent': copy.deepcopy(agent), 'plan': copy.deepcopy(plan), 'canvas': copy.deepcopy(canvas)}

@router.post('/api/project-library/{project_id}/agent/{node_id}/plan')
@router.post('/api/project-library/{project_id}/agent/{node_id}/plans')
async def create_project_agent_plan(project_id: str, node_id: str, payload: ProjectAgentPlanRequest, request: Request):
    ensure_same_origin_request(request)
    request_id = str(payload.request_id or '').strip()
    if not request_id:
        raise HTTPException(status_code=400, detail={'code': 'agent_request_id_required', 'message': 'Agent 计划需要稳定 request_id 以保证幂等'})
    try:
        opened = await asyncio.to_thread(_app_main.project_store_call, 'open_project', project_id)
        canvas = opened['canvas']
        agent = project_agent_node(canvas, node_id)
        current_revision = int(canvas.get('revision') or 0)
        fields_set = getattr(payload, 'model_fields_set', None)
        if fields_set is None:
            fields_set = getattr(payload, '__fields_set__', set())
        requested_revision = int(payload.base_revision or 0)
        if 'base_revision' not in fields_set:
            requested_revision = current_revision
        if requested_revision != current_revision:
            raise RevisionConflict(canvas)
        previous_plan = agent.get('agent_plan')
        if request_id:
            durable_plans = []
            if isinstance(previous_plan, dict):
                durable_plans.append(previous_plan)
            durable_plans.extend((item for item in agent.get('agent_plans') or [] if isinstance(item, dict)))
            original = next((item for item in reversed(durable_plans) if str(item.get('request_id') or '') == request_id), None)
            if original is not None:
                agent_response = copy.deepcopy(agent)
                agent_response['agent_plan'] = copy.deepcopy(original)
                return _agent_plan_response(project_id, canvas, agent_response, copy.deepcopy(original))
        inputs = await asyncio.to_thread(project_agent_connected_inputs, project_id, canvas, node_id)
        goal = str(payload.goal or payload.prompt or '').strip()[:VIDEO_PROMPT_MAX_LENGTH]
        tasks = [_agent_normalize_task(item, index, goal=goal, inputs=inputs) for index, item in enumerate(_agent_task_specs(payload))]
        if not tasks:
            raise HTTPException(status_code=400, detail='Agent 计划至少需要一个在线任务')
        now = _app_main.now_ms()
        plan_id = f'agent-plan-{uuid.uuid4().hex}'
        planned_tasks = []
        for task in tasks:
            task_id = f'agent-task-{uuid.uuid4().hex}'
            planned_tasks.append({**copy.deepcopy(task), 'task_id': task_id, 'plan_id': plan_id, 'result_node_id': f'{task_id}-result', 'status': 'planned', 'created_at': now})
        plan = {'plan_id': plan_id, 'request_id': request_id, 'status': 'awaiting_confirmation', 'goal': goal, 'inputs': copy.deepcopy(inputs), 'tasks': planned_tasks, 'created_at': now, 'updated_at': now, 'planned_revision': current_revision, 'executions': []}
        plan['plan_hash'] = _agent_plan_hash(plan)
        replacement = copy.deepcopy(agent)
        replacement['agent_plan'] = plan
        history_plans = [item for item in replacement.get('agent_plans') or [] if isinstance(item, dict)]
        history_plans.append(copy.deepcopy(plan))
        replacement['agent_plans'] = history_plans[-32:]
        result = await asyncio.to_thread(_app_main.project_store_call, 'apply_mutations', project_id, [{'op': 'upsert_node', 'node': replacement}], base_revision=current_revision)
        await asyncio.to_thread(_app_main.project_store_call, 'flush', project_id)
        saved = await asyncio.to_thread(_app_main.project_store_call, 'open_project', project_id)
        saved_agent = project_agent_node(saved['canvas'], node_id)
        return _agent_plan_response(project_id, saved['canvas'], saved_agent, saved_agent['agent_plan'])
    except RevisionConflict as exc:
        if request_id:
            try:
                reopened = await asyncio.to_thread(_app_main.project_store_call, 'open_project', project_id)
                reopened_agent = project_agent_node(reopened['canvas'], node_id)
                durable_plan = reopened_agent.get('agent_plan')
                if isinstance(durable_plan, dict) and str(durable_plan.get('request_id') or '') == request_id:
                    return _agent_plan_response(project_id, reopened['canvas'], reopened_agent, durable_plan)
            except (ProjectNotFound, ProjectStoreError, HTTPException):
                pass
        raise HTTPException(status_code=409, detail={'message': str(exc), 'canvas': exc.canvas}) from exc
    except ProjectNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except HTTPException:
        raise
    except (ProjectStoreError, InvalidMutation, ValueError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

@router.get('/api/project-library/{project_id}/agent/{node_id}/plan')
@router.get('/api/project-library/{project_id}/agent/{node_id}/plans')
async def get_project_agent_plan(project_id: str, node_id: str):
    try:
        opened = await asyncio.to_thread(_app_main.project_store_call, 'open_project', project_id)
        agent = project_agent_node(opened['canvas'], node_id)
        plan = agent.get('agent_plan')
        if not isinstance(plan, dict):
            raise HTTPException(status_code=404, detail='Agent 尚未生成计划')
        return _agent_plan_response(project_id, opened['canvas'], agent, plan)
    except ProjectNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

def _agent_execution_tasks_from_plan(plan: Mapping[str, Any], execution_id: str) -> List[Dict[str, Any]]:
    values = []
    for item in plan.get('tasks') or []:
        if not isinstance(item, dict):
            continue
        task = copy.deepcopy(item)
        task['execution_id'] = execution_id
        task['status'] = 'queued'
        task['idempotency_key'] = str(task.get('task_id') or '')
        task['dispatch_state'] = 'queued'
        values.append(task)
    return values

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

def _agent_admit_execution_sync(project_id: str, node_id: str, payload_data: Mapping[str, Any]):
    plan_id = str(payload_data.get('plan_id') or '').strip()
    execution_id = str(payload_data.get('execution_id') or '').strip()
    plan_hash = str(payload_data.get('plan_hash') or '').strip()
    payload_tasks = payload_data.get('tasks') or []
    if not execution_id:
        raise HTTPException(status_code=400, detail={'code': 'agent_execution_id_required', 'message': 'Agent 执行需要稳定 execution_id 以保证幂等'})
    lock = project_agent_execution_lock(project_id, node_id)
    with PROJECT_AGENT_ADMISSION_LOCK, lock:
        try:
            opened = _app_main.project_store_call('open_project', project_id)
            canvas = opened['canvas']
            agent = project_agent_node(canvas, node_id)
            plan = _agent_plan_from_node(agent, plan_id)
            if not plan:
                raise HTTPException(status_code=404, detail='Agent 计划不存在')
            stored_hash = str(plan.get('plan_hash') or '')
            canonical_hash = _agent_plan_hash(plan)
            if not stored_hash or stored_hash != canonical_hash:
                raise HTTPException(status_code=409, detail={'code': 'agent_plan_changed', 'message': 'Agent 计划已被修改，请重新生成并确认'})
            expected_hash = canonical_hash
            if plan_hash and plan_hash != expected_hash:
                raise HTTPException(status_code=409, detail={'code': 'agent_plan_changed', 'message': 'Agent 计划已变化，请重新确认'})
            if not plan_hash and (not str(plan.get('request_id') or '')):
                raise HTTPException(status_code=409, detail={'code': 'agent_plan_changed', 'message': '确认 Agent 计划需要 plan_hash 或 request_id'})
            current_inputs = project_agent_connected_inputs(project_id, canvas, node_id)
            if json.dumps(plan.get('inputs') or {}, ensure_ascii=False, sort_keys=True, separators=(',', ':')) != json.dumps(current_inputs, ensure_ascii=False, sort_keys=True, separators=(',', ':')):
                raise HTTPException(status_code=409, detail={'code': 'agent_plan_changed', 'message': 'Agent 直连输入已变化，请重新生成并确认'})
            validated_tasks = []
            for index, stored_task in enumerate(plan.get('tasks') or []):
                if not isinstance(stored_task, dict):
                    raise HTTPException(status_code=409, detail={'code': 'agent_plan_changed', 'message': 'Agent 计划任务格式已变化'})
                normalized = _agent_normalize_task(stored_task, index, goal=str(plan.get('goal') or ''), inputs=current_inputs)
                if _agent_task_canonical(normalized) != _agent_task_canonical(stored_task):
                    raise HTTPException(status_code=409, detail={'code': 'agent_plan_changed', 'message': 'Agent 计划任务已被修改，请重新生成并确认'})
                validated_tasks.append(normalized)
            if payload_tasks:
                comparable = []
                for item in payload_tasks:
                    if not isinstance(item, dict):
                        raise HTTPException(status_code=400, detail='确认任务必须是对象')
                    unknown_confirm_fields = set(item.keys()) - _AGENT_TASK_INPUT_KEYS
                    if unknown_confirm_fields:
                        raise HTTPException(status_code=400, detail={'code': 'agent_task_field_unknown', 'message': f'Agent 不允许确认字段: {sorted((str(key) for key in unknown_confirm_fields))[0]}'})
                    comparable.append({key: item.get(key) for key in ('task_id', 'kind', 'model_type', 'count', 'prompt', 'params', 'reference_asset_ids') if key in item})
                expected = [{key: item.get(key) for key in ('task_id', 'kind', 'model_type', 'count', 'prompt', 'params', 'reference_asset_ids') if key in item} for item in plan.get('tasks') or []]
                if comparable != expected:
                    raise HTTPException(status_code=409, detail={'code': 'agent_plan_changed', 'message': '确认内容不是已展示的 Agent 计划'})
            executions = [item for item in plan.get('executions') or [] if isinstance(item, dict)]
            existing_execution = next((item for item in executions if str(item.get('execution_id') or '') == execution_id), None)
            if existing_execution is not None:
                uncertain_task_ids: List[str] = []
                submit_tasks: List[Dict[str, Any]] = []
                for summary in existing_execution.get('tasks') or []:
                    if not isinstance(summary, dict) or str(summary.get('status') or '') not in {'queued', 'running'}:
                        continue
                    task_id = str(summary.get('task_id') or '')
                    durable_node = _agent_find_task_node(canvas, task_id)
                    if not durable_node:
                        continue
                    durable_status = str(durable_node.get('status') or '')
                    if durable_status == 'queued':
                        recovery_task = _agent_recovery_payload(durable_node, plan)
                        if recovery_task:
                            submit_tasks.append(recovery_task)
                    elif durable_status == 'running':
                        if not _agent_memory_task_active(task_id):
                            _agent_mark_recovered_uncertain(project_id, node_id, plan, durable_node)
                            uncertain_task_ids.append(task_id)
                if uncertain_task_ids:
                    raise HTTPException(status_code=409, detail={'code': 'agent_execution_uncertain', 'message': 'Agent 在线调用状态未知；为避免重复计费，请重新生成计划并确认新的 execution_id', 'task_ids': uncertain_task_ids})
                return {'kind': 'duplicate', 'ok': True, 'project_id': project_id, 'agent_id': node_id, 'plan_id': plan['plan_id'], 'execution_id': execution_id, 'status': str(existing_execution.get('status') or 'running'), 'tasks': [copy.deepcopy(item) for item in existing_execution.get('tasks') or []], 'plan': copy.deepcopy(plan), 'submit_tasks': submit_tasks}
            if str(plan.get('status') or '') not in {'awaiting_confirmation', 'confirmed', 'running'}:
                raise HTTPException(status_code=409, detail='Agent 计划已经完成，不能重复确认')
            if str(plan.get('status') or '') == 'running':
                raise HTTPException(status_code=409, detail='Agent 计划正在执行，请等待当前执行完成')
            tasks = _agent_execution_tasks_from_plan(plan, execution_id)
            global_count, project_count, node_count = _app_main._agent_active_counts(project_id, node_id)
            if global_count + len(tasks) > _app_main.PROJECT_AGENT_MAX_PENDING:
                raise HTTPException(status_code=429, detail='Agent 任务队列已满')
            if project_count + len(tasks) > PROJECT_AGENT_MAX_PER_PROJECT:
                raise HTTPException(status_code=429, detail='当前项目的 Agent 任务队列已满')
            if node_count + len(tasks) > _app_main.PROJECT_AGENT_MAX_PER_NODE:
                raise HTTPException(status_code=429, detail='当前 Agent 节点的待处理任务已达上限')
            mutations: List[Dict[str, Any]] = []
            task_w = 300
            result_w = 300
            layouts = _agent_task_layout(canvas, agent, len(tasks))
            for task_index, task in enumerate(tasks):
                task['status'] = 'queued'
                layout = layouts[task_index]
                row_y = layout['y']
                task['x'] = layout['x']
                task['y'] = row_y
                task['w'] = task_w
                task['h'] = 236
                task['result_x'] = layout['result_x']
                task['result_y'] = layout['result_y']
                task['result_w'] = result_w
                task['result_h'] = 236
                mutations.append({'op': 'upsert_node', 'node': _agent_task_node(task, agent_id=node_id, execution_id=execution_id, x=layout['x'], y=row_y, w=task_w, h=236)})
                mutations.append({'op': 'upsert_node', 'node': _agent_result_node(task, agent_id=node_id, execution_id=execution_id, x=task['result_x'], y=row_y, w=result_w, h=236)})
                if str(task.get('kind') or '') != 'prompt':
                    mutations.append({'op': 'upsert_edge', 'edge': {'id': f"{node_id}-{task['task_id']}", 'from': node_id, 'to': task['task_id'], 'kind': 'agent-task'}})
                    mutations.append({'op': 'upsert_edge', 'edge': {'id': f"{task['task_id']}-result", 'from': task['task_id'], 'to': task['result_node_id'], 'kind': 'agent-result'}})
            execution = {'execution_id': execution_id, 'status': 'running', 'created_at': _app_main.now_ms(), 'updated_at': _app_main.now_ms(), 'tasks': [{'task_id': task['task_id'], 'result_node_id': task['result_node_id'], 'kind': task['kind'], 'status': 'queued'} for task in tasks]}
            plan['status'] = 'running'
            plan['updated_at'] = _app_main.now_ms()
            plan['executions'] = [*executions, execution][-32:]
            replacement_agent = copy.deepcopy(agent)
            replacement_agent['agent_plan'] = plan
            mutations.append({'op': 'upsert_node', 'node': replacement_agent})
            _agent_apply_mutations(project_id, mutations)
            _app_main.project_store_call('flush', project_id)
        except RevisionConflict as exc:
            raise HTTPException(status_code=409, detail={'message': str(exc), 'canvas': exc.canvas}) from exc
        except ProjectNotFound as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except HTTPException:
            raise
        except (ProjectStoreError, InvalidMutation, ValueError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return {'kind': 'new', 'ok': True, 'project_id': project_id, 'agent_id': node_id, 'plan_id': plan['plan_id'], 'execution_id': execution_id, 'status': 'running', 'tasks': [{'task_id': task['task_id'], 'result_node_id': task['result_node_id'], 'kind': task['kind'], 'status': 'queued'} for task in tasks], 'plan': copy.deepcopy(plan), 'submit_tasks': copy.deepcopy(tasks)}

@router.post('/api/project-library/{project_id}/agent/{node_id}/execute')
@router.post('/api/project-library/{project_id}/agent/{node_id}/confirm')
@router.post('/api/project-library/{project_id}/agent/{node_id}/plan/execute')
async def execute_project_agent_plan(project_id: str, node_id: str, payload: ProjectAgentExecuteRequest, request: Request):
    ensure_same_origin_request(request)
    execution_id = str(payload.execution_id or '').strip()
    if not execution_id:
        raise HTTPException(status_code=400, detail={'code': 'agent_execution_id_required', 'message': 'Agent 执行需要稳定 execution_id 以保证幂等'})
    payload_data = payload.model_dump() if hasattr(payload, 'model_dump') else payload.dict()
    result = await asyncio.to_thread(_agent_admit_execution_sync, project_id, node_id, payload_data)
    submit_tasks = result.pop('submit_tasks', [])
    for task in submit_tasks:
        _agent_submit_task(task, project_id, node_id, execution_id)
    result.pop('kind', None)
    return JSONResponse(status_code=202, content=result)

@router.get('/api/project-library/{project_id}/agent/{node_id}/tasks/{task_id}')
async def get_project_agent_task(project_id: str, node_id: str, task_id: str):
    with PROJECT_AGENT_TASKS_LOCK:
        current = PROJECT_AGENT_TASKS.get(str(task_id))
        if current and str(current.get('project_id') or '') == str(project_id) and (str(current.get('agent_id') or '') == str(node_id)):
            return _agent_public_task(current)
    try:
        opened = await asyncio.to_thread(_app_main.project_store_call, 'open_project', project_id)
        project_agent_node(opened['canvas'], node_id)
        node = _agent_find_task_node(opened['canvas'], task_id)
        if not node or str(node.get('agent_id') or '') != str(node_id):
            raise HTTPException(status_code=404, detail='Agent 任务不存在')
        result = _agent_public_task(node)
        result_node_id = str(node.get('result_node_id') or '')
        result_node = next((item for item in opened['canvas'].get('nodes') or [] if str(item.get('id') or '') == result_node_id), None)
        if result_node is not None:
            result['result_node'] = copy.deepcopy(result_node)
        history_id = f'agent-generation-{task_id}'
        result['history_record'] = next((item for item in opened['canvas'].get('logs') or [] if str(item.get('id') or '') == history_id), None)
        return result
    except ProjectNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

async def generate_project_video(request_snapshot: VideoGenerationRequest, route, references: List[Dict[str, Any]], provider: Optional[Mapping[str, Any]]=None) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
    """Adapt project-owned references to the existing online video adapters."""
    legacy_payload = project_canvas_video_payload(request_snapshot, route.provider_id, route.model, references)
    requested_count = max(1, int(request_snapshot.count or 1))
    candidates = []
    raw_results = []
    for _ in range(requested_count):
        result = await execute_canvas_video(legacy_payload, provider) if provider is not None else await _app_main.canvas_video(legacy_payload)
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
            project_media_adapter_path(adapter_url, item.get('kind') or '')
        if provider is None:
            return await _app_main.generate_project_video(request_snapshot, route, references)
        return await _app_main.generate_project_video(request_snapshot, route, references, provider=provider)
    finally:
        PROJECT_MEDIA_ADAPTER_PROJECT.reset(scope_token)

async def run_project_image_task(task_id: str, project_id: str, payload: ProjectImageGenerationRequest, request_snapshot: ImageGenerationRequest, route, registered_revision: int):
    started_at = _app_main.now_ms()
    generated_items = []
    preexisting_asset_ids = set()
    with CANVAS_TASK_LOCK:
        CANVAS_TASKS[task_id]['status'] = 'running'
        CANVAS_TASKS[task_id]['updated_at'] = time.time()
    try:
        preexisting_asset_ids = {item.get('asset_id') for item in await asyncio.to_thread(_app_main.project_store_call, 'list_media', project_id) if item.get('asset_id')}
        references = await asyncio.to_thread(project_image_references, project_id, payload.reference_asset_ids)
        executed_prompt = apply_angle_preset(payload.prompt, payload.angle_preset)
        image_parameters = {'width': request_snapshot.width, 'height': request_snapshot.height, 'aspect_ratio': request_snapshot.aspect_ratio, 'resolution': request_snapshot.resolution, 'media_type': request_snapshot.media_type}
        scope_token = PROJECT_MEDIA_ADAPTER_PROJECT.set(project_id)
        try:
            for _ in range(payload.n):
                image_data, raw = await _app_main.generate_ai_image(executed_prompt, payload.size, payload.quality, route.model, references, route.provider_id, preserve_model=True, image_parameters=image_parameters)
                try:
                    candidates = extract_images(raw) if isinstance(raw, dict) else []
                except HTTPException:
                    candidates = []
                if not candidates:
                    candidates = [image_data]
                for candidate in candidates:
                    generated_items.append(await archive_project_generated_image(project_id, candidate, len(generated_items)))
        finally:
            PROJECT_MEDIA_ADAPTER_PROJECT.reset(scope_token)
        if not generated_items:
            raise ProjectStoreError('在线模型没有返回图片')
        timestamp = _app_main.now_ms()
        history_record = {'id': f'generation-{uuid.uuid4().hex}', 'type': 'online-image', 'status': 'succeeded', 'prompt': payload.prompt, 'executed_prompt': executed_prompt, 'angle_preset': payload.angle_preset, 'created_at': started_at, 'completed_at': timestamp, 'routing_mode': payload.routing_mode, 'requested_provider_id': payload.provider_id, 'requested_model': payload.model, 'actual_provider_id': route.provider_id, 'actual_provider_name': route.provider_name, 'actual_model': route.model, 'route_reason': route.reason, 'params': request_snapshot.to_dict(), 'outputs': [{'asset_id': item['asset_id'], 'kind': 'image'} for item in generated_items], 'result_node_id': payload.result_node_id}
        canvas = await asyncio.to_thread(_app_main.project_store_call, 'complete_image_generation', project_id, payload.result_node_id, generated_items, history_record)
        await asyncio.to_thread(_app_main.project_store_call, 'flush', project_id)
        base = f"/api/project-library/{urllib.parse.quote(project_id, safe='')}/media/"
        image_items = [{**item, 'url': base + urllib.parse.quote(item['asset_id'], safe='')} for item in generated_items]
        result = {'prompt': payload.prompt, 'executed_prompt': executed_prompt, 'images': [item['url'] for item in image_items], 'image_items': image_items, 'provider_id': route.provider_id, 'provider_name': route.provider_name, 'model': route.model, 'route': route.to_dict(), 'history_id': history_record['id'], 'history_recorded': True, 'history_record': history_record, 'project_id': project_id, 'project_revision': canvas['revision'], 'registered_revision': registered_revision, 'result_node_id': payload.result_node_id, 'final_node': next((item for item in canvas.get('nodes') or [] if item.get('id') == payload.result_node_id), None)}
        with CANVAS_TASK_LOCK:
            CANVAS_TASKS[task_id].update({'status': 'succeeded', 'result': result, 'error': '', 'updated_at': time.time()})
        await cleanup_project_generation_orphans(project_id)
        await manager.broadcast_canvas_updated(project_id, int(canvas.get('updated_at') or _app_main.now_ms()))
    except Exception as exc:
        detail = getattr(exc, 'detail', None) or str(exc)
        failure_time = _app_main.now_ms()
        failure_result = None
        failure_record = {'id': f'generation-{uuid.uuid4().hex}', 'type': 'online-image', 'status': 'failed', 'prompt': payload.prompt, 'executed_prompt': apply_angle_preset(payload.prompt, payload.angle_preset), 'angle_preset': payload.angle_preset, 'created_at': started_at, 'completed_at': failure_time, 'routing_mode': payload.routing_mode, 'requested_provider_id': payload.provider_id, 'requested_model': payload.model, 'actual_provider_id': route.provider_id, 'actual_provider_name': route.provider_name, 'actual_model': route.model, 'route_reason': route.reason, 'params': request_snapshot.to_dict(), 'outputs': [], 'result_node_id': payload.result_node_id, 'error': str(detail)}
        try:
            failed_canvas = await asyncio.to_thread(_app_main.project_store_call, 'fail_image_generation', project_id, payload.result_node_id, task_id, failure_record)
            await asyncio.to_thread(_app_main.project_store_call, 'flush', project_id)
            await manager.broadcast_canvas_updated(project_id, int(failed_canvas.get('updated_at') or failure_time))
            failure_result = {'history_recorded': True, 'history_record': failure_record, 'project_id': project_id, 'project_revision': failed_canvas['revision'], 'registered_revision': registered_revision, 'result_node_id': payload.result_node_id, 'final_node': next((item for item in failed_canvas.get('nodes') or [] if item.get('id') == payload.result_node_id), None)}
        except Exception:
            pass
        with CANVAS_TASK_LOCK:
            orphan_ids = PROJECT_IMAGE_GENERATION_ORPHANS.setdefault(project_id, set())
            orphan_ids.update((item.get('asset_id') for item in generated_items if item.get('asset_id') and item.get('asset_id') not in preexisting_asset_ids))
            CANVAS_TASKS[task_id].update({'status': 'failed', 'error': str(detail), 'status_code': getattr(exc, 'status_code', 500), 'result': failure_result, 'updated_at': time.time()})
        await cleanup_project_generation_orphans(project_id)

async def run_project_video_task(task_id: str, project_id: str, payload: ProjectVideoGenerationRequest, request_snapshot: VideoGenerationRequest, route, registered_revision: int, references: Optional[List[Dict[str, Any]]]=None, provider: Optional[Mapping[str, Any]]=None):
    started_at = _app_main.now_ms()
    generated_items = []
    preexisting_asset_ids = set()
    with CANVAS_TASK_LOCK:
        CANVAS_TASKS[task_id]['status'] = 'running'
        CANVAS_TASKS[task_id]['updated_at'] = time.time()
    try:
        preexisting_asset_ids = {item.get('asset_id') for item in await asyncio.to_thread(_app_main.project_store_call, 'list_media', project_id) if item.get('asset_id')}
        references = copy.deepcopy(references) if references is not None else await asyncio.to_thread(project_video_all_references, project_id, payload)
        candidates, raw = await _app_main.execute_project_video_generation(project_id, request_snapshot, route, references, provider)
        for candidate in candidates:
            generated_items.append(await archive_project_generated_video(project_id, candidate, len(generated_items)))
        if not generated_items:
            raise ProjectStoreError('在线模型没有返回视频')
        completed_at = _app_main.now_ms()
        history_record = {'id': f'generation-{uuid.uuid4().hex}', 'type': 'online-video', 'status': 'succeeded', 'prompt': payload.prompt, 'executed_prompt': payload.prompt, 'created_at': started_at, 'completed_at': completed_at, 'routing_mode': payload.routing_mode, 'requested_provider_id': payload.provider_id, 'requested_model': payload.model, 'actual_provider_id': route.provider_id, 'actual_provider_name': route.provider_name, 'actual_model': route.model, 'route_reason': route.reason, 'params': request_snapshot.to_dict(), 'references': [{'asset_id': item['asset_id'], 'kind': item['kind']} for item in references], 'outputs': [{'asset_id': item['asset_id'], 'kind': 'video'} for item in generated_items], 'result_node_id': payload.result_node_id}
        canvas = await asyncio.to_thread(_app_main.project_store_call, 'complete_media_generation', project_id, payload.result_node_id, generated_items, history_record, media_kind='video')
        await asyncio.to_thread(_app_main.project_store_call, 'flush', project_id)
        base = f"/api/project-library/{urllib.parse.quote(project_id, safe='')}/media/"
        video_items = [{**item, 'url': base + urllib.parse.quote(item['asset_id'], safe='')} for item in generated_items]
        result = {'prompt': payload.prompt, 'videos': [item['url'] for item in video_items], 'video_items': video_items, 'media_items': video_items, 'provider_id': route.provider_id, 'provider_name': route.provider_name, 'model': route.model, 'route': route.to_dict(), 'history_id': history_record['id'], 'history_recorded': True, 'history_record': history_record, 'project_id': project_id, 'project_revision': canvas['revision'], 'registered_revision': registered_revision, 'result_node_id': payload.result_node_id, 'final_node': next((item for item in canvas.get('nodes') or [] if item.get('id') == payload.result_node_id), None), 'raw': raw if isinstance(raw, dict) else {}}
        with CANVAS_TASK_LOCK:
            CANVAS_TASKS[task_id].update({'status': 'succeeded', 'result': result, 'error': '', 'updated_at': time.time()})
        await cleanup_project_generation_orphans(project_id)
        await manager.broadcast_canvas_updated(project_id, int(canvas.get('updated_at') or completed_at))
    except Exception as exc:
        detail = getattr(exc, 'detail', None) or str(exc)
        failure_time = _app_main.now_ms()
        failure_record = {'id': f'generation-{uuid.uuid4().hex}', 'type': 'online-video', 'status': 'failed', 'prompt': payload.prompt, 'executed_prompt': payload.prompt, 'created_at': started_at, 'completed_at': failure_time, 'routing_mode': payload.routing_mode, 'requested_provider_id': payload.provider_id, 'requested_model': payload.model, 'actual_provider_id': route.provider_id, 'actual_provider_name': route.provider_name, 'actual_model': route.model, 'route_reason': route.reason, 'params': request_snapshot.to_dict(), 'outputs': [], 'result_node_id': payload.result_node_id, 'error': str(detail)}
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
            CANVAS_TASKS[task_id].update({'status': 'failed', 'error': str(detail), 'status_code': getattr(exc, 'status_code', 500), 'result': failure_result, 'updated_at': time.time()})
        await cleanup_project_generation_orphans(project_id)

async def cleanup_project_generation_orphans(project_id: str):

    def cleanup():
        with project_lifecycle_lock(project_id):
            with CANVAS_TASK_LOCK:
                if any((item.get('type') in {'project-online-image', 'project-online-video'} and item.get('project_id') == project_id and (item.get('status') in {'queued', 'running'}) for item in CANVAS_TASKS.values())):
                    return
                candidates = PROJECT_IMAGE_GENERATION_ORPHANS.pop(project_id, set()) | PROJECT_VIDEO_GENERATION_ORPHANS.pop(project_id, set())
            for asset_id in candidates:
                try:
                    _app_main.project_store_call('delete_media_if_unreferenced', project_id, asset_id)
                except Exception:
                    continue
    await asyncio.to_thread(cleanup)

def reserve_project_image_task(project_id: str, task_id: str, payload: ProjectImageGenerationRequest, route):
    with project_lifecycle_lock(project_id):
        with CANVAS_TASK_LOCK:
            expiry = time.time() - 24 * 60 * 60
            for expired_id, expired_task in list(CANVAS_TASKS.items()):
                if expired_task.get('type') == 'project-online-image' and expired_task.get('status') in {'succeeded', 'failed'} and (float(expired_task.get('updated_at') or 0) < expiry):
                    CANVAS_TASKS.pop(expired_id, None)
            active_tasks = [task for task in CANVAS_TASKS.values() if task.get('type') == 'project-online-image' and task.get('status') in {'queued', 'running'}]
            project_active = [task for task in active_tasks if task.get('project_id') == project_id]
            if len(active_tasks) >= PROJECT_IMAGE_GENERATION_MAX_PENDING:
                raise HTTPException(status_code=429, detail='在线生图队列已满，请等待已有任务完成')
            if len(project_active) >= PROJECT_IMAGE_GENERATION_MAX_PER_PROJECT:
                raise HTTPException(status_code=429, detail='当前项目同时最多运行 8 个在线生图任务')
            CANVAS_TASKS[task_id] = {'id': task_id, 'type': 'project-online-image', 'project_id': project_id, 'result_node_id': payload.result_node_id, 'status': 'queued', 'created_at': time.time(), 'updated_at': time.time(), 'result': None, 'error': '', 'provider_id': route.provider_id, 'model': route.model, 'route': route.to_dict()}

@router.post('/api/project-library/{project_id}/image-generation-tasks')
async def create_project_image_task(project_id: str, payload: ProjectImageGenerationRequest, request: Request):
    ensure_same_origin_request(request)
    try:
        await asyncio.to_thread(_app_main.project_store_call, 'open_project', project_id)
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
    reserve_project_image_task(project_id, task_id, payload, route)
    try:
        registered_canvas = await asyncio.to_thread(_app_main.project_store_call, 'begin_image_generation', project_id, payload.result_node_id, {'taskId': task_id, 'kind': 'image', 'count': payload.n, 'providerId': route.provider_id, 'model': route.model, 'referenceAssetIds': list(payload.reference_asset_ids)}, base_revision=payload.base_revision)
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
    if GLOBAL_LOOP is current_loop:
        future = current_loop.create_task(run_project_image_task(task_id, project_id, payload, request_snapshot, route, registered_canvas['revision']))
    else:
        executor = ensure_project_image_generation_executor()
        future = executor.submit(asyncio.run, run_project_image_task(task_id, project_id, payload, request_snapshot, route, registered_canvas['revision']))
    with CANVAS_TASK_LOCK:
        PROJECT_IMAGE_GENERATION_FUTURES[task_id] = future

    def forget_project_image_future(done_future: Future):
        with CANVAS_TASK_LOCK:
            if PROJECT_IMAGE_GENERATION_FUTURES.get(task_id) is done_future:
                PROJECT_IMAGE_GENERATION_FUTURES.pop(task_id, None)
    future.add_done_callback(forget_project_image_future)
    return JSONResponse(status_code=202, content={'task_id': task_id, 'idempotency_key': task_id, 'status': 'queued', 'route': route.to_dict(), 'project_revision': registered_canvas['revision'], 'registered_node': next((item for item in registered_canvas.get('nodes') or [] if item.get('id') == payload.result_node_id), None)})

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
            CANVAS_TASKS[task_id] = {'id': task_id, 'type': 'project-online-video', 'kind': 'video', 'project_id': project_id, 'result_node_id': payload.result_node_id, 'status': 'queued', 'created_at': time.time(), 'updated_at': time.time(), 'result': None, 'error': '', 'provider_id': route.provider_id, 'model': route.model, 'route': route.to_dict()}

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
        registered_canvas = await asyncio.to_thread(_app_main.project_store_call, 'begin_media_generation', project_id, payload.result_node_id, {'taskId': task_id, 'kind': 'video', 'count': payload.n, 'providerId': route.provider_id, 'model': route.model, 'referenceAssetIds': list(dict.fromkeys([*payload.image_reference_asset_ids, *payload.video_reference_asset_ids, *payload.audio_reference_asset_ids]))}, base_revision=payload.base_revision, media_kind='video')
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
    if GLOBAL_LOOP is current_loop:
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

def project_canvas_video_payload(request: VideoGenerationRequest, provider_id: str, model: str, references: List[Dict[str, Any]]) -> CanvasVideoRequest:
    image_refs = [AIReference(url=item['adapter_url'], name=item.get('name') or 'reference', kind='image', mime=item.get('mime') or '') for item in references if item.get('kind') == 'image']
    video_refs = [item['adapter_url'] for item in references if item.get('kind') == 'video']
    audio_refs = [item['adapter_url'] for item in references if item.get('kind') == 'audio']
    return CanvasVideoRequest(prompt=request.prompt, provider_id=provider_id, model=model, duration=request.duration, width=request.width, height=request.height, aspect_ratio=request.aspect_ratio, resolution=request.resolution, size=request.size, quality=request.quality, media_type=request.media_type, images=image_refs, videos=video_refs, audios=audio_refs, enhance_prompt=request.enhance_prompt, enable_upsample=request.enable_upsample, watermark=request.watermark, seed=request.seed, camera_fixed=request.camera_fixed, return_last_frame=request.return_last_frame, generate_audio=request.generate_audio, multimodal=bool(video_refs or audio_refs or len(image_refs) > 1))

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
    return {'projects': await asyncio.to_thread(_app_main.project_store_call, 'list_projects')}

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
        return any((task.get('type') == 'project-online-image' and task.get('project_id') == project_id and (task.get('status') in {'queued', 'running'}) for task in CANVAS_TASKS.values()))

def project_has_active_video_generation(project_id: str) -> bool:
    with CANVAS_TASK_LOCK:
        return any((task.get('type') == 'project-online-video' and task.get('project_id') == project_id and (task.get('status') in {'queued', 'running'}) for task in CANVAS_TASKS.values()))

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

def project_has_active_agent_task(project_id: str) -> bool:
    """Block lifecycle moves while any Agent task for the project is still queued or running."""
    with PROJECT_AGENT_TASKS_LOCK:
        return any(
            str(task.get('project_id') or '') == str(project_id) and task.get('status') in {'queued', 'running'}
            for task in PROJECT_AGENT_TASKS.values()
        )

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

@router.get('/api/project-library/{project_id}/export')
async def export_firefly_project(project_id: str, request: Request):
    ensure_same_origin_request(request)
    descriptor, path = tempfile.mkstemp(prefix='firefly-export-', suffix='.fly')
    os.close(descriptor)
    try:
        exported = await asyncio.to_thread(export_project_package, project_id, path)
    except ProjectMediaImportActive as exc:
        Path(path).unlink(missing_ok=True)
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except ProjectNotFound as exc:
        Path(path).unlink(missing_ok=True)
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except (ProjectStoreError, OSError) as exc:
        Path(path).unlink(missing_ok=True)
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    filename = f"{str(exported.get('project_name') or 'firefly-project')[:60]}.fly"
    return FileResponse(path, media_type='application/zip', filename=filename, background=BackgroundTask(lambda: Path(path).unlink(missing_ok=True)))

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

@router.get('/api/project-library/{project_id}/media/{asset_id}')
async def get_project_media(project_id: str, asset_id: str):
    try:
        record = await asyncio.to_thread(_app_main.project_store_call, 'media_record', project_id, asset_id)
    except ProjectNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ProjectStoreError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return FileResponse(record['path'], media_type=record.get('mime_type') or 'application/octet-stream')

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
    for request_id, record in list(PHOTOSHOP_ROUND_TRIP_REQUESTS.items()):
        expires_at = int(record.get('expires_at_ms') or 0)
        if record.get('state') == 'pending' and now_value >= expires_at:
            record['state'] = 'expired'
        terminal_at = int(record.get('returned_at') or 0) * 1000 or expires_at
        if record.get('state') in {'returned', 'expired'} and now_value - terminal_at > PHOTOSHOP_ROUND_TRIP_RETENTION_MS:
            PHOTOSHOP_ROUND_TRIP_REQUESTS.pop(request_id, None)
    if len(PHOTOSHOP_ROUND_TRIP_REQUESTS) <= PHOTOSHOP_ROUND_TRIP_MAX_REQUESTS:
        return
    terminal = sorted(((int(record.get('returned_at') or 0) * 1000 or int(record.get('expires_at_ms') or 0), request_id) for request_id, record in PHOTOSHOP_ROUND_TRIP_REQUESTS.items() if record.get('state') in {'returned', 'expired'}))
    for _, request_id in terminal[:max(0, len(PHOTOSHOP_ROUND_TRIP_REQUESTS) - PHOTOSHOP_ROUND_TRIP_MAX_REQUESTS)]:
        PHOTOSHOP_ROUND_TRIP_REQUESTS.pop(request_id, None)

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
        with PHOTOSHOP_ROUND_TRIP_LOCK:
            _photoshop_prune_requests_locked(created_at_ms)
            PHOTOSHOP_ROUND_TRIP_REQUESTS[request_id] = copy.deepcopy(record)
        await manager.broadcast_photoshop_round_trip(_photoshop_public_request(record))
        return _photoshop_public_request(record)
    except HTTPException:
        raise
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
        result = await asyncio.to_thread(_app_main.project_store_call, 'apply_mutations', project_id, payload.mutations, base_revision=payload.base_revision)
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
        history_record = {'id': f'local-edit-{uuid.uuid4().hex}', 'type': 'local-edit', 'status': 'succeeded', 'mode': mode, 'prompt': payload.prompt, 'selection': persisted_selection, 'aspect_ratio': payload.aspect_ratio, 'feather_radius': payload.feather_radius, 'generation_size': payload.size, 'generation_resolution': payload.resolution, 'original_asset_id': payload.original_asset_id, 'reference_asset_ids': [item for item, _content in references], 'model_input_asset_id': model_input_asset['asset_id'], 'model_result_asset_id': model_asset['asset_id'], 'selection_asset_id': selection_asset['asset_id'] if mode == 'modify' else '', 'mask_asset_id': selection_asset['asset_id'] if mode == 'retain' else '', 'output_asset_id': output_asset['asset_id'], 'result_node_id': payload.result_node_id}
        existing_history = opened['canvas'].get('logs') if isinstance(opened.get('canvas'), dict) else []
        history_records = [item for item in existing_history or [] if isinstance(item, dict)][-4999:] + [history_record]
        changed = await asyncio.to_thread(_app_main.project_store_call, 'apply_mutations', project_id, [{'op': 'upsert_node', 'node': node}, {'op': 'set_canvas_meta', 'logs': history_records}], base_revision=payload.base_revision)
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
        canvas = await asyncio.to_thread(_app_main.project_store_call, 'delete_history_record', project_id, payload.log_id, base_revision=payload.base_revision)
    except RevisionConflict as exc:
        raise HTTPException(status_code=409, detail={'message': str(exc), 'canvas': exc.canvas}) from exc
    except ProjectNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    await asyncio.to_thread(_app_main.project_store_call, 'flush', project_id)
    await manager.broadcast_canvas_updated(project_id, int(canvas.get('updated_at') or _app_main.now_ms()))
    return {'ok': True, 'canvas': canvas}

@router.post('/api/project-library/{project_id}/original/delete')
async def delete_project_generated_original(project_id: str, payload: ProjectOriginalDeleteRequest):
    try:
        result = await asyncio.to_thread(_app_main.project_store_call, 'delete_generated_original', project_id, payload.asset_id, base_revision=payload.base_revision)
    except RevisionConflict as exc:
        raise HTTPException(status_code=409, detail={'message': str(exc), 'canvas': exc.canvas}) from exc
    except ProjectNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ProjectStoreError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    canvas = result['canvas']
    await manager.broadcast_canvas_updated(project_id, int(canvas.get('updated_at') or _app_main.now_ms()))
    return {'ok': True, 'canvas': canvas, 'deleted': result['deleted']}

@router.post('/api/project-library/{project_id}/original/restore')
async def restore_project_generated_original(project_id: str, payload: ProjectOriginalDeleteRequest):
    try:
        result = await asyncio.to_thread(_app_main.project_store_call, 'restore_deleted_original', project_id, payload.asset_id, base_revision=payload.base_revision)
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

@router.delete('/api/project-library/{project_id}/trash/media')
async def purge_project_media_trash(project_id: str, asset_id: str | None=None):
    try:
        removed = await asyncio.to_thread(_app_main.project_store_call, 'purge_deleted_media', project_id, asset_id)
    except ProjectNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ProjectStoreError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return {'ok': True, 'removed': removed}