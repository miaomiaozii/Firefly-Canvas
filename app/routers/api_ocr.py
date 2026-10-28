"""Router: /api/ocr —— 图片文字提取（OCR），#256。

ADR-lite 选型注释（票面要求选型写注释）：
- 选型：rapidocr-onnxruntime（纯 Python + onnxruntime，CPU 推理，无需 GPU /
  无需系统级依赖，pip 可离线安装）。
- 与 ADR 0004 的关系：0004 禁的是图片*生成*链路的进程内推理运行时（详见
  docs/adr/0004）。OCR 是图片
  *轻量后处理*（文字识别，非生成推理），且本端点只读项目已存在的媒体文件、
  不产生活体运行时，故判定不冲突，维持票面首选方案；若后续维护者判定冲突，
  切换成本被隔离在 `_get_ocr_engine` + `ocr_bytes` 两个函数内（换成外部
  OCR API provider 即可，路由层不变）。
- 移植来源：ComfyArt `src/elements.ts:33-35` `runOcr` 交互模式（图片节点
  「提取文字」→ 服务端 OCR → 落可编辑文本），MIT 许可，已在 NOTICE.md 登记。
  本实现为原创代码，仅交互模式参考。
"""
from __future__ import annotations

import sys as _sys
import os as _os

_app_main = _sys.modules.get(_os.environ.get('FIREFLY_MAIN_MODULE', '')) or _sys.modules.get('main') or _sys.modules['__main__']

import asyncio
import base64
import re
import threading
import time
from io import BytesIO
from pathlib import Path
from typing import Any, Dict, List
from urllib.parse import urlparse

import httpx
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from app.core import Image, ProjectNotFound, ProjectStoreError
from app.project_activity import ProjectCleanupActive, assert_project_owner_admissible

router = APIRouter()

# 与 api_project_library 的媒体 URL 同款解析规则（api_canvas_assets.py:184 同源）。
_PROJECT_MEDIA_URL_RE = re.compile(r'^/api/project-library/([^/]+)/media/([^/]+?)(?:/preview)?/?$')
# 25MB 上限：OCR 预处理会把大图缩小，超过此尺寸的截图/图基本没有识别价值，
# 且 onnx 推理耗时随像素数线性涨，必须挡住。
MAX_OCR_IMAGE_BYTES = 25 * 1024 * 1024
# 只放行位图格式（PIL 能确认解开的才送引擎，挡掉把任意文件当图片打）。
_ALLOWED_IMAGE_FORMATS = {'PNG', 'JPEG', 'WEBP', 'GIF', 'BMP'}
_HTTP_FETCH_TIMEOUT_SECONDS = 20.0

_ocr_engine = None
_ocr_engine_lock = threading.Lock()


class OcrRequest(BaseModel):
    # 三选一（按优先级 asset_id > 内部媒体 URL > 外部 URL）：
    # asset_id 需配 project_id，走 project_store 就地解析文件字节；
    # image_url 支持 /api/project-library/{pid}/media/{aid}（就地解析，不发 HTTP）、
    # data:...;base64 或 http(s) 远端 URL。
    image_url: str = ''
    project_id: str = ''
    asset_id: str = ''


def _get_ocr_engine():
    """延迟初始化 RapidOCR 单例（模型加载 ~800ms，只在第一次请求付）。"""
    global _ocr_engine
    if _ocr_engine is None:
        with _ocr_engine_lock:
            if _ocr_engine is None:
                try:
                    from rapidocr_onnxruntime import RapidOCR
                except ImportError as exc:
                    raise HTTPException(
                        status_code=503,
                        detail='OCR 引擎未安装：pip install rapidocr-onnxruntime',
                    ) from exc
                _ocr_engine = RapidOCR()
    return _ocr_engine


def ocr_bytes(data: bytes) -> Dict[str, Any]:
    """对图片字节跑 OCR，返回 {text, items, elapsed_ms}。

    纯函数接缝（单测直接打这里）；rapidocr 是同步 CPU 推理，
    端点层用 asyncio.to_thread 移出事件循环。
    """
    engine = _get_ocr_engine()
    result, _elapse = engine(data)
    items: List[Dict[str, Any]] = []
    for entry in (result or []):
        # rapidocr 条目形如 [box(4 点), text, score]
        text = str(entry[1]).strip()
        if not text:
            continue
        items.append({
            'text': text,
            'score': round(float(entry[2]), 4),
            'box': [list(map(float, point)) for point in entry[0]],
        })
    return {'text': '\n'.join(item['text'] for item in items), 'items': items}


def _validate_image_bytes(data: bytes) -> None:
    if len(data) > MAX_OCR_IMAGE_BYTES:
        raise HTTPException(status_code=413, detail='图片超过 25MB 上限')
    try:
        with Image.open(BytesIO(data)) as img:
            if img.format not in _ALLOWED_IMAGE_FORMATS:
                raise ValueError(img.format or 'unknown')
    except Exception as exc:
        raise HTTPException(status_code=415, detail=f'不是受支持的图片（{exc}）') from exc


def _decode_data_url(value: str) -> bytes:
    head, _, payload = value.partition(',')
    if not head.startswith('data:') or 'base64' not in head:
        raise HTTPException(status_code=415, detail='只支持 base64 编码的 data URL')
    try:
        return base64.b64decode(payload, validate=True)
    except Exception as exc:
        raise HTTPException(status_code=415, detail=f'data URL 解码失败（{exc}）') from exc


def _load_project_media_bytes(project_id: str, asset_id: str) -> bytes:
    assert_project_owner_admissible(project_id)

    def resolve() -> bytes:
        record = _app_main.project_store_call('media_record', project_id, asset_id)
        path = Path(record['path'])
        if not path.is_file():
            raise ProjectStoreError('项目媒体文件不存在')
        data = path.read_bytes()
        if len(data) > MAX_OCR_IMAGE_BYTES:
            raise HTTPException(status_code=413, detail='图片超过 25MB 上限')
        return data

    try:
        return resolve()
    except ProjectCleanupActive as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except ProjectNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except (ProjectStoreError, OSError) as exc:
        raise HTTPException(status_code=400, detail=f'项目媒体解析失败：{exc}') from exc


async def _fetch_http_bytes(value: str) -> bytes:
    parsed = urlparse(value)
    if parsed.scheme not in {'http', 'https'}:
        raise HTTPException(status_code=400, detail=f'不支持的 URL 协议：{parsed.scheme or "空"}')
    try:
        async with httpx.AsyncClient(timeout=_HTTP_FETCH_TIMEOUT_SECONDS, follow_redirects=True) as client:
            response = await client.get(value)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f'图片下载失败：{exc}') from exc
    if response.status_code != 200:
        raise HTTPException(status_code=400, detail=f'图片下载失败：HTTP {response.status_code}')
    return response.content


async def _resolve_source_bytes(payload: OcrRequest) -> bytes:
    if payload.asset_id:
        if not payload.project_id:
            raise HTTPException(status_code=422, detail='asset_id 需要配套 project_id')
        return await asyncio.to_thread(_load_project_media_bytes, payload.project_id, payload.asset_id)
    url = payload.image_url.strip()
    if not url:
        raise HTTPException(status_code=422, detail='请提供 asset_id 或 image_url')
    match = _PROJECT_MEDIA_URL_RE.match(url)
    if match:
        return await asyncio.to_thread(_load_project_media_bytes, match.group(1), match.group(2))
    if url.startswith('data:'):
        return _decode_data_url(url)
    return await _fetch_http_bytes(url)


@router.post('/api/ocr')
async def ocr_image(payload: OcrRequest) -> Dict[str, Any]:
    """图片 → 文字。返回 {text, items:[{text, score, box}], elapsed_ms}。"""
    data = await _resolve_source_bytes(payload)
    _validate_image_bytes(data)
    started = time.monotonic()
    result = await asyncio.to_thread(ocr_bytes, data)
    result['elapsed_ms'] = int((time.monotonic() - started) * 1000)
    return result
