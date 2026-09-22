"""app.core - pure shared layer extracted from main.py (auto-split)."""
from __future__ import annotations
import sys as _sys
import os as _os

def _app_main():
    _m = _sys.modules.get(_os.environ.get('FIREFLY_MAIN_MODULE', '')) or _sys.modules.get('main') or _sys.modules.get('__main__')
    if _m is None:
        import main as _m
    return _m
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

logger = logging.getLogger(__name__)
from typing import List, Dict, Any, Mapping, Optional, Tuple
from threading import Lock, RLock, Thread
from app.project_activity import (
    PROJECT_LIFECYCLE_LOCKS,
    PROJECT_LIFECYCLE_LOCKS_GUARD,
    project_lifecycle_lock,
)
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
QUIET_ACCESS_PATHS = {'/api/queue_status', '/api/canvases', '/api/canvases/trash'}
QUIET_ACCESS_PREFIXES = ('/api/canvases/',)
PROJECT_MEDIA_IMPORT_TASKS: Dict[str, Dict[str, Any]] = {}
PROJECT_MEDIA_IMPORT_TASKS_LOCK = Lock()
PROJECT_MEDIA_IMPORT_EXECUTOR: Optional[ThreadPoolExecutor] = None
PROJECT_MEDIA_IMPORT_FUTURES: Dict[str, Future] = {}
PROJECT_MEDIA_IMPORT_ACCEPTING = True
PROJECT_MEDIA_IMPORT_SHUTTING_DOWN = False
PROJECT_MEDIA_IMPORT_MAX_PENDING = 32
PROJECT_IMAGE_GENERATION_EXECUTOR: Optional[ThreadPoolExecutor] = None
PROJECT_IMAGE_GENERATION_FUTURES: Dict[str, Future] = {}
PROJECT_IMAGE_GENERATION_ORPHANS: Dict[str, set[str]] = {}
PROJECT_VIDEO_GENERATION_FUTURES: Dict[str, Future] = {}
PROJECT_VIDEO_GENERATION_ORPHANS: Dict[str, set[str]] = {}
PROJECT_CONVERSATION_TASKS: Dict[Tuple[str, str, str], asyncio.Future] = {}
PROJECT_CONVERSATION_READY_RESULTS: Dict[Tuple[str, str, str], Dict[str, Any]] = {}
PROJECT_CONVERSATION_TASKS_LOCK = Lock()
PROJECT_CONVERSATION_ADMISSION_LOCK = Lock()
PROJECT_CONVERSATION_MAX_VIDEO_REFERENCES = 3
PROJECT_CONVERSATION_MAX_AUDIO_REFERENCES = 3
PROJECT_IMAGE_GENERATION_MAX_PENDING = 32
PROJECT_IMAGE_GENERATION_MAX_PER_PROJECT = 8


def image_generation_task_timeout_seconds() -> float:
    """图片生成任务的硬超时（秒）。默认 1800s，env FIREFLY_IMAGE_GENERATION_TASK_TIMEOUT_SECONDS 可调。

    超时后任务必须到达 timeout 终态——provider 挂起时不能让任务无限 running，否则
    前端永远收不到终态事件、节点无限停留在「生成中」。
    """
    raw = os.environ.get('FIREFLY_IMAGE_GENERATION_TASK_TIMEOUT_SECONDS', '1800') or '1800'
    try:
        return max(0.1, float(raw))
    except (TypeError, ValueError):
        return 1800.0


def video_generation_task_timeout_seconds() -> float:
    """视频生成任务的硬超时（秒）。默认 3600s，env FIREFLY_VIDEO_GENERATION_TASK_TIMEOUT_SECONDS 可调。

    与图片同一约束：provider 挂起时任务必须到 timeout 终态，不能让节点无限「生成中」。
    视频渲染通常比图片慢（几十秒到十几分钟），默认给到 1 小时；
    真正的 provider 侧轮询上限另由 VIDEO_POLL_TIMEOUT（默认 1800s）控制。
    """
    raw = os.environ.get('FIREFLY_VIDEO_GENERATION_TASK_TIMEOUT_SECONDS', '3600') or '3600'
    try:
        return max(0.1, float(raw))
    except (TypeError, ValueError):
        return 3600.0
PROJECT_AGENT_TASKS: Dict[str, Dict[str, Any]] = {}
PROJECT_AGENT_TASKS_LOCK = RLock()
PROJECT_AGENT_FUTURES: Dict[str, Future] = {}
PROJECT_AGENT_RUNNING_IDS: set[str] = set()
PROJECT_AGENT_RECOVERY_LOCK = RLock()
PROJECT_AGENT_EXECUTION_LOCKS: Dict[Tuple[str, str], Lock] = {}
PROJECT_AGENT_EXECUTION_LOCKS_GUARD = Lock()
PROJECT_AGENT_ADMISSION_LOCK = Lock()
PROJECT_AGENT_MAX_PER_PROJECT = 8
PROJECT_AGENT_MAX_TASKS_PER_PLAN = 8
FLY_IMPORT_UPLOAD_LOCK = Lock()
PHOTOSHOP_ROUND_TRIP_REQUESTS: Dict[str, Dict[str, Any]] = {}
PHOTOSHOP_ROUND_TRIP_LOCK = RLock()
PHOTOSHOP_ROUND_TRIP_MAX_MEDIA_BYTES = 128 * 1024 * 1024
PHOTOSHOP_ROUND_TRIP_RETENTION_MS = 24 * 60 * 60 * 1000
PHOTOSHOP_ROUND_TRIP_MAX_REQUESTS = 4096
GLOBAL_LOOP = None
APP_VERSION = '2026.06.03'
from app.constants import *

QUEUE = []
QUEUE_LOCK = Lock()
HISTORY_LOCK = Lock()
GLOBAL_CONFIG_LOCK = Lock()
API_ENV_LOCK = RLock()
PROJECT_LIBRARY_SWITCH_LOCK = Lock()
PROJECT_LIBRARY_MIGRATION_ACTIVE = False
CONVERSATION_LOCK = Lock()
CANVAS_LOCK = RLock()
LOAD_LOCK = Lock()
NEXT_TASK_ID = 1
UPDATE_LOCK = Lock()
JIMENG_LOGIN_SESSION = {'proc': None, 'stdout': '', 'stderr': '', 'started_at': 0.0}
PROVIDER_ID_RE = re.compile('^[a-zA-Z0-9_-]{2,40}$')
SUPPORTED_PROVIDER_PROTOCOLS = {'openai', 'apimart', 'gemini', 'gemini-cli', 'volcengine', 'runninghub', 'jimeng', 'codex'}
REMOVED_PROVIDER_IDS = {'modelscope', 'runninghub'}
SUPPORTED_IMAGE_REQUEST_MODES = {'openai', 'openai-json', 'openai-video-proxy', 'openai-responses'}
RUNNINGHUB_DEFAULT_BASE_URL = 'https://www.runninghub.cn'
RUNNINGHUB_OPENAPI_BASE_URL = 'https://www.runninghub.cn/openapi/v2'
RUNNINGHUB_LLM_BASE_URL = 'https://llm.runninghub.cn/v1'
RUNNINGHUB_FILE_HOST_REWRITES = {'rh-images-1252422369.cos.ap-beijing.myqcloud.com': 'rh-images.xiaoyaoyou.com'}
LINGJING_DEFAULT_BASE_URL = 'https://apistudio.vip'
RUNNINGHUB_LLM_MODELS_URLS = ['https://llm.runninghub.cn/v1/models', 'https://llm.runninghub.ai/v1/models']
RUNNINGHUB_FALLBACK_CHAT_MODELS = ['google/gemini-3.1-flash-lite-preview', 'qwen/qwen3-vl-235b-a22b-instruct', 'qwen/qwen-plus', 'openai/gpt-5.1']
JIMENG_DEFAULT_IMAGE_MODELS = ['5.0', '4.6', '4.5', '4.1', '4.0', '3.1', '3.0']
JIMENG_DEFAULT_VIDEO_MODELS = ['seedance2.0_vip', 'seedance2.0fast_vip', 'seedance2.0', 'seedance2.0fast', '3.5pro', '3.0pro', '3.0', '3.0fast']
CODEX_DEFAULT_IMAGE_MODELS = ['gpt-image-2']
CODEX_DEFAULT_CHAT_MODELS = ['gpt-5.4']
GEMINI_CLI_DEFAULT_IMAGE_MODELS = ['auto']
GEMINI_CLI_DEFAULT_CHAT_MODELS = ['auto']
try:
    CODEX_DEFAULT_TIMEOUT = max(30, min(3600, int(os.getenv('CODEX_CLI_TIMEOUT', '900'))))
except Exception:
    CODEX_DEFAULT_TIMEOUT = 900
try:
    GEMINI_CLI_DEFAULT_TIMEOUT = max(30, min(3600, int(os.getenv('GEMINI_CLI_TIMEOUT', '900'))))
except Exception:
    GEMINI_CLI_DEFAULT_TIMEOUT = 900
AGNES_DEFAULT_VIDEO_MODELS = ['agnes-video-v2.0']
JIMENG_LEGACY_IMAGE_MODELS = {'jimeng-image-2k', 'jimeng-image-4k'}
JIMENG_LEGACY_VIDEO_MODELS = {'jimeng-video-720p', 'jimeng-video-1080p'}
try:
    JIMENG_DEFAULT_POLL_SECONDS = max(1, min(3600, int(os.getenv('JIMENG_POLL_SECONDS', '900'))))
except Exception:
    JIMENG_DEFAULT_POLL_SECONDS = 900
VOLCENGINE_DEFAULT_BASE_URL = 'https://ark.cn-beijing.volces.com/api/v3'
VOLCENGINE_DEFAULT_PROJECT_NAME = 'default'
VOLCENGINE_DEFAULT_REGION = 'cn-beijing'
RUNNINGHUB_DEFAULT_IMAGE_MODELS = ['gpt-image-2.0/text-to-image-channel-low-price', 'gpt-image-2.0/edit-channel-low-price', 'gpt-image-2/text-to-image-official-stable', 'gpt-image-2/image-to-image-official-stable', 'nano-banana/text-to-image-official-stable', 'nano-banana/edit-official-stable']
RUNNINGHUB_DEFAULT_VIDEO_MODELS = ['google/veo3.1-fast/text-to-video-channel-low-price', 'sora-2/text-to-video-official-stable', 'seedance-2.0-global/text-to-video', 'seedance-2.0-global/image-to-video']
RUNNINGHUB_MODEL_ENDPOINT_ALIASES = {'Seedance2.0 Image to Video': 'bytedance/seedance-2.0-global/image-to-video', 'Seedance2.0 Text to Video': 'bytedance/seedance-2.0-global/text-to-video', 'seedance2.0 image to video': 'bytedance/seedance-2.0-global/image-to-video', 'seedance2.0 text to video': 'bytedance/seedance-2.0-global/text-to-video', 'gpt-image-2.0/text-to-image-channel-low-price': 'rhart-image-g-2/text-to-image', 'gpt-image-2/text-to-image-channel-low-price': 'rhart-image-g-2/text-to-image', 'gpt-image-2.0/edit-channel-low-price': 'rhart-image-g-2/image-to-image', 'gpt-image-2/edit-channel-low-price': 'rhart-image-g-2/image-to-image', 'gpt-image-2.0/image-to-image-channel-low-price': 'rhart-image-g-2/image-to-image', 'gpt-image-2/image-to-image-channel-low-price': 'rhart-image-g-2/image-to-image', 'nano-banana/text-to-image-channel-low-price': 'rhart-image-v1/text-to-image', 'nano-banana/edit-channel-low-price': 'rhart-image-v1/edit'}
RUNNINGHUB_DEFAULT_APPS = [{'id': '2058517022748798977', 'appId': '2058517022748798977', 'title': '2511-风格迁移', 'note': '', 'thumbnail': '', 'enabled': True, 'fields': [{'id': '100::image', 'nodeId': '100', 'fieldName': 'image', 'fieldValue': 'pasted/57ef7dc980b6446bca366caaf3f94eb12b22b23f78aa30e294b39cabd7d0187b.png', 'fieldType': 'IMAGE', 'label': 'image', 'enabled': True, 'sourceFromUpstream': True, 'group': 'AI 应用参数', 'note': 'image', 'options': [], 'random_enabled': False, 'min': '', 'max': '', 'step': '', 'imageOrder': 0, 'required': False}, {'id': '112::image', 'nodeId': '112', 'fieldName': 'image', 'fieldValue': '8cff63ee4b3e0285ca85ab90a52e26746df84ed0dec0be9d76c679cbb62a247d.png', 'fieldType': 'IMAGE', 'label': 'image', 'enabled': True, 'sourceFromUpstream': True, 'group': 'AI 应用参数', 'note': 'image', 'options': [], 'random_enabled': False, 'min': '', 'max': '', 'step': '', 'imageOrder': 0, 'required': False}, {'id': '14::seed', 'nodeId': '14', 'fieldName': 'seed', 'fieldValue': '3250470112', 'fieldType': 'INT', 'label': 'seed', 'enabled': True, 'sourceFromUpstream': True, 'group': 'AI 应用参数', 'note': 'seed', 'options': [], 'random_enabled': True, 'min': '1', 'max': '4294967295', 'step': '1', 'imageOrder': 0, 'required': False}]}, {'id': '1997622492837646338', 'appId': '1997622492837646338', 'title': '2511-光线迁移', 'note': '', 'thumbnail': '', 'enabled': True}]
AI_API_KEY = os.getenv('COMFLY_API_KEY', '')
PUBLIC_BASE_URL = os.getenv('PUBLIC_BASE_URL', '').strip().rstrip('/')
PUBLIC_MEDIA_BASE_URL = os.getenv('PUBLIC_MEDIA_BASE_URL', '').strip().rstrip('/')
MODELSCOPE_API_KEY = os.getenv('MODELSCOPE_API_KEY', '')
MODELSCOPE_CHAT_BASE_URL = 'https://api-inference.modelscope.cn/v1'
MODELSCOPE_DEFAULT_IMAGE_MODELS = ['Qwen/Qwen-Image-2512', 'Qwen/Qwen-Image-Edit-2511']
MODELSCOPE_DEFAULT_CHAT_MODELS = ['Qwen/Qwen3-235B-A22B', 'Qwen/Qwen3-VL-235B-A22B-Instruct', 'MiniMax/MiniMax-M2.7:MiniMax']
_MODELSCOPE_CONFIGURED_CHAT_MODELS = [m.strip() for m in os.getenv('MODELSCOPE_CHAT_MODELS', '').split(',') if m.strip()]
MODELSCOPE_CHAT_MODELS = list(dict.fromkeys([m for m in [*MODELSCOPE_DEFAULT_CHAT_MODELS, *_MODELSCOPE_CONFIGURED_CHAT_MODELS] if m]))
MODELSCOPE_DEFAULT_IMAGE_MODEL = MODELSCOPE_DEFAULT_IMAGE_MODELS[0]
MODELSCOPE_DEFAULT_CHAT_MODEL = 'Qwen/Qwen3-235B-A22B'
MODELSCOPE_DEFAULT_LORAS = [{'id': 'Daniel8152/Qwen-Image-2512-Film', 'name': 'Qwen Image 2512 Film', 'target_model': 'Qwen/Qwen-Image-2512', 'strength': 0.8, 'enabled': True, 'note': ''}]
MODELSCOPE_DEFAULTS_VERSION = 3
CHAT_MODEL = os.getenv('CHAT_MODEL', 'gpt-5.4')
IMAGE_MODEL = os.getenv('IMAGE_MODEL', 'nano-banana-pro')
SYSTEM_PROMPT = os.getenv('SYSTEM_PROMPT', 'You are a helpful assistant.')
MAX_HISTORY_MESSAGES = int(os.getenv('MAX_HISTORY_MESSAGES', '30'))
AI_REQUEST_TIMEOUT = float(os.getenv('REQUEST_TIMEOUT', '1800'))
IMAGE_POLL_INTERVAL = float(os.getenv('IMAGE_POLL_INTERVAL', '2'))
IMAGE_TASK_TIMEOUT = float(os.getenv('IMAGE_TASK_TIMEOUT', str(AI_REQUEST_TIMEOUT)))
APIMART_IMAGE_TASK_TIMEOUT = float(os.getenv('APIMART_IMAGE_TASK_TIMEOUT', '1800'))
APIMART_IMAGE_POLL_INTERVAL = float(os.getenv('APIMART_IMAGE_POLL_INTERVAL', '5'))
APIMART_IMAGE_INITIAL_POLL_DELAY = float(os.getenv('APIMART_IMAGE_INITIAL_POLL_DELAY', '10'))
VIDEO_POLL_TIMEOUT = float(os.getenv('VIDEO_POLL_TIMEOUT', '1800'))
ONLINE_IMAGE_PROMPT_MAX_LENGTH = int(os.getenv('ONLINE_IMAGE_PROMPT_MAX_LENGTH', '20000'))
VIDEO_PROMPT_MAX_LENGTH = int(os.getenv('VIDEO_PROMPT_MAX_LENGTH', '4000'))
LLM_MESSAGE_MAX_LENGTH = int(os.getenv('LLM_MESSAGE_MAX_LENGTH', '20000'))
CHAT_ATTACHMENT_MAX = int(os.getenv('CHAT_ATTACHMENT_MAX', '20'))
ONLINE_IMAGE_REFERENCE_MAX = int(os.getenv('ONLINE_IMAGE_REFERENCE_MAX', '20'))
ONLINE_VIDEO_REFERENCE_MAX = int(os.getenv('ONLINE_VIDEO_REFERENCE_MAX', '20'))
FIELD_LABELS = {'prompt': '提示词', 'message': '文本', 'system_prompt': '系统提示词'}
LOCKED_RECOMMENDED_PROVIDER_RULES = {'exellome': {'names': {'exellome'}, 'base_urls': {'https://new.exellome.online'}, 'protocol': 'apimart', 'image_request_mode': 'openai-video-proxy', 'video_models': []}, 'fhl': {'names': {'fhl'}, 'base_urls': {'https://www.fhl.mom'}, 'protocol': 'openai', 'image_request_mode': 'openai-responses', 'video_models': []}}
os.makedirs(STATIC_DIR, exist_ok=True)
STATIC_PROMPT_TEMPLATE_MD = os.path.join(STATIC_DIR, 'system-prompts', 'firefly-canvas-prompt-templates.md')
PROMPT_TEMPLATE_PATHS = [STATIC_PROMPT_TEMPLATE_MD]
PROMPT_TEMPLATE_EN = {'多机位九宫格': {'name': '9-Angle Multi-Camera Grid', 'scene': 'Show the same subject or scene from 9 camera angles for character turnarounds, product views, or space scouting.'}, '多机位九宫格4K': {'name': '9-Angle Multi-Camera Grid 4K', 'scene': 'A high-resolution 9-angle reference sheet for print-grade output, large displays, and fine material study.'}, '剧情推演四宫格': {'name': '4-Panel Story Progression', 'scene': 'Preview four consecutive story beats or emotional stages for storyboard planning and narrative rhythm tests.'}, '角色脸部三视图': {'name': 'Character Face 3-View Sheet', 'scene': 'Front, side, and three-quarter face references for Actor ID locking and expression consistency.'}, '产品三视图': {'name': 'Product 3-View Sheet', 'scene': 'Front, side, and top product views for industrial design, ecommerce detail pages, and technical documents.'}, '25宫格连贯分镜': {'name': '25-Panel Continuous Storyboard', 'scene': 'A full 5x5 storyboard for continuous scene or action flow, useful for film previews and motion continuity tests.'}, '电影级光影校正': {'name': 'Cinematic Lighting Comparison', 'scene': 'Compare the same subject or scene under different lighting conditions for mood, color, and lighting choices.'}, '角色设定参考表（胸口特写+全身三视图）': {'name': 'Character Reference Sheet: Portrait + Full-Body Views', 'scene': 'A consistency reference combining a face anchor and full-body front, side, and back views for Actor ID and costume lock.'}, '6种基础表情胸像（2×3六宫格）': {'name': '6 Basic Expression Busts', 'scene': 'Six basic expressions of the same character for expression consistency, emotion baselines, and Seedance Talk-to-Edit reference.'}, '360全景图': {'name': '360 Panorama VR Image', 'scene': 'Generate a seamless 360-degree VR panorama with continuous left and right edges and natural pole transitions.'}}
CANVAS_TASKS: Dict[str, Dict[str, Any]] = {}
CANVAS_TASK_LOCK = Lock()
DEFAULT_PROJECT_ID = 'default'
CANVAS_COLORS = {'', 'red', 'orange', 'amber', 'green', 'teal', 'blue', 'violet', 'pink', 'slate'}
IMAGE_OUTPUT_KEY_HINTS = ('url', 'image_url', 'imageUrl', 'image', 'output_url', 'outputUrl', 'result_url', 'resultUrl', 'download_url', 'downloadUrl', 'asset_url', 'assetUrl')
IMAGE_CONTAINER_KEY_HINTS = ('images', 'image', 'output', 'outputs', 'result', 'results', 'data', 'items', 'files')
IMAGE_BASE64_KEY_HINTS = ('b64_json', 'base64', 'image_base64', 'imageBase64')
RESPONSES_REJECT_STATUSES = {400, 404, 405, 415, 422}
RESPONSES_POLL_INTERVAL = 5.0
RESPONSES_POLL_MAX_SECONDS = 1500.0
PER_MODEL_PROTOCOL_OPTIONS = {'openai', 'gemini'}
FIXED_PROTOCOL_PROVIDER_IDS = {'modelscope', 'volcengine', 'jimeng', 'runninghub'}
AVATAR_SUPPORTED_PLATFORMS = {'apimart', 'volcengine'}
JIMENG_MIN_CLI_VERSION = (1, 4, 2)
JIMENG_RATIO_CHOICES = [(21, 9), (16, 9), (3, 2), (4, 3), (1, 1), (3, 4), (2, 3), (9, 16)]
JIMENG_TEXT2IMAGE_MODELS = {'3.0', '3.1', '4.0', '4.1', '4.5', '4.6', '5.0'}
JIMENG_IMAGE2IMAGE_MODELS = {'4.0', '4.1', '4.5', '4.6', '5.0'}
JIMENG_VIDEO_1080P_MODELS = {'seedance2.0_vip', 'seedance2.0fast_vip'}
IMAGE_TASK_SUCCESS_STATUSES = {'SUCCESS', 'SUCCESSFUL', 'SUCCEED', 'SUCCEEDED', 'COMPLETED', 'COMPLETE', 'DONE', 'FINISHED', 'OK', 'READY'}
IMAGE_TASK_FAILED_STATUSES = {'FAILURE', 'FAILED', 'FAIL', 'ERROR', 'ERRORED', 'CANCELED', 'CANCELLED', 'TIMEOUT', 'REJECTED', 'EXPIRED'}
PROJECT_MEDIA_ADAPTER_SCHEME = 'project-media'
PROJECT_MEDIA_ADAPTER_PROJECT: ContextVar[Optional[str]] = ContextVar('project_media_adapter_project', default=None)
STORAGE_IMAGE_EXTS = {'.png', '.jpg', '.jpeg', '.webp', '.gif', '.bmp', '.avif'}
AVATAR_LEGACY_FLAT_FIELDS = ('platform', 'provider_id', 'project_name', 'avatar_task_id', 'avatar_status', 'avatar_detail', 'asset_uri', 'asset_id', 'registered_at')
ASSET_CLASSIFICATION_PROMPT = '请识别这张图片，输出严格 JSON，不要 Markdown，不要解释。\n目标是给素材库做非常全面的筛选分类。所有字段都用中文短标签数组，尽量具体但不要虚构。\nJSON 结构：\n{\n  "summary": "一句话描述",\n  "categories": {\n    "environment": ["室内/室外/自然/城市/棚拍/商业空间等环境大类"],\n    "scene": ["室内/室外/棚拍/街景/自然/商业空间等"],\n    "space": ["卧室/餐厅/客厅/厨房/浴室/办公室/店铺/展厅/户外道路等"],\n    "subject": ["人物/模特/产品/家具/建筑/食物/动物/车辆/植物等"],\n    "model": ["无人/单人模特/多人模特/男性模特/女性模特/儿童模特/半身模特/全身模特/手部模特等"],\n    "people": ["无人/单人/多人/男性/女性/儿童/半身/全身/手部特写等"],\n    "style": ["写实/摄影/插画/3D/极简/奢华/复古/现代/电商/电影感等"],\n    "lighting": ["自然光/硬光/柔光/逆光/侧光/夜景/暖光/冷光/高对比/低对比等"],\n    "color": ["白色/黑色/暖色/冷色/高饱和/低饱和/莫兰迪/金属色等"],\n    "composition": ["近景/中景/远景/俯拍/仰拍/正面/侧面/居中/留白/对称/特写等"],\n    "mood": ["温馨/高级/清爽/科技/自然/浪漫/神秘/活力/安静等"],\n    "use_case": ["广告/电商主图/海报/社媒/样机/参考图/背景/角色参考/空间参考等"],\n    "objects": ["画面中重要物体"],\n    "materials": ["木材/金属/玻璃/布料/皮革/石材/陶瓷等"],\n    "quality": ["高清/模糊/低清/噪点/水印/截图/透明背景等"]\n  },\n  "tags": ["综合关键词，20个以内"]\n}\n要求：只返回可解析 JSON；每个数组最多 8 项；如果不确定就省略该标签。'
ASSET_CLASSIFICATION_DIMENSION_NAMES = {'environment': '环境', 'scene': '场景', 'space': '空间', 'subject': '主体', 'model': '模特', 'people': '人物', 'style': '风格', 'lighting': '光影', 'color': '色彩', 'composition': '构图', 'mood': '氛围', 'use_case': '用途', 'objects': '物体', 'materials': '材质', 'quality': '质量'}
SHARED_MEDIA_EXTS = {'.png', '.jpg', '.jpeg', '.webp', '.gif', '.bmp', '.mp4', '.webm', '.mov', '.m4v', '.mkv', '.mp3', '.wav', '.m4a', '.aac', '.ogg', '.flac'}
SHARED_SCAN_MAX_ENTRIES = 8000
SHARED_FOLDERS_LOCK = Lock()
TEXT_ATTACHMENT_EXTS = {'.txt', '.md', '.markdown', '.json', '.csv', '.log', '.py', '.js', '.ts', '.tsx', '.jsx', '.html', '.css', '.xml', '.yaml', '.yml'}
XLSX_IMAGE_EXTS = {'.png', '.jpg', '.jpeg', '.webp', '.gif', '.bmp', '.tif', '.tiff'}
EXCEL_MAX_SHEETS = 8
EXCEL_MAX_ROWS_PER_SHEET = 80
EXCEL_MAX_COLS_PER_ROW = 30
MAX_ATTACHMENT_TEXT_CHARS = 12000
APIMART_UPLOAD_RETRY_ATTEMPTS = 3
AVATAR_TASK_DONE_STATUSES = {'completed', 'complete', 'succeeded', 'success', 'active', 'done'}
AVATAR_TASK_FAIL_STATUSES = {'failed', 'fail', 'error', 'rejected', 'canceled', 'cancelled', 'expired'}
VOLCENGINE_ARK_ASSET_HOST = 'open.volcengineapi.com'
VOLCENGINE_ARK_ASSET_SERVICE = 'ark'
VOLCENGINE_ARK_ASSET_REGION = 'cn-beijing'
VOLCENGINE_ARK_ASSET_VERSION = '2024-01-01'
CHAT_RATIO_SIZE_OPTIONS = {'1:1': ('1024x1024', '1536x1536', '2048x2048'), '2:3': ('720x1080', '1024x1536', '1365x2048'), '3:2': ('1080x720', '1536x1024', '2048x1365'), '3:4': ('1008x1344', '1536x2048', '2448x3264'), '4:3': ('1344x1008', '2048x1536', '3264x2448'), '9:16': ('720x1280', '1080x1920', '1440x2560'), '16:9': ('1280x720', '1920x1080', '2560x1440'), '4:5': ('819x1024', '1216x1520', '1621x2048'), '5:4': ('1024x819', '1536x1228', '2048x1638'), '21:9': ('1344x576', '2048x878', '2752x1182')}
GPT_IMAGE2_MAX_EDGE = 3840
GPT_IMAGE2_MAX_PIXELS = 8294400
GPT_IMAGE2_MIN_PIXELS = 655360
VOLCENGINE_MIN_PIXELS = 3686400
VOLCENGINE_MIN_EDGE = 1536
VOLCENGINE_MAX_EDGE = 4096
VOLCENGINE_RATIO_CHOICES = [(1, 1, '1:1'), (4, 3, '4:3'), (3, 4, '3:4'), (16, 9, '16:9'), (9, 16, '9:16'), (21, 9, '21:9'), (9, 21, '9:21'), (3, 2, '3:2'), (2, 3, '2:3'), (5, 4, '5:4'), (4, 5, '4:5')]
GEMINI_IMAGE_ASPECT_RATIOS = ((1, 1, '1:1'), (1, 4, '1:4'), (1, 8, '1:8'), (2, 3, '2:3'), (3, 2, '3:2'), (3, 4, '3:4'), (4, 1, '4:1'), (4, 3, '4:3'), (4, 5, '4:5'), (5, 4, '5:4'), (8, 1, '8:1'), (9, 16, '9:16'), (16, 9, '16:9'), (21, 9, '21:9'))
RUNNINGHUB_ENTRY_MODEL_RE = re.compile('^app:(.+)$')
SEED_UINT32_MAX = 4294967295
AGENT_ACTIONS = {'chat', 'generate_image', 'edit_image'}
AGENT_IMAGE_KEYWORDS = ['生成', '画', '出图', '生图', '图片', '图像', '海报', '头像', '壁纸', '插画', '照片', 'photo', 'image', 'picture', 'draw', 'generate']
AGENT_EDIT_KEYWORDS = ['修改', '改成', '换成', '调整', '优化', '编辑', '重绘', '上一张', '刚才', '这张', '那张', '参考图', '改图', 'edit', 'modify', 'change', 'revise']
CN_NUMERAL_MAP = {'一': 1, '二': 2, '两': 2, '俩': 2, '三': 3, '四': 4}
_DOUBLE_EXT_RE = re.compile('(\\.[A-Za-z0-9]{1,5})\\1$', re.IGNORECASE)
_DOUBLE_EXT_MEDIA = {'.png', '.jpg', '.jpeg', '.webp', '.gif', '.bmp', '.avif', '.mp4', '.webm', '.mov', '.m4v', '.flv'}
_AGENT_IMAGE_PARAM_SCHEMA: Dict[str, Tuple[type, ...]] = {'size': (str,), 'width': (int,), 'height': (int,), 'aspect_ratio': (str,), 'resolution': (str,), 'quality': (str,), 'model': (str,), 'provider_id': (str,), 'seed': (int,), 'prompt': (str,), 'media_type': (str,), 'n': (int,), 'reference_asset_ids': (list,)}
_AGENT_VIDEO_PARAM_SCHEMA: Dict[str, Tuple[type, ...]] = {**_AGENT_IMAGE_PARAM_SCHEMA, 'duration': (int,), 'enhance_prompt': (bool,), 'enable_upsample': (bool,), 'watermark': (bool,), 'camera_fixed': (bool,), 'return_last_frame': (bool,), 'generate_audio': (bool,)}
_AGENT_PROMPT_PARAM_SCHEMA: Dict[str, Tuple[type, ...]] = {'prompt': (str,), 'media_type': (str,), 'n': (int,), 'model': (str,), 'style': (str,), 'format': (str,), 'language': (str,), 'reference_asset_ids': (list,)}
_AGENT_TASK_INPUT_KEYS = {'kind', 'media_type', 'type', 'count', 'quantity', 'n', 'params', 'parameters', 'prompt', 'model_type', 'modelType', 'model', 'reference_asset_ids', 'references', 'index', 'task_id', 'plan_id', 'result_node_id', 'status', 'created_at', 'updated_at', 'started_at', 'completed_at', 'error', 'outputs', 'result', 'prompt_output', 'dependencies'}
_AGENT_FORBIDDEN_VALUE_TOKENS = ('delete', 'global_settings', 'global-setting', 'photoshop', 'comfyui', 'comfy', 'local_runtime', 'local-runtime', 'filesystem', 'project_library', 'workspace', 'rm -rf', 'rmrf', 'api_key', 'apikey', 'secret', 'password', 'bearer')
IMAGE_PARAM_RATIOS = [{'value': '1:1', 'label': '1:1'}, {'value': '3:4', 'label': '3:4'}, {'value': '4:3', 'label': '4:3'}, {'value': '16:9', 'label': '16:9'}, {'value': '9:16', 'label': '9:16'}, {'value': '2:3', 'label': '2:3'}, {'value': '3:2', 'label': '3:2'}]
IMAGE_PARAM_RESOLUTIONS = [{'value': '1k', 'label': '1K'}, {'value': '2k', 'label': '2K'}, {'value': '4k', 'label': '4K'}]
VIDEO_URL_KEYS = ('url', 'video_url', 'videoUrl', 'mp4_url', 'mp4Url', 'output', 'output_url', 'outputUrl', 'download_url', 'downloadUrl', 'video', 'src', 'uri', 'preview_url', 'previewUrl', 'path', 'last_frame_url', 'lastFrameUrl', 'remixed_from_video_id')
VIDEO_TASK_SUCCESS_STATUSES = {'SUCCESS', 'SUCCEED', 'SUCCEEDED', 'COMPLETED', 'COMPLETE', 'DONE', 'FINISHED', 'FINISH', 'OK', 'READY'}
VIDEO_TASK_FAILURE_STATUSES = {'FAILURE', 'FAILED', 'FAIL', 'ERROR', 'ERRORED', 'CANCELED', 'CANCELLED', 'TIMEOUT', 'TIMEDOUT', 'REJECTED', 'EXPIRED'}
PROJECT_VIDEO_CANDIDATE_INCOMPATIBLE_KEY = '_project_video_candidate_incompatible_parameters'
PROMPT_BUILTIN_CATEGORY_IDS = {'view', 'storyboard', 'character', 'product', 'lighting', 'custom'}

class QuietAccessLogFilter(logging.Filter):

    def filter(self, record):
        args = record.args if isinstance(record.args, tuple) else ()
        if len(args) >= 3:
            path = str(args[2]).split('?', 1)[0]
            status = int(args[4]) if len(args) >= 5 and str(args[4]).isdigit() else 0
            quiet_dynamic = any((path.startswith(prefix) and path.endswith('/meta') for prefix in QUIET_ACCESS_PREFIXES))
            if (path in QUIET_ACCESS_PATHS or quiet_dynamic) and status < 400:
                return False
        message = record.getMessage()
        if any((f'"GET {path}' in message and '" 200' in message for path in QUIET_ACCESS_PATHS)):
            return False
        if 'GET /api/canvases/' in message and '/meta' in message and ('" 200' in message):
            return False
        return True

def _offline_local_edit_adapter(request: Dict[str, Any]) -> bytes:
    """Deterministic fake retained as an explicit test seam."""
    image = as_rgba(bytes(request['image']))
    r, g, b, a = image.getpixel((0, 0))
    image.putpixel((0, 0), ((r + 1) % 256, g, b, a))
    return encode_png(image)

def _conversation_pending_task_keys_from_canvas(project_id: str, canvas: Optional[Dict[str, Any]]) -> set[Tuple[str, str, str]]:
    keys: set[Tuple[str, str, str]] = set()
    if not isinstance(canvas, dict):
        return keys
    for node in canvas.get('nodes') or []:
        if not isinstance(node, dict) or node.get('type') != 'smart-conversation':
            continue
        node_id = str(node.get('id') or '')
        if not node_id:
            continue
        for message in node.get('messages') or []:
            if not isinstance(message, dict) or message.get('role') != 'user' or message.get('status') != 'pending':
                continue
            request_id = str(message.get('request_id') or '')
            if request_id:
                keys.add((str(project_id), node_id, request_id))
    return keys

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

def ensure_project_media_import_executor() -> ThreadPoolExecutor:
    global PROJECT_MEDIA_IMPORT_EXECUTOR, PROJECT_MEDIA_IMPORT_ACCEPTING
    with PROJECT_MEDIA_IMPORT_TASKS_LOCK:
        if not PROJECT_MEDIA_IMPORT_ACCEPTING:
            if PROJECT_MEDIA_IMPORT_SHUTTING_DOWN:
                raise RuntimeError('媒体导入服务正在关闭')
            PROJECT_MEDIA_IMPORT_ACCEPTING = True
        if PROJECT_MEDIA_IMPORT_EXECUTOR is None:
            PROJECT_MEDIA_IMPORT_EXECUTOR = ThreadPoolExecutor(max_workers=4, thread_name_prefix='firefly-media')
        return PROJECT_MEDIA_IMPORT_EXECUTOR

def ensure_project_image_generation_executor() -> ThreadPoolExecutor:
    global PROJECT_IMAGE_GENERATION_EXECUTOR
    with CANVAS_TASK_LOCK:
        if PROJECT_IMAGE_GENERATION_EXECUTOR is None:
            PROJECT_IMAGE_GENERATION_EXECUTOR = ThreadPoolExecutor(max_workers=4, thread_name_prefix='firefly-generation')
        return PROJECT_IMAGE_GENERATION_EXECUTOR

def public_project_media_import_task(task: Dict[str, Any]) -> Dict[str, Any]:
    return {key: value for key, value in task.items() if key not in {'project_id', 'source_key'}}

def _storage_abs_path(value, fallback):
    text = str(value or '').strip()
    if not text:
        return os.path.abspath(fallback)
    text = os.path.expanduser(os.path.expandvars(text))
    if not os.path.isabs(text):
        text = os.path.join(BASE_DIR, text)
    return os.path.abspath(text)

def friendly_validation_error(errors):
    parts = []
    for err in errors or []:
        loc = [str(item) for item in err.get('loc', []) if item != 'body']
        field = loc[-1] if loc else ''
        label = FIELD_LABELS.get(field, field or '请求参数')
        ctx = err.get('ctx') or {}
        limit = ctx.get('limit_value') or ctx.get('max_length') or ctx.get('min_length')
        err_type = str(err.get('type') or '')
        msg = str(err.get('msg') or '')
        if 'max_length' in err_type or 'at most' in msg:
            parts.append(f'{label}过长：当前内容超过后端上限 {limit} 个字符。请拆分为多个提示词节点，或先用 LLM 节点压缩后再生成。')
        elif 'min_length' in err_type:
            parts.append(f'{label}不能为空。')
        else:
            parts.append(f'{label}格式不正确：{msg}')
    return '\n'.join(parts) or '请求参数不正确。'

async def request_validation_exception_handler(request: Request, exc: RequestValidationError):
    return JSONResponse(status_code=422, content={'detail': friendly_validation_error(exc.errors()), 'errors': exc.errors()})

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
    _app_main().AI_BASE_URL = os.getenv('COMFLY_BASE_URL', 'https://ai.comfly.chat').rstrip('/')
    IMAGE_MODELS = model_list('IMAGE_MODELS', os.getenv('IMAGE_MODEL', IMAGE_MODEL), ['nano-banana-pro'])
    CHAT_MODELS = model_list('CHAT_MODELS', os.getenv('CHAT_MODEL', CHAT_MODEL), ['gpt-4o-mini', 'gemini-3.1-flash-image-preview-2k'])
    VIDEO_MODELS = model_list('VIDEO_MODELS', 'veo3-fast', ['veo2', 'veo2-fast', 'veo2-pro', 'veo3', 'veo3-fast', 'veo3-pro', 'veo3.1', 'veo3.1-fast', 'veo3.1-quality', 'veo3.1-lite', 'sora-2', 'sora-2-pro', 'wan2.6-t2v', 'wan2.6-i2v', 'wan2.5-t2v-preview', 'wan2.5-i2v-preview', 'wan2.2-t2v-plus', 'wan2.2-i2v-plus', 'wan2.2-i2v-flash', 'doubao-seedance-2-0-260128', 'doubao-seedance-2-0-fast-260128', 'doubao-seedance-1-5-pro-251215', 'doubao-seedance-1-0-pro-250528', 'doubao-seedance-1-0-lite-t2v-250428', 'doubao-seedance-1-0-lite-i2v-250428'])
    _configured = [m.strip() for m in os.getenv('MODELSCOPE_CHAT_MODELS', '').split(',') if m.strip()]
    MODELSCOPE_CHAT_MODELS = list(dict.fromkeys([m for m in [*MODELSCOPE_DEFAULT_CHAT_MODELS, *_configured] if m]))

def provider_key_env(provider_id):
    if provider_id == 'comfly':
        return 'COMFLY_API_KEY'
    if provider_id == 'modelscope':
        return 'MODELSCOPE_API_KEY'
    if provider_id == 'runninghub':
        return 'RUNNINGHUB_API_KEY'
    if provider_id == 'volcengine':
        return 'ARK_API_KEY'
    return f"API_PROVIDER_{re.sub('[^A-Za-z0-9]', '_', provider_id).upper()}_KEY"

def runninghub_wallet_key_env():
    return 'RUNNINGHUB_WALLET_API_KEY'

def volcengine_access_key_env():
    return 'VOLCENGINE_ACCESS_KEY_ID'

def volcengine_secret_key_env():
    return 'VOLCENGINE_SECRET_ACCESS_KEY'

def mask_secret(value):
    if not value:
        return ''
    if len(value) <= 4:
        return '••••••••'
    tail = value[-4:]
    return f'••••••••{tail}'

def strip_auth_scheme(value, scheme='Bearer'):
    text = str(value or '').strip()
    if not text:
        return ''
    pattern = f'^{re.escape(scheme)}\\s+'
    return re.sub(pattern, '', text, flags=re.I).strip()

def bearer_auth_value(value):
    token = strip_auth_scheme(value, 'Bearer')
    return f'Bearer {token}' if token else ''

def default_api_providers():
    return [{'id': 'volcengine', 'name': '火山引擎', 'base_url': VOLCENGINE_DEFAULT_BASE_URL, 'protocol': 'volcengine', 'image_request_mode': 'openai', 'image_generation_endpoint': '', 'image_edit_endpoint': '', 'enabled': True, 'primary': False, 'image_models': [], 'chat_models': [], 'video_models': [], 'ms_loras': [], 'ms_defaults_version': 0, 'volcengine_project_name': VOLCENGINE_DEFAULT_PROJECT_NAME, 'volcengine_region': VOLCENGINE_DEFAULT_REGION}]

def merge_default_api_providers(providers, inject_missing=True):
    merged = [dict(item) for item in providers if str(item.get('id') or '').strip().lower() not in REMOVED_PROVIDER_IDS]
    volc_default = next((d for d in default_api_providers() if d['id'] == 'volcengine'), None)
    if volc_default:
        current = next((item for item in merged if item.get('id') == 'volcengine'), None)
        legacy = next((item for item in merged if item.get('id') != 'volcengine' and str(item.get('protocol') or '').lower() == 'volcengine'), None)
        if not current:
            if legacy:
                legacy_image_models = model_list_from_values(legacy.get('image_models') or [])
                legacy_video_models = model_list_from_values(legacy.get('video_models') or [])
                current = {**volc_default, 'base_url': legacy.get('base_url') or volc_default['base_url'], 'image_models': legacy_image_models or model_list_from_values(volc_default.get('image_models') or []), 'chat_models': model_list_from_values(legacy.get('chat_models') or []), 'video_models': legacy_video_models}
                merged.append(current)
            elif inject_missing:
                merged.append(volc_default)
        else:
            if not current.get('base_url'):
                current['base_url'] = volc_default['base_url']
            current['protocol'] = 'volcengine'
            current['volcengine_project_name'] = str(current.get('volcengine_project_name') or VOLCENGINE_DEFAULT_PROJECT_NAME).strip() or VOLCENGINE_DEFAULT_PROJECT_NAME
            current['volcengine_region'] = str(current.get('volcengine_region') or VOLCENGINE_DEFAULT_REGION).strip() or VOLCENGINE_DEFAULT_REGION
    for current in merged:
        if not is_jimeng_provider(current):
            continue
        current['protocol'] = 'jimeng'
        current['base_url'] = ''
        current['image_models'] = model_list_from_values([*[item for item in current.get('image_models') or [] if str(item or '').strip() not in JIMENG_LEGACY_IMAGE_MODELS], *JIMENG_DEFAULT_IMAGE_MODELS])
        current['video_models'] = model_list_from_values([*[item for item in current.get('video_models') or [] if str(item or '').strip() not in JIMENG_LEGACY_VIDEO_MODELS], *JIMENG_DEFAULT_VIDEO_MODELS])
    for current in merged:
        current_protocol = str((current or {}).get('protocol') or '').strip().lower()
        if current_protocol not in {'codex', 'gemini-cli'}:
            continue
        current['protocol'] = current_protocol
        current['base_url'] = ''
        default_image_models = CODEX_DEFAULT_IMAGE_MODELS if current_protocol == 'codex' else GEMINI_CLI_DEFAULT_IMAGE_MODELS
        default_chat_models = CODEX_DEFAULT_CHAT_MODELS if current_protocol == 'codex' else GEMINI_CLI_DEFAULT_CHAT_MODELS
        image_models = current.get('image_models') or []
        if current_protocol == 'codex':
            image_models = [item for item in image_models if str(item or '').strip().lower() != '$imagegen']
        current['image_models'] = model_list_from_values([*image_models, *default_image_models])
        current['chat_models'] = model_list_from_values([*(current.get('chat_models') or []), *default_chat_models])
        current['video_models'] = []
    return merged

def normalize_model_list(values):
    return model_list_from_values(values)

def model_list_from_values(values):
    deduped = []
    for value in values or []:
        item = str(value or '').strip()
        if item and item not in deduped:
            selected_model(item, item)
            deduped.append(item)
    return deduped

def normalize_ms_loras(values):
    normalized = []
    seen = set()
    for raw in values or []:
        if not isinstance(raw, dict):
            continue
        lora_id = str(raw.get('id') or '').strip()
        if not lora_id:
            continue
        target_model = str(raw.get('target_model') or raw.get('model') or '').strip()
        if not target_model:
            continue
        key = (target_model, lora_id)
        if key in seen:
            continue
        seen.add(key)
        try:
            strength = float(raw.get('strength', raw.get('default_strength', 0.8)))
        except Exception:
            strength = 0.8
        strength = max(0.0, min(2.0, strength))
        name = re.sub('\\s+', ' ', str(raw.get('name') or '').strip())[:80]
        normalized.append({'id': lora_id[:180], 'name': name or lora_id, 'target_model': target_model[:180], 'strength': strength, 'enabled': bool(raw.get('enabled', True)), 'note': str(raw.get('note') or '').strip()[:300]})
    return normalized

def normalize_runninghub_entry(raw, kind):
    if kind != 'app' or not isinstance(raw, dict):
        return None
    entry_id = str(raw.get('appId') or raw.get('id') or '').strip()
    match = re.search('/run/ai-app/([0-9A-Za-z_-]+)', entry_id)
    if match:
        entry_id = match.group(1)
    if not entry_id:
        return None
    title = re.sub('\\s+', ' ', str(raw.get('title') or raw.get('name') or '').strip())[:80]
    entry = {'id': entry_id[:80], 'title': title or f'AI 应用 {entry_id[-6:]}', 'note': str(raw.get('note') or raw.get('description') or '').strip()[:500], 'thumbnail': str(raw.get('thumbnail') or '').strip()[:1500000], 'enabled': bool(raw.get('enabled', True)), 'appId': entry_id[:80]}
    if raw.get('hidden') is True:
        entry['hidden'] = True
    fields = raw.get('fields')
    if isinstance(fields, list):
        entry['fields'] = [dict(field) for field in fields if isinstance(field, dict)]
    raw_payload = raw.get('raw')
    if isinstance(raw_payload, dict):
        entry['raw'] = raw_payload
    try:
        updated_at = int(raw.get('updatedAt') or raw.get('updated_at') or 0)
        if updated_at > 0:
            entry['updatedAt'] = updated_at
    except Exception:
        pass
    return entry

def normalize_runninghub_entries(values, kind):
    if kind != 'app':
        return []
    normalized = []
    seen = set()
    for raw in values or []:
        entry = normalize_runninghub_entry(raw, 'app')
        if not entry or entry['id'] in seen:
            continue
        seen.add(entry['id'])
        normalized.append(entry)
    return normalized

def runninghub_entry_id(entry, kind):
    if kind != 'app' or not isinstance(entry, dict):
        return ''
    return str(entry.get('appId') or entry.get('id') or '').strip()

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

def merge_runninghub_entry_overlay(system_entry, user_entry):
    if not isinstance(system_entry, dict):
        return user_entry
    if not isinstance(user_entry, dict):
        return system_entry
    merged = {**system_entry, **user_entry}
    if not merged.get('thumbnail') and system_entry.get('thumbnail'):
        merged['thumbnail'] = system_entry.get('thumbnail')
    return merged

def merge_runninghub_system_entries(system_entries, user_entries, kind):
    if kind != 'app':
        return []
    merged = []
    index = {}
    for entry in apply_runninghub_system_thumbnails(system_entries or [], 'app'):
        entry_id = runninghub_entry_id(entry, 'app')
        if entry_id:
            index[entry_id] = len(merged)
            merged.append(entry)
    for entry in apply_runninghub_system_thumbnails(user_entries or [], 'app'):
        entry_id = runninghub_entry_id(entry, 'app')
        if not entry_id:
            continue
        if entry.get('hidden') is True:
            if entry_id in index:
                merged.pop(index[entry_id])
                index = {runninghub_entry_id(item, 'app'): idx for idx, item in enumerate(merged)}
            continue
        if entry_id in index:
            merged[index[entry_id]] = merge_runninghub_entry_overlay(merged[index[entry_id]], entry)
        else:
            index[entry_id] = len(merged)
            merged.append(entry)
    return merged

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

def merge_runninghub_provider_with_static(provider):
    static_provider = load_static_runninghub_provider()
    if not static_provider:
        return provider
    if not isinstance(provider, dict):
        return static_provider
    merged = {**static_provider, **provider}
    merged['protocol'] = 'runninghub'
    merged['image_models'] = model_list_from_values(provider.get('image_models') or [])
    merged['chat_models'] = model_list_from_values(provider.get('chat_models') or [])
    merged['video_models'] = model_list_from_values(provider.get('video_models') or [])
    merged['rh_apps'] = merge_runninghub_system_entries(static_provider.get('rh_apps') or [], provider.get('rh_apps') or [], 'app')
    return normalize_provider(merged)

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

def normalize_endpoint_override(value, label):
    endpoint = str(value or '').strip()
    if not endpoint:
        return ''
    if len(endpoint) > 300 or re.search('\\s', endpoint):
        raise HTTPException(status_code=400, detail=f'{label} 不合法，请填写类似 /v1/images/edits 的路径')
    if re.match('^https?://', endpoint, re.I):
        return endpoint.rstrip('/')
    if not endpoint.startswith('/'):
        raise HTTPException(status_code=400, detail=f'{label} 需要以 /v1/... 开头，或填写完整 http(s) 地址')
    return endpoint

def normalize_image_request_mode(value):
    mode = str(value or '').strip().lower()
    return mode if mode in SUPPORTED_IMAGE_REQUEST_MODES else 'openai'

def locked_recommended_provider_rule(provider_id='', name='', base_url=''):
    pid = str(provider_id or '').strip().lower()
    pname = str(name or '').strip().lower()
    pbase = str(base_url or '').strip().rstrip('/').lower()
    try:
        phost = urllib.parse.urlsplit(pbase).netloc.lower()
    except Exception:
        phost = ''
    for key, rule in LOCKED_RECOMMENDED_PROVIDER_RULES.items():
        hosts = {urllib.parse.urlsplit(url).netloc.lower() for url in rule['base_urls']}
        if pid == key or pname in rule['names'] or pbase in rule['base_urls'] or (phost and phost in hosts):
            return rule
    return None

def apply_locked_recommended_model_rules(base_url='', grouped=None):
    rule = locked_recommended_provider_rule('', '', base_url)
    if not rule or 'video_models' not in rule:
        return grouped
    grouped = {key: list(value or []) for key, value in (grouped or {}).items()}
    grouped.setdefault('image', [])
    grouped.setdefault('chat', [])
    grouped['video'] = list(rule.get('video_models') or [])
    return grouped

def runninghub_endpoint_url(provider, path):
    base_url = str((provider or {}).get('base_url') or RUNNINGHUB_DEFAULT_BASE_URL).strip().rstrip('/')
    return f'{base_url}{path}'

def runninghub_openapi_base_url(provider=None):
    base_url = str((provider or {}).get('base_url') or RUNNINGHUB_DEFAULT_BASE_URL).strip().rstrip('/')
    if base_url.endswith('/openapi/v2'):
        return base_url
    return f'{base_url}/openapi/v2'

def runninghub_openapi_url(provider, path=''):
    path = str(path or '').strip()
    if path.startswith('http://') or path.startswith('https://'):
        return path
    path = path.lstrip('/')
    base = runninghub_openapi_base_url(provider)
    return f'{base}/{path}' if path else base

def normalize_recommended_models(value):
    """Keep only the model kinds understood by the automatic router."""
    if not isinstance(value, dict):
        return {}
    normalized = {}
    for kind in ('image', 'chat', 'video'):
        model = str(value.get(kind) or '').strip()
        if model:
            normalized[kind] = model[:200]
    return normalized

def normalize_available_models(value):
    """Store fetched-but-unselected models separately from active model lists."""
    if not isinstance(value, dict):
        return {}
    normalized = {}
    for field in ('image_models', 'chat_models', 'video_models'):
        raw = value.get(field)
        if isinstance(raw, list):
            normalized[field] = model_list_from_values(raw)
    return normalized

def normalize_provider(item):
    provider_id = str(item.get('id') or '').strip().lower()
    if not PROVIDER_ID_RE.fullmatch(provider_id):
        raise HTTPException(status_code=400, detail=f"API 平台 ID 不合法：{provider_id or '(empty)'}")
    name = re.sub('\\s+', ' ', str(item.get('name') or provider_id).strip())[:60] or provider_id
    base_url = str(item.get('base_url') or '').strip().rstrip('/')
    if base_url and (not re.match('^https?://', base_url)):
        raise HTTPException(status_code=400, detail=f'{name} 的 Base URL 需要以 http:// 或 https:// 开头')
    protocol = str(item.get('protocol') or 'openai').strip().lower()
    if protocol not in SUPPORTED_PROVIDER_PROTOCOLS:
        protocol = 'openai'
    image_request_mode = detect_image_request_mode(base_url, item.get('image_models') or []) or normalize_image_request_mode(item.get('image_request_mode'))
    image_generation_endpoint = normalize_endpoint_override(item.get('image_generation_endpoint'), '文生图端口')
    image_edit_endpoint = normalize_endpoint_override(item.get('image_edit_endpoint'), '图生图/编辑端口')
    video_generation_endpoint = normalize_endpoint_override(item.get('video_generation_endpoint'), '视频生成端口')
    volc_project = re.sub('\\s+', ' ', str(item.get('volcengine_project_name') or '').strip())[:80]
    volc_region = re.sub('\\s+', ' ', str(item.get('volcengine_region') or '').strip())[:40]
    if provider_id == 'volcengine':
        protocol = 'volcengine'
        base_url = base_url or VOLCENGINE_DEFAULT_BASE_URL
        volc_project = volc_project or VOLCENGINE_DEFAULT_PROJECT_NAME
        volc_region = volc_region or VOLCENGINE_DEFAULT_REGION
    if provider_id == 'jimeng' or protocol == 'jimeng':
        protocol = 'jimeng'
        base_url = ''
    if protocol in {'codex', 'gemini-cli'}:
        base_url = ''
    if provider_id == 'runninghub':
        protocol = 'runninghub'
        base_url = base_url or RUNNINGHUB_DEFAULT_BASE_URL
    locked_rule = locked_recommended_provider_rule(provider_id, name, base_url)
    if locked_rule:
        protocol = locked_rule['protocol']
        image_request_mode = locked_rule['image_request_mode']
    image_models = model_list_from_values(item.get('image_models') or [])
    video_models = model_list_from_values(item.get('video_models') or [])
    if locked_rule and 'video_models' in locked_rule:
        video_models = model_list_from_values(locked_rule.get('video_models') or [])
    try:
        model_selection_version = max(0, int(item.get('model_selection_version') or 0))
    except (TypeError, ValueError):
        model_selection_version = 0
    return {'id': provider_id, 'name': name, 'base_url': base_url, 'protocol': protocol, 'image_request_mode': image_request_mode, 'image_generation_endpoint': image_generation_endpoint, 'image_edit_endpoint': image_edit_endpoint, 'video_generation_endpoint': video_generation_endpoint, 'enabled': bool(item.get('enabled', True)), 'primary': bool(item.get('primary', False)), 'image_models': image_models, 'image_model_capabilities': normalize_image_model_capabilities(item.get('image_model_capabilities'), image_models), 'chat_models': model_list_from_values(item.get('chat_models') or []), 'chat_audio_input': bool(item.get('chat_audio_input', False)), 'video_models': video_models, 'video_model_capabilities': normalize_video_model_capabilities(item.get('video_model_capabilities'), video_models), 'recommended_models': normalize_recommended_models(item.get('recommended_models')), 'available_models': normalize_available_models(item.get('available_models')), 'model_selection_version': model_selection_version, 'model_names': normalize_model_name_map(item.get('model_names')), 'model_protocols': normalize_model_protocols(item.get('model_protocols')), 'ms_loras': normalize_ms_loras(item.get('ms_loras') or []), 'ms_defaults_version': int(item.get('ms_defaults_version') or 0), 'rh_apps': normalize_runninghub_entries(item.get('rh_apps') or [], 'app'), 'volcengine_project_name': volc_project, 'volcengine_region': volc_region}

def modelscope_image_api_root():
    return MODELSCOPE_CHAT_BASE_URL.rstrip('/')

def env_quote(value):
    text = str(value or '')
    if not text or re.search('\\s|#|[\'\\"]', text):
        return '"' + text.replace('\\', '\\\\').replace('"', '\\"') + '"'
    return text

def current_app_version():
    version_file = os.path.join(BASE_DIR, 'VERSION')
    try:
        if os.path.exists(version_file):
            with open(version_file, 'r', encoding='utf-8') as f:
                version = (f.read().strip().splitlines() or [''])[0].strip()
                if version:
                    return version
    except Exception:
        pass
    try:
        return time.strftime('%Y.%m.%d', time.localtime())
    except Exception:
        return ''

def static_html_response(filename: str):
    path = os.path.join(STATIC_DIR, filename)
    with open(path, 'r', encoding='utf-8') as f:
        html = f.read()
    return Response(html, media_type='text/html; charset=utf-8', headers={'Cache-Control': 'no-cache'})

def prompt_template_markdown_path() -> str:
    for path in PROMPT_TEMPLATE_PATHS:
        if os.path.exists(path):
            return path
    return ''

def prompt_template_category(name: str, scene: str) -> str:
    text = f'{name} {scene}'
    if any((k in text for k in ['光影', '灯光', '光效', '电影级'])):
        return 'lighting'
    if any((k in text for k in ['视角', '全景', 'VR', '镜头', '俯拍', '仰拍', '景别', '构图', '透视'])):
        return 'view'
    if any((k in text for k in ['角色', '脸部', '表情', 'Actor', '服装'])):
        return 'character'
    if any((k in name for k in ['产品', '电商', '工业'])):
        return 'product'
    return 'storyboard'

def extract_prompt_template_section(block: str, title: str) -> str:
    pattern = f'###\\s*{re.escape(title)}\\s*\\n(?P<body>.*?)(?=\\n###\\s+|\\Z)'
    match = re.search(pattern, block, re.S)
    if not match:
        return ''
    body = match.group('body').strip()
    fence = re.search('```(?:\\w+)?\\s*\\n(?P<code>.*?)\\n```', body, re.S)
    return (fence.group('code') if fence else body).strip()

def parse_prompt_template_markdown(text: str):
    templates = []
    matches = list(re.finditer('^##\\s*预设\\s*(\\d+)\\s*[：:]\\s*(.+?)\\s*$', text, re.M))
    for index, match in enumerate(matches):
        number = match.group(1).strip()
        name = match.group(2).strip()
        start = match.end()
        end = matches[index + 1].start() if index + 1 < len(matches) else len(text)
        block = text[start:end]
        scene = extract_prompt_template_section(block, '适用场景')
        positive = extract_prompt_template_section(block, '正向提示词')
        negative = extract_prompt_template_section(block, '负向提示词')
        params_raw = extract_prompt_template_section(block, '平台参数建议')
        params = {}
        for line in params_raw.splitlines():
            item = re.match('[-*]\\s*\\*\\*(.+?)\\*\\*\\s*[：:]\\s*(.+)', line.strip())
            if item:
                params[item.group(1).strip()] = item.group(2).strip()
        if not positive:
            continue
        templates.append({'id': f'builtin_md_{number}', 'number': number, 'name': name, 'name_en': PROMPT_TEMPLATE_EN.get(name, {}).get('name', name), 'category': prompt_template_category(name, scene), 'scene': scene, 'scene_en': PROMPT_TEMPLATE_EN.get(name, {}).get('scene', scene), 'positive': positive, 'negative': negative, 'params': params, 'builtin': True})
    return templates

class DeleteCanvasLogRequest(BaseModel):
    log_id: str
    delete_unreferenced_media: bool = False
    reset_referencing_nodes: bool = False
    base_updated_at: int = 0

class AIReference(BaseModel):
    url: str = ''
    name: str = ''
    role: str = ''
    kind: str = ''
    mime: str = ''

class OnlineImageRequest(BaseModel):
    prompt: str = Field(min_length=1, max_length=ONLINE_IMAGE_PROMPT_MAX_LENGTH)
    provider_id: str = 'comfly'
    model: str = ''
    size: str = '1024x1024'
    quality: str = 'auto'
    n: int = 1
    reference_images: List[AIReference] = []

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
    idempotency_key: str = Field(default='', max_length=240)

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

class ImageTaskQueryRequest(BaseModel):
    provider_id: str = 'comfly'
    task_id: str = Field(min_length=1, max_length=240)

class CanvasVideoRequest(BaseModel):
    prompt: str = Field(min_length=1, max_length=VIDEO_PROMPT_MAX_LENGTH)
    provider_id: str = 'comfly'
    model: str = 'veo3-fast'
    duration: int = 5
    width: Optional[int] = None
    height: Optional[int] = None
    aspect_ratio: str = '16:9'
    resolution: str = ''
    size: str = ''
    quality: str = 'auto'
    media_type: str = 'video'
    images: List[AIReference] = []
    videos: List[str] = []
    audios: List[str] = []
    enhance_prompt: bool = False
    enable_upsample: bool = False
    watermark: bool = False
    seed: Optional[int] = None
    camera_fixed: Optional[bool] = None
    camerafixed: bool = False
    return_last_frame: bool = False
    generate_audio: bool = False
    multimodal: bool = False
    trusted_asset: bool = False

def canvas_video_camera_fixed(payload: CanvasVideoRequest) -> bool:
    """Read the canonical camera_fixed field while accepting the legacy UI spelling."""
    if payload.camera_fixed is not None:
        return bool(payload.camera_fixed)
    return bool(payload.camerafixed)

class TempShUploadRequest(BaseModel):
    url: str = ''

class CloudVideoUploadRequest(BaseModel):
    url: str = ''
    service: str = 'auto'

class RunningHubSubmitRequest(BaseModel):
    webappId: str = ''
    nodeInfoList: List[Dict[str, Any]] = []
    instanceType: str = ''
    useWallet: bool = False

class RunningHubUploadAssetRequest(BaseModel):
    url: str = ''
    useWallet: bool = False

class JimengHelpRequest(BaseModel):
    command: str = ''

class CodexHelpRequest(BaseModel):
    command: str = ''

class GeminiCliHelpRequest(BaseModel):
    command: str = ''

class JimengQueryMediaRequest(BaseModel):
    submit_id: str = ''
    kind: str = 'image'

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

def chat_system_prompt(payload):
    prompt = str(getattr(payload, 'system_prompt', '') or '').strip()
    return prompt or SYSTEM_PROMPT

class CanvasLLMRequest(BaseModel):
    project_id: str = ''
    message: str = Field(min_length=1, max_length=LLM_MESSAGE_MAX_LENGTH)
    system_prompt: str = ''
    model: str = ''
    messages: List[Dict[str, Any]] = []
    provider: str = 'comfly'
    ms_model: str = ''
    images: List[str] = []
    videos: List[str] = []
    audios: List[str] = []
    audio_references: List[Dict[str, Any]] = Field(default_factory=list)

class CanvasCreateRequest(BaseModel):
    title: str = '未命名画布'
    icon: str = '🧩'
    kind: str = 'classic'
    project: Optional[str] = None
    board_x: Optional[float] = None
    board_y: Optional[float] = None

class CanvasMetaUpdate(BaseModel):
    title: Optional[str] = None
    icon: Optional[str] = None
    owner: Optional[str] = None
    color: Optional[str] = None
    pinned: Optional[bool] = None
    project: Optional[str] = None
    board_x: Optional[float] = None
    board_y: Optional[float] = None

class ProjectCreateRequest(BaseModel):
    name: str = '新项目'

class ProjectUpdateRequest(BaseModel):
    name: Optional[str] = None
    order: Optional[int] = None

class FireflyProjectCreateRequest(BaseModel):
    name: str = '新项目'

class GlobalPreferencesPayload(BaseModel):
    theme: str = 'dark'
    language: str = 'zh'

class ProjectLibraryChangeRequest(BaseModel):
    path: str = Field(min_length=1, max_length=1024)
    move_existing: bool = False

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
    force_empty: bool = False

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
    delete_media: bool = False

class ProjectOriginalDeleteRequest(BaseModel):
    asset_id: str
    base_revision: int = Field(ge=0)
    log_id: str = Field(min_length=1, max_length=200)
    output_id: str = Field(min_length=1, max_length=200)
    output_index: int = Field(ge=0)

class ProjectOriginalRestoreRequest(BaseModel):
    asset_id: str
    base_revision: int = Field(ge=0)
    deletion_id: str = Field(min_length=1, max_length=200)

class ProjectOriginalProjectWideRequest(BaseModel):
    asset_id: str
    base_revision: int = Field(ge=0)

class CanvasSaveRequest(BaseModel):
    title: str = '未命名画布'
    icon: str = '🧩'
    nodes: List[Dict[str, Any]] = []
    connections: List[Dict[str, Any]] = []
    viewport: Dict[str, Any] = {}
    logs: List[Dict[str, Any]] = []
    settings: Dict[str, Any] = {}
    client_id: str = ''
    base_updated_at: int = 0

class CanvasAssetCheckRequest(BaseModel):
    urls: List[str] = []

class CanvasAssetDownloadRequest(BaseModel):
    urls: List[str] = []
    items: List[Dict[str, Any]] = []
    filename: str = 'canvas-output-images.zip'

class SmartCanvasGroupExportItem(BaseModel):
    kind: str = ''
    url: str = ''
    text: str = ''
    name: str = ''

class SmartCanvasGroupExportRequest(BaseModel):
    folder: str = ''
    group_name: str = 'group'
    items: List[SmartCanvasGroupExportItem] = []

class LocalImageImportRequest(BaseModel):
    path: str = ''
    paths: List[str] = Field(default_factory=list)

class LocalAssetCaptionRequest(BaseModel):
    names: List[str] = []
    provider: str = 'comfly'
    model: str = ''
    ms_model: str = ''
    prompt: str = '描述图片'

class LocalAssetCaptionSaveRequest(BaseModel):
    name: str = ''
    caption: str = ''

class LocalAssetClassifyRequest(BaseModel):
    names: List[str] = []
    provider: str = 'comfly'
    model: str = ''
    ms_model: str = ''
    prompt: str = ''

class LocalAssetUrlImportItem(BaseModel):
    url: str = ''
    name: str = ''
    data: str = ''
    content_type: str = ''

class LocalAssetUrlImportRequest(BaseModel):
    items: List[LocalAssetUrlImportItem] = []
    folder: str = ''
    classify: bool = False
    provider: str = 'comfly'
    model: str = ''
    ms_model: str = ''
    prompt: str = ''

class LocalAssetFolderRequest(BaseModel):
    parent: str = ''
    path: str = ''
    name: str = ''

class LocalAssetRenameRequest(BaseModel):
    path: str = ''
    name: str = ''

class AssetLibraryCategoryRequest(BaseModel):
    name: str = '新文件夹'
    type: str = 'image'
    library_id: str = ''

class AssetLibraryRequest(BaseModel):
    name: str = '全局资源库'

class AssetLibraryAddRequest(BaseModel):
    category_id: str = ''
    url: str = ''
    name: str = ''
    library_id: str = ''

class AssetLibraryBatchAddRequest(BaseModel):
    category_id: str = ''
    library_id: str = ''
    items: List[AssetLibraryAddRequest] = []

class SharedFolderRegister(BaseModel):
    path: str = ''
    name: str = ''

class SharedFolderImport(BaseModel):
    library_id: str = ''
    category_id: str = ''
    folder_id: str = ''
    paths: List[str] = []

class AssetLibraryRenameRequest(BaseModel):
    name: str = ''
    library_id: str = ''

class AssetLibraryBatchDeleteRequest(BaseModel):
    ids: List[str] = []
    library_id: str = ''

class AssetLibraryBatchMoveRequest(BaseModel):
    ids: List[str] = []
    library_id: str = ''
    target_library_id: str = ''
    target_category_id: str = ''

class AssetLibraryBatchCropRequest(BaseModel):
    ids: List[str] = []
    library_id: str = ''
    target_library_id: str = ''
    target_category_id: str = ''
    mode: str = 'square'

class AssetAvatarRegisterRequest(BaseModel):
    library_id: str = ''
    provider_id: str = ''
    project_name: str = 'default'
    group_name: str = ''

class AssetLibraryClassifyRequest(BaseModel):
    library_id: str = ''
    ids: List[str] = []
    provider: str = 'comfly'
    model: str = ''
    ms_model: str = ''
    prompt: str = ''

class PromptLibraryRequest(BaseModel):
    name: str = '提示词库'

class PromptLibraryItemRequest(BaseModel):
    library_id: str = ''
    item_id: str = ''
    name: str = '提示词'
    category: str = 'custom'
    positive: str = ''
    negative: str = ''
    scene: str = ''

class PromptLibraryBatchDeleteRequest(BaseModel):
    ids: List[str] = []

class PromptLibraryCategoryRequest(BaseModel):
    name: str = '新分组'
    library_id: str = ''

def safe_user_id(user_id, request: Request):
    candidate = (user_id or '').strip()
    if not candidate and request.client:
        candidate = f'ip-{request.client.host}'
    if not candidate:
        candidate = 'anonymous'
    candidate = re.sub('[^a-zA-Z0-9_.-]', '-', candidate)[:80].strip('.-')
    return candidate or 'anonymous'

def normalize_canvas_kind(kind='classic'):
    return 'smart' if str(kind or '').strip().lower() == 'smart' else 'classic'

def project_record(p):
    return {'id': p.get('id'), 'name': (p.get('name') or '未命名项目')[:60], 'order': int(p.get('order') or 0), 'created_at': p.get('created_at', 0), 'updated_at': p.get('updated_at', 0)}

def normalize_canvas_color(value):
    color = str(value or '').strip().lower()
    return color if color in CANVAS_COLORS else ''

def canvas_record(data):
    return {'id': data.get('id'), 'title': data.get('title', '未命名画布'), 'icon': data.get('icon', '🧩'), 'kind': normalize_canvas_kind(data.get('kind')), 'owner': str(data.get('owner') or '')[:40], 'color': normalize_canvas_color(data.get('color')), 'pinned': bool(data.get('pinned') or False), 'project': str(data.get('project') or '').strip() or DEFAULT_PROJECT_ID, 'board_x': data.get('board_x'), 'board_y': data.get('board_y'), 'created_at': data.get('created_at', 0), 'updated_at': data.get('updated_at', 0), 'deleted_at': data.get('deleted_at', 0), 'node_count': len(data.get('nodes', []))}

def canvas_asset_url_value(value):
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, dict):
        for key in ('url', 'path', 'src', 'uri', 'output', 'output_url', 'outputUrl', 'video', 'video_url', 'videoUrl'):
            text = str(value.get(key) or '').strip()
            if text:
                return text
    return ''

def canvas_asset_downloadable_url(url):
    text = str(url or '').strip()
    return text if text.startswith(('/output/', '/assets/', 'http://', 'https://')) else ''

def canvas_asset_kind(value, url=''):
    explicit = ''
    if isinstance(value, dict):
        explicit = str(value.get('kind') or value.get('mediaKind') or value.get('type') or '').lower()
    if 'video' in explicit:
        return 'video'
    if 'audio' in explicit:
        return 'audio'
    if 'text' in explicit:
        return 'text'
    return asset_library_media_kind(url or canvas_asset_url_value(value))

def canvas_asset_name(value, url='', fallback='asset'):
    if isinstance(value, dict):
        for key in ('name', 'filename', 'file', 'title'):
            name = str(value.get(key) or '').strip()
            if name:
                return sanitize_asset_name(name, fallback)
    return sanitize_asset_name(filename_from_media_url(url, fallback), fallback)

def iter_canvas_asset_values(value, path=''):
    if isinstance(value, dict):
        url = canvas_asset_downloadable_url(canvas_asset_url_value(value))
        if url:
            yield (path, value, url)
        for key, child in value.items():
            if key in {'run', 'runs', 'settings', 'params', 'metadata', 'meta', 'prompt', 'text', 'caption', 'logs'}:
                continue
            yield from iter_canvas_asset_values(child, f'{path}.{key}' if path else str(key))
    elif isinstance(value, list):
        for index, child in enumerate(value):
            yield from iter_canvas_asset_values(child, f'{path}[{index}]')
    elif isinstance(value, str):
        url = canvas_asset_downloadable_url(value)
        if url:
            yield (path, value, url)

def canvas_node_title(node):
    if not isinstance(node, dict):
        return ''
    return str(node.get('title') or node.get('name') or node.get('label') or node.get('type') or '节点')[:120]

def extract_canvas_assets(canvas):
    record = canvas_record(canvas)
    canvas_id = str(record.get('id') or '')
    items = []
    seen = set()
    nodes = canvas.get('nodes') if isinstance(canvas.get('nodes'), list) else []
    for node_index, node in enumerate(nodes):
        if not isinstance(node, dict):
            continue
        node_id = str(node.get('id') or f'node_{node_index}')
        node_title = canvas_node_title(node)
        for field_path, raw, url in iter_canvas_asset_values(node):
            dedupe_key = url
            if dedupe_key in seen:
                continue
            seen.add(dedupe_key)
            kind = canvas_asset_kind(raw, url)
            if kind not in {'image', 'video', 'audio', 'text'}:
                continue
            fallback = f"{record.get('title') or 'canvas'}-{len(items) + 1}"
            item = {'id': hashlib.sha1(f'{canvas_id}:{url}'.encode('utf-8')).hexdigest()[:24], 'url': url, 'name': canvas_asset_name(raw, url, fallback), 'kind': kind, 'canvas_id': canvas_id, 'canvas_title': record.get('title') or '未命名画布', 'canvas_kind': record.get('kind') or 'classic', 'canvas_icon': record.get('icon') or 'layers', 'canvas_owner': record.get('owner') or '', 'canvas_color': record.get('color') or '', 'canvas_created_at': record.get('created_at') or 0, 'canvas_updated_at': record.get('updated_at') or 0, 'node_id': node_id, 'node_title': node_title, 'node_type': str(node.get('type') or ''), 'source_path': field_path, 'created_at': node.get('created_at') or record.get('updated_at') or record.get('created_at') or 0}
            if isinstance(raw, dict):
                for key in ('natural_w', 'natural_h', 'width', 'height', 'size', 'duration', 'runMs'):
                    if raw.get(key) is not None:
                        item[key] = raw.get(key)
            items.append(item)
    return items

def display_title(text):
    title = re.sub('\\s+', ' ', text or '').strip()
    return title[:24] or '新对话'

def log_net_error(context, exc, url=''):
    """把网络请求异常的完整链路（含底层 SSL/socket 原因）打到控制台，方便排查 VPN/代理问题。
    httpx 通常把真正的 SSL/连接错误包在 __cause__/__context__ 里，这里把整条链都打出来，
    并附上请求 URL 与当前生效的系统代理，便于判断是「代理瞬时 TLS 错误」还是「线路不通」。
    日志本身绝不能影响主流程，全部包在 try 里。"""
    try:
        chain = []
        cur = exc
        seen = 0
        while cur is not None and seen < 6:
            chain.append(f'{type(cur).__module__}.{type(cur).__name__}: {str(cur)[:200]}')
            nxt = getattr(cur, '__cause__', None) or getattr(cur, '__context__', None)
            if nxt is cur:
                break
            cur = nxt
            seen += 1
        if not url:
            req = getattr(exc, 'request', None)
            if req is not None:
                url = str(getattr(req, 'url', '') or '')
        try:
            proxies = urllib.request.getproxies() or '无'
        except Exception:
            proxies = '?'
        logger.warning(f"[NET-ERR] {context} | url={url or '?'} | sys_proxy={proxies} | " + ' <- '.join(chain))
    except Exception:
        try:
            logger.warning(f'[NET-ERR] {context} | {type(exc).__name__}: {exc}')
        except Exception:
            pass

def selected_model(requested, fallback):
    model = (requested or fallback).strip()
    if not model:
        raise HTTPException(status_code=400, detail='模型名称不能为空')
    if len(model) > 240 or any((ord(ch) < 32 or ord(ch) == 127 for ch in model)):
        raise HTTPException(status_code=400, detail=f'模型名称不合法：{model}')
    return model

def looks_like_vision_chat_model(model):
    """Check if a model supports vision (image input).

    First checks model_specs.json, falls back to name-based detection.
    """
    try:
        from app.services.model_specs import is_vision_model
        return is_vision_model(model)
    except ImportError:
        # Fallback if model_specs service not available
        lc = str(model or '').strip().lower()
        if not lc:
            return False
        vision_keys = ['vision', 'vl-', '-vl-', 'internvl', 'qvq', 'qwen-vl', 'doubao-vision', 'glm-4v', 'minicpm-v', 'gpt-4o', 'gpt-4.1', 'gpt-5', 'claude', 'gemini']
        return any((key in lc for key in vision_keys))

def preferred_chat_model(provider):
    values = [str(item or '').strip() for item in provider.get('chat_models') or [CHAT_MODEL]]
    models = [item for item in values if item]
    if not models:
        return CHAT_MODEL
    if is_volcengine_provider(provider):
        endpoint_models = [item for item in models if item.lower().startswith('ep-')]
        if endpoint_models:
            return endpoint_models[0]
        text_like_models = [item for item in models if not looks_like_vision_chat_model(item)]
        if text_like_models:
            return text_like_models[0]
    for hint in ('gpt-5.4', 'chatgpt-5.4'):
        exact = next((item for item in models if item.lower() == hint), None)
        if exact:
            return exact
        prefix = next((item for item in models if item.lower().startswith(hint)), None)
        if prefix:
            return prefix
    return models[0]

def modelscope_size(value, fallback='1024x1024'):
    size = str(value or fallback).strip().lower().replace('*', 'x')
    if re.fullmatch('\\d{2,5}x\\d{2,5}', size):
        return size
    raise HTTPException(status_code=400, detail=f'ModelScope size 格式不正确：{value or fallback}，应为 WxH，例如 1024x1024')

def unwrap_apimart_response(raw):
    """APIMart 将标准 OpenAI 响应包在 {"code":200,"data":{...}} 里；如果检测到就解包。"""
    if isinstance(raw, dict) and 'data' in raw and isinstance(raw.get('data'), dict) and ('choices' not in raw):
        return raw['data']
    return raw

def text_from_chat_response(data):
    data = unwrap_apimart_response(data)
    choices = data.get('choices') or []
    if not choices:
        return ''
    message = choices[0].get('message') or {}
    content = message.get('content', '')
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for item in content:
            if isinstance(item, dict):
                parts.append(item.get('text') or item.get('content') or '')
        return '\n'.join((part for part in parts if part))
    return str(content)

def responses_input_from_messages(messages):
    """将 OpenAI chat `messages` 转换为 Responses API 的 `input`。
    - 字符串 content → 原样（Responses 支持 role+字符串 content）
    - 数组 content：{type:image_url,image_url:{url}} → {type:input_image,image_url:url}
    - 数组 content：{type:text,text} / 其它 → {type:input_text,text}
    兼容 Responses API（OpenAI 及兼容中转统一使用）。"""
    converted = []
    for msg in messages or []:
        if not isinstance(msg, dict):
            continue
        role = str(msg.get('role') or 'user')
        if role not in {'user', 'assistant', 'system', 'developer'}:
            role = 'user'
        content = msg.get('content', '')
        if isinstance(content, str):
            converted.append({'role': role, 'content': content})
            continue
        if not isinstance(content, list):
            converted.append({'role': role, 'content': str(content or '')})
            continue
        new_content = []
        for part in content:
            if isinstance(part, dict):
                ptype = str(part.get('type') or '')
                if ptype == 'image_url':
                    img = part.get('image_url') or {}
                    url = img.get('url') if isinstance(img, dict) else str(img or '')
                    if url:
                        new_content.append({'type': 'input_image', 'image_url': url})
                elif 'text' in part:
                    new_content.append({'type': 'input_text', 'text': str(part.get('text') or '')})
                else:
                    new_content.append(part)
            else:
                new_content.append(part)
        converted.append({'role': role, 'content': new_content})
    return converted

def text_from_responses(data):
    """从 Responses API 响应提取助手文本。
    结构：output[] 中 type=message 的项，content[] 中 type=output_text 的 text。
    也兼容部分中转直接返回 {output_text} 或扁平 {output:[{content:[{text}]}]}。"""
    if not isinstance(data, dict):
        return ''
    parts = []
    def collect(obj):
        if isinstance(obj, str):
            if obj.strip():
                parts.append(obj)
            return
        if isinstance(obj, list):
            for item in obj:
                collect(item)
            return
        if not isinstance(obj, dict):
            return
        t = obj.get('text')
        if isinstance(t, str) and t:
            parts.append(t)
            return
        for key in ('output', 'content', 'message', 'choices'):
            if key in obj:
                collect(obj[key])
    collect(data)
    # 去重保持顺序
    seen = set()
    uniq = []
    for p in parts:
        if p not in seen:
            seen.add(p)
            uniq.append(p)
    return '\n'.join(uniq)

def text_delta_from_chat_chunk(data):
    choices = data.get('choices') or []
    if not choices:
        return ''
    delta = choices[0].get('delta') or {}
    content = delta.get('content', '')
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for item in content:
            if isinstance(item, dict):
                parts.append(item.get('text') or item.get('content') or '')
        return ''.join(parts)
    return str(content) if content else ''

def sse_event(data):
    return f'data: {json.dumps(data, ensure_ascii=False)}\n\n'

def looks_like_generated_image_url(value):
    text = str(value or '').strip()
    if not text:
        return False
    if text.startswith('data:image/'):
        return True
    if text.lower().startswith(('http://', 'https://')):
        return True
    clean = text.split('?', 1)[0].split('#', 1)[0].lower()
    return text.startswith(('http://', 'https://', '/output/', '/assets/')) and re.search('\\.(png|jpe?g|webp|gif|bmp|tiff?)$', clean)

def looks_like_image_base64(value):
    text = str(value or '').strip()
    if not text:
        return False
    if text.startswith('data:image/'):
        return True
    if len(text) < 200:
        return False
    sample = re.sub('\\s+', '', text[:4096])
    if not re.fullmatch('[A-Za-z0-9+/=_-]+', sample):
        return False
    padded = sample.replace('-', '+').replace('_', '/')
    padded += '=' * (-len(padded) % 4)
    try:
        head = base64.b64decode(padded[:256], validate=False)
    except Exception:
        return False
    return head.startswith(b'\x89PNG\r\n\x1a\n') or head.startswith(b'\xff\xd8\xff') or (head.startswith(b'RIFF') and head[8:12] == b'WEBP') or head.startswith(b'GIF87a') or head.startswith(b'GIF89a')

def image_payload_from_string(value, mime_type='image/png', assume_b64=False):
    text = str(value or '').strip()
    if not text:
        return None
    if text.startswith('data:image/'):
        header, sep, encoded = text.partition(',')
        if sep and encoded:
            return {'type': 'b64', 'value': encoded.strip(), 'mime_type': header.split(';', 1)[0].replace('data:', '', 1) or mime_type or 'image/png'}
    if looks_like_generated_image_url(text):
        return {'type': 'url', 'value': text}
    if assume_b64 or looks_like_image_base64(text):
        return {'type': 'b64', 'value': text, 'mime_type': mime_type or 'image/png'}
    return None

def extract_image_flexible(value, depth=0):
    if depth > 8 or value is None:
        return None
    if isinstance(value, str):
        return image_payload_from_string(value)
    if isinstance(value, list):
        for item in value:
            found = extract_image_flexible(item, depth + 1)
            if found:
                return found
        return None
    if not isinstance(value, dict):
        return None
    for key in IMAGE_BASE64_KEY_HINTS:
        item = value.get(key)
        if isinstance(item, str) and item.strip():
            return image_payload_from_string(item, value.get('mime_type') or value.get('mimeType') or 'image/png', assume_b64=True)
    for key in IMAGE_OUTPUT_KEY_HINTS:
        item = value.get(key)
        if isinstance(item, str):
            found = image_payload_from_string(item, value.get('mime_type') or value.get('mimeType') or 'image/png')
            if found:
                return found
        found = extract_image_flexible(item, depth + 1)
        if found:
            return found
    for key in IMAGE_CONTAINER_KEY_HINTS:
        found = extract_image_flexible(value.get(key), depth + 1)
        if found:
            return found
    return None

def extract_images(data):
    found = []
    seen = set()

    def add_image(item):
        if not isinstance(item, dict):
            return
        img_type = item.get('type') or 'url'
        value = item.get('value')
        if not value:
            return
        key = (img_type, value)
        if key in seen:
            return
        seen.add(key)
        found.append(item)

    def collect(value, depth=0):
        if depth > 8 or value is None:
            return
        if isinstance(value, str):
            found = image_payload_from_string(value)
            if found:
                add_image(found)
            return
        if isinstance(value, list):
            for item in value:
                collect(item, depth + 1)
            return
        if not isinstance(value, dict):
            return
        if value.get('type') == 'image_generation_call':
            result = value.get('result')
            if isinstance(result, str) and result.strip():
                add_image(image_payload_from_string(result, value.get('mime_type') or value.get('mimeType') or 'image/png', assume_b64=not looks_like_generated_image_url(result)))
            else:
                collect(result, depth + 1)
        has_direct_url = any((isinstance(value.get(key), str) and looks_like_generated_image_url(value.get(key)) for key in IMAGE_OUTPUT_KEY_HINTS))
        if not has_direct_url:
            for key in IMAGE_BASE64_KEY_HINTS:
                item = value.get(key)
                if isinstance(item, str) and item.strip():
                    add_image(image_payload_from_string(item, value.get('mime_type') or value.get('mimeType') or 'image/png', assume_b64=True))
        for key in IMAGE_OUTPUT_KEY_HINTS:
            item = value.get(key)
            if isinstance(item, str):
                add_image(image_payload_from_string(item, value.get('mime_type') or value.get('mimeType') or 'image/png'))
            else:
                collect(item, depth + 1)
        for key in IMAGE_CONTAINER_KEY_HINTS:
            collect(value.get(key), depth + 1)
    candidates = data.get('candidates') if isinstance(data, dict) else None
    if isinstance(candidates, list):
        for candidate in candidates:
            if not isinstance(candidate, dict):
                continue
            content = candidate.get('content') or {}
            parts = content.get('parts') if isinstance(content, dict) else None
            if not isinstance(parts, list):
                continue
            for part in parts:
                if not isinstance(part, dict):
                    continue
                inline = part.get('inlineData') or part.get('inline_data') or {}
                if not isinstance(inline, dict):
                    continue
                value = inline.get('data')
                if value:
                    add_image({'type': 'b64', 'value': value, 'mime_type': inline.get('mimeType') or inline.get('mime_type') or 'image/png'})
    current = data
    if isinstance(current, dict) and isinstance(current.get('data'), dict) and isinstance(current['data'].get('result'), dict):
        current = current['data']
    if isinstance(current, dict) and isinstance(current.get('result'), dict):
        for item in current['result'].get('images') or []:
            if not isinstance(item, dict):
                collect(item)
                continue
            url = item.get('url')
            if isinstance(url, list):
                for one in url:
                    collect(one)
            else:
                collect(url)
            collect(item)
    collect(data)
    if isinstance(data, dict) and isinstance(data.get('data'), dict) and isinstance(data['data'].get('data'), dict):
        collect(data['data']['data'])
    if found:
        return found
    raise HTTPException(status_code=502, detail='无法识别生图接口返回格式')

def extract_image(data):
    try:
        images = extract_images(data)
        if images:
            return images[0]
    except HTTPException:
        pass
    candidates = data.get('candidates') if isinstance(data, dict) else None
    if isinstance(candidates, list):
        for candidate in candidates:
            if not isinstance(candidate, dict):
                continue
            content = candidate.get('content') or {}
            parts = content.get('parts') if isinstance(content, dict) else None
            if not isinstance(parts, list):
                continue
            for part in parts:
                if not isinstance(part, dict):
                    continue
                inline = part.get('inlineData') or part.get('inline_data') or {}
                if not isinstance(inline, dict):
                    continue
                value = inline.get('data')
                if value:
                    return {'type': 'b64', 'value': value, 'mime_type': inline.get('mimeType') or inline.get('mime_type') or 'image/png'}
    if isinstance(data.get('data'), dict) and isinstance(data['data'].get('result'), dict):
        data = data['data']
    if isinstance(data.get('result'), dict):
        result_images = data['result'].get('images') or []
        if result_images:
            first = result_images[0]
            url = first.get('url')
            if isinstance(url, list) and url:
                return {'type': 'url', 'value': url[0]}
            if isinstance(url, str) and url:
                return {'type': 'url', 'value': url}
    flexible = extract_image_flexible(data)
    if flexible:
        return flexible
    if isinstance(data.get('data'), dict) and isinstance(data['data'].get('data'), dict):
        data = data['data']['data']
    images = data.get('data') or []
    if not isinstance(images, list) or not images:
        raise HTTPException(status_code=502, detail='生图接口没有返回图片数据')
    first = images[0]
    if first.get('url'):
        return {'type': 'url', 'value': first['url']}
    if first.get('b64_json'):
        return {'type': 'b64', 'value': first['b64_json']}
    flexible = extract_image_flexible(first)
    if flexible:
        return flexible
    raise HTTPException(status_code=502, detail='无法识别生图接口返回格式')

def extract_task_id(data):
    if data.get('task_id'):
        return str(data['task_id'])
    if data.get('taskId'):
        return str(data['taskId'])
    if data.get('submit_id'):
        return str(data['submit_id'])
    if data.get('video_id'):
        return str(data['video_id'])
    if data.get('videoId'):
        return str(data['videoId'])
    if data.get('id') and str(data.get('id', '')).startswith('task'):
        return str(data['id'])
    nested = data.get('data')
    if isinstance(nested, list) and nested:
        first = nested[0]
        if isinstance(first, dict):
            return extract_task_id(first)
    if isinstance(nested, dict):
        return extract_task_id(nested)
    return None

def extract_task_id_from_text(text):
    value = str(text or '')
    match = re.search('(?:task_id|taskId|task id)\\s*[=:：]\\s*([A-Za-z0-9_.:-]+)', value, re.IGNORECASE)
    return match.group(1) if match else ''

def images_api_unsupported(response):
    text = str(getattr(response, 'text', '') or '').lower()
    return 'images api is not supported' in text or 'not supported for this platform' in text

def responses_image_size_instruction(size: str) -> str:
    """RS 中转多为网页版逆向：结构化 size 参数（tool.size / 顶层 size / --size 尾注）全被无视，
    只有内部模型能“听懂”的自然语言比例要求有效（实测中文明确说横版+比例+禁止正方形可让
    1:1 变成 3:2 横版）。这里生成中英双语的强化指令。"""
    match = re.match('^\\s*(\\d{2,5})\\s*[xX*]\\s*(\\d{2,5})\\s*$', str(size or ''))
    if not match:
        return ''
    width, height = (int(match.group(1)), int(match.group(2)))
    if width <= 0 or height <= 0:
        return ''
    if width == height:
        return '请生成正方形图片（宽高比 1:1）。Generate a SQUARE image (aspect ratio 1:1).'
    from fractions import Fraction
    ratio = Fraction(width, height).limit_denominator(32)
    rw, rh = (ratio.numerator, ratio.denominator)
    if width > height:
        zh_shape, en_shape = ('横版（宽幅）', 'LANDSCAPE (wide)')
    else:
        zh_shape, en_shape = ('竖版（长幅）', 'PORTRAIT (tall)')
    return f'请生成{zh_shape}图片：宽高比 {rw}:{rh}，目标尺寸为宽 {width} × 高 {height} 像素，绝对不要输出正方形（1:1）。 Generate a {en_shape} image with aspect ratio {rw}:{rh}, target size {width}x{height} pixels (width x height). Never output a square 1:1 image. Do not swap width and height.'

def responses_proxy_tool_size(size: str) -> str:
    """部分 RS 中转把 image_generation.size 当成 height x width；这里只对 RS 模式做兼容翻转。"""
    match = re.match('^\\s*(\\d{2,5})\\s*[xX*]\\s*(\\d{2,5})\\s*$', str(size or ''))
    if not match:
        return str(size or '').strip()
    width, height = (match.group(1), match.group(2))
    return f'{height}x{width}' if width != height else f'{width}x{height}'

def responses_no_image_detail(data) -> str:
    if not isinstance(data, dict):
        return ''
    details = []
    error = data.get('error')
    if isinstance(error, dict):
        msg = error.get('message') or error.get('detail') or error.get('code')
        if msg:
            details.append(str(msg))
    output_text = data.get('output_text')
    if isinstance(output_text, str) and output_text.strip():
        details.append(output_text.strip()[:300])
    output = data.get('output')
    if isinstance(output, list):
        for item in output:
            if not isinstance(item, dict) or item.get('type') != 'image_generation_call':
                continue
            status = item.get('status')
            if status:
                details.append(f'image_generation_call.status={status}')
            item_error = item.get('error')
            if isinstance(item_error, dict):
                msg = item_error.get('message') or item_error.get('detail') or item_error.get('code')
                if msg:
                    details.append(str(msg))
            elif isinstance(item_error, str) and item_error.strip():
                details.append(item_error.strip())
    joined = '；'.join(dict.fromkeys(details))
    return f"RS / Responses 没有返回图片数据{(f'：{joined}' if joined else '')}"

def responses_output_text_image(raw):
    """兜底解析：部分 RS 中转不返回标准 image_generation_call，而是把生图结果
    以 output_text 里的 markdown 图片链接（![...](url)）或裸图片 URL 返回。"""
    texts = []

    def collect(value, depth=0):
        if depth > 6 or len(texts) > 40:
            return
        if isinstance(value, str):
            if value.strip():
                texts.append(value)
            return
        if isinstance(value, list):
            for item in value:
                collect(item, depth + 1)
            return
        if isinstance(value, dict):
            for key in ('output', 'content', 'text', 'output_text', 'message', 'response'):
                if key in value:
                    collect(value[key], depth + 1)
    collect(raw)
    for text in texts:
        match = re.search('!\\[[^\\]]*\\]\\((https?://[^)\\s]+)\\)', text)
        if match:
            return {'type': 'url', 'value': match.group(1)}
        match = re.search('https?://[^\\s)\\"\'<>]+\\.(?:png|jpe?g|webp|gif)(?:\\?[^\\s)\\"\'<>]*)?', text, re.I)
        if match:
            return {'type': 'url', 'value': match.group(0)}
    return None

def _responses_wrap(url, status_code, payload):
    return httpx.Response(status_code, headers={'content-type': 'application/json'}, content=json.dumps(payload, ensure_ascii=False).encode('utf-8'), request=httpx.Request('POST', url))

async def post_openai_responses(client, url, headers, body):
    """RS / Responses 请求。图片编辑经常超过 120 秒，非流式请求会被中转前面的
    Cloudflare 读超时掐断（Error 524）。策略按可靠性排序：
    1) background:true 后台任务 + 轮询 GET /v1/responses/{id}（每个请求都秒回，彻底绕开超时）；
    2) 后台模式被拒（4xx 参数类错误）→ SSE 流式；
    3) 流式也被拒 → 非流式直接请求。
    5xx/超时一律不自动重试，避免上游已开始生成后重复扣费。"""
    bg_body = dict(body)
    bg_body['background'] = True
    try:
        resp = await client.post(url, headers=headers, json=bg_body)
    except httpx.HTTPError as e:
        logger.warning(f'RS background 请求传输失败，改走流式：{e}')
        return await post_openai_responses_stream(client, url, headers, body)
    if resp.status_code in RESPONSES_REJECT_STATUSES:
        logger.warning(f'RS background 模式被拒（{resp.status_code}），改走流式：{resp.text[:200]}')
        return await post_openai_responses_stream(client, url, headers, body)
    if resp.status_code >= 400:
        if resp.status_code == 524:
            return _responses_wrap(url, 502, {'error': {'message': f'中转在 background 模式下仍然 524 超时：该渠道对 /v1/responses 的 background/stream 都不透传，无法完成超过 120 秒的图片编辑。请换支持 Responses 透传的渠道。上游原文：{resp.text[:300]}'}})
        return resp
    try:
        data = resp.json()
    except ValueError:
        return resp
    status = str((data or {}).get('status') or '').lower()
    rid = str((data or {}).get('id') or '').strip()
    if status not in {'queued', 'in_progress', 'processing', 'pending', 'running'} or not rid:
        return resp
    retrieve_url = f"{url.rstrip('/')}/{urllib.parse.quote(rid)}"
    deadline = time.monotonic() + RESPONSES_POLL_MAX_SECONDS
    transient_failures = 0
    while time.monotonic() < deadline:
        await asyncio.sleep(RESPONSES_POLL_INTERVAL)
        try:
            poll = await client.get(retrieve_url, headers=headers)
        except httpx.HTTPError as e:
            transient_failures += 1
            if transient_failures > 5:
                return _responses_wrap(url, 502, {'error': {'message': f'RS 后台任务轮询连续失败：{e}（任务 id={rid}）'}})
            continue
        if poll.status_code >= 400:
            transient_failures += 1
            if transient_failures > 5:
                return _responses_wrap(url, 502, {'error': {'message': f'RS 后台任务轮询失败（{poll.status_code}）：{poll.text[:200]}（任务 id={rid}）'}})
            continue
        transient_failures = 0
        try:
            data = poll.json()
        except ValueError:
            continue
        status = str((data or {}).get('status') or '').lower()
        if status == 'completed':
            return _responses_wrap(url, 200, data)
        if status in {'failed', 'cancelled', 'incomplete'}:
            return _responses_wrap(url, 502, data)
    return _responses_wrap(url, 502, {'error': {'message': f'RS 后台任务超过 {int(RESPONSES_POLL_MAX_SECONDS)}s 仍未完成（任务 id={rid}）'}})

async def post_openai_responses_stream(client, url, headers, body):
    """RS / Responses 的 SSE 流式请求：流式从一开始就持续有事件字节返回，
    不会触发中转的 Cloudflare 120s 读超时。收到 response.completed 后
    把完整 response 对象包装成普通 httpx.Response，下游解析逻辑不变。"""
    request = httpx.Request('POST', url)

    def wrap(status_code, payload):
        return _responses_wrap(url, status_code, payload)
    stream_body = dict(body)
    stream_body['stream'] = True
    try:
        async with client.stream('POST', url, headers=headers, json=stream_body) as resp:
            ctype = (resp.headers.get('content-type') or '').lower()
            if resp.status_code >= 400 or 'text/event-stream' not in ctype:
                content = await resp.aread()
                if resp.status_code in {400, 404, 405, 415, 422}:
                    logger.warning(f'RS 流式请求被拒（{resp.status_code}），回退非流式：{content[:200]!r}')
                    return await client.post(url, headers=headers, json=body)
                return httpx.Response(resp.status_code, headers=resp.headers, content=content, request=request)
            completed = None
            error_payload = None
            stream_images = []
            stream_seen_images = set()

            def remember_stream_image(image):
                if not isinstance(image, dict):
                    return
                value = image.get('value')
                if not value:
                    return
                key = (image.get('type') or 'url', value)
                if key in stream_seen_images:
                    return
                stream_seen_images.add(key)
                stream_images.append(image)

            def remember_stream_images_from(value):
                try:
                    for image in extract_images(value):
                        remember_stream_image(image)
                except HTTPException:
                    pass
            async for line in resp.aiter_lines():
                if not line.startswith('data:'):
                    continue
                chunk = line[5:].strip()
                if not chunk or chunk == '[DONE]':
                    continue
                try:
                    event = json.loads(chunk)
                except ValueError:
                    continue
                if not isinstance(event, dict):
                    continue
                etype = str(event.get('type') or '')
                if etype in {'response.completed', 'response.incomplete'} and isinstance(event.get('response'), dict):
                    completed = event['response']
                elif etype == 'response.failed':
                    failed = event.get('response')
                    error_payload = failed if isinstance(failed, dict) else {'error': {'message': 'response.failed'}}
                elif etype == 'error':
                    message = event.get('message') or event.get('error') or chunk[:300]
                    error_payload = {'error': {'message': str(message)}}
                if isinstance(event.get('item'), dict):
                    item = event['item']
                    if item.get('type') not in {'input_image', 'input_text'}:
                        remember_stream_images_from(item)
                for key in ('partial_image_b64', 'image_b64', 'b64_json'):
                    image = image_payload_from_string(event.get(key), assume_b64=True)
                    if image:
                        remember_stream_image(image)
                for key in ('result', 'image', 'image_url'):
                    image = image_payload_from_string(event.get(key))
                    if image:
                        remember_stream_image(image)
            if completed is not None and stream_images:
                try:
                    has_completed_image = bool(extract_images(completed))
                except HTTPException:
                    has_completed_image = False
                if not has_completed_image:
                    completed = dict(completed)
                    completed['output'] = list(completed.get('output') or [])
                    for image in stream_images:
                        if image.get('type') == 'b64':
                            completed['output'].append({'type': 'image_generation_call', 'status': 'completed', 'result': image.get('value'), 'mime_type': image.get('mime_type') or 'image/png'})
                        else:
                            completed['output'].append({'type': 'image', 'image_url': image.get('value')})
            if completed is None and error_payload is None and stream_images:
                image = stream_images[-1]
                if image.get('type') == 'b64':
                    completed = {'output': [{'type': 'image_generation_call', 'status': 'completed', 'result': image.get('value'), 'mime_type': image.get('mime_type') or 'image/png'}]}
                else:
                    completed = {'output': [{'type': 'image', 'image_url': image.get('value')}]}
            if completed is not None:
                return wrap(200, completed)
            return wrap(502, error_payload or {'error': {'message': 'RS 流式响应结束但没有 response.completed 事件'}})
    except httpx.HTTPError as e:
        logger.warning(f'RS 流式请求传输失败，回退非流式：{e}')
        return await client.post(url, headers=headers, json=body)

def provider_protocol(provider):
    return str((provider or {}).get('protocol') or 'openai').strip().lower()

def normalize_model_protocols(value):
    """规整 {模型名: 协议} 覆盖表，仅保留 openai/gemini。"""
    out = {}
    if isinstance(value, dict):
        for raw_name, raw_proto in value.items():
            name = str(raw_name or '').strip()
            proto = str(raw_proto or '').strip().lower()
            if name and proto in PER_MODEL_PROTOCOL_OPTIONS:
                out[name] = proto
    return out

def normalize_model_name_map(value):
    """规整 {模型ID: 展示名}，只保存真正有意义的显示标签。"""
    normalized = {}
    if isinstance(value, dict):
        for raw_model, raw_label in value.items():
            model = str(raw_model or '').strip()
            label = re.sub('\\s+', ' ', str(raw_label or '').strip())[:160]
            if model and label and (label != model):
                normalized[model] = label
    return normalized

def effective_protocol(provider, model=''):
    """返回某模型实际生效的协议：优先单模型覆盖，否则用平台全局协议。"""
    base = provider_protocol(provider)
    pid = str((provider or {}).get('id') or '').strip().lower()
    if pid in FIXED_PROTOCOL_PROVIDER_IDS:
        return base
    overrides = (provider or {}).get('model_protocols')
    if isinstance(overrides, dict):
        val = str(overrides.get(str(model or '').strip()) or '').strip().lower()
        if val in PER_MODEL_PROTOCOL_OPTIONS:
            return val
    return base

def is_apimart_provider(provider):
    base_url = str((provider or {}).get('base_url') or '').lower()
    return provider_protocol(provider) == 'apimart' or 'apimart.ai' in base_url

def is_fhl_provider(provider):
    base_url = str((provider or {}).get('base_url') or '').strip().lower()
    name = str((provider or {}).get('name') or '').strip().lower()
    try:
        host = urllib.parse.urlsplit(base_url).netloc.lower()
    except Exception:
        host = ''
    return host in {'www.fhl.mom', 'fhl.mom'} or name in {'fhl', 'fhl-image'}

def detect_image_request_mode(base_url='', models=None):
    base = str(base_url or '').strip().lower()
    if 'apihub.agnes-ai.com' in base:
        return 'openai-json'
    for model in models or []:
        if str(model or '').strip().lower().startswith('agnes-image-'):
            return 'openai-json'
    return ''

def effective_image_request_mode(provider, model=''):
    detected = detect_image_request_mode((provider or {}).get('base_url'), [model])
    if detected:
        return detected
    return normalize_image_request_mode((provider or {}).get('image_request_mode'))

def is_gemini_provider(provider):
    return provider_protocol(provider) == 'gemini'

def is_volcengine_provider(provider):
    return provider_protocol(provider) == 'volcengine'

def is_runninghub_provider(provider):
    return provider_protocol(provider) == 'runninghub' or str((provider or {}).get('id') or '').strip().lower() == 'runninghub'

def is_jimeng_provider(provider):
    return provider_protocol(provider) == 'jimeng' or str((provider or {}).get('id') or '').strip().lower() == 'jimeng'

def is_codex_provider(provider):
    return provider_protocol(provider) == 'codex'

def is_gemini_cli_provider(provider):
    provider_id = str((provider or {}).get('id') or '').strip().lower()
    base_url = str((provider or {}).get('base_url') or '').strip().lower()
    return provider_protocol(provider) == 'gemini-cli' or provider_id == 'gemini-cli' or 'gemini-cli' in base_url

def codex_timeout(default=CODEX_DEFAULT_TIMEOUT):
    try:
        return max(30, min(3600, int(os.getenv('CODEX_CLI_TIMEOUT', str(default)) or default)))
    except Exception:
        return default

def codex_model_for_exec(model='', fallback=''):
    value = str(model or fallback or '').strip()
    low = value.lower()
    if not value or low.startswith('$imagegen') or low.startswith('gpt-image'):
        return ''
    return value

def codex_decode_output(stdout, stderr):
    out_text = (stdout or b'').decode('utf-8', errors='replace').strip()
    err_text = (stderr or b'').decode('utf-8', errors='replace').strip()
    return (out_text, err_text)

def gpt_image_2_skill_auth_json(auth_file=''):
    path = str(auth_file or '').strip()
    if not path or not os.path.isfile(path):
        return {}
    try:
        with open(path, 'r', encoding='utf-8-sig') as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}

def gpt_image_2_skill_access_token(auth_data):
    if not isinstance(auth_data, dict):
        return ''
    for key in ('access_token', 'accessToken'):
        value = str(auth_data.get(key) or '').strip()
        if value:
            return value
    tokens = auth_data.get('tokens')
    if isinstance(tokens, dict):
        for key in ('access_token', 'accessToken'):
            value = str(tokens.get(key) or '').strip()
            if value:
                return value
    return ''

def gpt_image_2_skill_model_arg(model='', provider='openai'):
    value = str(model or '').strip()
    low = value.lower()
    provider = str(provider or '').strip().lower()
    if provider == 'codex':
        if not value or low.startswith('$imagegen') or low.startswith('gpt-image'):
            return 'gpt-5.4'
        return value
    if not value or low.startswith('$imagegen'):
        return 'gpt-image-2'
    return value

def gpt_image_2_skill_size_arg(size='', model='', prompt='', provider='openai'):
    text = ' '.join([str(size or ''), str(model or ''), str(prompt or '')]).lower()
    size_text = str(size or '').strip()
    if str(provider or '').strip().lower() == 'codex':
        if '1k' in text or '1024' in text:
            return '1K'
        if '2k' in text or '2048' in text:
            return '2K'
        if '4k' in text or '3840' in text:
            return '4K'
        width, height = parse_size_pair(size_text)
        if 0 < max(width, height) < 1800:
            return '1K'
        if 1800 <= max(width, height) < 3000:
            return '2K'
        return '4K'
    match = re.search('(\\d{3,5})\\s*[x×*]\\s*(\\d{3,5})', size_text, flags=re.I)
    if match:
        width = int(match.group(1))
        height = int(match.group(2))
        if width > 0 and height > 0:
            return normalize_gpt_image_2_size(f'{width}x{height}')
    ratio_match = re.fullmatch('\\s*(\\d{1,2})\\s*:\\s*(\\d{1,2})\\s*', size_text)
    if ratio_match:
        ratio = f'{int(ratio_match.group(1))}:{int(ratio_match.group(2))}'
        options = CHAT_RATIO_SIZE_OPTIONS.get(ratio)
        if options:
            if '4k' in text or '3840' in text:
                return options[-1]
            if '1k' in text or '1024' in text:
                return options[0]
            return options[1] if len(options) > 1 else options[0]
    if '4k' in text or '3840' in text:
        return '4K'
    if '1k' in text or '1024' in text:
        return '1K'
    return '2K'

def gpt_image_2_skill_prompt_arg(prompt='', size='', provider='openai'):
    prompt_text = str(prompt or '').strip()
    if str(provider or '').strip().lower() != 'codex':
        return prompt_text
    size_arg = gpt_image_2_skill_size_arg(size, '', prompt, provider)
    size_text = str(size or '').strip()
    width, height = parse_size_pair(size_text)
    ratio_text = ''
    if width and height:
        divisor = math.gcd(width, height) or 1
        ratio_text = f'{width // divisor}:{height // divisor}'
    else:
        ratio_match = re.fullmatch('\\s*(\\d{1,2})\\s*:\\s*(\\d{1,2})\\s*', size_text)
        if ratio_match:
            width = int(ratio_match.group(1))
            height = int(ratio_match.group(2))
            ratio_text = f'{width}:{height}'
    if not ratio_text:
        return f'{prompt_text} 画质要求：目标输出 {size_arg} 高分辨率图片。 Image quality requirement: output a {size_arg} high-resolution image.'
    orientation_zh = '横版/宽幅' if width > height else '竖版/长幅' if height > width else '正方形'
    orientation_en = 'landscape/wide' if width > height else 'portrait/tall' if height > width else 'square'
    return f'{prompt_text} 画质要求：目标输出 {size_arg} 高分辨率图片。画幅要求：必须生成 {orientation_zh} 图片，宽高比 {ratio_text}。请不要交换宽高，不要输出反向比例。 Image quality requirement: output a {size_arg} high-resolution image. Canvas requirement: generate a {orientation_en} image with aspect ratio {ratio_text}; do not swap width and height.'

def parse_gpt_image_2_skill_output(stdout_text='', stderr_text=''):
    items = []
    for line in (stdout_text or '').splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            items.append(json.loads(line))
        except Exception:
            continue
    if not items and stdout_text:
        try:
            parsed = json.loads(stdout_text)
            items = parsed if isinstance(parsed, list) else [parsed]
        except Exception:
            pass
    paths = []
    for item in items:
        if not isinstance(item, dict):
            continue
        candidates = [item.get('path'), item.get('file'), item.get('output'), item.get('out'), item.get('url')]
        for image in item.get('images') or []:
            if isinstance(image, dict):
                candidates.extend([image.get('path'), image.get('file'), image.get('url')])
            else:
                candidates.append(image)
        for candidate in candidates:
            value = str(candidate or '').strip()
            if value:
                paths.append(value)
    text = stdout_text or stderr_text or ''
    pattern = '([A-Za-z]:\\\\[^\\r\\n\\"\'<>]+\\.(?:png|jpe?g|webp|gif)|/[^\\r\\n\\"\'<>]+\\.(?:png|jpe?g|webp|gif))'
    paths.extend(re.findall(pattern, text, flags=re.I))
    return (items, paths)

def gpt_image_2_skill_failure_message(stdout_text='', stderr_text='', returncode=0):
    combined = '\n'.join([str(stdout_text or '').strip(), str(stderr_text or '').strip()]).strip()
    if not combined:
        return f'exit={returncode}'
    objects = []
    plain_lines = []
    for line in combined.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            objects.append(json.loads(line))
        except Exception:
            plain_lines.append(line)
    if not objects:
        try:
            parsed = json.loads(combined)
            objects = parsed if isinstance(parsed, list) else [parsed]
            plain_lines = []
        except Exception:
            pass
    messages = []
    progress = []
    for item in objects:
        if not isinstance(item, dict):
            continue
        error = item.get('error')
        if isinstance(error, dict):
            msg = error.get('message') or error.get('detail') or error.get('code')
            if msg:
                messages.append(str(msg))
        elif isinstance(error, str) and error.strip():
            messages.append(error.strip())
        if item.get('ok') is False:
            msg = item.get('message') or item.get('detail')
            if msg:
                messages.append(str(msg))
        data = item.get('data')
        if isinstance(data, dict):
            msg = data.get('error') or data.get('message') or data.get('status')
            event_type = str(item.get('type') or data.get('phase') or '').strip()
            if msg and event_type not in {'request.started', 'request_started'}:
                progress.append(f'{event_type}: {msg}' if event_type else str(msg))
    if messages:
        return '；'.join(dict.fromkeys(messages))[:1600]
    if plain_lines:
        return '\n'.join(plain_lines)[:1600]
    if progress:
        return ('只收到了进度事件，没有收到最终错误详情：' + '；'.join(dict.fromkeys(progress)))[:1600]
    return combined[:1600]

def codex_postprocess_image_to_requested_size(path='', requested_size='', provider=''):
    provider_text = str(provider or '').strip().lower()
    if provider_text not in {'codex', 'gemini-cli'}:
        return ''
    width, height = parse_size_pair(requested_size)
    if not width or not height or (not path) or (not os.path.isfile(path)):
        return ''
    try:
        with Image.open(path) as img:
            img.load()
            if img.width == width and img.height == height:
                return ''
            resample = getattr(Image, 'Resampling', Image).LANCZOS
            oriented = ImageOps.exif_transpose(img)
            converted = oriented.convert('RGBA') if oriented.mode in ('RGBA', 'LA', 'P') else oriented.convert('RGB')
            resized = ImageOps.fit(converted, (width, height), method=resample, centering=(0.5, 0.5))
            base, _ext = os.path.splitext(path)
            upscaled_path = f'{base}_upscaled_{width}x{height}.png'
            resized.save(upscaled_path, format='PNG')
            return upscaled_path
    except Exception as exc:
        label = 'Gemini CLI' if provider_text == 'gemini-cli' else 'Codex GPT Image 2'
        logger.warning(f'{label} 图片尺寸后处理失败：{exc}')
        return ''

def codex_models_payload(raw=None):
    all_models = [*CODEX_DEFAULT_IMAGE_MODELS, *CODEX_DEFAULT_CHAT_MODELS]
    return {'ok': True, 'protocol': 'codex', 'status': 200, 'message': 'OpenAI Codex CLI 可用，模型列表来自本机 CLI 默认配置。', 'model_count': len(all_models), 'total': len(all_models), 'image_models': CODEX_DEFAULT_IMAGE_MODELS, 'chat_models': CODEX_DEFAULT_CHAT_MODELS, 'video_models': [], 'all': all_models, 'raw': raw or {}}

def codex_chat_prompt(payload, history_messages=None):
    parts = []
    system_prompt = str(getattr(payload, 'system_prompt', '') or '').strip()
    if system_prompt:
        parts.append(f'系统要求：\n{system_prompt}')
    for item in (history_messages or [])[-MAX_HISTORY_MESSAGES:]:
        role = str(item.get('role') or '').strip()
        content = item.get('content')
        if role in {'user', 'assistant'} and content:
            label = '用户' if role == 'user' else '助手'
            parts.append(f'{label}：\n{content}')
    message = str(getattr(payload, 'message', '') or '').strip()
    parts.append(f'用户：\n{message}')
    parts.append('请直接回答用户，输出纯文本，不要修改项目文件。')
    return '\n\n'.join((part for part in parts if part)).strip()

def antigravity_cli_winget_candidates():
    patterns = [os.path.join(os.path.expanduser('~'), 'AppData', 'Local', 'Microsoft', 'WinGet', 'Packages', 'Google.AntigravityCLI_*', 'agy.exe'), os.path.join(os.getenv('LOCALAPPDATA', ''), 'Microsoft', 'WinGet', 'Packages', 'Google.AntigravityCLI_*', 'agy.exe')]
    candidates = []
    for pattern in patterns:
        if not pattern:
            continue
        candidates.extend(glob.glob(pattern))
    return sorted(dict.fromkeys((path for path in candidates if os.path.exists(path))), reverse=True)

def is_antigravity_cli(exe):
    text = str(exe or '').lower()
    return os.path.basename(text).startswith('agy') or 'antigravity' in text

def gemini_cli_timeout(default=GEMINI_CLI_DEFAULT_TIMEOUT):
    try:
        return max(30, min(3600, int(os.getenv('GEMINI_CLI_TIMEOUT', str(default)) or default)))
    except Exception:
        return default

def gemini_cli_image_timeout():
    raw = os.getenv('ANTIGRAVITY_IMAGE_TIMEOUT') or os.getenv('GEMINI_CLI_IMAGE_TIMEOUT') or '300'
    try:
        return max(60, min(1800, int(raw)))
    except Exception:
        return 300

def gemini_cli_model(model='', fallback=''):
    value = str(model or fallback or '').strip()
    return value or 'auto'

def gemini_cli_text_from_raw(raw, fallback_text=''):
    if isinstance(raw, str):
        return raw.strip()
    if isinstance(raw, dict):
        for key in ('response', 'text', 'content', 'message', 'output'):
            value = raw.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
        candidates = []
        for value in raw.values():
            if isinstance(value, str) and value.strip():
                candidates.append(value.strip())
            elif isinstance(value, list):
                for item in value:
                    if isinstance(item, dict):
                        text = gemini_cli_text_from_raw(item)
                        if text:
                            candidates.append(text)
                    elif isinstance(item, str) and item.strip():
                        candidates.append(item.strip())
        if candidates:
            return '\n'.join(candidates).strip()
    if isinstance(raw, list):
        parts = [gemini_cli_text_from_raw(item) for item in raw]
        return '\n'.join((part for part in parts if part)).strip()
    return str(fallback_text or '').strip()

def gemini_cli_parse_stdout(out_text):
    text = str(out_text or '').strip()
    if not text:
        return ({}, '')
    try:
        raw = json.loads(text)
        return (raw, gemini_cli_text_from_raw(raw, text))
    except Exception:
        pass
    parsed = jimeng_extract_json(text)
    if isinstance(parsed, (dict, list)) and parsed != {'text': text}:
        return (parsed, gemini_cli_text_from_raw(parsed, text))
    return ({'text': text}, text)

def gemini_cli_models_payload(raw=None):
    all_models = [*GEMINI_CLI_DEFAULT_IMAGE_MODELS, *GEMINI_CLI_DEFAULT_CHAT_MODELS]
    all_models = model_list_from_values(all_models)
    return {'ok': True, 'protocol': 'gemini-cli', 'status': 200, 'message': 'Antigravity CLI 可用，模型列表使用 auto 默认模型。', 'model_count': len(all_models), 'total': len(all_models), 'image_models': GEMINI_CLI_DEFAULT_IMAGE_MODELS, 'chat_models': GEMINI_CLI_DEFAULT_CHAT_MODELS, 'video_models': [], 'all': all_models, 'raw': raw or {}}

def gemini_cli_reference_note(reference_images=None):
    refs = []
    temp_paths = []
    for ref in (reference_images or [])[:ONLINE_IMAGE_REFERENCE_MAX]:
        url = ref.get('url') if isinstance(ref, dict) else getattr(ref, 'url', '')
        if not url:
            continue
        refs.append(url)
    return (refs, temp_paths)

def gemini_cli_image_size_instruction(size='', model=''):
    size_text = str(size or '').strip()
    model_text = str(model or '').strip()
    match = re.match('^\\s*(\\d{2,5})\\s*[xX*]\\s*(\\d{2,5})\\s*$', size_text)
    if match:
        width, height = (int(match.group(1)), int(match.group(2)))
        if width > 0 and height > 0:
            orientation = '正方形' if width == height else '横版' if width > height else '竖版'
            return f'目标输出分辨率：{width}x{height} 像素（宽 x 高），画面方向：{orientation}。最终保存到输出目录的图片文件实际像素必须是 {width}x{height}。如果生成器先得到较小图片，请在保存前放大或导出到目标尺寸，不要返回 1024px 小图。'
    combined = f'{size_text} {model_text}'.lower()
    if '4k' in combined:
        return '目标输出为 4K 高分辨率图片；最终保存文件需要达到当前画幅对应的 4K 像素尺寸，不要默认输出 1024px 小图。'
    if '2k' in combined:
        return '目标输出为 2K 高分辨率图片；最终保存文件需要达到当前画幅对应的 2K 像素尺寸，不要默认输出 1024px 小图。'
    return f"尺寸/比例参考：{size_text or 'auto'}。如果可以指定分辨率，请优先输出高分辨率图片。"

def gemini_cli_chat_prompt(payload, history_messages=None):
    parts = []
    system_prompt = str(getattr(payload, 'system_prompt', '') or '').strip()
    if system_prompt:
        parts.append(f'系统要求：\n{system_prompt}')
    for item in (history_messages or [])[-MAX_HISTORY_MESSAGES:]:
        role = str(item.get('role') or '').strip()
        content = item.get('content')
        if role in {'user', 'assistant'} and content:
            label = '用户' if role == 'user' else '助手'
            parts.append(f'{label}：\n{content}')
    message = str(getattr(payload, 'message', '') or '').strip()
    parts.append(f'用户：\n{message}')
    image_values = []
    if hasattr(payload, 'images'):
        image_values.extend([{'url': item} for item in getattr(payload, 'images', None) or [] if item])
    if hasattr(payload, 'reference_images'):
        image_values.extend([ref.dict() for ref in getattr(payload, 'reference_images', None) or [] if getattr(ref, 'url', '')])
    refs = []
    temp_paths = []
    return ('\n\n'.join((part for part in parts if part)).strip(), image_values)

def is_yuli_provider(provider):
    base_url = str((provider or {}).get('base_url') or '').lower()
    return 'yuli.host' in base_url

def is_lingjing_provider(provider):
    base_url = str((provider or {}).get('base_url') or '').lower()
    provider_id = str((provider or {}).get('id') or '').strip().lower()
    return provider_id == 'lingjing' or 'apistudio.vip' in base_url

def is_agnes_provider(provider, model=''):
    base_url = str((provider or {}).get('base_url') or '').lower()
    model_id = str(model or '').strip().lower()
    return 'apihub.agnes-ai.com' in base_url or model_id.startswith('agnes-video-')

def is_kling_model(model=''):
    model_id = str(model or '').strip().lower()
    return (model_id.startswith('kling-v1') or model_id.startswith('kling-video-v1')
            or model_id.startswith('kling-v3') or model_id == 'kling-video-v3'
            or model_id.startswith('kling-v2') or model_id.startswith('kling-video-o1')
            or model_id.startswith('kling-video/v') or model_id.startswith('kling-omni'))

def is_unified_format_video_model(model=''):
    model_id = str(model or '').strip().lower()
    return (model_id.startswith('veo') or model_id.startswith('wan2') or model_id.startswith('wanx')
            or model_id.startswith('doubao-seedance') or model_id.startswith('seedance')
            or model_id.startswith('grok-video') or model_id.startswith('grok-imagine-video')
            or model_id.startswith('grok-1.5-video') or model_id.startswith('grok-omni')
            or model_id.startswith('minimax-hailuo') or model_id == 'hailuo-video'
            or model_id.startswith('sora-2') or model_id == 'sora2')

def video_adapter_label(model='', provider=None):
    model_id = str(model or '').strip().lower()
    provider = provider or {}
    if is_jimeng_provider(provider):
        return ('官方-即梦 CLI', True)
    if is_runninghub_provider(provider):
        return ('官方-RunningHub', True)
    if is_kling_model(model_id):
        return ('官方-可灵', True)
    if is_volcengine_provider(provider):
        return ('官方-火山引擎', True)
    if is_agnes_provider(provider, model_id):
        return ('中转站-Agnes', True)
    if is_lingjing_provider(provider):
        return ('中转站-灵境', True)
    if is_yuli_provider(provider):
        return ('中转站-玉玉', True)
    if is_apimart_veo31_model(model_id):
        return ('中转站-统一格式(veo3.1)', True)
    if is_apimart_provider(provider):
        return ('中转站-统一格式', True)
    if model_id.startswith('wan2') or model_id.startswith('wanx'):
        return ('中转站-统一格式(wan)', True)
    if (model_id.startswith('doubao-seedance')
            or model_id.startswith('seedance')
            or model_id.startswith('grok-video')
            or model_id.startswith('grok-imagine-video')
            or model_id.startswith('grok-1.5-video')
            or model_id.startswith('minimax-hailuo')
            or model_id == 'hailuo-video'):
        return ('中转站-统一格式(seedance/grok/hailuo)', True)
    if is_codex_provider(provider):
        return ('官方-Codex CLI', True)
    if is_gemini_cli_provider(provider):
        return ('官方-Gemini CLI', True)
    known_video_hint = any(tag in model_id for tag in (
        'video', 'veo', 'wan', 'seedance', 'hailuo', 'runway', 'pika', 'sora',
        'grok-imagine', 'luma', 'mochi', 'ltx', 'hunyuan', 'cog', 'vidu', 'minimax-',
        'higgsfield', 'pixverse', 'kling',
    ))
    if known_video_hint:
        declared_models = [str(item or '').strip().lower() for item in (provider.get('video_models') or [])]
        if model_id in declared_models:
            return ('OpenAI 兼容兜底', True)
        return ('未适配', False)
    return ('OpenAI 兼容兜底', True)

def is_kling_provider(provider, model=''):
    model_id = str(model or '').strip().lower()
    return is_kling_model(model_id) or model_id.startswith('kling-')

def avatar_platform_for_provider(provider) -> str:
    if not provider:
        return ''
    if is_apimart_provider(provider):
        return 'apimart'
    if is_volcengine_provider(provider):
        return 'volcengine'
    return ''

def provider_supports_avatar(provider) -> bool:
    return avatar_platform_for_provider(provider) in AVATAR_SUPPORTED_PLATFORMS

def decode_utf16_auto(raw: bytes) -> str:
    try:
        le = raw.decode('utf-16le', errors='ignore')
    except Exception:
        le = ''
    try:
        be = raw.decode('utf-16be', errors='ignore')
    except Exception:
        be = ''

    def ascii_score(text):
        return sum((1 for ch in text if 32 <= ord(ch) <= 126))
    return le if ascii_score(le) >= ascii_score(be) else be

def decode_wsl_output(data: bytes) -> str:
    data = data or b''
    if not data:
        return ''
    if b'\x00' in data[:400]:
        lines = []
        for raw_line in data.splitlines():
            if not raw_line:
                lines.append('')
                continue
            sample = raw_line[:200]
            nul_ratio = sample.count(0) / max(1, len(sample))
            if nul_ratio > 0.2:
                try:
                    lines.append(decode_utf16_auto(raw_line))
                    continue
                except Exception:
                    pass
            lines.append(raw_line.decode('utf-8-sig', errors='ignore'))
        return '\n'.join(lines)
    if b'\x00' in data[:200]:
        try:
            return decode_utf16_auto(data)
        except Exception:
            pass
    return data.decode('utf-8-sig', errors='ignore')

def jimeng_clean_wsl_stderr(text):
    lines = []
    skip_next_warning_context = False
    for line in str(text or '').splitlines():
        clean = line.replace('\x00', '').strip()
        low = clean.lower()
        is_proxy_warning = 'localhost' in low and 'wsl' in low and ('nat' in low or 'proxy' in low or '代理' in clean)
        is_python_warning = 'requestsdependencywarning' in low or (skip_next_warning_context and clean.startswith('warnings.warn('))
        skip_next_warning_context = 'requestsdependencywarning' in low
        if clean and (not is_proxy_warning) and (not is_python_warning):
            lines.append(clean)
    return '\n'.join(lines).strip()

def windows_path_to_wsl(path):
    text = str(path or '').replace('\\', '/')
    match = re.match('^([A-Za-z]):/(.*)$', text)
    if match:
        return f'/mnt/{match.group(1).lower()}/{match.group(2)}'
    return text

def wsl_path_to_windows(path):
    text = str(path or '').strip()
    match = re.match('^/mnt/([A-Za-z])/(.*)$', text)
    if match:
        tail = match.group(2).replace('/', '\\')
        return f'{match.group(1).upper()}:\\{tail}'
    return text

def jimeng_poll_seconds(default=JIMENG_DEFAULT_POLL_SECONDS):
    try:
        return max(1, min(3600, int(os.getenv('JIMENG_POLL_SECONDS', str(default)) or default)))
    except Exception:
        return default

def jimeng_extract_json(text):
    text = str(text or '').strip()
    if not text:
        return {}
    decoder = json.JSONDecoder()
    parsed = []
    for i, ch in enumerate(text):
        if ch not in '[{':
            continue
        try:
            obj, _end = decoder.raw_decode(text[i:])
            if not text[:i].strip():
                return obj
            parsed.append((i, obj))
        except Exception:
            continue

    def score(item):
        _idx, obj = item
        if not isinstance(obj, dict):
            return 1
        keys = {str(key).lower() for key in obj.keys()}
        weight = 0
        for key in ('submit_id', 'gen_status', 'result_json', 'images', 'videos', 'data', 'total_credit'):
            if key in keys:
                weight += 10
        return weight
    return max(parsed, key=score)[1] if parsed else {'text': text}

def jimeng_parse_version(text):
    match = re.search('(\\d+)\\.(\\d+)\\.(\\d+)', str(text or ''))
    if not match:
        return None
    return tuple((int(part) for part in match.groups()))

def jimeng_login_text():
    parts = []
    for key in ('stdout', 'stderr'):
        value = str(JIMENG_LOGIN_SESSION.get(key) or '').strip()
        if value:
            parts.append(value)
    return '\n'.join(parts).strip()

def jimeng_login_qr_from_text(text):
    text = str(text or '')
    candidates = []
    patterns = ['(https?://[^\\s\\"\'<>]+)', '(dreamina://[^\\s\\"\'<>]+)', '(data:image/[^\\s\\"\'<>]+)']
    for pattern in patterns:
        candidates.extend(re.findall(pattern, text))
    for value in candidates:
        if 'login' in value.lower() or 'qr' in value.lower() or value.startswith(('data:image', 'dreamina://')):
            return value
    return candidates[0] if candidates else ''

def jimeng_submit_id(raw):
    found = []

    def visit(value):
        if isinstance(value, dict):
            for key, item in value.items():
                if str(key).lower() in {'submit_id', 'submitid', 'task_id', 'taskid'} and item:
                    found.append(str(item))
                else:
                    visit(item)
        elif isinstance(value, list):
            for item in value:
                visit(item)
    visit(raw)
    return found[0] if found else ''

class IncompatibleGenerationParameterError(Exception):
    """Raised when a user-requested generation parameter is not supported by the selected model.

    Per ADR 0006, the system must stop generation and report the incompatible
    parameter rather than silently modifying or downgrading it.
    """
    def __init__(self, parameter: str, requested_value, supported_values=None, model_id: str = '', detail: str = ''):
        self.parameter = parameter
        self.requested_value = requested_value
        self.supported_values = supported_values
        self.model_id = model_id
        self.detail = detail
        model_part = f" for model '{model_id}'" if model_id else ''
        if supported_values is not None:
            supported_str = ', '.join(str(v) for v in supported_values)
            msg = f"{parameter} '{requested_value}'{model_part} is not supported. Supported values: {supported_str}"
        else:
            msg = f"{parameter} '{requested_value}'{model_part} is not supported"
        if detail:
            msg += f". {detail}"
        super().__init__(msg)


class JimengPendingError(Exception):
    """即梦任务还在云端排队/生成（轮询超时但未失败）。submit_id 可用于后续续查。"""

    def __init__(self, submit_id, kind='image', queue_info=None, raw=None):
        self.submit_id = str(submit_id or '')
        self.kind = kind or 'image'
        self.queue_info = queue_info if isinstance(queue_info, dict) else {}
        self.raw = raw
        super().__init__(f'jimeng pending submit_id={self.submit_id}')

def jimeng_queue_info(raw):
    """从即梦原始返回里就近取出 queue_info（含 queue_idx/queue_length/queue_status）。"""
    found = []

    def visit(value):
        if isinstance(value, dict):
            qi = value.get('queue_info')
            if isinstance(qi, dict) and qi:
                found.append(qi)
            for item in value.values():
                if isinstance(item, (dict, list)):
                    visit(item)
        elif isinstance(value, list):
            for item in value:
                visit(item)
    visit(raw)
    return found[0] if found else {}

def jimeng_pending_payload(exc: 'JimengPendingError'):
    qi = exc.queue_info or {}
    idx = qi.get('queue_idx')
    length = qi.get('queue_length')
    if idx is not None and length is not None:
        msg = f'即梦云端排队中（第 {idx}/{length} 位），任务未丢失，可继续等待或手动查询。submit_id={exc.submit_id}'
    else:
        msg = f'即梦任务仍在生成中，任务未丢失。submit_id={exc.submit_id}'
    return {'jimeng_pending': True, 'submit_id': exc.submit_id, 'kind': exc.kind, 'queue_info': qi, 'message': msg}

async def jimeng_pending_exception_handler(request: Request, exc: JimengPendingError):
    return JSONResponse(status_code=202, content=jimeng_pending_payload(exc))

def jimeng_failure_reason(raw):
    found = []

    def visit(value):
        if isinstance(value, dict):
            status = str(value.get('gen_status') or value.get('status') or '').strip().lower()
            reason = value.get('fail_reason') or value.get('failReason') or value.get('error') or value.get('message') or value.get('msg')
            if reason and (status in {'fail', 'failed', 'error'} or 'fail' in str(reason).lower() or 'invalid param' in str(reason).lower()):
                found.append(str(reason))
            for item in value.values():
                if isinstance(item, (dict, list)):
                    visit(item)
        elif isinstance(value, list):
            for item in value:
                visit(item)
    visit(raw)
    return found[0] if found else ''

def jimeng_collect_media_values(value, outputs):
    media_ext = re.compile('\\.(png|jpe?g|webp|gif|bmp|mp4|webm|mov|m4v|avi|mkv)(\\?|#|$)', re.I)
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return
        if text.startswith(('http://', 'https://', '/output/', '/assets/', 'file://')) or media_ext.search(text):
            outputs.append(text)
        return
    if isinstance(value, list):
        for item in value:
            jimeng_collect_media_values(item, outputs)
        return
    if isinstance(value, dict):
        for key in ('url', 'urls', 'image', 'images', 'image_url', 'image_urls', 'video', 'videos', 'video_url', 'video_urls', 'output', 'outputs', 'result', 'results', 'file', 'files', 'path', 'paths', 'download_url', 'download_urls', 'downloadUrl', 'file_path', 'filePath'):
            if key in value:
                jimeng_collect_media_values(value.get(key), outputs)
        for item in value.values():
            if isinstance(item, (dict, list)):
                jimeng_collect_media_values(item, outputs)

def jimeng_output_values(raw):
    outputs = []
    jimeng_collect_media_values(raw, outputs)
    deduped = []
    for value in outputs:
        if value not in deduped:
            deduped.append(value)
    return deduped

def jimeng_ratio_from_size(size, fallback='1:1'):
    width, height = parse_size_pair(size)
    if not width or not height:
        return fallback
    ratio = width / max(1, height)
    left, right = min(JIMENG_RATIO_CHOICES, key=lambda item: abs(ratio - item[0] / item[1]))
    return f'{left}:{right}'

def jimeng_normalize_image_model(model):
    match = re.search('(\\d+\\.\\d+)', str(model or ''))
    return match.group(1) if match else ''

def jimeng_image_model_version(model, mode='text2image'):
    version = jimeng_normalize_image_model(model)
    allowed = JIMENG_IMAGE2IMAGE_MODELS if mode == 'image2image' else JIMENG_TEXT2IMAGE_MODELS
    return version if version in allowed else ''

def jimeng_image_resolution(model, size, mode='text2image'):
    text = str(model or '').lower()
    if '4k' in text:
        desired = '4k'
    elif '1k' in text:
        desired = '1k'
    elif '2k' in text:
        desired = '2k'
    else:
        width, height = parse_size_pair(size)
        desired = '4k' if max(width, height) > 2048 else '2k'
    version = jimeng_normalize_image_model(model)
    if mode == 'image2image':
        return '4k' if desired == '4k' else '2k'
    if version in ('3.0', '3.1'):
        return '1k' if desired == '1k' else '2k'
    return '4k' if desired == '4k' else '2k'

def jimeng_video_resolution(model, resolution):
    version = jimeng_video_model_version(model)
    requested = str(resolution or '').strip().upper()
    if requested not in {'480P', '720P', '1080P'}:
        text = str(model or '').lower()
        requested = '1080P' if '1080' in text else '720P'
    if requested == '1080P' and version in JIMENG_VIDEO_1080P_MODELS:
        return '1080P'
    return '720P'

def jimeng_video_duration_range(model):
    version = jimeng_video_model_version(model)
    if version in ('3.0', '3.0fast', '3.0pro'):
        return (3, 10)
    if version == '3.5pro':
        return (4, 12)
    return (4, 15)

def jimeng_video_duration(duration, model=None):
    low, high = jimeng_video_duration_range(model)
    default = max(low, min(high, 5))
    try:
        text = str(duration).strip() if duration is not None else ''
        value = default if text == '' else int(text)
    except Exception:
        value = default
    return max(low, min(high, value))

def jimeng_transition_duration(total_duration, transition_count):
    count = max(1, int(transition_count or 1))
    try:
        total = float(total_duration or 5)
    except Exception:
        total = 5.0
    return max(0.5, min(8.0, total / count))

def jimeng_video_model_version(model):
    value = str(model or '').strip()
    low = value.lower()
    aliases = {'seedance2.0fast_vip': 'seedance2.0fast_vip', 'seedance2.0_vip': 'seedance2.0_vip', 'seedance2.0fast': 'seedance2.0fast', 'seedance2.0': 'seedance2.0', '3.0_fast': '3.0fast', '3.0fast': '3.0fast', '3.0_pro': '3.0pro', '3.0pro': '3.0pro', '3.5_pro': '3.5pro', '3.5pro': '3.5pro', '3.0': '3.0'}
    for key, mapped in aliases.items():
        if key in low:
            return mapped
    return ''

def jimeng_video_resolution_arg(model, resolution):
    return jimeng_video_resolution(model, resolution).lower()

def jimeng_video_ratio_arg(aspect_ratio):
    value = str(aspect_ratio or '').strip()
    allowed = {'1:1', '3:4', '16:9', '4:3', '9:16', '21:9'}
    if value in allowed:
        return value
    return ''

def jimeng_append_model_resolution_args(args, payload: CanvasVideoRequest, include_model=False):
    model_version = jimeng_video_model_version(payload.model)
    if include_model and model_version:
        args.append(f'--model_version={model_version}')
    if payload.resolution:
        args.append(f'--video_resolution={jimeng_video_resolution_arg(payload.model, payload.resolution)}')

def jimeng_video_ref_role(ref):
    role = getattr(ref, 'role', '')
    if isinstance(ref, dict):
        role = ref.get('role', role)
    return str(role or '').lower()

def jimeng_video_ref_url(ref):
    url = getattr(ref, 'url', '')
    if isinstance(ref, dict):
        url = ref.get('url', url)
    return str(url or '').strip()

def image_task_data(payload):
    if isinstance(payload, dict) and isinstance(payload.get('data'), dict):
        return payload['data']
    return payload if isinstance(payload, dict) else {}

def image_task_status(payload):
    task_data = image_task_data(payload)
    return str(task_data.get('status') or task_data.get('task_status') or '').upper()

def image_task_fail_reason(payload):
    task_data = image_task_data(payload)
    error = task_data.get('error') if isinstance(task_data.get('error'), dict) else {}
    return task_data.get('fail_reason') or task_data.get('message') or error.get('message') or (payload.get('message') if isinstance(payload, dict) else '') or '生图任务失败'

async def httpx_request_with_transient_retries(client, method, url, attempts=2, retry_delay=1.2, **kwargs):
    attempts = max(1, int(attempts or 1))
    last_exc = None
    retry_statuses = {502, 503, 504, 520, 522, 524}
    for attempt in range(attempts):
        try:
            response = await client.request(method, url, **kwargs)
            if response.status_code in retry_statuses and attempt + 1 < attempts:
                await asyncio.sleep(retry_delay * (attempt + 1))
                continue
            return response
        except (httpx.RemoteProtocolError, httpx.ReadError, httpx.ConnectError, httpx.ConnectTimeout, httpx.ReadTimeout, httpx.PoolTimeout) as exc:
            last_exc = exc
            if attempt + 1 >= attempts:
                raise
            logger.info(f'[HTTPX-RETRY] {method} {url} transient error: {exc}; retry {attempt + 2}/{attempts}')
            await asyncio.sleep(retry_delay * (attempt + 1))
    if last_exc:
        raise last_exc
    raise httpx.HTTPError(f'请求失败：{method} {url}')

def project_media_adapter_project_id(value: str) -> str:
    """Return the project encoded in an internal/legacy project-media reference."""
    text = str(value or '').strip()
    legacy_handle = project_media_adapter_reference_from_url(text)
    if legacy_handle:
        text = legacy_handle
    parsed = urllib.parse.urlsplit(text)
    if parsed.scheme != PROJECT_MEDIA_ADAPTER_SCHEME:
        return ''
    return urllib.parse.unquote(parsed.netloc or '')

def require_project_media_adapter_scope(value: str) -> None:
    """Allow project media only while the owning project task is executing."""
    project_id = project_media_adapter_project_id(value)
    if not project_id:
        return
    active_project_id = PROJECT_MEDIA_ADAPTER_PROJECT.get()
    if not active_project_id:
        raise ProjectStoreError('项目媒体适配句柄只能由内部项目任务使用')
    if not hmac.compare_digest(project_id, active_project_id):
        raise ProjectStoreError('项目媒体适配句柄不属于当前项目任务')

def require_project_media_values_scope(values: List[str]) -> None:
    """Enforce the internal project capability for any project-owned media input."""
    for value in values:
        if not project_media_adapter_project_id(value):
            continue
        active_project_id = PROJECT_MEDIA_ADAPTER_PROJECT.get()
        try:
            require_project_media_adapter_scope(value)
        except ProjectStoreError as exc:
            code = 'project_media_handle_project_mismatch' if active_project_id else 'project_media_handle_requires_project_task'
            raise HTTPException(status_code=400, detail={'code': code, 'message': str(exc)}) from exc

def project_media_adapter_reference(project_id: str, asset_id: str) -> str:
    """Create an internal, non-HTTP handle for one project-owned media file."""
    project = str(project_id or '').strip()
    asset = str(asset_id or '').strip()
    if not project or not asset:
        raise ProjectStoreError('项目媒体适配句柄缺少项目或资源标识')
    return f"{PROJECT_MEDIA_ADAPTER_SCHEME}://{urllib.parse.quote(project, safe='')}/{urllib.parse.quote(asset, safe='')}"

def project_media_adapter_reference_from_url(value: str) -> str:
    """Convert a legacy project-media URL to the internal adapter handle."""
    clean = urllib.parse.unquote(str(value or '').split('?', 1)[0]).replace('\\', '/')
    match = re.fullmatch('/api/project-library/([^/]+)/media/([^/]+)', clean)
    if not match:
        return ''
    return project_media_adapter_reference(match.group(1), match.group(2))

def collect_local_media_urls(value: Any) -> List[str]:
    """Collect local /assets and /output URLs from nested canvas log payloads."""
    urls = []
    if isinstance(value, str):
        text = value.strip()
        if text.startswith(('/assets/', '/output/', '/api/storage-files/')):
            urls.append(text)
    elif isinstance(value, dict):
        for item in value.values():
            urls.extend(collect_local_media_urls(item))
    elif isinstance(value, (list, tuple)):
        for item in value:
            urls.extend(collect_local_media_urls(item))
    return urls

def image_has_alpha(img: Image.Image) -> bool:
    if img.mode in ('RGBA', 'LA'):
        return True
    if img.mode == 'P':
        return 'transparency' in img.info
    return False

def storage_file_item(kind, root, path):
    rel = os.path.relpath(path, root).replace('\\', '/')
    try:
        stat = os.stat(path)
    except OSError:
        return None
    item = {'id': f'{kind}:{rel}', 'kind': kind, 'rel': rel, 'name': os.path.basename(path), 'folder': os.path.dirname(rel).replace('\\', '/'), 'url': f"/api/storage-files/{kind}/{urllib.parse.quote(rel, safe='/')}", 'size': stat.st_size, 'created_at': stat.st_mtime}
    try:
        with Image.open(path) as img:
            item['width'], item['height'] = img.size
    except Exception:
        pass
    return item

def is_video_preview_file(path: str) -> bool:
    return os.path.splitext(str(path or '').split('?', 1)[0])[1].lower() in {'.mp4', '.webm', '.mov', '.m4v', '.avi', '.mkv'}

def filename_from_media_url(url: str, fallback: str='download.bin') -> str:
    path = urllib.parse.urlsplit(str(url or '')).path
    name = os.path.basename(urllib.parse.unquote(path))
    return sanitize_export_filename(name or fallback, fallback)

def fetch_remote_media_bytes(url: str, timeout: float=30.0, max_bytes: int=200 * 1024 * 1024):
    text = rewrite_runninghub_file_url(str(url or '').strip())
    parsed = urllib.parse.urlparse(text)
    if parsed.scheme not in ('http', 'https') or not parsed.netloc:
        return None
    with requests.get(text, stream=True, timeout=timeout, headers={'User-Agent': 'Firefly-Canvas/1.0'}) as response:
        response.raise_for_status()
        content_type = response.headers.get('content-type') or 'application/octet-stream'
        chunks = []
        total = 0
        for chunk in response.iter_content(chunk_size=1024 * 256):
            if not chunk:
                continue
            total += len(chunk)
            if total > max_bytes:
                raise HTTPException(status_code=413, detail='文件太大，无法下载')
            chunks.append(chunk)
        return (b''.join(chunks), content_type)

def origin_from_url(value):
    parsed = urllib.parse.urlparse(str(value or ''))
    if parsed.scheme not in ('http', 'https') or not parsed.netloc:
        return ''
    return f'{parsed.scheme}://{parsed.netloc}'.lower()

def ensure_same_origin_request(request: Request):
    host = str(request.headers.get('host') or '').lower()
    try:
        hostname = (urllib.parse.urlsplit(f'//{host}').hostname or '').lower()
    except ValueError:
        hostname = ''
    if hostname not in {'localhost', '127.0.0.1', '::1', '0.0.0.0', '::'}:
        raise HTTPException(status_code=403, detail='只允许从当前应用页面操作本机数据')
    expected = f'{request.url.scheme}://{host}'.lower()
    origin = origin_from_url(request.headers.get('origin', ''))
    referer = origin_from_url(request.headers.get('referer', ''))
    actual = origin or referer
    if actual != expected:
        raise HTTPException(status_code=403, detail='只允许从当前应用页面操作本机数据')

def normalize_local_image_path(value):
    text = str(value or '').strip().strip('"').strip("'")
    if not text:
        raise HTTPException(status_code=400, detail='本地图片路径为空')
    if text.lower().startswith('file:'):
        parsed = urllib.parse.urlparse(text)
        if parsed.scheme.lower() != 'file':
            raise HTTPException(status_code=400, detail='只支持本地图片路径')
        if parsed.netloc and re.match('^[a-zA-Z]:$', parsed.netloc) and (os.name == 'nt'):
            path = f"{parsed.netloc}{urllib.request.url2pathname(parsed.path or '')}"
        elif parsed.netloc and parsed.netloc.lower() not in ('localhost',):
            raise HTTPException(status_code=400, detail='只支持本机图片路径')
        else:
            path = urllib.request.url2pathname(parsed.path or '')
    else:
        path = text
    path = path.strip().strip('"').strip("'")
    if re.match('^/[a-zA-Z]:[\\\\/]', path):
        path = path[1:]
    if re.match('^[a-zA-Z]:[\\\\/]', path):
        return os.path.abspath(path)
    if path.startswith('/') and os.name != 'nt':
        return os.path.abspath(path)
    raise HTTPException(status_code=400, detail='只支持本机绝对图片路径')

def migrate_asset_item_registrations(item):
    """一个素材可注册到多平台：把旧的单平台扁平字段折叠进 item['registrations'][platform]，再清掉旧字段。"""
    if not isinstance(item, dict):
        return
    regs = item.get('registrations')
    if not isinstance(regs, dict):
        regs = {}
    legacy_platform = str(item.get('platform') or '').strip()
    if legacy_platform and legacy_platform not in regs and (item.get('asset_uri') or item.get('avatar_task_id')):
        regs[legacy_platform] = {'provider_id': item.get('provider_id') or '', 'project_name': item.get('project_name') or 'default', 'task_id': item.get('avatar_task_id') or '', 'status': item.get('avatar_status') or '', 'detail': item.get('avatar_detail') or '', 'asset_uri': item.get('asset_uri') or '', 'asset_id': item.get('asset_id') or '', 'registered_at': item.get('registered_at') or 0}
    item['registrations'] = regs if isinstance(regs, dict) else {}
    for key in AVATAR_LEGACY_FLAT_FIELDS:
        item.pop(key, None)

def sort_asset_library_items(lib):
    cats = list(lib.get('categories', []))
    for library in lib.get('libraries', []) if isinstance(lib.get('libraries'), list) else []:
        cats.extend(library.get('categories') or [])
    seen = set()
    for cat in cats:
        if id(cat) in seen:
            continue
        seen.add(id(cat))
        items = cat.get('items')
        if isinstance(items, list):

            def created_at_key(item):
                if not isinstance(item, dict):
                    return 0
                try:
                    return int(float(item.get('created_at') or 0))
                except (TypeError, ValueError):
                    return 0
            items.sort(key=created_at_key, reverse=True)

def asset_library_media_kind(path: str, content_type: str='') -> str:
    ext = os.path.splitext(path or '')[1].lower()
    ct = (content_type or '').lower()
    if ext in {'.mp4', '.webm', '.mov', '.m4v', '.avi', '.mkv'} or ct.startswith('video/'):
        return 'video'
    if ext in {'.mp3', '.wav', '.m4a', '.aac', '.ogg', '.flac'} or ct.startswith('audio/'):
        return 'audio'
    return 'image'

def asset_library_safe_extension(path: str, kind: str) -> str:
    ext = os.path.splitext(path or '')[1].lower()
    allowed = {'image': {'.png', '.jpg', '.jpeg', '.webp', '.gif'}, 'video': {'.mp4', '.webm', '.mov', '.m4v', '.avi', '.mkv'}, 'audio': {'.mp3', '.wav', '.m4a', '.aac', '.ogg', '.flac'}}
    fallback = {'image': '.png', 'video': '.mp4', 'audio': '.mp3'}
    return ext if ext in allowed.get(kind, allowed['image']) else fallback.get(kind, '.png')

def _safe_asset_tag(value, limit=24):
    text = re.sub('\\s+', ' ', str(value or '').strip())
    text = re.sub('^[#＃]+', '', text).strip(' ,，、;；|/')
    return text[:limit]

def find_asset_category(lib, category_id):
    for cat in lib.get('categories', []):
        if cat.get('id') == category_id:
            return cat
    return None

def shared_folder_abs(entry):
    rel = (entry or {}).get('rel') or ''
    return os.path.normpath(os.path.join(BASE_DIR, rel))

def shared_resolve_register(path):
    """校验 path 必须位于项目目录内、是一个存在的子目录（非项目根）。返回 (abs, rel)。"""
    raw = (path or '').strip().strip('"').strip("'")
    if not raw:
        raise HTTPException(status_code=400, detail='请提供文件夹路径')
    candidate = raw if os.path.isabs(raw) else os.path.join(BASE_DIR, raw)
    abs_path = os.path.normpath(os.path.abspath(candidate))
    base = os.path.normpath(os.path.abspath(BASE_DIR))
    try:
        common = os.path.commonpath([abs_path, base])
    except ValueError:
        raise HTTPException(status_code=400, detail='只允许登记项目目录内的文件夹')
    if common != base:
        raise HTTPException(status_code=400, detail='只允许登记项目目录内的文件夹')
    if abs_path == base:
        raise HTTPException(status_code=400, detail='不能直接登记项目根目录，请选择子文件夹')
    if not os.path.isdir(abs_path):
        raise HTTPException(status_code=400, detail='文件夹不存在')
    rel = os.path.relpath(abs_path, base)
    return (abs_path, rel)

def shared_child_abs(folder_abs, rel):
    """把相对 folder_abs 的子路径解析为绝对路径，并防止越界访问。"""
    rel = (rel or '').replace('\\', '/').lstrip('/')
    abs_path = os.path.normpath(os.path.join(folder_abs, rel))
    base = os.path.normpath(os.path.abspath(folder_abs))
    try:
        common = os.path.commonpath([os.path.abspath(abs_path), base])
    except ValueError:
        raise HTTPException(status_code=400, detail='非法路径')
    if common != base:
        raise HTTPException(status_code=400, detail='非法路径')
    return abs_path

def image_path_to_data_url(path, max_size=1024):
    if max_size:
        try:
            with Image.open(path) as img:
                img.load()
                if max(img.size) > max_size:
                    img.thumbnail((max_size, max_size), Image.LANCZOS)
                if img.mode not in ('RGB', 'RGBA'):
                    img = img.convert('RGB')
                buf = BytesIO()
                fmt = 'PNG' if img.mode == 'RGBA' else 'JPEG'
                img.save(buf, format=fmt, quality=88 if fmt == 'JPEG' else None)
                encoded = base64.b64encode(buf.getvalue()).decode('ascii')
                mime = 'image/png' if fmt == 'PNG' else 'image/jpeg'
                return f'data:{mime};base64,{encoded}'
        except Exception as e:
            logger.warning(f'shared caption image resize failed: {e}')
    with open(path, 'rb') as f:
        encoded = base64.b64encode(f.read()).decode('ascii')
    return f'data:{content_type_for_path(path)};base64,{encoded}'

def scan_shared_tree(folder_id, folder_abs, rel_prefix='', display='', counter=None):
    """递归扫描共享文件夹，返回 {id,name,path,items,children}。"""
    if counter is None:
        counter = {'n': 0}
    node = {'id': f"{folder_id}:{rel_prefix or '__root__'}", 'name': display or os.path.basename(folder_abs) or folder_abs, 'path': rel_prefix, 'items': [], 'children': []}
    try:
        entries = sorted(os.scandir(folder_abs), key=lambda e: (not e.is_dir(), e.name.lower()))
    except OSError:
        return node
    for ent in entries:
        if counter['n'] >= SHARED_SCAN_MAX_ENTRIES:
            break
        if ent.name.startswith('.') or ent.name.startswith('._'):
            continue
        child_rel = f'{rel_prefix}/{ent.name}'.lstrip('/')
        if ent.is_dir():
            child = scan_shared_tree(folder_id, ent.path, child_rel, ent.name, counter)
            if child['items'] or child['children']:
                node['children'].append(child)
        elif ent.is_file():
            ext = os.path.splitext(ent.name)[1].lower()
            if ext not in SHARED_MEDIA_EXTS:
                continue
            counter['n'] += 1
            try:
                st = ent.stat()
                size = st.st_size
                mtime = int(st.st_mtime * 1000)
            except OSError:
                size = 0
                mtime = 0
            node['items'].append({'id': f'{folder_id}:{child_rel}', 'name': ent.name, 'url': f'/api/shared-folders/{folder_id}/file?path={urllib.parse.quote(child_rel)}', 'kind': asset_library_media_kind(ent.name), 'size': size, 'lastModified': mtime, 'relativePath': child_rel, 'folderId': folder_id})
    return node

def builtin_prompt_templates():
    try:
        template_path = prompt_template_markdown_path()
        if not template_path:
            return []
        with open(template_path, 'r', encoding='utf-8') as f:
            return parse_prompt_template_markdown(f.read())
    except Exception as e:
        logger.warning(f'读取提示词模板失败: {e}')
        return []

def normalize_prompt_category_id(category='custom'):
    category_id = re.sub('[^A-Za-z0-9_-]+', '_', str(category or 'custom'))[:40] or 'custom'
    return 'custom' if category_id in {'mine', 'my', 'personal'} else category_id

def seed_system_prompt_library():
    return {'id': 'system', 'name': '系统提示词库', 'type': 'prompt', 'items': builtin_prompt_templates(), 'categories': defaultPromptTemplateCategories()}

def defaultPromptTemplateCategories():
    return [{'id': 'view', 'name': '视角'}, {'id': 'storyboard', 'name': '分镜'}, {'id': 'character', 'name': '角色'}, {'id': 'product', 'name': '产品'}, {'id': 'lighting', 'name': '光影'}, {'id': 'custom', 'name': '我的'}]

def normalize_prompt_template_categories(*category_lists, include_defaults=True):
    normalized = []
    seen = set()

    def add_category(category):
        if not isinstance(category, dict):
            return
        cat_id = normalize_prompt_category_id(category.get('id') or category.get('name') or 'custom')
        if cat_id in seen:
            return
        seen.add(cat_id)
        name = sanitize_asset_name(category.get('name') or cat_id, cat_id)
        normalized.append({'id': cat_id, 'name': name})
    for categories in category_lists:
        if isinstance(categories, list):
            for category in categories:
                add_category(category)
    if include_defaults and (not normalized):
        for category in defaultPromptTemplateCategories():
            add_category(category)
    return normalized

def find_prompt_library(data, library_id=''):
    if not isinstance(data, dict):
        return None
    libraries = data.get('libraries') if isinstance(data.get('libraries'), list) else []
    library_id = str(library_id or data.get('active_library_id') or '').strip()
    return next((item for item in libraries if item.get('id') == library_id), None) or (libraries[0] if libraries else None)

def sanitize_asset_name(name, fallback='asset'):
    name = re.sub('[\\\\/:*?"<>|]+', '_', str(name or fallback)).strip()
    return name[:120] or fallback

def content_type_for_path(path):
    ext = os.path.splitext(path)[1].lower()
    if ext in ['.mp4', '.m4v']:
        return 'video/mp4'
    if ext == '.webm':
        return 'video/webm'
    if ext == '.mov':
        return 'video/quicktime'
    if ext == '.avi':
        return 'video/x-msvideo'
    if ext == '.mkv':
        return 'video/x-matroska'
    if ext == '.flv':
        return 'video/x-flv'
    if ext == '.mp3':
        return 'audio/mpeg'
    if ext == '.wav':
        return 'audio/wav'
    if ext == '.m4a':
        return 'audio/mp4'
    if ext == '.aac':
        return 'audio/aac'
    if ext == '.ogg':
        return 'audio/ogg'
    if ext == '.flac':
        return 'audio/flac'
    if ext == '.gif':
        return 'image/gif'
    if ext in ['.jpg', '.jpeg']:
        return 'image/jpeg'
    if ext == '.webp':
        return 'image/webp'
    if ext == '.txt':
        return 'text/plain; charset=utf-8'
    if ext == '.json':
        return 'application/json; charset=utf-8'
    if ext == '.csv':
        return 'text/csv; charset=utf-8'
    if ext == '.md':
        return 'text/markdown; charset=utf-8'
    if ext == '.srt':
        return 'application/x-subrip; charset=utf-8'
    if ext == '.vtt':
        return 'text/vtt; charset=utf-8'
    if ext == '.png':
        return 'image/png'
    return 'application/octet-stream'

def is_image_reference(ref):
    if not isinstance(ref, dict):
        return False
    kind = str(ref.get('kind') or '').strip().lower()
    mime = str(ref.get('mime') or '').strip().lower()
    url = str(ref.get('url') or '').strip().lower()
    if kind:
        return kind == 'image'
    if mime:
        return mime.startswith('image/')
    return bool(re.search('\\.(png|jpe?g|webp|gif|bmp|tiff?)(\\?|#|$)', url))

def image_references(refs):
    return [ref for ref in refs or [] if is_image_reference(ref)]

def _xml_local_name(tag):
    return str(tag or '').rsplit('}', 1)[-1]

def _xlsx_join_text(node):
    parts = []
    for child in node.iter():
        if _xml_local_name(child.tag) == 't' and child.text:
            parts.append(child.text)
    return ''.join(parts).strip()

def _xlsx_shared_strings(archive):
    try:
        raw = archive.read('xl/sharedStrings.xml')
    except KeyError:
        return []
    root = ET.fromstring(raw)
    values = []
    for node in root:
        if _xml_local_name(node.tag) == 'si':
            values.append(_xlsx_join_text(node))
    return values

def _xlsx_sheet_paths(archive):
    names = set(archive.namelist())
    fallback = [(os.path.basename(name).rsplit('.', 1)[0], name) for name in sorted(names) if re.match('xl/worksheets/sheet\\d+\\.xml$', name)]
    try:
        workbook = ET.fromstring(archive.read('xl/workbook.xml'))
        rels = ET.fromstring(archive.read('xl/_rels/workbook.xml.rels'))
        rel_map = {}
        for rel in rels:
            rid = rel.attrib.get('Id')
            target = rel.attrib.get('Target') or ''
            if not rid or not target:
                continue
            target = target.lstrip('/')
            if not target.startswith('xl/'):
                target = f'xl/{target}'
            rel_map[rid] = target.replace('\\', '/')
        result = []
        for sheet in workbook.iter():
            if _xml_local_name(sheet.tag) != 'sheet':
                continue
            title = sheet.attrib.get('name') or 'Sheet'
            rid = sheet.attrib.get('{http://schemas.openxmlformats.org/officeDocument/2006/relationships}id')
            target = rel_map.get(rid, '')
            if target in names:
                result.append((title, target))
        return result or fallback
    except Exception:
        return fallback

def _xlsx_cell_text(cell, shared_strings):
    cell_type = cell.attrib.get('t', '')
    value_node = None
    formula_node = None
    inline_node = None
    for child in cell:
        name = _xml_local_name(child.tag)
        if name == 'v':
            value_node = child
        elif name == 'f':
            formula_node = child
        elif name == 'is':
            inline_node = child
    raw_value = (value_node.text if value_node is not None else '') or ''
    formula = (formula_node.text if formula_node is not None else '') or ''
    if cell_type == 's' and raw_value.isdigit():
        idx = int(raw_value)
        value = shared_strings[idx] if 0 <= idx < len(shared_strings) else raw_value
    elif cell_type == 'inlineStr' and inline_node is not None:
        value = _xlsx_join_text(inline_node)
    elif cell_type == 'b':
        value = 'TRUE' if raw_value == '1' else 'FALSE' if raw_value == '0' else raw_value
    else:
        value = raw_value
    value = str(value or '').strip()
    if formula and value:
        return f'{value} [={formula}]'
    if formula:
        return f'={formula}'
    return value

def read_xlsx_attachment(path, limit=MAX_ATTACHMENT_TEXT_CHARS):
    parts = []
    used = 0
    with zipfile.ZipFile(path) as archive:
        shared = _xlsx_shared_strings(archive)
        sheets = _xlsx_sheet_paths(archive)
        media_count = sum((1 for name in archive.namelist() if name.startswith('xl/media/') and os.path.splitext(name)[1].lower() in XLSX_IMAGE_EXTS))
        parts.append(f'Excel 工作簿：{os.path.basename(path)}')
        if media_count:
            parts.append(f'内嵌图片数量：{media_count}（已作为图片参考一并提供给模型）')
        for sheet_index, (sheet_name, sheet_path) in enumerate(sheets[:EXCEL_MAX_SHEETS], start=1):
            try:
                root = ET.fromstring(archive.read(sheet_path))
            except Exception:
                continue
            rows = []
            for row in root.iter():
                if _xml_local_name(row.tag) != 'row':
                    continue
                cells = []
                for cell in row:
                    if _xml_local_name(cell.tag) != 'c':
                        continue
                    ref = cell.attrib.get('r') or ''
                    value = _xlsx_cell_text(cell, shared)
                    if value:
                        cells.append(f'{ref}={value}' if ref else value)
                    if len(cells) >= EXCEL_MAX_COLS_PER_ROW:
                        break
                if cells:
                    row_ref = row.attrib.get('r') or str(len(rows) + 1)
                    rows.append(f'第 {row_ref} 行：' + ' | '.join(cells))
                if len(rows) >= EXCEL_MAX_ROWS_PER_SHEET:
                    break
            if rows:
                section = f'\n工作表 {sheet_index}：{sheet_name}\n' + '\n'.join(rows)
            else:
                section = f'\n工作表 {sheet_index}：{sheet_name}\n（未读取到非空单元格）'
            if used + len(section) > limit:
                remain = max(0, limit - used)
                if remain:
                    parts.append(section[:remain])
                parts.append('\n（Excel 内容较长，已截断）')
                break
            parts.append(section)
            used += len(section)
    return '\n'.join(parts).strip()[:limit]

def xlsx_embedded_image_data_urls(path, max_images=4, max_size=1536):
    urls = []
    try:
        with zipfile.ZipFile(path) as archive:
            media = [name for name in archive.namelist() if name.startswith('xl/media/') and os.path.splitext(name)[1].lower() in XLSX_IMAGE_EXTS]
            for name in sorted(media)[:max_images]:
                try:
                    raw = archive.read(name)
                    with Image.open(BytesIO(raw)) as img:
                        img.load()
                        if max(img.size) > max_size:
                            img.thumbnail((max_size, max_size), Image.LANCZOS)
                        if img.mode in ('RGBA', 'LA') or (img.mode == 'P' and 'transparency' in img.info):
                            bg = Image.new('RGB', img.size, (255, 255, 255))
                            bg.paste(img.convert('RGBA'), mask=img.convert('RGBA').split()[-1])
                            img = bg
                        elif img.mode != 'RGB':
                            img = img.convert('RGB')
                        buf = BytesIO()
                        img.save(buf, format='JPEG', quality=88, optimize=True)
                        encoded = base64.b64encode(buf.getvalue()).decode('ascii')
                        urls.append(f'data:image/jpeg;base64,{encoded}')
                except Exception as exc:
                    logger.warning(f'[chat] failed to extract xlsx image {name}: {exc}')
    except Exception as exc:
        logger.warning(f'[chat] failed to read xlsx images {path}: {exc}')
    return urls

def read_text_attachment(path, limit=MAX_ATTACHMENT_TEXT_CHARS):
    ext = os.path.splitext(path or '')[1].lower()
    if not path or not os.path.isfile(path):
        return ''
    try:
        if ext == '.xlsx':
            return read_xlsx_attachment(path, limit)
        if ext == '.xls':
            return '这是旧版 .xls 二进制 Excel 文件，当前内置解析器暂不支持直接读取内容。请另存为 .xlsx 后重新上传。'
        if ext == '.docx':
            with zipfile.ZipFile(path) as archive:
                raw = archive.read('word/document.xml')
            root = ET.fromstring(raw)
            parts = []
            for node in root.iter():
                if node.tag.endswith('}t') and node.text:
                    parts.append(node.text)
                elif node.tag.endswith('}p'):
                    parts.append('\n')
            return html.unescape(''.join(parts)).strip()[:limit]
        if ext in TEXT_ATTACHMENT_EXTS:
            with open(path, 'rb') as f:
                data = f.read(min(os.path.getsize(path), limit * 4))
            for encoding in ('utf-8-sig', 'utf-8', 'gb18030'):
                try:
                    return data.decode(encoding, errors='strict').strip()[:limit]
                except UnicodeDecodeError:
                    continue
            return data.decode('utf-8', errors='replace').strip()[:limit]
    except Exception as exc:
        logger.warning(f'[chat] failed to read attachment text {path}: {exc}')
    return ''

def is_private_asset_url(value: str) -> bool:
    return isinstance(value, str) and value.strip().startswith('asset://')

def looks_like_image_media_url(value: str) -> bool:
    text = str(value or '').strip().lower()
    if not text:
        return False
    if text.startswith('data:image/'):
        return True
    if text.startswith('asset://'):
        return False
    path = urllib.parse.urlparse(text).path or text
    return bool(re.search('\\.(png|jpe?g|webp|gif|bmp|tiff)$', path))

def volcengine_content_role(role: str, kind: str='image') -> Optional[str]:
    value = str(role or '').strip().lower()
    allowed = {'first_frame', 'last_frame', 'reference_image', 'reference_video', 'reference_audio', 'video', 'audio', 'image'}
    if value in allowed:
        if value == 'audio' and kind == 'audio':
            return 'reference_audio'
        return 'reference_video' if value == 'video' and kind == 'video' else value
    if kind == 'audio':
        return 'reference_audio'
    if kind == 'video':
        return 'reference_video'
    return None

def volcengine_video_duration(duration) -> int:
    try:
        value = int(duration)
    except Exception:
        value = 5
    return max(1, min(60, value))

def volcengine_video_resolution(value: str) -> str:
    text = str(value or '').strip().lower()
    aliases = {'': '', 'auto': '', '480': '480p', '720': '720p', '1080': '1080p'}
    text = aliases.get(text, text)
    return text if text in {'480p', '720p', '1080p'} else ''

def is_volcengine_seedance2_model(model: str) -> bool:
    value = str(model or '').strip().lower().replace('_', '-').replace('.', '-')
    return 'seedance-2-0' in value

def compress_data_url_image(value, max_size=1536, jpeg_quality=88):
    if not isinstance(value, str) or not value.startswith('data:image/') or ';base64,' not in value:
        return value
    header, encoded = value.split(';base64,', 1)
    try:
        raw = base64.b64decode(encoded)
        with Image.open(BytesIO(raw)) as img:
            img.load()
            if max_size and max(img.size) > max_size:
                img.thumbnail((max_size, max_size), Image.LANCZOS)
            has_alpha = img.mode in ('RGBA', 'LA') or (img.mode == 'P' and 'transparency' in img.info)
            if has_alpha:
                if img.mode != 'RGBA':
                    img = img.convert('RGBA')
                fmt, mime = ('PNG', 'image/png')
            else:
                if img.mode != 'RGB':
                    img = img.convert('RGB')
                fmt, mime = ('JPEG', 'image/jpeg')
            buf = BytesIO()
            if fmt == 'JPEG':
                img.save(buf, format=fmt, quality=jpeg_quality, optimize=True)
            else:
                img.save(buf, format=fmt, optimize=True)
            return f"data:{mime};base64,{base64.b64encode(buf.getvalue()).decode('ascii')}"
    except Exception as e:
        logger.warning(f'data url image compress failed, fallback to raw: {e}')
        return value

def valid_video_image_input(value: str) -> bool:
    if not isinstance(value, str):
        return False
    value = value.strip()
    return value.startswith('http://') or value.startswith('https://') or value.startswith('asset://') or (value.startswith('data:image/') and ';base64,' in value)

def valid_apimart_video_image_input(value: str) -> bool:
    if not isinstance(value, str):
        return False
    value = value.strip()
    return value.startswith('http://') or value.startswith('https://') or value.startswith('asset://')

def apply_trusted_asset_prompt_index(prompt: str, image_count: int, video_count: int, audio_count: int) -> str:
    """可信素材模式下，按平台规则在 prompt 里补「图片N/视频N/音频N」索引。
    若用户已手动引用了某类素材（如已写「图片1」），则不重复追加该类。"""
    text = str(prompt or '').strip()
    segments = []
    for label, count in (('图片', image_count), ('视频', video_count), ('音频', audio_count)):
        if count <= 0:
            continue
        if any((f'{label}{i}' in text for i in range(1, count + 1))):
            continue
        segments.append('、'.join((f'{label}{i}' for i in range(1, count + 1))))
    if not segments:
        return text
    hint = '参考素材：' + '，'.join(segments) + '。'
    return f'{text}\n{hint}' if text else hint

def public_media_url_suffix() -> str:
    token = str(os.getenv('PUBLIC_MEDIA_TOKEN') or '').strip()
    return f'?token={urllib.parse.quote(token)}' if token else ''

def apimart_video_duration(duration) -> int:
    try:
        value = int(duration)
    except Exception:
        value = 5
    return max(4, min(15, value))

def apimart_veo31_duration(duration) -> int:
    try:
        value = int(duration)
    except Exception:
        value = 8
    return max(4, min(8, value))

def is_apimart_veo31_model(model: str) -> bool:
    return str(model or '').strip().lower().startswith('veo3.1')

def apimart_veo31_model(model: str) -> str:
    value = str(model or '').strip().lower()
    aliases = {'veo3.1': 'veo3.1-fast', 'veo3.1-pro': 'veo3.1-quality', 'veo3.1-preview': 'veo3.1-fast'}
    value = aliases.get(value, value or 'veo3.1-fast')
    allowed = {'veo3.1-fast', 'veo3.1-quality', 'veo3.1-lite'}
    return value if value in allowed else 'veo3.1-fast'

def apimart_veo31_aspect(aspect: str) -> str:
    value = str(aspect or '16:9').strip()
    return value if value in {'16:9', '9:16'} else '16:9'

def apimart_veo31_resolution(resolution: str) -> str:
    value = str(resolution or '').strip().lower()
    aliases = {'': '720p', 'auto': '720p', '480p': '720p', '780p': '720p', '1080': '1080p', '4k': '4k'}
    value = aliases.get(value, value)
    return value if value in {'720p', '1080p', '4k'} else '720p'

def apimart_upload_file_payload(path: str):
    """Return (filename, bytes, content_type), keeping APIMart VEO images under the documented 10MB limit."""
    max_bytes = 9500000
    size = os.path.getsize(path)
    if size <= max_bytes:
        with open(path, 'rb') as fh:
            return (os.path.basename(path), fh.read(), content_type_for_path(path))
    with Image.open(path) as img:
        img = img.convert('RGBA')
        bg = Image.new('RGB', img.size, (255, 255, 255))
        bg.paste(img, mask=img.split()[-1])
        quality = 92
        while quality >= 62:
            buf = BytesIO()
            bg.save(buf, format='JPEG', quality=quality, optimize=True)
            data = buf.getvalue()
            if len(data) <= max_bytes:
                name = os.path.splitext(os.path.basename(path))[0] + '.jpg'
                return (name, data, 'image/jpeg')
            quality -= 8
    raise ValueError('图片超过 10MB，且压缩后仍无法满足 VEO3.1 图片限制')

def invalid_video_image_preview(value: str) -> str:
    text = str(value or '')
    if text.startswith('data:'):
        return text.split(';base64,', 1)[0] + ';base64,...'
    return text[:120]

def extract_apimart_asset_url(payload):
    if isinstance(payload, list):
        for item in payload:
            found = extract_apimart_asset_url(item)
            if found:
                return found
        return ''
    if not isinstance(payload, dict):
        return ''
    url_keys = ('url', 'asset_url', 'assetUrl', 'uri', 'file_url', 'fileUrl')
    for key in url_keys:
        value = str(payload.get(key) or '').strip()
        if valid_apimart_video_image_input(value):
            return value
    id_keys = ('asset_id', 'assetId', 'file_id', 'fileId', 'id')
    for key in id_keys:
        value = str(payload.get(key) or '').strip()
        if value:
            return value if value.startswith('asset://') else f'asset://{value}'
    for key in ('data', 'file', 'asset', 'result'):
        found = extract_apimart_asset_url(payload.get(key))
        if found:
            return found
    return ''

def apimart_upload_payload_from_bytes(data: bytes, mime: str, name_hint: str='image'):
    """把内存中的图片字节按 APIMart 的 10MB 限制压缩为可上传 payload。"""
    max_bytes = 9500000
    ext = mimetypes.guess_extension(mime or 'image/png') or '.png'
    if len(data) <= max_bytes and (mime or '').lower() in ('image/png', 'image/jpeg', 'image/webp'):
        return (f'{name_hint}{ext}', data, mime or 'image/png')
    with Image.open(BytesIO(data)) as img:
        has_alpha = img.mode in ('RGBA', 'LA') or (img.mode == 'P' and 'transparency' in img.info)
        if has_alpha:
            base = img.convert('RGBA')
            bg = Image.new('RGB', base.size, (255, 255, 255))
            bg.paste(base, mask=base.split()[-1])
            target = bg
        else:
            target = img.convert('RGB')
        quality = 92
        while quality >= 62:
            buf = BytesIO()
            target.save(buf, format='JPEG', quality=quality, optimize=True)
            payload = buf.getvalue()
            if len(payload) <= max_bytes:
                return (f'{name_hint}.jpg', payload, 'image/jpeg')
            quality -= 8
    raise ValueError('data URL 图片超过 10MB，且压缩后仍无法满足 APIMart 限制')

def apimart_upload_raw_file_payload(path: str):
    with open(path, 'rb') as fh:
        return (os.path.basename(path), fh.read(), content_type_for_path(path))

def is_transient_tls_error(exc) -> bool:
    """识别可重试的瞬时 TLS/传输错误，如 SSLV3_ALERT_BAD_RECORD_MAC、EOF occurred 等，
    这类错误多由连接池中被污染/复用坏掉的 TLS 连接引起，换新连接重试通常即可成功。"""
    if isinstance(exc, httpx.TransportError):
        return True
    msg = f'{type(exc).__name__}: {exc}'.upper()
    return any((token in msg for token in ('SSL', 'BAD RECORD MAC', 'EOF OCCURRED', 'DECRYPTION FAILED', 'WRONG VERSION NUMBER')))

async def apimart_upload_post(client, upload_url, headers, file_tuple, timeout=60):
    """上传文件到 APIMart，对瞬时 TLS 错误自动重试；重试时改用全新连接，避免复用坏掉的 TLS 连接。
    file_tuple 形如 (filename, content_bytes, content_type)，content 为已读入内存的 bytes，可跨重试复用。"""
    last_exc = None
    for attempt in range(APIMART_UPLOAD_RETRY_ATTEMPTS):
        files = {'file': file_tuple}
        try:
            if attempt == 0:
                return await client.post(upload_url, headers=headers, files=files, timeout=timeout)
            async with httpx.AsyncClient(timeout=httpx.Timeout(connect=20.0, read=max(120.0, float(timeout)), write=120.0, pool=20.0), follow_redirects=True) as fresh:
                return await fresh.post(upload_url, headers=headers, files=files, timeout=timeout)
        except Exception as e:
            if not is_transient_tls_error(e) or attempt == APIMART_UPLOAD_RETRY_ATTEMPTS - 1:
                raise
            last_exc = e
            logger.warning(f'APIMart 上传遇到瞬时 TLS 错误，换新连接重试（第 {attempt + 1} 次）：{e}')
            await asyncio.sleep(0.6 * (attempt + 1))
    if last_exc:
        raise last_exc

def apimart_avatar_asset_type(kind: str) -> str:
    return {'video': 'Video', 'audio': 'Audio'}.get(str(kind or '').lower(), 'Image')

def extract_apimart_avatar_asset_uri(payload) -> str:
    """从 /v1/tasks 审核结果里取出 asset://<id> 形式的可信素材 URI。"""
    if isinstance(payload, list):
        for item in payload:
            found = extract_apimart_avatar_asset_uri(item)
            if found:
                return found
        return ''
    if not isinstance(payload, dict):
        return ''
    for key in ('asset_url', 'assetUrl', 'uri', 'url'):
        value = str(payload.get(key) or '').strip()
        if value.startswith('asset://'):
            return value
    for key in ('usable_assets', 'assets', 'result', 'data'):
        found = extract_apimart_avatar_asset_uri(payload.get(key))
        if found:
            return found
    asset_id = str(payload.get('asset_id') or payload.get('assetId') or '').strip()
    if asset_id:
        return f'asset://{asset_id}'
    return ''

def _volc_hmac(key: bytes, msg: str) -> bytes:
    return hmac.new(key, msg.encode('utf-8'), hashlib.sha256).digest()

def volcengine_sign_v4_headers(ak: str, sk: str, action: str, body_str: str, service: str=VOLCENGINE_ARK_ASSET_SERVICE, region: str=VOLCENGINE_ARK_ASSET_REGION, version: str=VOLCENGINE_ARK_ASSET_VERSION, host: str=VOLCENGINE_ARK_ASSET_HOST) -> Dict[str, str]:
    """火山引擎 OpenAPI 签名 V4（POST + JSON body）。返回需随请求发送的鉴权头。"""
    method = 'POST'
    content_type = 'application/json'
    now = datetime.datetime.now(datetime.timezone.utc)
    x_date = now.strftime('%Y%m%dT%H%M%SZ')
    short_date = x_date[:8]
    payload_hash = hashlib.sha256(body_str.encode('utf-8')).hexdigest()
    canonical_query = f"Action={urllib.parse.quote(action, safe='')}&Version={urllib.parse.quote(version, safe='')}"
    canonical_headers = f'content-type:{content_type}\nhost:{host}\nx-content-sha256:{payload_hash}\nx-date:{x_date}\n'
    signed_headers = 'content-type;host;x-content-sha256;x-date'
    canonical_request = '\n'.join([method, '/', canonical_query, canonical_headers, signed_headers, payload_hash])
    algorithm = 'HMAC-SHA256'
    credential_scope = f'{short_date}/{region}/{service}/request'
    string_to_sign = '\n'.join([algorithm, x_date, credential_scope, hashlib.sha256(canonical_request.encode('utf-8')).hexdigest()])
    k_date = _volc_hmac(sk.encode('utf-8'), short_date)
    k_region = _volc_hmac(k_date, region)
    k_service = _volc_hmac(k_region, service)
    k_signing = _volc_hmac(k_service, 'request')
    signature = hmac.new(k_signing, string_to_sign.encode('utf-8'), hashlib.sha256).hexdigest()
    authorization = f'{algorithm} Credential={ak}/{credential_scope}, SignedHeaders={signed_headers}, Signature={signature}'
    return {'Content-Type': content_type, 'Host': host, 'X-Date': x_date, 'X-Content-Sha256': payload_hash, 'Authorization': authorization}

async def upload_video_to_litterbox(path: str, source_url: str) -> Dict[str, str]:
    upload_url = os.getenv('LITTERBOX_UPLOAD_URL', 'https://litterbox.catbox.moe/resources/internals/api.php').strip() or 'https://litterbox.catbox.moe/resources/internals/api.php'
    time_value = os.getenv('LITTERBOX_TIME', '72h').strip() or '72h'
    ct = content_type_for_path(path)
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(connect=20.0, read=600.0, write=600.0, pool=20.0), follow_redirects=True) as client:
            with open(path, 'rb') as fh:
                files = {'fileToUpload': (os.path.basename(path), fh, ct)}
                data = {'reqtype': 'fileupload', 'time': time_value}
                response = await client.post(upload_url, data=data, files=files)
        if not response.is_success:
            raise HTTPException(status_code=response.status_code, detail=f'Litterbox 上传失败：{response.text[:300]}')
        direct_url = response.text.strip().splitlines()[0].strip()
        if not re.match('^https?://', direct_url, re.I):
            raise HTTPException(status_code=502, detail=f'Litterbox 返回了无法识别的链接：{response.text[:300]}')
        return {'url': direct_url, 'source': source_url, 'name': os.path.basename(path), 'expires': time_value, 'service': 'litterbox'}
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f'Litterbox 上传异常：{exc}') from exc

async def upload_video_to_temp_sh(path: str, source_url: str) -> Dict[str, str]:
    upload_url = os.getenv('TEMP_SH_UPLOAD_URL', 'https://temp.sh/upload').strip() or 'https://temp.sh/upload'
    ct = content_type_for_path(path)
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(connect=20.0, read=600.0, write=600.0, pool=20.0), follow_redirects=True) as client:
            with open(path, 'rb') as fh:
                files = {'file': (os.path.basename(path), fh, ct)}
                response = await client.post(upload_url, files=files)
        if not response.is_success:
            raise HTTPException(status_code=response.status_code, detail=f'Temp.sh 上传失败：{response.text[:300]}')
        direct_url = response.text.strip().splitlines()[0].strip()
        if not re.match('^https?://', direct_url, re.I):
            raise HTTPException(status_code=502, detail=f'Temp.sh 返回了无法识别的链接：{response.text[:300]}')
        return {'url': direct_url, 'source': source_url, 'name': os.path.basename(path), 'expires': '3 days', 'service': 'temp.sh'}
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f'Temp.sh 上传异常：{exc}') from exc

def parse_size_pair(size):
    match = re.fullmatch('\\s*(\\d+)\\s*[xX*]\\s*(\\d+)\\s*', str(size or ''))
    if not match:
        return (0, 0)
    return (int(match.group(1)), int(match.group(2)))

def snap_size_to_multiple(size, multiple=16):
    width, height = parse_size_pair(size)
    if not width or not height:
        return size
    step = max(1, int(multiple or 16))
    snapped_w = max(step, int(math.ceil(width / step) * step))
    snapped_h = max(step, int(math.ceil(height / step) * step))
    return f'{snapped_w}x{snapped_h}'

def chat_prompt_size_override(message, current_size=''):
    text = str(message or '')
    direct = re.search('(?<!\\d)([1-9]\\d{2,4})\\s*[xX×*]\\s*([1-9]\\d{2,4})(?!\\d)', text)
    if direct:
        width, height = (int(direct.group(1)), int(direct.group(2)))
        if width >= 256 and height >= 256:
            return f'{width}x{height}'
    normalized = text.replace('：', ':').replace('﹕', ':').replace('∶', ':').replace('比', ':').replace('／', '/').replace('/', ':')
    ratio_match = re.search('(?<!\\d)(1|2|3|4|9|16)\\s*:\\s*(1|2|3|4|9|16)(?!\\d)', normalized)
    if not ratio_match:
        return ''
    ratio = f'{int(ratio_match.group(1))}:{int(ratio_match.group(2))}'
    options = CHAT_RATIO_SIZE_OPTIONS.get(ratio)
    if not options:
        return ''
    width, height = parse_size_pair(current_size)
    wants_4k = bool(re.search('(?i)\\b4\\s*k\\b|4K|超清|超高分辨率', text))
    wants_2k = bool(re.search('(?i)\\b2\\s*k\\b|2K|高清|高分辨率', text))
    long_edge = max(width, height)
    if wants_4k or long_edge >= 2400:
        return options[2] if len(options) > 2 else options[-1]
    if wants_2k or long_edge >= 1500:
        return options[1] if len(options) > 1 else options[0]
    return options[0]

def is_gpt_image_2_model(model):
    raw = str(model or '').strip().lower()
    normalized = re.sub('[^a-z0-9]+', '-', raw).strip('-')
    compact = re.sub('[^a-z0-9]+', '', raw)
    return normalized == 'gpt-image-2' or normalized.startswith('gpt-image-2-') or normalized.endswith('-gpt-image-2') or ('-gpt-image-2-' in normalized) or (compact == 'gptimage2') or compact.startswith('gptimage2') or compact.endswith('gptimage2')

def normalize_gpt_image_2_size(size):
    width, height = parse_size_pair(size)
    if not width or not height:
        return size or 'auto'
    if max(width, height) <= GPT_IMAGE2_MAX_EDGE and width * height <= GPT_IMAGE2_MAX_PIXELS:
        return f'{width}x{height}'
    ratio = width / height
    if ratio > 3:
        width = height * 3
    elif ratio < 1 / 3:
        height = width * 3
    scale = min(1.0, GPT_IMAGE2_MAX_EDGE / max(width, height), (GPT_IMAGE2_MAX_PIXELS / max(1, width * height)) ** 0.5)
    width = max(16, int(width * scale // 16) * 16)
    height = max(16, int(height * scale // 16) * 16)
    if width * height < GPT_IMAGE2_MIN_PIXELS:
        grow = (GPT_IMAGE2_MIN_PIXELS / max(1, width * height)) ** 0.5
        width = int((width * grow + 15) // 16) * 16
        height = int((height * grow + 15) // 16) * 16
    return f'{width}x{height}'

def gpt_image_2_size_error_message(size):
    width, height = parse_size_pair(size)
    display_size = size or '未指定'
    return f'GPT-Image-2 不支持当前尺寸 {display_size}：它有最大像素限制（长边最大 3840、总像素约 829 万）。请改用更小的尺寸，或切换到 nano-banana 生成更高分辨率。'

def gpt_image_2_size_exceeds_supported(size):
    width, height = parse_size_pair(size)
    return bool(width and height and (max(width, height) > GPT_IMAGE2_MAX_EDGE or width * height > GPT_IMAGE2_MAX_PIXELS))

def apimart_size_resolution(size):
    width, height = parse_size_pair(size)
    if not width or not height:
        raw = str(size or '').strip().lower()
        if raw in {'1k', '2k', '4k'}:
            return ('1:1', raw)
        if re.fullmatch('(auto|\\d+\\s*:\\s*\\d+)', raw):
            return (raw.replace(' ', ''), '1k')
        return ('1:1', '1k')
    long_edge = max(width, height)
    pixels = width * height
    if long_edge >= 3000 or pixels > 4500000:
        resolution = '4k'
    elif long_edge >= 1800 or pixels > 1800000:
        resolution = '2k'
    else:
        resolution = '1k'
    common = [(1, 1, '1:1'), (3, 2, '3:2'), (2, 3, '2:3'), (4, 3, '4:3'), (3, 4, '3:4'), (5, 4, '5:4'), (4, 5, '4:5'), (16, 9, '16:9'), (9, 16, '9:16'), (2, 1, '2:1'), (1, 2, '1:2'), (3, 1, '3:1'), (1, 3, '1:3'), (21, 9, '21:9'), (9, 21, '9:21')]
    ratio = width / height
    best = min(common, key=lambda item: abs(ratio - item[0] / item[1]))
    return (best[2], resolution)

def is_volcengine_seedream_model(model):
    value = str(model or '').strip().lower()
    return 'seedream' in value or 'doubao-seedream' in value

def normalize_volcengine_size(size, model=''):
    width, height = parse_size_pair(size)
    raw = str(size or '').strip().lower()
    if not width or not height:
        if raw == '4k':
            return '4096x4096'
        if raw == '2k':
            return '2048x2048'
        return '2048x2048' if is_volcengine_seedream_model(model) else size or '1024x1024'
    if not is_volcengine_seedream_model(model):
        return f'{width}x{height}'
    ratio = width / max(1, height)
    best_ratio = min(VOLCENGINE_RATIO_CHOICES, key=lambda item: abs(ratio - item[0] / item[1]))
    rw, rh = (best_ratio[0], best_ratio[1])
    scale = max((VOLCENGINE_MIN_PIXELS / max(1, rw * rh)) ** 0.5, VOLCENGINE_MIN_EDGE / max(1, min(rw, rh)))
    target_w = rw * scale
    target_h = rh * scale
    cap = min(1.0, VOLCENGINE_MAX_EDGE / max(target_w, target_h))
    target_w *= cap
    target_h *= cap
    snapped_w = max(64, int(target_w // 16) * 16)
    snapped_h = max(64, int(target_h // 16) * 16)
    while snapped_w * snapped_h < VOLCENGINE_MIN_PIXELS:
        if snapped_w <= snapped_h:
            snapped_w += 16
        else:
            snapped_h += 16
        if max(snapped_w, snapped_h) > VOLCENGINE_MAX_EDGE:
            break
    return f'{snapped_w}x{snapped_h}'

def friendly_image_error_detail(text, size='', model=''):
    text = str(text or '')
    lower_text = text.lower()
    if is_gpt_image_2_model(model) and gpt_image_2_size_exceeds_supported(size):
        return gpt_image_2_size_error_message(size)
    mentions_size = any((token in lower_text for token in ['size', 'resolution', 'dimension']))
    is_gpt_size_error = is_gpt_image_2_model(model) and mentions_size and ('invalid' in lower_text or 'unsupported' in lower_text or 'not supported' in lower_text or ('exceed' in lower_text) or ('must be one of' in lower_text))
    m = re.search('longest edge must be less than or equal to (\\d+)', text)
    if m and is_gpt_image_2_model(model):
        limit = m.group(1)
        return f"GPT-Image-2 不支持当前尺寸 {size or '未指定'}：最长边超过 {limit}px。如果需要更高分辨率，请切换到 nano-banana；继续使用 GPT 时请调低分辨率。"
    if m:
        limit = m.group(1)
        return f'该模型不支持当前分辨率：最长边超过 {limit}px。请把图片分辨率调低（例如换到 2K 或更小），或更换支持高分辨率的模型。'
    if 'image size must be at least' in lower_text:
        pixel_match = re.search('at least (\\d+) pixels', lower_text)
        pixels = pixel_match.group(1) if pixel_match else '3686400'
        return f"该模型要求更高分辨率，当前尺寸 {size or '过小'} 不满足最低像素要求（至少 {pixels} 像素）。火山 Seedream 5.0 建议从 2K 起步。"
    if is_gpt_size_error or (('invalid size' in lower_text or 'invalid_value' in lower_text) and is_gpt_image_2_model(model)):
        return gpt_image_2_size_error_message(size)
    if 'invalid size' in lower_text or 'invalid_value' in lower_text:
        return f"该模型不支持当前尺寸：{size or '未指定'}。请尝试更换分辨率或模型。"
    if 'inputtextsensitivecontentdetected' in lower_text or 'policyviolation' in lower_text or 'copyright restrictions' in lower_text:
        return '上游内容安全拦截了这段提示词，原因偏向版权/敏感内容限制。请改写提示词，避免直接出现具体 IP、角色名、品牌名、影视/动漫作品名，改成风格特征描述再试。'
    if 'rejected by the safety system' in lower_text or 'image_generation_user_error' in lower_text or 'safety system' in lower_text or ('content_policy_violation' in lower_text) or ('content policy' in lower_text):
        return '上游（Azure/OpenAI 系）内容安全系统拒绝了本次生图请求。可能是提示词或参考图触发了内容审核。请改写提示词、避免敏感/暴力/成人/名人/版权角色等描述；若使用了人物参考图，可换一张图再试。这是上游平台的审核策略，并非本系统报错。'
    if 'rate limit' in lower_text or '429' in lower_text:
        return '请求过于频繁，已被上游限流，请稍后再试。'
    if 'unauthorized' in lower_text or '401' in lower_text:
        return 'API Key 无效或已过期，请到「API 设置」检查 Key。'
    if 'model_not_found' in lower_text or 'channel not found' in lower_text:
        return f'上游平台找不到模型「{model}」可用通道。可能该模型未在此账号开通，请换一个已开通的模型。'
    return ''

def parse_error_payload_text(text):
    body = str(text or '').strip()
    if not body:
        return {}
    try:
        parsed = json.loads(body)
    except Exception:
        return {}
    return parsed if isinstance(parsed, dict) else {}

def friendly_chat_error_detail(text, model='', provider=None):
    raw_text = str(text or '')
    lower_text = raw_text.lower()
    payload = parse_error_payload_text(raw_text)
    error = payload.get('error') if isinstance(payload.get('error'), dict) else {}
    code = str(error.get('code') or payload.get('code') or '').strip()
    message = str(error.get('message') or payload.get('message') or '').strip()
    code_lc = code.lower()
    message_lc = message.lower()
    model_name = str(model or '').strip()
    if is_volcengine_provider(provider):
        if code_lc in {'invalidendpointormodel.notfound', 'invalidendpointormodel.modelidaccessdisabled'}:
            provider_name = provider.get('name') or provider.get('id') or '火山方舟'
            return f"{provider_name} 当前不接受模型名「{model_name or '未指定'}」直接调用聊天接口，请在火山方舟控制台创建并使用推理接入点 ID（形如 `ep-...`）作为聊天模型。\n\n补充说明：`/api/v3/models` 能拉到公开模型列表，但你的账号未必能直接用这些模型名调用 `/chat/completions`；很多账号只允许传自己已开通的 `ep-...` 接入点。"
        if 'does not exist or you do not have access to it' in message_lc:
            return f"火山方舟找不到或无权访问聊天模型「{model_name or '未指定'}」。如果你现在填的是模型名，请改成已开通的推理接入点 ID（`ep-...`）；如果已经是 `ep-...`，请检查这个接入点是否绑定了聊天模型、区域是否正确、以及账号是否有调用权限。"
    if 'unauthorized' in lower_text or '401' in lower_text:
        return 'API Key 无效或已过期，请到「API 设置」检查 Key。'
    if 'rate limit' in lower_text or '429' in lower_text:
        return '请求过于频繁，已被上游限流，请稍后再试。'
    return ''

def gemini_model_name(model):
    value = selected_model(model, 'gemini-3-pro-image-preview').strip()
    return value[len('models/'):] if value.startswith('models/') else value

def gemini_supported_aspect_ratio(size, fallback='1:1', strict=False):
    """Find the supported aspect ratio closest to the requested size.

    When strict=False (default): returns the closest supported ratio (snap-to-nearest).
    When strict=True: raises IncompatibleGenerationParameterError if the requested
    ratio is not within tolerance of any supported ratio, per ADR 0006.

    A small tolerance (0.5%) is used to account for pixel rounding when users
    specify pixel dimensions that are meant to represent a standard ratio.
    """
    width, height = parse_size_pair(size)
    if not width or not height:
        match = re.fullmatch('\\s*(\\d+)\\s*:\\s*(\\d+)\\s*', str(size or ''))
        if match:
            width, height = (int(match.group(1)), int(match.group(2)))
    if not width or not height:
        return fallback
    ratio = width / height
    # Check if the requested ratio is within tolerance of a supported ratio
    # Tolerance of 0.5% accounts for pixel rounding (e.g., 3840x1648 ≈ 21:9)
    RATIO_TOLERANCE = 0.005
    for item in GEMINI_IMAGE_ASPECT_RATIOS:
        supported_ratio = item[0] / item[1]
        if abs(ratio - supported_ratio) / supported_ratio < RATIO_TOLERANCE:
            return item[2]
    if strict:
        raise IncompatibleGenerationParameterError(
            parameter='aspect_ratio',
            requested_value=str(size),
            supported_values=[item[2] for item in GEMINI_IMAGE_ASPECT_RATIOS],
            model_id='gemini-image-family',
            detail='The requested aspect ratio is not in the model\'s supported list. Generation must stop per ADR 0006.'
        )
    return min(GEMINI_IMAGE_ASPECT_RATIOS, key=lambda item: abs(math.log(ratio / (item[0] / item[1]))))[2]

def banana_image_request_params(size):
    _, resolution = apimart_size_resolution(size)
    return {'aspect_ratio': gemini_supported_aspect_ratio(size, strict=True), 'image_size': resolution.upper() if resolution in {'1k', '2k', '4k'} else resolution}

def image_provider_model_ids(provider):
    configured = (provider or {}).get('image_models') or []
    discovered = ((provider or {}).get('available_models') or {}).get('image_models') or []
    return {str(item or '').strip().lower() for item in [*configured, *discovered] if str(item or '').strip()}

def banana_model_family(model):
    value = str(model or '').strip().lower()
    if value in {'nano-banana', 'nano-banana-hd'}:
        return 'nano-banana'
    if value in {'banana', 'nano-banana-pro'}:
        return 'nano-banana-pro'
    match = re.fullmatch('(nano-banana-(?:pro|2))(?:-(?:1k|2k|4k))?', value)
    return match.group(1) if match else ''

def looks_like_gemini_image_model(model):
    value = str(model or '').strip().lower()
    return value.startswith('gemini-') and 'image' in value

def model_spec_for(model_id):
    """Look up a model in model_specs.json (single source of truth for adapted models).

    Returns the spec dict or None. Never raises — import/load failures degrade
    to None so routing falls back to legacy name heuristics.
    """
    try:
        from app.services.model_specs import get_model_spec
        return get_model_spec(model_id)
    except Exception:
        return None

def route_image_from_spec(requested_model, size, spec):
    """Build an image-route from a model_specs.json entry.

    The spec's ``parameters`` keys decide the param style:
      - aspect_ratio / image_size -> banana/gemini style (aspect_ratio + image_size)
      - otherwise                 -> OpenAI style (rely on ``size`` sent downstream)
    """
    sp = (spec or {}).get('parameters', {}) or {}
    if 'aspect_ratio' in sp or 'image_size' in sp:
        return {
            'requested_model': requested_model,
            'model': requested_model,
            'params': banana_image_request_params(size),
            'family': 'spec-aspect',
        }
    return {
        'requested_model': requested_model,
        'model': requested_model,
        'params': {},
        'family': '',
    }

def route_openai_image_request(provider, model, size):
    requested_model = selected_model(model, IMAGE_MODEL)
    family = banana_model_family(requested_model)
    # SPEC-FIRST: model_specs.json is the authoritative registry for adapted
    # models. For models not already handled by name heuristics (banana/gemini),
    # consult the spec so that "adding a model to the spec" is what adapts it.
    if not family and not looks_like_gemini_image_model(requested_model):
        spec = model_spec_for(requested_model)
        if spec and spec.get('api_type') == 'images_generations':
            return route_image_from_spec(requested_model, size, spec)
    params = {}
    routed_model = requested_model
    if family:
        available = image_provider_model_ids(provider)
        _, resolution = apimart_size_resolution(size)
        requested_variant = requested_model != family
        params = {
            'aspect_ratio': gemini_supported_aspect_ratio(size, strict=True),
            'image_size': resolution.upper() if resolution in {'1k', '2k', '4k'} else resolution,
        }
        target_model = 'nano-banana-hd' if family == 'nano-banana' and resolution in {'2k', '4k'} else f'{family}-{resolution}'
        if requested_variant:
            if requested_model.lower() in available:
                routed_model = requested_model
            else:
                if family in available:
                    params['image_size'] = resolution.upper() if resolution in {'1k', '2k', '4k'} else resolution
                    routed_model = family
                else:
                    routed_model = requested_model
        elif family in available:
            routed_model = target_model if resolution in {'2k', '4k'} and target_model in available else family
        else:
            if target_model in available:
                routed_model = target_model
            elif requested_model.lower() not in available:
                routed_model = target_model
            else:
                routed_model = family
    elif looks_like_gemini_image_model(requested_model):
        params = banana_image_request_params(size)
        available = image_provider_model_ids(provider)
        _, resolution = apimart_size_resolution(size)
        target_model = f'{requested_model}-{resolution}'
        if resolution in {'2k', '4k'} and not re.search(r'-(?:1k|2k|4k)$', requested_model.lower()) and target_model.lower() in available:
            routed_model = target_model
    return {'requested_model': requested_model, 'model': routed_model, 'params': params, 'family': family or ('gemini-image' if params else '')}

def gemini_image_config(size, image_parameters=None):
    explicit = image_parameters if isinstance(image_parameters, dict) else {}
    explicit_aspect = str(explicit.get('aspect_ratio') or '').strip()
    explicit_resolution = str(explicit.get('resolution') or '').strip()
    if explicit_aspect or explicit_resolution:
        return {'aspectRatio': explicit_aspect or gemini_supported_aspect_ratio(size, strict=True), 'imageSize': (explicit_resolution or apimart_size_resolution(size)[1]).upper()}
    width, height = parse_size_pair(size)
    if not width or not height:
        raw = str(size or '').strip().upper()
        if raw in {'1K', '2K', '4K'}:
            return {'aspectRatio': '1:1', 'imageSize': raw}
        if re.fullmatch('\\d+\\s*:\\s*\\d+', raw):
            return {'aspectRatio': gemini_supported_aspect_ratio(raw, strict=True), 'imageSize': '1K'}
        return {'aspectRatio': '1:1', 'imageSize': '2K'}
    _, resolution = apimart_size_resolution(size)
    aspect_ratio = gemini_supported_aspect_ratio(size, strict=True)
    return {'aspectRatio': aspect_ratio, 'imageSize': resolution.upper()}

def runninghub_output_ext(remote, content_type=''):
    tail = str(remote or '').split('?', 1)[0].split('#', 1)[0]
    ext = os.path.splitext(tail)[1].lower().strip('.')
    allowed = {'png', 'jpg', 'jpeg', 'webp', 'gif', 'bmp', 'mp4', 'webm', 'mov', 'm4v', 'mkv', 'mp3', 'wav', 'ogg', 'm4a', 'flac', 'aac'}
    if ext in allowed:
        return ext
    ct = str(content_type or '').lower()
    if 'mp4' in ct:
        return 'mp4'
    if 'webm' in ct:
        return 'webm'
    if 'quicktime' in ct:
        return 'mov'
    if 'mpeg' in ct:
        return 'mp3'
    if 'wav' in ct:
        return 'wav'
    if 'ogg' in ct:
        return 'ogg'
    if 'webp' in ct:
        return 'webp'
    if 'jpeg' in ct:
        return 'jpg'
    return 'png'

def rewrite_runninghub_file_url(url):
    text = str(url or '')
    if not text:
        return text
    try:
        parsed = urllib.parse.urlsplit(text)
    except Exception:
        return text
    target = RUNNINGHUB_FILE_HOST_REWRITES.get((parsed.netloc or '').lower())
    return parsed._replace(netloc=target).geturl() if target else text

def runninghub_extract_outputs(data):
    arr = []
    if isinstance(data, list):
        arr = data
    elif isinstance(data, dict):
        for key in ('outputs', 'results', 'files', 'data'):
            value = data.get(key)
            if isinstance(value, list):
                arr = value
                break
        if not arr and (data.get('fileUrl') or data.get('url')):
            arr = [data]
    outputs = []
    for item in arr:
        if isinstance(item, str):
            outputs.append(rewrite_runninghub_file_url(item))
        elif isinstance(item, dict):
            url = item.get('fileUrl') or item.get('file_url') or item.get('url') or item.get('downloadUrl') or item.get('download_url')
            if isinstance(url, list):
                outputs.extend([rewrite_runninghub_file_url(u) for u in url if u])
            elif url:
                outputs.append(rewrite_runninghub_file_url(url))
    return outputs

def runninghub_fail_reason(raw):
    data = raw.get('data') if isinstance(raw, dict) else None
    values = []
    if isinstance(data, dict):
        values.extend([data.get('failedReason'), data.get('failReason'), data.get('message'), data.get('error'), data.get('errorMessage')])
    if isinstance(raw, dict):
        values.extend([raw.get('msg'), raw.get('message'), raw.get('error'), raw.get('errorMessage')])
    for value in values:
        if not value:
            continue
        if isinstance(value, str):
            return value
        if isinstance(value, dict):
            return value.get('exception_message') or value.get('message') or json.dumps(value, ensure_ascii=False)
        return str(value)
    if isinstance(raw, dict) and raw.get('errorCode'):
        return f"RunningHub errorCode={raw.get('errorCode')}"
    return ''

def runninghub_task_endpoint(provider, model):
    raw_model_path = str(model or '').strip()
    model_path = raw_model_path.strip('/')
    if not model_path:
        model_path = RUNNINGHUB_DEFAULT_IMAGE_MODELS[0]
    if raw_model_path.startswith('/openapi/'):
        return runninghub_endpoint_url(provider, raw_model_path)
    if model_path.startswith('openapi/'):
        return runninghub_endpoint_url(provider, f'/{model_path}')
    return runninghub_openapi_url(provider, model_path)

def runninghub_endpoint_alias_for_model(model):
    model_id = str(model or '').strip().strip('/')
    if not model_id:
        return ''
    direct = RUNNINGHUB_MODEL_ENDPOINT_ALIASES.get(model_id)
    if direct:
        return direct
    lowered = model_id.lower()
    if lowered.startswith('gpt-image-2.0/') or lowered.startswith('gpt-image-2/'):
        if '/text-to-image-' in lowered or lowered.endswith('/text-to-image'):
            return 'rhart-image-g-2/text-to-image'
        if '/edit-' in lowered or lowered.endswith('/edit'):
            return 'rhart-image-g-2/image-to-image'
        if '/image-to-image-' in lowered or lowered.endswith('/image-to-image'):
            return 'rhart-image-g-2/image-to-image'
    if lowered.startswith('nano-banana/'):
        if '/text-to-image-' in lowered or lowered.endswith('/text-to-image'):
            return 'rhart-image-v1/text-to-image'
        if '/edit-' in lowered or lowered.endswith('/edit'):
            return 'rhart-image-v1/edit'
    return ''

def runninghub_registry_fallback():
    image = [{'name_en': 'gpt-image-2.0/text-to-image-channel-low-price', 'endpoint': 'rhart-image-g-2/text-to-image', 'output_type': 'image'}, {'name_en': 'gpt-image-2.0/edit-channel-low-price', 'endpoint': 'rhart-image-g-2/image-to-image', 'output_type': 'image'}, {'name_en': 'gpt-image-2/text-to-image-official-stable', 'endpoint': 'rhart-image-g-2-official/text-to-image', 'output_type': 'image'}, {'name_en': 'gpt-image-2/image-to-image-official-stable', 'endpoint': 'rhart-image-g-2-official/image-to-image', 'output_type': 'image'}, {'name_en': 'nano-banana/text-to-image-official-stable', 'endpoint': 'rhart-image-v1-official/text-to-image', 'output_type': 'image'}, {'name_en': 'nano-banana/edit-official-stable', 'endpoint': 'rhart-image-v1-official/edit', 'output_type': 'image'}]
    video = [{'name_en': 'google/veo3.1-fast/text-to-video-channel-low-price', 'endpoint': 'rhart-video-v3.1-fast/text-to-video', 'output_type': 'video'}, {'name_en': 'sora-2/text-to-video-official-stable', 'endpoint': 'rhart-video-s-official/text-to-video', 'output_type': 'video'}, {'name_en': 'seedance-2.0-global/text-to-video', 'endpoint': 'bytedance/seedance-2.0-global/text-to-video', 'output_type': 'video'}, {'name_en': 'seedance-2.0-global/image-to-video', 'endpoint': 'bytedance/seedance-2.0-global/image-to-video', 'output_type': 'video'}]
    return image + video

def runninghub_registry_items_from_raw(raw):
    candidates = [raw]
    if isinstance(raw, dict):
        candidates.extend([raw.get('data'), raw.get('models'), raw.get('list'), raw.get('items'), raw.get('records'), raw.get('result')])
    for candidate in candidates:
        if isinstance(candidate, list):
            return [item for item in candidate if isinstance(item, dict)]
        if isinstance(candidate, dict):
            nested = candidate.get('models') or candidate.get('list') or candidate.get('items') or candidate.get('records') or candidate.get('data')
            if isinstance(nested, list):
                return [item for item in nested if isinstance(item, dict)]
    return []

def runninghub_registry_model_from_id(model_id, output_type=''):
    model_id = str(model_id or '').strip()
    if not model_id:
        return None
    output_type = str(output_type or '').strip().lower() or classify_upstream_model(model_id)
    return {'name_en': model_id, 'endpoint': model_id, 'output_type': output_type}

def runninghub_model_id(item):
    if not isinstance(item, dict):
        return ''
    return str(item.get('name_en') or item.get('id') or item.get('name') or item.get('endpoint') or '').strip()

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

def runninghub_schema_options(field):
    values = []
    for item in (field or {}).get('options') or []:
        if isinstance(item, dict):
            value = item.get('value')
        else:
            value = item
        if value is not None and str(value) != '':
            values.append(str(value))
    return values

def runninghub_schema_value(field, preferred=None):
    preferred = '' if preferred is None else str(preferred).strip()
    options = runninghub_schema_options(field)
    if preferred and (not options or preferred in options):
        return preferred
    default = (field or {}).get('defaultValue')
    if default is not None and str(default) != '':
        return default
    return options[0] if options else preferred

def runninghub_schema_field(params, *keys):
    wanted = {str(k).lower() for k in keys if k}
    for field in params or []:
        if not isinstance(field, dict):
            continue
        names = {str(field.get('fieldKey') or '').lower(), str(field.get('label') or '').lower()}
        if names & wanted:
            return field
    return None

def runninghub_schema_field_required(field):
    value = (field or {}).get('required')
    return value is True or (isinstance(value, str) and value.strip().lower() == 'true')

def runninghub_aspect_from_size(size, fallback='1:1'):
    width, height = parse_size_pair(size)
    if width and height:
        divisor = math.gcd(width, height) or 1
        return f'{width // divisor}:{height // divisor}'
    raw = str(size or '').strip().lower()
    if re.fullmatch('(auto|\\d+\\s*:\\s*\\d+)', raw):
        return raw.replace(' ', '')
    return fallback

def runninghub_resolution_from_size(size, fallback='2k'):
    width, height = parse_size_pair(size)
    if width and height:
        long_edge = max(width, height)
        if long_edge >= 3200:
            return '4k'
        if long_edge >= 1400:
            return '2k'
        return '1k'
    raw = str(size or '').strip().lower()
    return raw if raw in {'1k', '2k', '4k', '480p', '720p', '1080p', 'native1080p'} else fallback

def runninghub_size_for_aspect(aspect_ratio, fallback='1280x720'):
    ratio = str(aspect_ratio or '').strip()
    return {'9:16': '720x1280', '16:9': '1280x720', '1:1': '1024x1024', '4:3': '1024x768', '3:4': '768x1024'}.get(ratio, fallback)

def runninghub_is_image_to_video(value):
    text = str(value or '').strip().lower()
    compact = re.sub('[\\s_/]+', '-', text)
    return 'image-to-video' in compact or '-i2v' in compact or compact.endswith('i2v')

def runninghub_apply_schema_defaults(body, params):
    for field in params or []:
        if not isinstance(field, dict):
            continue
        key = str(field.get('fieldKey') or '').strip()
        if not key or key in body:
            continue
        default = field.get('defaultValue')
        options = runninghub_schema_options(field)
        if default is None or default == '':
            if field.get('required') is True and options:
                default = options[0]
            else:
                continue
        ftype = str(field.get('type') or '').upper()
        if ftype == 'BOOLEAN':
            body[key] = bool(default) if not isinstance(default, str) else default.lower() == 'true'
        elif ftype in {'INT', 'INTEGER'}:
            try:
                body[key] = int(default)
            except Exception:
                body[key] = default
        elif ftype == 'FLOAT':
            try:
                body[key] = float(default)
            except Exception:
                body[key] = default
        else:
            body[key] = default
    return body

def runninghub_query_status(raw):
    if not isinstance(raw, dict):
        return ''
    values = [raw.get('status'), raw.get('state'), raw.get('taskStatus'), raw.get('task_status')]
    data = raw.get('data')
    if isinstance(data, dict):
        values.extend([data.get('status'), data.get('state'), data.get('taskStatus'), data.get('task_status')])
    for value in values:
        if value is not None:
            return str(value).lower()
    return ''

def runninghub_extract_task_id(raw):
    if not isinstance(raw, dict):
        return ''
    for key in ('taskId', 'task_id', 'id'):
        if raw.get(key):
            return str(raw[key])
    data = raw.get('data')
    if isinstance(data, dict):
        for key in ('taskId', 'task_id', 'id'):
            if data.get(key):
                return str(data[key])
    return ''

def runninghub_extract_image(raw):
    if not isinstance(raw, dict):
        raise HTTPException(status_code=502, detail='RunningHub 返回格式不是 JSON 对象')
    containers = [raw]
    data = raw.get('data')
    if isinstance(data, dict):
        containers.append(data)
    for container in containers:
        results = container.get('results') or container.get('result') or container.get('outputs') or container.get('output')
        if isinstance(results, dict):
            results = [results]
        if isinstance(results, list):
            for item in results:
                if isinstance(item, str) and item.startswith(('http://', 'https://')):
                    return {'type': 'url', 'value': rewrite_runninghub_file_url(item)}
                if not isinstance(item, dict):
                    continue
                if item.get('type') == 'url' and item.get('value'):
                    return {'type': 'url', 'value': rewrite_runninghub_file_url(item['value'])}
                if item.get('type') == 'b64' and item.get('value'):
                    return {'type': 'b64', 'value': item['value'], 'mime_type': item.get('mime_type') or 'image/png'}
                url = item.get('url') or item.get('fileUrl') or item.get('file_url') or item.get('download_url') or item.get('imageUrl') or item.get('image_url')
                if isinstance(url, list) and url:
                    url = url[0]
                if isinstance(url, str) and url:
                    return {'type': 'url', 'value': rewrite_runninghub_file_url(url)}
    image = extract_image(raw)
    if image.get('type') == 'url':
        image['value'] = rewrite_runninghub_file_url(image.get('value'))
    return image

def rh_field_kind(field):
    field = field or {}
    t = str(field.get('fieldType') or '').strip().upper()
    if t == 'IMAGE':
        return 'image'
    if t == 'VIDEO':
        return 'video'
    if t == 'AUDIO':
        return 'audio'
    if t == 'SLIDER':
        return 'slider'
    if t in ('NUMBER', 'FLOAT', 'INTEGER', 'INT'):
        return 'number'
    if t in ('BOOLEAN', 'BOOL'):
        return 'boolean'
    key = f"{field.get('fieldName') or ''} {field.get('fieldValue') or ''}".lower()
    if re.search('\\b(image|img|mask|photo|picture)\\b', key) or re.search('\\.(png|jpe?g|webp|gif|bmp)(\\?|$)', key, re.I):
        return 'image'
    if re.search('\\b(video|movie|mp4)\\b', key) or re.search('\\.(mp4|webm|mov|m4v|mkv)(\\?|$)', key, re.I):
        return 'video'
    if re.search('\\b(audio|sound|music|voice)\\b', key) or re.search('\\.(mp3|wav|ogg|m4a|flac|aac)(\\?|$)', key, re.I):
        return 'audio'
    return 'text'

def rh_field_role(field):
    kind = rh_field_kind(field)
    if kind in ('image', 'video', 'audio', 'number', 'slider', 'boolean'):
        return kind
    field = field or {}
    text = f"{field.get('fieldName') or ''} {field.get('label') or ''} {field.get('group') or ''}".lower()
    if re.search('prompt|positive|negative|text|caption|description|关键词|提示词|正向|负向', text):
        return 'prompt'
    return 'text'

def _rh_natural_cmp(x, y):
    if x == y:
        return 0
    if x.isdigit() and y.isdigit():
        ix, iy = (int(x), int(y))
        return (ix > iy) - (ix < iy)
    return (x > y) - (x < y)

def _rh_field_cmp(a, b):
    ak, bk = (rh_field_kind(a), rh_field_kind(b))
    if ak == 'image' and bk == 'image':
        try:
            ao = int(a.get('imageOrder') or 0) or 9999
        except Exception:
            ao = 9999
        try:
            bo = int(b.get('imageOrder') or 0) or 9999
        except Exception:
            bo = 9999
        if ao != bo:
            return ao - bo
    if ak == 'image' and bk != 'image':
        return -1
    if ak != 'image' and bk == 'image':
        return 1
    node_cmp = _rh_natural_cmp(str(a.get('nodeId') or ''), str(b.get('nodeId') or ''))
    if node_cmp != 0:
        return node_cmp
    fa, fb = (str(a.get('fieldName') or ''), str(b.get('fieldName') or ''))
    return (fa > fb) - (fa < fb)

def rh_sort_fields(fields):
    return sorted(list(fields or []), key=functools.cmp_to_key(_rh_field_cmp))

def rh_field_indexes(fields):
    counters = {'image': 0, 'video': 0, 'audio': 0}
    mapping = {}
    for field in rh_sort_fields(fields):
        kind = rh_field_kind(field)
        if kind in counters:
            mapping[str(field.get('nodeId') or ''), str(field.get('fieldName') or '')] = counters[kind]
            counters[kind] += 1
    return mapping

def rh_default_value(field):
    value = (field or {}).get('fieldValue')
    if isinstance(value, list):
        value = value[0] if value else ''
    if value is None or isinstance(value, dict):
        return ''
    return str(value)

def rh_is_seed_like_name(*parts) -> bool:
    text = ' '.join((str(part or '') for part in parts)).lower()
    return any((key in text for key in ('seed', 'noise', '随机', '种子', '噪')))

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

def sanitize_seed_like_values(value, parent_key=''):
    if isinstance(value, dict):
        result = {}
        for key, item in value.items():
            if rh_is_seed_like_name(key) and (not isinstance(item, (dict, list))):
                result[key] = normalize_seed_uint32(item)
            else:
                result[key] = sanitize_seed_like_values(item, key)
        return result
    if isinstance(value, list):
        return [sanitize_seed_like_values(item, parent_key) for item in value]
    if rh_is_seed_like_name(parent_key):
        return normalize_seed_uint32(value)
    return value

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

def rh_random_field_value(field):

    def _num(raw, default):
        try:
            s = str(raw).strip()
            if s == '' or s.lower() == 'none':
                return default
            return float(s)
        except Exception:
            return default
    looks_seed = rh_is_seed_like_name((field or {}).get('fieldName'), (field or {}).get('label'), (field or {}).get('note'))
    lo = _num((field or {}).get('min'), 0.0)
    hi = _num((field or {}).get('max'), float(SEED_UINT32_MAX) if looks_seed else 999999.0)
    if looks_seed:
        hi = min(hi, float(SEED_UINT32_MAX))
        lo = max(0.0, min(lo, hi))
    if hi < lo:
        lo, hi = (hi, lo)
    step = _num((field or {}).get('step'), 1.0)
    value = random.uniform(lo, hi)
    if step and step > 0:
        value = lo + round((value - lo) / step) * step
    if float(step).is_integer() and float(lo).is_integer() and float(hi).is_integer():
        return str(int(round(value)))
    return str(value)

def runninghub_entry_config_from_model(provider, model):
    """解析 model=app:ID，返回在线 AI 应用配置。"""
    text = str(model or '').strip()
    match = RUNNINGHUB_ENTRY_MODEL_RE.match(text)
    if not match:
        return None
    entry_id = match.group(1).strip()
    if not entry_id:
        return None
    entry = next((e for e in provider.get('rh_apps') or [] if runninghub_entry_id(e, 'app') == entry_id), None)
    if not entry:
        return None
    return {'kind': 'app', 'id': entry_id, 'fields': entry.get('fields') or []}

def latest_chat_image_refs(conversation, limit=1):
    refs = []
    for item in reversed(conversation.get('messages') or []):
        url = item.get('image_url') if isinstance(item, dict) else ''
        if url:
            refs.append({'url': url, 'name': item.get('content') or '上一张图片', 'role': 'source'})
        if len(refs) >= limit:
            break
    return refs

def chat_requested_image_count(message):
    text = str(message or '')
    match = re.search('(?<!\\d)([1-4])\\s*(?:张|幅|个|组|套)(?!\\d)', text)
    if match:
        return max(1, min(4, int(match.group(1))))
    match = re.search('([一二两俩三四])\\s*(?:张|幅|个|组|套)', text)
    if match:
        return max(1, min(4, CN_NUMERAL_MAP.get(match.group(1), 1)))
    return 1

def chat_split_parallel_prompts(prompt, count):
    text = str(prompt or '').strip()
    if count <= 1:
        return [text]
    noun_match = re.search('(.+?)(?:的)?(海报|头像|壁纸|插画|照片|图片|图像)\\s*$', text)
    if not noun_match:
        return [text] * count
    prefix = noun_match.group(1).strip()
    suffix = noun_match.group(2)
    prefix = re.sub('(?:再)?(?:生成|画|绘制|制作|创建)\\s*[1-4一二两俩三四]?\\s*(?:张|幅|个|组|套)?', '', prefix).strip()
    prefix = re.sub('[,，、\\s]+$', '', prefix).strip()
    if not prefix:
        return [text] * count
    candidates = [item.strip(' ，,、') for item in re.split('\\s*(?:和|与|、|，|,|\\+|＋)\\s*', prefix) if item.strip(' ，,、')]
    if len(candidates) < count:
        return [text] * count
    return [f'{item}的{suffix}' for item in candidates[:count]]

def heuristic_agent_decision(message, refs, has_previous_image):
    text = str(message or '').strip().lower()
    has_image_word = any((key.lower() in text for key in AGENT_IMAGE_KEYWORDS))
    has_edit_word = any((key.lower() in text for key in AGENT_EDIT_KEYWORDS))
    if refs and (has_edit_word or has_image_word):
        return {'action': 'edit_image', 'prompt': message, 'reply': ''}
    if has_previous_image and has_edit_word:
        return {'action': 'edit_image', 'prompt': message, 'reply': ''}
    if has_image_word and (not has_edit_word):
        return {'action': 'generate_image', 'prompt': message, 'reply': ''}
    return {'action': 'chat', 'prompt': message, 'reply': ''}

def parse_agent_decision(raw_text, message, refs, has_previous_image):
    text = str(raw_text or '').strip()
    data = None
    if text:
        match = re.search('\\{[\\s\\S]*\\}', text)
        candidate = match.group(0) if match else text
        try:
            data = json.loads(candidate)
        except Exception:
            data = None
    heuristic = heuristic_agent_decision(message, refs, has_previous_image)
    if not isinstance(data, dict):
        return heuristic
    action = str(data.get('action') or '').strip()
    if action not in AGENT_ACTIONS:
        action = heuristic['action']
    prompt = str(data.get('prompt') or message).strip() or message
    reply = str(data.get('reply') or '').strip()
    if action == 'edit_image' and (not (refs or has_previous_image)):
        action = 'generate_image' if any((key.lower() in str(message).lower() for key in AGENT_IMAGE_KEYWORDS)) else 'chat'
    return {'action': action, 'prompt': prompt, 'reply': reply}

class Base64UploadRequest(BaseModel):
    data: str = ''
    name: str = ''
    content_type: str = ''

def _local_upload_kind_ext(filename, content_type):
    image_exts = {'.png', '.jpg', '.jpeg', '.webp', '.gif'}
    video_exts = {'.mp4', '.webm', '.mov', '.m4v', '.flv'}
    audio_exts = {'.mp3', '.wav', '.m4a', '.aac', '.ogg', '.flac'}
    ext = os.path.splitext(filename or '')[1].lower()
    ct = (content_type or '').lower()
    if ext in video_exts or ct.startswith('video/'):
        if ext not in video_exts:
            ext = '.webm' if 'webm' in ct else '.mov' if 'quicktime' in ct else '.mp4'
        return ('video', ext)
    if ext in audio_exts or ct.startswith('audio/'):
        if ext not in audio_exts:
            ext = '.wav' if 'wav' in ct else '.ogg' if 'ogg' in ct else '.m4a' if 'mp4' in ct else '.mp3'
        return ('audio', ext)
    if ext in image_exts or ct.startswith('image/'):
        if ext not in image_exts:
            ext = '.jpg' if 'jpeg' in ct else '.webp' if 'webp' in ct else '.gif' if 'gif' in ct else '.png'
        return ('image', ext)
    return (None, ext)

def _local_upload_display_name(filename):
    base = os.path.basename(str(filename or ''))
    m = re.match('^up_[0-9a-f]{12}_(.+)$', base)
    return m.group(1) if m else base

def _local_upload_rel_path(value):
    text = str(value or '').replace('\\', '/').strip().lstrip('/')
    if not text:
        return ''
    norm = os.path.normpath(text).replace('\\', '/')
    if norm in {'.', ''}:
        return ''
    if norm.startswith('../') or norm == '..' or os.path.isabs(norm):
        raise HTTPException(status_code=400, detail='非法路径')
    return norm

def _local_upload_safe_folder_name(name):
    cleaned = sanitize_asset_name(os.path.basename(str(name or '').strip()), '')
    cleaned = re.sub('[\\\\/]+', '_', cleaned).strip(' ._')
    if not cleaned:
        raise HTTPException(status_code=400, detail='文件夹名称不能为空')
    return cleaned[:60]

def _local_upload_safe_file_stem(name):
    raw = os.path.splitext(os.path.basename(str(name or '').strip()))[0]
    cleaned = sanitize_asset_name(raw, '')
    cleaned = re.sub('[\\\\/]+', '_', cleaned).strip(' ._')
    if not cleaned:
        raise HTTPException(status_code=400, detail='文件名称不能为空')
    return cleaned[:120]

def _local_upload_folder_node(path='', name='全部上传'):
    rel = _local_upload_rel_path(path)
    return {'id': rel or '__root__', 'path': rel, 'name': name if not rel else os.path.basename(rel), 'items': [], 'children': []}

def _sniff_image_ext_bytes(head):
    """按文件头魔数判断真实图片格式，返回规范扩展名（含点），无法识别返回 None。"""
    head = head or b''
    if head.startswith(b'\x89PNG\r\n\x1a\n'):
        return '.png'
    if head.startswith(b'\xff\xd8\xff'):
        return '.jpg'
    if head[:4] == b'RIFF' and head[8:12] == b'WEBP':
        return '.webp'
    if head[:6] in (b'GIF87a', b'GIF89a'):
        return '.gif'
    if head[:2] == b'BM':
        return '.bmp'
    return None

def _sniff_image_ext(path):
    try:
        with open(path, 'rb') as f:
            return _sniff_image_ext_bytes(f.read(16))
    except OSError:
        return None

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

def classify_upstream_model(mid):
    # SPEC-FIRST: model_specs.json is the authoritative registry for media type.
    # A model present in the spec is classified by the category it belongs to,
    # so that "adding a model to the spec" is what defines how it's routed.
    try:
        from app.services.model_specs import get_model_category
        kind = get_model_category(mid)
        if kind:
            return kind
    except Exception:
        pass
    # FALLBACK: legacy keyword classification (models not yet in the spec)
    lc = str(mid or '').lower()
    video_keys = ['veo', 'sora', 'wan2', 'wanx', 'doubao-seedance', 'doubao-1', 'kling', 'hailuo', 'video', 't2v-', 'i2v-', 's2v']
    if any((k in lc for k in video_keys)):
        return 'video'
    image_keys = ['banana', 'image', 'dalle', 'dall-e', 'imagen', 'flux', 'stable', 'sdxl', 'midjourney', 'nano-banana', 'ideogram', 'fal-ai', 'qwen-image', 'seedream', 'doubao-seedream', 'text-to-image', 'image-to-image']
    if any((k in lc for k in image_keys)):
        return 'image'
    return 'chat'

def parse_upstream_models(raw, protocol='openai'):
    items = raw.get('data') if isinstance(raw, dict) else None
    if not items and isinstance(raw, dict):
        items = raw.get('models') or raw.get('list') or []
    if not isinstance(items, list):
        items = []
    ids = []
    for it in items:
        if isinstance(it, str):
            mid = it
        elif isinstance(it, dict):
            mid = it.get('id') or it.get('name') or it.get('model')
        else:
            mid = ''
        if mid:
            mid = str(mid)
            if protocol == 'gemini' and mid.startswith('models/'):
                mid = mid[len('models/'):]
            ids.append(mid)
    ids = sorted(set(ids))
    grouped = {'image': [], 'chat': [], 'video': []}
    for mid in ids:
        grouped[classify_upstream_model(mid)].append(mid)
    return (grouped, ids)

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

def require_online_image_project_media_scope(payload: OnlineImageRequest) -> None:
    require_project_media_values_scope([str(getattr(ref, 'url', '') or '').strip() for ref in payload.reference_images or [] if str(getattr(ref, 'url', '') or '').strip()])

def project_agent_execution_lock(project_id: str, node_id: str) -> Lock:
    key = (str(project_id), str(node_id))
    with PROJECT_AGENT_EXECUTION_LOCKS_GUARD:
        return PROJECT_AGENT_EXECUTION_LOCKS.setdefault(key, Lock())

def _agent_nodes_by_id(canvas: Mapping[str, Any]) -> Dict[str, Dict[str, Any]]:
    return {str(node.get('id')): node for node in canvas.get('nodes') or [] if isinstance(node, dict) and str(node.get('id') or '')}

def project_agent_node(canvas: Mapping[str, Any], node_id: str) -> Dict[str, Any]:
    node = _agent_nodes_by_id(canvas).get(str(node_id or ''))
    if not isinstance(node, dict) or str(node.get('type') or '') != 'smart-agent':
        raise HTTPException(status_code=400, detail='目标节点不是 Agent 节点')
    return node

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
    dependencies = raw.get('dependencies')
    if dependencies is not None:
        if not isinstance(dependencies, list) or not all(isinstance(d, int) and not isinstance(d, bool) for d in dependencies):
            raise HTTPException(status_code=400, detail='Agent 任务依赖必须是任务序号数组')
        if any((d < 0 or d >= index for d in dependencies)):
            raise HTTPException(status_code=400, detail='Agent 任务依赖只能引用更早的任务序号')
        if index in dependencies:
            raise HTTPException(status_code=400, detail='Agent 任务不能依赖自身')
        dependencies = sorted(set(dependencies))
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
    return {'index': index, 'kind': kind, 'model_type': model_type, 'count': count, 'prompt': prompt, 'params': params, 'references': copy.deepcopy(references), 'reference_asset_ids': [str(item['asset_id']) for item in references], 'dependencies': dependencies}

def _agent_plan_hash(plan: Mapping[str, Any]) -> str:
    value = {'goal': plan.get('goal'), 'inputs': plan.get('inputs'), 'tasks': [{**{key: copy.deepcopy(item.get(key)) for key in ('task_id', 'result_node_id', 'kind', 'model_type', 'count', 'prompt', 'params', 'references', 'reference_asset_ids', 'dependencies')}} for item in plan.get('tasks') or [] if isinstance(item, dict)]}
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':')).encode('utf-8')).hexdigest()

def _agent_task_canonical(task: Mapping[str, Any]) -> Dict[str, Any]:
    """Return the only task fields that are user-confirmed and executable."""
    return {key: copy.deepcopy(task.get(key)) for key in ('kind', 'model_type', 'count', 'prompt', 'params', 'references', 'reference_asset_ids')}

def _agent_plan_from_node(node: Mapping[str, Any], plan_id: str) -> Optional[Dict[str, Any]]:
    plan = node.get('agent_plan')
    if not isinstance(plan, dict) or str(plan.get('plan_id') or '') != str(plan_id):
        return None
    return copy.deepcopy(plan)

def _agent_public_task(task: Mapping[str, Any]) -> Dict[str, Any]:
    return {key: copy.deepcopy(value) for key, value in task.items() if key not in {'adapter_request', 'raw_result'}}

def _agent_history_output(project_id: str, item: Mapping[str, Any], kind: str) -> Dict[str, Any]:
    asset_id = str(item.get('asset_id') or '')
    output = {'asset_id': asset_id, 'kind': kind, 'name': str(item.get('name') or asset_id)}
    return output

def _agent_find_task_node(canvas: Mapping[str, Any], task_id: str) -> Optional[Dict[str, Any]]:
    return next((node for node in canvas.get('nodes') or [] if isinstance(node, dict) and str(node.get('type') or '') == 'agent-task' and (str(node.get('task_id') or node.get('id') or '') == str(task_id))), None)

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

def _agent_int_param(params: Mapping[str, Any], key: str, default: Optional[int]=None) -> Optional[int]:
    value = params.get(key)
    if value in (None, ''):
        return default
    try:
        return int(value)
    except (TypeError, ValueError) as exc:
        raise ProjectStoreError(f'Agent 参数 {key} 必须是整数') from exc

def _agent_normalize_candidates(raw: Any, kind: str) -> List[Dict[str, Any]]:
    """Normalize fake/online adapter return shapes without accepting tools."""
    if isinstance(raw, dict):
        values = raw.get('images') if kind == 'image' else raw.get('videos')
        if values is None:
            values = raw.get('media') or raw.get('outputs')
        if values is None and (raw.get('type') or raw.get('value')):
            values = [raw]
    elif isinstance(raw, (list, tuple)):
        values = list(raw)
    elif isinstance(raw, (bytes, bytearray)):
        values = [bytes(raw)]
    else:
        values = []
    normalized: List[Dict[str, Any]] = []
    for item in values or []:
        if isinstance(item, (bytes, bytearray)):
            mime = 'image/png' if kind == 'image' else 'video/mp4'
            item = {'type': 'b64', 'value': base64.b64encode(bytes(item)).decode('ascii'), 'mime_type': mime}
        elif isinstance(item, str):
            item = {'type': 'url', 'value': item}
        elif isinstance(item, dict):
            item = copy.deepcopy(item)
            if 'asset_id' in item and (not item.get('type')) and (not item.get('value')):
                item = {'asset_id': str(item.get('asset_id')), 'kind': kind, 'name': item.get('name') or ''}
        else:
            continue
        if isinstance(item, dict):
            normalized.append(item)
    return normalized

def _agent_recovery_payload(node: Mapping[str, Any], plan: Mapping[str, Any]) -> Optional[Dict[str, Any]]:
    task_id = str(node.get('task_id') or node.get('id') or '')
    if not task_id:
        return None
    plan_task = next((item for item in plan.get('tasks') or [] if isinstance(item, dict) and str(item.get('task_id') or '') == task_id), None)
    if not isinstance(plan_task, dict):
        return None
    execution_id = str(node.get('execution_id') or '')
    if not execution_id:
        execution_id = next((str(execution.get('execution_id') or '') for execution in plan.get('executions') or [] if any((str(item.get('task_id') or '') == task_id for item in execution.get('tasks') or []))), '')
    if not execution_id:
        return None
    task = copy.deepcopy(plan_task)
    for key in ('kind', 'model_type', 'count', 'prompt', 'params', 'reference_asset_ids', 'result_node_id', 'plan_id'):
        if key in node and key not in {'params', 'reference_asset_ids'}:
            task[key] = copy.deepcopy(node[key])
    task['execution_id'] = execution_id
    return task

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

def generated_image_filename(index: int, mime_type: str) -> str:
    mime = str(mime_type or 'image/png').lower()
    extension = '.jpg' if 'jpeg' in mime or 'jpg' in mime else '.webp' if 'webp' in mime else '.gif' if 'gif' in mime else '.png'
    return f'generated-{index + 1}{extension}'

def validate_generated_image(source) -> Tuple[int, int, str]:
    try:
        with Image.open(source) as probe:
            image_format = str(probe.format or '').upper()
            probe.verify()
        if hasattr(source, 'seek'):
            source.seek(0)
        with Image.open(source) as decoded:
            decoded.load()
            width, height = decoded.size
            image_format = str(decoded.format or image_format).upper()
    except (OSError, ValueError, Image.DecompressionBombError) as exc:
        raise ProjectStoreError('在线模型返回的内容不是有效图片') from exc
    mime_types = {'PNG': 'image/png', 'JPEG': 'image/jpeg', 'WEBP': 'image/webp', 'GIF': 'image/gif'}
    mime_type = mime_types.get(image_format)
    if not mime_type or width <= 0 or height <= 0:
        raise ProjectStoreError('在线模型返回了不支持的图片格式')
    return (width, height, mime_type)

def validate_generated_video(source) -> Tuple[int, int, str]:
    """Validate a returned video container before it becomes project media."""
    probe_bytes = 8 * 1024 * 1024
    try:
        if isinstance(source, (str, Path)):
            path = Path(source)
            file_size = path.stat().st_size if path.is_file() else 0
            if file_size <= 0:
                raise ProjectStoreError('在线模型返回的视频为空')
            with path.open('rb') as handle:
                sample = handle.read(probe_bytes)
                if file_size > probe_bytes:
                    handle.seek(max(0, file_size - probe_bytes))
                    sample += handle.read(probe_bytes)
        else:
            if hasattr(source, 'seek'):
                source.seek(0)
            sample = source.read(probe_bytes)
            if hasattr(source, 'seek'):
                current = source.tell()
                source.seek(0, 2)
                file_size = source.tell()
                if file_size > current:
                    source.seek(max(0, file_size - probe_bytes))
                    sample += source.read(probe_bytes)
            if hasattr(source, 'seek'):
                source.seek(0)
    except (OSError, ValueError, TypeError) as exc:
        raise ProjectStoreError('在线模型返回的内容不是有效视频') from exc
    is_iso_bmff = len(sample) >= 12 and sample[4:8] == b'ftyp' and (b'moov' in sample) and (b'vide' in sample)
    is_webm = sample.startswith(b'\x1aE\xdf\xa3')
    is_avi = len(sample) >= 12 and sample[:4] == b'RIFF' and (sample[8:12] == b'AVI ')
    if is_iso_bmff:
        return (0, 0, 'video/mp4')
    if is_webm:
        return (0, 0, 'video/webm')
    if is_avi:
        return (0, 0, 'video/x-msvideo')
    raise ProjectStoreError('在线模型返回的内容不是有效视频或不支持的视频格式')

async def _resolve_generation_host(hostname: str, port: int, attempts: int = 2, timeout: float = 8.0):
    """带超时地解析在线生成结果的主机名。

    socket.getaddrinfo 是同步阻塞调用，放到 to_thread 里在 DNS 故障/黑洞时
    线程会无限挂起（asyncio.wait_for 取消不了线程），曾导致项目生图任务
    在 saving 阶段卡死到 1800s 超时。改用 loop.getaddrinfo 之后 wait_for
    能可靠超时，并对瞬态 DNS 故障做有限重试。
    """
    loop = asyncio.get_running_loop()
    last_error: BaseException = OSError('host resolution failed')
    for _ in range(max(1, attempts)):
        try:
            return await asyncio.wait_for(
                loop.getaddrinfo(hostname, port, type=socket.SOCK_STREAM),
                timeout=timeout,
            )
        except (OSError, asyncio.TimeoutError) as exc:
            last_error = exc
            await asyncio.sleep(0.4)
    raise last_error


async def ensure_public_generation_target(value: str, media_label: str='图片') -> Tuple[str, Dict[str, str], Dict[str, Any]]:
    parsed = urllib.parse.urlsplit(str(value or ''))
    if parsed.scheme not in {'http', 'https'} or not parsed.hostname or parsed.username or parsed.password:
        raise ProjectStoreError(f'在线模型返回了不安全的{media_label}地址')
    try:
        addresses = await _resolve_generation_host(
            parsed.hostname,
            parsed.port or (443 if parsed.scheme == 'https' else 80),
        )
    except (OSError, asyncio.TimeoutError) as exc:
        raise ProjectStoreError(f'在线模型返回的{media_label}地址无法解析') from exc
    resolved = {item[4][0].split('%', 1)[0] for item in addresses if item[4]}
    if not resolved or any((not ipaddress.ip_address(address).is_global for address in resolved)):
        raise ProjectStoreError(f'在线模型返回的{media_label}地址指向本机或私有网络')
    address = sorted(resolved)[0]
    authority_address = f'[{address}]' if ':' in address else address
    if parsed.port:
        authority_address = f'{authority_address}:{parsed.port}'
    pinned_url = urllib.parse.urlunsplit((parsed.scheme, authority_address, parsed.path, parsed.query, ''))
    return (pinned_url, {'host': parsed.netloc}, {'sni_hostname': parsed.hostname.encode('idna').decode('ascii')})

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

def build_image_param_fields(engine: str, provider: dict, model: str):
    """返回某平台/引擎的图像生成参数字段定义。客户端按 type 动态渲染并回填到生成请求。
    字段 key 直接对应 OnlineImageRequest 的字段名（size/quality/n/reference_images）。"""
    gpt_auto_size = engine == 'api' and is_gpt_image_2_model(model)
    image_resolutions = [{'value': 'auto', 'label': '自动'}] + IMAGE_PARAM_RESOLUTIONS if gpt_auto_size else IMAGE_PARAM_RESOLUTIONS
    size_field = {'key': 'size', 'type': 'size', 'label': '尺寸', 'ratios': IMAGE_PARAM_RATIOS, 'resolutions': image_resolutions, 'default': {'ratio': '1:1', 'resolution': 'auto' if gpt_auto_size else '1k'}}
    count_field = {'key': 'n', 'type': 'int', 'label': '数量', 'control': 'chips', 'options': [1, 2, 3, 4], 'default': 1}
    refs_field = {'key': 'reference_images', 'type': 'refs', 'label': '参考图', 'max': ONLINE_IMAGE_REFERENCE_MAX}
    if engine == 'runninghub':
        return [size_field, count_field, refs_field]
    fields = [size_field]
    if engine in ('api', 'volcengine'):
        fields.append({'key': 'quality', 'type': 'select', 'label': '质量', 'control': 'chips', 'options': [{'value': 'auto', 'label': '自动'}, {'value': 'low', 'label': '低'}, {'value': 'medium', 'label': '中'}, {'value': 'high', 'label': '高'}], 'default': 'auto'})
    fields.append(count_field)
    fields.append(refs_field)
    return fields

def _collect_video_url(value, urls):
    if not value:
        return
    if isinstance(value, str):
        if value.startswith('http://') or value.startswith('https://') or value.startswith('/output/') or value.startswith('/assets/'):
            urls.append(value)
        return
    if isinstance(value, list):
        for item in value:
            _collect_video_url(item, urls)
        return
    if isinstance(value, dict):
        for key in ('videos', 'outputs', 'data', 'detail', 'result', 'results', 'content'):
            if key in value:
                _collect_video_url(value.get(key), urls)
        for key in VIDEO_URL_KEYS:
            if key in value:
                _collect_video_url(value.get(key), urls)

def video_output_urls(raw):
    urls = []
    if not isinstance(raw, dict):
        return urls
    candidates = [raw]
    data = raw.get('data')
    detail = raw.get('detail')
    content = raw.get('content')
    if isinstance(data, dict):
        candidates.append(data)
    elif isinstance(data, list):
        for item in data:
            if isinstance(item, dict):
                candidates.append(item)
    if isinstance(detail, dict):
        candidates.append(detail)
    elif isinstance(detail, list):
        for item in detail:
            if isinstance(item, dict):
                candidates.append(item)
    if isinstance(content, dict):
        candidates.append(content)
    elif isinstance(content, list):
        for item in content:
            if isinstance(item, dict):
                candidates.append(item)
    for node in list(candidates):
        result = node.get('result') if isinstance(node, dict) else None
        results = node.get('results') if isinstance(node, dict) else None
        if isinstance(result, dict):
            candidates.append(result)
        elif isinstance(result, list):
            for item in result:
                if isinstance(item, dict):
                    candidates.append(item)
        if isinstance(results, dict):
            candidates.append(results)
        elif isinstance(results, list):
            for item in results:
                if isinstance(item, dict):
                    candidates.append(item)
    for node in candidates:
        if not isinstance(node, dict):
            continue
        for key in ('videos', 'outputs', 'results', 'content'):
            value = node.get(key)
            if value:
                _collect_video_url(value, urls)
        for key in VIDEO_URL_KEYS:
            if key in node:
                _collect_video_url(node.get(key), urls)
    deduped = []
    for url in urls:
        if isinstance(url, str) and url and (url not in deduped):
            deduped.append(url)
    return deduped

def looks_like_html_response(text: str) -> bool:
    sample = str(text or '').lstrip()[:200].lower()
    return sample.startswith('<!doctype html') or sample.startswith('<html') or '<head' in sample

def video_task_url_candidates(provider, base_url, task_id, submit_url=''):
    if is_agnes_provider(provider):
        quoted_id = urllib.parse.quote(str(task_id), safe='')
        return [f"{base_url}/agnesapi?{urllib.parse.urlencode({'video_id': task_id})}", f'{base_url}/v1/videos/{quoted_id}']
    if is_lingjing_provider(provider):
        quoted_id = urllib.parse.quote(str(task_id), safe='')
        return [f'{base_url}/v1/videos/{quoted_id}', f"{base_url}/v1/video/query?{urllib.parse.urlencode({'id': task_id})}"]
    if is_apimart_provider(provider):
        task_path = f'{base_url}/tasks/{task_id}' if base_url.endswith('/v1') else f'{base_url}/v1/tasks/{task_id}'
        return [f'{task_path}?language=zh']
    if is_volcengine_provider(provider):
        parsed = urllib.parse.urlparse(base_url)
        if parsed.path and parsed.path.rstrip('/'):
            return [f'{base_url}/{task_id}']
        return [f'{base_url}/api/v3/contents/generations/tasks/{task_id}']
    if is_yuli_provider(provider):
        return [f'{base_url}/v1/videos/{task_id}', f'{base_url}/v1/video/query?id={task_id}']
    v1_task = f'{base_url}/v1/videos/generations/{task_id}'
    v1_generic_task = f'{base_url}/v1/tasks/{task_id}'
    v2_task = f'{base_url}/v2/videos/generations/{task_id}'
    if '/v2/videos/generations' in str(submit_url or ''):
        return [v2_task, v1_task, v1_generic_task]
    return [v1_task, v1_generic_task, v2_task]

def humanize_video_task_failure(reason) -> str:
    """把上游视频任务的失败原因转成对用户友好的中文提示。
    目前主要处理 veo（Google）的内容安全过滤码。"""
    text = str(reason or '').strip()
    upper = text.upper()
    if 'PROMINENT_PEOPLE_FILTER' in upper or 'PROMINENT_PEOPLE' in upper:
        return f'视频生成被上游内容安全策略拦截：检测到提示词或参考图里包含知名人物 / 真人面孔（错误码：{text}）。\n\n这不是代码错误，而是 veo（Google）的内容审核规则——它会拒绝生成涉及真实/知名人物的视频。\n\n建议这样处理：\n  1. 去掉提示词里的人名、明星、公众人物等指向具体真人的描述；\n  2. 换用非真人参考图，例如插画、AI 头像、卡通形象、商品图、场景图；\n  3. 如果用了真人照片做参考图，先做模糊/遮挡/转成明显的二次元插画风，或干脆只用文字提示词测试。'
    if 'SAFETY' in upper or 'CONTENT_FILTER' in upper or 'POLICY' in upper:
        return f'视频生成被上游内容安全策略拦截（错误码：{text}）。\n\n这是 veo 的内容审核规则，提示词或参考图触发了安全过滤。\n请调整提示词/参考图后重试，避免涉及真人、暴力、敏感或受限内容。'
    return f'视频生成任务失败：{text}'

def apimart_video_size(size):
    value = str(size or '16:9').strip()
    if value == 'keep_ratio':
        return 'adaptive'
    allowed = {'16:9', '9:16', '1:1', '4:3', '3:4', '21:9', 'adaptive'}
    return value if value in allowed else '16:9'

def agnes_video_dimensions(aspect_ratio='', resolution='', width=None, height=None):
    if width is not None and height is not None:
        return (int(width), int(height))
    ratio = str(aspect_ratio or '16:9').strip()
    width, height = {'16:9': (1152, 648), '9:16': (648, 1152), '4:3': (1024, 768), '3:4': (768, 1024), '1:1': (768, 768), '21:9': (1280, 544), '9:21': (544, 1280)}.get(ratio, (1152, 768))
    scale = {'480p': 0.625, '720p': 1.0, '780p': 1.0, '1080p': 1.5}.get(str(resolution or '').strip().lower(), 1.0)
    width = max(64, int(round(width * scale / 8) * 8))
    height = max(64, int(round(height * scale / 8) * 8))
    return (width, height)

def agnes_video_frame_count(duration, fps=24):
    try:
        seconds = max(1, min(18, int(duration or 5)))
    except Exception:
        seconds = 5
    try:
        frame_rate = max(1, min(60, int(fps or 24)))
    except Exception:
        frame_rate = 24
    target = min(441, max(9, seconds * frame_rate))
    n = max(1, round((target - 1) / 8))
    return (min(441, max(9, 8 * n + 1)), frame_rate)

def _yuli_model_norm(model: str) -> str:
    return str(model or '').strip().lower().replace('_', '').replace('.', '').replace('-', '')

def yuli_is_veo_openai_model(model: str) -> bool:
    return _yuli_model_norm(model) in {'veo31', 'veo31fast'}

def yuli_openai_model_name(model: str) -> str:
    return 'veo_3_1-fast' if _yuli_model_norm(model) == 'veo31fast' else 'veo_3_1'

def yuli_openai_size(aspect_ratio: str) -> str:
    value = str(aspect_ratio or '').strip()
    if value == '9:16':
        return '9x16'
    return '16x9'

def lingjing_openai_video_model(model: str) -> str:
    value = str(model or '').strip() or 'veo_3_1-fast'
    lower = value.lower()
    if lower.startswith('veo3.1'):
        value = 'veo_3_1' + value[len('veo3.1'):]
    elif lower.startswith('veo3_1'):
        value = 'veo_3_1' + value[len('veo3_1'):]
    if value.lower().endswith('-4k'):
        value = value[:-2] + '4K'
    return value

def yuli_video_seconds(duration) -> str:
    try:
        value = int(duration)
    except Exception:
        value = 8
    if value <= 0:
        value = 8
    return str(value)

def volcengine_video_prompt_text(prompt, aspect_ratio='', duration=None):
    text = str(prompt or '').strip()
    suffixes = []
    ratio = str(aspect_ratio or '').strip()
    if ratio:
        suffixes.append(f'--ratio {ratio}')
    if not suffixes:
        return text
    suffix_text = ' '.join(suffixes)
    return f'{text} {suffix_text}'.strip() if text else suffix_text

def project_canvas_video_payload(request: VideoGenerationRequest, provider_id: str, model: str, references: List[Dict[str, Any]]) -> CanvasVideoRequest:
    image_refs = [AIReference(url=item['adapter_url'], name=item.get('name') or 'reference', kind='image', mime=item.get('mime') or '') for item in references if item.get('kind') == 'image']
    video_refs = [item['adapter_url'] for item in references if item.get('kind') == 'video']
    audio_refs = [item['adapter_url'] for item in references if item.get('kind') == 'audio']
    # T8 归一化把未指定的 size/aspect_ratio/resolution 置为 None（供路由识别「未指定」），
    # 但 CanvasVideoRequest 这三个字段是 str 契约（空串=未指定，与归一化前行为一致）——None 会触发 pydantic 400。
    return CanvasVideoRequest(prompt=request.prompt, provider_id=provider_id, model=model, duration=request.duration, width=request.width, height=request.height, aspect_ratio=request.aspect_ratio or '', resolution=request.resolution or '', size=request.size or '', quality=request.quality, media_type=request.media_type, images=image_refs, videos=video_refs, audios=audio_refs, enhance_prompt=request.enhance_prompt, enable_upsample=request.enable_upsample, watermark=request.watermark, seed=request.seed, camera_fixed=request.camera_fixed, return_last_frame=request.return_last_frame, generate_audio=request.generate_audio, multimodal=bool(video_refs or audio_refs or len(image_refs) > 1))

def runninghub_configured_video_model_definition(provider: Mapping[str, Any], model: str) -> Optional[Dict[str, Any]]:
    cached = provider.get('_runninghub_video_model_definitions')
    if isinstance(cached, Mapping) and isinstance(cached.get(model), Mapping):
        return dict(cached[model])
    capabilities = provider.get('video_model_capabilities')
    capability = capabilities.get(model) if isinstance(capabilities, Mapping) else None
    schema = capability.get('adapter_schema') if isinstance(capability, Mapping) else None
    if not isinstance(schema, Mapping):
        return None
    return {'name_en': model, 'endpoint': str(schema.get('endpoint') or model).strip(), 'output_type': 'video', 'params': [dict(item) for item in schema.get('params') or [] if isinstance(item, Mapping)]}

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

def runninghub_video_incompatible_parameters(payload: CanvasVideoRequest, params: List[Dict[str, Any]], *, image_to_video: bool) -> List[str]:
    """Validate schema-dependent RunningHub fields before task submission."""
    incompatible: List[str] = []

    def add(name: str) -> None:
        if name not in incompatible:
            incompatible.append(name)

    def usable_field(*keys: str, preferred: Any=None):
        field = runninghub_schema_field(params, *keys)
        if not field:
            return None
        options = runninghub_schema_options(field)
        if preferred is not None and options and (str(preferred) not in options):
            return None
        return field
    aspect = str(payload.aspect_ratio or '16:9').strip() or '16:9'
    if aspect != '16:9':
        aspect_field = usable_field('aspectRatio', 'ratio', preferred=aspect)
        size_value = runninghub_size_for_aspect(aspect)
        size_field = usable_field('size', preferred=size_value) if runninghub_aspect_from_size(size_value, '') == aspect else None
        if not aspect_field and (not size_field):
            add('aspect_ratio')
    if payload.duration != 5:
        if payload.duration < 1 or payload.duration > 60 or (not usable_field('duration', preferred=str(payload.duration))):
            add('duration')
    resolution = str(payload.resolution or '').strip().lower()
    if resolution and (not usable_field('resolution', preferred=resolution)):
        add('resolution')
    if payload.watermark and (not usable_field('watermark')):
        add('watermark')
    if payload.generate_audio and (not usable_field('generateAudio')):
        add('generate_audio')
    image_count = len(payload.images or [])
    first_field = runninghub_schema_field(params, 'firstFrameUrl', 'first_frame_url', 'firstFrameImage', 'first_frame_image')
    if not image_count and (image_to_video or runninghub_schema_field_required(first_field)):
        add('image_reference_count')
    elif image_count:
        image_field = runninghub_schema_field(params, 'imageUrls', 'image_urls', 'imageUrl', 'image_url', 'referenceImages', 'referenceImageUrls')
        if image_field:
            key = str(image_field.get('fieldKey') or '')
            multiple = key.endswith('s') or image_field.get('multipleInputs') is True
            if image_count > (10 if multiple else 1):
                add('image_reference_count')
        else:
            last_field = runninghub_schema_field(params, 'lastFrameUrl', 'last_frame_url', 'lastFrameImage', 'last_frame_image')
            supported_count = int(bool(first_field or image_to_video)) + int(bool(last_field))
            if not supported_count or image_count > supported_count:
                add('image_reference_count')
    return incompatible

def canvas_video_project_media_values(payload: CanvasVideoRequest) -> List[str]:
    values = [str(getattr(ref, 'url', '') or '').strip() for ref in payload.images or []]
    values.extend((str(value or '').strip() for value in payload.videos or []))
    values.extend((str(value or '').strip() for value in payload.audios or []))
    return [value for value in values if value]

def require_canvas_video_project_media_scope(payload: CanvasVideoRequest) -> None:
    """Reject project-media references unless an internal project task owns the call."""
    require_project_media_values_scope(canvas_video_project_media_values(payload))

def chat_provider_supports_audio(provider: Mapping[str, Any]) -> bool:
    """Require an explicit provider capability before sending conversation audio."""
    provider = provider if isinstance(provider, Mapping) else {}
    if str(provider.get('protocol') or '').strip().lower() in {'codex', 'gemini-cli'}:
        return False
    return bool(provider.get('chat_audio_input') or provider.get('audio_input') or provider.get('supports_audio_input') or provider.get('supports_audio'))

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

def _photoshop_protocol_error(status: int, code: str, message: str) -> HTTPException:
    return HTTPException(status_code=status, detail={'code': code, 'message': message})

def _ensure_photoshop_loopback(request: Request) -> None:
    client_host = str(getattr(getattr(request, 'client', None), 'host', '') or '').strip().lower()
    host = client_host or str(getattr(request.url, 'hostname', '') or '').strip().lower()
    if host not in {'127.0.0.1', '::1', 'localhost'}:
        raise _photoshop_protocol_error(403, 'LOOPBACK_ONLY', 'Photoshop 连接器仅允许本机 127.0.0.1')

def _photoshop_public_request(record: Dict[str, Any]) -> Dict[str, Any]:
    result = copy.deepcopy(record)
    if result.get('state') == 'processing':
        result['state'] = 'pending'
    result.pop('expires_at_ms', None)
    return result

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

def conversation_node_by_id(canvas: Dict[str, Any], node_id: str) -> Optional[Dict[str, Any]]:
    return next((node for node in canvas.get('nodes') or [] if isinstance(node, dict) and str(node.get('id') or '') == str(node_id)), None)

def conversation_task_snapshot(canvas: Dict[str, Any], node_id: str, request_id: str) -> Optional[Dict[str, Any]]:
    node = conversation_node_by_id(canvas, node_id)
    if not isinstance(node, dict) or node.get('type') != 'smart-conversation':
        return None
    messages = [item for item in node.get('messages') or [] if isinstance(item, dict)]
    user_message = next((item for item in messages if str(item.get('request_id') or '') == request_id and item.get('role') == 'user'), None)
    if user_message is None:
        return None
    assistant_message = next((item for item in messages if str(item.get('request_id') or '') == request_id and item.get('role') == 'assistant'), None)
    failed = bool(user_message.get('status') == 'failed' or (assistant_message and assistant_message.get('status') == 'failed'))
    status = 'failed' if failed else 'completed' if assistant_message else 'pending'
    return {'status': status, 'request_id': request_id, 'message': copy.deepcopy(user_message), 'assistant': copy.deepcopy(assistant_message) if assistant_message else None}

def forget_project_conversation_task(project_id: str, node_id: str, request_id: str, *, handle: Optional[asyncio.Future]=None) -> None:
    task_key = (str(project_id), str(node_id), str(request_id))
    with PROJECT_CONVERSATION_TASKS_LOCK:
        current = PROJECT_CONVERSATION_TASKS.get(task_key)
        if current is not None and (handle is None or current is handle):
            PROJECT_CONVERSATION_TASKS.pop(task_key, None)

def project_conversation_ready_result(project_id: str, node_id: str, request_id: str) -> Optional[Dict[str, Any]]:
    task_key = (str(project_id), str(node_id), str(request_id))
    with PROJECT_CONVERSATION_TASKS_LOCK:
        ready = PROJECT_CONVERSATION_READY_RESULTS.get(task_key)
        return copy.deepcopy(ready) if ready is not None else None

def remember_project_conversation_ready_result(project_id: str, node_id: str, request_id: str, ready: Dict[str, Any]) -> None:
    task_key = (str(project_id), str(node_id), str(request_id))
    with PROJECT_CONVERSATION_TASKS_LOCK:
        PROJECT_CONVERSATION_READY_RESULTS[task_key] = copy.deepcopy(ready)

def forget_project_conversation_ready_result(project_id: str, node_id: str, request_id: str) -> None:
    task_key = (str(project_id), str(node_id), str(request_id))
    with PROJECT_CONVERSATION_TASKS_LOCK:
        PROJECT_CONVERSATION_READY_RESULTS.pop(task_key, None)

def conversation_error_message(error: Exception) -> str:
    detail = getattr(error, 'detail', None)
    if isinstance(detail, dict):
        return str(detail.get('message') or detail.get('detail') or json.dumps(detail, ensure_ascii=False))[:LLM_MESSAGE_MAX_LENGTH]
    return str(detail or error or '对话适配器失败')[:LLM_MESSAGE_MAX_LENGTH]

def conversation_task_response(canvas: Dict[str, Any], snapshot: Dict[str, Any]) -> Dict[str, Any]:
    return {'ok': True, 'canvas': canvas, 'status': snapshot['status'], 'request_id': snapshot['request_id'], 'message': snapshot['message'], 'assistant': snapshot.get('assistant'), 'saved': True}

def sanitize_export_filename(name: str, fallback: str) -> str:
    base = os.path.basename(str(name or '').strip()) or fallback
    base = re.sub('[\\\\/:*?"<>|]+', '_', base)
    return base or fallback

def find_asset_item_in_library(lib, item_id, library_id=''):
    for library in lib.get('libraries', []):
        if library_id and library.get('id') != library_id:
            continue
        for cat in library.get('categories', []):
            for item in cat.get('items', []):
                if item.get('id') == item_id:
                    return item
    return None
