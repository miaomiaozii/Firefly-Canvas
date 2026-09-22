"""Services layer machinery — imports + runtime binding."""
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
from app.core import (_photoshop_public_request, _agent_normalize_candidates, _local_upload_folder_node, _agent_nodes_by_id, _photoshop_protocol_error, _agent_task_layout, _agent_history_output, _safe_asset_tag, _agent_find_task_node, _agent_recovery_payload, _agent_plan_hash, _storage_abs_path, _agent_normalize_task, _agent_int_param, _DOUBLE_EXT_RE, _agent_plan_from_node, _AGENT_TASK_INPUT_KEYS, _local_upload_kind_ext, _DOUBLE_EXT_MEDIA, _local_upload_display_name, _agent_task_canonical, _agent_execution_tasks_from_plan, _agent_memory_task_active, _sniff_image_ext, _local_upload_rel_path)

_DYN_NAMES = ('AI_BASE_URL', 'API_ENV_FILE', 'API_PROVIDERS_FILE', 'ASSETS_DIR', 'ASSET_CLASSIFICATION_PROMPT_FILE', 'ASSET_LIBRARY_DIR', 'ASSET_LIBRARY_PATH', 'CANVAS_DIR', 'CONVERSATION_DIR', 'DATA_DIR', 'DEFAULT_STORAGE_DIRS', 'FIREFLY_PROJECT_STORE', 'FLY_MAX_PACKAGE_BYTES', 'FLY_MAX_REQUEST_BYTES', 'GLOBAL_CONFIG_FILE', 'GLOBAL_SETTINGS_FILE', 'HISTORY_FILE', 'LOCAL_UPLOAD_DIR', 'MEDIA_PREVIEW_DIR', 'OUTPUT_DIR', 'OUTPUT_INPUT_DIR', 'OUTPUT_OUTPUT_DIR', 'PROJECTS_PATH', 'PROJECT_AGENT_MAX_PENDING', 'PROJECT_AGENT_MAX_PER_NODE', 'PROJECT_AGENT_TASK_TIMEOUT_SECONDS', 'PROJECT_CONVERSATION_IMAGE_MAX_PIXELS', 'PROJECT_CONVERSATION_MAX_IMAGE_REFERENCES', 'PROJECT_CONVERSATION_MAX_PENDING', 'PROJECT_CONVERSATION_MAX_PER_NODE', 'PROJECT_CONVERSATION_MAX_PER_PROJECT', 'PROJECT_CONVERSATION_MEDIA_MAX_FILE_BYTES', 'PROJECT_CONVERSATION_MEDIA_MAX_TOTAL_BYTES', 'PROJECT_CONVERSATION_RUNNER', 'PROJECT_GENERATED_VIDEO_MAX_BYTES', 'PROJECT_MEDIA_IMPORT_MAX_PER_PROJECT', 'PROJECT_VIDEO_REFERENCE_INLINE_MAX_BYTES', 'PROMPT_LIBRARY_PATH', 'SHARED_FOLDERS_FILE', 'STORAGE_SETTINGS_FILE', '_agent_active_counts', '_agent_apply_mutations', '_agent_mark_recovered_uncertain', '_agent_set_task_node', '_agent_update_plan', '_photoshop_prune_requests_locked', 'agent_image_adapter', 'agent_video_adapter', 'api_headers', 'apply_storage_settings', 'attachment_embedded_image_data_urls', 'attachment_text_blocks', 'build_online_image_result', 'canvas_assets_index', 'canvas_llm', 'canvas_path', 'canvas_video', 'caption_image_with_provider', 'cleanup_expired_canvas_trash', 'cleanup_project_generation_orphans', 'codex_cli_executable', 'codex_output_image_files', 'codex_output_url_from_path', 'codex_prepare_local_media', 'codex_status', 'conversation_path', 'conversation_pending_task_keys', 'convert_output_to_jpg', 'delete_media_preview_cache', 'ensure_runtime_config_files', 'execute_canvas_video', 'execute_project_video_generation', 'export_project_package', 'fetch_image_task_payload', 'fetch_runninghub_model_registry', 'forget_project_conversation_durable', 'gemini_cli_executable', 'gemini_cli_status', 'generate_ai_image', 'generate_codex_provider_image_via_gpt_image_2_skill', 'generate_gemini_cli_provider_image', 'generate_jimeng_video', 'generate_project_video', 'generate_video_preview_image', 'generated_media_path_from_url', 'get_api_provider', 'get_api_provider_exact', 'get_primary_provider_id', 'gpt_image_2_skill_executable', 'image_task_url_for_provider', 'iter_canvas_records', 'jimeng_local_output_url', 'jimeng_pending_exception_handler', 'jimeng_prepare_local_media', 'jimeng_query_result', 'jimeng_status', 'jimeng_store_outputs', 'json_references_media_path', 'list_projects', 'load_api_providers', 'load_asset_library', 'load_global_preferences', 'load_projects', 'load_prompt_libraries', 'load_storage_settings', 'local_asset_public_url', 'local_edit_adapter', 'local_media_adapter_path', 'local_media_file_by_basename', 'local_media_path_for_cloud_upload', 'local_media_path_from_url', 'manager', 'media_preview_cache_paths', 'merge_project_conversation_result', 'migrate_asset_library_into_dirs', 'migrate_double_extension_uploads', 'migrate_mislabeled_image_extensions', 'now_ms', 'output_file_from_url', 'output_storage', 'output_url_for', 'persist_project_conversation_pending', 'persisted_json_references_media_path', 'photoshop_connection_status', 'preflight_project_video_providers', 'project_agent_default_image_adapter', 'project_conversation_adapter', 'project_conversation_durable_path', 'project_conversation_media_budget', 'project_has_active_conversation', 'project_library_status', 'project_media_adapter_path', 'project_media_source_path', 'project_store_call', 'project_video_candidate_execution_incompatible_parameters', 'project_video_references', 'protect_project_library_origin', 'provider_endpoint_url', 'provider_env_key_value', 'public_base_url', 'reference_to_data_url', 'request_validation_exception_handler', 'reserve_project_media_import', 'resolve_chat_provider', 'route_image_generation', 'route_video_generation', 'run_canvas_image_task', 'run_codex_cli', 'run_project_agent_task', 'run_project_conversation_task', 'run_project_image_task', 'run_project_video_task', 'runninghub_local_asset_path', 'runninghub_model_definition', 'runninghub_models_payload', 'runninghub_upload_local_to_filename', 'runninghub_wallet_key_value', 'save_api_providers', 'save_asset_classification_prompt', 'save_asset_library', 'save_canvas', 'save_conversation', 'save_global_preferences', 'save_projects', 'save_prompt_libraries', 'save_remote_video_to_output', 'save_storage_settings', 'save_to_history', 'schedule_project_conversation_task', 'shared_folders_save', 'shutdown_event', 'smart_group_export_folder', 'startup_event', 'storage_kind_dir', 'switch_project_library', 'sync_static_html_versions', 'update_env_values', 'upload_audio_for_apimart', 'upload_image_for_apimart', 'upload_video_for_apimart', 'user_dir', 'video_adapter_incompatible_parameters', 'video_api_root', 'video_reference_to_frame_data_urls')

_main_bindings = {}


def _bind_main(mod):
    global _main_bound_module
    _main_bound_module = mod
    for _n in _DYN_NAMES:
        if _n not in globals():
            globals()[_n] = getattr(mod, _n, None)
    _main_bindings.clear()
    _main_bindings.update({k: v for k, v in vars(mod).items()})


_main_bound_module = None


def _main(name):
    if _main_bound_module is not None and hasattr(_main_bound_module, name):
        return getattr(_main_bound_module, name)
    _m = _sys.modules.get(_os.environ.get('FIREFLY_MAIN_MODULE', 'main')) or _sys.modules.get('main')
    if _m is not None and hasattr(_m, name):
        return getattr(_m, name)
    if name in _main_bindings:
        return _main_bindings[name]
    _pkg = _sys.modules.get('app.services')
    if _pkg is not None:
        return getattr(_pkg, name, None)
    return globals().get(name)
