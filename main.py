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
from firefly_generation import (
    ImageGenerationRequest,
    ModelRoutingError,
    VIDEO_EXECUTION_PROTOCOLS,
    VideoGenerationRequest,
    apply_angle_preset,
    normalize_image_model_capabilities,
    normalize_video_model_capabilities,
    route_image_generation,
    route_video_generation,
)
from local_edit import (
    as_rgba,
    composite_preserve,
    composite_rect,
    encode_png,
    rasterize_preserve_mask,
    selection_json,
)

QUIET_ACCESS_PATHS = {
    "/api/queue_status",
    "/api/canvases",
    "/api/canvases/trash",
}
QUIET_ACCESS_PREFIXES = (
    "/api/canvases/",
)

class QuietAccessLogFilter(logging.Filter):
    def filter(self, record):
        args = record.args if isinstance(record.args, tuple) else ()
        if len(args) >= 3:
            path = str(args[2]).split("?", 1)[0]
            status = int(args[4]) if len(args) >= 5 and str(args[4]).isdigit() else 0
            quiet_dynamic = any(path.startswith(prefix) and path.endswith("/meta") for prefix in QUIET_ACCESS_PREFIXES)
            if (path in QUIET_ACCESS_PATHS or quiet_dynamic) and status < 400:
                return False
        message = record.getMessage()
        if any(f'"GET {path}' in message and '" 200' in message for path in QUIET_ACCESS_PATHS):
            return False
        if 'GET /api/canvases/' in message and '/meta' in message and '" 200' in message:
            return False
        return True

logging.getLogger("uvicorn.access").addFilter(QuietAccessLogFilter())

app = FastAPI()
FIREFLY_PROJECT_STORE: Optional[ProjectStore] = None
PROJECT_MEDIA_IMPORT_TASKS: Dict[str, Dict[str, Any]] = {}
PROJECT_MEDIA_IMPORT_TASKS_LOCK = Lock()
PROJECT_MEDIA_IMPORT_EXECUTOR: Optional[ThreadPoolExecutor] = None
PROJECT_MEDIA_IMPORT_FUTURES: Dict[str, Future] = {}
PROJECT_MEDIA_IMPORT_ACCEPTING = True
PROJECT_MEDIA_IMPORT_SHUTTING_DOWN = False
PROJECT_MEDIA_IMPORT_MAX_PENDING = 32
PROJECT_MEDIA_IMPORT_MAX_PER_PROJECT = 8
PROJECT_IMAGE_GENERATION_EXECUTOR: Optional[ThreadPoolExecutor] = None
PROJECT_IMAGE_GENERATION_FUTURES: Dict[str, Future] = {}
PROJECT_IMAGE_GENERATION_ORPHANS: Dict[str, set[str]] = {}
PROJECT_VIDEO_GENERATION_FUTURES: Dict[str, Future] = {}
PROJECT_VIDEO_GENERATION_ORPHANS: Dict[str, set[str]] = {}
PROJECT_CONVERSATION_TASKS: Dict[Tuple[str, str, str], asyncio.Future] = {}
PROJECT_CONVERSATION_READY_RESULTS: Dict[Tuple[str, str, str], Dict[str, Any]] = {}
PROJECT_CONVERSATION_TASKS_LOCK = Lock()
PROJECT_CONVERSATION_ADMISSION_LOCK = Lock()
PROJECT_CONVERSATION_RUNNER: Optional["ProjectConversationTaskRunner"] = None
PROJECT_CONVERSATION_MAX_PENDING = 32
PROJECT_CONVERSATION_MAX_PER_PROJECT = 8
PROJECT_CONVERSATION_MAX_PER_NODE = 4
PROJECT_CONVERSATION_MAX_IMAGE_REFERENCES = 8
PROJECT_CONVERSATION_MAX_VIDEO_REFERENCES = 3
PROJECT_CONVERSATION_MAX_AUDIO_REFERENCES = 3
PROJECT_CONVERSATION_MEDIA_MAX_FILE_BYTES = 16 * 1024 * 1024
PROJECT_CONVERSATION_MEDIA_MAX_TOTAL_BYTES = 32 * 1024 * 1024
PROJECT_CONVERSATION_IMAGE_MAX_PIXELS = 24_000_000
PROJECT_IMAGE_GENERATION_MAX_PENDING = 32
PROJECT_IMAGE_GENERATION_MAX_PER_PROJECT = 8
PROJECT_GENERATED_VIDEO_MAX_BYTES = 1024 * 1024 * 1024
# Agent work is deliberately separate from the conversation runner and from
# the legacy canvas task registry.  The registry is only a process-local
# scheduling hint; the authoritative plan/task state lives in the project
# canvas so a reload can show exactly what was planned and what ran.
PROJECT_AGENT_TASKS: Dict[str, Dict[str, Any]] = {}
PROJECT_AGENT_TASKS_LOCK = RLock()
PROJECT_AGENT_FUTURES: Dict[str, Future] = {}
PROJECT_AGENT_RUNNING_IDS: set[str] = set()
PROJECT_AGENT_RECOVERY_LOCK = RLock()
PROJECT_AGENT_EXECUTION_LOCKS: Dict[Tuple[str, str], Lock] = {}
PROJECT_AGENT_EXECUTION_LOCKS_GUARD = Lock()
PROJECT_AGENT_ADMISSION_LOCK = Lock()
PROJECT_AGENT_MAX_PENDING = 32
PROJECT_AGENT_MAX_PER_PROJECT = 8
PROJECT_AGENT_MAX_PER_NODE = 8
PROJECT_AGENT_MAX_TASKS_PER_PLAN = 8
PROJECT_AGENT_TASK_TIMEOUT_SECONDS = 15 * 60
# These are intentionally injectable seams.  Tests replace them with an
# offline fake; production may install an online adapter at process startup.
agent_image_adapter = None
agent_video_adapter = None


def _offline_local_edit_adapter(request: Dict[str, Any]) -> bytes:
    """Deterministic fake retained as an explicit test seam."""
    image = as_rgba(bytes(request["image"]))
    r, g, b, a = image.getpixel((0, 0))
    image.putpixel((0, 0), ((r + 1) % 256, g, b, a))
    return encode_png(image)


async def project_local_edit_online_adapter(request: Dict[str, Any]) -> bytes:
    """Run local-edit generation through the normal routed online boundary.

    The local-edit endpoint owns cropping and compositing; this adapter only
    turns the already-prepared input asset plus project references into one
    routed image request and returns the model image bytes.
    """
    project_id = str(request.get("project_id") or "").strip()
    input_asset_id = str(request.get("input_asset_id") or "").strip()
    reference_asset_ids = [str(value).strip() for value in request.get("reference_asset_ids") or [] if str(value).strip()]
    if not project_id or not input_asset_id:
        raise ProjectStoreError("局部编辑在线适配器缺少项目或模型输入资源")
    if input_asset_id not in reference_asset_ids:
        reference_asset_ids.insert(0, input_asset_id)
    prompt = str(request.get("prompt") or "").strip()
    size = str(request.get("size") or "1024x1024").strip()
    request_snapshot = ImageGenerationRequest(
        prompt=prompt,
        size=size,
        width=None,
        height=None,
        aspect_ratio=str(request.get("aspect_ratio") or "1:1"),
        resolution=str(request.get("resolution") or "1k"),
        reference_asset_ids=tuple(reference_asset_ids),
        media_type="image",
        quality=str(request.get("quality") or "auto"),
        count=1,
    )
    route = route_image_generation(
        request_snapshot,
        load_api_providers(),
        provider_id=str(request.get("provider_id") or ""),
        model=str(request.get("model") or ""),
    )
    result = await project_agent_default_image_adapter({
        "project_id": project_id,
        "task_id": f"local-edit-{uuid.uuid4().hex}",
        "model_type": "image",
        "count": 1,
        "prompt": prompt,
        "params": {
            "size": size,
            "aspect_ratio": request_snapshot.aspect_ratio,
            "resolution": request_snapshot.resolution,
            "quality": request_snapshot.quality,
            "provider_id": route.provider_id,
            "model": route.model,
            "reference_asset_ids": reference_asset_ids,
        },
    })
    candidate = (result.get("images") or [None])[0]
    candidate_type = str(candidate.get("type") or "").strip().lower() if isinstance(candidate, dict) else ""
    value = (candidate.get("url") or candidate.get("value") or candidate.get("b64_json")) if isinstance(candidate, dict) else candidate
    if isinstance(value, str):
        if value.startswith("data:") and ";base64," in value:
            value = value.split(";base64,", 1)[1]
            candidate_type = "b64"
        if candidate_type in {"b64", "base64", "inline_base64"}:
            encoded = re.sub(r"\s+", "", value)
            try:
                decoded = base64.b64decode(encoded + "=" * (-len(encoded) % 4), validate=True)
            except (ValueError, binascii.Error):
                decoded = b""
            if decoded:
                return decoded
    path = output_file_from_url(value)
    if path and Path(path).is_file():
        return Path(path).read_bytes()
    raise ProjectStoreError("在线局部编辑没有返回可读取的图片")


# Production uses the routed online boundary; tests and browser harnesses can
# replace this seam with the explicit offline adapter without touching D:.
local_edit_adapter = project_local_edit_online_adapter
PROJECT_LIFECYCLE_LOCKS: Dict[str, Lock] = {}
PROJECT_LIFECYCLE_LOCKS_GUARD = Lock()
FLY_MAX_PACKAGE_BYTES = 20 * 1024 * 1024 * 1024
FLY_MAX_REQUEST_BYTES = FLY_MAX_PACKAGE_BYTES + 1024 * 1024
FLY_IMPORT_UPLOAD_LOCK = Lock()


def _conversation_pending_task_keys_from_canvas(project_id: str, canvas: Optional[Dict[str, Any]]) -> set[Tuple[str, str, str]]:
    keys: set[Tuple[str, str, str]] = set()
    if not isinstance(canvas, dict):
        return keys
    for node in canvas.get("nodes") or []:
        if not isinstance(node, dict) or node.get("type") != "smart-conversation":
            continue
        node_id = str(node.get("id") or "")
        if not node_id:
            continue
        for message in node.get("messages") or []:
            if not isinstance(message, dict) or message.get("role") != "user" or message.get("status") != "pending":
                continue
            request_id = str(message.get("request_id") or "")
            if request_id:
                keys.add((str(project_id), node_id, request_id))
    return keys


def conversation_pending_task_keys(
    project_id: str,
    *,
    canvas: Optional[Dict[str, Any]] = None,
    include_all_projects: bool = False,
) -> set[Tuple[str, str, str]]:
    """Return pending users; all-project scans are opt-in for bounded request I/O."""
    if canvas is None:
        try:
            canvas = project_store_call("open_project", project_id).get("canvas")
        except (ProjectNotFound, ProjectStoreError, OSError, ValueError):
            canvas = None
    keys = _conversation_pending_task_keys_from_canvas(project_id, canvas)
    if not include_all_projects:
        return keys
    with PROJECT_LIBRARY_SWITCH_LOCK:
        store = FIREFLY_PROJECT_STORE
        if store is None:
            return keys
        try:
            projects = store.list_projects()
        except (ProjectNotFound, ProjectStoreError, OSError, ValueError):
            return keys
        for project in projects:
            candidate_id = str(project.get("id") or "")
            if not candidate_id:
                continue
            if candidate_id == str(project_id) and canvas is not None:
                continue
            try:
                opened = store.open_project(candidate_id)
            except (ProjectNotFound, ProjectStoreError, OSError, ValueError):
                continue
            keys.update(_conversation_pending_task_keys_from_canvas(candidate_id, opened.get("canvas")))
    return keys


class ProjectConversationTaskRunner:
    """Keep conversation work owned by the application lifecycle, not a request."""

    WORKER_COUNT = 4

    def __init__(self):
        self.loop: Optional[asyncio.AbstractEventLoop] = None
        self.queue: Optional[asyncio.Queue] = None
        self.workers: List[asyncio.Task] = []
        self.handles: set[asyncio.Future] = set()
        self.accepting = False
        self.node_locks: Dict[Tuple[str, str], asyncio.Lock] = {}

    async def start(self) -> None:
        if self.accepting:
            return
        self.loop = asyncio.get_running_loop()
        self.queue = asyncio.Queue()
        self.accepting = True
        self.workers = [
            asyncio.create_task(self._worker_loop(), name=f"firefly-conversation-worker-{index}")
            for index in range(self.WORKER_COUNT)
        ]

    def admission_error(
        self,
        project_id: str,
        node_id: str,
        request_id: str,
        *,
        canvas: Optional[Dict[str, Any]] = None,
        pending_keys: Optional[set[Tuple[str, str, str]]] = None,
    ) -> Optional[Dict[str, Any]]:
        task_key = (str(project_id), str(node_id), str(request_id))
        with PROJECT_CONVERSATION_TASKS_LOCK:
            current = PROJECT_CONVERSATION_TASKS.get(task_key)
            if current is not None and not current.done():
                return None
            active = {key for key, value in PROJECT_CONVERSATION_TASKS.items() if not value.done()}
        # Registry state is only a process-local hint.  After restart it is
        # empty while pending user messages remain durable in canvas.json; use
        # the union so a new POST cannot fill a slot that is already persisted.
        pending = pending_keys if pending_keys is not None else conversation_pending_task_keys(
            project_id,
            canvas=canvas,
            include_all_projects=True,
        )
        combined = (active | pending) - {task_key}
        if len(combined) >= PROJECT_CONVERSATION_MAX_PENDING:
            return {"code": "conversation_queue_global_full", "message": "对话队列已满，请稍后重试"}
        if sum(key[0] == task_key[0] for key in combined) >= PROJECT_CONVERSATION_MAX_PER_PROJECT:
            return {"code": "conversation_queue_project_full", "message": "该项目的对话队列已满，请稍后重试"}
        if sum(key[:2] == task_key[:2] for key in combined) >= PROJECT_CONVERSATION_MAX_PER_NODE:
            return {"code": "conversation_queue_node_full", "message": "该对话节点的待处理消息已达上限"}
        return None

    def submit(
        self,
        project_id: str,
        node_id: str,
        request_id: str,
        *,
        adapter=None,
        allow_persisted_overage: bool = False,
        pending_keys: Optional[set[Tuple[str, str, str]]] = None,
    ) -> bool:
        loop = self.loop
        queue = self.queue
        if not self.accepting or loop is None or queue is None:
            return False
        try:
            if asyncio.get_running_loop() is not loop:
                return False
        except RuntimeError:
            return False
        task_key = (str(project_id), str(node_id), str(request_id))
        with PROJECT_CONVERSATION_TASKS_LOCK:
            current = PROJECT_CONVERSATION_TASKS.get(task_key)
            if current is not None and not current.done():
                return False
            if current is not None:
                PROJECT_CONVERSATION_TASKS.pop(task_key, None)
            active_keys = {key for key, value in PROJECT_CONVERSATION_TASKS.items() if not value.done()}
        pending_keys = pending_keys if pending_keys is not None else conversation_pending_task_keys(project_id)
        combined_keys = active_keys | pending_keys
        current_is_persisted = task_key in combined_keys
        if (
            (len(combined_keys) > PROJECT_CONVERSATION_MAX_PENDING and not allow_persisted_overage)
            if current_is_persisted
            else len(combined_keys) >= PROJECT_CONVERSATION_MAX_PENDING
        ):
            return False
        project_count = sum(key[0] == task_key[0] for key in combined_keys)
        project_limit_reached = (
            project_count > PROJECT_CONVERSATION_MAX_PER_PROJECT and not allow_persisted_overage
            if current_is_persisted
            else project_count >= PROJECT_CONVERSATION_MAX_PER_PROJECT
        )
        if project_limit_reached:
            return False
        node_count = sum(key[:2] == task_key[:2] for key in combined_keys)
        node_limit_reached = (
            node_count > PROJECT_CONVERSATION_MAX_PER_NODE and not allow_persisted_overage
            if current_is_persisted
            else node_count >= PROJECT_CONVERSATION_MAX_PER_NODE
        )
        if node_limit_reached:
            return False
        with PROJECT_CONVERSATION_TASKS_LOCK:
            current = PROJECT_CONVERSATION_TASKS.get(task_key)
            if current is not None and not current.done():
                return False
            handle = loop.create_future()
            PROJECT_CONVERSATION_TASKS[task_key] = handle
            self.handles.add(handle)
        try:
            queue.put_nowait((task_key, handle, adapter, 0))
        except Exception:
            with PROJECT_CONVERSATION_TASKS_LOCK:
                if PROJECT_CONVERSATION_TASKS.get(task_key) is handle:
                    PROJECT_CONVERSATION_TASKS.pop(task_key, None)
            self.handles.discard(handle)
            handle.cancel()
            return False
        return True

    async def _worker_loop(self) -> None:
        queue = self.queue
        assert queue is not None
        while True:
            item = await queue.get()
            task_key, handle, adapter = item[:3]
            retry_count = int(item[3]) if len(item) > 3 else 0
            retain_for_retry = False
            try:
                node_key = task_key[:2]
                node_lock = self.node_locks.setdefault(node_key, asyncio.Lock())
                async with node_lock:
                    retain_for_retry = not await run_project_conversation_task(*task_key, adapter=adapter)
            except asyncio.CancelledError:
                if not handle.done():
                    handle.cancel()
                raise
            except Exception as exc:
                # The normal task path persists failures. This second guard covers
                # failures in that path itself so an adapter error cannot strand pending.
                merged = None
                merge_error = None
                try:
                    merged = await merge_project_conversation_result(*task_key, error=exc)
                except Exception as merge_exc:
                    merge_error = merge_exc
                    logging.getLogger(__name__).exception(
                        "conversation task failure could not be persisted: %s", task_key,
                    )
                if merged is None:
                    # A merge conflict budget exhaustion (or an exception in
                    # the failure path) must leave a durable uncertain record;
                    # otherwise canvas.json keeps a pending user with no way to
                    # recover it after restart. Retry while the project exists.
                    persisted = await asyncio.to_thread(
                        persist_project_conversation_uncertain,
                        *task_key,
                        error=merge_error or exc,
                    )
                    if persisted or not isinstance(merge_error, ProjectNotFound):
                        retain_for_retry = True
                        if self.accepting and self.queue is queue and not handle.done():
                            await asyncio.sleep(min(0.025 * (2 ** min(retry_count, 4)), 0.4))
                            if self.accepting and self.queue is queue and not handle.done():
                                queue.put_nowait((task_key, handle, adapter, retry_count + 1))
                    elif not handle.done():
                        # There is no owning project left to recover; a
                        # ProjectNotFound cannot strand a canvas pending record.
                        handle.set_result(None)
                elif not handle.done():
                    handle.set_result(None)
            else:
                if retain_for_retry:
                    await asyncio.sleep(min(0.025 * (2 ** min(retry_count, 4)), 0.4))
                    if self.accepting and self.queue is queue and not handle.done():
                        queue.put_nowait((task_key, handle, adapter, retry_count + 1))
                    else:
                        retain_for_retry = False
                elif not handle.done():
                    handle.set_result(None)
            finally:
                if not retain_for_retry:
                    forget_project_conversation_task(*task_key, handle=handle)
                    self.handles.discard(handle)
                    if self.accepting:
                        schedule_project_conversation_task(
                            *task_key,
                            adapter=adapter,
                            recover_fifo=True,
                        )
                queue.task_done()
            if not self.accepting:
                return

    async def stop(self) -> None:
        self.accepting = False
        current_loop = asyncio.get_running_loop()
        workers = list(self.workers)
        current_workers = []
        foreign_waiters = []
        unreachable_workers = []
        for worker in workers:
            if worker.done():
                continue
            worker_loop = worker.get_loop()
            if worker_loop is current_loop:
                worker.cancel()
                current_workers.append(worker)
            elif worker_loop.is_running() and not worker_loop.is_closed():
                completion = current_loop.create_future()

                def mark_done(_worker, waiter=completion):
                    def resolve():
                        if not waiter.done():
                            waiter.set_result(None)
                    try:
                        current_loop.call_soon_threadsafe(resolve)
                    except RuntimeError:
                        pass

                def cancel_on_owner(target=worker, callback=mark_done):
                    if target.done():
                        callback(target)
                        return
                    target.add_done_callback(callback)
                    target.cancel()

                try:
                    worker_loop.call_soon_threadsafe(cancel_on_owner)
                    foreign_waiters.append(completion)
                except RuntimeError:
                    completion.cancel()
                    unreachable_workers.append(worker)
            else:
                unreachable_workers.append(worker)
        if current_workers:
            await asyncio.gather(*current_workers, return_exceptions=True)
        if foreign_waiters:
            await asyncio.gather(*foreign_waiters, return_exceptions=True)
        unfinished = [worker for worker in unreachable_workers if not worker.done()]
        if unfinished:
            raise RuntimeError("对话 worker 所属事件循环不可用，runner 状态已保留且未完成关闭")
        with PROJECT_CONVERSATION_TASKS_LOCK:
            for task_key, handle in list(PROJECT_CONVERSATION_TASKS.items()):
                if handle in self.handles:
                    if not handle.done():
                        handle_loop = handle.get_loop()
                        if handle_loop is current_loop:
                            handle.cancel()
                        elif handle_loop.is_running() and not handle_loop.is_closed():
                            try:
                                handle_loop.call_soon_threadsafe(handle.cancel)
                            except RuntimeError:
                                pass
                    PROJECT_CONVERSATION_TASKS.pop(task_key, None)
        self.handles.clear()
        self.workers = []
        self.queue = None
        self.loop = None
        self.node_locks.clear()

class ProjectMediaImportActive(RuntimeError):
    pass

def project_lifecycle_lock(project_id: str) -> Lock:
    with PROJECT_LIFECYCLE_LOCKS_GUARD:
        return PROJECT_LIFECYCLE_LOCKS.setdefault(project_id, Lock())

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
                raise MultiPartException("Fly 项目包超过 20GB 安全限制")
        super().on_part_data(data, start, end)

async def acquire_fly_import_slot() -> None:
    while not FLY_IMPORT_UPLOAD_LOCK.acquire(blocking=False):
        await asyncio.sleep(0.05)

def ensure_project_media_import_executor() -> ThreadPoolExecutor:
    global PROJECT_MEDIA_IMPORT_EXECUTOR, PROJECT_MEDIA_IMPORT_ACCEPTING
    with PROJECT_MEDIA_IMPORT_TASKS_LOCK:
        if not PROJECT_MEDIA_IMPORT_ACCEPTING:
            if PROJECT_MEDIA_IMPORT_SHUTTING_DOWN:
                raise RuntimeError("媒体导入服务正在关闭")
            PROJECT_MEDIA_IMPORT_ACCEPTING = True
        if PROJECT_MEDIA_IMPORT_EXECUTOR is None:
            PROJECT_MEDIA_IMPORT_EXECUTOR = ThreadPoolExecutor(max_workers=4, thread_name_prefix="firefly-media")
        return PROJECT_MEDIA_IMPORT_EXECUTOR

def ensure_project_image_generation_executor() -> ThreadPoolExecutor:
    global PROJECT_IMAGE_GENERATION_EXECUTOR
    with CANVAS_TASK_LOCK:
        if PROJECT_IMAGE_GENERATION_EXECUTOR is None:
            PROJECT_IMAGE_GENERATION_EXECUTOR = ThreadPoolExecutor(max_workers=4, thread_name_prefix="firefly-generation")
        return PROJECT_IMAGE_GENERATION_EXECUTOR

def public_project_media_import_task(task: Dict[str, Any]) -> Dict[str, Any]:
    return {key: value for key, value in task.items() if key not in {"project_id", "source_key"}}

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# --- WebSocket 状态管理器 ---
class ConnectionManager:
    def __init__(self):
        self.active_connections: List[WebSocket] = []
        self.user_connections: Dict[str, WebSocket] = {}
        self.connection_clients: Dict[WebSocket, str] = {}

    async def connect(self, websocket: WebSocket, client_id: str = None):
        await websocket.accept()
        self.active_connections.append(websocket)
        self.connection_clients[websocket] = client_id or f"anon-{id(websocket)}"
        if client_id:
            self.user_connections[client_id] = websocket
        print(f"WS Connected. Total: {len(self.active_connections)}, Online: {self.online_count()}")
        await self.broadcast_count()
        if str(client_id or "").lower().startswith("ps_"):
            with PHOTOSHOP_ROUND_TRIP_LOCK:
                _photoshop_prune_requests_locked()
                pending = [
                    _photoshop_public_request(record)
                    for record in PHOTOSHOP_ROUND_TRIP_REQUESTS.values()
                    if record.get("state") == "pending"
                ]
            for binding in pending:
                try:
                    await websocket.send_text(json.dumps({"type": "photoshop_round_trip_created", "data": binding}))
                except Exception as exc:
                    print(f"Photoshop pending replay error: {exc}")
                    break

    async def disconnect(self, websocket: WebSocket, client_id: str = None):
        if websocket in self.active_connections:
            self.active_connections.remove(websocket)
        self.connection_clients.pop(websocket, None)
        if client_id and self.user_connections.get(client_id) is websocket:
            del self.user_connections[client_id]
        print(f"WS Disconnected. Total: {len(self.active_connections)}, Online: {self.online_count()}")
        await self.broadcast_count()

    def online_count(self):
        visible_clients = {
            client_id for client_id in self.connection_clients.values()
            if client_id and not str(client_id).startswith("canvas_")
        }
        return len(visible_clients)

    async def broadcast_count(self):
        count = self.online_count()
        data = json.dumps({"type": "stats", "online_count": count})
        for connection in self.active_connections[:]:
            try:
                await connection.send_text(data)
            except Exception as e:
                print(f"Broadcast error: {e}")
                self.active_connections.remove(connection)

    async def broadcast_new_image(self, image_data: dict):
        data = json.dumps({"type": "new_image", "data": image_data})
        for connection in self.active_connections[:]:
            try:
                await connection.send_text(data)
            except Exception as e:
                print(f"Broadcast image error: {e}")
                self.active_connections.remove(connection)

    async def broadcast_canvas_updated(self, canvas_id: str, updated_at: int, client_id: str = ""):
        data = json.dumps({
            "type": "canvas_updated",
            "canvas_id": canvas_id,
            "updated_at": updated_at,
            "client_id": client_id or "",
        })
        for connection in self.active_connections[:]:
            try:
                await connection.send_text(data)
            except Exception as e:
                print(f"Broadcast canvas error: {e}")
                self.active_connections.remove(connection)

    async def broadcast_asset_library_updated(self, updated_at: int = 0):
        data = json.dumps({
            "type": "asset_library_updated",
            "updated_at": updated_at or now_ms(),
        })
        for connection in self.active_connections[:]:
            try:
                await connection.send_text(data)
            except Exception as e:
                print(f"Broadcast asset library error: {e}")
                self.active_connections.remove(connection)

    async def send_personal_message(self, message: dict, client_id: str):
        ws = self.user_connections.get(client_id)
        if ws:
            try:
                await ws.send_text(json.dumps(message))
            except Exception as e:
                print(f"Personal message error for {client_id}: {e}")

    async def broadcast_photoshop_round_trip(self, binding: Dict[str, Any]):
        """Deliver a canvas-created binding only to connected PS connectors."""
        data = json.dumps({"type": "photoshop_round_trip_created", "data": binding})
        for connection in self.active_connections[:]:
            client_id = str(self.connection_clients.get(connection) or "")
            if not client_id.lower().startswith("ps_"):
                continue
            try:
                await connection.send_text(data)
            except Exception as exc:
                print(f"Broadcast Photoshop round-trip error: {exc}")
                if connection in self.active_connections:
                    self.active_connections.remove(connection)

manager = ConnectionManager()
# Photoshop round-trip bindings are deliberately process-local protocol state:
# each request is one-time and binds project/source/request before any media
# bytes are exposed.  Project media and canvas mutations remain project-owned
# and durable in ``ProjectStore``.
PHOTOSHOP_ROUND_TRIP_REQUESTS: Dict[str, Dict[str, Any]] = {}
PHOTOSHOP_ROUND_TRIP_LOCK = RLock()
PHOTOSHOP_ROUND_TRIP_MAX_MEDIA_BYTES = 128 * 1024 * 1024
PHOTOSHOP_ROUND_TRIP_RETENTION_MS = 24 * 60 * 60 * 1000
PHOTOSHOP_ROUND_TRIP_MAX_REQUESTS = 4096
GLOBAL_LOOP = None
APP_VERSION = "2026.06.03"
FIREFLY_REPO_URL = "https://github.com/miaomiaozii/Firefly-Canvas"

@app.on_event("startup")
async def startup_event():
    global GLOBAL_LOOP, PROJECT_MEDIA_IMPORT_ACCEPTING, PROJECT_MEDIA_IMPORT_SHUTTING_DOWN, PROJECT_CONVERSATION_RUNNER
    GLOBAL_LOOP = asyncio.get_running_loop()
    if PROJECT_CONVERSATION_RUNNER is not None:
        await PROJECT_CONVERSATION_RUNNER.stop()
    PROJECT_CONVERSATION_RUNNER = ProjectConversationTaskRunner()
    with PROJECT_MEDIA_IMPORT_TASKS_LOCK:
        PROJECT_MEDIA_IMPORT_SHUTTING_DOWN = False
        PROJECT_MEDIA_IMPORT_ACCEPTING = True
    ensure_project_media_import_executor()
    # 启动时整理资产库：给所有图片分组（含默认角色/场景）建好文件夹，并把根目录里的旧素材归整进去。
    try:
        await asyncio.to_thread(migrate_asset_library_into_dirs)
    except Exception as exc:
        print(f"资产库分组整理失败: {exc}")
    # 修复历史遗留的双重扩展名素材（foo.png.png → foo.png），否则这些卡片无法显示
    try:
        await asyncio.to_thread(migrate_double_extension_uploads)
    except Exception as exc:
        print(f"修复双重扩展名素材失败: {exc}")
    # 纠正内容与扩展名不符的图片（如 WebP 内容却叫 .png），否则严格客户端解不出来
    try:
        await asyncio.to_thread(migrate_mislabeled_image_extensions)
    except Exception as exc:
        print(f"纠正图片扩展名失败: {exc}")
    await PROJECT_CONVERSATION_RUNNER.start()
    try:
        recovered = await asyncio.to_thread(recover_project_agent_tasks)
        if recovered:
            print(f"Agent 任务恢复提交: {recovered}")
    except Exception as exc:
        print(f"Agent 任务恢复失败: {exc}")

@app.on_event("shutdown")
async def shutdown_event():
    global PROJECT_MEDIA_IMPORT_EXECUTOR, PROJECT_MEDIA_IMPORT_ACCEPTING, PROJECT_MEDIA_IMPORT_SHUTTING_DOWN, PROJECT_IMAGE_GENERATION_EXECUTOR, PROJECT_CONVERSATION_RUNNER
    conversation_runner = PROJECT_CONVERSATION_RUNNER
    if conversation_runner is not None:
        await conversation_runner.stop()
        if PROJECT_CONVERSATION_RUNNER is conversation_runner:
            PROJECT_CONVERSATION_RUNNER = None
    with PROJECT_MEDIA_IMPORT_TASKS_LOCK:
        PROJECT_MEDIA_IMPORT_SHUTTING_DOWN = True
        PROJECT_MEDIA_IMPORT_ACCEPTING = False
        executor = PROJECT_MEDIA_IMPORT_EXECUTOR
        PROJECT_MEDIA_IMPORT_EXECUTOR = None
        for task_id, task in PROJECT_MEDIA_IMPORT_TASKS.items():
            future = PROJECT_MEDIA_IMPORT_FUTURES.get(task_id)
            if task.get("status") == "pending" and future is not None and future.cancel():
                task.update({"status": "failed", "error": "应用关闭，媒体导入已取消", "updated_at": now_ms()})
    if executor is not None:
        await asyncio.to_thread(executor.shutdown, wait=True, cancel_futures=True)
    with PROJECT_MEDIA_IMPORT_TASKS_LOCK:
        if PROJECT_MEDIA_IMPORT_EXECUTOR is None:
            PROJECT_MEDIA_IMPORT_SHUTTING_DOWN = False
            PROJECT_MEDIA_IMPORT_ACCEPTING = True
    with CANVAS_TASK_LOCK:
        generation_executor = PROJECT_IMAGE_GENERATION_EXECUTOR
        PROJECT_IMAGE_GENERATION_EXECUTOR = None
        generation_futures = [
            *PROJECT_IMAGE_GENERATION_FUTURES.values(),
            *PROJECT_VIDEO_GENERATION_FUTURES.values(),
        ]
        PROJECT_IMAGE_GENERATION_FUTURES.clear()
        PROJECT_VIDEO_GENERATION_FUTURES.clear()
    async_generation_tasks = [future for future in generation_futures if isinstance(future, asyncio.Task)]
    for future in generation_futures:
        if not future.done():
            future.cancel()
    if async_generation_tasks:
        await asyncio.gather(*async_generation_tasks, return_exceptions=True)
    if generation_executor is not None:
        await asyncio.to_thread(generation_executor.shutdown, wait=True, cancel_futures=True)
    await asyncio.to_thread(project_store_call, "close")

@app.websocket("/ws/stats")
async def websocket_endpoint(websocket: WebSocket, client_id: str = None):
    # Photoshop round-trip sockets are intentionally loopback-only. Keep the
    # legacy stats/canvas sockets available, but do not let a remote peer
    # subscribe to project-bound PS bindings.
    if str(client_id or "").lower().startswith("ps_"):
        try:
            _ensure_photoshop_loopback(websocket)
        except HTTPException as exc:
            await websocket.close(code=1008, reason=str(exc.detail))
            return
    await manager.connect(websocket, client_id)
    try:
        while True:
            data = await websocket.receive_text()
            if data == "ping":
                await websocket.send_text(json.dumps({"type": "pong"}))
    except WebSocketDisconnect:
        await manager.disconnect(websocket, client_id)
    except Exception as e:
        print(f"WS Error: {e}")
        await manager.disconnect(websocket, client_id)

# --- 配置区域 ---

CLIENT_ID = str(uuid.uuid4())
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
RUNTIME_ROOT = os.path.abspath(os.getenv("FIREFLY_RUNTIME_ROOT", BASE_DIR))
STATIC_DIR = os.path.join(BASE_DIR, "static")
STATIC_RUNNINGHUB_DIR = os.path.join(STATIC_DIR, "runninghub")
STATIC_RUNNINGHUB_THUMBNAIL_DIR = os.path.join(STATIC_RUNNINGHUB_DIR, "thumbnails")
STATIC_RUNNINGHUB_API_PROVIDERS_FILE = os.path.join(STATIC_RUNNINGHUB_DIR, "api_providers.json")
STATIC_RUNNINGHUB_MODEL_REGISTRY_FILE = os.path.join(STATIC_RUNNINGHUB_DIR, "models_registry.json")
OUTPUT_DIR = os.path.join(RUNTIME_ROOT, "output")
ASSETS_DIR = os.path.join(RUNTIME_ROOT, "assets")
OUTPUT_INPUT_DIR = os.path.join(ASSETS_DIR, "input")
OUTPUT_OUTPUT_DIR = os.path.join(ASSETS_DIR, "output")
ASSET_LIBRARY_DIR = os.path.join(ASSETS_DIR, "library")
LOCAL_UPLOAD_DIR = os.path.join(ASSETS_DIR, "uploads")
HISTORY_FILE = os.path.join(RUNTIME_ROOT, "history.json")
API_ENV_FILE = os.path.join(RUNTIME_ROOT, "API", ".env")
DATA_DIR = os.path.join(RUNTIME_ROOT, "data")
CONVERSATION_DIR = os.path.join(DATA_DIR, "conversations")
CANVAS_DIR = os.path.join(DATA_DIR, "canvases")
MEDIA_PREVIEW_DIR = os.path.join(DATA_DIR, "media_previews")
ASSET_LIBRARY_PATH = os.path.join(DATA_DIR, "asset_library.json")
PROMPT_LIBRARY_PATH = os.path.join(DATA_DIR, "prompt_libraries.json")
API_PROVIDERS_FILE = os.path.join(DATA_DIR, "api_providers.json")
GLOBAL_SETTINGS_FILE = os.path.join(DATA_DIR, "global_settings.json")
SHARED_FOLDERS_FILE = os.path.join(DATA_DIR, "shared_folders.json")
GLOBAL_CONFIG_FILE = os.path.join(RUNTIME_ROOT, "global_config.json")
CANVAS_TRASH_RETENTION_MS = 30 * 24 * 60 * 60 * 1000
LOCAL_IMAGE_IMPORT_MAX_BYTES = int(os.getenv("LOCAL_IMAGE_IMPORT_MAX_BYTES", str(50 * 1024 * 1024)))
LOCAL_IMAGE_IMPORT_EXTS = {".png", ".jpg", ".jpeg", ".webp", ".gif"}
RUNNINGHUB_THUMBNAIL_EXTS = (".jpg",)
STORAGE_SETTINGS_FILE = os.path.join(DATA_DIR, "storage_settings.json")
DEFAULT_STORAGE_DIRS = {
    "upload": OUTPUT_INPUT_DIR,
    "generated": OUTPUT_OUTPUT_DIR,
    "local": LOCAL_UPLOAD_DIR,
}

def _storage_abs_path(value, fallback):
    text = str(value or "").strip()
    if not text:
        return os.path.abspath(fallback)
    text = os.path.expanduser(os.path.expandvars(text))
    if not os.path.isabs(text):
        text = os.path.join(BASE_DIR, text)
    return os.path.abspath(text)

def load_storage_settings():
    raw = {}
    try:
        if os.path.exists(STORAGE_SETTINGS_FILE):
            with open(STORAGE_SETTINGS_FILE, "r", encoding="utf-8-sig") as f:
                raw = json.load(f) if f else {}
    except Exception as exc:
        print(f"加载存储目录设置失败: {exc}")
        raw = {}
    dirs = {}
    for key, fallback in DEFAULT_STORAGE_DIRS.items():
        dirs[key] = _storage_abs_path((raw or {}).get(key), fallback)
    return {"dirs": dirs}

def save_storage_settings(payload):
    dirs = {}
    for key, fallback in DEFAULT_STORAGE_DIRS.items():
        dirs[key] = _storage_abs_path((payload or {}).get(key), fallback)
    for path in dirs.values():
        os.makedirs(path, exist_ok=True)
    os.makedirs(DATA_DIR, exist_ok=True)
    with open(STORAGE_SETTINGS_FILE, "w", encoding="utf-8") as f:
        json.dump(dirs, f, ensure_ascii=False, indent=2)
    apply_storage_settings(dirs)
    return {"dirs": dirs}

def apply_storage_settings(dirs=None):
    global OUTPUT_INPUT_DIR, OUTPUT_OUTPUT_DIR, LOCAL_UPLOAD_DIR
    dirs = dirs or load_storage_settings().get("dirs") or {}
    OUTPUT_INPUT_DIR = dirs.get("upload") or OUTPUT_INPUT_DIR
    OUTPUT_OUTPUT_DIR = dirs.get("generated") or OUTPUT_OUTPUT_DIR
    LOCAL_UPLOAD_DIR = dirs.get("local") or LOCAL_UPLOAD_DIR

apply_storage_settings()

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
JIMENG_LOGIN_SESSION = {
    "proc": None,
    "stdout": "",
    "stderr": "",
    "started_at": 0.0,
}

PROVIDER_ID_RE = re.compile(r"^[a-zA-Z0-9_-]{2,40}$")
SUPPORTED_PROVIDER_PROTOCOLS = {"openai", "apimart", "gemini", "gemini-cli", "volcengine", "runninghub", "jimeng", "codex"}
# These adapters were part of the old canvas surface. Keep their API handlers
# for backward-compatible error responses, but never expose or re-inject them
# into the current global provider registry.
REMOVED_PROVIDER_IDS = {"modelscope", "runninghub"}
SUPPORTED_IMAGE_REQUEST_MODES = {"openai", "openai-json", "openai-video-proxy", "openai-responses"}
RUNNINGHUB_DEFAULT_BASE_URL = "https://www.runninghub.cn"
RUNNINGHUB_OPENAPI_BASE_URL = "https://www.runninghub.cn/openapi/v2"
RUNNINGHUB_LLM_BASE_URL = "https://llm.runninghub.cn/v1"
RUNNINGHUB_FILE_HOST_REWRITES = {
    "rh-images-1252422369.cos.ap-beijing.myqcloud.com": "rh-images.xiaoyaoyou.com",
}
LINGJING_DEFAULT_BASE_URL = "https://apistudio.vip"
RUNNINGHUB_LLM_MODELS_URLS = [
    "https://llm.runninghub.cn/v1/models",
    "https://llm.runninghub.ai/v1/models",
]
RUNNINGHUB_FALLBACK_CHAT_MODELS = [
    "google/gemini-3.1-flash-lite-preview",
    "qwen/qwen3-vl-235b-a22b-instruct",
    "qwen/qwen-plus",
    "openai/gpt-5.1",
]
JIMENG_DEFAULT_IMAGE_MODELS = [
    "5.0",
    "4.6",
    "4.5",
    "4.1",
    "4.0",
    "3.1",
    "3.0",
]
JIMENG_DEFAULT_VIDEO_MODELS = [
    "seedance2.0_vip",
    "seedance2.0fast_vip",
    "seedance2.0",
    "seedance2.0fast",
    "3.5pro",
    "3.0pro",
    "3.0",
    "3.0fast",
]
CODEX_DEFAULT_IMAGE_MODELS = ["gpt-image-2"]
CODEX_DEFAULT_CHAT_MODELS = ["gpt-5.4"]
GEMINI_CLI_DEFAULT_IMAGE_MODELS = ["auto"]
GEMINI_CLI_DEFAULT_CHAT_MODELS = ["auto"]
try:
    CODEX_DEFAULT_TIMEOUT = max(30, min(3600, int(os.getenv("CODEX_CLI_TIMEOUT", "900"))))
except Exception:
    CODEX_DEFAULT_TIMEOUT = 900
try:
    GEMINI_CLI_DEFAULT_TIMEOUT = max(30, min(3600, int(os.getenv("GEMINI_CLI_TIMEOUT", "900"))))
except Exception:
    GEMINI_CLI_DEFAULT_TIMEOUT = 900
AGNES_DEFAULT_VIDEO_MODELS = ["agnes-video-v2.0"]
JIMENG_LEGACY_IMAGE_MODELS = {
    "jimeng-image-2k",
    "jimeng-image-4k",
}
JIMENG_LEGACY_VIDEO_MODELS = {
    "jimeng-video-720p",
    "jimeng-video-1080p",
}
try:
    JIMENG_DEFAULT_POLL_SECONDS = max(1, min(3600, int(os.getenv("JIMENG_POLL_SECONDS", "900"))))
except Exception:
    JIMENG_DEFAULT_POLL_SECONDS = 900
VOLCENGINE_DEFAULT_BASE_URL = "https://ark.cn-beijing.volces.com/api/v3"
VOLCENGINE_DEFAULT_PROJECT_NAME = "default"
VOLCENGINE_DEFAULT_REGION = "cn-beijing"
RUNNINGHUB_DEFAULT_IMAGE_MODELS = [
    "gpt-image-2.0/text-to-image-channel-low-price",
    "gpt-image-2.0/edit-channel-low-price",
    "gpt-image-2/text-to-image-official-stable",
    "gpt-image-2/image-to-image-official-stable",
    "nano-banana/text-to-image-official-stable",
    "nano-banana/edit-official-stable",
]
RUNNINGHUB_DEFAULT_VIDEO_MODELS = [
    "google/veo3.1-fast/text-to-video-channel-low-price",
    "sora-2/text-to-video-official-stable",
    "seedance-2.0-global/text-to-video",
    "seedance-2.0-global/image-to-video",
]
RUNNINGHUB_MODEL_ENDPOINT_ALIASES = {
    "Seedance2.0 Image to Video": "bytedance/seedance-2.0-global/image-to-video",
    "Seedance2.0 Text to Video": "bytedance/seedance-2.0-global/text-to-video",
    "seedance2.0 image to video": "bytedance/seedance-2.0-global/image-to-video",
    "seedance2.0 text to video": "bytedance/seedance-2.0-global/text-to-video",
    "gpt-image-2.0/text-to-image-channel-low-price": "rhart-image-g-2/text-to-image",
    "gpt-image-2/text-to-image-channel-low-price": "rhart-image-g-2/text-to-image",
    "gpt-image-2.0/edit-channel-low-price": "rhart-image-g-2/image-to-image",
    "gpt-image-2/edit-channel-low-price": "rhart-image-g-2/image-to-image",
    "gpt-image-2.0/image-to-image-channel-low-price": "rhart-image-g-2/image-to-image",
    "gpt-image-2/image-to-image-channel-low-price": "rhart-image-g-2/image-to-image",
    "nano-banana/text-to-image-channel-low-price": "rhart-image-v1/text-to-image",
    "nano-banana/edit-channel-low-price": "rhart-image-v1/edit",
}
RUNNINGHUB_DEFAULT_APPS = [
    {
        "id": "2058517022748798977",
        "appId": "2058517022748798977",
        "title": "2511-风格迁移",
        "note": "",
        "thumbnail": "",
        "enabled": True,
        "fields": [
            {
                "id": "100::image",
                "nodeId": "100",
                "fieldName": "image",
                "fieldValue": "pasted/57ef7dc980b6446bca366caaf3f94eb12b22b23f78aa30e294b39cabd7d0187b.png",
                "fieldType": "IMAGE",
                "label": "image",
                "enabled": True,
                "sourceFromUpstream": True,
                "group": "AI 应用参数",
                "note": "image",
                "options": [],
                "random_enabled": False,
                "min": "",
                "max": "",
                "step": "",
                "imageOrder": 0,
                "required": False,
            },
            {
                "id": "112::image",
                "nodeId": "112",
                "fieldName": "image",
                "fieldValue": "8cff63ee4b3e0285ca85ab90a52e26746df84ed0dec0be9d76c679cbb62a247d.png",
                "fieldType": "IMAGE",
                "label": "image",
                "enabled": True,
                "sourceFromUpstream": True,
                "group": "AI 应用参数",
                "note": "image",
                "options": [],
                "random_enabled": False,
                "min": "",
                "max": "",
                "step": "",
                "imageOrder": 0,
                "required": False,
            },
            {
                "id": "14::seed",
                "nodeId": "14",
                "fieldName": "seed",
                "fieldValue": "3250470112",
                "fieldType": "INT",
                "label": "seed",
                "enabled": True,
                "sourceFromUpstream": True,
                "group": "AI 应用参数",
                "note": "seed",
                "options": [],
                "random_enabled": True,
                "min": "1",
                "max": "4294967295",
                "step": "1",
                "imageOrder": 0,
                "required": False,
            },
        ],
    },
    {
        "id": "1997622492837646338",
        "appId": "1997622492837646338",
        "title": "2511-光线迁移",
        "note": "",
        "thumbnail": "",
        "enabled": True,
    },
]
def ensure_runtime_config_files():
    """首次运行时提前创建配置目录，避免第一次保存 API Key 时才创建目录/文件。"""
    try:
        os.makedirs(os.path.dirname(API_ENV_FILE), exist_ok=True)
        os.makedirs(DATA_DIR, exist_ok=True)
        if not os.path.exists(API_ENV_FILE):
            with open(API_ENV_FILE, "a", encoding="utf-8"):
                pass
    except Exception as e:
        print(f"初始化 API 配置目录失败: {e}")

def load_env_file():
    if not os.path.exists(API_ENV_FILE):
        return
    try:
        with open(API_ENV_FILE, 'r', encoding='utf-8-sig') as f:
            for raw_line in f.read().splitlines():
                line = raw_line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, value = line.split("=", 1)
                key = key.strip()
                value = value.strip().strip('"').strip("'")
                os.environ.setdefault(key, value)
    except Exception as e:
        print(f"加载 API/.env 失败: {e}")
ensure_runtime_config_files()
load_env_file()
FIREFLY_PROJECT_STORE = ProjectStore()

AI_BASE_URL = os.getenv("COMFLY_BASE_URL", "https://ai.comfly.chat").rstrip("/")
AI_API_KEY = os.getenv("COMFLY_API_KEY", "")
PUBLIC_BASE_URL = os.getenv("PUBLIC_BASE_URL", "").strip().rstrip("/")
PUBLIC_MEDIA_BASE_URL = os.getenv("PUBLIC_MEDIA_BASE_URL", "").strip().rstrip("/")
MODELSCOPE_API_KEY = os.getenv("MODELSCOPE_API_KEY", "")
MODELSCOPE_CHAT_BASE_URL = "https://api-inference.modelscope.cn/v1"
MODELSCOPE_DEFAULT_IMAGE_MODELS = [
    "Qwen/Qwen-Image-2512",
    "Qwen/Qwen-Image-Edit-2511",
]
MODELSCOPE_DEFAULT_CHAT_MODELS = [
    "Qwen/Qwen3-235B-A22B",
    "Qwen/Qwen3-VL-235B-A22B-Instruct",
    "MiniMax/MiniMax-M2.7:MiniMax",
]
_MODELSCOPE_CONFIGURED_CHAT_MODELS = [m.strip() for m in os.getenv("MODELSCOPE_CHAT_MODELS", "").split(",") if m.strip()]
MODELSCOPE_CHAT_MODELS = list(dict.fromkeys([m for m in [*MODELSCOPE_DEFAULT_CHAT_MODELS, *_MODELSCOPE_CONFIGURED_CHAT_MODELS] if m]))
MODELSCOPE_DEFAULT_IMAGE_MODEL = MODELSCOPE_DEFAULT_IMAGE_MODELS[0]
MODELSCOPE_DEFAULT_CHAT_MODEL = "Qwen/Qwen3-235B-A22B"
MODELSCOPE_DEFAULT_LORAS = [
    {
        "id": "Daniel8152/Qwen-Image-2512-Film",
        "name": "Qwen Image 2512 Film",
        "target_model": "Qwen/Qwen-Image-2512",
        "strength": 0.8,
        "enabled": True,
        "note": "",
    },
]
MODELSCOPE_DEFAULTS_VERSION = 3
CHAT_MODEL = os.getenv("CHAT_MODEL", "gpt-5.4")
IMAGE_MODEL = os.getenv("IMAGE_MODEL", "nano-banana-pro")
SYSTEM_PROMPT = os.getenv("SYSTEM_PROMPT", "You are a helpful assistant.")
MAX_HISTORY_MESSAGES = int(os.getenv("MAX_HISTORY_MESSAGES", "30"))
AI_REQUEST_TIMEOUT = float(os.getenv("REQUEST_TIMEOUT", "1800"))
IMAGE_POLL_INTERVAL = float(os.getenv("IMAGE_POLL_INTERVAL", "2"))
IMAGE_TASK_TIMEOUT = float(os.getenv("IMAGE_TASK_TIMEOUT", str(AI_REQUEST_TIMEOUT)))
APIMART_IMAGE_TASK_TIMEOUT = float(os.getenv("APIMART_IMAGE_TASK_TIMEOUT", "1800"))
APIMART_IMAGE_POLL_INTERVAL = float(os.getenv("APIMART_IMAGE_POLL_INTERVAL", "5"))
APIMART_IMAGE_INITIAL_POLL_DELAY = float(os.getenv("APIMART_IMAGE_INITIAL_POLL_DELAY", "10"))
VIDEO_POLL_TIMEOUT = float(os.getenv("VIDEO_POLL_TIMEOUT", "1800"))
ONLINE_IMAGE_PROMPT_MAX_LENGTH = int(os.getenv("ONLINE_IMAGE_PROMPT_MAX_LENGTH", "20000"))
VIDEO_PROMPT_MAX_LENGTH = int(os.getenv("VIDEO_PROMPT_MAX_LENGTH", "4000"))
LLM_MESSAGE_MAX_LENGTH = int(os.getenv("LLM_MESSAGE_MAX_LENGTH", "20000"))
CHAT_ATTACHMENT_MAX = int(os.getenv("CHAT_ATTACHMENT_MAX", "20"))
ONLINE_IMAGE_REFERENCE_MAX = int(os.getenv("ONLINE_IMAGE_REFERENCE_MAX", "20"))
ONLINE_VIDEO_REFERENCE_MAX = int(os.getenv("ONLINE_VIDEO_REFERENCE_MAX", "20"))
PROJECT_VIDEO_REFERENCE_INLINE_MAX_BYTES = int(os.getenv("PROJECT_VIDEO_REFERENCE_INLINE_MAX_BYTES", str(64 * 1024 * 1024)))

FIELD_LABELS = {
    "prompt": "提示词",
    "message": "文本",
    "system_prompt": "系统提示词",
}

def friendly_validation_error(errors):
    parts = []
    for err in errors or []:
        loc = [str(item) for item in err.get("loc", []) if item != "body"]
        field = loc[-1] if loc else ""
        label = FIELD_LABELS.get(field, field or "请求参数")
        ctx = err.get("ctx") or {}
        limit = ctx.get("limit_value") or ctx.get("max_length") or ctx.get("min_length")
        err_type = str(err.get("type") or "")
        msg = str(err.get("msg") or "")
        if "max_length" in err_type or "at most" in msg:
            parts.append(f"{label}过长：当前内容超过后端上限 {limit} 个字符。请拆分为多个提示词节点，或先用 LLM 节点压缩后再生成。")
        elif "min_length" in err_type:
            parts.append(f"{label}不能为空。")
        else:
            parts.append(f"{label}格式不正确：{msg}")
    return "\n".join(parts) or "请求参数不正确。"

@app.exception_handler(RequestValidationError)
async def request_validation_exception_handler(request: Request, exc: RequestValidationError):
    return JSONResponse(
        status_code=422,
        content={"detail": friendly_validation_error(exc.errors()), "errors": exc.errors()},
    )

def model_list(env_name, primary, defaults):
    configured = os.getenv(env_name, "")
    configured_values = [item.strip() for item in configured.split(",") if item.strip()]
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
    MODELSCOPE_API_KEY = os.getenv("MODELSCOPE_API_KEY", "")
    AI_API_KEY = os.getenv("COMFLY_API_KEY", "")
    AI_BASE_URL = os.getenv("COMFLY_BASE_URL", "https://ai.comfly.chat").rstrip("/")
    IMAGE_MODELS = model_list("IMAGE_MODELS", os.getenv("IMAGE_MODEL", IMAGE_MODEL), ["nano-banana-pro"])
    CHAT_MODELS = model_list("CHAT_MODELS", os.getenv("CHAT_MODEL", CHAT_MODEL), ["gpt-4o-mini", "gemini-3.1-flash-image-preview-2k"])
    VIDEO_MODELS = model_list("VIDEO_MODELS", "veo3-fast", [
        "veo2", "veo2-fast", "veo2-pro",
        "veo3", "veo3-fast", "veo3-pro",
        "veo3.1", "veo3.1-fast", "veo3.1-quality", "veo3.1-lite",
        "sora-2", "sora-2-pro",
        "wan2.6-t2v", "wan2.6-i2v",
        "wan2.5-t2v-preview", "wan2.5-i2v-preview",
        "wan2.2-t2v-plus", "wan2.2-i2v-plus", "wan2.2-i2v-flash",
        "doubao-seedance-2-0-260128",
        "doubao-seedance-2-0-fast-260128",
        "doubao-seedance-1-5-pro-251215",
        "doubao-seedance-1-0-pro-250528",
        "doubao-seedance-1-0-lite-t2v-250428",
        "doubao-seedance-1-0-lite-i2v-250428",
    ])
    _configured = [m.strip() for m in os.getenv("MODELSCOPE_CHAT_MODELS", "").split(",") if m.strip()]
    MODELSCOPE_CHAT_MODELS = list(dict.fromkeys([m for m in [*MODELSCOPE_DEFAULT_CHAT_MODELS, *_configured] if m]))

CHAT_MODELS = model_list("CHAT_MODELS", CHAT_MODEL, ["gpt-4o-mini", "gemini-3.1-flash-image-preview-2k"])
IMAGE_MODELS = model_list("IMAGE_MODELS", IMAGE_MODEL, ["nano-banana-pro"])
VIDEO_MODELS = model_list("VIDEO_MODELS", "veo3-fast", [
    # —— Veo 系列 ——
    "veo2", "veo2-fast", "veo2-pro",
    "veo3", "veo3-fast", "veo3-pro",
    "veo3.1", "veo3.1-fast", "veo3.1-quality", "veo3.1-lite",
    # —— Sora ——
    "sora-2", "sora-2-pro",
    # —— 阿里 通义万相 ——
    "wan2.6-t2v", "wan2.6-i2v",
    "wan2.5-t2v-preview", "wan2.5-i2v-preview",
    "wan2.2-t2v-plus", "wan2.2-i2v-plus", "wan2.2-i2v-flash",
    # —— 火山 豆包 Seedance ——
    "doubao-seedance-2-0-260128",
    "doubao-seedance-2-0-fast-260128",
    "doubao-seedance-1-5-pro-251215",
    "doubao-seedance-1-0-pro-250528",
    "doubao-seedance-1-0-lite-t2v-250428",
    "doubao-seedance-1-0-lite-i2v-250428",
])

def provider_key_env(provider_id):
    if provider_id == "comfly":
        return "COMFLY_API_KEY"
    if provider_id == "modelscope":
        return "MODELSCOPE_API_KEY"
    if provider_id == "runninghub":
        return "RUNNINGHUB_API_KEY"
    if provider_id == "volcengine":
        return "ARK_API_KEY"
    return f"API_PROVIDER_{re.sub(r'[^A-Za-z0-9]', '_', provider_id).upper()}_KEY"

def runninghub_wallet_key_env():
    return "RUNNINGHUB_WALLET_API_KEY"

def volcengine_access_key_env():
    return "VOLCENGINE_ACCESS_KEY_ID"

def volcengine_secret_key_env():
    return "VOLCENGINE_SECRET_ACCESS_KEY"

def read_api_env_value(key: str) -> str:
    key = str(key or "").strip()
    if not key or not os.path.exists(API_ENV_FILE):
        return ""
    try:
        with open(API_ENV_FILE, "r", encoding="utf-8-sig") as f:
            for raw_line in f.read().splitlines():
                line = raw_line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                env_key, value = line.split("=", 1)
                if env_key.strip() == key:
                    return value.strip().strip('"').strip("'")
    except Exception:
        return ""
    return ""

def provider_env_key_value(provider_id: str) -> str:
    provider_id = str(provider_id or "").strip().lower()
    env_key = provider_key_env(provider_id)
    key = os.getenv(env_key, "") or read_api_env_value(env_key)
    if key:
        return key
    if provider_id == "modelscope":
        return MODELSCOPE_API_KEY or ""
    return ""

def runninghub_wallet_key_value() -> str:
    env_key = runninghub_wallet_key_env()
    return os.getenv(env_key, "") or read_api_env_value(env_key)

def volcengine_access_key_value() -> str:
    env_key = volcengine_access_key_env()
    return os.getenv(env_key, "") or read_api_env_value(env_key)

def volcengine_secret_key_value() -> str:
    env_key = volcengine_secret_key_env()
    return os.getenv(env_key, "") or read_api_env_value(env_key)

def volcengine_provider_api_key(explicit_key: str = "") -> str:
    explicit_key = str(explicit_key or "").strip()
    if explicit_key:
        return explicit_key
    return provider_env_key_value("volcengine")

def mask_secret(value):
    if not value:
        return ""
    if len(value) <= 4:
        return "••••••••"
    tail = value[-4:]
    return f"••••••••{tail}"

def strip_auth_scheme(value, scheme="Bearer"):
    text = str(value or "").strip()
    if not text:
        return ""
    pattern = rf"^{re.escape(scheme)}\s+"
    return re.sub(pattern, "", text, flags=re.I).strip()

def bearer_auth_value(value):
    token = strip_auth_scheme(value, "Bearer")
    return f"Bearer {token}" if token else ""

def default_api_providers():
    # The current product has one editable online provider surface. Legacy
    # ModelScope/RunningHub adapters remain only as compatibility endpoints.
    return [
        {
            "id": "volcengine",
            "name": "火山引擎",
            "base_url": VOLCENGINE_DEFAULT_BASE_URL,
            "protocol": "volcengine",
            "image_request_mode": "openai",
            "image_generation_endpoint": "",
            "image_edit_endpoint": "",
            "enabled": True,
            "primary": False,
            "image_models": [],
            "chat_models": [],
            "video_models": [],
            "ms_loras": [],
            "ms_defaults_version": 0,
            "volcengine_project_name": VOLCENGINE_DEFAULT_PROJECT_NAME,
            "volcengine_region": VOLCENGINE_DEFAULT_REGION,
        },
    ]

def merge_default_api_providers(providers, inject_missing=True):
    merged = [dict(item) for item in providers if str(item.get("id") or "").strip().lower() not in REMOVED_PROVIDER_IDS]
    volc_default = next((d for d in default_api_providers() if d["id"] == "volcengine"), None)
    if volc_default:
        current = next((item for item in merged if item.get("id") == "volcengine"), None)
        legacy = next((item for item in merged if item.get("id") != "volcengine" and str(item.get("protocol") or "").lower() == "volcengine"), None)
        if not current:
            if legacy:
                legacy_image_models = model_list_from_values(legacy.get("image_models") or [])
                legacy_video_models = model_list_from_values(legacy.get("video_models") or [])
                current = {
                    **volc_default,
                    "base_url": legacy.get("base_url") or volc_default["base_url"],
                    "image_models": legacy_image_models or model_list_from_values(volc_default.get("image_models") or []),
                    "chat_models": model_list_from_values(legacy.get("chat_models") or []),
                    "video_models": legacy_video_models,
                }
                merged.append(current)
            elif inject_missing:
                merged.append(volc_default)
        else:
            if not current.get("base_url"):
                current["base_url"] = volc_default["base_url"]
            current["protocol"] = "volcengine"
            current["volcengine_project_name"] = str(current.get("volcengine_project_name") or VOLCENGINE_DEFAULT_PROJECT_NAME).strip() or VOLCENGINE_DEFAULT_PROJECT_NAME
            current["volcengine_region"] = str(current.get("volcengine_region") or VOLCENGINE_DEFAULT_REGION).strip() or VOLCENGINE_DEFAULT_REGION
    # 即梦 CLI 不再是强制保留的默认平台：仅在用户已添加了即梦协议的平台时，规范化其默认模型/地址。
    for current in merged:
        if not is_jimeng_provider(current):
            continue
        current["protocol"] = "jimeng"
        current["base_url"] = ""
        current["image_models"] = model_list_from_values([
            *[item for item in (current.get("image_models") or []) if str(item or "").strip() not in JIMENG_LEGACY_IMAGE_MODELS],
            *JIMENG_DEFAULT_IMAGE_MODELS,
        ])
        current["video_models"] = model_list_from_values([
            *[item for item in (current.get("video_models") or []) if str(item or "").strip() not in JIMENG_LEGACY_VIDEO_MODELS],
            *JIMENG_DEFAULT_VIDEO_MODELS,
        ])
    # OpenAI/Antigravity CLI 和即梦一样作为协议使用：用户选中 CLI 协议时再规范化模型与地址，不强制额外注入平台。
    for current in merged:
        current_protocol = str((current or {}).get("protocol") or "").strip().lower()
        if current_protocol not in {"codex", "gemini-cli"}:
            continue
        current["protocol"] = current_protocol
        current["base_url"] = ""
        default_image_models = CODEX_DEFAULT_IMAGE_MODELS if current_protocol == "codex" else GEMINI_CLI_DEFAULT_IMAGE_MODELS
        default_chat_models = CODEX_DEFAULT_CHAT_MODELS if current_protocol == "codex" else GEMINI_CLI_DEFAULT_CHAT_MODELS
        image_models = current.get("image_models") or []
        if current_protocol == "codex":
            image_models = [item for item in image_models if str(item or "").strip().lower() != "$imagegen"]
        current["image_models"] = model_list_from_values([*image_models, *default_image_models])
        current["chat_models"] = model_list_from_values([*(current.get("chat_models") or []), *default_chat_models])
        current["video_models"] = []
    return merged

def normalize_model_list(values):
    return model_list_from_values(values)

def model_list_from_values(values):
    deduped = []
    for value in values or []:
        item = str(value or "").strip()
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
        lora_id = str(raw.get("id") or "").strip()
        if not lora_id:
            continue
        target_model = str(raw.get("target_model") or raw.get("model") or "").strip()
        if not target_model:
            continue
        key = (target_model, lora_id)
        if key in seen:
            continue
        seen.add(key)
        try:
            strength = float(raw.get("strength", raw.get("default_strength", 0.8)))
        except Exception:
            strength = 0.8
        strength = max(0.0, min(2.0, strength))
        name = re.sub(r"\s+", " ", str(raw.get("name") or "").strip())[:80]
        normalized.append({
            "id": lora_id[:180],
            "name": name or lora_id,
            "target_model": target_model[:180],
            "strength": strength,
            "enabled": bool(raw.get("enabled", True)),
            "note": str(raw.get("note") or "").strip()[:300],
        })
    return normalized

def normalize_runninghub_entry(raw, kind):
    if kind != "app" or not isinstance(raw, dict):
        return None
    entry_id = str(raw.get("appId") or raw.get("id") or "").strip()
    match = re.search(r"/run/ai-app/([0-9A-Za-z_-]+)", entry_id)
    if match:
        entry_id = match.group(1)
    if not entry_id:
        return None
    title = re.sub(r"\s+", " ", str(raw.get("title") or raw.get("name") or "").strip())[:80]
    entry = {
        "id": entry_id[:80],
        "title": title or f"AI 应用 {entry_id[-6:]}",
        "note": str(raw.get("note") or raw.get("description") or "").strip()[:500],
        "thumbnail": str(raw.get("thumbnail") or "").strip()[:1500000],
        "enabled": bool(raw.get("enabled", True)),
        "appId": entry_id[:80],
    }
    if raw.get("hidden") is True:
        entry["hidden"] = True
    fields = raw.get("fields")
    if isinstance(fields, list):
        entry["fields"] = [dict(field) for field in fields if isinstance(field, dict)]
    raw_payload = raw.get("raw")
    if isinstance(raw_payload, dict):
        entry["raw"] = raw_payload
    try:
        updated_at = int(raw.get("updatedAt") or raw.get("updated_at") or 0)
        if updated_at > 0:
            entry["updatedAt"] = updated_at
    except Exception:
        pass
    return entry

def normalize_runninghub_entries(values, kind):
    if kind != "app":
        return []
    normalized = []
    seen = set()
    for raw in values or []:
        entry = normalize_runninghub_entry(raw, "app")
        if not entry or entry["id"] in seen:
            continue
        seen.add(entry["id"])
        normalized.append(entry)
    return normalized

def runninghub_entry_id(entry, kind):
    if kind != "app" or not isinstance(entry, dict):
        return ""
    return str(entry.get("appId") or entry.get("id") or "").strip()

def static_runninghub_thumbnail_url(entry_id, kind):
    if kind != "app":
        return ""
    entry_id = re.sub(r"[^0-9A-Za-z_-]", "", str(entry_id or "").strip())
    if not entry_id:
        return ""
    for name in (f"app-{entry_id}", entry_id):
        for ext in RUNNINGHUB_THUMBNAIL_EXTS:
            for root in (STATIC_RUNNINGHUB_THUMBNAIL_DIR, STATIC_RUNNINGHUB_DIR):
                path = os.path.abspath(os.path.join(root, f"{name}{ext}"))
                if not path.startswith(os.path.abspath(STATIC_RUNNINGHUB_DIR) + os.sep):
                    continue
                if os.path.isfile(path):
                    rel = os.path.relpath(path, STATIC_DIR).replace(os.sep, "/")
                    return f"/static/{urllib.parse.quote(rel, safe='/._-')}?v={int(os.path.getmtime(path))}"
    return ""

def apply_runninghub_system_thumbnails(entries, kind):
    result = []
    for entry in normalize_runninghub_entries(entries or [], kind):
        if not entry.get("thumbnail"):
            thumb = static_runninghub_thumbnail_url(runninghub_entry_id(entry, kind), kind)
            if thumb:
                entry["thumbnail"] = thumb
        result.append(entry)
    return result

def merge_runninghub_entry_overlay(system_entry, user_entry):
    if not isinstance(system_entry, dict):
        return user_entry
    if not isinstance(user_entry, dict):
        return system_entry
    merged = {**system_entry, **user_entry}
    if not merged.get("thumbnail") and system_entry.get("thumbnail"):
        merged["thumbnail"] = system_entry.get("thumbnail")
    return merged

def merge_runninghub_system_entries(system_entries, user_entries, kind):
    if kind != "app":
        return []
    merged = []
    index = {}
    for entry in apply_runninghub_system_thumbnails(system_entries or [], "app"):
        entry_id = runninghub_entry_id(entry, "app")
        if entry_id:
            index[entry_id] = len(merged)
            merged.append(entry)
    for entry in apply_runninghub_system_thumbnails(user_entries or [], "app"):
        entry_id = runninghub_entry_id(entry, "app")
        if not entry_id:
            continue
        if entry.get("hidden") is True:
            if entry_id in index:
                merged.pop(index[entry_id])
                index = {runninghub_entry_id(item, "app"): idx for idx, item in enumerate(merged)}
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
        with open(STATIC_RUNNINGHUB_API_PROVIDERS_FILE, "r", encoding="utf-8") as f:
            raw = json.load(f)
        candidates = raw if isinstance(raw, list) else raw.get("providers") if isinstance(raw, dict) else []
        if isinstance(raw, dict) and raw.get("id") == "runninghub":
            candidates = [raw]
        for item in candidates or []:
            if isinstance(item, dict) and str(item.get("id") or "").strip().lower() == "runninghub":
                provider = normalize_provider(item)
                provider["rh_apps"] = apply_runninghub_system_thumbnails(provider.get("rh_apps") or [], "app")
                return provider
    except Exception as e:
        print(f"加载 static RunningHub 配置失败: {e}")
    return None

def merge_runninghub_provider_with_static(provider):
    static_provider = load_static_runninghub_provider()
    if not static_provider:
        return provider
    if not isinstance(provider, dict):
        return static_provider
    merged = {**static_provider, **provider}
    merged["protocol"] = "runninghub"
    merged["image_models"] = model_list_from_values(provider.get("image_models") or [])
    merged["chat_models"] = model_list_from_values(provider.get("chat_models") or [])
    merged["video_models"] = model_list_from_values(provider.get("video_models") or [])
    merged["rh_apps"] = merge_runninghub_system_entries(static_provider.get("rh_apps") or [], provider.get("rh_apps") or [], "app")
    return normalize_provider(merged)

def preserve_runninghub_hidden_overrides(provider):
    if not isinstance(provider, dict) or provider.get("id") != "runninghub":
        return provider
    static_provider = load_static_runninghub_provider()
    if not static_provider:
        return provider
    provider = dict(provider)
    current = normalize_runninghub_entries(provider.get("rh_apps") or [], "app")
    current_ids = {runninghub_entry_id(item, "app") for item in current}
    for static_entry in static_provider.get("rh_apps") or []:
        entry_id = runninghub_entry_id(static_entry, "app")
        if entry_id and entry_id not in current_ids:
            current.append(normalize_runninghub_entry({**static_entry, "enabled": False, "hidden": True}, "app"))
    provider["rh_apps"] = [item for item in current if item]
    return provider


def normalize_endpoint_override(value, label):
    endpoint = str(value or "").strip()
    if not endpoint:
        return ""
    if len(endpoint) > 300 or re.search(r"\s", endpoint):
        raise HTTPException(status_code=400, detail=f"{label} 不合法，请填写类似 /v1/images/edits 的路径")
    if re.match(r"^https?://", endpoint, re.I):
        return endpoint.rstrip("/")
    if not endpoint.startswith("/"):
        raise HTTPException(status_code=400, detail=f"{label} 需要以 /v1/... 开头，或填写完整 http(s) 地址")
    return endpoint

def normalize_image_request_mode(value):
    mode = str(value or "").strip().lower()
    return mode if mode in SUPPORTED_IMAGE_REQUEST_MODES else "openai"

LOCKED_RECOMMENDED_PROVIDER_RULES = {
    "exellome": {
        "names": {"exellome"},
        "base_urls": {"https://new.exellome.online"},
        "protocol": "apimart",
        "image_request_mode": "openai-video-proxy",
        "video_models": [],
    },
    "fhl": {
        "names": {"fhl"},
        "base_urls": {"https://www.fhl.mom"},
        "protocol": "openai",
        "image_request_mode": "openai-responses",
        "video_models": [],
    },
}

def locked_recommended_provider_rule(provider_id="", name="", base_url=""):
    pid = str(provider_id or "").strip().lower()
    pname = str(name or "").strip().lower()
    pbase = str(base_url or "").strip().rstrip("/").lower()
    try:
        phost = urllib.parse.urlsplit(pbase).netloc.lower()
    except Exception:
        phost = ""
    for key, rule in LOCKED_RECOMMENDED_PROVIDER_RULES.items():
        hosts = {urllib.parse.urlsplit(url).netloc.lower() for url in rule["base_urls"]}
        if pid == key or pname in rule["names"] or pbase in rule["base_urls"] or (phost and phost in hosts):
            return rule
    return None

def apply_locked_recommended_model_rules(base_url="", grouped=None):
    rule = locked_recommended_provider_rule("", "", base_url)
    if not rule or "video_models" not in rule:
        return grouped
    grouped = {key: list(value or []) for key, value in (grouped or {}).items()}
    grouped.setdefault("image", [])
    grouped.setdefault("chat", [])
    grouped["video"] = list(rule.get("video_models") or [])
    return grouped

def provider_endpoint_url(provider, key, default_path):
    base_url = str((provider or {}).get("base_url") or AI_BASE_URL).strip().rstrip("/")
    override = str((provider or {}).get(key) or "").strip()
    if override:
        if re.match(r"^https?://", override, re.I):
            return override.rstrip("/")
        parsed = urllib.parse.urlsplit(base_url)
        if parsed.scheme and parsed.netloc:
            return f"{parsed.scheme}://{parsed.netloc}{override}"
        return override
    for prefix in ("/api/v3", "/v1beta", "/v1", "/v2"):
        if base_url.endswith(prefix) and default_path.startswith(f"{prefix}/"):
            return f"{base_url}{default_path[len(prefix):]}"
    return f"{base_url}{default_path}"

def runninghub_endpoint_url(provider, path):
    base_url = str((provider or {}).get("base_url") or RUNNINGHUB_DEFAULT_BASE_URL).strip().rstrip("/")
    return f"{base_url}{path}"

def runninghub_openapi_base_url(provider=None):
    base_url = str((provider or {}).get("base_url") or RUNNINGHUB_DEFAULT_BASE_URL).strip().rstrip("/")
    if base_url.endswith("/openapi/v2"):
        return base_url
    return f"{base_url}/openapi/v2"

def runninghub_openapi_url(provider, path=""):
    path = str(path or "").strip()
    if path.startswith("http://") or path.startswith("https://"):
        return path
    path = path.lstrip("/")
    base = runninghub_openapi_base_url(provider)
    return f"{base}/{path}" if path else base

def normalize_recommended_models(value):
    """Keep only the model kinds understood by the automatic router."""
    if not isinstance(value, dict):
        return {}
    normalized = {}
    for kind in ("image", "chat", "video"):
        model = str(value.get(kind) or "").strip()
        if model:
            normalized[kind] = model[:200]
    return normalized

def normalize_available_models(value):
    """Store fetched-but-unselected models separately from active model lists."""
    if not isinstance(value, dict):
        return {}
    normalized = {}
    for field in ("image_models", "chat_models", "video_models"):
        raw = value.get(field)
        if isinstance(raw, list):
            normalized[field] = model_list_from_values(raw)
    return normalized

def normalize_provider(item):
    provider_id = str(item.get("id") or "").strip().lower()
    if not PROVIDER_ID_RE.fullmatch(provider_id):
        raise HTTPException(status_code=400, detail=f"API 平台 ID 不合法：{provider_id or '(empty)'}")
    name = re.sub(r"\s+", " ", str(item.get("name") or provider_id).strip())[:60] or provider_id
    base_url = str(item.get("base_url") or "").strip().rstrip("/")
    if base_url and not re.match(r"^https?://", base_url):
        raise HTTPException(status_code=400, detail=f"{name} 的 Base URL 需要以 http:// 或 https:// 开头")
    protocol = str(item.get("protocol") or "openai").strip().lower()
    if protocol not in SUPPORTED_PROVIDER_PROTOCOLS:
        protocol = "openai"
    image_request_mode = detect_image_request_mode(base_url, item.get("image_models") or []) or normalize_image_request_mode(item.get("image_request_mode"))
    image_generation_endpoint = normalize_endpoint_override(item.get("image_generation_endpoint"), "文生图端口")
    image_edit_endpoint = normalize_endpoint_override(item.get("image_edit_endpoint"), "图生图/编辑端口")
    video_generation_endpoint = normalize_endpoint_override(item.get("video_generation_endpoint"), "视频生成端口")
    volc_project = re.sub(r"\s+", " ", str(item.get("volcengine_project_name") or "").strip())[:80]
    volc_region = re.sub(r"\s+", " ", str(item.get("volcengine_region") or "").strip())[:40]
    if provider_id == "volcengine":
        protocol = "volcengine"
        base_url = base_url or VOLCENGINE_DEFAULT_BASE_URL
        volc_project = volc_project or VOLCENGINE_DEFAULT_PROJECT_NAME
        volc_region = volc_region or VOLCENGINE_DEFAULT_REGION
    if provider_id == "jimeng" or protocol == "jimeng":
        protocol = "jimeng"
        base_url = ""
    if protocol in {"codex", "gemini-cli"}:
        base_url = ""
    if provider_id == "runninghub":
        protocol = "runninghub"
        base_url = base_url or RUNNINGHUB_DEFAULT_BASE_URL
    locked_rule = locked_recommended_provider_rule(provider_id, name, base_url)
    if locked_rule:
        protocol = locked_rule["protocol"]
        image_request_mode = locked_rule["image_request_mode"]
    image_models = model_list_from_values(item.get("image_models") or [])
    video_models = model_list_from_values(item.get("video_models") or [])
    if locked_rule and "video_models" in locked_rule:
        video_models = model_list_from_values(locked_rule.get("video_models") or [])
    try:
        model_selection_version = max(0, int(item.get("model_selection_version") or 0))
    except (TypeError, ValueError):
        model_selection_version = 0
    return {
        "id": provider_id,
        "name": name,
        "base_url": base_url,
        "protocol": protocol,
        "image_request_mode": image_request_mode,
        "image_generation_endpoint": image_generation_endpoint,
        "image_edit_endpoint": image_edit_endpoint,
        "video_generation_endpoint": video_generation_endpoint,
        "enabled": bool(item.get("enabled", True)),
        "primary": bool(item.get("primary", False)),
        "image_models": image_models,
        "image_model_capabilities": normalize_image_model_capabilities(item.get("image_model_capabilities"), image_models),
        "chat_models": model_list_from_values(item.get("chat_models") or []),
        "chat_audio_input": bool(item.get("chat_audio_input", False)),
        "video_models": video_models,
        "video_model_capabilities": normalize_video_model_capabilities(item.get("video_model_capabilities"), video_models),
        "recommended_models": normalize_recommended_models(item.get("recommended_models")),
        "available_models": normalize_available_models(item.get("available_models")),
        "model_selection_version": model_selection_version,
        "model_names": normalize_model_name_map(item.get("model_names")),
        "model_protocols": normalize_model_protocols(item.get("model_protocols")),
        "ms_loras": normalize_ms_loras(item.get("ms_loras") or []),
        "ms_defaults_version": int(item.get("ms_defaults_version") or 0),
        "rh_apps": normalize_runninghub_entries(item.get("rh_apps") or [], "app"),
        "volcengine_project_name": volc_project,
        "volcengine_region": volc_region,
    }

def load_api_providers():
    defaults = default_api_providers()
    if not os.path.exists(API_PROVIDERS_FILE):
        return merge_default_api_providers(defaults)
    try:
        with open(API_PROVIDERS_FILE, "r", encoding="utf-8") as f:
            raw = json.load(f)
        providers = [normalize_provider(item) for item in raw if isinstance(item, dict)]
        providers = [item for item in providers if item.get("id") not in REMOVED_PROVIDER_IDS]
        return merge_default_api_providers(providers or defaults, inject_missing=not bool(providers))
    except Exception as e:
        print(f"加载 API 平台配置失败: {e}")
        return defaults

def save_api_providers(providers):
    os.makedirs(DATA_DIR, exist_ok=True)
    with GLOBAL_CONFIG_LOCK:
        with open(API_PROVIDERS_FILE, "w", encoding="utf-8") as f:
            json.dump(providers, f, ensure_ascii=False, indent=2)

def public_provider(provider):
    # RunningHub apps are online HTTP adapters; only their app configuration is public.
    provider = dict(provider or {})
    key = provider_env_key_value(provider["id"])
    item = {
        **provider,
        "has_key": bool(key),
        "key_preview": mask_secret(key),
        "key_env": provider_key_env(provider["id"]),
    }
    if provider.get("id") == "runninghub":
        wallet_key = runninghub_wallet_key_value()
        item.update({
            "has_wallet_key": bool(wallet_key),
            "wallet_key_preview": mask_secret(wallet_key),
            "wallet_key_env": runninghub_wallet_key_env(),
        })
    if provider.get("id") == "volcengine":
        ak = volcengine_access_key_value()
        sk = volcengine_secret_key_value()
        item.update({
            "has_volcengine_access_key": bool(ak),
            "volcengine_access_key_preview": mask_secret(ak),
            "volcengine_access_key_env": volcengine_access_key_env(),
            "has_volcengine_secret_key": bool(sk),
            "volcengine_secret_key_preview": mask_secret(sk),
            "volcengine_secret_key_env": volcengine_secret_key_env(),
            "volcengine_project_name": provider.get("volcengine_project_name") or VOLCENGINE_DEFAULT_PROJECT_NAME,
            "volcengine_region": provider.get("volcengine_region") or VOLCENGINE_DEFAULT_REGION,
        })
    return item

def public_api_providers():
    return [public_provider(p) for p in load_api_providers()]

def get_primary_provider_id(providers=None):
    """Return the preferred provider id, falling back to the first enabled one."""
    providers = providers if providers is not None else load_api_providers()
    primary = next((p for p in providers if p.get("primary") and p.get("enabled", True)), None)
    if primary:
        return primary["id"]
    enabled = next((p for p in providers if p.get("enabled", True)), None)
    if enabled:
        return enabled["id"]
    return providers[0]["id"] if providers else "volcengine"

def get_api_provider(provider_id="comfly"):
    providers = load_api_providers()
    target = (provider_id or "").strip().lower()
    # 兼容旧的 "comfly" 硬编码：若 comfly 不存在或未指定，回退到首选 provider
    if not target or not any(p["id"] == target for p in providers):
        target = get_primary_provider_id(providers)
    provider = next((p for p in providers if p["id"] == target), None)
    if not provider:
        raise HTTPException(status_code=400, detail=f"未找到 API 平台：{target}")
    if not provider.get("enabled", True):
        raise HTTPException(status_code=400, detail=f"API 平台已禁用：{provider.get('name') or target}")
    return provider

def get_api_provider_exact(provider_id: str):
    providers = load_api_providers()
    target = (provider_id or "").strip().lower()
    provider = next((p for p in providers if p["id"] == target), None)
    if not provider:
        raise HTTPException(status_code=400, detail=f"未找到 API 平台：{target or '(empty)'}。新增平台未保存时请使用当前表单拉取模型。")
    if not provider.get("enabled", True):
        raise HTTPException(status_code=400, detail=f"API 平台已禁用：{provider.get('name') or target}")
    return provider

def modelscope_provider_config():
    return get_api_provider_exact("modelscope")

def modelscope_api_key(explicit_key: str = ""):
    return (
        strip_auth_scheme(explicit_key, "Bearer")
        or strip_auth_scheme(provider_env_key_value("modelscope"), "Bearer")
        or strip_auth_scheme(MODELSCOPE_API_KEY, "Bearer")
    )

def modelscope_api_root(provider=None):
    provider = provider or modelscope_provider_config()
    base_root = str((provider or {}).get("base_url") or MODELSCOPE_CHAT_BASE_URL).strip().rstrip("/")
    if not base_root:
        base_root = MODELSCOPE_CHAT_BASE_URL
    return base_root if base_root.endswith("/v1") else f"{base_root}/v1"

def modelscope_image_api_root():
    return MODELSCOPE_CHAT_BASE_URL.rstrip("/")

def env_quote(value):
    text = str(value or "")
    if not text or re.search(r"\s|#|['\"]", text):
        return '"' + text.replace("\\", "\\\\").replace('"', '\\"') + '"'
    return text

def update_env_values(updates):
    with API_ENV_LOCK:
        return _update_env_values_unlocked(updates)


def _update_env_values_unlocked(updates):
    os.makedirs(os.path.dirname(API_ENV_FILE), exist_ok=True)
    lines = []
    if os.path.exists(API_ENV_FILE):
        with open(API_ENV_FILE, "r", encoding="utf-8-sig") as f:
            lines = f.read().splitlines()
    seen = set()
    next_lines = []
    for line in lines:
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in line:
            next_lines.append(line)
            continue
        key = line.split("=", 1)[0].strip()
        if key in updates:
            next_lines.append(f"{key}={env_quote(updates[key])}")
            seen.add(key)
        else:
            next_lines.append(line)
    for key, value in updates.items():
        if key not in seen:
            next_lines.append(f"{key}={env_quote(value)}")
    content = "\n".join(next_lines).rstrip() + "\n"
    temporary = f"{API_ENV_FILE}.{uuid.uuid4().hex}.tmp"
    try:
        with open(temporary, "w", encoding="utf-8", newline="\n") as f:
            f.write(content)
            f.flush()
            os.fsync(f.fileno())
        os.replace(temporary, API_ENV_FILE)
        for key, value in updates.items():
            os.environ[key] = str(value or "")
    finally:
        try:
            os.remove(temporary)
        except FileNotFoundError:
            pass


def load_global_preferences():
    defaults = {"theme": "dark", "language": "zh", "autosave_interval_minutes": 20}
    try:
        with open(GLOBAL_SETTINGS_FILE, "r", encoding="utf-8-sig") as handle:
            raw = json.load(handle)
    except (OSError, ValueError, json.JSONDecodeError):
        return defaults
    theme = raw.get("theme") if isinstance(raw, dict) else None
    language = raw.get("language") if isinstance(raw, dict) else None
    autosave_interval = raw.get("autosave_interval_minutes") if isinstance(raw, dict) else None
    try:
        autosave_interval = int(autosave_interval)
    except (TypeError, ValueError):
        autosave_interval = defaults["autosave_interval_minutes"]
    return {
        "theme": theme if theme in {"light", "dark"} else defaults["theme"],
        "language": language if language in {"zh", "en"} else defaults["language"],
        "autosave_interval_minutes": autosave_interval if 1 <= autosave_interval <= 240 else defaults["autosave_interval_minutes"],
    }


def save_global_preferences(theme: str, language: str, autosave_interval_minutes: Optional[int] = None):
    current = load_global_preferences()
    interval = current["autosave_interval_minutes"] if autosave_interval_minutes is None else int(autosave_interval_minutes)
    preferences = {
        "theme": theme if theme in {"light", "dark"} else "dark",
        "language": language if language in {"zh", "en"} else "zh",
        "autosave_interval_minutes": interval if 1 <= interval <= 240 else 20,
    }
    os.makedirs(os.path.dirname(GLOBAL_SETTINGS_FILE), exist_ok=True)
    temporary = f"{GLOBAL_SETTINGS_FILE}.{uuid.uuid4().hex}.tmp"
    try:
        with open(temporary, "w", encoding="utf-8", newline="\n") as handle:
            json.dump(preferences, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, GLOBAL_SETTINGS_FILE)
    finally:
        try:
            os.remove(temporary)
        except FileNotFoundError:
            pass
    return preferences

os.makedirs(OUTPUT_DIR, exist_ok=True)
os.makedirs(ASSETS_DIR, exist_ok=True)
os.makedirs(OUTPUT_INPUT_DIR, exist_ok=True)
os.makedirs(OUTPUT_OUTPUT_DIR, exist_ok=True)
os.makedirs(ASSET_LIBRARY_DIR, exist_ok=True)
os.makedirs(LOCAL_UPLOAD_DIR, exist_ok=True)
os.makedirs(STATIC_DIR, exist_ok=True)
os.makedirs(CONVERSATION_DIR, exist_ok=True)
os.makedirs(CANVAS_DIR, exist_ok=True)

app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
app.mount("/output", StaticFiles(directory=OUTPUT_DIR), name="output")
app.mount("/assets", StaticFiles(directory=ASSETS_DIR), name="assets")

# --- Pydantic 模型 ---

def current_app_version():
    version_file = os.path.join(BASE_DIR, "VERSION")
    try:
        if os.path.exists(version_file):
            with open(version_file, "r", encoding="utf-8") as f:
                version = (f.read().strip().splitlines() or [""])[0].strip()
                if version:
                    return version
    except Exception:
        pass
    try:
        return time.strftime("%Y.%m.%d", time.localtime())
    except Exception:
        return ""

# Kept as a test/lifespan seam for downstream callers; it intentionally never
# contacts a remote repository or rewrites local files.
def sync_static_html_versions():
    return None

def static_html_response(filename: str):
    path = os.path.join(STATIC_DIR, filename)
    with open(path, "r", encoding="utf-8") as f:
        html = f.read()
    return Response(html, media_type="text/html; charset=utf-8", headers={"Cache-Control": "no-cache"})

STATIC_PROMPT_TEMPLATE_MD = os.path.join(STATIC_DIR, "system-prompts", "firefly-canvas-prompt-templates.md")
PROMPT_TEMPLATE_PATHS = [STATIC_PROMPT_TEMPLATE_MD]
PROMPT_TEMPLATE_EN = {
    "多机位九宫格": {
        "name": "9-Angle Multi-Camera Grid",
        "scene": "Show the same subject or scene from 9 camera angles for character turnarounds, product views, or space scouting.",
    },
    "多机位九宫格4K": {
        "name": "9-Angle Multi-Camera Grid 4K",
        "scene": "A high-resolution 9-angle reference sheet for print-grade output, large displays, and fine material study.",
    },
    "剧情推演四宫格": {
        "name": "4-Panel Story Progression",
        "scene": "Preview four consecutive story beats or emotional stages for storyboard planning and narrative rhythm tests.",
    },
    "角色脸部三视图": {
        "name": "Character Face 3-View Sheet",
        "scene": "Front, side, and three-quarter face references for Actor ID locking and expression consistency.",
    },
    "产品三视图": {
        "name": "Product 3-View Sheet",
        "scene": "Front, side, and top product views for industrial design, ecommerce detail pages, and technical documents.",
    },
    "25宫格连贯分镜": {
        "name": "25-Panel Continuous Storyboard",
        "scene": "A full 5x5 storyboard for continuous scene or action flow, useful for film previews and motion continuity tests.",
    },
    "电影级光影校正": {
        "name": "Cinematic Lighting Comparison",
        "scene": "Compare the same subject or scene under different lighting conditions for mood, color, and lighting choices.",
    },
    "角色设定参考表（胸口特写+全身三视图）": {
        "name": "Character Reference Sheet: Portrait + Full-Body Views",
        "scene": "A consistency reference combining a face anchor and full-body front, side, and back views for Actor ID and costume lock.",
    },
    "6种基础表情胸像（2×3六宫格）": {
        "name": "6 Basic Expression Busts",
        "scene": "Six basic expressions of the same character for expression consistency, emotion baselines, and Seedance Talk-to-Edit reference.",
    },
    "360全景图": {
        "name": "360 Panorama VR Image",
        "scene": "Generate a seamless 360-degree VR panorama with continuous left and right edges and natural pole transitions.",
    },
}

def prompt_template_markdown_path() -> str:
    for path in PROMPT_TEMPLATE_PATHS:
        if os.path.exists(path):
            return path
    return ""

def prompt_template_category(name: str, scene: str) -> str:
    text = f"{name} {scene}"
    if any(k in text for k in ["光影", "灯光", "光效", "电影级"]):
        return "lighting"
    if any(k in text for k in ["视角", "全景", "VR", "镜头", "俯拍", "仰拍", "景别", "构图", "透视"]):
        return "view"
    if any(k in text for k in ["角色", "脸部", "表情", "Actor", "服装"]):
        return "character"
    if any(k in name for k in ["产品", "电商", "工业"]):
        return "product"
    return "storyboard"

def extract_prompt_template_section(block: str, title: str) -> str:
    pattern = rf"###\s*{re.escape(title)}\s*\n(?P<body>.*?)(?=\n###\s+|\Z)"
    match = re.search(pattern, block, re.S)
    if not match:
        return ""
    body = match.group("body").strip()
    fence = re.search(r"```(?:\w+)?\s*\n(?P<code>.*?)\n```", body, re.S)
    return (fence.group("code") if fence else body).strip()

def parse_prompt_template_markdown(text: str):
    templates = []
    matches = list(re.finditer(r"^##\s*预设\s*(\d+)\s*[：:]\s*(.+?)\s*$", text, re.M))
    for index, match in enumerate(matches):
        number = match.group(1).strip()
        name = match.group(2).strip()
        start = match.end()
        end = matches[index + 1].start() if index + 1 < len(matches) else len(text)
        block = text[start:end]
        scene = extract_prompt_template_section(block, "适用场景")
        positive = extract_prompt_template_section(block, "正向提示词")
        negative = extract_prompt_template_section(block, "负向提示词")
        params_raw = extract_prompt_template_section(block, "平台参数建议")
        params = {}
        for line in params_raw.splitlines():
            item = re.match(r"[-*]\s*\*\*(.+?)\*\*\s*[：:]\s*(.+)", line.strip())
            if item:
                params[item.group(1).strip()] = item.group(2).strip()
        if not positive:
            continue
        templates.append({
            "id": f"builtin_md_{number}",
            "number": number,
            "name": name,
            "name_en": PROMPT_TEMPLATE_EN.get(name, {}).get("name", name),
            "category": prompt_template_category(name, scene),
            "scene": scene,
            "scene_en": PROMPT_TEMPLATE_EN.get(name, {}).get("scene", scene),
            "positive": positive,
            "negative": negative,
            "params": params,
            "builtin": True,
        })
    return templates

@app.get("/api/app-info")
def app_info():
    version = current_app_version()
    return {
        "name": "萤火画布",
        "version": version,
        "repo_url": FIREFLY_REPO_URL,
        "repository": {"label": "萤火画布", "url": FIREFLY_REPO_URL},
    }

# Remote update, staging, rollback, and connectivity probes were removed.
# Version/repository identity is exposed by /api/app-info only.
class DeleteHistoryRequest(BaseModel):
    timestamp: float

class DeleteCanvasLogRequest(BaseModel):
    log_id: str
    delete_unreferenced_media: bool = False
    reset_referencing_nodes: bool = False
    base_updated_at: int = 0

class AIReference(BaseModel):
    url: str = ""
    name: str = ""
    role: str = ""
    kind: str = ""
    mime: str = ""

class OnlineImageRequest(BaseModel):
    prompt: str = Field(min_length=1, max_length=ONLINE_IMAGE_PROMPT_MAX_LENGTH)
    provider_id: str = "comfly"
    model: str = ""
    size: str = "1024x1024"
    quality: str = "auto"
    n: int = 1
    reference_images: List[AIReference] = []

class ProjectImageGenerationRequest(BaseModel):
    prompt: str = Field(min_length=1, max_length=ONLINE_IMAGE_PROMPT_MAX_LENGTH)
    routing_mode: str = "auto"
    provider_id: str = ""
    model: str = ""
    size: str = "1024x1024"
    width: Optional[int] = None
    height: Optional[int] = None
    aspect_ratio: str = "1:1"
    resolution: str = "1k"
    quality: str = "auto"
    n: int = 1
    reference_asset_ids: List[str] = Field(default_factory=list)
    media_type: str = "image"
    result_node_id: str = Field(min_length=1, max_length=200)
    angle_preset: str = "none"
    base_revision: int = Field(ge=0)

class ProjectVideoGenerationRequest(BaseModel):
    prompt: str = Field(min_length=1, max_length=VIDEO_PROMPT_MAX_LENGTH)
    routing_mode: str = "auto"
    provider_id: str = ""
    model: str = ""
    size: str = "1280x720"
    width: Optional[int] = None
    height: Optional[int] = None
    aspect_ratio: str = "16:9"
    resolution: str = "720p"
    duration: int = Field(ge=1, le=600)
    quality: str = "auto"
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
    media_type: str = "video"
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

    goal: str = Field(default="", max_length=VIDEO_PROMPT_MAX_LENGTH)
    prompt: str = Field(default="", max_length=VIDEO_PROMPT_MAX_LENGTH)
    tasks: List[Dict[str, Any]] = Field(default_factory=list)
    task_specs: List[Dict[str, Any]] = Field(default_factory=list)
    request_id: str = Field(default="", max_length=240)
    base_revision: int = Field(default=0, ge=0)


class ProjectAgentExecuteRequest(BaseModel):
    plan_id: str = Field(min_length=1, max_length=240)
    execution_id: str = Field(default="", max_length=240)
    # A confirmation may carry the exact plan hash/tasks displayed by the UI;
    # the server compares it to the durable plan and never executes a changed
    # or plan-external task.
    plan_hash: str = Field(default="", max_length=128)
    tasks: List[Dict[str, Any]] = Field(default_factory=list)

class ImageTaskQueryRequest(BaseModel):
    provider_id: str = "comfly"
    task_id: str = Field(min_length=1, max_length=240)

CANVAS_TASKS: Dict[str, Dict[str, Any]] = {}
CANVAS_TASK_LOCK = Lock()

class CanvasVideoRequest(BaseModel):
    prompt: str = Field(min_length=1, max_length=VIDEO_PROMPT_MAX_LENGTH)
    provider_id: str = "comfly"
    model: str = "veo3-fast"
    duration: int = 5
    width: Optional[int] = None
    height: Optional[int] = None
    aspect_ratio: str = "16:9"
    resolution: str = ""
    size: str = ""
    quality: str = "auto"
    media_type: str = "video"
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
    url: str = ""

class CloudVideoUploadRequest(BaseModel):
    url: str = ""
    service: str = "auto"

class RunningHubSubmitRequest(BaseModel):
    webappId: str = ""
    nodeInfoList: List[Dict[str, Any]] = []
    instanceType: str = ""
    useWallet: bool = False

class RunningHubUploadAssetRequest(BaseModel):
    url: str = ""
    useWallet: bool = False

class JimengHelpRequest(BaseModel):
    command: str = ""

class CodexHelpRequest(BaseModel):
    command: str = ""

class GeminiCliHelpRequest(BaseModel):
    command: str = ""

class JimengQueryMediaRequest(BaseModel):
    submit_id: str = ""
    kind: str = "image"

class ApiProviderPayload(BaseModel):
    id: str = ""
    name: str = ""
    base_url: str = ""
    protocol: str = "openai"
    image_request_mode: str = "openai"
    image_generation_endpoint: str = ""
    image_edit_endpoint: str = ""
    video_generation_endpoint: str = ""
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

class ChatRequest(BaseModel):
    conversation_id: str = ""
    message: str = Field(min_length=1, max_length=LLM_MESSAGE_MAX_LENGTH)
    system_prompt: str = ""
    model: str = ""
    image_model: str = ""
    image_provider: str = ""
    mode: str = "chat"
    size: str = "1024x1024"
    quality: str = "auto"
    reference_images: List[AIReference] = []
    provider: str = "comfly"
    ms_model: str = ""

def chat_system_prompt(payload):
    prompt = str(getattr(payload, "system_prompt", "") or "").strip()
    return prompt or SYSTEM_PROMPT

class CanvasLLMRequest(BaseModel):
    message: str = Field(min_length=1, max_length=LLM_MESSAGE_MAX_LENGTH)
    system_prompt: str = ""
    model: str = ""
    messages: List[Dict[str, Any]] = []
    provider: str = "comfly"
    ms_model: str = ""
    images: List[str] = []   # 可以是 /output/*.png、/assets/*.png 本地路径 或 http(s) URL 或 data URL
    videos: List[str] = []   # 可以是 /output/*.mp4、/assets/*.mp4 本地路径 或 http(s) URL 或 data URL
    audios: List[str] = []
    audio_references: List[Dict[str, Any]] = Field(default_factory=list)

class ConversationCreateRequest(BaseModel):
    title: str = "新对话"

class CanvasCreateRequest(BaseModel):
    title: str = "未命名画布"
    icon: str = "🧩"
    kind: str = "classic"
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
    name: str = "新项目"

class ProjectUpdateRequest(BaseModel):
    name: Optional[str] = None
    order: Optional[int] = None

class FireflyProjectCreateRequest(BaseModel):
    name: str = "新项目"

class GlobalPreferencesPayload(BaseModel):
    theme: str = "dark"
    language: str = "zh"
    autosave_interval_minutes: Optional[int] = Field(default=None, ge=1, le=240)

class ProjectLibraryChangeRequest(BaseModel):
    path: str = Field(min_length=1, max_length=1024)
    move_existing: bool = False

class ProjectMediaImportRequest(BaseModel):
    source_url: str
    source_locator: str = ""
    name: str = ""
    mime_type: str = ""
    expected_kind: str = ""


class PhotoshopRoundTripCreateRequest(BaseModel):
    source_node_id: str = Field(min_length=1, max_length=200)
    source_media_id: str = Field(min_length=1, max_length=240)
    ttl_seconds: int = Field(default=300, ge=1, le=3600)


class PhotoshopRoundTripReturnRequest(BaseModel):
    project_id: str = Field(min_length=1, max_length=200)
    source_node_id: str = Field(min_length=1, max_length=200)
    request_id: str = Field(min_length=1, max_length=200)
    name: str = Field(default="photoshop-edit.png", min_length=1, max_length=240)
    mime_type: str = Field(default="image/png", min_length=1, max_length=128)
    sha256: str = Field(min_length=64, max_length=64)
    bytes_base64: str = Field(default="", max_length=256 * 1024 * 1024)
    # ``data`` is accepted as a connector-friendly alias for the same base64
    # payload; the service never stores this encoded representation in canvas.
    data: str = Field(default="", max_length=256 * 1024 * 1024)

class CanvasMutationBatch(BaseModel):
    mutations: List[Dict[str, Any]] = Field(default_factory=list)
    client_id: str = ""
    base_revision: int = Field(ge=0)


class ProjectLocalEditRequest(BaseModel):
    """Explicit local-edit node inputs and source-pixel selection contract."""

    original_asset_id: str = Field(min_length=1, max_length=240)
    reference_asset_ids: List[str] = Field(default_factory=list, max_length=16)
    prompt: str = Field(min_length=1, max_length=ONLINE_IMAGE_PROMPT_MAX_LENGTH)
    mode: str = "modify"
    # Modify: [left, top, right, bottom] in original-image pixels.
    # Retain: {type: polygon|freehand|curve, points: [[x,y], ...]} or shapes list.
    selection: Any
    aspect_ratio: str = ""
    size: str = Field(default="1024x1024", min_length=3, max_length=32)
    resolution: str = Field(default="1k", min_length=1, max_length=16)
    feather_radius: float = Field(default=0, ge=0, le=256)
    result_node_id: str = Field(min_length=1, max_length=200)
    base_revision: int = Field(ge=0)


class ProjectConversationMessageRequest(BaseModel):
    text: str = Field(min_length=1, max_length=LLM_MESSAGE_MAX_LENGTH)
    request_id: str = Field(default="", max_length=200)
    provider: Optional[str] = Field(default=None, max_length=80)
    model: Optional[str] = Field(default=None, max_length=200)
    ms_model: Optional[str] = Field(default=None, max_length=200)
    system_prompt: Optional[str] = Field(default=None, max_length=12_000)


class ProjectHistoryDeleteRequest(BaseModel):
    log_id: str
    base_revision: int = Field(ge=0)

class ProjectOriginalDeleteRequest(BaseModel):
    asset_id: str
    base_revision: int = Field(ge=0)

class CanvasSaveRequest(BaseModel):
    title: str = "未命名画布"
    icon: str = "🧩"
    nodes: List[Dict[str, Any]] = []
    connections: List[Dict[str, Any]] = []
    viewport: Dict[str, Any] = {}
    logs: List[Dict[str, Any]] = []
    settings: Dict[str, Any] = {}
    client_id: str = ""
    base_updated_at: int = 0

class CanvasAssetCheckRequest(BaseModel):
    urls: List[str] = []

class CanvasAssetDownloadRequest(BaseModel):
    urls: List[str] = []
    items: List[Dict[str, Any]] = []
    filename: str = "canvas-output-images.zip"

class SmartCanvasGroupExportItem(BaseModel):
    kind: str = ""
    url: str = ""
    text: str = ""
    name: str = ""

class SmartCanvasGroupExportRequest(BaseModel):
    folder: str = ""
    group_name: str = "group"
    items: List[SmartCanvasGroupExportItem] = []

class LocalImageImportRequest(BaseModel):
    path: str = ""
    paths: List[str] = Field(default_factory=list)

class LocalAssetCaptionRequest(BaseModel):
    names: List[str] = []
    provider: str = "comfly"
    model: str = ""
    ms_model: str = ""
    prompt: str = "描述图片"

class LocalAssetCaptionSaveRequest(BaseModel):
    name: str = ""
    caption: str = ""

class LocalAssetClassifyRequest(BaseModel):
    names: List[str] = []
    provider: str = "comfly"
    model: str = ""
    ms_model: str = ""
    prompt: str = ""

class LocalAssetUrlImportItem(BaseModel):
    url: str = ""
    name: str = ""
    data: str = ""          # 可选：base64 / dataURL，由插件在网页上下文里读取（blob: 等无法服务端下载的素材）
    content_type: str = ""  # 配合 data 使用，用于推断扩展名

class LocalAssetUrlImportRequest(BaseModel):
    items: List[LocalAssetUrlImportItem] = []
    folder: str = ""
    classify: bool = False
    provider: str = "comfly"
    model: str = ""
    ms_model: str = ""
    prompt: str = ""

class LocalAssetFolderRequest(BaseModel):
    parent: str = ""
    path: str = ""
    name: str = ""

class LocalAssetRenameRequest(BaseModel):
    path: str = ""
    name: str = ""

class AssetLibraryCategoryRequest(BaseModel):
    name: str = "新文件夹"
    type: str = "image"
    library_id: str = ""

class AssetLibraryRequest(BaseModel):
    name: str = "资产库"

class AssetLibraryAddRequest(BaseModel):
    category_id: str = ""
    url: str = ""
    name: str = ""
    library_id: str = ""

class AssetLibraryBatchAddRequest(BaseModel):
    category_id: str = ""
    library_id: str = ""
    items: List[AssetLibraryAddRequest] = []

class SharedFolderRegister(BaseModel):
    path: str = ""
    name: str = ""

class SharedFolderImport(BaseModel):
    library_id: str = ""
    category_id: str = ""
    folder_id: str = ""
    paths: List[str] = []

class AssetLibraryRenameRequest(BaseModel):
    name: str = ""
    library_id: str = ""

class AssetLibraryBatchDeleteRequest(BaseModel):
    ids: List[str] = []
    library_id: str = ""

class AssetLibraryBatchMoveRequest(BaseModel):
    ids: List[str] = []
    library_id: str = ""
    target_library_id: str = ""
    target_category_id: str = ""

class AssetLibraryBatchCropRequest(BaseModel):
    ids: List[str] = []
    library_id: str = ""
    target_library_id: str = ""
    target_category_id: str = ""
    mode: str = "square"

class AssetAvatarRegisterRequest(BaseModel):
    library_id: str = ""
    provider_id: str = ""
    project_name: str = "default"
    group_name: str = ""

class AssetLibraryClassifyRequest(BaseModel):
    library_id: str = ""
    ids: List[str] = []
    provider: str = "comfly"
    model: str = ""
    ms_model: str = ""
    prompt: str = ""

class PromptLibraryRequest(BaseModel):
    name: str = "提示词库"

class PromptLibraryItemRequest(BaseModel):
    library_id: str = ""
    item_id: str = ""
    name: str = "提示词"
    category: str = "custom"
    positive: str = ""
    negative: str = ""
    scene: str = ""

class PromptLibraryBatchDeleteRequest(BaseModel):
    ids: List[str] = []

class PromptLibraryCategoryRequest(BaseModel):
    name: str = "新分组"
    library_id: str = ""

# --- 在线适配器辅助工具 ---

def save_to_history(record):
    with CANVAS_LOCK, HISTORY_LOCK:
        history = []
        if os.path.exists(HISTORY_FILE):
            try:
                with open(HISTORY_FILE, 'r', encoding='utf-8') as f:
                    history = json.load(f)
            except: pass
        if "timestamp" not in record:
            record["timestamp"] = time.time()
        history.insert(0, record)
        with open(HISTORY_FILE, 'w', encoding='utf-8') as f:
            json.dump(history[:5000], f, ensure_ascii=False, indent=4)

def safe_user_id(user_id, request: Request):
    candidate = (user_id or "").strip()
    if not candidate and request.client:
        candidate = f"ip-{request.client.host}"
    if not candidate:
        candidate = "anonymous"
    candidate = re.sub(r"[^a-zA-Z0-9_.-]", "-", candidate)[:80].strip(".-")
    return candidate or "anonymous"

def user_dir(user_id):
    path = os.path.join(CONVERSATION_DIR, user_id)
    os.makedirs(path, exist_ok=True)
    return path

def conversation_path(user_id, conversation_id):
    cleaned = re.sub(r"[^a-zA-Z0-9_-]", "", conversation_id or "")
    if not cleaned:
        raise HTTPException(status_code=400, detail="无效的对话 ID")
    return os.path.join(user_dir(user_id), f"{cleaned}.json")

def now_ms():
    return int(time.time() * 1000)

def save_conversation(user_id, conversation):
    with CANVAS_LOCK, CONVERSATION_LOCK:
        path = conversation_path(user_id, conversation["id"])
        with open(path, 'w', encoding='utf-8') as f:
            json.dump(conversation, f, ensure_ascii=False, indent=2)

def new_conversation(user_id, title="新对话"):
    timestamp = now_ms()
    conversation = {
        "id": uuid.uuid4().hex,
        "title": (title or "新对话")[:80],
        "created_at": timestamp,
        "updated_at": timestamp,
        "messages": [],
    }
    save_conversation(user_id, conversation)
    return conversation

def load_conversation(user_id, conversation_id):
    path = conversation_path(user_id, conversation_id)
    if not os.path.exists(path):
        raise HTTPException(status_code=404, detail="对话不存在")
    with open(path, 'r', encoding='utf-8') as f:
        return json.load(f)

def list_conversations(user_id):
    records = []
    for filename in os.listdir(user_dir(user_id)):
        if not filename.endswith(".json"):
            continue
        path = os.path.join(user_dir(user_id), filename)
        try:
            with open(path, 'r', encoding='utf-8') as f:
                data = json.load(f)
        except Exception:
            continue
        messages = data.get("messages", [])
        last_message = next((m for m in reversed(messages) if m.get("role") != "system"), None)
        records.append({
            "id": data.get("id"),
            "title": data.get("title", "新对话"),
            "created_at": data.get("created_at", 0),
            "updated_at": data.get("updated_at", 0),
            "last_message": (last_message or {}).get("content", ""),
        })
    return sorted(records, key=lambda item: item["updated_at"], reverse=True)

def canvas_path(canvas_id):
    cleaned = re.sub(r"[^a-zA-Z0-9_-]", "", canvas_id or "")
    if not cleaned:
        raise HTTPException(status_code=400, detail="无效的画布 ID")
    return os.path.join(CANVAS_DIR, f"{cleaned}.json")

def save_canvas(canvas):
    if canvas.get("format") == "firefly-canvas-v1" or canvas.get("kind") == "core":
        raise HTTPException(status_code=410, detail="核心画布必须通过带版本号的增量项目接口保存")
    with CANVAS_LOCK:
        canvas["updated_at"] = max(now_ms(), int(canvas.get("updated_at") or 0) + 1)
        with open(canvas_path(canvas["id"]), 'w', encoding='utf-8') as f:
            json.dump(canvas, f, ensure_ascii=False, indent=2)

def normalize_canvas_kind(kind="classic"):
    return "smart" if str(kind or "").strip().lower() == "smart" else "classic"

# ===== 项目（按项目分类管理画布）=====
PROJECTS_PATH = os.path.join(DATA_DIR, "projects.json")
DEFAULT_PROJECT_ID = "default"

def load_projects():
    try:
        with open(PROJECTS_PATH, 'r', encoding='utf-8') as f:
            data = json.load(f)
        projects = data.get("projects") if isinstance(data, dict) else data
        if isinstance(projects, list):
            return [p for p in projects if isinstance(p, dict) and p.get("id")]
    except Exception:
        pass
    return []

def save_projects(projects):
    with CANVAS_LOCK:
        with open(PROJECTS_PATH, 'w', encoding='utf-8') as f:
            json.dump({"projects": projects}, f, ensure_ascii=False, indent=2)

def project_record(p):
    return {
        "id": p.get("id"),
        "name": (p.get("name") or "未命名项目")[:60],
        "order": int(p.get("order") or 0),
        "created_at": p.get("created_at", 0),
        "updated_at": p.get("updated_at", 0),
    }

def ensure_default_project():
    """兼容读取旧分类元数据；核心产品不再创建默认项目。"""
    return load_projects()

def new_project(name="新项目"):
    projects = ensure_default_project()
    ts = now_ms()
    clean = (str(name or "").strip() or "新项目")[:60]
    order = max([int(p.get("order") or 0) for p in projects], default=0) + 1
    proj = {"id": uuid.uuid4().hex, "name": clean, "order": order, "created_at": ts, "updated_at": ts}
    projects.append(proj)
    save_projects(projects)
    return proj

def list_projects():
    projects = ensure_default_project()
    counts = {}
    for rec in iter_canvas_records(include_deleted=False):
        pid = rec.get("project") or DEFAULT_PROJECT_ID
        counts[pid] = counts.get(pid, 0) + 1
    out = []
    for p in sorted(projects, key=lambda x: (int(x.get("order") or 0), x.get("created_at") or 0)):
        rec = project_record(p)
        rec["canvas_count"] = counts.get(rec["id"], 0)
        out.append(rec)
    return out

def new_canvas(title="未命名画布", icon="layers", kind="classic", project=None, board_x=None, board_y=None):
    project_id = str(project or "").strip()
    if not project_id:
        raise HTTPException(status_code=410, detail="旧画布创建入口已停用，请从项目列表新建项目")
    timestamp = now_ms()
    canvas_kind = normalize_canvas_kind(kind)
    canvas = {
        "id": uuid.uuid4().hex,
        "title": (title or ("智能画布" if canvas_kind == "smart" else "未命名画布"))[:80],
        "icon": (icon or ("sparkles" if canvas_kind == "smart" else "🧩"))[:32],
        "kind": canvas_kind,
        "owner": "",
        "color": "",
        "pinned": False,
        "project": project_id,
        "created_at": timestamp,
        "updated_at": timestamp,
        "nodes": [],
        "connections": [],
        "viewport": {"x": 0, "y": 0, "scale": 1},
    }
    if board_x is not None:
        canvas["board_x"] = float(board_x)
    if board_y is not None:
        canvas["board_y"] = float(board_y)
    save_canvas(canvas)
    return canvas

def load_canvas(canvas_id):
    try:
        return project_store_call("canvas", canvas_id)
    except ProjectNotFound:
        pass
    path = canvas_path(canvas_id)
    if not os.path.exists(path):
        raise HTTPException(status_code=404, detail="画布不存在")
    with open(path, 'r', encoding='utf-8') as f:
        canvas = json.load(f)
    if canvas.get("deleted_at"):
        raise HTTPException(status_code=404, detail="画布已在回收站")
    return canvas

def load_canvas_any(canvas_id):
    path = canvas_path(canvas_id)
    if not os.path.exists(path):
        raise HTTPException(status_code=404, detail="画布不存在")
    with open(path, 'r', encoding='utf-8') as f:
        return json.load(f)

CANVAS_COLORS = {"", "red", "orange", "amber", "green", "teal", "blue", "violet", "pink", "slate"}

def normalize_canvas_color(value):
    color = str(value or "").strip().lower()
    return color if color in CANVAS_COLORS else ""

def canvas_record(data):
    return {
        "id": data.get("id"),
        "title": data.get("title", "未命名画布"),
        "icon": data.get("icon", "🧩"),
        "kind": normalize_canvas_kind(data.get("kind")),
        "owner": str(data.get("owner") or "")[:40],
        "color": normalize_canvas_color(data.get("color")),
        "pinned": bool(data.get("pinned") or False),
        "project": str(data.get("project") or "").strip() or DEFAULT_PROJECT_ID,
        "board_x": data.get("board_x"),
        "board_y": data.get("board_y"),
        "created_at": data.get("created_at", 0),
        "updated_at": data.get("updated_at", 0),
        "deleted_at": data.get("deleted_at", 0),
        "node_count": len(data.get("nodes", [])),
    }

def cleanup_expired_canvas_trash():
    cutoff = now_ms() - CANVAS_TRASH_RETENTION_MS
    with CANVAS_LOCK:
        for filename in os.listdir(CANVAS_DIR):
            if not filename.endswith(".json"):
                continue
            path = os.path.join(CANVAS_DIR, filename)
            try:
                with open(path, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                deleted_at = int(data.get("deleted_at") or 0)
                if deleted_at and deleted_at < cutoff:
                    os.remove(path)
            except Exception:
                continue

def iter_canvas_records(include_deleted=False):
    cleanup_expired_canvas_trash()
    records = []
    for filename in os.listdir(CANVAS_DIR):
        if not filename.endswith(".json"):
            continue
        try:
            with open(os.path.join(CANVAS_DIR, filename), 'r', encoding='utf-8') as f:
                data = json.load(f)
        except Exception:
            continue
        is_deleted = bool(data.get("deleted_at"))
        if include_deleted != is_deleted:
            continue
        records.append(canvas_record(data))
    return records

def list_canvases():
    records = iter_canvas_records(include_deleted=False)
    return sorted(
        records,
        key=lambda item: (
            0 if item.get("pinned") else 1,
            -int(item.get("updated_at") or item.get("created_at") or 0),
        ),
    )

def list_deleted_canvases():
    records = iter_canvas_records(include_deleted=True)
    return sorted(records, key=lambda item: item["deleted_at"], reverse=True)

def canvas_asset_url_value(value):
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, dict):
        for key in ("url", "path", "src", "uri", "output", "output_url", "outputUrl", "video", "video_url", "videoUrl"):
            text = str(value.get(key) or "").strip()
            if text:
                return text
    return ""

def canvas_asset_downloadable_url(url):
    text = str(url or "").strip()
    return text if text.startswith(("/output/", "/assets/", "http://", "https://")) else ""

def canvas_asset_kind(value, url=""):
    explicit = ""
    if isinstance(value, dict):
        explicit = str(value.get("kind") or value.get("mediaKind") or value.get("type") or "").lower()
    if "video" in explicit:
        return "video"
    if "audio" in explicit:
        return "audio"
    if "text" in explicit:
        return "text"
    return asset_library_media_kind(url or canvas_asset_url_value(value))

def canvas_asset_name(value, url="", fallback="asset"):
    if isinstance(value, dict):
        for key in ("name", "filename", "file", "title"):
            name = str(value.get(key) or "").strip()
            if name:
                return sanitize_asset_name(name, fallback)
    return sanitize_asset_name(filename_from_media_url(url, fallback), fallback)

def iter_canvas_asset_values(value, path=""):
    if isinstance(value, dict):
        url = canvas_asset_downloadable_url(canvas_asset_url_value(value))
        if url:
            yield path, value, url
        for key, child in value.items():
            if key in {"run", "runs", "settings", "params", "metadata", "meta", "prompt", "text", "caption", "logs"}:
                continue
            yield from iter_canvas_asset_values(child, f"{path}.{key}" if path else str(key))
    elif isinstance(value, list):
        for index, child in enumerate(value):
            yield from iter_canvas_asset_values(child, f"{path}[{index}]")
    elif isinstance(value, str):
        url = canvas_asset_downloadable_url(value)
        if url:
            yield path, value, url

def canvas_node_title(node):
    if not isinstance(node, dict):
        return ""
    return str(node.get("title") or node.get("name") or node.get("label") or node.get("type") or "节点")[:120]

def extract_canvas_assets(canvas):
    record = canvas_record(canvas)
    canvas_id = str(record.get("id") or "")
    items = []
    seen = set()
    nodes = canvas.get("nodes") if isinstance(canvas.get("nodes"), list) else []
    for node_index, node in enumerate(nodes):
        if not isinstance(node, dict):
            continue
        node_id = str(node.get("id") or f"node_{node_index}")
        node_title = canvas_node_title(node)
        for field_path, raw, url in iter_canvas_asset_values(node):
            dedupe_key = url
            if dedupe_key in seen:
                continue
            seen.add(dedupe_key)
            kind = canvas_asset_kind(raw, url)
            if kind not in {"image", "video", "audio", "text"}:
                continue
            fallback = f"{record.get('title') or 'canvas'}-{len(items) + 1}"
            item = {
                "id": hashlib.sha1(f"{canvas_id}:{url}".encode("utf-8")).hexdigest()[:24],
                "url": url,
                "name": canvas_asset_name(raw, url, fallback),
                "kind": kind,
                "canvas_id": canvas_id,
                "canvas_title": record.get("title") or "未命名画布",
                "canvas_kind": record.get("kind") or "classic",
                "canvas_icon": record.get("icon") or "layers",
                "canvas_owner": record.get("owner") or "",
                "canvas_color": record.get("color") or "",
                "canvas_created_at": record.get("created_at") or 0,
                "canvas_updated_at": record.get("updated_at") or 0,
                "node_id": node_id,
                "node_title": node_title,
                "node_type": str(node.get("type") or ""),
                "source_path": field_path,
                "created_at": node.get("created_at") or record.get("updated_at") or record.get("created_at") or 0,
            }
            if isinstance(raw, dict):
                for key in ("natural_w", "natural_h", "width", "height", "size", "duration", "runMs"):
                    if raw.get(key) is not None:
                        item[key] = raw.get(key)
            items.append(item)
    return items

def canvas_assets_index():
    canvases = []
    items = []
    canvas_counts = {"all": 0, "smart": 0, "classic": 0}
    item_counts = {"all": 0, "smart": 0, "classic": 0}
    cleanup_expired_canvas_trash()
    for filename in os.listdir(CANVAS_DIR):
        if not filename.endswith(".json"):
            continue
        try:
            with open(os.path.join(CANVAS_DIR, filename), "r", encoding="utf-8") as f:
                canvas = json.load(f)
        except Exception:
            continue
        if canvas.get("deleted_at"):
            continue
        record = canvas_record(canvas)
        canvas_items = extract_canvas_assets(canvas)
        record["asset_count"] = len(canvas_items)
        canvases.append(record)
        items.extend(canvas_items)
        kind = record.get("kind") or "classic"
        canvas_counts["all"] += 1
        canvas_counts[kind] = canvas_counts.get(kind, 0) + 1
        item_counts["all"] += len(canvas_items)
        item_counts[kind] = item_counts.get(kind, 0) + len(canvas_items)
    canvases.sort(key=lambda item: (0 if item.get("pinned") else 1, -int(item.get("updated_at") or item.get("created_at") or 0)))
    items.sort(key=lambda item: int(item.get("canvas_updated_at") or item.get("created_at") or 0), reverse=True)
    categories = [
        {"id": "all", "name": "全部画布", "count": item_counts.get("all", 0), "canvas_count": canvas_counts.get("all", 0)},
        {"id": "smart", "name": "智能画布", "count": item_counts.get("smart", 0), "canvas_count": canvas_counts.get("smart", 0)},
        {"id": "classic", "name": "普通画布", "count": item_counts.get("classic", 0), "canvas_count": canvas_counts.get("classic", 0)},
    ]
    return {"categories": categories, "canvases": canvases, "items": items}

def display_title(text):
    title = re.sub(r"\s+", " ", text or "").strip()
    return title[:24] or "新对话"

def resolve_chat_provider(provider: str, model: str, ms_model: str):
    if provider == "modelscope":
        clean_token = modelscope_api_key()
        if not clean_token:
            raise HTTPException(status_code=400, detail="未配置 ModelScope API Key，请在 API 设置中填写。")
        base = modelscope_api_root()
        hdrs = {"Authorization": bearer_auth_value(clean_token), "Content-Type": "application/json"}
        mdl = selected_model(ms_model or model, MODELSCOPE_CHAT_MODELS[0] if MODELSCOPE_CHAT_MODELS else "MiniMax/MiniMax-M2.7")
        return base, hdrs, mdl
    api_provider = get_api_provider(provider or "")
    if is_codex_provider(api_provider):
        raise HTTPException(status_code=400, detail="OpenAI CLI 使用本机 codex 登录态，不需要 API Key。请使用画布/聊天里的 OpenAI CLI 专用通道。")
    if is_gemini_cli_provider(api_provider):
        raise HTTPException(status_code=400, detail="Antigravity CLI 使用本机 agy 登录态，不需要 API Key。请使用画布/聊天里的 Antigravity CLI 专用通道。")
    base_root = (api_provider.get("base_url") or AI_BASE_URL).rstrip("/")
    if not base_root:
        raise HTTPException(status_code=400, detail=f"{api_provider.get('name') or api_provider['id']} 未配置 Base URL")
    default_model = preferred_chat_model(api_provider)
    mdl = selected_model(model, default_model)
    protocol = effective_protocol(api_provider, mdl)
    if protocol == "gemini":
        base = base_root if base_root.endswith("/v1beta") else base_root + "/v1beta"
    elif protocol == "volcengine":
        base = base_root if base_root.endswith("/api/v3") else base_root + "/api/v3"
    elif protocol == "runninghub":
        base = RUNNINGHUB_LLM_BASE_URL
    else:
        base = base_root if base_root.endswith("/v1") else base_root + "/v1"
    hdrs = api_headers(provider=api_provider, model=mdl)
    return base, hdrs, mdl

def log_net_error(context, exc, url=""):
    """把网络请求异常的完整链路（含底层 SSL/socket 原因）打到控制台，方便排查 VPN/代理问题。
    httpx 通常把真正的 SSL/连接错误包在 __cause__/__context__ 里，这里把整条链都打出来，
    并附上请求 URL 与当前生效的系统代理，便于判断是「代理瞬时 TLS 错误」还是「线路不通」。
    日志本身绝不能影响主流程，全部包在 try 里。"""
    try:
        chain = []
        cur = exc
        seen = 0
        while cur is not None and seen < 6:
            chain.append(f"{type(cur).__module__}.{type(cur).__name__}: {str(cur)[:200]}")
            nxt = getattr(cur, "__cause__", None) or getattr(cur, "__context__", None)
            if nxt is cur:
                break
            cur = nxt
            seen += 1
        if not url:
            req = getattr(exc, "request", None)
            if req is not None:
                url = str(getattr(req, "url", "") or "")
        try:
            proxies = urllib.request.getproxies() or "无"
        except Exception:
            proxies = "?"
        print(f"[NET-ERR] {context} | url={url or '?'} | sys_proxy={proxies} | " + " <- ".join(chain), flush=True)
    except Exception:
        try:
            print(f"[NET-ERR] {context} | {type(exc).__name__}: {exc}", flush=True)
        except Exception:
            pass

def api_headers(json_body=True, provider=None, model=""):
    if provider:
        if is_codex_provider(provider) or is_gemini_cli_provider(provider):
            raise HTTPException(status_code=400, detail="CLI 协议使用本机登录态，不需要 API Key。当前入口应走对应 CLI 专用通道。")
        api_key = provider_env_key_value(provider["id"])
        provider_name = provider.get("name") or provider["id"]
        if not api_key:
            raise HTTPException(status_code=400, detail=f"未配置 {provider_name} 的 API Key，请在 API 平台管理中填写。")
    else:
        api_key = AI_API_KEY
        if not api_key:
            raise HTTPException(status_code=400, detail="未配置 COMFLY_API_KEY，请在 API/.env 中填写。")
    if provider and effective_protocol(provider, model) == "gemini":
        headers = {"Accept": "application/json", "x-goog-api-key": api_key}
    else:
        headers = {"Accept": "application/json", "Authorization": bearer_auth_value(api_key)}
    if json_body:
        headers["Content-Type"] = "application/json"
    return headers

def selected_model(requested, fallback):
    model = (requested or fallback).strip()
    if not model:
        raise HTTPException(status_code=400, detail="模型名称不能为空")
    if len(model) > 240 or any(ord(ch) < 32 or ord(ch) == 127 for ch in model):
        raise HTTPException(status_code=400, detail=f"模型名称不合法：{model}")
    return model

def looks_like_vision_chat_model(model):
    lc = str(model or "").strip().lower()
    if not lc:
        return False
    vision_keys = [
        "vision", "vl-", "-vl-", "internvl", "qvq", "qwen-vl",
        "doubao-vision", "glm-4v", "minicpm-v",
    ]
    return any(key in lc for key in vision_keys)

def preferred_chat_model(provider):
    values = [str(item or "").strip() for item in (provider.get("chat_models") or [CHAT_MODEL])]
    models = [item for item in values if item]
    if not models:
        return CHAT_MODEL
    if is_volcengine_provider(provider):
        endpoint_models = [item for item in models if item.lower().startswith("ep-")]
        if endpoint_models:
            return endpoint_models[0]
        text_like_models = [item for item in models if not looks_like_vision_chat_model(item)]
        if text_like_models:
            return text_like_models[0]
    # Keep the product default stable when a provider exposes several chat
    # models.  The model list remains user-controlled; this only decides
    # which of the selected models is used when the canvas has no override.
    for hint in ("gpt-5.4", "chatgpt-5.4"):
        exact = next((item for item in models if item.lower() == hint), None)
        if exact:
            return exact
        prefix = next((item for item in models if item.lower().startswith(hint)), None)
        if prefix:
            return prefix
    return models[0]

def modelscope_size(value, fallback="1024x1024"):
    size = str(value or fallback).strip().lower().replace("*", "x")
    if re.fullmatch(r"\d{2,5}x\d{2,5}", size):
        return size
    raise HTTPException(status_code=400, detail=f"ModelScope size 格式不正确：{value or fallback}，应为 WxH，例如 1024x1024")

def unwrap_apimart_response(raw):
    """APIMart 将标准 OpenAI 响应包在 {"code":200,"data":{...}} 里；如果检测到就解包。"""
    if isinstance(raw, dict) and "data" in raw and isinstance(raw.get("data"), dict) and "choices" not in raw:
        return raw["data"]
    return raw

def text_from_chat_response(data):
    data = unwrap_apimart_response(data)
    choices = data.get("choices") or []
    if not choices:
        return ""
    message = choices[0].get("message") or {}
    content = message.get("content", "")
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for item in content:
            if isinstance(item, dict):
                parts.append(item.get("text") or item.get("content") or "")
        return "\n".join(part for part in parts if part)
    return str(content)

def text_delta_from_chat_chunk(data):
    choices = data.get("choices") or []
    if not choices:
        return ""
    delta = choices[0].get("delta") or {}
    content = delta.get("content", "")
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for item in content:
            if isinstance(item, dict):
                parts.append(item.get("text") or item.get("content") or "")
        return "".join(parts)
    return str(content) if content else ""

def sse_event(data):
    return f"data: {json.dumps(data, ensure_ascii=False)}\n\n"

IMAGE_OUTPUT_KEY_HINTS = (
    "url", "image_url", "imageUrl", "image", "output_url", "outputUrl",
    "result_url", "resultUrl", "download_url", "downloadUrl", "asset_url", "assetUrl",
)
IMAGE_CONTAINER_KEY_HINTS = (
    "images", "image", "output", "outputs", "result", "results", "data", "items", "files",
)
IMAGE_BASE64_KEY_HINTS = ("b64_json", "base64", "image_base64", "imageBase64")

def looks_like_generated_image_url(value):
    text = str(value or "").strip()
    if not text:
        return False
    if text.startswith("data:image/"):
        return True
    clean = text.split("?", 1)[0].split("#", 1)[0].lower()
    return text.startswith(("http://", "https://", "/output/", "/assets/")) and re.search(r"\.(png|jpe?g|webp|gif|bmp|tiff?)$", clean)

def looks_like_image_base64(value):
    text = str(value or "").strip()
    if not text:
        return False
    if text.startswith("data:image/"):
        return True
    if len(text) < 200:
        return False
    sample = re.sub(r"\s+", "", text[:4096])
    if not re.fullmatch(r"[A-Za-z0-9+/=_-]+", sample):
        return False
    padded = sample.replace("-", "+").replace("_", "/")
    padded += "=" * (-len(padded) % 4)
    try:
        head = base64.b64decode(padded[:256], validate=False)
    except Exception:
        return False
    return (
        head.startswith(b"\x89PNG\r\n\x1a\n")
        or head.startswith(b"\xff\xd8\xff")
        or head.startswith(b"RIFF") and head[8:12] == b"WEBP"
        or head.startswith(b"GIF87a")
        or head.startswith(b"GIF89a")
    )

def image_payload_from_string(value, mime_type="image/png", assume_b64=False):
    text = str(value or "").strip()
    if not text:
        return None
    if text.startswith("data:image/"):
        header, sep, encoded = text.partition(",")
        if sep and encoded:
            return {
                "type": "b64",
                "value": encoded.strip(),
                "mime_type": header.split(";", 1)[0].replace("data:", "", 1) or mime_type or "image/png",
            }
    if looks_like_generated_image_url(text):
        return {"type": "url", "value": text}
    if assume_b64 or looks_like_image_base64(text):
        return {"type": "b64", "value": text, "mime_type": mime_type or "image/png"}
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
            return image_payload_from_string(item, value.get("mime_type") or value.get("mimeType") or "image/png", assume_b64=True)
    for key in IMAGE_OUTPUT_KEY_HINTS:
        item = value.get(key)
        if isinstance(item, str):
            found = image_payload_from_string(item, value.get("mime_type") or value.get("mimeType") or "image/png")
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
        img_type = item.get("type") or "url"
        value = item.get("value")
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
        if value.get("type") == "image_generation_call":
            result = value.get("result")
            if isinstance(result, str) and result.strip():
                add_image(image_payload_from_string(
                    result,
                    value.get("mime_type") or value.get("mimeType") or "image/png",
                    assume_b64=not looks_like_generated_image_url(result),
                ))
            else:
                collect(result, depth + 1)
        has_direct_url = any(
            isinstance(value.get(key), str) and looks_like_generated_image_url(value.get(key))
            for key in IMAGE_OUTPUT_KEY_HINTS
        )
        if not has_direct_url:
            for key in IMAGE_BASE64_KEY_HINTS:
                item = value.get(key)
                if isinstance(item, str) and item.strip():
                    add_image(image_payload_from_string(item, value.get("mime_type") or value.get("mimeType") or "image/png", assume_b64=True))
        for key in IMAGE_OUTPUT_KEY_HINTS:
            item = value.get(key)
            if isinstance(item, str):
                add_image(image_payload_from_string(item, value.get("mime_type") or value.get("mimeType") or "image/png"))
            else:
                collect(item, depth + 1)
        for key in IMAGE_CONTAINER_KEY_HINTS:
            collect(value.get(key), depth + 1)

    candidates = data.get("candidates") if isinstance(data, dict) else None
    if isinstance(candidates, list):
        for candidate in candidates:
            if not isinstance(candidate, dict):
                continue
            content = candidate.get("content") or {}
            parts = content.get("parts") if isinstance(content, dict) else None
            if not isinstance(parts, list):
                continue
            for part in parts:
                if not isinstance(part, dict):
                    continue
                inline = part.get("inlineData") or part.get("inline_data") or {}
                if not isinstance(inline, dict):
                    continue
                value = inline.get("data")
                if value:
                    add_image({
                        "type": "b64",
                        "value": value,
                        "mime_type": inline.get("mimeType") or inline.get("mime_type") or "image/png",
                    })

    current = data
    if isinstance(current, dict) and isinstance(current.get("data"), dict) and isinstance(current["data"].get("result"), dict):
        current = current["data"]
    if isinstance(current, dict) and isinstance(current.get("result"), dict):
        for item in current["result"].get("images") or []:
            if not isinstance(item, dict):
                collect(item)
                continue
            url = item.get("url")
            if isinstance(url, list):
                for one in url:
                    collect(one)
            else:
                collect(url)
            collect(item)

    collect(data)
    if isinstance(data, dict) and isinstance(data.get("data"), dict) and isinstance(data["data"].get("data"), dict):
        collect(data["data"]["data"])
    if found:
        return found
    raise HTTPException(status_code=502, detail="无法识别生图接口返回格式")

def extract_image(data):
    try:
        images = extract_images(data)
        if images:
            return images[0]
    except HTTPException:
        pass
    candidates = data.get("candidates") if isinstance(data, dict) else None
    if isinstance(candidates, list):
        for candidate in candidates:
            if not isinstance(candidate, dict):
                continue
            content = candidate.get("content") or {}
            parts = content.get("parts") if isinstance(content, dict) else None
            if not isinstance(parts, list):
                continue
            for part in parts:
                if not isinstance(part, dict):
                    continue
                inline = part.get("inlineData") or part.get("inline_data") or {}
                if not isinstance(inline, dict):
                    continue
                value = inline.get("data")
                if value:
                    return {
                        "type": "b64",
                        "value": value,
                        "mime_type": inline.get("mimeType") or inline.get("mime_type") or "image/png",
                    }
    if isinstance(data.get("data"), dict) and isinstance(data["data"].get("result"), dict):
        data = data["data"]
    if isinstance(data.get("result"), dict):
        result_images = data["result"].get("images") or []
        if result_images:
            first = result_images[0]
            url = first.get("url")
            if isinstance(url, list) and url:
                return {"type": "url", "value": url[0]}
            if isinstance(url, str) and url:
                return {"type": "url", "value": url}
    flexible = extract_image_flexible(data)
    if flexible:
        return flexible
    if isinstance(data.get("data"), dict) and isinstance(data["data"].get("data"), dict):
        data = data["data"]["data"]
    images = data.get("data") or []
    if not isinstance(images, list) or not images:
        raise HTTPException(status_code=502, detail="生图接口没有返回图片数据")
    first = images[0]
    if first.get("url"):
        return {"type": "url", "value": first["url"]}
    if first.get("b64_json"):
        return {"type": "b64", "value": first["b64_json"]}
    flexible = extract_image_flexible(first)
    if flexible:
        return flexible
    raise HTTPException(status_code=502, detail="无法识别生图接口返回格式")

def extract_task_id(data):
    if data.get("task_id"):
        return str(data["task_id"])
    if data.get("taskId"):
        return str(data["taskId"])
    if data.get("submit_id"):
        return str(data["submit_id"])
    if data.get("video_id"):
        return str(data["video_id"])
    if data.get("videoId"):
        return str(data["videoId"])
    if data.get("id") and str(data.get("id", "")).startswith("task"):
        return str(data["id"])
    nested = data.get("data")
    if isinstance(nested, list) and nested:
        first = nested[0]
        if isinstance(first, dict):
            return extract_task_id(first)
    if isinstance(nested, dict):
        return extract_task_id(nested)
    return None

def extract_task_id_from_text(text):
    value = str(text or "")
    match = re.search(r"(?:task_id|taskId|task id)\s*[=:：]\s*([A-Za-z0-9_.:-]+)", value, re.IGNORECASE)
    return match.group(1) if match else ""

def images_api_unsupported(response):
    text = str(getattr(response, "text", "") or "").lower()
    return "images api is not supported" in text or "not supported for this platform" in text

def responses_image_size_instruction(size: str) -> str:
    """RS 中转多为网页版逆向：结构化 size 参数（tool.size / 顶层 size / --size 尾注）全被无视，
    只有内部模型能“听懂”的自然语言比例要求有效（实测中文明确说横版+比例+禁止正方形可让
    1:1 变成 3:2 横版）。这里生成中英双语的强化指令。"""
    match = re.match(r"^\s*(\d{2,5})\s*[xX*]\s*(\d{2,5})\s*$", str(size or ""))
    if not match:
        return ""
    width, height = int(match.group(1)), int(match.group(2))
    if width <= 0 or height <= 0:
        return ""
    if width == height:
        return "请生成正方形图片（宽高比 1:1）。Generate a SQUARE image (aspect ratio 1:1)."
    from fractions import Fraction
    ratio = Fraction(width, height).limit_denominator(32)
    rw, rh = ratio.numerator, ratio.denominator
    if width > height:
        zh_shape, en_shape = "横版（宽幅）", "LANDSCAPE (wide)"
    else:
        zh_shape, en_shape = "竖版（长幅）", "PORTRAIT (tall)"
    return (
        f"请生成{zh_shape}图片：宽高比 {rw}:{rh}，目标尺寸为宽 {width} × 高 {height} 像素，绝对不要输出正方形（1:1）。"
        f" Generate a {en_shape} image with aspect ratio {rw}:{rh}, target size {width}x{height} pixels (width x height)."
        f" Never output a square 1:1 image. Do not swap width and height."
    )

def responses_proxy_tool_size(size: str) -> str:
    """部分 RS 中转把 image_generation.size 当成 height x width；这里只对 RS 模式做兼容翻转。"""
    match = re.match(r"^\s*(\d{2,5})\s*[xX*]\s*(\d{2,5})\s*$", str(size or ""))
    if not match:
        return str(size or "").strip()
    width, height = match.group(1), match.group(2)
    return f"{height}x{width}" if width != height else f"{width}x{height}"

async def responses_input_image_url(ref, require_public_url=False) -> str:
    """RS / Responses 的 input_image。
    本机/内网 URL 不能透传（上游拉不到会挂到 Cloudflare 120s 超时/524）。
    本地文件优先上传图床（同视频卡片的 Litterbox/temp.sh 通道）换公网短链——
    几 MB 的 base64 请求体会让部分中转源站处理超时，公网 URL 让请求体和文生图一样小；
    图床不可用时回退内联 base64（Responses 协议两种都支持）。"""
    raw = ref.get("url", "") if isinstance(ref, dict) else ref
    text = str(raw or "").strip()
    if not text:
        return ""
    local_path = text
    if re.match(r"^https?://", text, re.I):
        parsed = urllib.parse.urlsplit(text)
        host = (parsed.hostname or "").lower()
        if host in {"127.0.0.1", "localhost", "::1"} or re.match(r"^(192\.168\.|10\.|172\.(1[6-9]|2\d|3[01])\.)", host):
            local_path = urllib.parse.unquote(parsed.path or "")
        else:
            return text
    local_file = output_file_from_url(local_path)
    if not local_file:
        if require_public_url:
            raise HTTPException(status_code=400, detail=f"RS 参考图不是公网 URL，无法传给上游：{text[:160]}")
        return ""
    if require_public_url:
        return await openai_video_proxy_public_reference_url(local_path)
    try:
        uploaded = await upload_local_video_to_cloud(local_path)
        url = str((uploaded or {}).get("url") or "")
        if url.startswith(("http://", "https://")):
            return url
    except HTTPException as exc:
        print(f"RS 参考图上传图床失败，回退内联 base64：{exc.detail}")
    except Exception as exc:
        print(f"RS 参考图上传图床异常，回退内联 base64：{exc}")
    data_url = reference_to_data_url({"url": local_path}, max_size=1536)
    return data_url if data_url.startswith("data:") else ""

def responses_no_image_detail(data) -> str:
    if not isinstance(data, dict):
        return ""
    details = []
    error = data.get("error")
    if isinstance(error, dict):
        msg = error.get("message") or error.get("detail") or error.get("code")
        if msg:
            details.append(str(msg))
    output_text = data.get("output_text")
    if isinstance(output_text, str) and output_text.strip():
        details.append(output_text.strip()[:300])
    output = data.get("output")
    if isinstance(output, list):
        for item in output:
            if not isinstance(item, dict) or item.get("type") != "image_generation_call":
                continue
            status = item.get("status")
            if status:
                details.append(f"image_generation_call.status={status}")
            item_error = item.get("error")
            if isinstance(item_error, dict):
                msg = item_error.get("message") or item_error.get("detail") or item_error.get("code")
                if msg:
                    details.append(str(msg))
            elif isinstance(item_error, str) and item_error.strip():
                details.append(item_error.strip())
    joined = "；".join(dict.fromkeys(details))
    return f"RS / Responses 没有返回图片数据{f'：{joined}' if joined else ''}"

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
            for key in ("output", "content", "text", "output_text", "message", "response"):
                if key in value:
                    collect(value[key], depth + 1)
    collect(raw)
    for text in texts:
        match = re.search(r"!\[[^\]]*\]\((https?://[^)\s]+)\)", text)
        if match:
            return {"type": "url", "value": match.group(1)}
        match = re.search(r"https?://[^\s)\"'<>]+\.(?:png|jpe?g|webp|gif)(?:\?[^\s)\"'<>]*)?", text, re.I)
        if match:
            return {"type": "url", "value": match.group(0)}
    return None

def _responses_wrap(url, status_code, payload):
    return httpx.Response(
        status_code,
        headers={"content-type": "application/json"},
        content=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        request=httpx.Request("POST", url),
    )

RESPONSES_REJECT_STATUSES = {400, 404, 405, 415, 422}
RESPONSES_POLL_INTERVAL = 5.0
RESPONSES_POLL_MAX_SECONDS = 1500.0

async def post_openai_responses(client, url, headers, body):
    """RS / Responses 请求。图片编辑经常超过 120 秒，非流式请求会被中转前面的
    Cloudflare 读超时掐断（Error 524）。策略按可靠性排序：
    1) background:true 后台任务 + 轮询 GET /v1/responses/{id}（每个请求都秒回，彻底绕开超时）；
    2) 后台模式被拒（4xx 参数类错误）→ SSE 流式；
    3) 流式也被拒 → 非流式直接请求。
    5xx/超时一律不自动重试，避免上游已开始生成后重复扣费。"""
    bg_body = dict(body)
    bg_body["background"] = True
    try:
        resp = await client.post(url, headers=headers, json=bg_body)
    except httpx.HTTPError as e:
        print(f"RS background 请求传输失败，改走流式：{e}")
        return await post_openai_responses_stream(client, url, headers, body)
    if resp.status_code in RESPONSES_REJECT_STATUSES:
        print(f"RS background 模式被拒（{resp.status_code}），改走流式：{resp.text[:200]}")
        return await post_openai_responses_stream(client, url, headers, body)
    if resp.status_code >= 400:
        if resp.status_code == 524:
            return _responses_wrap(url, 502, {"error": {"message": (
                "中转在 background 模式下仍然 524 超时：该渠道对 /v1/responses 的 background/stream 都不透传，"
                "无法完成超过 120 秒的图片编辑。请换支持 Responses 透传的渠道。上游原文："
                f"{resp.text[:300]}"
            )}})
        return resp
    try:
        data = resp.json()
    except ValueError:
        return resp
    status = str((data or {}).get("status") or "").lower()
    rid = str((data or {}).get("id") or "").strip()
    if status not in {"queued", "in_progress", "processing", "pending", "running"} or not rid:
        return resp  # 中转忽略 background 直接同步返回了结果（或未知结构），交给下游解析
    # 轮询后台任务
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
                return _responses_wrap(url, 502, {"error": {"message": f"RS 后台任务轮询连续失败：{e}（任务 id={rid}）"}})
            continue
        if poll.status_code >= 400:
            transient_failures += 1
            if transient_failures > 5:
                return _responses_wrap(url, 502, {"error": {"message": f"RS 后台任务轮询失败（{poll.status_code}）：{poll.text[:200]}（任务 id={rid}）"}})
            continue
        transient_failures = 0
        try:
            data = poll.json()
        except ValueError:
            continue
        status = str((data or {}).get("status") or "").lower()
        if status == "completed":
            return _responses_wrap(url, 200, data)
        if status in {"failed", "cancelled", "incomplete"}:
            return _responses_wrap(url, 502, data)
    return _responses_wrap(url, 502, {"error": {"message": f"RS 后台任务超过 {int(RESPONSES_POLL_MAX_SECONDS)}s 仍未完成（任务 id={rid}）"}})

async def post_openai_responses_stream(client, url, headers, body):
    """RS / Responses 的 SSE 流式请求：流式从一开始就持续有事件字节返回，
    不会触发中转的 Cloudflare 120s 读超时。收到 response.completed 后
    把完整 response 对象包装成普通 httpx.Response，下游解析逻辑不变。"""
    request = httpx.Request("POST", url)

    def wrap(status_code, payload):
        return _responses_wrap(url, status_code, payload)

    stream_body = dict(body)
    stream_body["stream"] = True
    try:
        async with client.stream("POST", url, headers=headers, json=stream_body) as resp:
            ctype = (resp.headers.get("content-type") or "").lower()
            if resp.status_code >= 400 or "text/event-stream" not in ctype:
                content = await resp.aread()
                # 个别中转不支持 responses 流式（对 stream 参数直接报错）→ 回退一次非流式。
                # 仅对“请求被拒绝”类状态码回退，5xx/超时不重试，避免上游已开始生成后重复扣费。
                if resp.status_code in {400, 404, 405, 415, 422}:
                    print(f"RS 流式请求被拒（{resp.status_code}），回退非流式：{content[:200]!r}")
                    return await client.post(url, headers=headers, json=body)
                return httpx.Response(resp.status_code, headers=resp.headers, content=content, request=request)
            completed = None
            error_payload = None
            stream_images = []
            stream_seen_images = set()

            def remember_stream_image(image):
                if not isinstance(image, dict):
                    return
                value = image.get("value")
                if not value:
                    return
                key = (image.get("type") or "url", value)
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
                if not line.startswith("data:"):
                    continue
                chunk = line[5:].strip()
                if not chunk or chunk == "[DONE]":
                    continue
                try:
                    event = json.loads(chunk)
                except ValueError:
                    continue
                if not isinstance(event, dict):
                    continue
                etype = str(event.get("type") or "")
                if etype in {"response.completed", "response.incomplete"} and isinstance(event.get("response"), dict):
                    completed = event["response"]
                elif etype == "response.failed":
                    failed = event.get("response")
                    error_payload = failed if isinstance(failed, dict) else {"error": {"message": "response.failed"}}
                elif etype == "error":
                    message = event.get("message") or event.get("error") or chunk[:300]
                    error_payload = {"error": {"message": str(message)}}
                if isinstance(event.get("item"), dict):
                    item = event["item"]
                    if item.get("type") not in {"input_image", "input_text"}:
                        remember_stream_images_from(item)
                for key in ("partial_image_b64", "image_b64", "b64_json"):
                    image = image_payload_from_string(event.get(key), assume_b64=True)
                    if image:
                        remember_stream_image(image)
                for key in ("result", "image", "image_url"):
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
                    completed["output"] = list(completed.get("output") or [])
                    for image in stream_images:
                        if image.get("type") == "b64":
                            completed["output"].append({
                                "type": "image_generation_call",
                                "status": "completed",
                                "result": image.get("value"),
                                "mime_type": image.get("mime_type") or "image/png",
                            })
                        else:
                            completed["output"].append({"type": "image", "image_url": image.get("value")})
            if completed is None and error_payload is None and stream_images:
                # 流被提前掐断但已收到图片事件：用最后一张图片兜底。
                image = stream_images[-1]
                if image.get("type") == "b64":
                    completed = {"output": [{"type": "image_generation_call", "status": "completed", "result": image.get("value"), "mime_type": image.get("mime_type") or "image/png"}]}
                else:
                    completed = {"output": [{"type": "image", "image_url": image.get("value")}]}
            if completed is not None:
                return wrap(200, completed)
            return wrap(502, error_payload or {"error": {"message": "RS 流式响应结束但没有 response.completed 事件"}})
    except httpx.HTTPError as e:
        print(f"RS 流式请求传输失败，回退非流式：{e}")
        return await client.post(url, headers=headers, json=body)

def provider_protocol(provider):
    return str((provider or {}).get("protocol") or "openai").strip().lower()

# 单模型可覆盖的协议（仅 OpenAI / Gemini，二者可共用同一站点的 Base URL + Key）
PER_MODEL_PROTOCOL_OPTIONS = {"openai", "gemini"}
# 协议固定、不支持单模型覆盖的内置平台
FIXED_PROTOCOL_PROVIDER_IDS = {"modelscope", "volcengine", "jimeng", "runninghub"}

def normalize_model_protocols(value):
    """规整 {模型名: 协议} 覆盖表，仅保留 openai/gemini。"""
    out = {}
    if isinstance(value, dict):
        for raw_name, raw_proto in value.items():
            name = str(raw_name or "").strip()
            proto = str(raw_proto or "").strip().lower()
            if name and proto in PER_MODEL_PROTOCOL_OPTIONS:
                out[name] = proto
    return out

def normalize_model_name_map(value):
    """规整 {模型ID: 展示名}，只保存真正有意义的显示标签。"""
    normalized = {}
    if isinstance(value, dict):
        for raw_model, raw_label in value.items():
            model = str(raw_model or "").strip()
            label = re.sub(r"\s+", " ", str(raw_label or "").strip())[:160]
            if model and label and label != model:
                normalized[model] = label
    return normalized

def effective_protocol(provider, model=""):
    """返回某模型实际生效的协议：优先单模型覆盖，否则用平台全局协议。"""
    base = provider_protocol(provider)
    pid = str((provider or {}).get("id") or "").strip().lower()
    if pid in FIXED_PROTOCOL_PROVIDER_IDS:
        return base
    overrides = (provider or {}).get("model_protocols")
    if isinstance(overrides, dict):
        val = str(overrides.get(str(model or "").strip()) or "").strip().lower()
        if val in PER_MODEL_PROTOCOL_OPTIONS:
            return val
    return base

def is_apimart_provider(provider):
    base_url = str((provider or {}).get("base_url") or "").lower()
    return provider_protocol(provider) == "apimart" or "apimart.ai" in base_url

def is_fhl_provider(provider):
    base_url = str((provider or {}).get("base_url") or "").strip().lower()
    name = str((provider or {}).get("name") or "").strip().lower()
    try:
        host = urllib.parse.urlsplit(base_url).netloc.lower()
    except Exception:
        host = ""
    return host in {"www.fhl.mom", "fhl.mom"} or name in {"fhl", "fhl-image"}

def detect_image_request_mode(base_url="", models=None):
    base = str(base_url or "").strip().lower()
    if "apihub.agnes-ai.com" in base:
        return "openai-json"
    for model in models or []:
        if str(model or "").strip().lower().startswith("agnes-image-"):
            return "openai-json"
    return ""

def effective_image_request_mode(provider, model=""):
    detected = detect_image_request_mode((provider or {}).get("base_url"), [model])
    if detected:
        return detected
    return normalize_image_request_mode((provider or {}).get("image_request_mode"))

def is_gemini_provider(provider):
    return provider_protocol(provider) == "gemini"

def is_volcengine_provider(provider):
    return provider_protocol(provider) == "volcengine"

def is_runninghub_provider(provider):
    return provider_protocol(provider) == "runninghub" or str((provider or {}).get("id") or "").strip().lower() == "runninghub"

def is_jimeng_provider(provider):
    return provider_protocol(provider) == "jimeng" or str((provider or {}).get("id") or "").strip().lower() == "jimeng"

def is_codex_provider(provider):
    return provider_protocol(provider) == "codex"

def is_gemini_cli_provider(provider):
    return provider_protocol(provider) == "gemini-cli"

def codex_env_value(key):
    return os.getenv(key, "") or read_api_env_value(key)

def codex_cli_executable():
    configured = str(codex_env_value("CODEX_BIN") or "").strip()
    if configured:
        return configured
    return shutil.which("codex") or shutil.which("codex.exe") or shutil.which("codex.cmd") or ""

def codex_timeout(default=CODEX_DEFAULT_TIMEOUT):
    try:
        return max(30, min(3600, int(os.getenv("CODEX_CLI_TIMEOUT", str(default)) or default)))
    except Exception:
        return default

def codex_model_for_exec(model="", fallback=""):
    value = str(model or fallback or "").strip()
    low = value.lower()
    if not value or low.startswith("$imagegen") or low.startswith("gpt-image"):
        return ""
    return value

def codex_decode_output(stdout, stderr):
    out_text = (stdout or b"").decode("utf-8", errors="replace").strip()
    err_text = (stderr or b"").decode("utf-8", errors="replace").strip()
    return out_text, err_text

async def run_codex_cli(prompt, model="", image_paths=None, timeout=None, output_last_message=True):
    exe = codex_cli_executable()
    if not exe:
        raise HTTPException(status_code=400, detail="未找到 OpenAI Codex CLI。请先运行 CLI/windows/openai/install_openai_codex_cli.bat，并完成 codex 登录。")
    image_paths = [str(path) for path in (image_paths or []) if path and os.path.isfile(str(path))]
    last_path = ""
    args = [
        exe,
        "exec",
        "--cd",
        BASE_DIR,
        "--sandbox",
        "workspace-write",
        "--skip-git-repo-check",
    ]
    exec_model = codex_model_for_exec(model)
    if exec_model:
        args.extend(["--model", exec_model])
    for path in image_paths:
        args.extend(["--image", path])
    if output_last_message:
        fd, last_path = tempfile.mkstemp(prefix="codex_last_", suffix=".txt", dir=OUTPUT_OUTPUT_DIR)
        os.close(fd)
        args.extend(["--output-last-message", last_path])
    args.append("-")
    prompt_bytes = str(prompt or "").encode("utf-8")
    try:
        proc = await asyncio.create_subprocess_exec(
            *args,
            cwd=BASE_DIR,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await asyncio.wait_for(proc.communicate(input=prompt_bytes), timeout=timeout or codex_timeout())
    except asyncio.TimeoutError as exc:
        raise HTTPException(status_code=504, detail="OpenAI Codex CLI 执行超时。可设置 CODEX_CLI_TIMEOUT 增大等待时间。") from exc
    except FileNotFoundError as exc:
        raise HTTPException(status_code=400, detail=f"未找到 OpenAI Codex CLI：{exe}") from exc
    out_text, err_text = codex_decode_output(stdout, stderr)
    last_text = ""
    if last_path and os.path.exists(last_path):
        try:
            with open(last_path, "r", encoding="utf-8-sig") as f:
                last_text = f.read().strip()
        except Exception:
            last_text = ""
        try:
            os.remove(last_path)
        except Exception:
            pass
    if proc.returncode != 0:
        message = err_text or out_text or last_text or f"exit={proc.returncode}"
        raise HTTPException(status_code=502, detail=f"OpenAI Codex CLI 调用失败：{message[:1200]}")
    return {"text": last_text or out_text, "_stdout": out_text, "_stderr": err_text}

def codex_output_image_files(since_time=0):
    exts = {".png", ".jpg", ".jpeg", ".webp", ".gif"}
    root = os.path.abspath(OUTPUT_OUTPUT_DIR)
    files = []
    try:
        for name in os.listdir(root):
            path = os.path.join(root, name)
            if not os.path.isfile(path):
                continue
            ext = os.path.splitext(name)[1].lower()
            if ext not in exts:
                continue
            mtime = os.path.getmtime(path)
            if mtime + 1 < float(since_time or 0):
                continue
            files.append((mtime, path))
    except Exception:
        return []
    return [path for _mtime, path in sorted(files, reverse=True)]

def codex_output_url_from_path(path):
    path = os.path.abspath(str(path or ""))
    root = os.path.abspath(OUTPUT_OUTPUT_DIR)
    try:
        if os.path.commonpath([root, path]) == root:
            return output_url_for(os.path.basename(path), "output")
    except Exception:
        return ""
    return ""

def gpt_image_2_skill_executable():
    configured = str(codex_env_value("GPT_IMAGE_2_SKILL_BIN") or "").strip()
    if configured:
        return configured
    return (
        shutil.which("gpt-image-2-skill")
        or shutil.which("gpt-image-2-skill.exe")
        or shutil.which("gpt-image-2-skill.cmd")
        or ""
    )

def gpt_image_2_skill_auth_file():
    configured = str(codex_env_value("GPT_IMAGE_2_SKILL_AUTH_FILE") or codex_env_value("CODEX_AUTH_FILE") or "").strip()
    if configured:
        return configured
    project_auth = os.path.join(BASE_DIR, "API", "openai-gpt-account-auth.json")
    user_profile = os.getenv("USERPROFILE", "").strip()
    candidates = [
        project_auth,
        os.path.join(user_profile, ".codex", "auth.json") if user_profile else "",
        os.path.join(os.path.expanduser("~"), ".codex", "auth.json"),
    ]
    for path in candidates:
        if path and os.path.isfile(path):
            return path
    return candidates[0] if candidates and candidates[0] else ""

def gpt_image_2_skill_auth_json(auth_file=""):
    path = str(auth_file or "").strip()
    if not path or not os.path.isfile(path):
        return {}
    try:
        with open(path, "r", encoding="utf-8-sig") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}

def gpt_image_2_skill_access_token(auth_data):
    if not isinstance(auth_data, dict):
        return ""
    for key in ("access_token", "accessToken"):
        value = str(auth_data.get(key) or "").strip()
        if value:
            return value
    tokens = auth_data.get("tokens")
    if isinstance(tokens, dict):
        for key in ("access_token", "accessToken"):
            value = str(tokens.get(key) or "").strip()
            if value:
                return value
    return ""

def gpt_image_2_skill_api_key(auth_data=None):
    for key in ("GPT_IMAGE_2_SKILL_API_KEY", "OPENAI_API_KEY"):
        value = str(codex_env_value(key) or "").strip()
        if value:
            return value
    if isinstance(auth_data, dict):
        value = str(auth_data.get("OPENAI_API_KEY") or auth_data.get("api_key") or auth_data.get("apiKey") or "").strip()
        if value:
            return value
    user_profile = os.getenv("USERPROFILE", "").strip()
    user_auth = os.path.join(user_profile, ".codex", "auth.json") if user_profile else ""
    if user_auth:
        user_data = gpt_image_2_skill_auth_json(user_auth)
        value = str(user_data.get("OPENAI_API_KEY") or user_data.get("api_key") or user_data.get("apiKey") or "").strip()
        if value:
            return value
    return ""

def gpt_image_2_skill_provider_args(auth_file=""):
    auth_data = gpt_image_2_skill_auth_json(auth_file)
    if gpt_image_2_skill_access_token(auth_data):
        return ["--provider", "codex", "--auth-file", auth_file] if auth_file else ["--provider", "codex"], "codex"
    api_key = gpt_image_2_skill_api_key(auth_data)
    if api_key:
        return ["--provider", "openai", "--api-key", api_key], "openai"
    return (["--provider", "codex", "--auth-file", auth_file] if auth_file else ["--provider", "codex"]), "codex"

def gpt_image_2_skill_model_arg(model="", provider="openai"):
    value = str(model or "").strip()
    low = value.lower()
    provider = str(provider or "").strip().lower()
    if provider == "codex":
        if not value or low.startswith("$imagegen") or low.startswith("gpt-image"):
            return "gpt-5.4"
        return value
    if not value or low.startswith("$imagegen"):
        return "gpt-image-2"
    return value

def gpt_image_2_skill_size_arg(size="", model="", prompt="", provider="openai"):
    text = " ".join([str(size or ""), str(model or ""), str(prompt or "")]).lower()
    size_text = str(size or "").strip()
    if str(provider or "").strip().lower() == "codex":
        if "1k" in text or "1024" in text:
            return "1K"
        if "2k" in text or "2048" in text:
            return "2K"
        if "4k" in text or "3840" in text:
            return "4K"
        width, height = parse_size_pair(size_text)
        if 0 < max(width, height) < 1800:
            return "1K"
        if 1800 <= max(width, height) < 3000:
            return "2K"
        return "4K"
    match = re.search(r"(\d{3,5})\s*[x×*]\s*(\d{3,5})", size_text, flags=re.I)
    if match:
        width = int(match.group(1))
        height = int(match.group(2))
        if width > 0 and height > 0:
            return normalize_gpt_image_2_size(f"{width}x{height}")
    ratio_match = re.fullmatch(r"\s*(\d{1,2})\s*:\s*(\d{1,2})\s*", size_text)
    if ratio_match:
        ratio = f"{int(ratio_match.group(1))}:{int(ratio_match.group(2))}"
        options = CHAT_RATIO_SIZE_OPTIONS.get(ratio)
        if options:
            if "4k" in text or "3840" in text:
                return options[-1]
            if "1k" in text or "1024" in text:
                return options[0]
            return options[1] if len(options) > 1 else options[0]
    if "4k" in text or "3840" in text:
        return "4K"
    if "1k" in text or "1024" in text:
        return "1K"
    return "2K"

def gpt_image_2_skill_prompt_arg(prompt="", size="", provider="openai"):
    prompt_text = str(prompt or "").strip()
    if str(provider or "").strip().lower() != "codex":
        return prompt_text
    size_arg = gpt_image_2_skill_size_arg(size, "", prompt, provider)
    size_text = str(size or "").strip()
    width, height = parse_size_pair(size_text)
    ratio_text = ""
    if width and height:
        divisor = math.gcd(width, height) or 1
        ratio_text = f"{width // divisor}:{height // divisor}"
    else:
        ratio_match = re.fullmatch(r"\s*(\d{1,2})\s*:\s*(\d{1,2})\s*", size_text)
        if ratio_match:
            width = int(ratio_match.group(1))
            height = int(ratio_match.group(2))
            ratio_text = f"{width}:{height}"
    if not ratio_text:
        return f"{prompt_text} 画质要求：目标输出 {size_arg} 高分辨率图片。 Image quality requirement: output a {size_arg} high-resolution image."
    orientation_zh = "横版/宽幅" if width > height else ("竖版/长幅" if height > width else "正方形")
    orientation_en = "landscape/wide" if width > height else ("portrait/tall" if height > width else "square")
    return (
        f"{prompt_text} "
        f"画质要求：目标输出 {size_arg} 高分辨率图片。"
        f"画幅要求：必须生成 {orientation_zh} 图片，宽高比 {ratio_text}。"
        f"请不要交换宽高，不要输出反向比例。"
        f" Image quality requirement: output a {size_arg} high-resolution image."
        f" Canvas requirement: generate a {orientation_en} image with aspect ratio {ratio_text}; "
        "do not swap width and height."
    )

def parse_gpt_image_2_skill_output(stdout_text="", stderr_text=""):
    items = []
    for line in (stdout_text or "").splitlines():
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
        candidates = [
            item.get("path"),
            item.get("file"),
            item.get("output"),
            item.get("out"),
            item.get("url"),
        ]
        for image in item.get("images") or []:
            if isinstance(image, dict):
                candidates.extend([image.get("path"), image.get("file"), image.get("url")])
            else:
                candidates.append(image)
        for candidate in candidates:
            value = str(candidate or "").strip()
            if value:
                paths.append(value)
    text = stdout_text or stderr_text or ""
    pattern = r"([A-Za-z]:\\[^\r\n\"'<>]+\.(?:png|jpe?g|webp|gif)|/[^\r\n\"'<>]+\.(?:png|jpe?g|webp|gif))"
    paths.extend(re.findall(pattern, text, flags=re.I))
    return items, paths

def gpt_image_2_skill_failure_message(stdout_text="", stderr_text="", returncode=0):
    combined = "\n".join([str(stdout_text or "").strip(), str(stderr_text or "").strip()]).strip()
    if not combined:
        return f"exit={returncode}"
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
        error = item.get("error")
        if isinstance(error, dict):
            msg = error.get("message") or error.get("detail") or error.get("code")
            if msg:
                messages.append(str(msg))
        elif isinstance(error, str) and error.strip():
            messages.append(error.strip())
        if item.get("ok") is False:
            msg = item.get("message") or item.get("detail")
            if msg:
                messages.append(str(msg))
        data = item.get("data")
        if isinstance(data, dict):
            msg = data.get("error") or data.get("message") or data.get("status")
            event_type = str(item.get("type") or data.get("phase") or "").strip()
            if msg and event_type not in {"request.started", "request_started"}:
                progress.append(f"{event_type}: {msg}" if event_type else str(msg))
    if messages:
        return "；".join(dict.fromkeys(messages))[:1600]
    if plain_lines:
        return "\n".join(plain_lines)[:1600]
    if progress:
        return ("只收到了进度事件，没有收到最终错误详情：" + "；".join(dict.fromkeys(progress)))[:1600]
    return combined[:1600]

def codex_postprocess_image_to_requested_size(path="", requested_size="", provider=""):
    provider_text = str(provider or "").strip().lower()
    if provider_text not in {"codex", "gemini-cli"}:
        return ""
    width, height = parse_size_pair(requested_size)
    if not width or not height or not path or not os.path.isfile(path):
        return ""
    try:
        with Image.open(path) as img:
            img.load()
            if img.width == width and img.height == height:
                return ""
            resample = getattr(Image, "Resampling", Image).LANCZOS
            oriented = ImageOps.exif_transpose(img)
            converted = oriented.convert("RGBA") if oriented.mode in ("RGBA", "LA", "P") else oriented.convert("RGB")
            resized = ImageOps.fit(converted, (width, height), method=resample, centering=(0.5, 0.5))
            base, _ext = os.path.splitext(path)
            upscaled_path = f"{base}_upscaled_{width}x{height}.png"
            resized.save(upscaled_path, format="PNG")
            return upscaled_path
    except Exception as exc:
        label = "Gemini CLI" if provider_text == "gemini-cli" else "Codex GPT Image 2"
        print(f"{label} 图片尺寸后处理失败：{exc}")
        return ""

async def generate_codex_provider_image_via_gpt_image_2_skill(prompt, size, model, ref_paths=None):
    exe = gpt_image_2_skill_executable()
    if not exe:
        return None
    ref_paths = [str(path) for path in (ref_paths or []) if path and os.path.isfile(str(path))]
    auth_file = gpt_image_2_skill_auth_file()
    auth_data = gpt_image_2_skill_auth_json(auth_file)
    provider_args, tool_provider = gpt_image_2_skill_provider_args(auth_file)
    attempts = [(provider_args, tool_provider)]
    fallback_api_key = gpt_image_2_skill_api_key(auth_data)
    if tool_provider == "codex" and fallback_api_key:
        attempts.append((["--provider", "openai", "--api-key", fallback_api_key], "openai"))
    last_message = ""
    for attempt_index, (attempt_provider_args, attempt_provider) in enumerate(attempts):
        out_path = os.path.join(OUTPUT_OUTPUT_DIR, f"gpt_image_2_{uuid.uuid4().hex}.png")
        mode = "edit" if ref_paths else "generate"
        args = [
            exe,
            "--json",
        ]
        args.extend(attempt_provider_args)
        args.extend([
            "images",
            mode,
            "--prompt",
            gpt_image_2_skill_prompt_arg(prompt, size, attempt_provider),
            "--out",
            out_path,
            "--model",
            gpt_image_2_skill_model_arg(model, attempt_provider),
            "--format",
            "png",
            "--size",
            gpt_image_2_skill_size_arg(size, model, prompt, attempt_provider),
            "--quality",
            "high",
        ])
        for path in ref_paths:
            args.extend(["--ref-image", path])
        if ref_paths and attempt_provider == "openai":
            args.extend(["--input-fidelity", "high"])
        try:
            proc = await asyncio.create_subprocess_exec(
                *args,
                cwd=BASE_DIR,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=codex_timeout())
        except asyncio.TimeoutError as exc:
            try:
                proc.kill()
                await proc.wait()
            except Exception:
                pass
            raise HTTPException(status_code=504, detail="GPT Image 2 Skill 执行超时。可设置 CODEX_CLI_TIMEOUT 增大等待时间。") from exc
        except FileNotFoundError:
            return None
        out_text, err_text = codex_decode_output(stdout, stderr)
        if proc.returncode != 0:
            message = gpt_image_2_skill_failure_message(out_text, err_text, proc.returncode)
            last_message = f"{attempt_provider}: {message}"
            auth_failed = bool(re.search(r"\b401\b|unauthori[sz]ed|access[_ -]?token|api[_ -]?key", message, re.I))
            if attempt_provider == "codex" and attempt_index + 1 < len(attempts) and auth_failed:
                continue
            if auth_failed:
                return None
            raise HTTPException(status_code=502, detail=f"GPT Image 2 Skill 调用失败：{last_message[:1200]}")
        parsed, reported_paths = parse_gpt_image_2_skill_output(out_text, err_text)
        candidate_paths = []
        if os.path.isfile(out_path):
            candidate_paths.append(out_path)
        candidate_paths.extend([path for path in reported_paths if path and os.path.isfile(path)])
        urls = []
        for path in candidate_paths:
            processed_path = codex_postprocess_image_to_requested_size(path, size, attempt_provider)
            url = codex_output_url_from_path(processed_path or path)
            if url:
                urls.append(url)
        if not urls:
            status_text = (out_text or err_text or "")[:1200]
            raise HTTPException(status_code=502, detail=f"GPT Image 2 Skill 已返回，但没有在输出目录发现图片：{status_text}")
        return {"type": "url", "value": urls[0]}, {
            "images": urls,
            "text": out_text,
            "provider": "codex",
            "tool": "gpt-image-2-skill",
            "tool_provider": attempt_provider,
            "raw": parsed or {"stdout": out_text, "stderr": err_text},
        }
    raise HTTPException(status_code=502, detail=f"GPT Image 2 Skill 调用失败：{last_message[:1200]}")

async def codex_prepare_local_media(ref_url):
    text = str(ref_url or "").strip()
    if not text:
        return "", []
    if text.startswith(("/output/", "/assets/", "/api/project-library/")):
        path = output_file_from_url(text)
        if path:
            return path, []
        raise HTTPException(status_code=404, detail=f"OpenAI CLI 参考素材不存在：{text}")
    if text.startswith("file://"):
        path = urllib.parse.unquote(urllib.parse.urlparse(text).path)
        if os.name == "nt" and re.match(r"^/[A-Za-z]:/", path):
            path = path[1:]
        if os.path.isfile(path):
            return path, []
    if os.path.isfile(text):
        return text, []
    temp_paths = []
    suffix = ".png"
    if text.startswith("data:"):
        if ";base64," not in text:
            raise HTTPException(status_code=400, detail="OpenAI CLI 参考素材 data URL 缺少 base64 数据")
        header, encoded = text.split(";base64,", 1)
        mime = header.split(":", 1)[1].split(";", 1)[0] if ":" in header else ""
        suffix = mimetypes.guess_extension(mime) or suffix
        fd, path = tempfile.mkstemp(prefix="codex_ref_", suffix=suffix)
        with os.fdopen(fd, "wb") as f:
            f.write(base64.b64decode(encoded))
        temp_paths.append(path)
        return path, temp_paths
    if text.startswith(("http://", "https://")):
        async with httpx.AsyncClient(timeout=httpx.Timeout(connect=20.0, read=300.0, write=60.0, pool=20.0), follow_redirects=True) as client:
            response = await client.get(text)
            response.raise_for_status()
            clean_path = urllib.parse.urlparse(text).path
            suffix = os.path.splitext(clean_path)[1] or mimetypes.guess_extension(response.headers.get("content-type", "")) or suffix
            fd, path = tempfile.mkstemp(prefix="codex_ref_", suffix=suffix)
            with os.fdopen(fd, "wb") as f:
                f.write(response.content)
            temp_paths.append(path)
            return path, temp_paths
    raise HTTPException(status_code=400, detail=f"OpenAI CLI 无法读取参考素材：{text[:120]}")

async def codex_reference_paths(reference_images=None):
    paths = []
    temp_paths = []
    try:
        for ref in (reference_images or [])[:ONLINE_IMAGE_REFERENCE_MAX]:
            url = ref.get("url") if isinstance(ref, dict) else getattr(ref, "url", "")
            if not url:
                continue
            path, created = await codex_prepare_local_media(url)
            if path:
                paths.append(path)
            temp_paths.extend(created)
        return paths, temp_paths
    except Exception:
        for path in temp_paths:
            try:
                os.remove(path)
            except Exception:
                pass
        raise

def codex_models_payload(raw=None):
    all_models = [*CODEX_DEFAULT_IMAGE_MODELS, *CODEX_DEFAULT_CHAT_MODELS]
    return {
        "ok": True,
        "protocol": "codex",
        "status": 200,
        "message": "OpenAI Codex CLI 可用，模型列表来自本机 CLI 默认配置。",
        "model_count": len(all_models),
        "total": len(all_models),
        "image_models": CODEX_DEFAULT_IMAGE_MODELS,
        "chat_models": CODEX_DEFAULT_CHAT_MODELS,
        "video_models": [],
        "all": all_models,
        "raw": raw or {},
    }

async def generate_codex_provider_image(prompt, size, model, reference_images=None, provider=None):
    ref_paths, temp_paths = await codex_reference_paths(reference_images)
    try:
        skill_result = await generate_codex_provider_image_via_gpt_image_2_skill(prompt, size, model, ref_paths)
        if skill_result:
            return skill_result
        raise HTTPException(status_code=400, detail="未找到 GPT Image 2 helper，OpenAI CLI 生图已禁用 $imagegen 回退。请先安装 gpt-image-2-skill 后再生成图片。")
    finally:
        for path in temp_paths:
            try:
                os.remove(path)
            except Exception:
                pass

def codex_chat_prompt(payload, history_messages=None):
    parts = []
    system_prompt = str(getattr(payload, "system_prompt", "") or "").strip()
    if system_prompt:
        parts.append(f"系统要求：\n{system_prompt}")
    for item in (history_messages or [])[-MAX_HISTORY_MESSAGES:]:
        role = str(item.get("role") or "").strip()
        content = item.get("content")
        if role in {"user", "assistant"} and content:
            label = "用户" if role == "user" else "助手"
            parts.append(f"{label}：\n{content}")
    message = str(getattr(payload, "message", "") or "").strip()
    parts.append(f"用户：\n{message}")
    parts.append("请直接回答用户，输出纯文本，不要修改项目文件。")
    return "\n\n".join(part for part in parts if part).strip()

async def codex_chat_text(payload, history_messages=None):
    image_paths = []
    temp_paths = []
    try:
        image_values = []
        if hasattr(payload, "images"):
            image_values.extend([{"url": item} for item in (getattr(payload, "images", None) or []) if item])
        if hasattr(payload, "reference_images"):
            image_values.extend([ref.dict() for ref in (getattr(payload, "reference_images", None) or []) if getattr(ref, "url", "")])
        image_paths, temp_paths = await codex_reference_paths(image_values)
        raw = await run_codex_cli(
            codex_chat_prompt(payload, history_messages),
            model=getattr(payload, "model", "") or CODEX_DEFAULT_CHAT_MODELS[0],
            image_paths=image_paths,
            timeout=codex_timeout(),
            output_last_message=True,
        )
        text = str(raw.get("text") or "").strip()
        return text or "Codex CLI 返回了空回复。", raw
    finally:
        for path in temp_paths:
            try:
                os.remove(path)
            except Exception:
                pass

def gemini_cli_env_value(key):
    return os.getenv(key, "") or read_api_env_value(key)

def antigravity_cli_winget_candidates():
    patterns = [
        os.path.join(os.path.expanduser("~"), "AppData", "Local", "Microsoft", "WinGet", "Packages", "Google.AntigravityCLI_*", "agy.exe"),
        os.path.join(os.getenv("LOCALAPPDATA", ""), "Microsoft", "WinGet", "Packages", "Google.AntigravityCLI_*", "agy.exe"),
    ]
    candidates = []
    for pattern in patterns:
        if not pattern:
            continue
        candidates.extend(glob.glob(pattern))
    return sorted(dict.fromkeys(path for path in candidates if os.path.exists(path)), reverse=True)

def gemini_cli_executable():
    for key in ("ANTIGRAVITY_BIN", "AGY_BIN", "GEMINI_BIN"):
        configured = str(gemini_cli_env_value(key) or "").strip().strip('"')
        if configured:
            return configured
    for name in ("agy", "agy.exe"):
        found = shutil.which(name)
        if found:
            return found
    for candidate in antigravity_cli_winget_candidates():
        return candidate
    return shutil.which("gemini") or shutil.which("gemini.exe") or shutil.which("gemini.cmd") or ""

def is_antigravity_cli(exe):
    text = str(exe or "").lower()
    return os.path.basename(text).startswith("agy") or "antigravity" in text

def gemini_cli_display_name(exe=None):
    return "Antigravity CLI" if is_antigravity_cli(exe or gemini_cli_executable()) else "Gemini CLI"

def gemini_cli_timeout(default=GEMINI_CLI_DEFAULT_TIMEOUT):
    try:
        return max(30, min(3600, int(os.getenv("GEMINI_CLI_TIMEOUT", str(default)) or default)))
    except Exception:
        return default

def gemini_cli_image_timeout():
    raw = os.getenv("ANTIGRAVITY_IMAGE_TIMEOUT") or os.getenv("GEMINI_CLI_IMAGE_TIMEOUT") or "300"
    try:
        return max(60, min(1800, int(raw)))
    except Exception:
        return 300

def gemini_cli_model(model="", fallback=""):
    value = str(model or fallback or "").strip()
    return value or "auto"

def gemini_cli_text_from_raw(raw, fallback_text=""):
    if isinstance(raw, str):
        return raw.strip()
    if isinstance(raw, dict):
        for key in ("response", "text", "content", "message", "output"):
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
            return "\n".join(candidates).strip()
    if isinstance(raw, list):
        parts = [gemini_cli_text_from_raw(item) for item in raw]
        return "\n".join(part for part in parts if part).strip()
    return str(fallback_text or "").strip()

def gemini_cli_parse_stdout(out_text):
    text = str(out_text or "").strip()
    if not text:
        return {}, ""
    try:
        raw = json.loads(text)
        return raw, gemini_cli_text_from_raw(raw, text)
    except Exception:
        pass
    parsed = jimeng_extract_json(text)
    if isinstance(parsed, (dict, list)) and parsed != {"text": text}:
        return parsed, gemini_cli_text_from_raw(parsed, text)
    return {"text": text}, text

async def run_gemini_cli(prompt, model="", timeout=None, allow_tools=False):
    exe = gemini_cli_executable()
    if not exe:
        raise HTTPException(status_code=400, detail="未找到 Antigravity CLI。请先安装 Google Antigravity CLI，并完成 agy 登录。")
    timeout_seconds = timeout or gemini_cli_timeout()
    if is_antigravity_cli(exe):
        args = [exe, "--print-timeout", f"{int(timeout_seconds)}s"]
        selected = gemini_cli_model(model)
        if selected and selected != "auto":
            args.extend(["--model", selected])
        if allow_tools:
            args.append("--dangerously-skip-permissions")
        args.extend(["-p", str(prompt or "")])
    else:
        args = [
            exe,
            "--model",
            gemini_cli_model(model),
            "--output-format",
            "json",
            "--skip-trust",
        ]
        if allow_tools:
            args.extend(["--approval-mode", "yolo"])
        args.extend(["--prompt", str(prompt or "")])
    proc = None
    try:
        proc = await asyncio.create_subprocess_exec(
            *args,
            cwd=BASE_DIR,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout_seconds)
    except asyncio.TimeoutError as exc:
        if proc and proc.returncode is None:
            try:
                proc.kill()
                await proc.wait()
            except Exception:
                pass
        raise HTTPException(status_code=504, detail=f"{gemini_cli_display_name(exe)} 执行超时。可设置 GEMINI_CLI_TIMEOUT 增大等待时间。") from exc
    except FileNotFoundError as exc:
        raise HTTPException(status_code=400, detail=f"未找到 {gemini_cli_display_name(exe)}：{exe}") from exc
    out_text, err_text = codex_decode_output(stdout, stderr)
    raw, text = gemini_cli_parse_stdout(out_text)
    if proc.returncode != 0:
        message = err_text or out_text or f"exit={proc.returncode}"
        raise HTTPException(status_code=502, detail=f"{gemini_cli_display_name(exe)} 调用失败：{message[:1200]}")
    return {"text": text or out_text, "raw": raw, "_stdout": out_text, "_stderr": err_text}

def gemini_cli_models_payload(raw=None):
    all_models = [*GEMINI_CLI_DEFAULT_IMAGE_MODELS, *GEMINI_CLI_DEFAULT_CHAT_MODELS]
    all_models = model_list_from_values(all_models)
    return {
        "ok": True,
        "protocol": "gemini-cli",
        "status": 200,
        "message": "Antigravity CLI 可用，模型列表使用 auto 默认模型。",
        "model_count": len(all_models),
        "total": len(all_models),
        "image_models": GEMINI_CLI_DEFAULT_IMAGE_MODELS,
        "chat_models": GEMINI_CLI_DEFAULT_CHAT_MODELS,
        "video_models": [],
        "all": all_models,
        "raw": raw or {},
    }

def gemini_cli_reference_note(reference_images=None):
    refs = []
    temp_paths = []
    for ref in (reference_images or [])[:ONLINE_IMAGE_REFERENCE_MAX]:
        url = ref.get("url") if isinstance(ref, dict) else getattr(ref, "url", "")
        if not url:
            continue
        refs.append(url)
    return refs, temp_paths

async def gemini_cli_reference_paths(reference_images=None):
    return await codex_reference_paths(reference_images)

def gemini_cli_image_size_instruction(size="", model=""):
    size_text = str(size or "").strip()
    model_text = str(model or "").strip()
    match = re.match(r"^\s*(\d{2,5})\s*[xX*]\s*(\d{2,5})\s*$", size_text)
    if match:
        width, height = int(match.group(1)), int(match.group(2))
        if width > 0 and height > 0:
            orientation = "正方形" if width == height else ("横版" if width > height else "竖版")
            return (
                f"目标输出分辨率：{width}x{height} 像素（宽 x 高），画面方向：{orientation}。"
                f"最终保存到输出目录的图片文件实际像素必须是 {width}x{height}。"
                "如果生成器先得到较小图片，请在保存前放大或导出到目标尺寸，不要返回 1024px 小图。"
            )
    combined = f"{size_text} {model_text}".lower()
    if "4k" in combined:
        return "目标输出为 4K 高分辨率图片；最终保存文件需要达到当前画幅对应的 4K 像素尺寸，不要默认输出 1024px 小图。"
    if "2k" in combined:
        return "目标输出为 2K 高分辨率图片；最终保存文件需要达到当前画幅对应的 2K 像素尺寸，不要默认输出 1024px 小图。"
    return f"尺寸/比例参考：{size_text or 'auto'}。如果可以指定分辨率，请优先输出高分辨率图片。"

async def generate_gemini_cli_provider_image(prompt, size, model, reference_images=None, provider=None):
    ref_paths, temp_paths = await gemini_cli_reference_paths(reference_images)
    since = time.time()
    try:
        ref_text = ""
        if ref_paths:
            ref_text = "\n参考图片本地路径：\n" + "\n".join(ref_paths)
        size_context = f"{model or ''} {prompt or ''}"
        image_prompt = (
            f"你正在为萤火画布生成图片。\n"
            f"任务：{prompt}\n\n"
            f"{gemini_cli_image_size_instruction(size, size_context)}\n"
            f"{ref_text}\n\n"
            f"如果当前 Antigravity CLI/模型支持图片生成或图片编辑，请把最终图片保存到这个本地目录：{OUTPUT_OUTPUT_DIR}\n"
            "文件格式优先 png 或 jpg。只输出最终文件路径和一句简短说明；不要修改项目代码，不要创建额外文档。\n"
            "如果你无法真正创建图片文件，请在 60 秒内直接回复“无法生成图片文件”，不要只写计划，也不要持续尝试。"
        )
        raw = await run_gemini_cli(
            image_prompt,
            model=model or GEMINI_CLI_DEFAULT_IMAGE_MODELS[0],
            timeout=gemini_cli_image_timeout() if is_antigravity_cli(gemini_cli_executable()) else gemini_cli_timeout(),
            allow_tools=True,
        )
        files = codex_output_image_files(since)
        urls = []
        for path in files:
            processed_path = codex_postprocess_image_to_requested_size(path, size, "gemini-cli")
            url = codex_output_url_from_path(processed_path or path)
            if url and url not in urls:
                urls.append(url)
        if not urls:
            text = f"{raw.get('text') or raw.get('_stdout') or ''}\n{raw.get('_stderr') or ''}"
            pattern = r"([A-Za-z]:\\[^\r\n\"'<>]+\.(?:png|jpe?g|webp|gif)|/[^\r\n\"'<>]+\.(?:png|jpe?g|webp|gif))"
            for match in re.findall(pattern, text, flags=re.I):
                match_path = match.strip()
                processed_path = codex_postprocess_image_to_requested_size(match_path, size, "gemini-cli")
                url = codex_output_url_from_path(processed_path or match_path)
                if url and url not in urls:
                    urls.append(url)
        if not urls:
            status_text = (raw.get("text") or raw.get("_stdout") or raw.get("_stderr") or "")[:1200]
            raise HTTPException(status_code=502, detail=f"{gemini_cli_display_name()} 已返回，但没有在输出目录发现图片：{status_text}")
        return {"type": "url", "value": urls[0]}, {"images": urls, "text": raw.get("text"), "provider": "gemini-cli", "raw": raw.get("raw")}
    finally:
        for path in temp_paths:
            try:
                os.remove(path)
            except Exception:
                pass

def gemini_cli_chat_prompt(payload, history_messages=None):
    parts = []
    system_prompt = str(getattr(payload, "system_prompt", "") or "").strip()
    if system_prompt:
        parts.append(f"系统要求：\n{system_prompt}")
    for item in (history_messages or [])[-MAX_HISTORY_MESSAGES:]:
        role = str(item.get("role") or "").strip()
        content = item.get("content")
        if role in {"user", "assistant"} and content:
            label = "用户" if role == "user" else "助手"
            parts.append(f"{label}：\n{content}")
    message = str(getattr(payload, "message", "") or "").strip()
    parts.append(f"用户：\n{message}")
    image_values = []
    if hasattr(payload, "images"):
        image_values.extend([{"url": item} for item in (getattr(payload, "images", None) or []) if item])
    if hasattr(payload, "reference_images"):
        image_values.extend([ref.dict() for ref in (getattr(payload, "reference_images", None) or []) if getattr(ref, "url", "")])
    refs = []
    temp_paths = []
    return "\n\n".join(part for part in parts if part).strip(), image_values

async def gemini_cli_chat_text(payload, history_messages=None):
    temp_paths = []
    try:
        prompt, image_values = gemini_cli_chat_prompt(payload, history_messages)
        image_paths, temp_paths = await gemini_cli_reference_paths(image_values)
        if image_paths:
            prompt = f"{prompt}\n\n可参考的本地图片路径：\n" + "\n".join(image_paths)
        prompt = f"{prompt}\n\n请直接回答用户，输出纯文本，不要修改项目文件。"
        raw = await run_gemini_cli(
            prompt,
            model=getattr(payload, "model", "") or GEMINI_CLI_DEFAULT_CHAT_MODELS[0],
            timeout=gemini_cli_timeout(),
            allow_tools=False,
        )
        text = str(raw.get("text") or "").strip()
        return text or f"{gemini_cli_display_name()} 返回了空回复。", raw
    finally:
        for path in temp_paths:
            try:
                os.remove(path)
            except Exception:
                pass

def is_yuli_provider(provider):
    # 玉玉API（yuli.host）的视频接口走自有格式（/v1/video/create + /v1/video/query），
    # 与通用 OpenAI /v1/videos/generations 不同，需单独识别。
    base_url = str((provider or {}).get("base_url") or "").lower()
    return "yuli.host" in base_url

def is_lingjing_provider(provider):
    base_url = str((provider or {}).get("base_url") or "").lower()
    provider_id = str((provider or {}).get("id") or "").strip().lower()
    return provider_id == "lingjing" or "apistudio.vip" in base_url

def is_agnes_provider(provider, model=""):
    base_url = str((provider or {}).get("base_url") or "").lower()
    model_id = str(model or "").strip().lower()
    return "apihub.agnes-ai.com" in base_url or model_id.startswith("agnes-video-")

# ---- 数字人/真人认证：平台无关分发 ----
# 认证是一个跨平台功能。每个平台用不同的资产 API 实现，但对外是统一入口。
# 新增平台时：在 avatar_platform_for_provider 里加一条识别，并把平台键加进
# AVATAR_SUPPORTED_PLATFORMS，再在 register/avatar-status 端点里补一个分发分支即可。
AVATAR_SUPPORTED_PLATFORMS = {"apimart", "volcengine"}  # 已接入官方资产 API 的平台

def avatar_platform_for_provider(provider) -> str:
    if not provider:
        return ""
    if is_apimart_provider(provider):
        return "apimart"
    if is_volcengine_provider(provider):
        return "volcengine"
    return ""

def provider_supports_avatar(provider) -> bool:
    return avatar_platform_for_provider(provider) in AVATAR_SUPPORTED_PLATFORMS

def jimeng_env_value(key):
    return os.getenv(key, "") or read_api_env_value(key)

def jimeng_use_wsl():
    value = str(jimeng_env_value("JIMENG_USE_WSL") or "").strip().lower()
    return value in {"1", "true", "yes", "on", "wsl"}

def jimeng_cli_executable():
    if jimeng_use_wsl():
        return shutil.which("wsl.exe") or shutil.which("wsl") or "wsl.exe"
    configured = str(
        jimeng_env_value("JIMENG_BIN")
        or jimeng_env_value("DREAMINA_BIN")
        or ""
    ).strip()
    if configured:
        return configured
    return shutil.which("dreamina") or shutil.which("dreamina.exe") or shutil.which("dreamina.cmd") or ""

def decode_utf16_auto(raw: bytes) -> str:
    # WSL/Windows interop emits UTF-16 for null-heavy diagnostics, but the
    # endianness varies by source (console vs proxy vs subprocess), so a
    # hard-coded "utf-16le" silently byte-swaps UTF-16BE text into garbage
    # (e.g. "localhost" -> 氀漀挀愀氀栀漀猀琀). Decode both ways and keep
    # whichever produces more plain ASCII, since diagnostics are ASCII-heavy.
    try:
        le = raw.decode("utf-16le", errors="ignore")
    except Exception:
        le = ""
    try:
        be = raw.decode("utf-16be", errors="ignore")
    except Exception:
        be = ""
    def ascii_score(text):
        return sum(1 for ch in text if 0x20 <= ord(ch) <= 0x7e)
    return le if ascii_score(le) >= ascii_score(be) else be

def decode_wsl_output(data: bytes) -> str:
    data = data or b""
    if not data:
        return ""

    # WSL can mix UTF-16 diagnostics with UTF-8 command output in the same
    # stream. Decode per line so a WSL proxy warning does not corrupt CLI errors.
    if b"\x00" in data[:400]:
        lines = []
        for raw_line in data.splitlines():
            if not raw_line:
                lines.append("")
                continue
            sample = raw_line[:200]
            nul_ratio = sample.count(0) / max(1, len(sample))
            if nul_ratio > 0.2:
                try:
                    lines.append(decode_utf16_auto(raw_line))
                    continue
                except Exception:
                    pass
            lines.append(raw_line.decode("utf-8-sig", errors="ignore"))
        return "\n".join(lines)
    if b"\x00" in data[:200]:
        try:
            return decode_utf16_auto(data)
        except Exception:
            pass
    return data.decode("utf-8-sig", errors="ignore")

def jimeng_wsl_base_args(exe="wsl.exe"):
    configured = str(jimeng_env_value("JIMENG_WSL_DISTRO") or "").strip()
    names = []
    try:
        proc = subprocess.run(
            [exe, "-l", "-q"],
            cwd=BASE_DIR,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            timeout=5,
            check=False,
        )
        names = [
            line.replace("\x00", "").strip().lstrip("*").strip()
            for line in decode_wsl_output(proc.stdout).splitlines()
            if line.replace("\x00", "").strip()
        ]
    except Exception:
        names = []
    if configured and (not names or configured in names):
        return ["-d", configured]
    if configured and names:
        print(f"JIMENG_WSL_DISTRO={configured} 不存在，已回退自动选择。可用发行版：{names}")
    try:
        ubuntu = next((name for name in names if re.match(r"^Ubuntu($|-)", name)), "")
        if ubuntu:
            return ["-d", ubuntu]
    except Exception:
        pass
    return []

def jimeng_clean_wsl_stderr(text):
    lines = []
    skip_next_warning_context = False
    for line in str(text or "").splitlines():
        clean = line.replace("\x00", "").strip()
        low = clean.lower()
        is_proxy_warning = "localhost" in low and "wsl" in low and ("nat" in low or "proxy" in low or "代理" in clean)
        is_python_warning = "requestsdependencywarning" in low or (skip_next_warning_context and clean.startswith("warnings.warn("))
        skip_next_warning_context = "requestsdependencywarning" in low
        if clean and not is_proxy_warning and not is_python_warning:
            lines.append(clean)
    return "\n".join(lines).strip()

def windows_path_to_wsl(path):
    text = str(path or "").replace("\\", "/")
    match = re.match(r"^([A-Za-z]):/(.*)$", text)
    if match:
        return f"/mnt/{match.group(1).lower()}/{match.group(2)}"
    return text

def wsl_path_to_windows(path):
    text = str(path or "").strip()
    match = re.match(r"^/mnt/([A-Za-z])/(.*)$", text)
    if match:
        tail = match.group(2).replace("/", "\\")
        return f"{match.group(1).upper()}:\\{tail}"
    return text

def jimeng_cli_path_arg(path):
    return windows_path_to_wsl(path) if jimeng_use_wsl() else path

def jimeng_poll_seconds(default=JIMENG_DEFAULT_POLL_SECONDS):
    try:
        return max(1, min(3600, int(os.getenv("JIMENG_POLL_SECONDS", str(default)) or default)))
    except Exception:
        return default

def jimeng_extract_json(text):
    text = str(text or "").strip()
    if not text:
        return {}
    decoder = json.JSONDecoder()
    parsed = []
    for i, ch in enumerate(text):
        if ch not in "[{":
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
        for key in ("submit_id", "gen_status", "result_json", "images", "videos", "data", "total_credit"):
            if key in keys:
                weight += 10
        return weight
    return max(parsed, key=score)[1] if parsed else {"text": text}

async def run_jimeng_cli(args, timeout=120, raw_text=False):
    exe = jimeng_cli_executable()
    if not exe:
        raise HTTPException(status_code=400, detail="未找到 dreamina CLI。请先安装：curl -fsSL https://jimeng.jianying.com/cli | bash，并完成 dreamina login。")
    clean_args = [str(arg) for arg in args if str(arg) != ""]
    command = jimeng_command(clean_args, exe)
    try:
        proc = await asyncio.create_subprocess_exec(
            *command,
            cwd=BASE_DIR,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)
    except asyncio.TimeoutError as exc:
        raise HTTPException(status_code=504, detail=f"即梦 CLI 执行超时：{' '.join(command[:3])}") from exc
    except FileNotFoundError as exc:
        raise HTTPException(status_code=400, detail=f"未找到即梦 CLI：{exe}") from exc
    out_text, clean_err_text = jimeng_decode_cli_output(stdout, stderr)
    if proc.returncode != 0:
        message = clean_err_text or out_text or f"exit={proc.returncode}"
        raise HTTPException(status_code=502, detail=f"即梦 CLI 调用失败：{message[:1000]}")
    # 帮助等纯文本输出不应被 JSON 提取吞掉（如 [0.5, 8] 会被误判为结果）
    if raw_text:
        return {"_stdout": out_text, "_stderr": clean_err_text}
    raw = jimeng_extract_json(f"{out_text}\n{clean_err_text}".strip())
    if isinstance(raw, dict):
        raw.setdefault("_stdout", out_text)
        if clean_err_text:
            raw.setdefault("_stderr", clean_err_text)
    return raw

# 旧版 dreamina CLI 将 submit_id 用 16 位 hex，v1.4.2 起升级为 UUID，
# 与当前轮询逻辑不兼容。这里做尽力而为的版本探测，失败不阻断主流程。
JIMENG_MIN_CLI_VERSION = (1, 4, 2)

def jimeng_parse_version(text):
    match = re.search(r"(\d+)\.(\d+)\.(\d+)", str(text or ""))
    if not match:
        return None
    return tuple(int(part) for part in match.groups())

async def jimeng_cli_version():
    for flag in ("--version", "-V", "version"):
        try:
            raw = await run_jimeng_cli([flag], timeout=15)
        except HTTPException:
            continue
        text = raw if isinstance(raw, str) else (raw.get("_stdout") or raw.get("_stderr") or "" if isinstance(raw, dict) else "")
        version = jimeng_parse_version(text)
        if version:
            return version, str(text).strip()
    return None, ""

def jimeng_command(clean_args, exe=None):
    exe = exe or jimeng_cli_executable()
    if jimeng_use_wsl():
        shell_line = (
            ". ~/.profile >/dev/null 2>&1 || true; . ~/.bashrc >/dev/null 2>&1 || true; "
            "DREAMINA_BIN=$(command -v dreamina || find \"$HOME\" -maxdepth 4 -type f -name dreamina 2>/dev/null | head -n 1); "
            "if [ -z \"$DREAMINA_BIN\" ]; then echo 'dreamina CLI not found in WSL' >&2; exit 127; fi; "
            "\"$DREAMINA_BIN\" " + " ".join(shlex.quote(arg) for arg in clean_args)
        )
        return [exe, *jimeng_wsl_base_args(exe), "-e", "sh", "-lc", shell_line]
    return [exe, *clean_args]

def jimeng_decode_cli_output(stdout, stderr):
    out_text = (decode_wsl_output(stdout) if jimeng_use_wsl() else stdout.decode("utf-8", errors="replace")).strip()
    err_text = (decode_wsl_output(stderr) if jimeng_use_wsl() else stderr.decode("utf-8", errors="replace")).strip()
    clean_err_text = jimeng_clean_wsl_stderr(err_text) if jimeng_use_wsl() else err_text
    return out_text, clean_err_text

def jimeng_login_text():
    parts = []
    for key in ("stdout", "stderr"):
        value = str(JIMENG_LOGIN_SESSION.get(key) or "").strip()
        if value:
            parts.append(value)
    return "\n".join(parts).strip()

def jimeng_login_qr_from_text(text):
    text = str(text or "")
    candidates = []
    patterns = [
        r"(https?://[^\s\"'<>]+)",
        r"(dreamina://[^\s\"'<>]+)",
        r"(data:image/[^\s\"'<>]+)",
    ]
    for pattern in patterns:
        candidates.extend(re.findall(pattern, text))
    for value in candidates:
        if "login" in value.lower() or "qr" in value.lower() or value.startswith(("data:image", "dreamina://")):
            return value
    return candidates[0] if candidates else ""

async def jimeng_login_reader(proc):
    async def read_stream(stream, key):
        while True:
            chunk = await stream.readline()
            if not chunk:
                break
            text = (decode_wsl_output(chunk) if jimeng_use_wsl() else chunk.decode("utf-8", errors="replace"))
            if key == "stderr":
                text = jimeng_clean_wsl_stderr(text)
            if text:
                JIMENG_LOGIN_SESSION[key] = str(JIMENG_LOGIN_SESSION.get(key) or "") + text
    await asyncio.gather(read_stream(proc.stdout, "stdout"), read_stream(proc.stderr, "stderr"))

def jimeng_submit_id(raw):
    found = []
    def visit(value):
        if isinstance(value, dict):
            for key, item in value.items():
                if str(key).lower() in {"submit_id", "submitid", "task_id", "taskid"} and item:
                    found.append(str(item))
                else:
                    visit(item)
        elif isinstance(value, list):
            for item in value:
                visit(item)
    visit(raw)
    return found[0] if found else ""

class JimengPendingError(Exception):
    """即梦任务还在云端排队/生成（轮询超时但未失败）。submit_id 可用于后续续查。"""
    def __init__(self, submit_id, kind="image", queue_info=None, raw=None):
        self.submit_id = str(submit_id or "")
        self.kind = kind or "image"
        self.queue_info = queue_info if isinstance(queue_info, dict) else {}
        self.raw = raw
        super().__init__(f"jimeng pending submit_id={self.submit_id}")

def jimeng_queue_info(raw):
    """从即梦原始返回里就近取出 queue_info（含 queue_idx/queue_length/queue_status）。"""
    found = []
    def visit(value):
        if isinstance(value, dict):
            qi = value.get("queue_info")
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

def jimeng_pending_payload(exc: "JimengPendingError"):
    qi = exc.queue_info or {}
    idx = qi.get("queue_idx")
    length = qi.get("queue_length")
    if idx is not None and length is not None:
        msg = f"即梦云端排队中（第 {idx}/{length} 位），任务未丢失，可继续等待或手动查询。submit_id={exc.submit_id}"
    else:
        msg = f"即梦任务仍在生成中，任务未丢失。submit_id={exc.submit_id}"
    return {
        "jimeng_pending": True,
        "submit_id": exc.submit_id,
        "kind": exc.kind,
        "queue_info": qi,
        "message": msg,
    }

@app.exception_handler(JimengPendingError)
async def jimeng_pending_exception_handler(request: Request, exc: JimengPendingError):
    # 轮询超时但任务还在云端排队：返回 202 + submit_id，让前端保持「排队中」卡片并续查
    return JSONResponse(status_code=202, content=jimeng_pending_payload(exc))

def jimeng_failure_reason(raw):
    found = []
    def visit(value):
        if isinstance(value, dict):
            status = str(value.get("gen_status") or value.get("status") or "").strip().lower()
            reason = value.get("fail_reason") or value.get("failReason") or value.get("error") or value.get("message") or value.get("msg")
            if reason and (status in {"fail", "failed", "error"} or "fail" in str(reason).lower() or "invalid param" in str(reason).lower()):
                found.append(str(reason))
            for item in value.values():
                if isinstance(item, (dict, list)):
                    visit(item)
        elif isinstance(value, list):
            for item in value:
                visit(item)
    visit(raw)
    return found[0] if found else ""

def jimeng_collect_media_values(value, outputs):
    media_ext = re.compile(r"\.(png|jpe?g|webp|gif|bmp|mp4|webm|mov|m4v|avi|mkv)(\?|#|$)", re.I)
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return
        if text.startswith(("http://", "https://", "/output/", "/assets/", "file://")) or media_ext.search(text):
            outputs.append(text)
        return
    if isinstance(value, list):
        for item in value:
            jimeng_collect_media_values(item, outputs)
        return
    if isinstance(value, dict):
        for key in (
            "url", "urls", "image", "images", "image_url", "image_urls",
            "video", "videos", "video_url", "video_urls", "output", "outputs",
            "result", "results", "file", "files", "path", "paths",
            "download_url", "download_urls", "downloadUrl", "file_path", "filePath",
        ):
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

JIMENG_RATIO_CHOICES = [(21, 9), (16, 9), (3, 2), (4, 3), (1, 1), (3, 4), (2, 3), (9, 16)]
def jimeng_ratio_from_size(size, fallback="1:1"):
    width, height = parse_size_pair(size)
    if not width or not height:
        return fallback
    ratio = width / max(1, height)
    left, right = min(JIMENG_RATIO_CHOICES, key=lambda item: abs(ratio - item[0] / item[1]))
    return f"{left}:{right}"

# 官方 dreamina 支持的图片模型（来自 text2image/image2image -h）。
# image2image 不支持 3.0/3.1。
JIMENG_TEXT2IMAGE_MODELS = {"3.0", "3.1", "4.0", "4.1", "4.5", "4.6", "5.0"}
JIMENG_IMAGE2IMAGE_MODELS = {"4.0", "4.1", "4.5", "4.6", "5.0"}

def jimeng_normalize_image_model(model):
    match = re.search(r"(\d+\.\d+)", str(model or ""))
    return match.group(1) if match else ""

def jimeng_image_model_version(model, mode="text2image"):
    version = jimeng_normalize_image_model(model)
    allowed = JIMENG_IMAGE2IMAGE_MODELS if mode == "image2image" else JIMENG_TEXT2IMAGE_MODELS
    return version if version in allowed else ""

def jimeng_image_resolution(model, size, mode="text2image"):
    text = str(model or "").lower()
    if "4k" in text:
        desired = "4k"
    elif "1k" in text:
        desired = "1k"
    elif "2k" in text:
        desired = "2k"
    else:
        width, height = parse_size_pair(size)
        desired = "4k" if max(width, height) > 2048 else "2k"
    # 按官方规则收敛到模型允许的分辨率
    version = jimeng_normalize_image_model(model)
    if mode == "image2image":
        # image2image 只支持 2k/4k
        return "4k" if desired == "4k" else "2k"
    if version in ("3.0", "3.1"):
        # 3.0/3.1 只支持 1k/2k
        return "1k" if desired == "1k" else "2k"
    # 4.x/5.0 只支持 2k/4k
    return "4k" if desired == "4k" else "2k"

# 仅 VIP seedance 支持 1080P；其余模型最高 720P（官方无 480P 选项）
JIMENG_VIDEO_1080P_MODELS = {"seedance2.0_vip", "seedance2.0fast_vip"}

def jimeng_video_resolution(model, resolution):
    version = jimeng_video_model_version(model)
    requested = str(resolution or "").strip().upper()
    if requested not in {"480P", "720P", "1080P"}:
        text = str(model or "").lower()
        requested = "1080P" if "1080" in text else "720P"
    if requested == "1080P" and version in JIMENG_VIDEO_1080P_MODELS:
        return "1080P"
    return "720P"

# 各模型支持的时长区间（秒）：3.0 系列 3-10，3.5pro 4-12，seedance 4-15
def jimeng_video_duration_range(model):
    version = jimeng_video_model_version(model)
    if version in ("3.0", "3.0fast", "3.0pro"):
        return 3, 10
    if version == "3.5pro":
        return 4, 12
    return 4, 15

def jimeng_video_duration(duration, model=None):
    low, high = jimeng_video_duration_range(model)
    default = max(low, min(high, 5))
    try:
        text = str(duration).strip() if duration is not None else ""
        value = default if text == "" else int(text)
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
    value = str(model or "").strip()
    low = value.lower()
    aliases = {
        "seedance2.0fast_vip": "seedance2.0fast_vip",
        "seedance2.0_vip": "seedance2.0_vip",
        "seedance2.0fast": "seedance2.0fast",
        "seedance2.0": "seedance2.0",
        "3.0_fast": "3.0fast",
        "3.0fast": "3.0fast",
        "3.0_pro": "3.0pro",
        "3.0pro": "3.0pro",
        "3.5_pro": "3.5pro",
        "3.5pro": "3.5pro",
        "3.0": "3.0",
    }
    for key, mapped in aliases.items():
        if key in low:
            return mapped
    return ""

def jimeng_video_resolution_arg(model, resolution):
    return jimeng_video_resolution(model, resolution).lower()

def jimeng_video_ratio_arg(aspect_ratio):
    value = str(aspect_ratio or "").strip()
    allowed = {"1:1", "3:4", "16:9", "4:3", "9:16", "21:9"}
    if value in allowed:
        return value
    return ""

def jimeng_append_model_resolution_args(args, payload: CanvasVideoRequest, include_model=False):
    model_version = jimeng_video_model_version(payload.model)
    if include_model and model_version:
        args.append(f"--model_version={model_version}")
    if payload.resolution:
        args.append(f"--video_resolution={jimeng_video_resolution_arg(payload.model, payload.resolution)}")

def jimeng_video_ref_role(ref):
    role = getattr(ref, "role", "")
    if isinstance(ref, dict):
        role = ref.get("role", role)
    return str(role or "").lower()

def jimeng_video_ref_url(ref):
    url = getattr(ref, "url", "")
    if isinstance(ref, dict):
        url = ref.get("url", url)
    return str(url or "").strip()

def jimeng_local_output_url(path, kind="image"):
    path = os.path.abspath(str(path or ""))
    if not os.path.isfile(path):
        return ""
    output_root = os.path.abspath(OUTPUT_OUTPUT_DIR)
    try:
        if os.path.commonpath([output_root, path]) == output_root:
            return output_url_for(os.path.basename(path), "output")
    except Exception:
        pass
    ext = os.path.splitext(path)[1].lower()
    allowed = {".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp", ".mp4", ".webm", ".mov", ".m4v", ".avi", ".mkv"}
    if ext not in allowed:
        ct = content_type_for_path(path)
        ext = ".mp4" if ct.startswith("video/") else ".png"
    prefix = "jimeng_video_" if kind == "video" else "jimeng_"
    filename = f"{prefix}{uuid.uuid4().hex[:10]}{ext}"
    dest = output_path_for(filename, "output")
    shutil.copyfile(path, dest)
    return output_url_for(filename, "output")

async def jimeng_store_output_value(value, kind="image"):
    text = str(value or "").strip()
    if not text:
        return ""
    if text.startswith("/output/") or text.startswith("/assets/"):
        return text
    if text.startswith("file://"):
        text = urllib.parse.unquote(urllib.parse.urlparse(text).path)
        if os.name == "nt" and re.match(r"^/[A-Za-z]:/", text):
            text = text[1:]
    if jimeng_use_wsl() and text.startswith("/mnt/"):
        text = wsl_path_to_windows(text)
    if text.startswith(("http://", "https://")):
        if kind == "video":
            return await save_remote_video_to_output(text, prefix="jimeng_video_")
        return await save_ai_image_to_output({"type": "url", "value": text}, prefix="jimeng_")
    if os.path.isfile(text):
        return jimeng_local_output_url(text, kind)
    return ""

async def jimeng_query_result(submit_id, kind="image"):
    args = [
        "query_result",
        f"--submit_id={submit_id}",
        f"--download_dir={jimeng_cli_path_arg(OUTPUT_OUTPUT_DIR)}",
    ]
    return await run_jimeng_cli(args, timeout=min(300, jimeng_poll_seconds() + 60))

async def jimeng_store_outputs(raw, kind="image", allow_query=True):
    failure = jimeng_failure_reason(raw)
    if failure:
        raise HTTPException(status_code=502, detail=f"即梦生成失败：{failure}")
    values = jimeng_output_values(raw)
    urls = []
    for value in values:
        local_url = await jimeng_store_output_value(value, kind)
        if local_url and local_url not in urls:
            urls.append(local_url)
    if urls:
        return urls
    submit_id = jimeng_submit_id(raw)
    if submit_id and allow_query:
        queried = await jimeng_query_result(submit_id, kind)
        try:
            return await jimeng_store_outputs(queried, kind, allow_query=False)
        except HTTPException as exc:
            if getattr(exc, "status_code", None) == 502:
                status_text = json.dumps(queried, ensure_ascii=False)[:800] if isinstance(queried, (dict, list)) else str(queried)[:800]
                raise HTTPException(status_code=502, detail=f"即梦任务已返回但没有下载到媒体：{status_text}") from exc
            raise
    status_text = json.dumps(raw, ensure_ascii=False)[:800] if isinstance(raw, (dict, list)) else str(raw)[:800]
    if submit_id:
        raise JimengPendingError(submit_id, kind, jimeng_queue_info(raw), raw)
    raise HTTPException(status_code=502, detail=f"即梦 CLI 未返回可用媒体结果：{status_text}")

async def jimeng_prepare_local_media(ref_url, kind="image"):
    text = str(ref_url or "").strip()
    if not text:
        return "", []
    adapter_path = local_media_adapter_path(text, kind)
    if adapter_path:
        return adapter_path, []
    if text.startswith("/output/") or text.startswith("/assets/"):
        path = output_file_from_url(text)
        if path:
            return path, []
        raise HTTPException(status_code=404, detail=f"即梦参考素材不存在：{text}")
    if text.startswith("file://"):
        path = urllib.parse.unquote(urllib.parse.urlparse(text).path)
        if os.name == "nt" and re.match(r"^/[A-Za-z]:/", path):
            path = path[1:]
        if os.path.isfile(path):
            return path, []
    if os.path.isfile(text):
        return text, []
    suffix = ".mp4" if kind == "video" else (".mp3" if kind == "audio" else ".png")
    temp_paths = []
    if text.startswith("data:"):
        if ";base64," not in text:
            raise HTTPException(status_code=400, detail="即梦参考素材 data URL 缺少 base64 数据")
        header, encoded = text.split(";base64,", 1)
        mime = header.split(":", 1)[1].split(";", 1)[0] if ":" in header else ""
        suffix = mimetypes.guess_extension(mime) or suffix
        fd, path = tempfile.mkstemp(prefix="jimeng_ref_", suffix=suffix)
        with os.fdopen(fd, "wb") as f:
            f.write(base64.b64decode(encoded))
        temp_paths.append(path)
        return path, temp_paths
    if text.startswith(("http://", "https://")):
        async with httpx.AsyncClient(timeout=httpx.Timeout(connect=20.0, read=300.0, write=60.0, pool=20.0), follow_redirects=True) as client:
            response = await client.get(text)
            response.raise_for_status()
            clean_path = urllib.parse.urlparse(text).path
            suffix = os.path.splitext(clean_path)[1] or mimetypes.guess_extension(response.headers.get("content-type", "")) or suffix
            fd, path = tempfile.mkstemp(prefix="jimeng_ref_", suffix=suffix)
            with os.fdopen(fd, "wb") as f:
                f.write(response.content)
            temp_paths.append(path)
            return path, temp_paths
    raise HTTPException(status_code=400, detail=f"即梦 CLI 只支持本地文件参考素材，无法读取：{text[:120]}")

async def generate_jimeng_provider_image(prompt, size, model, reference_images=None, provider=None):
    refs = [ref for ref in (reference_images or []) if ref.get("url")]
    temp_paths = []
    try:
        args = []
        if refs:
            image_paths = []
            for ref in refs[:10]:
                image_path, created = await jimeng_prepare_local_media(ref.get("url"), "image")
                image_paths.append(jimeng_cli_path_arg(image_path))
                temp_paths.extend(created)
            model_version = jimeng_image_model_version(model, "image2image")
            args = [
                "image2image",
                f"--images={','.join(image_paths)}",
                f"--prompt={prompt}",
                f"--resolution_type={jimeng_image_resolution(model, size, 'image2image')}",
                f"--poll={jimeng_poll_seconds()}",
            ]
            if model_version:
                args.append(f"--model_version={model_version}")
        else:
            model_version = jimeng_image_model_version(model, "text2image")
            args = [
                "text2image",
                f"--prompt={prompt}",
                f"--ratio={jimeng_ratio_from_size(size)}",
                f"--resolution_type={jimeng_image_resolution(model, size, 'text2image')}",
                f"--poll={jimeng_poll_seconds()}",
            ]
            if model_version:
                args.append(f"--model_version={model_version}")
        raw = await run_jimeng_cli(args, timeout=jimeng_poll_seconds() + 120)
        urls = await jimeng_store_outputs(raw, "image")
        return {"type": "url", "value": urls[0]}, raw
    finally:
        for path in temp_paths:
            try:
                os.remove(path)
            except Exception:
                pass

async def generate_jimeng_video(payload: CanvasVideoRequest, provider):
    image_refs = [ref for ref in (payload.images or []) if jimeng_video_ref_url(ref)]
    video_refs = [url for url in (payload.videos or []) if str(url or "").strip()]
    audio_refs = [url for url in (payload.audios or []) if str(url or "").strip()][:3]
    duration = jimeng_video_duration(payload.duration, payload.model)
    temp_paths = []
    try:
        if payload.multimodal or video_refs or audio_refs:
            image_paths = []
            video_paths = []
            audio_paths = []
            for ref in image_refs[:9]:
                image_path, created = await jimeng_prepare_local_media(jimeng_video_ref_url(ref), "image")
                temp_paths.extend(created)
                image_paths.append(image_path)
            for ref_url in video_refs[:3]:
                video_path, created = await jimeng_prepare_local_media(ref_url, "video")
                temp_paths.extend(created)
                video_paths.append(video_path)
            for ref_url in audio_refs:
                audio_path, created = await jimeng_prepare_local_media(ref_url, "audio")
                temp_paths.extend(created)
                audio_paths.append(audio_path)
            args = [
                "multimodal2video",
                f"--prompt={payload.prompt}",
                f"--duration={duration}",
                f"--poll={jimeng_poll_seconds()}",
            ]
            ratio = jimeng_video_ratio_arg(payload.aspect_ratio)
            if ratio:
                args.append(f"--ratio={ratio}")
            jimeng_append_model_resolution_args(args, payload, include_model=True)
            for image_path in image_paths:
                args.append(f"--image={jimeng_cli_path_arg(image_path)}")
            for video_path in video_paths:
                args.append(f"--video={jimeng_cli_path_arg(video_path)}")
            for audio_path in audio_paths:
                args.append(f"--audio={jimeng_cli_path_arg(audio_path)}")
        elif len(image_refs) >= 2:
            first_frame = next((ref for ref in image_refs if jimeng_video_ref_role(ref) == "first_frame"), None)
            last_frame = next((ref for ref in image_refs if jimeng_video_ref_role(ref) == "last_frame"), None)
            if first_frame and last_frame:
                first_path, created = await jimeng_prepare_local_media(jimeng_video_ref_url(first_frame), "image")
                temp_paths.extend(created)
                last_path, created = await jimeng_prepare_local_media(jimeng_video_ref_url(last_frame), "image")
                temp_paths.extend(created)
                args = [
                    "frames2video",
                    f"--first={jimeng_cli_path_arg(first_path)}",
                    f"--last={jimeng_cli_path_arg(last_path)}",
                    f"--prompt={payload.prompt}",
                    f"--duration={duration}",
                    f"--poll={jimeng_poll_seconds()}",
                ]
                jimeng_append_model_resolution_args(args, payload, include_model=True)
            else:
                image_paths = []
                for ref in image_refs:
                    image_path, created = await jimeng_prepare_local_media(jimeng_video_ref_url(ref), "image")
                    temp_paths.extend(created)
                    image_paths.append(image_path)
                args = [
                    "multiframe2video",
                    f"--images={','.join(jimeng_cli_path_arg(path) for path in image_paths)}",
                    f"--prompt={payload.prompt}",
                    f"--duration={duration}",
                    f"--poll={jimeng_poll_seconds()}",
                ]
                jimeng_append_model_resolution_args(args, payload, include_model=True)
        elif image_refs:
            image_path, created = await jimeng_prepare_local_media(jimeng_video_ref_url(image_refs[0]), "image")
            temp_paths.extend(created)
            ratio = jimeng_video_ratio_arg(payload.aspect_ratio)
            if ratio:
                args = [
                    "multimodal2video",
                    f"--image={jimeng_cli_path_arg(image_path)}",
                    f"--prompt={payload.prompt}",
                    f"--duration={duration}",
                    f"--ratio={ratio}",
                    f"--poll={jimeng_poll_seconds()}",
                ]
                jimeng_append_model_resolution_args(args, payload, include_model=True)
            else:
                args = [
                    "image2video",
                    f"--image={jimeng_cli_path_arg(image_path)}",
                    f"--prompt={payload.prompt}",
                    f"--duration={duration}",
                    f"--poll={jimeng_poll_seconds()}",
                ]
                jimeng_append_model_resolution_args(args, payload, include_model=True)
        else:
            args = [
                "text2video",
                f"--prompt={payload.prompt}",
                f"--duration={duration}",
                f"--ratio={payload.aspect_ratio or '16:9'}",
                f"--video_resolution={jimeng_video_resolution(payload.model, payload.resolution)}",
                f"--poll={jimeng_poll_seconds()}",
            ]
            model_version = jimeng_video_model_version(payload.model)
            if model_version:
                args.append(f"--model_version={model_version}")
        raw = await run_jimeng_cli(args, timeout=jimeng_poll_seconds() + 180)
        urls = await jimeng_store_outputs(raw, "video")
        return {"videos": urls, "task_id": jimeng_submit_id(raw) or None, "raw": raw}
    finally:
        for path in temp_paths:
            try:
                os.remove(path)
            except Exception:
                pass

IMAGE_TASK_SUCCESS_STATUSES = {"SUCCESS", "SUCCESSFUL", "SUCCEED", "SUCCEEDED", "COMPLETED", "COMPLETE", "DONE", "FINISHED", "OK", "READY"}
IMAGE_TASK_FAILED_STATUSES = {"FAILURE", "FAILED", "FAIL", "ERROR", "ERRORED", "CANCELED", "CANCELLED", "TIMEOUT", "REJECTED", "EXPIRED"}

def image_task_url_for_provider(provider, task_id):
    base_url = (provider.get("base_url") if provider else AI_BASE_URL).rstrip("/")
    # 异步生图（openai-video-proxy）模式优先于 apimart 协议判断：
    # 提交走 /v1/videos，轮询必须走 /v1/videos/{id}；否则 protocol=apimart 的平台会错走 /v1/tasks/{id}
    if normalize_image_request_mode((provider or {}).get("image_request_mode")) == "openai-video-proxy":
        return f"{base_url}/videos/{task_id}" if base_url.endswith("/v1") else f"{base_url}/v1/videos/{task_id}"
    if is_apimart_provider(provider):
        return f"{base_url}/tasks/{task_id}" if base_url.endswith("/v1") else f"{base_url}/v1/tasks/{task_id}"
    return f"{base_url}/images/tasks/{task_id}" if base_url.endswith("/v1") else f"{base_url}/v1/images/tasks/{task_id}"

def image_task_data(payload):
    if isinstance(payload, dict) and isinstance(payload.get("data"), dict):
        return payload["data"]
    return payload if isinstance(payload, dict) else {}

def image_task_status(payload):
    task_data = image_task_data(payload)
    return str(task_data.get("status") or task_data.get("task_status") or "").upper()

def image_task_fail_reason(payload):
    task_data = image_task_data(payload)
    error = task_data.get("error") if isinstance(task_data.get("error"), dict) else {}
    return task_data.get("fail_reason") or task_data.get("message") or error.get("message") or (payload.get("message") if isinstance(payload, dict) else "") or "生图任务失败"

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
            print(f"[HTTPX-RETRY] {method} {url} transient error: {exc}; retry {attempt + 2}/{attempts}", flush=True)
            await asyncio.sleep(retry_delay * (attempt + 1))
    if last_exc:
        raise last_exc
    raise httpx.HTTPError(f"请求失败：{method} {url}")

async def fetch_image_task_payload(client, task_id, provider=None):
    task_url = image_task_url_for_provider(provider, task_id)
    response = await httpx_request_with_transient_retries(
        client,
        "GET",
        task_url,
        attempts=3,
        headers=api_headers(provider=provider),
    )
    response.raise_for_status()
    return response.json()

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
        last_payload = await fetch_image_task_payload(client, task_id, provider)
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
            raise HTTPException(status_code=502, detail=f"生图任务失败：{image_task_fail_reason(last_payload)}")
        await asyncio.sleep(min(interval, max(0.0, deadline - time.monotonic())))
    raw_text = json.dumps(last_payload, ensure_ascii=False)[:800] if last_payload else ""
    extra = f"，最后响应：{raw_text}" if raw_text else ""
    raise HTTPException(status_code=504, detail=f"生图任务超时（已等待 {int(timeout)} 秒），task_id={task_id}{extra}")

def output_storage(category="output"):
    return (OUTPUT_INPUT_DIR, "input") if category == "input" else (OUTPUT_OUTPUT_DIR, "output")

def output_url_for(filename, category="output"):
    folder, subdir = output_storage(category)
    rel = str(filename or "").replace("\\", "/").lstrip("/")
    try:
        asset_rel = os.path.relpath(os.path.join(folder, rel), ASSETS_DIR).replace("\\", "/")
        if not asset_rel.startswith("../") and asset_rel != "..":
            return f"/assets/{urllib.parse.quote(asset_rel, safe='/')}"
    except Exception:
        pass
    kind = "upload" if category == "input" else "generated"
    return f"/api/storage-files/{kind}/{urllib.parse.quote(rel, safe='/')}"

def output_path_for(filename, category="output"):
    folder, _ = output_storage(category)
    return os.path.join(folder, filename)

def storage_kind_dir(kind):
    kind = str(kind or "").strip().lower()
    if kind == "upload":
        return os.path.abspath(OUTPUT_INPUT_DIR)
    if kind == "generated":
        return os.path.abspath(OUTPUT_OUTPUT_DIR)
    if kind == "local":
        return os.path.abspath(LOCAL_UPLOAD_DIR)
    raise HTTPException(status_code=404, detail="未知存储目录")

def storage_file_path(kind, rel):
    root = storage_kind_dir(kind)
    rel_path = str(rel or "").replace("\\", "/").lstrip("/")
    rel_path = os.path.normpath(rel_path).replace("\\", "/")
    if not rel_path or rel_path == "." or rel_path == ".." or rel_path.startswith("../") or os.path.isabs(rel_path):
        raise HTTPException(status_code=400, detail="非法文件路径")
    path = os.path.abspath(os.path.join(root, rel_path))
    try:
        if os.path.commonpath([root, path]) != root:
            raise HTTPException(status_code=400, detail="非法文件路径")
    except ValueError:
        raise HTTPException(status_code=400, detail="非法文件路径")
    return path if os.path.exists(path) else None

PROJECT_MEDIA_ADAPTER_SCHEME = "project-media"
PROJECT_MEDIA_ADAPTER_PROJECT: ContextVar[Optional[str]] = ContextVar(
    "project_media_adapter_project",
    default=None,
)

def project_media_adapter_project_id(value: str) -> str:
    """Return the project encoded in an internal/legacy project-media reference."""
    text = str(value or "").strip()
    legacy_handle = project_media_adapter_reference_from_url(text)
    if legacy_handle:
        text = legacy_handle
    parsed = urllib.parse.urlsplit(text)
    if parsed.scheme != PROJECT_MEDIA_ADAPTER_SCHEME:
        return ""
    return urllib.parse.unquote(parsed.netloc or "")

def require_project_media_adapter_scope(value: str) -> None:
    """Allow project media only while the owning project task is executing."""
    project_id = project_media_adapter_project_id(value)
    if not project_id:
        return
    active_project_id = PROJECT_MEDIA_ADAPTER_PROJECT.get()
    if not active_project_id:
        raise ProjectStoreError("项目媒体适配句柄只能由内部项目任务使用")
    if not hmac.compare_digest(project_id, active_project_id):
        raise ProjectStoreError("项目媒体适配句柄不属于当前项目任务")

def require_project_media_values_scope(values: List[str]) -> None:
    """Enforce the internal project capability for any project-owned media input."""
    for value in values:
        if not project_media_adapter_project_id(value):
            continue
        active_project_id = PROJECT_MEDIA_ADAPTER_PROJECT.get()
        try:
            require_project_media_adapter_scope(value)
        except ProjectStoreError as exc:
            code = (
                "project_media_handle_project_mismatch"
                if active_project_id
                else "project_media_handle_requires_project_task"
            )
            raise HTTPException(status_code=400, detail={
                "code": code,
                "message": str(exc),
            }) from exc

def project_media_adapter_reference(project_id: str, asset_id: str) -> str:
    """Create an internal, non-HTTP handle for one project-owned media file."""
    project = str(project_id or "").strip()
    asset = str(asset_id or "").strip()
    if not project or not asset:
        raise ProjectStoreError("项目媒体适配句柄缺少项目或资源标识")
    return f"{PROJECT_MEDIA_ADAPTER_SCHEME}://{urllib.parse.quote(project, safe='')}/{urllib.parse.quote(asset, safe='')}"

def project_media_adapter_reference_from_url(value: str) -> str:
    """Convert a legacy project-media URL to the internal adapter handle."""
    clean = urllib.parse.unquote(str(value or "").split("?", 1)[0]).replace("\\", "/")
    match = re.fullmatch(r"/api/project-library/([^/]+)/media/([^/]+)", clean)
    if not match:
        return ""
    return project_media_adapter_reference(match.group(1), match.group(2))

def project_media_adapter_path(value: str, expected_kind: str = "") -> str:
    """Resolve an internal project-media handle to its verified local source path."""
    text = str(value or "").strip()
    require_project_media_adapter_scope(text)
    parsed = urllib.parse.urlsplit(text)
    if parsed.scheme != PROJECT_MEDIA_ADAPTER_SCHEME or not parsed.netloc or parsed.query or parsed.fragment:
        raise ProjectStoreError("项目媒体适配句柄无效")
    project_id = urllib.parse.unquote(parsed.netloc)
    asset_id = urllib.parse.unquote(parsed.path.lstrip("/"))
    if not project_id or not asset_id or "/" in asset_id or "\\" in asset_id:
        raise ProjectStoreError("项目媒体适配句柄无效")
    record = project_store_call("media_record", project_id, asset_id)
    actual_kind = str(record.get("kind") or "").strip().lower()
    expected = str(expected_kind or "").strip().lower()
    if expected and actual_kind != expected:
        raise ProjectStoreError(f"项目媒体类型不匹配：需要 {expected}，实际是 {actual_kind}")
    path = Path(str(record.get("path") or "")).resolve()
    if not path.is_file():
        raise ProjectNotFound("项目媒体不存在")
    return str(path)

def local_media_adapter_path(value: str, expected_kind: str = "") -> Optional[str]:
    """Resolve only app-local or internal project media handles for an adapter."""
    text = str(value or "").strip()
    legacy_project_handle = project_media_adapter_reference_from_url(text)
    if text.startswith("/api/project-library/") and not legacy_project_handle:
        raise ProjectStoreError("项目媒体 URL 无效")
    if legacy_project_handle:
        text = legacy_project_handle
    if text.startswith(f"{PROJECT_MEDIA_ADAPTER_SCHEME}://"):
        return project_media_adapter_path(text, expected_kind)
    if not text.startswith(("/output/", "/assets/", "/api/project-library/", "/api/storage-files/")):
        return None
    path = output_file_from_url(text)
    if not path:
        return None
    expected = str(expected_kind or "").strip().lower()
    if expected:
        actual_mime = content_type_for_path(path).split(";", 1)[0].lower()
        if not actual_mime.startswith(f"{expected}/"):
            raise ProjectStoreError(f"本地参考素材不是{expected}文件")
    return path

def output_file_from_url(url):
    if isinstance(url, dict):
        url = url.get("url", "")
    if not url:
        return None
    clean = urllib.parse.unquote(url.split("?", 1)[0]).replace("\\", "/")
    if clean.startswith(f"{PROJECT_MEDIA_ADAPTER_SCHEME}://"):
        try:
            return project_media_adapter_path(clean)
        except ProjectStoreError:
            return None
    project_handle = project_media_adapter_reference_from_url(clean)
    if project_handle:
        try:
            return project_media_adapter_path(project_handle)
        except (ProjectStoreError, KeyError, TypeError):
            return None
    if clean.startswith("/api/storage-files/"):
        rest = clean[len("/api/storage-files/"):].lstrip("/")
        kind, _, rel = rest.partition("/")
        return storage_file_path(kind, rel) if kind and rel else None
    if not (clean.startswith("/output/") or clean.startswith("/assets/")):
        return None
    if clean.startswith("/assets/"):
        root = ASSETS_DIR
        rel = clean[len("/assets/"):]
    else:
        rel = clean[len("/output/"):]
    rel = rel.lstrip("/")
    if not rel:
        return None
    roots = [ASSETS_DIR] if clean.startswith("/assets/") else [OUTPUT_OUTPUT_DIR, OUTPUT_DIR]
    for root in roots:
        path = os.path.abspath(os.path.join(root, rel))
        output_root = os.path.abspath(root)
        if os.path.commonpath([output_root, path]) == output_root and os.path.exists(path):
            return path
    return None

def collect_local_media_urls(value: Any) -> List[str]:
    """Collect local /assets and /output URLs from nested canvas log payloads."""
    urls = []
    if isinstance(value, str):
        text = value.strip()
        if text.startswith(("/assets/", "/output/", "/api/storage-files/")):
            urls.append(text)
    elif isinstance(value, dict):
        for item in value.values():
            urls.extend(collect_local_media_urls(item))
    elif isinstance(value, (list, tuple)):
        for item in value:
            urls.extend(collect_local_media_urls(item))
    return urls

def local_media_path_from_url(url: str) -> Optional[str]:
    """Resolve a local media URL using the same one-to-one mapping as app mounts."""
    if not url:
        return None
    clean = urllib.parse.unquote(str(url).split("?", 1)[0]).replace("\\", "/")
    if clean.startswith("/api/storage-files/"):
        rest = clean[len("/api/storage-files/"):].lstrip("/")
        kind, _, rel = rest.partition("/")
        return storage_file_path(kind, rel) if kind and rel else None
    if clean.startswith("/assets/"):
        root = ASSETS_DIR
        rel = clean[len("/assets/"):]
    elif clean.startswith("/output/"):
        root = OUTPUT_DIR
        rel = clean[len("/output/"):]
    else:
        return None
    rel = rel.lstrip("/")
    if not rel:
        return None
    root = os.path.abspath(root)
    path = os.path.abspath(os.path.join(root, rel))
    try:
        return path if os.path.commonpath([root, path]) == root and os.path.exists(path) else None
    except ValueError:
        return None

def generated_media_path_from_url(url: str) -> Optional[str]:
    """Resolve a URL only when it points inside a generated-output directory."""
    try:
        path = local_media_path_from_url(url)
    except (HTTPException, OSError, ValueError):
        return None
    if not path or not os.path.isfile(path):
        return None
    path = os.path.realpath(path)
    for root in (OUTPUT_OUTPUT_DIR, OUTPUT_DIR):
        root = os.path.realpath(root)
        try:
            if os.path.commonpath([root, path]) == root:
                return path
        except ValueError:
            continue
    return None

def json_references_media_path(value: Any, target_path: str) -> bool:
    """Return True when a JSON-compatible value references the local media file."""
    target = os.path.normcase(os.path.realpath(target_path))
    if isinstance(value, str):
        try:
            resolved = local_media_path_from_url(value.strip())
        except (HTTPException, OSError, ValueError):
            return False
        return bool(resolved and os.path.normcase(os.path.realpath(resolved)) == target)
    if isinstance(value, dict):
        return any(json_references_media_path(item, target) for item in value.values())
    if isinstance(value, (list, tuple)):
        return any(json_references_media_path(item, target) for item in value)
    return False

def persisted_json_references_media_path(target_path: str) -> bool:
    """Scan persisted user documents before deleting generated media.

    Canvas logs are only one possible owner. Nodes, other canvases, ordinary
    generation history, conversations, and asset metadata all count as live
    references and keep the file on disk.
    """
    # Generation history is an index of outputs, not an owner. When an output
    # is deleted we prune its history card separately so this index cannot pin
    # every generated file forever.
    candidates = [ASSET_LIBRARY_PATH]
    for root in (CANVAS_DIR, CONVERSATION_DIR):
        if os.path.isdir(root):
            for current, _, files in os.walk(root):
                candidates.extend(os.path.join(current, name) for name in files if name.lower().endswith(".json"))
    seen = set()
    for path in candidates:
        path = os.path.abspath(path)
        if path in seen or not os.path.isfile(path):
            continue
        seen.add(path)
        try:
            with open(path, "r", encoding="utf-8-sig") as handle:
                value = json.load(handle)
        except (OSError, UnicodeError, json.JSONDecodeError):
            # A file may be in the middle of an unrelated write. Safety wins:
            # keep the media instead of treating an unreadable owner as absent.
            return True
        if json_references_media_path(value, target_path):
            return True
    return False

def prune_generation_history_for_media(paths: List[str]) -> int:
    """Remove history cards that would otherwise point at deleted media."""
    if not paths or not os.path.isfile(HISTORY_FILE):
        return 0
    try:
        with HISTORY_LOCK:
            with open(HISTORY_FILE, "r", encoding="utf-8-sig") as handle:
                history = json.load(handle)
            if not isinstance(history, list):
                return 0
            kept = [
                record for record in history
                if not any(json_references_media_path(record, path) for path in paths)
            ]
            removed = len(history) - len(kept)
            if removed:
                with open(HISTORY_FILE, "w", encoding="utf-8") as handle:
                    json.dump(kept, handle, ensure_ascii=False, indent=4)
            return removed
    except (OSError, UnicodeError, json.JSONDecodeError):
        return 0

def smart_owned_result_items(images: List[Any], paths: List[str]) -> List[Any]:
    """Return generated results, including legacy results without the marker."""
    return [
        item for item in images
        if isinstance(item, dict)
        and item.get("loopInputPreview") is not True
        and (
            item.get("generatedResult") is True
            or any(json_references_media_path(item, path) for path in paths)
        )
    ]

def expand_canvas_generated_media_paths(canvas: Dict[str, Any], paths: List[str]) -> List[str]:
    """Include every generated result owned by a result node touched by the log."""
    expanded = list(paths)
    for node in list(canvas.get("nodes") or []):
        node_type = str(node.get("type") or "").strip().lower()
        images = list(node.get("images") or [])
        if node_type == "output":
            owned_items = images
        elif node_type == "smart-image":
            owned_items = smart_owned_result_items(images, paths)
        else:
            owned_items = []
        if not any(json_references_media_path(item, path) for item in owned_items for path in paths):
            continue
        for item in owned_items:
            for url in collect_local_media_urls(item):
                candidate = generated_media_path_from_url(url)
                if candidate and candidate not in expanded:
                    expanded.append(candidate)
    return expanded

def reset_canvas_result_nodes_for_media(canvas: Dict[str, Any], paths: List[str]) -> List[str]:
    """Clear generated media while preserving prompts, references, settings and links."""
    reset_ids = []
    updated_nodes = []
    for node in list(canvas.get("nodes") or []):
        node = dict(node)
        node_type = str(node.get("type") or "").strip().lower()
        changed = False
        if isinstance(node.get("generatedOutputs"), list):
            outputs = list(node.get("generatedOutputs") or [])
            kept_outputs = [
                item for item in outputs
                if not any(json_references_media_path(item, path) for path in paths)
            ]
            if len(kept_outputs) != len(outputs):
                node["generatedOutputs"] = kept_outputs
                changed = True
        if node_type in {"smart-image", "output"} and isinstance(node.get("images"), list):
            images = list(node.get("images") or [])
            if node_type == "smart-image":
                owned_items = smart_owned_result_items(images, paths)
            else:
                owned_items = images
            owns_target = any(
                json_references_media_path(item, path) for item in owned_items for path in paths
            )
            kept_images = [
                item for item in images
                if not (
                    owns_target
                    and item in owned_items
                    and any(json_references_media_path(item, path) for path in paths)
                )
            ]
            if len(kept_images) != len(images):
                node["images"] = kept_images
                changed = True
                if node_type == "output":
                    node["_pending"] = []
                    node["imageComparisons"] = {}
                if node_type == "smart-image":
                    node["pending"] = 0
                    node["running"] = False
                    node["queued"] = False
                    for key in (
                        "jimengPending", "pendingTasks", "runStartedAt", "runFinishedAt",
                        "runElapsedMs", "runTimerHidden", "outputKind", "w", "h",
                    ):
                        node.pop(key, None)
        elif node_type == "image" and any(json_references_media_path(node.get("url"), path) for path in paths):
            node["url"] = ""
            node["mediaKind"] = "image"
            node["name"] = "空白图片"
            changed = True
        if changed and node.get("id"):
            reset_ids.append(str(node["id"]))
        updated_nodes.append(node)

    if reset_ids:
        canvas["nodes"] = updated_nodes
    return reset_ids

def delete_media_preview_cache(path: str) -> int:
    """Delete derived previews for a source file before the source disappears."""
    try:
        stat = os.stat(path)
    except OSError:
        return 0
    source = os.path.realpath(path)
    removed = 0
    for width in range(0, 4097):
        keys = [hashlib.sha1(f"{source}|{stat.st_mtime_ns}|{stat.st_size}|{width}|jpg".encode("utf-8", "ignore")).hexdigest() + ".jpg"]
        if 64 <= width <= 2048:
            preview_key = hashlib.sha1(f"{source}|{stat.st_mtime_ns}|{stat.st_size}|{width}".encode("utf-8", "ignore")).hexdigest()
            keys.extend((preview_key + ".webp", preview_key + ".png"))
        for name in keys:
            cache_path = os.path.join(MEDIA_PREVIEW_DIR, name)
            try:
                if os.path.isfile(cache_path):
                    os.remove(cache_path)
                    removed += 1
            except OSError:
                pass
    return removed

def image_has_alpha(img: Image.Image) -> bool:
    if img.mode in ("RGBA", "LA"):
        return True
    if img.mode == "P":
        return "transparency" in img.info
    return False

STORAGE_IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp", ".avif"}

def storage_file_item(kind, root, path):
    rel = os.path.relpath(path, root).replace("\\", "/")
    try:
        stat = os.stat(path)
    except OSError:
        return None
    item = {
        "id": f"{kind}:{rel}",
        "kind": kind,
        "rel": rel,
        "name": os.path.basename(path),
        "folder": os.path.dirname(rel).replace("\\", "/"),
        "url": f"/api/storage-files/{kind}/{urllib.parse.quote(rel, safe='/')}",
        "size": stat.st_size,
        "created_at": stat.st_mtime,
    }
    try:
        with Image.open(path) as img:
            item["width"], item["height"] = img.size
    except Exception:
        pass
    return item

@app.get("/api/storage-settings")
async def get_storage_settings():
    settings = load_storage_settings()
    return {
        "dirs": settings["dirs"],
        "defaults": {key: os.path.abspath(value) for key, value in DEFAULT_STORAGE_DIRS.items()},
    }

@app.patch("/api/storage-settings")
async def update_storage_settings(payload: Dict[str, str]):
    return save_storage_settings(payload or {})

@app.get("/api/storage-files")
async def list_storage_files(kind: str = "generated", offset: int = 0, limit: int = 80):
    root = storage_kind_dir(kind)
    os.makedirs(root, exist_ok=True)
    offset = max(0, int(offset or 0))
    limit = max(20, min(200, int(limit or 80)))
    items = []
    for current, dirs, files in os.walk(root):
        dirs[:] = sorted([d for d in dirs if not d.startswith(".") and not d.startswith("._")], key=str.lower)
        for name in sorted(files, key=str.lower):
            if name.startswith(".") or name.startswith("._"):
                continue
            if os.path.splitext(name)[1].lower() not in STORAGE_IMAGE_EXTS:
                continue
            item = storage_file_item(kind, root, os.path.join(current, name))
            if item:
                items.append(item)
    items.sort(key=lambda item: item.get("created_at") or 0, reverse=True)
    total = len(items)
    page_items = items[offset:offset + limit]
    return {
        "kind": kind,
        "root": root,
        "items": page_items,
        "total": total,
        "offset": offset,
        "limit": limit,
        "has_more": offset + len(page_items) < total,
    }

@app.get("/api/storage-files/{kind}/{rel_path:path}")
async def get_storage_file(kind: str, rel_path: str):
    path = storage_file_path(kind, rel_path)
    if not path or not os.path.isfile(path):
        raise HTTPException(status_code=404, detail="文件不存在")
    return FileResponse(path, media_type=content_type_for_path(path))

@app.post("/api/storage-files/delete")
async def delete_storage_files(payload: Dict[str, Any]):
    kind = str((payload or {}).get("kind") or "").strip()
    rels = [str(item or "").strip() for item in ((payload or {}).get("items") or []) if str(item or "").strip()]
    if not rels:
        raise HTTPException(status_code=400, detail="请选择要删除的文件")
    removed = 0
    for rel in rels:
        path = storage_file_path(kind, rel)
        if not path or not os.path.isfile(path):
            continue
        try:
            os.remove(path)
            removed += 1
        except OSError:
            pass
    return {"removed": removed}

@app.get("/api/asset-classification-prompt")
async def get_asset_classification_prompt():
    current = load_asset_classification_prompt()
    return {
        "prompt": current,
        "default_prompt": ASSET_CLASSIFICATION_PROMPT,
        "custom": current.strip() != ASSET_CLASSIFICATION_PROMPT.strip(),
    }

@app.patch("/api/asset-classification-prompt")
async def update_asset_classification_prompt(payload: Dict[str, str]):
    prompt = save_asset_classification_prompt((payload or {}).get("prompt") or "")
    return {"prompt": prompt, "custom": True}

def media_preview_cache_paths(path: str, width: int):
    stat = os.stat(path)
    key = hashlib.sha1(
        f"{os.path.realpath(path)}|{stat.st_mtime_ns}|{stat.st_size}|{width}".encode("utf-8", "ignore")
    ).hexdigest()
    return (
        os.path.join(MEDIA_PREVIEW_DIR, f"{key}.webp"),
        os.path.join(MEDIA_PREVIEW_DIR, f"{key}.png"),
    )

def is_video_preview_file(path: str) -> bool:
    return os.path.splitext(str(path or "").split("?", 1)[0])[1].lower() in {".mp4", ".webm", ".mov", ".m4v", ".avi", ".mkv"}

def generate_video_preview_image(path: str, width: int) -> Image.Image:
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        raise RuntimeError("未找到 ffmpeg，无法生成视频预览图")
    fd, frame_path = tempfile.mkstemp(prefix="media_preview_frame_", suffix=".jpg")
    os.close(fd)
    try:
        cmd = [
            ffmpeg, "-hide_banner", "-loglevel", "error", "-y",
            "-ss", "0.5",
            "-i", path,
            "-frames:v", "1",
            "-vf", f"scale='min({width},iw)':-2",
            frame_path,
        ]
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
        if proc.returncode != 0 or not os.path.exists(frame_path) or os.path.getsize(frame_path) <= 0:
            raise RuntimeError((proc.stderr or "ffmpeg 未能抽取视频首帧").strip()[:300])
        with Image.open(frame_path) as frame:
            img = ImageOps.exif_transpose(frame).copy()
            img.thumbnail((width, width), Image.LANCZOS)
            return img.convert("RGB")
    finally:
        try:
            os.remove(frame_path)
        except OSError:
            pass

@app.get("/api/media-preview")
async def media_preview(url: str, w: int = 512):
    path = output_file_from_url(url)
    if not path or not os.path.isfile(path):
        raise HTTPException(status_code=404, detail="媒体文件不存在")

    width = max(64, min(2048, int(w or 512)))
    webp_path, png_path = media_preview_cache_paths(path, width)

    if os.path.exists(webp_path):
        return FileResponse(webp_path, media_type="image/webp")
    if os.path.exists(png_path):
        return FileResponse(png_path, media_type="image/png")

    def _build_preview():
        # 同步 PIL 处理 + 落盘，放到线程里执行，避免阻塞事件循环（几十张首次生成会卡死整个 loop → 缩略图全空白）
        os.makedirs(MEDIA_PREVIEW_DIR, exist_ok=True)
        if is_video_preview_file(path):
            img = generate_video_preview_image(path, width)
        else:
            with Image.open(path) as source:
                img = ImageOps.exif_transpose(source)
                img.thumbnail((width, width), Image.LANCZOS)
                img = img.convert("RGBA" if image_has_alpha(img) else "RGB")
        try:
            img.save(webp_path, format="WEBP", quality=80, method=1)   # method=1 生成更快（缩略图不追求极致压缩）
            return webp_path, "image/webp"
        except Exception:
            img.save(png_path, format="PNG")
            return png_path, "image/png"

    try:
        out_path, media_type = await asyncio.to_thread(_build_preview)
        return FileResponse(out_path, media_type=media_type)
    except Exception as exc:
        raise HTTPException(status_code=415, detail=f"无法生成预览图：{exc}") from exc

@app.get("/api/image-jpeg")
async def image_jpeg(url: str, w: int = 0):
    """把任意图片转成 JPEG 返回（带缓存）。给不支持 WebP 等格式显示的客户端（PS UXP）用。
    w>0 时同时缩放到该宽度（缩略图）；w=0 输出原尺寸。"""
    path = output_file_from_url(url)
    if not path or not os.path.isfile(path):
        raise HTTPException(status_code=404, detail="媒体文件不存在")
    width = max(0, min(4096, int(w or 0)))
    stat = os.stat(path)
    key = hashlib.sha1(f"{os.path.realpath(path)}|{stat.st_mtime_ns}|{stat.st_size}|{width}|jpg".encode("utf-8", "ignore")).hexdigest()
    cache_path = os.path.join(MEDIA_PREVIEW_DIR, f"{key}.jpg")
    if os.path.exists(cache_path):
        return FileResponse(cache_path, media_type="image/jpeg")

    def _build():
        os.makedirs(MEDIA_PREVIEW_DIR, exist_ok=True)
        with Image.open(path) as src:
            img = ImageOps.exif_transpose(src)
            if width:
                img.thumbnail((width, width), Image.LANCZOS)
            if img.mode in ("RGBA", "LA", "P"):
                bg = Image.new("RGB", img.size, (255, 255, 255))
                rgba = img.convert("RGBA")
                bg.paste(rgba, mask=rgba.split()[-1])
                img = bg
            else:
                img = img.convert("RGB")
            img.save(cache_path, format="JPEG", quality=86)
        return cache_path

    try:
        out_path = await asyncio.to_thread(_build)
        return FileResponse(out_path, media_type="image/jpeg")
    except Exception as exc:
        raise HTTPException(status_code=415, detail=f"无法转换图片：{exc}") from exc

def local_media_file_by_basename(name: str):
    safe = os.path.basename(urllib.parse.unquote(str(name or "")))
    if not safe:
        return None
    roots = [
        OUTPUT_OUTPUT_DIR,
        OUTPUT_INPUT_DIR,
        os.path.join(ASSETS_DIR, "output"),
        os.path.join(ASSETS_DIR, "input"),
        os.path.join(ASSETS_DIR, "library"),
    ]
    for root in roots:
        path = os.path.abspath(os.path.join(root, safe))
        root_abs = os.path.abspath(root)
        if os.path.commonpath([root_abs, path]) == root_abs and os.path.isfile(path):
            return path
    return None

def filename_from_media_url(url: str, fallback: str = "download.bin") -> str:
    path = urllib.parse.urlsplit(str(url or "")).path
    name = os.path.basename(urllib.parse.unquote(path))
    return sanitize_export_filename(name or fallback, fallback)

def fetch_remote_media_bytes(url: str, timeout: float = 30.0, max_bytes: int = 200 * 1024 * 1024):
    text = rewrite_runninghub_file_url(str(url or "").strip())
    parsed = urllib.parse.urlparse(text)
    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        return None
    with requests.get(text, stream=True, timeout=timeout, headers={"User-Agent": "Firefly-Canvas/1.0"}) as response:
        response.raise_for_status()
        content_type = response.headers.get("content-type") or "application/octet-stream"
        chunks = []
        total = 0
        for chunk in response.iter_content(chunk_size=1024 * 256):
            if not chunk:
                continue
            total += len(chunk)
            if total > max_bytes:
                raise HTTPException(status_code=413, detail="文件太大，无法下载")
            chunks.append(chunk)
        return b"".join(chunks), content_type

def origin_from_url(value):
    parsed = urllib.parse.urlparse(str(value or ""))
    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        return ""
    return f"{parsed.scheme}://{parsed.netloc}".lower()

def ensure_same_origin_request(request: Request):
    host = str(request.headers.get("host") or "").lower()
    try:
        hostname = (urllib.parse.urlsplit(f"//{host}").hostname or "").lower()
    except ValueError:
        hostname = ""
    if hostname not in {"localhost", "127.0.0.1", "::1", "0.0.0.0", "::"}:
        raise HTTPException(status_code=403, detail="只允许从当前应用页面操作本机数据")
    expected = f"{request.url.scheme}://{host}".lower()
    origin = origin_from_url(request.headers.get("origin", ""))
    referer = origin_from_url(request.headers.get("referer", ""))
    actual = origin or referer
    if actual != expected:
        raise HTTPException(status_code=403, detail="只允许从当前应用页面操作本机数据")

@app.middleware("http")
async def protect_project_library_origin(request: Request, call_next):
    path = request.url.path.rstrip("/")
    if path == "/api/projects" or path == "/api/project-library" or path.startswith("/api/project-library/"):
        # The UXP connector has no browser Origin/Referer, but its protocol
        # handlers enforce the stricter loopback-only boundary themselves.
        path_parts = [part for part in path.split("/") if part]
        is_photoshop_round_trip = (
            len(path_parts) >= 4
            and path_parts[0] == "api"
            and path_parts[1] == "project-library"
            and (
                (len(path_parts) >= 5 and path_parts[3] == "photoshop" and path_parts[4] == "round-trips")
                or path_parts[3] == "photoshop-round-trips"
            )
        )
        if not is_photoshop_round_trip:
            try:
                ensure_same_origin_request(request)
            except HTTPException as exc:
                return JSONResponse(status_code=exc.status_code, content={"detail": exc.detail})
    if path == "/api/project-library/import" and request.method == "POST":
        try:
            content_length = int(request.headers.get("content-length") or 0)
        except ValueError:
            return JSONResponse(status_code=400, content={"detail": "请求体大小无效"})
        if content_length > FLY_MAX_REQUEST_BYTES:
            return JSONResponse(status_code=413, content={"detail": "Fly 项目包超过 20GB 安全限制"})
    return await call_next(request)

def normalize_local_image_path(value):
    text = str(value or "").strip().strip('"').strip("'")
    if not text:
        raise HTTPException(status_code=400, detail="本地图片路径为空")
    if text.lower().startswith("file:"):
        parsed = urllib.parse.urlparse(text)
        if parsed.scheme.lower() != "file":
            raise HTTPException(status_code=400, detail="只支持本地图片路径")
        if parsed.netloc and re.match(r"^[a-zA-Z]:$", parsed.netloc) and os.name == "nt":
            path = f"{parsed.netloc}{urllib.request.url2pathname(parsed.path or '')}"
        elif parsed.netloc and parsed.netloc.lower() not in ("localhost",):
            raise HTTPException(status_code=400, detail="只支持本机图片路径")
        else:
            path = urllib.request.url2pathname(parsed.path or "")
    else:
        path = text
    path = path.strip().strip('"').strip("'")
    if re.match(r"^/[a-zA-Z]:[\\/]", path):
        path = path[1:]
    if re.match(r"^[a-zA-Z]:[\\/]", path):
        return os.path.abspath(path)
    if path.startswith("/") and os.name != "nt":
        return os.path.abspath(path)
    raise HTTPException(status_code=400, detail="只支持本机绝对图片路径")

def import_local_image_file(path):
    ext = os.path.splitext(path)[1].lower()
    if ext not in LOCAL_IMAGE_IMPORT_EXTS:
        raise HTTPException(status_code=400, detail="仅支持 PNG、JPG、JPEG、WEBP、GIF 图片")
    if not os.path.isfile(path):
        raise HTTPException(status_code=404, detail="本地图片不存在或无法读取")
    try:
        size = os.path.getsize(path)
    except OSError:
        raise HTTPException(status_code=404, detail="本地图片不存在或无法读取")
    if size <= 0:
        raise HTTPException(status_code=400, detail="本地图片为空")
    if size > LOCAL_IMAGE_IMPORT_MAX_BYTES:
        raise HTTPException(status_code=413, detail="本地图片过大，请使用 50MB 以内的图片")
    try:
        with Image.open(path) as img:
            img.verify()
    except Exception:
        raise HTTPException(status_code=400, detail="文件不是可识别的图片")
    filename = f"ai_ref_{uuid.uuid4().hex[:12]}{ext}"
    dest = output_path_for(filename, "input")
    try:
        shutil.copyfile(path, dest)
    except OSError:
        raise HTTPException(status_code=500, detail="导入本地图片失败")
    return {"url": output_url_for(filename, "input"), "name": os.path.basename(path) or filename, "kind": "image"}

def default_asset_library():
    categories = [
        {"id": "characters", "name": "角色", "type": "image", "items": []},
        {"id": "scenes", "name": "场景", "type": "image", "items": []},
    ]
    return {
        "active_library_id": "default",
        "libraries": [{"id": "default", "name": "默认资产库", "type": "asset", "categories": categories}],
        "categories": categories,
        "updated_at": now_ms(),
    }

def normalize_asset_library(lib):
    if not isinstance(lib, dict):
        lib = default_asset_library()
    legacy_categories = lib.get("categories") if isinstance(lib.get("categories"), list) else None
    libraries = lib.get("libraries") if isinstance(lib.get("libraries"), list) else []
    if not libraries:
        libraries = [{
            "id": "default",
            "name": "默认资产库",
            "type": "asset",
            "categories": legacy_categories or default_asset_library()["categories"],
        }]
    for library in libraries:
        library["id"] = re.sub(r"[^A-Za-z0-9_-]+", "_", str(library.get("id") or f"lib_{uuid.uuid4().hex[:8]}"))[:40]
        library["name"] = sanitize_asset_name(library.get("name") or "资产库", "资产库")
        cats = library.get("categories") if isinstance(library.get("categories"), list) else []
        cats[:] = [cat for cat in cats if isinstance(cat, dict) and cat.get("type", "image") != "workflow"]
        for cat in cats:
            for item in (cat.get("items") or []):
                migrate_asset_item_registrations(item)
        library["categories"] = cats
    active = str(lib.get("active_library_id") or libraries[0].get("id") or "default")
    if not any(item.get("id") == active for item in libraries):
        active = libraries[0].get("id") or "default"
    active_library = next((item for item in libraries if item.get("id") == active), libraries[0])
    lib["libraries"] = libraries
    lib["active_library_id"] = active
    lib["categories"] = active_library.get("categories") or []
    lib["updated_at"] = int(lib.get("updated_at") or now_ms())
    sort_asset_library_items(lib)
    return lib

AVATAR_LEGACY_FLAT_FIELDS = ("platform", "provider_id", "project_name", "avatar_task_id",
                             "avatar_status", "avatar_detail", "asset_uri", "asset_id", "registered_at")

def migrate_asset_item_registrations(item):
    """一个素材可注册到多平台：把旧的单平台扁平字段折叠进 item['registrations'][platform]，再清掉旧字段。"""
    if not isinstance(item, dict):
        return
    regs = item.get("registrations")
    if not isinstance(regs, dict):
        regs = {}
    legacy_platform = str(item.get("platform") or "").strip()
    if legacy_platform and legacy_platform not in regs and (item.get("asset_uri") or item.get("avatar_task_id")):
        regs[legacy_platform] = {
            "provider_id": item.get("provider_id") or "",
            "project_name": item.get("project_name") or "default",
            "task_id": item.get("avatar_task_id") or "",
            "status": item.get("avatar_status") or "",
            "detail": item.get("avatar_detail") or "",
            "asset_uri": item.get("asset_uri") or "",
            "asset_id": item.get("asset_id") or "",
            "registered_at": item.get("registered_at") or 0,
        }
    item["registrations"] = regs if isinstance(regs, dict) else {}
    for key in AVATAR_LEGACY_FLAT_FIELDS:
        item.pop(key, None)

def load_asset_library():
    if not os.path.exists(ASSET_LIBRARY_PATH):
        lib = default_asset_library()
        save_asset_library(lib)
        return lib
    try:
        with open(ASSET_LIBRARY_PATH, "r", encoding="utf-8") as f:
            lib = json.load(f)
    except Exception:
        lib = default_asset_library()
    return normalize_asset_library(lib)

def sort_asset_library_items(lib):
    cats = list(lib.get("categories", []))
    for library in lib.get("libraries", []) if isinstance(lib.get("libraries"), list) else []:
        cats.extend(library.get("categories") or [])
    seen = set()
    for cat in cats:
        if id(cat) in seen:
            continue
        seen.add(id(cat))
        items = cat.get("items")
        if isinstance(items, list):
            def created_at_key(item):
                if not isinstance(item, dict):
                    return 0
                try:
                    return int(float(item.get("created_at") or 0))
                except (TypeError, ValueError):
                    return 0
            items.sort(key=created_at_key, reverse=True)

def asset_library_media_kind(path: str, content_type: str = "") -> str:
    ext = os.path.splitext(path or "")[1].lower()
    ct = (content_type or "").lower()
    if ext in {".mp4", ".webm", ".mov", ".m4v", ".avi", ".mkv"} or ct.startswith("video/"):
        return "video"
    if ext in {".mp3", ".wav", ".m4a", ".aac", ".ogg", ".flac"} or ct.startswith("audio/"):
        return "audio"
    return "image"

def asset_library_safe_extension(path: str, kind: str) -> str:
    ext = os.path.splitext(path or "")[1].lower()
    allowed = {
        "image": {".png", ".jpg", ".jpeg", ".webp", ".gif"},
        "video": {".mp4", ".webm", ".mov", ".m4v", ".avi", ".mkv"},
        "audio": {".mp3", ".wav", ".m4a", ".aac", ".ogg", ".flac"},
    }
    fallback = {"image": ".png", "video": ".mp4", "audio": ".mp3"}
    return ext if ext in allowed.get(kind, allowed["image"]) else fallback.get(kind, ".png")

def unique_asset_category_dir(library, base_name: str) -> str:
    """为资产库分组生成一个唯一、文件系统安全的子文件夹名（library/<dir>/）。
    以分组名为基础（保留中文），与同库其它分组的 dir 及磁盘上已存在的文件夹去重。"""
    base = sanitize_asset_name(base_name, "分组").strip(" .") or "分组"
    existing = {
        str(c.get("dir")) for c in (library.get("categories") or [])
        if isinstance(c, dict) and c.get("dir")
    }
    candidate = base
    i = 2
    while candidate in existing or os.path.exists(os.path.join(ASSET_LIBRARY_DIR, candidate)):
        candidate = f"{base}_{i}"
        i += 1
    return candidate

def remove_asset_library_file(item) -> None:
    """删除资产对应的本地文件（仅限 library 副本，删了不影响 /output 原图）。日志不影响主流程。"""
    try:
        url = item.get("url") if isinstance(item, dict) else ""
        path = output_file_from_url(url)
        if path and os.path.isfile(path):
            os.remove(path)
    except Exception as exc:
        print(f"删除资产文件失败: {exc}")

def make_asset_library_item(src: str, name: str = "", subdir: str = "") -> Tuple[str, Dict[str, Any]]:
    kind = asset_library_media_kind(src)
    ext = asset_library_safe_extension(src, kind)
    safe_name = sanitize_asset_name(name or os.path.basename(src), "asset")
    if not os.path.splitext(safe_name)[1]:
        safe_name += ext
    dest_name = f"lib_{uuid.uuid4().hex[:12]}_{safe_name}"
    subdir = str(subdir or "").strip("/").strip()
    if subdir:
        dest_dir = os.path.join(ASSET_LIBRARY_DIR, subdir)
        os.makedirs(dest_dir, exist_ok=True)
        dest_path = os.path.join(dest_dir, dest_name)
        rel = f"{subdir}/{dest_name}"
    else:
        dest_path = os.path.join(ASSET_LIBRARY_DIR, dest_name)
        rel = dest_name
    shutil.copy2(src, dest_path)
    item = {
        "id": f"asset_{uuid.uuid4().hex[:12]}",
        "name": os.path.splitext(safe_name)[0][:120],
        "url": "/assets/library/" + urllib.parse.quote(rel, safe="/"),
        "kind": kind,
        "created_at": now_ms(),
    }
    return dest_name, item
    return lib

ASSET_CLASSIFICATION_PROMPT = """请识别这张图片，输出严格 JSON，不要 Markdown，不要解释。
目标是给素材库做非常全面的筛选分类。所有字段都用中文短标签数组，尽量具体但不要虚构。
JSON 结构：
{
  "summary": "一句话描述",
  "categories": {
    "environment": ["室内/室外/自然/城市/棚拍/商业空间等环境大类"],
    "scene": ["室内/室外/棚拍/街景/自然/商业空间等"],
    "space": ["卧室/餐厅/客厅/厨房/浴室/办公室/店铺/展厅/户外道路等"],
    "subject": ["人物/模特/产品/家具/建筑/食物/动物/车辆/植物等"],
    "model": ["无人/单人模特/多人模特/男性模特/女性模特/儿童模特/半身模特/全身模特/手部模特等"],
    "people": ["无人/单人/多人/男性/女性/儿童/半身/全身/手部特写等"],
    "style": ["写实/摄影/插画/3D/极简/奢华/复古/现代/电商/电影感等"],
    "lighting": ["自然光/硬光/柔光/逆光/侧光/夜景/暖光/冷光/高对比/低对比等"],
    "color": ["白色/黑色/暖色/冷色/高饱和/低饱和/莫兰迪/金属色等"],
    "composition": ["近景/中景/远景/俯拍/仰拍/正面/侧面/居中/留白/对称/特写等"],
    "mood": ["温馨/高级/清爽/科技/自然/浪漫/神秘/活力/安静等"],
    "use_case": ["广告/电商主图/海报/社媒/样机/参考图/背景/角色参考/空间参考等"],
    "objects": ["画面中重要物体"],
    "materials": ["木材/金属/玻璃/布料/皮革/石材/陶瓷等"],
    "quality": ["高清/模糊/低清/噪点/水印/截图/透明背景等"]
  },
  "tags": ["综合关键词，20个以内"]
}
要求：只返回可解析 JSON；每个数组最多 8 项；如果不确定就省略该标签。"""
ASSET_CLASSIFICATION_PROMPT_FILE = os.path.join(DATA_DIR, "asset_classification_prompt.txt")

def load_asset_classification_prompt():
    try:
        if os.path.isfile(ASSET_CLASSIFICATION_PROMPT_FILE):
            with open(ASSET_CLASSIFICATION_PROMPT_FILE, "r", encoding="utf-8-sig") as f:
                text = f.read().strip()
                if text:
                    return text
    except Exception as exc:
        print(f"读取素材分类规则失败: {exc}")
    return ASSET_CLASSIFICATION_PROMPT

def save_asset_classification_prompt(text):
    value = str(text or "").strip()
    if not value:
        raise HTTPException(status_code=400, detail="分类规则不能为空")
    if len(value) > 20000:
        raise HTTPException(status_code=400, detail="分类规则过长")
    os.makedirs(DATA_DIR, exist_ok=True)
    with open(ASSET_CLASSIFICATION_PROMPT_FILE, "w", encoding="utf-8") as f:
        f.write(value)
    return value

ASSET_CLASSIFICATION_DIMENSION_NAMES = {
    "environment": "环境",
    "scene": "场景",
    "space": "空间",
    "subject": "主体",
    "model": "模特",
    "people": "人物",
    "style": "风格",
    "lighting": "光影",
    "color": "色彩",
    "composition": "构图",
    "mood": "氛围",
    "use_case": "用途",
    "objects": "物体",
    "materials": "材质",
    "quality": "质量",
}

def _local_upload_classification_path(filename):
    return os.path.splitext(os.path.join(LOCAL_UPLOAD_DIR, filename))[0] + ".classification.json"

def _safe_asset_tag(value, limit=24):
    text = re.sub(r"\s+", " ", str(value or "").strip())
    text = re.sub(r"^[#＃]+", "", text).strip(" ,，、;；|/")
    return text[:limit]

def normalize_asset_classification(raw):
    if not isinstance(raw, dict):
        raw = {}
    categories = raw.get("categories") if isinstance(raw.get("categories"), dict) else {}
    clean_categories = {}
    flat = []
    for key, values in categories.items():
        norm_key = re.sub(r"[^A-Za-z0-9_-]+", "_", str(key or "").strip().lower())[:40]
        if not norm_key:
            continue
        if isinstance(values, str):
            values = re.split(r"[,，、/|;；\n]+", values)
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
            flat.append({"dimension": norm_key, "label": ASSET_CLASSIFICATION_DIMENSION_NAMES.get(norm_key, norm_key), "tag": tag})
            if len(clean_values) >= 8:
                break
        if clean_values:
            clean_categories[norm_key] = clean_values
    tags = raw.get("tags") if isinstance(raw.get("tags"), list) else []
    clean_tags = []
    seen_tags = set()
    for value in tags:
        tag = _safe_asset_tag(value)
        if not tag or tag in seen_tags:
            continue
        seen_tags.add(tag)
        clean_tags.append(tag)
        flat.append({"dimension": "tags", "label": "标签", "tag": tag})
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
    return {
        "summary": str(raw.get("summary") or "").strip()[:240],
        "categories": clean_categories,
        "tags": clean_tags,
        "flat": flat_unique,
        "updated_at": now_ms(),
    }

def parse_asset_classification_text(text):
    value = str(text or "").strip()
    if not value:
        return normalize_asset_classification({})
    value = re.sub(r"^```(?:json)?\s*", "", value, flags=re.IGNORECASE).strip()
    value = re.sub(r"\s*```$", "", value).strip()
    try:
        data = json.loads(value)
    except Exception:
        match = re.search(r"\{.*\}", value, re.S)
        data = json.loads(match.group(0)) if match else {}
    return normalize_asset_classification(data)

def _read_local_upload_classification(filename):
    path = _local_upload_classification_path(filename)
    if not os.path.isfile(path):
        return None
    try:
        with open(path, "r", encoding="utf-8") as f:
            return normalize_asset_classification(json.load(f))
    except Exception:
        return None

def _write_local_upload_classification(filename, classification):
    path = _local_upload_classification_path(filename)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(normalize_asset_classification(classification), f, ensure_ascii=False, indent=2)

def asset_classification_prompt(extra_prompt=""):
    base = load_asset_classification_prompt()
    extra = str(extra_prompt or "").strip()
    if not extra:
        return base
    return base + "\n\n用户补充分类要求：\n" + extra[:4000]

async def classify_image_with_provider(abs_path, provider_id="", model="", ms_model="", prompt=""):
    text, resolved_model = await caption_image_with_provider(
        abs_path,
        asset_classification_prompt(prompt),
        provider_id or get_primary_provider_id(),
        model,
        ms_model,
    )
    classification = parse_asset_classification_text(text)
    classification["model"] = resolved_model
    classification["provider"] = provider_id or get_primary_provider_id()
    return classification

async def classify_asset_image_best_effort(abs_path, provider_id="", model="", ms_model="", prompt=""):
    try:
        return await classify_image_with_provider(abs_path, provider_id, model, ms_model, prompt)
    except Exception as exc:
        print(f"素材智能分类失败: {exc}")
        return None

def migrate_asset_library_into_dirs():
    """一次性整理：给所有图片分组（含默认的角色/场景）补上真实文件夹，并把仍在 library/ 根目录的
    素材文件搬进各自分组的文件夹、同步更新 URL。幂等：已经在子文件夹里的不动；可安全反复执行。"""
    try:
        lib = load_asset_library()
    except Exception as exc:
        print(f"资产库分组整理：加载失败 {exc}")
        return
    changed = False
    for library in lib.get("libraries", []) or []:
        for cat in library.get("categories", []) or []:
            if (cat.get("type") or "image") != "image":
                continue
            if not cat.get("dir"):
                cat["dir"] = unique_asset_category_dir(library, cat.get("name") or "分组")
                changed = True
            cat_dir = str(cat.get("dir") or "").strip("/").strip()
            if not cat_dir:
                continue
            try:
                os.makedirs(os.path.join(ASSET_LIBRARY_DIR, cat_dir), exist_ok=True)
            except Exception as exc:
                print(f"资产库分组整理：建文件夹失败 {exc}")
                continue
            for item in (cat.get("items") or []):
                raw_url = urllib.parse.unquote(str(item.get("url") or "").split("?", 1)[0])
                m = re.match(r"^/assets/library/([^/]+)$", raw_url)  # 仅匹配仍在根目录的文件
                if not m:
                    continue
                fname = m.group(1)
                src = os.path.join(ASSET_LIBRARY_DIR, fname)
                if not os.path.isfile(src):
                    continue
                dst = os.path.join(ASSET_LIBRARY_DIR, cat_dir, fname)
                try:
                    if not os.path.exists(dst):
                        shutil.move(src, dst)
                    item["url"] = "/assets/library/" + urllib.parse.quote(f"{cat_dir}/{fname}", safe="/")
                    changed = True
                except Exception as exc:
                    print(f"资产库分组整理：搬运 {fname} 失败 {exc}")
    if changed:
        try:
            save_asset_library(lib)
        except Exception as exc:
            print(f"资产库分组整理：保存失败 {exc}")

def save_asset_library(lib):
    with CANVAS_LOCK:
        lib = normalize_asset_library(lib)
        sort_asset_library_items(lib)
        lib["updated_at"] = now_ms()
        os.makedirs(DATA_DIR, exist_ok=True)
        with open(ASSET_LIBRARY_PATH, "w", encoding="utf-8") as f:
            json.dump(lib, f, ensure_ascii=False, indent=2)
    if GLOBAL_LOOP:
        asyncio.run_coroutine_threadsafe(manager.broadcast_asset_library_updated(int(lib["updated_at"])), GLOBAL_LOOP)

def find_asset_category(lib, category_id):
    for cat in lib.get("categories", []):
        if cat.get("id") == category_id:
            return cat
    return None

def find_asset_library(lib, library_id=""):
    lib = normalize_asset_library(lib)
    library_id = str(library_id or lib.get("active_library_id") or "").strip()
    return next((item for item in lib.get("libraries", []) if item.get("id") == library_id), None) or (lib.get("libraries") or [None])[0]

def find_asset_category_in_library(lib, category_id, library_id=""):
    library = find_asset_library(lib, library_id)
    if not library:
        return None
    for cat in library.get("categories", []):
        if cat.get("id") == category_id:
            return cat
    return None

def find_asset_category_with_library(lib, category_id, library_id=""):
    lib = normalize_asset_library(lib)
    preferred = str(library_id or "").strip()
    libraries = lib.get("libraries", []) or []
    if preferred:
        libraries = [item for item in libraries if item.get("id") == preferred]
    for library in libraries:
        for cat in library.get("categories", []) or []:
            if cat.get("id") == category_id:
                return library, cat
    return None, None

# ---------------- 共享文件夹（局域网只读浏览/引用） ----------------
SHARED_MEDIA_EXTS = {
    ".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp",
    ".mp4", ".webm", ".mov", ".m4v", ".mkv",
    ".mp3", ".wav", ".m4a", ".aac", ".ogg", ".flac",
}
SHARED_SCAN_MAX_ENTRIES = 8000
SHARED_FOLDERS_LOCK = Lock()

def shared_folders_load():
    try:
        with open(SHARED_FOLDERS_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        data = {}
    if not isinstance(data, dict):
        data = {}
    folders = data.get("folders")
    if not isinstance(folders, list):
        folders = []
    return {"folders": [f for f in folders if isinstance(f, dict)]}

def shared_folders_save(data):
    os.makedirs(DATA_DIR, exist_ok=True)
    with open(SHARED_FOLDERS_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

def shared_folder_by_id(folder_id):
    for entry in shared_folders_load().get("folders", []):
        if entry.get("id") == folder_id:
            return entry
    return None

def shared_folder_abs(entry):
    rel = (entry or {}).get("rel") or ""
    return os.path.normpath(os.path.join(BASE_DIR, rel))

def shared_resolve_register(path):
    """校验 path 必须位于项目目录内、是一个存在的子目录（非项目根）。返回 (abs, rel)。"""
    raw = (path or "").strip().strip('"').strip("'")
    if not raw:
        raise HTTPException(status_code=400, detail="请提供文件夹路径")
    candidate = raw if os.path.isabs(raw) else os.path.join(BASE_DIR, raw)
    abs_path = os.path.normpath(os.path.abspath(candidate))
    base = os.path.normpath(os.path.abspath(BASE_DIR))
    try:
        common = os.path.commonpath([abs_path, base])
    except ValueError:
        raise HTTPException(status_code=400, detail="只允许登记项目目录内的文件夹")
    if common != base:
        raise HTTPException(status_code=400, detail="只允许登记项目目录内的文件夹")
    if abs_path == base:
        raise HTTPException(status_code=400, detail="不能直接登记项目根目录，请选择子文件夹")
    if not os.path.isdir(abs_path):
        raise HTTPException(status_code=400, detail="文件夹不存在")
    rel = os.path.relpath(abs_path, base)
    return abs_path, rel

def shared_child_abs(folder_abs, rel):
    """把相对 folder_abs 的子路径解析为绝对路径，并防止越界访问。"""
    rel = (rel or "").replace("\\", "/").lstrip("/")
    abs_path = os.path.normpath(os.path.join(folder_abs, rel))
    base = os.path.normpath(os.path.abspath(folder_abs))
    try:
        common = os.path.commonpath([os.path.abspath(abs_path), base])
    except ValueError:
        raise HTTPException(status_code=400, detail="非法路径")
    if common != base:
        raise HTTPException(status_code=400, detail="非法路径")
    return abs_path

def image_path_to_data_url(path, max_size=1024):
    if max_size:
        try:
            with Image.open(path) as img:
                img.load()
                if max(img.size) > max_size:
                    img.thumbnail((max_size, max_size), Image.LANCZOS)
                if img.mode not in ("RGB", "RGBA"):
                    img = img.convert("RGB")
                buf = BytesIO()
                fmt = "PNG" if img.mode == "RGBA" else "JPEG"
                img.save(buf, format=fmt, quality=88 if fmt == "JPEG" else None)
                encoded = base64.b64encode(buf.getvalue()).decode("ascii")
                mime = "image/png" if fmt == "PNG" else "image/jpeg"
                return f"data:{mime};base64,{encoded}"
        except Exception as e:
            print(f"shared caption image resize failed: {e}")
    with open(path, "rb") as f:
        encoded = base64.b64encode(f.read()).decode("ascii")
    return f"data:{content_type_for_path(path)};base64,{encoded}"

def scan_shared_tree(folder_id, folder_abs, rel_prefix="", display="", counter=None):
    """递归扫描共享文件夹，返回 {id,name,path,items,children}。"""
    if counter is None:
        counter = {"n": 0}
    node = {
        "id": f"{folder_id}:{rel_prefix or '__root__'}",
        "name": display or os.path.basename(folder_abs) or folder_abs,
        "path": rel_prefix,
        "items": [],
        "children": [],
    }
    try:
        entries = sorted(os.scandir(folder_abs), key=lambda e: (not e.is_dir(), e.name.lower()))
    except OSError:
        return node
    for ent in entries:
        if counter["n"] >= SHARED_SCAN_MAX_ENTRIES:
            break
        if ent.name.startswith(".") or ent.name.startswith("._"):
            continue
        child_rel = f"{rel_prefix}/{ent.name}".lstrip("/")
        if ent.is_dir():
            child = scan_shared_tree(folder_id, ent.path, child_rel, ent.name, counter)
            if child["items"] or child["children"]:
                node["children"].append(child)
        elif ent.is_file():
            ext = os.path.splitext(ent.name)[1].lower()
            if ext not in SHARED_MEDIA_EXTS:
                continue
            counter["n"] += 1
            try:
                st = ent.stat()
                size = st.st_size
                mtime = int(st.st_mtime * 1000)
            except OSError:
                size = 0
                mtime = 0
            node["items"].append({
                "id": f"{folder_id}:{child_rel}",
                "name": ent.name,
                "url": f"/api/shared-folders/{folder_id}/file?path={urllib.parse.quote(child_rel)}",
                "kind": asset_library_media_kind(ent.name),
                "size": size,
                "lastModified": mtime,
                "relativePath": child_rel,
                "folderId": folder_id,
            })
    return node

def builtin_prompt_templates():
    try:
        template_path = prompt_template_markdown_path()
        if not template_path:
            return []
        with open(template_path, "r", encoding="utf-8") as f:
            return parse_prompt_template_markdown(f.read())
    except Exception as e:
        print(f"读取提示词模板失败: {e}")
        return []

def normalize_prompt_category_id(category="custom"):
    category_id = re.sub(r"[^A-Za-z0-9_-]+", "_", str(category or "custom"))[:40] or "custom"
    return "custom" if category_id in {"mine", "my", "personal"} else category_id

def normalize_prompt_library_item(item):
    if not isinstance(item, dict):
        item = {}
    name = sanitize_asset_name(item.get("name") or "提示词", "提示词")
    positive = str(item.get("positive") or item.get("text") or "").strip()
    return {
        "id": re.sub(r"[^A-Za-z0-9_-]+", "_", str(item.get("id") or item.get("item_id") or f"tpl_{uuid.uuid4().hex[:12]}"))[:60],
        "name": name,
        "category": normalize_prompt_category_id(item.get("category") or "custom"),
        "scene": str(item.get("scene") or "").strip()[:500],
        "positive": positive,
        "negative": str(item.get("negative") or "").strip(),
        "params": item.get("params") if isinstance(item.get("params"), dict) else {},
        "created_at": int(item.get("created_at") or now_ms()),
        "updated_at": int(item.get("updated_at") or item.get("created_at") or now_ms()),
    }

def seed_system_prompt_library():
    return {
        "id": "system",
        "name": "系统提示词库",
        "type": "prompt",
        "items": builtin_prompt_templates(),
        "categories": defaultPromptTemplateCategories(),
    }

def default_prompt_libraries():
    return {
        "active_library_id": "system",
        "libraries": [seed_system_prompt_library()],
        "updated_at": now_ms(),
    }

def defaultPromptTemplateCategories():
    return [
        {"id": "view", "name": "视角"},
        {"id": "storyboard", "name": "分镜"},
        {"id": "character", "name": "角色"},
        {"id": "product", "name": "产品"},
        {"id": "lighting", "name": "光影"},
        {"id": "custom", "name": "我的"},
    ]

def normalize_prompt_template_categories(*category_lists, include_defaults=True):
    normalized = []
    seen = set()

    def add_category(category):
        if not isinstance(category, dict):
            return
        cat_id = normalize_prompt_category_id(category.get("id") or category.get("name") or "custom")
        if cat_id in seen:
            return
        seen.add(cat_id)
        # 不再强制把 custom 显示为“我的”，分组名以存储为准，这样内置分组也能被重命名。
        name = sanitize_asset_name(category.get("name") or cat_id, cat_id)
        normalized.append({"id": cat_id, "name": name})

    # 先采用已存储的分组（保留用户对内置分组的重命名/删除），
    # 只有在系统库一个分组都没有时才补齐默认内置分组（首次初始化）。
    for categories in category_lists:
        if isinstance(categories, list):
            for category in categories:
                add_category(category)
    if include_defaults and not normalized:
        for category in defaultPromptTemplateCategories():
            add_category(category)
    return normalized

def normalize_prompt_libraries(data):
    if not isinstance(data, dict):
        data = default_prompt_libraries()
    raw_libraries = data.get("libraries") if isinstance(data.get("libraries"), list) else []
    raw_libraries = [lib for lib in raw_libraries if isinstance(lib, dict)]
    if not any(lib.get("id") == "system" for lib in raw_libraries):
        raw_libraries = [seed_system_prompt_library()] + raw_libraries
    libraries = []
    seen_lib_ids = set()
    for raw in raw_libraries:
        is_system = raw.get("id") == "system"
        if is_system:
            lib_id = "system"
        else:
            lib_id = re.sub(r"[^A-Za-z0-9_-]+", "_", str(raw.get("id") or f"lib_{uuid.uuid4().hex[:12]}"))[:60] or f"lib_{uuid.uuid4().hex[:12]}"
        if lib_id in seen_lib_ids:
            continue
        seen_lib_ids.add(lib_id)
        items = []
        seen_items = set()
        for raw_item in (raw.get("items") if isinstance(raw.get("items"), list) else []):
            if not isinstance(raw_item, dict):
                continue
            item = normalize_prompt_library_item(raw_item)
            item_id = item.get("id") or f"tpl_{uuid.uuid4().hex[:12]}"
            if item_id in seen_items:
                continue
            seen_items.add(item_id)
            items.append(item)
        default_name = "系统提示词库" if is_system else "提示词库"
        raw_categories = raw.get("categories") if isinstance(raw.get("categories"), list) else []
        if not is_system:
            # 非系统库不保留任何内置分组（视角/分镜等），仅保留用户自建分组
            builtin_ids = {"view", "storyboard", "character", "product", "lighting", "custom"}
            raw_categories = [c for c in raw_categories if isinstance(c, dict) and normalize_prompt_category_id(c.get("id") or c.get("name") or "") not in builtin_ids]
        libraries.append({
            "id": lib_id,
            "name": sanitize_asset_name(raw.get("name") or default_name, default_name),
            "type": "prompt",
            "readonly": False,
            "system": is_system,
            "categories": normalize_prompt_template_categories(raw_categories, include_defaults=is_system),
            "items": items,
        })
    active = str(data.get("active_library_id") or "system")
    if not any(lib["id"] == active for lib in libraries):
        active = "system" if any(lib["id"] == "system" for lib in libraries) else (libraries[0]["id"] if libraries else "system")
    return {"active_library_id": active, "libraries": libraries, "updated_at": int(data.get("updated_at") or now_ms())}

def load_prompt_libraries():
    if not os.path.exists(PROMPT_LIBRARY_PATH):
        data = default_prompt_libraries()
        return save_prompt_libraries(data)
    try:
        with open(PROMPT_LIBRARY_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
    except Exception:
        data = default_prompt_libraries()
    if not isinstance(data, dict):
        data = default_prompt_libraries()
    normalized = normalize_prompt_libraries(data)
    if normalized.get("active_library_id") != data.get("active_library_id") or normalized.get("libraries") != data.get("libraries"):
        return save_prompt_libraries(normalized)
    return normalized

def save_prompt_libraries(data):
    data = normalize_prompt_libraries(data)
    data["updated_at"] = now_ms()
    os.makedirs(DATA_DIR, exist_ok=True)
    with open(PROMPT_LIBRARY_PATH, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    return data

def public_prompt_libraries(data=None):
    data = normalize_prompt_libraries(data or load_prompt_libraries())
    return {
        "active_library_id": data.get("active_library_id") or (data.get("libraries") or [{}])[0].get("id") or "system",
        "libraries": data.get("libraries") or [],
        "updated_at": data.get("updated_at") or now_ms(),
    }

def find_prompt_library(data, library_id=""):
    if not isinstance(data, dict):
        return None
    libraries = data.get("libraries") if isinstance(data.get("libraries"), list) else []
    library_id = str(library_id or data.get("active_library_id") or "").strip()
    return next((item for item in libraries if item.get("id") == library_id), None) or (libraries[0] if libraries else None)

def sanitize_asset_name(name, fallback="asset"):
    name = re.sub(r'[\\/:*?"<>|]+', "_", str(name or fallback)).strip()
    return name[:120] or fallback

def content_type_for_path(path):
    ext = os.path.splitext(path)[1].lower()
    if ext in [".mp4", ".m4v"]:
        return "video/mp4"
    if ext == ".webm":
        return "video/webm"
    if ext == ".mov":
        return "video/quicktime"
    if ext == ".avi":
        return "video/x-msvideo"
    if ext == ".mkv":
        return "video/x-matroska"
    if ext == ".flv":
        return "video/x-flv"
    if ext == ".mp3":
        return "audio/mpeg"
    if ext == ".wav":
        return "audio/wav"
    if ext == ".m4a":
        return "audio/mp4"
    if ext == ".aac":
        return "audio/aac"
    if ext == ".ogg":
        return "audio/ogg"
    if ext == ".flac":
        return "audio/flac"
    if ext == ".gif":
        return "image/gif"
    if ext in [".jpg", ".jpeg"]:
        return "image/jpeg"
    if ext == ".webp":
        return "image/webp"
    if ext == ".txt":
        return "text/plain; charset=utf-8"
    if ext == ".json":
        return "application/json; charset=utf-8"
    if ext == ".csv":
        return "text/csv; charset=utf-8"
    if ext == ".md":
        return "text/markdown; charset=utf-8"
    if ext == ".srt":
        return "application/x-subrip; charset=utf-8"
    if ext == ".vtt":
        return "text/vtt; charset=utf-8"
    if ext == ".png":
        return "image/png"
    return "application/octet-stream"

def is_image_reference_value(value):
    if not isinstance(value, str) or not value:
        return False
    if value.startswith("data:image/"):
        return True
    if value.startswith("data:"):
        return False
    if value.startswith("/output/") or value.startswith("/assets/"):
        path = output_file_from_url(value)
        return bool(path and content_type_for_path(path).startswith("image/"))
    clean = value.split("?", 1)[0].lower()
    if re.search(r"\.(mp4|webm|mov|m4v|avi|mkv|mp3|wav|m4a|aac|ogg|flac)$", clean):
        return False
    return True

def is_video_reference_value(value):
    if not isinstance(value, str) or not value:
        return False
    if value.startswith("data:video/"):
        return True
    if value.startswith("data:"):
        return False
    if value.startswith("/output/") or value.startswith("/assets/"):
        path = output_file_from_url(value)
        return bool(path and content_type_for_path(path).startswith("video/"))
    clean = value.split("?", 1)[0].lower()
    return bool(re.search(r"\.(mp4|webm|mov|m4v|avi|mkv)$", clean))

def convert_output_to_jpg(url, quality=88):
    path = output_file_from_url(url)
    if not path:
        return url
    root, ext = os.path.splitext(path)
    if ext.lower() in [".jpg", ".jpeg"]:
        return url
    jpg_path = f"{root}.jpg"
    try:
        with Image.open(path) as img:
            if img.mode in ("RGBA", "LA") or (img.mode == "P" and "transparency" in img.info):
                bg = Image.new("RGB", img.size, (255, 255, 255))
                bg.paste(img.convert("RGBA"), mask=img.convert("RGBA").split()[-1])
                img = bg
            else:
                img = img.convert("RGB")
            img.save(jpg_path, "JPEG", quality=quality, optimize=True)
        try:
            root = ASSETS_DIR if os.path.commonpath([os.path.abspath(ASSETS_DIR), os.path.abspath(jpg_path)]) == os.path.abspath(ASSETS_DIR) else OUTPUT_DIR
        except ValueError:
            root = OUTPUT_DIR
        rel = os.path.relpath(jpg_path, root).replace("\\", "/")
        prefix = "/assets" if root == ASSETS_DIR else "/output"
        return f"{prefix}/{rel}"
    except Exception as e:
        print(f"转换 JPG 失败: {e}")
        return url

def reference_to_data_url(ref, max_size=None, max_bytes=None):
    """把本地输出文件转为 data URL（base64）。max_size 限制最长边像素，避免 payload 过大。"""
    value = str(ref.get("url", "") or "").strip()
    path = local_media_adapter_path(value, "image")
    if not path:
        return value
    file_size = os.path.getsize(path)
    if max_bytes is not None and file_size > int(max_bytes):
        raise HTTPException(status_code=400, detail={
            "code": "conversation_media_file_too_large",
            "message": "项目图片参考素材超过对话适配器单文件上限",
        })
    if max_size:
        try:
            with Image.open(path) as img:
                w, h = img.size
                if max_bytes is not None and w * h > PROJECT_CONVERSATION_IMAGE_MAX_PIXELS:
                    raise HTTPException(status_code=400, detail={
                        "code": "conversation_image_dimensions_exceeded",
                        "message": "项目图片像素尺寸超过对话适配器安全上限",
                    })
                img.load()
                if max(w, h) > max_size:
                    img.thumbnail((max_size, max_size), Image.LANCZOS)
                if img.mode not in ("RGB", "RGBA"):
                    img = img.convert("RGB")
                buf = BytesIO()
                fmt = "PNG" if img.mode == "RGBA" else "JPEG"
                img.save(buf, format=fmt, quality=88 if fmt == "JPEG" else None)
                encoded = base64.b64encode(buf.getvalue()).decode("ascii")
                mime = "image/png" if fmt == "PNG" else "image/jpeg"
                return f"data:{mime};base64,{encoded}"
        except HTTPException:
            raise
        except Exception as e:
            print(f"reference resize failed, fallback to raw: {e}")
    with open(path, "rb") as f:
        encoded = base64.b64encode(f.read()).decode("ascii")
    return f"data:{content_type_for_path(path)};base64,{encoded}"

def media_adapter_data_url(value: str, expected_kind: str, *, max_bytes: Optional[int] = None) -> str:
    """Encode one local adapter input without exposing an app/project URL upstream."""
    path = local_media_adapter_path(value, expected_kind)
    if not path:
        return str(value or "").strip()
    size = os.path.getsize(path)
    if max_bytes is not None and size > int(max_bytes):
        raise HTTPException(status_code=400, detail={
            "code": "conversation_media_file_too_large",
            "message": f"项目{expected_kind}参考素材超过对话适配器单文件上限",
        })
    if size > PROJECT_VIDEO_REFERENCE_INLINE_MAX_BYTES:
        raise HTTPException(
            status_code=400,
            detail=f"项目{expected_kind}参考素材超过内联适配上限，请配置公网媒体地址或使用支持文件上传的服务商",
        )
    with open(path, "rb") as handle:
        encoded = base64.b64encode(handle.read()).decode("ascii")
    return f"data:{content_type_for_path(path)};base64,{encoded}"

def is_image_reference(ref):
    if not isinstance(ref, dict):
        return False
    kind = str(ref.get("kind") or "").strip().lower()
    mime = str(ref.get("mime") or "").strip().lower()
    url = str(ref.get("url") or "").strip().lower()
    if kind:
        return kind == "image"
    if mime:
        return mime.startswith("image/")
    return bool(re.search(r"\.(png|jpe?g|webp|gif|bmp|tiff?)(\?|#|$)", url))

def image_references(refs):
    return [ref for ref in (refs or []) if is_image_reference(ref)]

TEXT_ATTACHMENT_EXTS = {".txt", ".md", ".markdown", ".json", ".csv", ".log", ".py", ".js", ".ts", ".tsx", ".jsx", ".html", ".css", ".xml", ".yaml", ".yml"}
XLSX_IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp", ".tif", ".tiff"}
EXCEL_MAX_SHEETS = 8
EXCEL_MAX_ROWS_PER_SHEET = 80
EXCEL_MAX_COLS_PER_ROW = 30
MAX_ATTACHMENT_TEXT_CHARS = 12000

def _xml_local_name(tag):
    return str(tag or "").rsplit("}", 1)[-1]

def _xlsx_join_text(node):
    parts = []
    for child in node.iter():
        if _xml_local_name(child.tag) == "t" and child.text:
            parts.append(child.text)
    return "".join(parts).strip()

def _xlsx_shared_strings(archive):
    try:
        raw = archive.read("xl/sharedStrings.xml")
    except KeyError:
        return []
    root = ET.fromstring(raw)
    values = []
    for node in root:
        if _xml_local_name(node.tag) == "si":
            values.append(_xlsx_join_text(node))
    return values

def _xlsx_sheet_paths(archive):
    names = set(archive.namelist())
    fallback = [(os.path.basename(name).rsplit(".", 1)[0], name) for name in sorted(names) if re.match(r"xl/worksheets/sheet\d+\.xml$", name)]
    try:
        workbook = ET.fromstring(archive.read("xl/workbook.xml"))
        rels = ET.fromstring(archive.read("xl/_rels/workbook.xml.rels"))
        rel_map = {}
        for rel in rels:
            rid = rel.attrib.get("Id")
            target = rel.attrib.get("Target") or ""
            if not rid or not target:
                continue
            target = target.lstrip("/")
            if not target.startswith("xl/"):
                target = f"xl/{target}"
            rel_map[rid] = target.replace("\\", "/")
        result = []
        for sheet in workbook.iter():
            if _xml_local_name(sheet.tag) != "sheet":
                continue
            title = sheet.attrib.get("name") or "Sheet"
            rid = sheet.attrib.get("{http://schemas.openxmlformats.org/officeDocument/2006/relationships}id")
            target = rel_map.get(rid, "")
            if target in names:
                result.append((title, target))
        return result or fallback
    except Exception:
        return fallback

def _xlsx_cell_text(cell, shared_strings):
    cell_type = cell.attrib.get("t", "")
    value_node = None
    formula_node = None
    inline_node = None
    for child in cell:
        name = _xml_local_name(child.tag)
        if name == "v":
            value_node = child
        elif name == "f":
            formula_node = child
        elif name == "is":
            inline_node = child
    raw_value = (value_node.text if value_node is not None else "") or ""
    formula = (formula_node.text if formula_node is not None else "") or ""
    if cell_type == "s" and raw_value.isdigit():
        idx = int(raw_value)
        value = shared_strings[idx] if 0 <= idx < len(shared_strings) else raw_value
    elif cell_type == "inlineStr" and inline_node is not None:
        value = _xlsx_join_text(inline_node)
    elif cell_type == "b":
        value = "TRUE" if raw_value == "1" else "FALSE" if raw_value == "0" else raw_value
    else:
        value = raw_value
    value = str(value or "").strip()
    if formula and value:
        return f"{value} [={formula}]"
    if formula:
        return f"={formula}"
    return value

def read_xlsx_attachment(path, limit=MAX_ATTACHMENT_TEXT_CHARS):
    parts = []
    used = 0
    with zipfile.ZipFile(path) as archive:
        shared = _xlsx_shared_strings(archive)
        sheets = _xlsx_sheet_paths(archive)
        media_count = sum(1 for name in archive.namelist() if name.startswith("xl/media/") and os.path.splitext(name)[1].lower() in XLSX_IMAGE_EXTS)
        parts.append(f"Excel 工作簿：{os.path.basename(path)}")
        if media_count:
            parts.append(f"内嵌图片数量：{media_count}（已作为图片参考一并提供给模型）")
        for sheet_index, (sheet_name, sheet_path) in enumerate(sheets[:EXCEL_MAX_SHEETS], start=1):
            try:
                root = ET.fromstring(archive.read(sheet_path))
            except Exception:
                continue
            rows = []
            for row in root.iter():
                if _xml_local_name(row.tag) != "row":
                    continue
                cells = []
                for cell in row:
                    if _xml_local_name(cell.tag) != "c":
                        continue
                    ref = cell.attrib.get("r") or ""
                    value = _xlsx_cell_text(cell, shared)
                    if value:
                        cells.append(f"{ref}={value}" if ref else value)
                    if len(cells) >= EXCEL_MAX_COLS_PER_ROW:
                        break
                if cells:
                    row_ref = row.attrib.get("r") or str(len(rows) + 1)
                    rows.append(f"第 {row_ref} 行：" + " | ".join(cells))
                if len(rows) >= EXCEL_MAX_ROWS_PER_SHEET:
                    break
            if rows:
                section = f"\n工作表 {sheet_index}：{sheet_name}\n" + "\n".join(rows)
            else:
                section = f"\n工作表 {sheet_index}：{sheet_name}\n（未读取到非空单元格）"
            if used + len(section) > limit:
                remain = max(0, limit - used)
                if remain:
                    parts.append(section[:remain])
                parts.append("\n（Excel 内容较长，已截断）")
                break
            parts.append(section)
            used += len(section)
    return "\n".join(parts).strip()[:limit]

def xlsx_embedded_image_data_urls(path, max_images=4, max_size=1536):
    urls = []
    try:
        with zipfile.ZipFile(path) as archive:
            media = [name for name in archive.namelist() if name.startswith("xl/media/") and os.path.splitext(name)[1].lower() in XLSX_IMAGE_EXTS]
            for name in sorted(media)[:max_images]:
                try:
                    raw = archive.read(name)
                    with Image.open(BytesIO(raw)) as img:
                        img.load()
                        if max(img.size) > max_size:
                            img.thumbnail((max_size, max_size), Image.LANCZOS)
                        if img.mode in ("RGBA", "LA") or (img.mode == "P" and "transparency" in img.info):
                            bg = Image.new("RGB", img.size, (255, 255, 255))
                            bg.paste(img.convert("RGBA"), mask=img.convert("RGBA").split()[-1])
                            img = bg
                        elif img.mode != "RGB":
                            img = img.convert("RGB")
                        buf = BytesIO()
                        img.save(buf, format="JPEG", quality=88, optimize=True)
                        encoded = base64.b64encode(buf.getvalue()).decode("ascii")
                        urls.append(f"data:image/jpeg;base64,{encoded}")
                except Exception as exc:
                    print(f"[chat] failed to extract xlsx image {name}: {exc}")
    except Exception as exc:
        print(f"[chat] failed to read xlsx images {path}: {exc}")
    return urls

def attachment_embedded_image_data_urls(refs, max_images=4):
    urls = []
    for ref in (refs or []):
        if not isinstance(ref, dict) or is_image_reference(ref):
            continue
        path = output_file_from_url(ref.get("url", ""))
        if not path or os.path.splitext(path)[1].lower() != ".xlsx":
            continue
        urls.extend(xlsx_embedded_image_data_urls(path, max_images=max(0, max_images - len(urls))))
        if len(urls) >= max_images:
            break
    return urls[:max_images]

def read_text_attachment(path, limit=MAX_ATTACHMENT_TEXT_CHARS):
    ext = os.path.splitext(path or "")[1].lower()
    if not path or not os.path.isfile(path):
        return ""
    try:
        if ext == ".xlsx":
            return read_xlsx_attachment(path, limit)
        if ext == ".xls":
            return "这是旧版 .xls 二进制 Excel 文件，当前内置解析器暂不支持直接读取内容。请另存为 .xlsx 后重新上传。"
        if ext == ".docx":
            with zipfile.ZipFile(path) as archive:
                raw = archive.read("word/document.xml")
            root = ET.fromstring(raw)
            parts = []
            for node in root.iter():
                if node.tag.endswith("}t") and node.text:
                    parts.append(node.text)
                elif node.tag.endswith("}p"):
                    parts.append("\n")
            return html.unescape("".join(parts)).strip()[:limit]
        if ext in TEXT_ATTACHMENT_EXTS:
            with open(path, "rb") as f:
                data = f.read(min(os.path.getsize(path), limit * 4))
            for encoding in ("utf-8-sig", "utf-8", "gb18030"):
                try:
                    return data.decode(encoding, errors="strict").strip()[:limit]
                except UnicodeDecodeError:
                    continue
            return data.decode("utf-8", errors="replace").strip()[:limit]
    except Exception as exc:
        print(f"[chat] failed to read attachment text {path}: {exc}")
    return ""

def attachment_text_blocks(refs, limit_each=MAX_ATTACHMENT_TEXT_CHARS):
    blocks = []
    for ref in (refs or [])[:CHAT_ATTACHMENT_MAX]:
        if not isinstance(ref, dict) or is_image_reference(ref):
            continue
        path = output_file_from_url(ref.get("url", ""))
        text = read_text_attachment(path, limit_each) if path else ""
        if not text:
            continue
        name = ref.get("name") or os.path.basename(path)
        blocks.append(f"附件：{name}\n{text}")
    return blocks

def media_reference_to_url(value, max_image_size=None):
    if not isinstance(value, str) or not value:
        return ""
    if value.startswith("/output/") or value.startswith("/assets/"):
        return reference_to_data_url({"url": value}, max_size=max_image_size)
    return value

def is_private_asset_url(value: str) -> bool:
    return isinstance(value, str) and value.strip().startswith("asset://")

def volcengine_media_reference_url(value, max_image_size=1536):
    if not isinstance(value, str):
        return ""
    value = value.strip()
    if not value:
        return ""
    if is_private_asset_url(value):
        return value
    if value.startswith("/api/project-library/"):
        value = project_media_adapter_reference_from_url(value)
        if not value:
            return ""
    if value.startswith(f"{PROJECT_MEDIA_ADAPTER_SCHEME}://"):
        path = local_media_adapter_path(value)
        if path and content_type_for_path(path).startswith("image/"):
            return reference_to_data_url({"url": value}, max_size=max_image_size)
        return value
    if value.startswith("/output/") or value.startswith("/assets/"):
        return reference_to_data_url({"url": value}, max_size=max_image_size)
    return value

def looks_like_image_media_url(value: str) -> bool:
    text = str(value or "").strip().lower()
    if not text:
        return False
    if text.startswith("data:image/"):
        return True
    if text.startswith("asset://"):
        return False
    path = urllib.parse.urlparse(text).path or text
    return bool(re.search(r"\.(png|jpe?g|webp|gif|bmp|tiff)$", path))

def volcengine_content_role(role: str, kind: str = "image") -> Optional[str]:
    value = str(role or "").strip().lower()
    allowed = {
        "first_frame", "last_frame", "reference_image",
        "reference_video", "reference_audio", "video", "audio", "image"
    }
    if value in allowed:
        if value == "audio" and kind == "audio":
            return "reference_audio"
        return "reference_video" if value == "video" and kind == "video" else value
    if kind == "audio":
        return "reference_audio"
    if kind == "video":
        return "reference_video"
    # 修复：未显式指定 role 的纯生图请求不应兜底为 reference_image，
    # 否则火山后端会误判为 r2v(参考图生视频)，导致 seedance/seedream 等生图模型失败。
    return None

def volcengine_video_duration(duration) -> int:
    try:
        value = int(duration)
    except Exception:
        value = 5
    return max(1, min(60, value))

def volcengine_video_resolution(value: str) -> str:
    text = str(value or "").strip().lower()
    aliases = {"": "", "auto": "", "480": "480p", "720": "720p", "1080": "1080p"}
    text = aliases.get(text, text)
    return text if text in {"480p", "720p", "1080p"} else ""

def is_volcengine_seedance2_model(model: str) -> bool:
    value = str(model or "").strip().lower().replace("_", "-").replace(".", "-")
    return "seedance-2-0" in value

def probe_local_audio_duration_seconds(value: str) -> Optional[float]:
    path = local_media_adapter_path(value)
    if not path or not os.path.isfile(path):
        return None
    ffprobe = shutil.which("ffprobe")
    if not ffprobe:
        return None
    try:
        proc = subprocess.run(
            [
                ffprobe,
                "-v", "error",
                "-show_entries", "format=duration",
                "-of", "default=noprint_wrappers=1:nokey=1",
                path,
            ],
            capture_output=True,
            text=True,
            timeout=20,
        )
        if proc.returncode != 0:
            return None
        duration = float(str(proc.stdout or "").strip())
        return duration if math.isfinite(duration) and duration > 0 else None
    except Exception:
        return None

async def volcengine_video_reference_content_items(value, max_frames=4, max_size=768):
    text = str(value or "").strip()
    if not text:
        return []
    if is_private_asset_url(text):
        return [{
            "type": "video_url",
            "video_url": {"url": text},
            "role": "reference_video",
        }]
    frame_urls = await video_reference_to_frame_data_urls(text, max_frames=max_frames, max_size=max_size)
    return [
        {
            "type": "image_url",
            "image_url": {"url": frame_url},
            "role": "reference_image",
        }
        for frame_url in frame_urls
        if frame_url
    ]

async def video_reference_to_frame_data_urls(value, max_frames=6, max_size=768):
    if not isinstance(value, str) or not value:
        return []
    path = output_file_from_url(value)
    cleanup_path = ""
    if not path and value.startswith(("http://", "https://")):
        suffix = os.path.splitext(urllib.parse.urlparse(value).path)[1] or ".mp4"
        fd, cleanup_path = tempfile.mkstemp(prefix="canvas_llm_video_", suffix=suffix)
        os.close(fd)
        try:
            async with httpx.AsyncClient(timeout=httpx.Timeout(connect=20.0, read=120.0, write=30.0, pool=10.0)) as client:
                response = await client.get(value)
                response.raise_for_status()
                with open(cleanup_path, "wb") as f:
                    f.write(response.content)
            path = cleanup_path
        except Exception as e:
            print(f"[canvas-llm] video download failed: {e}")
            if cleanup_path and os.path.exists(cleanup_path):
                try: os.remove(cleanup_path)
                except OSError: pass
            return []
    if not path or not os.path.exists(path):
        return []
    frame_dir = tempfile.mkdtemp(prefix="canvas_llm_frames_")
    try:
        ffmpeg = shutil.which("ffmpeg")
        if not ffmpeg:
            return []
        pattern = os.path.join(frame_dir, "frame_%03d.jpg")
        cmd = [
            ffmpeg, "-hide_banner", "-loglevel", "error", "-y",
            "-i", path,
            "-vf", f"fps=1,scale='min({max_size},iw)':-2",
            "-frames:v", str(max(1, max_frames)),
            pattern
        ]
        proc = await asyncio.to_thread(subprocess.run, cmd, capture_output=True, text=True, timeout=90)
        if proc.returncode != 0:
            print(f"[canvas-llm] ffmpeg frame extract failed: {proc.stderr[:300]}")
            return []
        frames = []
        for name in sorted(os.listdir(frame_dir)):
            if not name.lower().endswith((".jpg", ".jpeg", ".png")):
                continue
            frame_path = os.path.join(frame_dir, name)
            with open(frame_path, "rb") as f:
                frames.append(f"data:image/jpeg;base64,{base64.b64encode(f.read()).decode('ascii')}")
        return frames
    finally:
        shutil.rmtree(frame_dir, ignore_errors=True)
        if cleanup_path and os.path.exists(cleanup_path):
            try: os.remove(cleanup_path)
            except OSError: pass

def compress_data_url_image(value, max_size=1536, jpeg_quality=88):
    if not isinstance(value, str) or not value.startswith("data:image/") or ";base64," not in value:
        return value
    header, encoded = value.split(";base64,", 1)
    try:
        raw = base64.b64decode(encoded)
        with Image.open(BytesIO(raw)) as img:
            img.load()
            if max_size and max(img.size) > max_size:
                img.thumbnail((max_size, max_size), Image.LANCZOS)
            has_alpha = img.mode in ("RGBA", "LA") or (img.mode == "P" and "transparency" in img.info)
            if has_alpha:
                if img.mode != "RGBA":
                    img = img.convert("RGBA")
                fmt, mime = "PNG", "image/png"
            else:
                if img.mode != "RGB":
                    img = img.convert("RGB")
                fmt, mime = "JPEG", "image/jpeg"
            buf = BytesIO()
            if fmt == "JPEG":
                img.save(buf, format=fmt, quality=jpeg_quality, optimize=True)
            else:
                img.save(buf, format=fmt, optimize=True)
            return f"data:{mime};base64,{base64.b64encode(buf.getvalue()).decode('ascii')}"
    except Exception as e:
        print(f"data url image compress failed, fallback to raw: {e}")
        return value

def modelscope_image_url(value, max_size=1536):
    if not value:
        return value
    if isinstance(value, str) and value.startswith(("/output/", "/assets/", "/api/project-library/")):
        return reference_to_data_url({"url": value}, max_size=max_size)
    return value

def valid_video_image_input(value: str) -> bool:
    if not isinstance(value, str):
        return False
    value = value.strip()
    return (
        value.startswith("http://") or
        value.startswith("https://") or
        value.startswith("asset://") or
        (value.startswith("data:image/") and ";base64," in value)
    )

def valid_apimart_video_image_input(value: str) -> bool:
    if not isinstance(value, str):
        return False
    value = value.strip()
    return value.startswith("http://") or value.startswith("https://") or value.startswith("asset://")

def apply_trusted_asset_prompt_index(prompt: str, image_count: int, video_count: int, audio_count: int) -> str:
    """可信素材模式下，按平台规则在 prompt 里补「图片N/视频N/音频N」索引。
    若用户已手动引用了某类素材（如已写「图片1」），则不重复追加该类。"""
    text = str(prompt or "").strip()
    segments = []
    for label, count in (("图片", image_count), ("视频", video_count), ("音频", audio_count)):
        if count <= 0:
            continue
        if any(f"{label}{i}" in text for i in range(1, count + 1)):
            continue
        segments.append("、".join(f"{label}{i}" for i in range(1, count + 1)))
    if not segments:
        return text
    hint = "参考素材：" + "，".join(segments) + "。"
    return f"{text}\n{hint}" if text else hint

def public_base_url() -> str:
    # 实时读 API/.env 且文件优先：公网隧道重启后地址会变，隧道脚本只改 .env；
    # 启动时 load_env_file 会把旧值复制进 os.environ，若 env 优先会永远读到过期地址
    value = (
        read_api_env_value("PUBLIC_MEDIA_BASE_URL") or
        os.getenv("PUBLIC_MEDIA_BASE_URL") or
        PUBLIC_MEDIA_BASE_URL or
        read_api_env_value("PUBLIC_BASE_URL") or
        os.getenv("PUBLIC_BASE_URL") or
        PUBLIC_BASE_URL or
        ""
    ).strip().rstrip("/")
    if value and re.match(r"^https?://", value, re.I):
        return value
    return ""

def public_media_url_suffix() -> str:
    token = str(os.getenv("PUBLIC_MEDIA_TOKEN") or "").strip()
    return f"?token={urllib.parse.quote(token)}" if token else ""

def local_asset_public_url(value: str) -> str:
    text = str(value or "").strip()
    if not text.startswith(("/output/", "/assets/")):
        return ""
    if not output_file_from_url(text):
        return ""
    base = public_base_url()
    if not base:
        return ""
    return f"{base}{urllib.parse.quote(text, safe='/:?&=%#.-_~')}{public_media_url_suffix()}"

async def openai_video_proxy_public_reference_url(ref) -> str:
    """异步生图（openai-video-proxy）的参考图公网化。
    不走公网隧道（暴露本机服务风险高）：本地文件上传图床（Litterbox/temp.sh，72h 短链），
    与 RS 模式同一通道；真正的公网 URL 原样透传；若手动配置了 PUBLIC_MEDIA_BASE_URL 则作为兜底。"""
    raw = ref.get("url", "") if isinstance(ref, dict) else ref
    text = str(raw or "").strip()
    if not text:
        return ""
    parsed = urllib.parse.urlsplit(text)
    local_path = ""
    if parsed.scheme in {"http", "https"}:
        host = (parsed.hostname or "").lower()
        if host in {"127.0.0.1", "localhost", "::1"} or re.match(r"^(192\.168\.|10\.|172\.(1[6-9]|2\d|3[01])\.)", host):
            local_path = urllib.parse.unquote(parsed.path or "")
        else:
            return text
    elif text.startswith(("/output/", "/assets/")):
        local_path = text
    if local_path and output_file_from_url(local_path):
        upload_error = ""
        try:
            uploaded = await upload_local_video_to_cloud(local_path)
            url = str((uploaded or {}).get("url") or "")
            if url.startswith(("http://", "https://")):
                return url
        except HTTPException as exc:
            upload_error = str(exc.detail)
        public_url = local_asset_public_url(local_path)
        if public_url:
            return public_url
        raise HTTPException(
            status_code=400,
            detail=f"参考图上传图床失败，无法转成公网 URL：{upload_error[:200] or '未知错误'}。请检查网络后重试。"
        )
    raise HTTPException(status_code=400, detail=f"参考图不是公网 URL，无法传给上游：{text[:160]}")

def openai_video_proxy_local_image_path(ref) -> str:
    raw = ref.get("url", "") if isinstance(ref, dict) else ref
    text = str(raw or "").strip()
    if not text:
        return ""
    local_path = ""
    if re.match(r"^https?://", text, re.I):
        parsed = urllib.parse.urlsplit(text)
        host = (parsed.hostname or "").lower()
        if host in {"127.0.0.1", "localhost", "::1"} or re.match(r"^(192\.168\.|10\.|172\.(1[6-9]|2\d|3[01])\.)", host):
            local_path = urllib.parse.unquote(parsed.path or "")
    elif text.startswith(("/output/", "/assets/")):
        local_path = text
    path = output_file_from_url(local_path) if local_path else None
    if not path:
        return ""
    return path if content_type_for_path(path).startswith("image/") else ""

def normalize_apimart_video_reference(value: str) -> str:
    text = str(value or "").strip()
    if valid_apimart_video_image_input(text):
        return text
    return local_asset_public_url(text)

def apimart_video_reference_error(value: str) -> str:
    text = str(value or "").strip()
    if not text:
        return "空的视频地址"
    if text.startswith(("/output/", "/assets/")):
        if not output_file_from_url(text):
            return "这是本地画布文件路径，但后端没有找到对应文件，请重新上传视频后再试。"
        return (
            "这是本地画布文件，APIMart 无法访问 127.0.0.1/局域网路径；"
            "请在 API/.env 配置 PUBLIC_MEDIA_BASE_URL 或 PUBLIC_BASE_URL 为可公网访问的媒体地址（例如内网穿透 HTTPS 地址），"
            "或改用公网 http/https 视频 URL、审核后的 asset:// 地址。"
        )
    if text.startswith("data:") or text.startswith("blob:") or text.startswith("file:"):
        return (
            "APIMart 的 video_urls 不支持 data/blob/file 地址；"
            "请改用公网 http/https 视频 URL，或审核后的 asset:// 地址。"
        )
    return "APIMart 的 video_urls 只支持公网 http/https URL 或 asset:// 私域素材 URL。"

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
    # APIMart VEO 3.1 currently accepts a narrower duration window than
    # the generic UI. Clamp instead of silently forcing every request to 8s.
    return max(4, min(8, value))

def is_apimart_veo31_model(model: str) -> bool:
    return str(model or "").strip().lower().startswith("veo3.1")

def apimart_veo31_model(model: str) -> str:
    value = str(model or "").strip().lower()
    aliases = {
        "veo3.1": "veo3.1-fast",
        "veo3.1-pro": "veo3.1-quality",
        "veo3.1-preview": "veo3.1-fast",
    }
    value = aliases.get(value, value or "veo3.1-fast")
    allowed = {"veo3.1-fast", "veo3.1-quality", "veo3.1-lite"}
    return value if value in allowed else "veo3.1-fast"

def apimart_veo31_aspect(aspect: str) -> str:
    value = str(aspect or "16:9").strip()
    return value if value in {"16:9", "9:16"} else "16:9"

def apimart_veo31_resolution(resolution: str) -> str:
    value = str(resolution or "").strip().lower()
    aliases = {"": "720p", "auto": "720p", "480p": "720p", "780p": "720p", "1080": "1080p", "4k": "4k"}
    value = aliases.get(value, value)
    return value if value in {"720p", "1080p", "4k"} else "720p"

def apimart_upload_file_payload(path: str):
    """Return (filename, bytes, content_type), keeping APIMart VEO images under the documented 10MB limit."""
    max_bytes = 9_500_000
    size = os.path.getsize(path)
    if size <= max_bytes:
        with open(path, "rb") as fh:
            return os.path.basename(path), fh.read(), content_type_for_path(path)
    with Image.open(path) as img:
        img = img.convert("RGBA")
        bg = Image.new("RGB", img.size, (255, 255, 255))
        bg.paste(img, mask=img.split()[-1])
        quality = 92
        while quality >= 62:
            buf = BytesIO()
            bg.save(buf, format="JPEG", quality=quality, optimize=True)
            data = buf.getvalue()
            if len(data) <= max_bytes:
                name = os.path.splitext(os.path.basename(path))[0] + ".jpg"
                return name, data, "image/jpeg"
            quality -= 8
    raise ValueError("图片超过 10MB，且压缩后仍无法满足 VEO3.1 图片限制")

def invalid_video_image_preview(value: str) -> str:
    text = str(value or "")
    if text.startswith("data:"):
        return text.split(";base64,", 1)[0] + ";base64,..."
    return text[:120]

def extract_apimart_asset_url(payload):
    if isinstance(payload, list):
        for item in payload:
            found = extract_apimart_asset_url(item)
            if found:
                return found
        return ""
    if not isinstance(payload, dict):
        return ""
    url_keys = ("url", "asset_url", "assetUrl", "uri", "file_url", "fileUrl")
    for key in url_keys:
        value = str(payload.get(key) or "").strip()
        if valid_apimart_video_image_input(value):
            return value
    id_keys = ("asset_id", "assetId", "file_id", "fileId", "id")
    for key in id_keys:
        value = str(payload.get(key) or "").strip()
        if value:
            return value if value.startswith("asset://") else f"asset://{value}"
    for key in ("data", "file", "asset", "result"):
        found = extract_apimart_asset_url(payload.get(key))
        if found:
            return found
    return ""

def apimart_upload_payload_from_bytes(data: bytes, mime: str, name_hint: str = "image"):
    """把内存中的图片字节按 APIMart 的 10MB 限制压缩为可上传 payload。"""
    max_bytes = 9_500_000
    ext = mimetypes.guess_extension(mime or "image/png") or ".png"
    if len(data) <= max_bytes and (mime or "").lower() in ("image/png", "image/jpeg", "image/webp"):
        return f"{name_hint}{ext}", data, (mime or "image/png")
    with Image.open(BytesIO(data)) as img:
        has_alpha = img.mode in ("RGBA", "LA") or (img.mode == "P" and "transparency" in img.info)
        if has_alpha:
            base = img.convert("RGBA")
            bg = Image.new("RGB", base.size, (255, 255, 255))
            bg.paste(base, mask=base.split()[-1])
            target = bg
        else:
            target = img.convert("RGB")
        quality = 92
        while quality >= 62:
            buf = BytesIO()
            target.save(buf, format="JPEG", quality=quality, optimize=True)
            payload = buf.getvalue()
            if len(payload) <= max_bytes:
                return f"{name_hint}.jpg", payload, "image/jpeg"
            quality -= 8
    raise ValueError("data URL 图片超过 10MB，且压缩后仍无法满足 APIMart 限制")

def apimart_upload_raw_file_payload(path: str):
    with open(path, "rb") as fh:
        return os.path.basename(path), fh.read(), content_type_for_path(path)

APIMART_UPLOAD_RETRY_ATTEMPTS = 3

def is_transient_tls_error(exc) -> bool:
    """识别可重试的瞬时 TLS/传输错误，如 SSLV3_ALERT_BAD_RECORD_MAC、EOF occurred 等，
    这类错误多由连接池中被污染/复用坏掉的 TLS 连接引起，换新连接重试通常即可成功。"""
    if isinstance(exc, httpx.TransportError):
        return True
    msg = f"{type(exc).__name__}: {exc}".upper()
    return any(token in msg for token in (
        "SSL", "BAD RECORD MAC", "EOF OCCURRED", "DECRYPTION FAILED", "WRONG VERSION NUMBER",
    ))

async def apimart_upload_post(client, upload_url, headers, file_tuple, timeout=60):
    """上传文件到 APIMart，对瞬时 TLS 错误自动重试；重试时改用全新连接，避免复用坏掉的 TLS 连接。
    file_tuple 形如 (filename, content_bytes, content_type)，content 为已读入内存的 bytes，可跨重试复用。"""
    last_exc = None
    for attempt in range(APIMART_UPLOAD_RETRY_ATTEMPTS):
        files = {"file": file_tuple}
        try:
            if attempt == 0:
                return await client.post(upload_url, headers=headers, files=files, timeout=timeout)
            async with httpx.AsyncClient(
                timeout=httpx.Timeout(connect=20.0, read=max(120.0, float(timeout)), write=120.0, pool=20.0),
                follow_redirects=True,
            ) as fresh:
                return await fresh.post(upload_url, headers=headers, files=files, timeout=timeout)
        except Exception as e:
            if not is_transient_tls_error(e) or attempt == APIMART_UPLOAD_RETRY_ATTEMPTS - 1:
                raise
            last_exc = e
            print(f"APIMart 上传遇到瞬时 TLS 错误，换新连接重试（第 {attempt + 1} 次）：{e}")
            await asyncio.sleep(0.6 * (attempt + 1))
    if last_exc:
        raise last_exc

async def upload_image_for_apimart(client, provider, ref_url: str) -> str:
    """把本地图片转成上游可接受的输入。
    按 APIMart 文档上传到 /v1/uploads/images，拿到可用于生成接口的 http/https URL。
    绝不把 /output/* 或 /assets/* 这类本地路径直接传给上游。
    返回上游可用 URL；返回值以 "ERR:" 开头表示具体失败原因（供前端展示）。"""
    ref_url = str(ref_url or "").strip()
    if not ref_url:
        return "ERR:空地址"
    # 已经是网络 URL 或 asset:// → 直接可用，无需上传
    if ref_url.startswith("http://") or ref_url.startswith("https://") or ref_url.startswith("asset://"):
        return ref_url
    base_url = video_api_root(provider)
    upload_url = f"{base_url}/v1/uploads/images"
    # data URL: 解码后直接上传到 APIMart
    if ref_url.startswith("data:"):
        try:
            if ";base64," not in ref_url:
                return "ERR:不支持的 data URL（缺少 base64 段）"
            header, encoded = ref_url.split(";base64,", 1)
            mime = header.split(":", 1)[1].split(";", 1)[0] if ":" in header else "image/png"
            raw = base64.b64decode(encoded)
            filename, content, ct = apimart_upload_payload_from_bytes(raw, mime, name_hint="canvas_image")
            resp = await apimart_upload_post(client, upload_url, api_headers(json_body=False, provider=provider), (filename, content, ct), timeout=60)
            if resp.status_code in (200, 201):
                rj = resp.json()
                url = extract_apimart_asset_url(rj)
                if valid_apimart_video_image_input(url):
                    return url
                print(f"APIMart 上传 data URL 返回中未找到可用 asset/url: {str(rj)[:300]}")
                return "ERR:APIMart 上传响应未包含可用 URL"
            print(f"APIMart 上传 data URL 失败 ({resp.status_code}): {resp.text[:300]}")
            return f"ERR:APIMart 上传失败({resp.status_code})"
        except ValueError as e:
            return f"ERR:{e}"
        except Exception as e:
            print(f"APIMart 上传 data URL 异常: {e}")
            return f"ERR:上传异常 {e}"
    # 本地画布文件或内部项目媒体句柄：确认真实文件后上传，绝不把句柄发给上游。
    path = local_media_adapter_path(ref_url, "image")
    if path:
        try:
            filename, content, ct = apimart_upload_file_payload(path)
            resp = await apimart_upload_post(client, upload_url, api_headers(json_body=False, provider=provider), (filename, content, ct), timeout=60)
            if resp.status_code in (200, 201):
                rj = resp.json()
                url = extract_apimart_asset_url(rj)
                if valid_apimart_video_image_input(url):
                    return url
                print(f"APIMart 文件上传返回中未找到可用 asset/url: {str(rj)[:300]}")
                return "ERR:APIMart 上传响应未包含可用 URL"
            print(f"APIMart 文件上传失败 ({resp.status_code}): {resp.text[:300]}")
            return f"ERR:APIMart 上传失败({resp.status_code})"
        except ValueError as e:
            return f"ERR:{e}"
        except Exception as e:
            print(f"APIMart 文件上传异常: {e}")
            return f"ERR:上传异常 {e}"
    return "ERR:不支持的图片来源（仅支持 http/https/asset/data 或应用本地媒体）"

async def upload_video_for_apimart(client, provider, ref_url: str) -> str:
    """尽力把本地参考视频转换为 APIMart 可接受的 http/https 或 asset:// URL。
    文档只公开了图片上传；如果视频上传端点不可用，会回退到 PUBLIC_BASE_URL 方案。"""
    ref_url = str(ref_url or "").strip()
    if not ref_url:
        return "ERR:空地址"
    if valid_apimart_video_image_input(ref_url):
        return ref_url
    public_url = local_asset_public_url(ref_url)
    if public_url:
        return public_url
    path = local_media_adapter_path(ref_url, "video")
    if not path:
        return f"ERR:{apimart_video_reference_error(ref_url)}"
    if not ref_url.startswith(f"{PROJECT_MEDIA_ADAPTER_SCHEME}://") and str(os.getenv("APIMART_TRY_VIDEO_UPLOAD") or "").strip().lower() not in {"1", "true", "yes", "on"}:
        return f"ERR:{apimart_video_reference_error(ref_url)}"
    base_url = video_api_root(provider)
    filename, content, content_type = apimart_upload_raw_file_payload(path)
    upload_paths = ("/v1/uploads/videos", "/v1/uploads/files", "/v1/uploads/images")
    last_error = ""
    for upload_path in upload_paths:
        upload_url = f"{base_url}{upload_path}"
        try:
            files = {"file": (filename, content, content_type)}
            resp = await client.post(upload_url, headers=api_headers(json_body=False, provider=provider), files=files, timeout=180)
            if resp.status_code in (200, 201):
                rj = resp.json()
                url = extract_apimart_asset_url(rj)
                if valid_apimart_video_image_input(url):
                    return url
                last_error = "上传响应未包含可用 URL"
                print(f"APIMart 视频上传返回中未找到可用 asset/url ({upload_path}): {str(rj)[:300]}")
                continue
            last_error = f"{upload_path} 返回 {resp.status_code}: {resp.text[:200]}"
            print(f"APIMart 视频上传失败 {last_error}")
        except Exception as e:
            last_error = f"{upload_path} 异常：{e}"
            print(f"APIMart 视频上传异常: {last_error}")
    return f"ERR:APIMart 未提供可用的视频文件上传入口（{last_error}）。请配置 PUBLIC_BASE_URL，或使用公网 http/https / asset:// 视频地址。"

async def upload_audio_for_apimart(client, provider, ref_url: str) -> str:
    """把本地参考音频转换为 APIMart 可接受的 http/https 或 asset:// URL。
    优先用公网地址（PUBLIC_BASE_URL），否则尝试上传到 APIMart 文件端点。
    返回值以 "ERR:" 开头表示失败原因。"""
    ref_url = str(ref_url or "").strip()
    if not ref_url:
        return "ERR:空地址"
    if valid_apimart_video_image_input(ref_url):
        return ref_url
    public_url = local_asset_public_url(ref_url)
    if public_url:
        return public_url
    base_url = video_api_root(provider)
    upload_paths = ("/v1/uploads/audios", "/v1/uploads/files", "/v1/uploads/images")
    last_error = ""
    if ref_url.startswith("data:"):
        if ";base64," not in ref_url:
            return "ERR:不支持的 data URL（缺少 base64 段）"
        header, encoded = ref_url.split(";base64,", 1)
        mime = header.split(":", 1)[1].split(";", 1)[0] if ":" in header else "audio/mpeg"
        try:
            raw = base64.b64decode(encoded)
        except Exception as exc:
            return f"ERR:音频 data URL 解码失败：{exc}"
        ext = mimetypes.guess_extension(mime) or ".mp3"
        filename, content, content_type = (f"canvas_audio{ext}", raw, mime or "audio/mpeg")
    else:
        path = local_media_adapter_path(ref_url, "audio")
        if not path:
            return f"ERR:{apimart_video_reference_error(ref_url)}"
        filename, content, content_type = apimart_upload_raw_file_payload(path)
    for upload_path in upload_paths:
        upload_url = f"{base_url}{upload_path}"
        try:
            files = {"file": (filename, content, content_type)}
            resp = await client.post(upload_url, headers=api_headers(json_body=False, provider=provider), files=files, timeout=180)
            if resp.status_code in (200, 201):
                rj = resp.json()
                url = extract_apimart_asset_url(rj)
                if valid_apimart_video_image_input(url):
                    return url
                last_error = "上传响应未包含可用 URL"
                continue
            last_error = f"{upload_path} 返回 {resp.status_code}: {resp.text[:200]}"
        except Exception as exc:
            last_error = f"{upload_path} 异常：{exc}"
    return f"ERR:APIMart 未提供可用的音频文件上传入口（{last_error}）。请配置 PUBLIC_BASE_URL，或使用公网 http/https / asset:// 音频地址。"

async def upload_media_for_apimart(client, provider, ref_url: str, kind: str) -> str:
    """按 kind 分派到对应的 APIMart 上传器，拿回上游可用的 http/https/asset:// URL。"""
    if kind == "video":
        return await upload_video_for_apimart(client, provider, ref_url)
    if kind == "audio":
        return await upload_audio_for_apimart(client, provider, ref_url)
    return await upload_image_for_apimart(client, provider, ref_url)

def apimart_avatar_asset_type(kind: str) -> str:
    return {"video": "Video", "audio": "Audio"}.get(str(kind or "").lower(), "Image")

def extract_apimart_avatar_asset_uri(payload) -> str:
    """从 /v1/tasks 审核结果里取出 asset://<id> 形式的可信素材 URI。"""
    if isinstance(payload, list):
        for item in payload:
            found = extract_apimart_avatar_asset_uri(item)
            if found:
                return found
        return ""
    if not isinstance(payload, dict):
        return ""
    for key in ("asset_url", "assetUrl", "uri", "url"):
        value = str(payload.get(key) or "").strip()
        if value.startswith("asset://"):
            return value
    for key in ("usable_assets", "assets", "result", "data"):
        found = extract_apimart_avatar_asset_uri(payload.get(key))
        if found:
            return found
    asset_id = str(payload.get("asset_id") or payload.get("assetId") or "").strip()
    if asset_id:
        return f"asset://{asset_id}"
    return ""

async def submit_apimart_avatar_asset(provider, public_url: str, name: str, kind: str, project_name: str = "default", group_name: str = "") -> str:
    """把一个公网可访问的素材提交到 APIMart private-avatar 审核，立即返回任务 ID（不阻塞轮询）。"""
    base_url = video_api_root(provider)
    if not base_url:
        raise HTTPException(status_code=400, detail=f"{provider.get('name') or provider['id']} 未配置 Base URL")
    register_url = f"{base_url}/v1/seedance2/private-avatar"
    body = {
        "project_name": str(project_name or "default").strip() or "default",
        "asset_type": apimart_avatar_asset_type(kind),
        "group": {"name": (group_name or name or "数字人素材")[:60]},
        "assets": [{"url": public_url, "name": (name or "asset")[:60]}],
    }
    async with httpx.AsyncClient(timeout=120) as client:
        resp = await client.post(register_url, headers=api_headers(provider=provider), json=body, timeout=120)
        if resp.status_code not in (200, 201):
            raise HTTPException(status_code=502, detail=f"APIMart 数字人注册失败（{resp.status_code}）：{resp.text[:300]}")
        data = resp.json()
        task = data.get("data") if isinstance(data.get("data"), dict) else data
        task_id = str(task.get("id") or task.get("task_id") or "").strip()
        if not task_id:
            raise HTTPException(status_code=502, detail=f"APIMart 数字人注册返回中未找到任务 ID：{str(data)[:300]}")
        return task_id

AVATAR_TASK_DONE_STATUSES = {"completed", "complete", "succeeded", "success", "active", "done"}
AVATAR_TASK_FAIL_STATUSES = {"failed", "fail", "error", "rejected", "canceled", "cancelled", "expired"}

async def check_apimart_avatar_task(provider, task_id: str) -> Dict[str, Any]:
    """查询一次 APIMart 审核任务。返回 {status: Active/Processing/Failed, asset_uri, detail}。"""
    base_url = video_api_root(provider)
    if not base_url:
        raise HTTPException(status_code=400, detail=f"{provider.get('name') or provider['id']} 未配置 Base URL")
    task_url = f"{base_url}/v1/tasks/{task_id}"
    async with httpx.AsyncClient(timeout=60) as client:
        resp = await client.get(task_url, headers=api_headers(provider=provider), timeout=60)
        if resp.status_code not in (200, 201):
            raise HTTPException(status_code=502, detail=f"查询审核状态失败（{resp.status_code}）：{resp.text[:200]}")
        payload = resp.json()
    node = payload.get("data") if isinstance(payload.get("data"), dict) else payload
    status = str(node.get("status") or "").strip().lower()
    if status in AVATAR_TASK_DONE_STATUSES:
        asset_uri = extract_apimart_avatar_asset_uri(payload)
        if not asset_uri:
            return {"status": "Failed", "asset_uri": "", "detail": "审核完成，但未返回可用的 asset:// 地址（可能部分素材被拒）。"}
        return {"status": "Active", "asset_uri": asset_uri, "detail": ""}
    if status in AVATAR_TASK_FAIL_STATUSES:
        return {"status": "Failed", "asset_uri": "", "detail": f"审核未通过（{status}）。"}
    return {"status": "Processing", "asset_uri": "", "detail": "审核中"}

# ---- 火山 Ark 私域素材资产（Assets）API：AK/SK 签名 V4 + CreateAssetGroup/CreateAsset/GetAsset ----
VOLCENGINE_ARK_ASSET_HOST = "open.volcengineapi.com"
VOLCENGINE_ARK_ASSET_SERVICE = "ark"
VOLCENGINE_ARK_ASSET_REGION = "cn-beijing"
VOLCENGINE_ARK_ASSET_VERSION = "2024-01-01"

def _volc_hmac(key: bytes, msg: str) -> bytes:
    return hmac.new(key, msg.encode("utf-8"), hashlib.sha256).digest()

def volcengine_sign_v4_headers(ak: str, sk: str, action: str, body_str: str,
                               service: str = VOLCENGINE_ARK_ASSET_SERVICE,
                               region: str = VOLCENGINE_ARK_ASSET_REGION,
                               version: str = VOLCENGINE_ARK_ASSET_VERSION,
                               host: str = VOLCENGINE_ARK_ASSET_HOST) -> Dict[str, str]:
    """火山引擎 OpenAPI 签名 V4（POST + JSON body）。返回需随请求发送的鉴权头。"""
    method = "POST"
    content_type = "application/json"
    now = datetime.datetime.now(datetime.timezone.utc)
    x_date = now.strftime("%Y%m%dT%H%M%SZ")
    short_date = x_date[:8]
    payload_hash = hashlib.sha256(body_str.encode("utf-8")).hexdigest()
    # 查询串按键排序：Action < Version
    canonical_query = f"Action={urllib.parse.quote(action, safe='')}&Version={urllib.parse.quote(version, safe='')}"
    canonical_headers = (
        f"content-type:{content_type}\n"
        f"host:{host}\n"
        f"x-content-sha256:{payload_hash}\n"
        f"x-date:{x_date}\n"
    )
    signed_headers = "content-type;host;x-content-sha256;x-date"
    canonical_request = "\n".join([method, "/", canonical_query, canonical_headers, signed_headers, payload_hash])
    algorithm = "HMAC-SHA256"
    credential_scope = f"{short_date}/{region}/{service}/request"
    string_to_sign = "\n".join([
        algorithm, x_date, credential_scope,
        hashlib.sha256(canonical_request.encode("utf-8")).hexdigest(),
    ])
    k_date = _volc_hmac(sk.encode("utf-8"), short_date)
    k_region = _volc_hmac(k_date, region)
    k_service = _volc_hmac(k_region, service)
    k_signing = _volc_hmac(k_service, "request")
    signature = hmac.new(k_signing, string_to_sign.encode("utf-8"), hashlib.sha256).hexdigest()
    authorization = (
        f"{algorithm} Credential={ak}/{credential_scope}, "
        f"SignedHeaders={signed_headers}, Signature={signature}"
    )
    return {
        "Content-Type": content_type,
        "Host": host,
        "X-Date": x_date,
        "X-Content-Sha256": payload_hash,
        "Authorization": authorization,
    }

async def volcengine_ark_asset_call(client, action: str, body: Dict[str, Any]) -> Dict[str, Any]:
    """调用一次火山 Ark Assets OpenAPI，返回 Result 内容；出错抛 HTTPException。"""
    ak = volcengine_access_key_value()
    sk = volcengine_secret_key_value()
    if not ak or not sk:
        raise HTTPException(status_code=400, detail="未配置火山引擎 AK/SK，请在 API 设置中填写 Access Key ID / Secret Access Key。")
    body_str = json.dumps(body, ensure_ascii=False)
    headers = volcengine_sign_v4_headers(ak, sk, action, body_str)
    url = f"https://{VOLCENGINE_ARK_ASSET_HOST}/?Action={urllib.parse.quote(action, safe='')}&Version={urllib.parse.quote(VOLCENGINE_ARK_ASSET_VERSION, safe='')}"
    resp = await client.post(url, headers=headers, content=body_str.encode("utf-8"), timeout=120)
    try:
        payload = resp.json()
    except Exception:
        raise HTTPException(status_code=502, detail=f"火山 {action} 返回非 JSON（{resp.status_code}）：{resp.text[:300]}")
    meta = payload.get("ResponseMetadata") if isinstance(payload, dict) else None
    if isinstance(meta, dict) and isinstance(meta.get("Error"), dict):
        err = meta["Error"]
        code = err.get("Code") or err.get("CodeN") or ""
        msg = err.get("Message") or ""
        raise HTTPException(status_code=502, detail=f"火山 {action} 失败：{code} {msg}".strip())
    if resp.status_code not in (200, 201):
        raise HTTPException(status_code=502, detail=f"火山 {action} 失败（{resp.status_code}）：{resp.text[:300]}")
    result = payload.get("Result") if isinstance(payload, dict) and isinstance(payload.get("Result"), dict) else None
    return result if result is not None else (payload if isinstance(payload, dict) else {})

async def volcengine_ensure_asset_group(client, project_name: str, group_name: str) -> str:
    """复用同名素材组合，没有则新建。返回 GroupId。"""
    name = (group_name or "可信素材").strip()[:60] or "可信素材"
    project_name = (project_name or "default").strip() or "default"
    # 先按 Name 模糊查找复用
    try:
        listed = await volcengine_ark_asset_call(client, "ListAssetGroups", {
            "Filter": {"Name": name, "GroupType": "AIGC"},
            "PageNumber": 1, "PageSize": 10, "ProjectName": project_name,
        })
        for item in (listed.get("Items") or []):
            if str(item.get("Name") or "").strip() == name and str(item.get("ProjectName") or "default") == project_name:
                gid = str(item.get("Id") or "").strip()
                if gid:
                    return gid
    except HTTPException:
        pass  # 查询失败不致命，继续走新建
    created = await volcengine_ark_asset_call(client, "CreateAssetGroup", {
        "Name": name, "Description": name, "ProjectName": project_name,
    })
    gid = str(created.get("Id") or "").strip()
    if not gid:
        raise HTTPException(status_code=502, detail=f"火山 CreateAssetGroup 未返回 GroupId：{str(created)[:200]}")
    return gid

async def submit_volcengine_avatar_asset(public_url: str, name: str, kind: str,
                                         project_name: str = "default", group_name: str = "") -> str:
    """把公网可访问素材提交到火山 Ark 私域素材库（异步）。返回 Asset Id 作为任务 ID。"""
    async with httpx.AsyncClient(timeout=120) as client:
        group_id = await volcengine_ensure_asset_group(client, project_name, group_name)
        created = await volcengine_ark_asset_call(client, "CreateAsset", {
            "GroupId": group_id,
            "URL": public_url,
            "AssetType": apimart_avatar_asset_type(kind),
            "Name": (name or "asset")[:60],
            "ProjectName": (project_name or "default").strip() or "default",
        })
    asset_id = str(created.get("Id") or "").strip()
    if not asset_id:
        raise HTTPException(status_code=502, detail=f"火山 CreateAsset 未返回 Asset Id：{str(created)[:200]}")
    return asset_id

async def check_volcengine_avatar_task(asset_id: str, project_name: str = "default") -> Dict[str, Any]:
    """查询一次火山素材状态。返回 {status: Active/Processing/Failed, asset_uri, detail}。"""
    async with httpx.AsyncClient(timeout=60) as client:
        info = await volcengine_ark_asset_call(client, "GetAsset", {
            "Id": asset_id,
            "ProjectName": (project_name or "default").strip() or "default",
        })
    status = str(info.get("Status") or "").strip()
    if status == "Active":
        return {"status": "Active", "asset_uri": f"asset://{asset_id}", "detail": ""}
    if status == "Failed":
        return {"status": "Failed", "asset_uri": "", "detail": "火山素材处理失败，无法用于推理。"}
    return {"status": "Processing", "asset_uri": "", "detail": "火山素材处理中"}

def volcengine_public_asset_url(url: str) -> str:
    """火山 CreateAsset 要求 URL 公网可访问；本地文件需 PUBLIC_BASE_URL，否则返回 ERR:。"""
    text = str(url or "").strip()
    if text.startswith("http://") or text.startswith("https://"):
        return text
    public = local_asset_public_url(text)
    if public:
        return public
    return "ERR:火山要求素材是公网可访问的 http/https URL；本地画布文件需配置 PUBLIC_BASE_URL/PUBLIC_MEDIA_BASE_URL 暴露为公网地址。"

def local_media_path_for_cloud_upload(ref_url: str, allowed_prefixes=("image/", "video/")) -> str:
    ref_url = str(ref_url or "").strip()
    if not ref_url:
        raise HTTPException(status_code=400, detail="没有可上传的媒体文件")
    if ref_url.startswith("http://") or ref_url.startswith("https://"):
        return ""
    if ref_url.startswith(f"{PROJECT_MEDIA_ADAPTER_SCHEME}://"):
        path = local_media_adapter_path(ref_url)
    elif ref_url.startswith(("/output/", "/assets/", "/api/project-library/")):
        path = output_file_from_url(ref_url)
    else:
        raise HTTPException(status_code=400, detail="云端上传只支持项目或画布里的本地图片/视频文件")
    if not path:
        raise HTTPException(status_code=404, detail="本地媒体文件不存在或已被删除")
    ct = content_type_for_path(path)
    if not any(ct.startswith(prefix) for prefix in allowed_prefixes):
        raise HTTPException(status_code=400, detail="请选择图片或视频文件再上传云端")
    max_bytes = int(os.getenv("TEMP_SH_MAX_BYTES", str(4 * 1024 * 1024 * 1024)))
    size = os.path.getsize(path)
    if size > max_bytes:
        raise HTTPException(status_code=400, detail=f"媒体文件超过云端上传大小限制：{size} bytes")
    return path

def local_video_path_for_cloud_upload(ref_url: str) -> str:
    return local_media_path_for_cloud_upload(ref_url, ("video/",))

async def upload_video_to_litterbox(path: str, source_url: str) -> Dict[str, str]:
    upload_url = os.getenv("LITTERBOX_UPLOAD_URL", "https://litterbox.catbox.moe/resources/internals/api.php").strip() or "https://litterbox.catbox.moe/resources/internals/api.php"
    time_value = os.getenv("LITTERBOX_TIME", "72h").strip() or "72h"
    ct = content_type_for_path(path)
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(connect=20.0, read=600.0, write=600.0, pool=20.0), follow_redirects=True) as client:
            with open(path, "rb") as fh:
                files = {"fileToUpload": (os.path.basename(path), fh, ct)}
                data = {"reqtype": "fileupload", "time": time_value}
                response = await client.post(upload_url, data=data, files=files)
        if not response.is_success:
            raise HTTPException(status_code=response.status_code, detail=f"Litterbox 上传失败：{response.text[:300]}")
        direct_url = response.text.strip().splitlines()[0].strip()
        if not re.match(r"^https?://", direct_url, re.I):
            raise HTTPException(status_code=502, detail=f"Litterbox 返回了无法识别的链接：{response.text[:300]}")
        return {"url": direct_url, "source": source_url, "name": os.path.basename(path), "expires": time_value, "service": "litterbox"}
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Litterbox 上传异常：{exc}") from exc

async def upload_video_to_temp_sh(path: str, source_url: str) -> Dict[str, str]:
    upload_url = os.getenv("TEMP_SH_UPLOAD_URL", "https://temp.sh/upload").strip() or "https://temp.sh/upload"
    ct = content_type_for_path(path)
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(connect=20.0, read=600.0, write=600.0, pool=20.0), follow_redirects=True) as client:
            with open(path, "rb") as fh:
                files = {"file": (os.path.basename(path), fh, ct)}
                response = await client.post(upload_url, files=files)
        if not response.is_success:
            raise HTTPException(status_code=response.status_code, detail=f"Temp.sh 上传失败：{response.text[:300]}")
        direct_url = response.text.strip().splitlines()[0].strip()
        if not re.match(r"^https?://", direct_url, re.I):
            raise HTTPException(status_code=502, detail=f"Temp.sh 返回了无法识别的链接：{response.text[:300]}")
        return {"url": direct_url, "source": source_url, "name": os.path.basename(path), "expires": "3 days", "service": "temp.sh"}
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Temp.sh 上传异常：{exc}") from exc

async def upload_local_video_to_cloud(ref_url: str, service: str = "auto") -> Dict[str, str]:
    ref_url = str(ref_url or "").strip()
    if ref_url.startswith("http://") or ref_url.startswith("https://"):
        return {"url": ref_url, "source": ref_url, "service": "existing"}
    path = local_media_path_for_cloud_upload(ref_url)
    service = str(service or os.getenv("CLOUD_VIDEO_UPLOAD_SERVICE", "auto") or "auto").strip().lower()
    if service in {"litterbox", "catbox"}:
        return await upload_video_to_litterbox(path, ref_url)
    if service in {"temp", "temp.sh", "tempsh"}:
        return await upload_video_to_temp_sh(path, ref_url)
    errors = []
    for name, func in (("litterbox", upload_video_to_litterbox), ("temp.sh", upload_video_to_temp_sh)):
        try:
            return await func(path, ref_url)
        except HTTPException as exc:
            errors.append(f"{name}: {exc.detail}")
    raise HTTPException(status_code=502, detail="云端上传失败：" + "；".join(errors))

async def upload_local_video_to_temp_sh(ref_url: str) -> Dict[str, str]:
    return await upload_local_video_to_cloud(ref_url, "auto")

async def save_ai_image_to_output(image_data, prefix="online_", category="output"):
    filename = f"{prefix}{uuid.uuid4().hex[:10]}.png"
    path = output_path_for(filename, category)
    if image_data["type"] == "b64":
        mime_type = str(image_data.get("mime_type") or "").lower()
        if "jpeg" in mime_type or "jpg" in mime_type:
            filename = filename[:-4] + ".jpg"
            path = output_path_for(filename, category)
        elif "webp" in mime_type:
            filename = filename[:-4] + ".webp"
            path = output_path_for(filename, category)
        with open(path, "wb") as f:
            f.write(base64.b64decode(image_data["value"]))
        return output_url_for(filename, category)
    value = image_data["value"]
    if value.startswith("/output/") or value.startswith("/assets/"):
        return value
    value = rewrite_runninghub_file_url(value)
    try:
        timeout = httpx.Timeout(connect=20.0, read=300.0, write=60.0, pool=20.0)
        async with httpx.AsyncClient(timeout=timeout, follow_redirects=True) as client:
            response = await client.get(value)
            response.raise_for_status()
            content_type = response.headers.get("Content-Type", "")
            if "jpeg" in content_type or "jpg" in content_type:
                filename = filename[:-4] + ".jpg"
                path = output_path_for(filename, category)
            elif "webp" in content_type:
                filename = filename[:-4] + ".webp"
                path = output_path_for(filename, category)
            with open(path, "wb") as f:
                f.write(response.content)
            return output_url_for(filename, category)
    except Exception as e:
        print(f"保存上游图片失败: {e}; url={value}")
        return value

def image_output_meta(url, source_item=None):
    meta = {"url": url, "kind": "image"}
    if not url:
        return meta
    parsed_name = os.path.basename(urllib.parse.urlparse(str(url)).path)
    if parsed_name:
        meta["name"] = parsed_name
    if isinstance(source_item, dict):
        for key in ("natural_w", "natural_h", "width", "height", "w", "h", "layout_w", "layout_h"):
            try:
                value = int(float(source_item.get(key) or 0))
            except (TypeError, ValueError):
                value = 0
            if value > 0:
                meta[key] = value
    path = output_file_from_url(url)
    if path and os.path.exists(path):
        try:
            with Image.open(path) as img:
                width, height = img.size
            if width > 0 and height > 0:
                meta.update({
                    "natural_w": width,
                    "natural_h": height,
                    "width": width,
                    "height": height,
                })
        except Exception:
            pass
    return meta

async def save_remote_video_to_output(url, prefix="video_", category="output"):
    if not url:
        return ""
    if url.startswith("/output/") or url.startswith("/assets/"):
        return url
    video_exts = {".mp4", ".webm", ".mov", ".m4v", ".avi", ".mkv", ".flv"}
    parsed = urllib.parse.urlparse(str(url or "").strip())
    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        return url
    clean_ext = os.path.splitext(parsed.path)[1].lower()
    stem = f"{prefix}{uuid.uuid4().hex[:10]}"
    filename = f"{stem}{clean_ext if clean_ext in video_exts else '.mp4'}"
    path = output_path_for(filename, category)
    try:
        timeout = httpx.Timeout(connect=20.0, read=VIDEO_POLL_TIMEOUT, write=60.0, pool=20.0)
        headers = {
            "User-Agent": "Firefly-Canvas/1.0",
            "Accept": "video/*,application/octet-stream,*/*;q=0.8",
        }
        async with httpx.AsyncClient(timeout=timeout, follow_redirects=True, headers=headers) as client:
            response = await client.get(url)
            response.raise_for_status()
            content_type = (response.headers.get("Content-Type") or "").lower()
            if "text/html" in content_type or "application/json" in content_type:
                raise RuntimeError(f"unexpected video content type: {content_type}")
            ext = clean_ext
            if ext in video_exts:
                filename = f"{stem}{ext}"
                path = output_path_for(filename, category)
            elif "webm" in content_type:
                filename = f"{stem}.webm"
                path = output_path_for(filename, category)
            elif "quicktime" in content_type or "mov" in content_type:
                filename = f"{stem}.mov"
                path = output_path_for(filename, category)
            elif "x-matroska" in content_type or "mkv" in content_type:
                filename = f"{stem}.mkv"
                path = output_path_for(filename, category)
            elif "x-flv" in content_type or "flv" in content_type:
                filename = f"{stem}.flv"
                path = output_path_for(filename, category)
            with open(path, "wb") as f:
                f.write(response.content)
            if os.path.getsize(path) <= 0:
                raise RuntimeError("empty video response")
            return output_url_for(filename, category)
    except Exception as e:
        print(f"保存上游视频失败: {e}")
        try:
            if os.path.exists(path):
                os.remove(path)
        except Exception:
            pass
        return url

def parse_size_pair(size):
    match = re.fullmatch(r"\s*(\d+)\s*[xX*]\s*(\d+)\s*", str(size or ""))
    if not match:
        return 0, 0
    return int(match.group(1)), int(match.group(2))

def snap_size_to_multiple(size, multiple=16):
    width, height = parse_size_pair(size)
    if not width or not height:
        return size
    step = max(1, int(multiple or 16))
    snapped_w = max(step, int(math.ceil(width / step) * step))
    snapped_h = max(step, int(math.ceil(height / step) * step))
    return f"{snapped_w}x{snapped_h}"

CHAT_RATIO_SIZE_OPTIONS = {
    "1:1": ("1024x1024", "1536x1536", "2048x2048"),
    "2:3": ("720x1080", "1024x1536", "1365x2048"),
    "3:2": ("1080x720", "1536x1024", "2048x1365"),
    "3:4": ("1008x1344", "1536x2048", "2448x3264"),
    "4:3": ("1344x1008", "2048x1536", "3264x2448"),
    "9:16": ("720x1280", "1080x1920", "1440x2560"),
    "16:9": ("1280x720", "1920x1080", "2560x1440"),
}

def chat_prompt_size_override(message, current_size=""):
    text = str(message or "")
    direct = re.search(r"(?<!\d)([1-9]\d{2,4})\s*[xX×*]\s*([1-9]\d{2,4})(?!\d)", text)
    if direct:
        width, height = int(direct.group(1)), int(direct.group(2))
        if width >= 256 and height >= 256:
            return f"{width}x{height}"

    normalized = (
        text.replace("：", ":")
        .replace("﹕", ":")
        .replace("∶", ":")
        .replace("比", ":")
        .replace("／", "/")
        .replace("/", ":")
    )
    ratio_match = re.search(r"(?<!\d)(1|2|3|4|9|16)\s*:\s*(1|2|3|4|9|16)(?!\d)", normalized)
    if not ratio_match:
        return ""
    ratio = f"{int(ratio_match.group(1))}:{int(ratio_match.group(2))}"
    options = CHAT_RATIO_SIZE_OPTIONS.get(ratio)
    if not options:
        return ""
    width, height = parse_size_pair(current_size)
    wants_4k = bool(re.search(r"(?i)\b4\s*k\b|4K|超清|超高分辨率", text))
    wants_2k = bool(re.search(r"(?i)\b2\s*k\b|2K|高清|高分辨率", text))
    long_edge = max(width, height)
    if wants_4k or long_edge >= 2400:
        return options[2] if len(options) > 2 else options[-1]
    if wants_2k or long_edge >= 1500:
        return options[1] if len(options) > 1 else options[0]
    return options[0]

# GPT-Image-2 限制：长边最大 3840，主要受最大像素限制（约 829 万 = 3840x2160）。
# 这里只用于上游报错后给出友好的像素上限提示；不对尺寸做任何缩小（用户选什么就原样发送）。
GPT_IMAGE2_MAX_EDGE = 3840
GPT_IMAGE2_MAX_PIXELS = 8_294_400
GPT_IMAGE2_MIN_PIXELS = 655_360

def is_gpt_image_2_model(model):
    raw = str(model or "").strip().lower()
    normalized = re.sub(r"[^a-z0-9]+", "-", raw).strip("-")
    compact = re.sub(r"[^a-z0-9]+", "", raw)
    return (
        normalized == "gpt-image-2"
        or normalized.startswith("gpt-image-2-")
        or normalized.endswith("-gpt-image-2")
        or "-gpt-image-2-" in normalized
        or compact == "gptimage2"
        or compact.startswith("gptimage2")
        or compact.endswith("gptimage2")
    )

def normalize_gpt_image_2_size(size):
    width, height = parse_size_pair(size)
    if not width or not height:
        return size or "auto"
    # 已在 GPT 支持范围内（长边≤3840 且 总像素≤约829万）的尺寸原样返回，不做任何改动。
    if max(width, height) <= GPT_IMAGE2_MAX_EDGE and width * height <= GPT_IMAGE2_MAX_PIXELS:
        return f"{width}x{height}"
    # 超限时按比例等比缩小到 GPT 上限，保持原始宽高比（例如 4096x4096 → ~2864x2864，仍是 1:1）。
    ratio = width / height
    if ratio > 3:
        width = height * 3
    elif ratio < 1 / 3:
        height = width * 3
    scale = min(
        1.0,
        GPT_IMAGE2_MAX_EDGE / max(width, height),
        (GPT_IMAGE2_MAX_PIXELS / max(1, width * height)) ** 0.5,
    )
    width = max(16, int((width * scale) // 16) * 16)
    height = max(16, int((height * scale) // 16) * 16)
    if width * height < GPT_IMAGE2_MIN_PIXELS:
        grow = (GPT_IMAGE2_MIN_PIXELS / max(1, width * height)) ** 0.5
        width = int((width * grow + 15) // 16) * 16
        height = int((height * grow + 15) // 16) * 16
    return f"{width}x{height}"

def gpt_image_2_size_error_message(size):
    width, height = parse_size_pair(size)
    display_size = size or "未指定"
    return (
        f"GPT-Image-2 不支持当前尺寸 {display_size}：它有最大像素限制"
        "（长边最大 3840、总像素约 829 万）。请改用更小的尺寸，"
        "或切换到 nano-banana 生成更高分辨率。"
    )

def gpt_image_2_size_exceeds_supported(size):
    width, height = parse_size_pair(size)
    return bool(width and height and (max(width, height) > GPT_IMAGE2_MAX_EDGE or width * height > GPT_IMAGE2_MAX_PIXELS))

def apimart_size_resolution(size):
    width, height = parse_size_pair(size)
    if not width or not height:
        raw = str(size or "").strip().lower()
        if raw in {"1k", "2k", "4k"}:
            return "1:1", raw
        if re.fullmatch(r"(auto|\d+\s*:\s*\d+)", raw):
            return raw.replace(" ", ""), "1k"
        return "1:1", "1k"
    long_edge = max(width, height)
    pixels = width * height
    if long_edge >= 3000 or pixels > 4_500_000:
        resolution = "4k"
    elif long_edge >= 1800 or pixels > 1_800_000:
        resolution = "2k"
    else:
        resolution = "1k"
    common = [
        (1, 1, "1:1"), (3, 2, "3:2"), (2, 3, "2:3"), (4, 3, "4:3"), (3, 4, "3:4"),
        (5, 4, "5:4"), (4, 5, "4:5"), (16, 9, "16:9"), (9, 16, "9:16"),
        (2, 1, "2:1"), (1, 2, "1:2"), (3, 1, "3:1"), (1, 3, "1:3"),
        (21, 9, "21:9"), (9, 21, "9:21"),
    ]
    ratio = width / height
    best = min(common, key=lambda item: abs(ratio - item[0] / item[1]))
    return best[2], resolution

VOLCENGINE_MIN_PIXELS = 3_686_400
VOLCENGINE_MIN_EDGE = 1536
VOLCENGINE_MAX_EDGE = 4096
VOLCENGINE_RATIO_CHOICES = [
    (1, 1, "1:1"),
    (4, 3, "4:3"),
    (3, 4, "3:4"),
    (16, 9, "16:9"),
    (9, 16, "9:16"),
    (21, 9, "21:9"),
    (9, 21, "9:21"),
    (3, 2, "3:2"),
    (2, 3, "2:3"),
    (5, 4, "5:4"),
    (4, 5, "4:5"),
]

def is_volcengine_seedream_model(model):
    value = str(model or "").strip().lower()
    return "seedream" in value or "doubao-seedream" in value

def normalize_volcengine_size(size, model=""):
    width, height = parse_size_pair(size)
    raw = str(size or "").strip().lower()
    if not width or not height:
        if raw == "4k":
            return "4096x4096"
        if raw == "2k":
            return "2048x2048"
        return "2048x2048" if is_volcengine_seedream_model(model) else (size or "1024x1024")
    if not is_volcengine_seedream_model(model):
        return f"{width}x{height}"
    ratio = width / max(1, height)
    best_ratio = min(VOLCENGINE_RATIO_CHOICES, key=lambda item: abs(ratio - item[0] / item[1]))
    rw, rh = best_ratio[0], best_ratio[1]
    scale = max(
        (VOLCENGINE_MIN_PIXELS / max(1, rw * rh)) ** 0.5,
        VOLCENGINE_MIN_EDGE / max(1, min(rw, rh)),
    )
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
    return f"{snapped_w}x{snapped_h}"

def friendly_image_error_detail(text, size="", model=""):
    text = str(text or "")
    lower_text = text.lower()
    if is_gpt_image_2_model(model) and gpt_image_2_size_exceeds_supported(size):
        return gpt_image_2_size_error_message(size)
    mentions_size = any(token in lower_text for token in ["size", "resolution", "dimension"])
    is_gpt_size_error = is_gpt_image_2_model(model) and mentions_size and (
        "invalid" in lower_text
        or "unsupported" in lower_text
        or "not supported" in lower_text
        or "exceed" in lower_text
        or "must be one of" in lower_text
    )
    m = re.search(r"longest edge must be less than or equal to (\d+)", text)
    if m and is_gpt_image_2_model(model):
        limit = m.group(1)
        return f"GPT-Image-2 不支持当前尺寸 {size or '未指定'}：最长边超过 {limit}px。如果需要更高分辨率，请切换到 nano-banana；继续使用 GPT 时请调低分辨率。"
    if m:
        limit = m.group(1)
        return f"该模型不支持当前分辨率：最长边超过 {limit}px。请把图片分辨率调低（例如换到 2K 或更小），或更换支持高分辨率的模型。"
    if "image size must be at least" in lower_text:
        pixel_match = re.search(r"at least (\d+) pixels", lower_text)
        pixels = pixel_match.group(1) if pixel_match else "3686400"
        return f"该模型要求更高分辨率，当前尺寸 {size or '过小'} 不满足最低像素要求（至少 {pixels} 像素）。火山 Seedream 5.0 建议从 2K 起步。"
    if is_gpt_size_error or (("invalid size" in lower_text or "invalid_value" in lower_text) and is_gpt_image_2_model(model)):
        return gpt_image_2_size_error_message(size)
    if "invalid size" in lower_text or "invalid_value" in lower_text:
        return f"该模型不支持当前尺寸：{size or '未指定'}。请尝试更换分辨率或模型。"
    if "inputtextsensitivecontentdetected" in lower_text or "policyviolation" in lower_text or "copyright restrictions" in lower_text:
        return "上游内容安全拦截了这段提示词，原因偏向版权/敏感内容限制。请改写提示词，避免直接出现具体 IP、角色名、品牌名、影视/动漫作品名，改成风格特征描述再试。"
    if "rejected by the safety system" in lower_text or "image_generation_user_error" in lower_text or "safety system" in lower_text or "content_policy_violation" in lower_text or "content policy" in lower_text:
        return "上游（Azure/OpenAI 系）内容安全系统拒绝了本次生图请求。可能是提示词或参考图触发了内容审核。请改写提示词、避免敏感/暴力/成人/名人/版权角色等描述；若使用了人物参考图，可换一张图再试。这是上游平台的审核策略，并非本系统报错。"
    if "rate limit" in lower_text or "429" in lower_text:
        return "请求过于频繁，已被上游限流，请稍后再试。"
    if "unauthorized" in lower_text or "401" in lower_text:
        return "API Key 无效或已过期，请到「API 设置」检查 Key。"
    if "model_not_found" in lower_text or "channel not found" in lower_text:
        return f"上游平台找不到模型「{model}」可用通道。可能该模型未在此账号开通，请换一个已开通的模型。"
    return ""

def parse_error_payload_text(text):
    body = str(text or "").strip()
    if not body:
        return {}
    try:
        parsed = json.loads(body)
    except Exception:
        return {}
    return parsed if isinstance(parsed, dict) else {}

def friendly_chat_error_detail(text, model="", provider=None):
    raw_text = str(text or "")
    lower_text = raw_text.lower()
    payload = parse_error_payload_text(raw_text)
    error = payload.get("error") if isinstance(payload.get("error"), dict) else {}
    code = str(error.get("code") or payload.get("code") or "").strip()
    message = str(error.get("message") or payload.get("message") or "").strip()
    code_lc = code.lower()
    message_lc = message.lower()
    model_name = str(model or "").strip()

    if is_volcengine_provider(provider):
        if code_lc in {"invalidendpointormodel.notfound", "invalidendpointormodel.modelidaccessdisabled"}:
            provider_name = provider.get("name") or provider.get("id") or "火山方舟"
            return (
                f"{provider_name} 当前不接受模型名「{model_name or '未指定'}」直接调用聊天接口，"
                f"请在火山方舟控制台创建并使用推理接入点 ID（形如 `ep-...`）作为聊天模型。\n\n"
                f"补充说明：`/api/v3/models` 能拉到公开模型列表，但你的账号未必能直接用这些模型名调用 `/chat/completions`；"
                f"很多账号只允许传自己已开通的 `ep-...` 接入点。"
            )
        if "does not exist or you do not have access to it" in message_lc:
            return (
                f"火山方舟找不到或无权访问聊天模型「{model_name or '未指定'}」。"
                f"如果你现在填的是模型名，请改成已开通的推理接入点 ID（`ep-...`）；"
                f"如果已经是 `ep-...`，请检查这个接入点是否绑定了聊天模型、区域是否正确、以及账号是否有调用权限。"
            )
    if "unauthorized" in lower_text or "401" in lower_text:
        return "API Key 无效或已过期，请到「API 设置」检查 Key。"
    if "rate limit" in lower_text or "429" in lower_text:
        return "请求过于频繁，已被上游限流，请稍后再试。"
    return ""

async def generate_modelscope_provider_image(prompt, size, model, reference_images=None, provider=None):
    clean_token = modelscope_api_key()
    if not clean_token:
        raise HTTPException(status_code=400, detail="未配置 ModelScope API Key，请在 API 设置中填写。")
    width, height = parse_size_pair(size)
    refs = []
    for ref in (reference_images or [])[:ONLINE_IMAGE_REFERENCE_MAX]:
        if not ref.get("url"):
            continue
        # 本地参考图转为 data URL；前端已生成的 data URL 保持原样，贴近旧版稳定链路。
        refs.append(modelscope_image_url(ref.get("url", ""), max_size=1536))
    headers = {
        "Authorization": f"Bearer {clean_token}",
        "Content-Type": "application/json",
        "X-ModelScope-Async-Mode": "true",
    }
    payload = {
        "model": selected_model(model, MODELSCOPE_DEFAULT_IMAGE_MODEL),
        "prompt": prompt.strip(),
    }
    if width and height:
        payload["width"] = width
        payload["height"] = height
        payload["size"] = f"{width}x{height}"
    if refs:
        payload["image_url"] = refs

    api_root = modelscope_image_api_root()
    async with httpx.AsyncClient(timeout=AI_REQUEST_TIMEOUT) as client:
        submit_res = await client.post(f"{api_root}/images/generations", headers=headers, json=payload)
        submit_res.raise_for_status()
        raw = submit_res.json()
        task_id = raw.get("task_id")
        if not task_id:
            try:
                return extract_image(raw), raw
            except HTTPException:
                raise HTTPException(status_code=502, detail=f"ModelScope 未返回 task_id：{raw}")

        deadline = time.monotonic() + AI_REQUEST_TIMEOUT
        last_payload = raw
        while time.monotonic() < deadline:
            await asyncio.sleep(IMAGE_POLL_INTERVAL)
            result = await client.get(
                f"{api_root}/tasks/{task_id}",
                headers={**headers, "X-ModelScope-Task-Type": "image_generation"},
            )
            result.raise_for_status()
            data = result.json()
            last_payload = data
            status = str(data.get("task_status") or "").upper()
            if status == "SUCCEED":
                images = data.get("output_images") or []
                if not images:
                    raise HTTPException(status_code=502, detail=f"ModelScope 成功但没有返回图片：{data}")
                return {"type": "url", "value": images[0]}, data
            if status in {"FAILED", "FAIL", "ERROR", "CANCELED", "CANCELLED", "TIMEOUT", "REVOKED"}:
                detail = data.get("error_info") or data.get("message") or data.get("detail") or str(data)
                raise HTTPException(status_code=502, detail=f"ModelScope 任务失败：{detail}")
        raise HTTPException(status_code=504, detail=f"ModelScope 生图任务超时：{last_payload}")

def gemini_model_name(model):
    value = selected_model(model, "gemini-3-pro-image-preview").strip()
    return value[len("models/"):] if value.startswith("models/") else value

GEMINI_IMAGE_ASPECT_RATIOS = (
    (1, 1, "1:1"), (1, 4, "1:4"), (1, 8, "1:8"),
    (2, 3, "2:3"), (3, 2, "3:2"), (3, 4, "3:4"),
    (4, 1, "4:1"), (4, 3, "4:3"), (4, 5, "4:5"),
    (5, 4, "5:4"), (8, 1, "8:1"), (9, 16, "9:16"),
    (16, 9, "16:9"), (21, 9, "21:9"),
)

def gemini_supported_aspect_ratio(size, fallback="1:1"):
    width, height = parse_size_pair(size)
    if not width or not height:
        match = re.fullmatch(r"\s*(\d+)\s*:\s*(\d+)\s*", str(size or ""))
        if match:
            width, height = int(match.group(1)), int(match.group(2))
    if not width or not height:
        return fallback
    ratio = width / height
    # Compare proportionally rather than by raw decimal distance so portrait
    # and landscape ratios are treated symmetrically.
    return min(
        GEMINI_IMAGE_ASPECT_RATIOS,
        key=lambda item: abs(math.log(ratio / (item[0] / item[1]))),
    )[2]

def banana_image_request_params(size):
    return {
        "aspect_ratio": gemini_supported_aspect_ratio(size),
    }

def image_provider_model_ids(provider):
    return {
        str(item or "").strip().lower()
        for item in ((provider or {}).get("image_models") or [])
        if str(item or "").strip()
    }

def banana_model_family(model):
    value = str(model or "").strip().lower()
    if value in {"banana", "nano-banana-pro"}:
        return "nano-banana-pro"
    match = re.fullmatch(r"(nano-banana-(?:pro|2))(?:-(?:1k|2k|4k))?", value)
    return match.group(1) if match else ""

def looks_like_gemini_image_model(model):
    value = str(model or "").strip().lower()
    return value.startswith("gemini-") and "image" in value

def route_openai_image_request(provider, model, size):
    requested_model = selected_model(model, IMAGE_MODEL)
    family = banana_model_family(requested_model)
    params = {}
    routed_model = requested_model
    if family:
        params = banana_image_request_params(size)
        available = image_provider_model_ids(provider)
        _, resolution = apimart_size_resolution(size)
        target_model = family if resolution == "1k" else f"{family}-{resolution}"
        if not available or target_model in available:
            routed_model = target_model
        elif family in available:
            routed_model = family
        elif requested_model.lower() not in available:
            routed_model = target_model
    elif looks_like_gemini_image_model(requested_model):
        # Some OpenAI-compatible gateways expose Gemini image IDs directly.
        # They still require a supported Gemini aspect_ratio.
        params = banana_image_request_params(size)
    return {
        "requested_model": requested_model,
        "model": routed_model,
        "params": params,
        "family": family or ("gemini-image" if params else ""),
    }

def gemini_endpoint_url(provider, model):
    model_name = urllib.parse.quote(gemini_model_name(model), safe="")
    return provider_endpoint_url(provider, "image_generation_endpoint", f"/v1beta/models/{model_name}:generateContent")

def gemini_image_config(size, image_parameters=None):
    explicit = image_parameters if isinstance(image_parameters, dict) else {}
    explicit_aspect = str(explicit.get("aspect_ratio") or "").strip()
    explicit_resolution = str(explicit.get("resolution") or "").strip()
    if explicit_aspect or explicit_resolution:
        return {
            "aspectRatio": explicit_aspect or gemini_supported_aspect_ratio(size),
            "imageSize": (explicit_resolution or apimart_size_resolution(size)[1]).upper(),
        }
    width, height = parse_size_pair(size)
    if not width or not height:
        raw = str(size or "").strip().upper()
        if raw in {"1K", "2K", "4K"}:
            return {"aspectRatio": "1:1", "imageSize": raw}
        if re.fullmatch(r"\d+\s*:\s*\d+", raw):
            return {"aspectRatio": gemini_supported_aspect_ratio(raw), "imageSize": "1K"}
        return {"aspectRatio": "1:1", "imageSize": "2K"}
    _, resolution = apimart_size_resolution(size)
    aspect_ratio = gemini_supported_aspect_ratio(size)
    return {"aspectRatio": aspect_ratio, "imageSize": resolution.upper()}

def gemini_reference_part(ref):
    value = reference_to_data_url(ref, max_size=1536)
    if not value:
        return None
    if isinstance(value, str) and value.startswith("data:image/") and ";base64," in value:
        header, encoded = value.split(";base64,", 1)
        mime_type = header.replace("data:", "", 1) or "image/png"
        return {"inlineData": {"mimeType": mime_type, "data": encoded}}
    if isinstance(value, str) and value.startswith(("http://", "https://")):
        return {"fileData": {"mimeType": "image/png", "fileUri": value}}
    return None

async def generate_gemini_provider_image(prompt, size, model, reference_images=None, provider=None, image_parameters=None):
    model_name = gemini_model_name(model)
    endpoint = gemini_endpoint_url(provider, model_name)
    parts = [{"text": prompt.strip()}]
    for ref in (reference_images or [])[:ONLINE_IMAGE_REFERENCE_MAX]:
        part = gemini_reference_part(ref)
        if part:
            parts.append(part)
    body = {
        "contents": [{"role": "user", "parts": parts}],
        "generationConfig": {
            "responseModalities": ["TEXT", "IMAGE"],
            "imageConfig": gemini_image_config(size, image_parameters),
        },
    }
    async with httpx.AsyncClient(timeout=httpx.Timeout(connect=20.0, read=1800.0, write=120.0, pool=20.0)) as client:
        response = await client.post(endpoint, headers=api_headers(provider=provider), json=body)
        response.raise_for_status()
        raw = response.json()
        return extract_image(raw), raw

def volcengine_endpoint_url(provider):
    return provider_endpoint_url(provider, "image_generation_endpoint", "/api/v3/images/generations")

def volcengine_image_payload(ref):
    value = reference_to_data_url(ref, max_size=1536)
    if not value:
        return None
    return value

async def generate_volcengine_provider_image(prompt, size, model, reference_images=None, provider=None, preserve_size=False):
    endpoint = volcengine_endpoint_url(provider)
    if not preserve_size:
        size = normalize_volcengine_size(size, model)
    body = {
        "model": model,
        "prompt": prompt,
        "size": size,
        "response_format": "url",
    }
    images = [volcengine_image_payload(ref) for ref in (reference_images or [])[:ONLINE_IMAGE_REFERENCE_MAX]]
    images = [value for value in images if value]
    if images:
        body["image"] = images
    async with httpx.AsyncClient(timeout=httpx.Timeout(connect=20.0, read=1800.0, write=120.0, pool=20.0)) as client:
        response = await client.post(endpoint, headers=api_headers(provider=provider), json=body)
        response.raise_for_status()
        raw = response.json()
        return extract_image(raw), raw

def runninghub_api_headers(provider, use_wallet=True):
    api_key = runninghub_api_key(provider, use_wallet=use_wallet)
    if not api_key:
        raise HTTPException(status_code=400, detail="未配置 RunningHub API Key，请在 API 设置中填写。")
    return {"Authorization": bearer_auth_value(api_key), "Accept": "application/json", "Content-Type": "application/json"}

def runninghub_json_headers(provider, use_wallet=True):
    return runninghub_api_headers(provider, use_wallet=use_wallet)

def runninghub_provider():
    return get_api_provider_exact("runninghub")

def runninghub_api_key(provider=None, use_wallet=False, prefer_wallet=False):
    provider = provider or runninghub_provider()
    provider_id = (provider or {}).get("id") or "runninghub"
    free_key = str((provider or {}).get("api_key") or "").strip() or provider_env_key_value(provider_id)
    wallet_key = str((provider or {}).get("wallet_api_key") or "").strip() or runninghub_wallet_key_value()
    if use_wallet and not wallet_key:
        raise HTTPException(status_code=400, detail="未配置 RunningHub 账户余额 API Key。标准模型接口只能走账户余额，请在 RH 设置中填写账户余额 Key。")
    api_key = wallet_key if (use_wallet or prefer_wallet) and wallet_key else free_key
    if not api_key:
        raise HTTPException(status_code=400, detail="未配置 RunningHub API Key，请在 RH 设置中填写。")
    return api_key

def runninghub_app_headers(json_body=True, use_wallet=False, provider=None):
    headers = {"Host": "www.runninghub.cn"}
    provider = provider or runninghub_provider()
    if provider:
        api_key = runninghub_api_key(provider, use_wallet=use_wallet)
        if api_key:
            headers["Authorization"] = bearer_auth_value(api_key)
    if json_body:
        headers["Content-Type"] = "application/json"
    return headers

def runninghub_local_asset_path(url):
    text = str(url or "").strip()
    if not text:
        return None
    if text.startswith("/assets/input/") or text.startswith("/input/"):
        clean = urllib.parse.unquote(text.split("?", 1)[0]).replace("\\", "/")
        rel = clean[len("/assets/input/"):] if clean.startswith("/assets/input/") else clean[len("/input/"):]
        root = OUTPUT_INPUT_DIR
    elif text.startswith("/assets/output/"):
        clean = urllib.parse.unquote(text.split("?", 1)[0]).replace("\\", "/")
        rel = clean[len("/assets/output/"):]
        root = OUTPUT_OUTPUT_DIR
    elif text.startswith(("/output/", "/assets/", "/api/project-library/", f"{PROJECT_MEDIA_ADAPTER_SCHEME}://")):
        if text.startswith(f"{PROJECT_MEDIA_ADAPTER_SCHEME}://"):
            return local_media_adapter_path(text)
        return output_file_from_url(text)
    else:
        return None
    rel = rel.lstrip("/")
    if not rel:
        return None
    path = os.path.abspath(os.path.join(root, rel))
    root_abs = os.path.abspath(root)
    if os.path.commonpath([root_abs, path]) != root_abs or not os.path.exists(path):
        return None
    return path

def runninghub_output_ext(remote, content_type=""):
    tail = str(remote or "").split("?", 1)[0].split("#", 1)[0]
    ext = os.path.splitext(tail)[1].lower().strip(".")
    allowed = {"png","jpg","jpeg","webp","gif","bmp","mp4","webm","mov","m4v","mkv","mp3","wav","ogg","m4a","flac","aac"}
    if ext in allowed:
        return ext
    ct = str(content_type or "").lower()
    if "mp4" in ct:
        return "mp4"
    if "webm" in ct:
        return "webm"
    if "quicktime" in ct:
        return "mov"
    if "mpeg" in ct:
        return "mp3"
    if "wav" in ct:
        return "wav"
    if "ogg" in ct:
        return "ogg"
    if "webp" in ct:
        return "webp"
    if "jpeg" in ct:
        return "jpg"
    return "png"

def rewrite_runninghub_file_url(url):
    text = str(url or "")
    if not text:
        return text
    try:
        parsed = urllib.parse.urlsplit(text)
    except Exception:
        return text
    target = RUNNINGHUB_FILE_HOST_REWRITES.get((parsed.netloc or "").lower())
    return parsed._replace(netloc=target).geturl() if target else text

def runninghub_extract_outputs(data):
    arr = []
    if isinstance(data, list):
        arr = data
    elif isinstance(data, dict):
        for key in ("outputs", "results", "files", "data"):
            value = data.get(key)
            if isinstance(value, list):
                arr = value
                break
        if not arr and (data.get("fileUrl") or data.get("url")):
            arr = [data]
    outputs = []
    for item in arr:
        if isinstance(item, str):
            outputs.append(rewrite_runninghub_file_url(item))
        elif isinstance(item, dict):
            url = item.get("fileUrl") or item.get("file_url") or item.get("url") or item.get("downloadUrl") or item.get("download_url")
            if isinstance(url, list):
                outputs.extend([rewrite_runninghub_file_url(u) for u in url if u])
            elif url:
                outputs.append(rewrite_runninghub_file_url(url))
    return outputs

async def runninghub_store_remote_output(client, remote):
    remote = rewrite_runninghub_file_url(remote)
    if not str(remote or "").startswith(("http://", "https://")):
        return remote
    response = await client.get(remote, follow_redirects=True)
    if not response.is_success:
        return remote
    ext = runninghub_output_ext(remote, response.headers.get("content-type", ""))
    filename = f"rh_{uuid.uuid4().hex[:12]}.{ext}"
    path = output_path_for(filename, "output")
    with open(path, "wb") as f:
        f.write(response.content)
    return output_url_for(filename, "output")

def runninghub_fail_reason(raw):
    data = raw.get("data") if isinstance(raw, dict) else None
    values = []
    if isinstance(data, dict):
        values.extend([data.get("failedReason"), data.get("failReason"), data.get("message"), data.get("error"), data.get("errorMessage")])
    if isinstance(raw, dict):
        values.extend([raw.get("msg"), raw.get("message"), raw.get("error"), raw.get("errorMessage")])
    for value in values:
        if not value:
            continue
        if isinstance(value, str):
            return value
        if isinstance(value, dict):
            return value.get("exception_message") or value.get("message") or json.dumps(value, ensure_ascii=False)
        return str(value)
    if isinstance(raw, dict) and raw.get("errorCode"):
        return f"RunningHub errorCode={raw.get('errorCode')}"
    return ""

# RunningHub 工作流字段推断已移除；AI 应用使用服务商提供的在线参数。


def runninghub_task_endpoint(provider, model):
    raw_model_path = str(model or "").strip()
    model_path = raw_model_path.strip("/")
    if not model_path:
        model_path = RUNNINGHUB_DEFAULT_IMAGE_MODELS[0]
    if raw_model_path.startswith("/openapi/"):
        return runninghub_endpoint_url(provider, raw_model_path)
    if model_path.startswith("openapi/"):
        return runninghub_endpoint_url(provider, f"/{model_path}")
    return runninghub_openapi_url(provider, model_path)

def runninghub_endpoint_alias_for_model(model):
    model_id = str(model or "").strip().strip("/")
    if not model_id:
        return ""
    direct = RUNNINGHUB_MODEL_ENDPOINT_ALIASES.get(model_id)
    if direct:
        return direct
    lowered = model_id.lower()
    if lowered.startswith("gpt-image-2.0/") or lowered.startswith("gpt-image-2/"):
        if "/text-to-image-" in lowered or lowered.endswith("/text-to-image"):
            return "rhart-image-g-2/text-to-image"
        if "/edit-" in lowered or lowered.endswith("/edit"):
            return "rhart-image-g-2/image-to-image"
        if "/image-to-image-" in lowered or lowered.endswith("/image-to-image"):
            return "rhart-image-g-2/image-to-image"
    if lowered.startswith("nano-banana/"):
        if "/text-to-image-" in lowered or lowered.endswith("/text-to-image"):
            return "rhart-image-v1/text-to-image"
        if "/edit-" in lowered or lowered.endswith("/edit"):
            return "rhart-image-v1/edit"
    return ""

def runninghub_registry_fallback():
    image = [
        {"name_en": "gpt-image-2.0/text-to-image-channel-low-price", "endpoint": "rhart-image-g-2/text-to-image", "output_type": "image"},
        {"name_en": "gpt-image-2.0/edit-channel-low-price", "endpoint": "rhart-image-g-2/image-to-image", "output_type": "image"},
        {"name_en": "gpt-image-2/text-to-image-official-stable", "endpoint": "rhart-image-g-2-official/text-to-image", "output_type": "image"},
        {"name_en": "gpt-image-2/image-to-image-official-stable", "endpoint": "rhart-image-g-2-official/image-to-image", "output_type": "image"},
        {"name_en": "nano-banana/text-to-image-official-stable", "endpoint": "rhart-image-v1-official/text-to-image", "output_type": "image"},
        {"name_en": "nano-banana/edit-official-stable", "endpoint": "rhart-image-v1-official/edit", "output_type": "image"},
    ]
    video = [
        {"name_en": "google/veo3.1-fast/text-to-video-channel-low-price", "endpoint": "rhart-video-v3.1-fast/text-to-video", "output_type": "video"},
        {"name_en": "sora-2/text-to-video-official-stable", "endpoint": "rhart-video-s-official/text-to-video", "output_type": "video"},
        {"name_en": "seedance-2.0-global/text-to-video", "endpoint": "bytedance/seedance-2.0-global/text-to-video", "output_type": "video"},
        {"name_en": "seedance-2.0-global/image-to-video", "endpoint": "bytedance/seedance-2.0-global/image-to-video", "output_type": "video"},
    ]
    return image + video

def runninghub_registry_items_from_raw(raw):
    candidates = [raw]
    if isinstance(raw, dict):
        candidates.extend([
            raw.get("data"),
            raw.get("models"),
            raw.get("list"),
            raw.get("items"),
            raw.get("records"),
            raw.get("result"),
        ])
    for candidate in candidates:
        if isinstance(candidate, list):
            return [item for item in candidate if isinstance(item, dict)]
        if isinstance(candidate, dict):
            nested = (
                candidate.get("models")
                or candidate.get("list")
                or candidate.get("items")
                or candidate.get("records")
                or candidate.get("data")
            )
            if isinstance(nested, list):
                return [item for item in nested if isinstance(item, dict)]
    return []

def runninghub_registry_model_from_id(model_id, output_type=""):
    model_id = str(model_id or "").strip()
    if not model_id:
        return None
    output_type = str(output_type or "").strip().lower() or classify_upstream_model(model_id)
    return {"name_en": model_id, "endpoint": model_id, "output_type": output_type}

async def fetch_runninghub_llm_models(provider=None):
    headers = runninghub_api_headers(provider)
    errors = []
    async with httpx.AsyncClient(timeout=20, follow_redirects=True) as client:
        for url in RUNNINGHUB_LLM_MODELS_URLS:
            try:
                resp = await client.get(url, headers=headers)
                if resp.status_code >= 400 or looks_like_html_response(resp.text):
                    errors.append(f"{url}: HTTP {resp.status_code} {resp.text[:180]}")
                    continue
                raw = resp.json() if resp.text else {}
                grouped, ids = parse_upstream_models(raw, "openai")
                if ids:
                    return [runninghub_registry_model_from_id(mid, "chat") for mid in ids], {"source": url, "count": len(ids)}
                errors.append(f"{url}: empty")
            except Exception as exc:
                errors.append(f"{url}: {str(exc)[:180]}")
    return [], {"source": "", "count": 0, "errors": errors[-3:]}

async def fetch_runninghub_model_registry(provider=None, include_fallback=True, include_meta=False):
    urls = [
        ("openapi", runninghub_openapi_url(provider, "models")),
        ("github", RUNNINGHUB_MODEL_REGISTRY_URL),
    ]
    if os.path.exists(STATIC_RUNNINGHUB_MODEL_REGISTRY_FILE):
        urls.append(("local", STATIC_RUNNINGHUB_MODEL_REGISTRY_FILE))
    headers = runninghub_api_headers(provider)
    errors = []
    source = ""
    items = []
    async with httpx.AsyncClient(timeout=30, follow_redirects=True) as client:
        for source_name, url in urls:
            try:
                if source_name == "local":
                    with open(url, "r", encoding="utf-8") as f:
                        raw = json.load(f)
                else:
                    req_headers = headers if source_name == "openapi" else {"Accept": "application/json"}
                    resp = await client.get(url, headers=req_headers)
                    if resp.status_code >= 400 or looks_like_html_response(resp.text):
                        errors.append(f"{source_name}: HTTP {resp.status_code} {resp.text[:180]}")
                        continue
                    raw = resp.json() if resp.text else []
                parsed = runninghub_registry_items_from_raw(raw)
                if parsed:
                    items = parsed
                    source = source_name
                    break
                errors.append(f"{source_name}: empty")
            except Exception as exc:
                errors.append(f"{source_name}: {str(exc)[:180]}")
                continue
    llm_items, llm_meta = await fetch_runninghub_llm_models(provider)
    combined = [*items]
    seen = {runninghub_model_id(item) for item in combined if runninghub_model_id(item)}
    for item in llm_items:
        mid = runninghub_model_id(item)
        if mid and mid not in seen:
            combined.append(item)
            seen.add(mid)
    if combined:
        meta = {
            "source": source or "llm",
            "openapi_count": len(items),
            "llm_count": len(llm_items),
            "llm_source": llm_meta.get("source") or "",
            "errors": [*errors[-3:], *((llm_meta.get("errors") or [])[-3:])],
        }
        return (combined, meta) if include_meta else combined
    if include_fallback:
        fallback = runninghub_registry_fallback()
        meta = {
            "source": "fallback",
            "openapi_count": 0,
            "llm_count": 0,
            "llm_source": "",
            "errors": [*errors[-3:], *((llm_meta.get("errors") or [])[-3:])],
        }
        return (fallback, meta) if include_meta else fallback
    raise HTTPException(status_code=502, detail=f"拉取 RunningHub 模型注册表失败：{'; '.join(errors[-4:]) or 'unknown error'}")

def runninghub_model_id(item):
    if not isinstance(item, dict):
        return ""
    return str(item.get("name_en") or item.get("id") or item.get("name") or item.get("endpoint") or "").strip()

def runninghub_model_display_name(item, model_id=""):
    if not isinstance(item, dict):
        return ""
    raw_id = str(model_id or runninghub_model_id(item) or "").strip()
    for key in (
        "name_cn", "name_zh", "zh_name", "cn_name", "display_name", "displayName",
        "title", "label", "nameCn", "nameZh", "chinese_name", "chineseName",
    ):
        value = re.sub(r"\s+", " ", str(item.get(key) or "").strip())
        if value and value != raw_id:
            return value[:160]
    name = re.sub(r"\s+", " ", str(item.get("name") or "").strip())
    if name and name != raw_id and not re.fullmatch(r"[A-Za-z0-9_./:-]+", name):
        return name[:160]
    return ""

def runninghub_registry_payload(items):
    grouped = {"image": [], "chat": RUNNINGHUB_FALLBACK_CHAT_MODELS[:], "video": []}
    model_names = {}
    all_ids = []
    for item in items or []:
        mid = runninghub_model_id(item)
        if not mid:
            continue
        display_name = runninghub_model_display_name(item, mid)
        if display_name:
            model_names[mid] = display_name
        output_type = str(item.get("output_type") or item.get("outputType") or "").strip().lower()
        if output_type in ("image", "video"):
            grouped[output_type].append(mid)
            all_ids.append(mid)
    for model in RUNNINGHUB_DEFAULT_IMAGE_MODELS:
        if model not in grouped["image"]:
            grouped["image"].append(model)
            all_ids.append(model)
    for model in RUNNINGHUB_DEFAULT_VIDEO_MODELS:
        if model not in grouped["video"]:
            grouped["video"].append(model)
            all_ids.append(model)
    for model in RUNNINGHUB_FALLBACK_CHAT_MODELS:
        if model not in all_ids:
            all_ids.append(model)
    for key in grouped:
        grouped[key] = sorted(set(grouped[key]))
    return {
        "total": len(set(all_ids)),
        "image_models": grouped["image"],
        "chat_models": grouped["chat"],
        "video_models": grouped["video"],
        "all": sorted(set(all_ids)),
        "model_names": model_names,
        "protocol": "runninghub",
    }

async def runninghub_models_payload(provider=None):
    registry, meta = await fetch_runninghub_model_registry(provider, include_fallback=True, include_meta=True)
    payload = runninghub_registry_payload(registry)
    payload["raw"] = {"registry_count": len(registry), **meta}
    if meta.get("source") == "fallback":
        payload["message"] = "RunningHub 模型接口未返回完整列表，当前显示内置兜底模型。"
    else:
        payload["message"] = f"RunningHub 模型列表来自 {meta.get('source')}"
    return payload

async def runninghub_model_definition(provider, model):
    requested = str(model or "").strip().strip("/")
    configured = runninghub_configured_video_model_definition(provider, requested)
    if configured is not None:
        return configured
    registry = provider.get("_runninghub_model_registry")
    if not isinstance(registry, list):
        registry = await fetch_runninghub_model_registry(provider, include_fallback=True)
        if isinstance(provider, dict):
            provider["_runninghub_model_registry"] = copy.deepcopy(registry)
    for item in registry:
        mid = runninghub_model_id(item)
        endpoint = str(item.get("endpoint") or "").strip().strip("/")
        if requested and requested in {mid, endpoint, f"/openapi/v2/{endpoint}", f"openapi/v2/{endpoint}"}:
            if endpoint:
                return item
            alias = runninghub_endpoint_alias_for_model(mid or requested)
            if alias:
                patched = dict(item)
                patched["endpoint"] = alias
                return patched
            return item
    endpoint = requested
    if endpoint.startswith("/openapi/v2/"):
        endpoint = endpoint[len("/openapi/v2/"):]
    elif endpoint.startswith("openapi/v2/"):
        endpoint = endpoint[len("openapi/v2/"):]
    endpoint = runninghub_endpoint_alias_for_model(requested) or endpoint
    return {"name_en": requested, "endpoint": endpoint or RUNNINGHUB_DEFAULT_IMAGE_MODELS[0], "output_type": classify_upstream_model(requested), "params": []}

def runninghub_schema_options(field):
    values = []
    for item in (field or {}).get("options") or []:
        if isinstance(item, dict):
            value = item.get("value")
        else:
            value = item
        if value is not None and str(value) != "":
            values.append(str(value))
    return values

def runninghub_schema_value(field, preferred=None):
    preferred = "" if preferred is None else str(preferred).strip()
    options = runninghub_schema_options(field)
    if preferred and (not options or preferred in options):
        return preferred
    default = (field or {}).get("defaultValue")
    if default is not None and str(default) != "":
        return default
    return options[0] if options else preferred

def runninghub_schema_field(params, *keys):
    wanted = {str(k).lower() for k in keys if k}
    for field in params or []:
        if not isinstance(field, dict):
            continue
        names = {str(field.get("fieldKey") or "").lower(), str(field.get("label") or "").lower()}
        if names & wanted:
            return field
    return None

def runninghub_schema_field_required(field):
    value = (field or {}).get("required")
    return value is True or (isinstance(value, str) and value.strip().lower() == "true")

def runninghub_aspect_from_size(size, fallback="1:1"):
    width, height = parse_size_pair(size)
    if width and height:
        divisor = math.gcd(width, height) or 1
        return f"{width // divisor}:{height // divisor}"
    raw = str(size or "").strip().lower()
    if re.fullmatch(r"(auto|\d+\s*:\s*\d+)", raw):
        return raw.replace(" ", "")
    return fallback

def runninghub_resolution_from_size(size, fallback="2k"):
    width, height = parse_size_pair(size)
    if width and height:
        long_edge = max(width, height)
        if long_edge >= 3200:
            return "4k"
        if long_edge >= 1400:
            return "2k"
        return "1k"
    raw = str(size or "").strip().lower()
    return raw if raw in {"1k", "2k", "4k", "480p", "720p", "1080p", "native1080p"} else fallback

def runninghub_size_for_aspect(aspect_ratio, fallback="1280x720"):
    ratio = str(aspect_ratio or "").strip()
    return {
        "9:16": "720x1280",
        "16:9": "1280x720",
        "1:1": "1024x1024",
        "4:3": "1024x768",
        "3:4": "768x1024",
    }.get(ratio, fallback)

def runninghub_is_image_to_video(value):
    text = str(value or "").strip().lower()
    compact = re.sub(r"[\s_/]+", "-", text)
    return "image-to-video" in compact or "-i2v" in compact or compact.endswith("i2v")

def runninghub_apply_schema_defaults(body, params):
    for field in params or []:
        if not isinstance(field, dict):
            continue
        key = str(field.get("fieldKey") or "").strip()
        if not key or key in body:
            continue
        default = field.get("defaultValue")
        options = runninghub_schema_options(field)
        if default is None or default == "":
            if field.get("required") is True and options:
                default = options[0]
            else:
                continue
        ftype = str(field.get("type") or "").upper()
        if ftype == "BOOLEAN":
            body[key] = bool(default) if not isinstance(default, str) else default.lower() == "true"
        elif ftype in {"INT", "INTEGER"}:
            try:
                body[key] = int(default)
            except Exception:
                body[key] = default
        elif ftype == "FLOAT":
            try:
                body[key] = float(default)
            except Exception:
                body[key] = default
        else:
            body[key] = default
    return body

def runninghub_query_status(raw):
    if not isinstance(raw, dict):
        return ""
    values = [
        raw.get("status"),
        raw.get("state"),
        raw.get("taskStatus"),
        raw.get("task_status"),
    ]
    data = raw.get("data")
    if isinstance(data, dict):
        values.extend([data.get("status"), data.get("state"), data.get("taskStatus"), data.get("task_status")])
    for value in values:
        if value is not None:
            return str(value).lower()
    return ""

def runninghub_extract_task_id(raw):
    if not isinstance(raw, dict):
        return ""
    for key in ("taskId", "task_id", "id"):
        if raw.get(key):
            return str(raw[key])
    data = raw.get("data")
    if isinstance(data, dict):
        for key in ("taskId", "task_id", "id"):
            if data.get(key):
                return str(data[key])
    return ""

def runninghub_extract_image(raw):
    if not isinstance(raw, dict):
        raise HTTPException(status_code=502, detail="RunningHub 返回格式不是 JSON 对象")
    containers = [raw]
    data = raw.get("data")
    if isinstance(data, dict):
        containers.append(data)
    for container in containers:
        results = container.get("results") or container.get("result") or container.get("outputs") or container.get("output")
        if isinstance(results, dict):
            results = [results]
        if isinstance(results, list):
            for item in results:
                if isinstance(item, str) and item.startswith(("http://", "https://")):
                    return {"type": "url", "value": rewrite_runninghub_file_url(item)}
                if not isinstance(item, dict):
                    continue
                if item.get("type") == "url" and item.get("value"):
                    return {"type": "url", "value": rewrite_runninghub_file_url(item["value"])}
                if item.get("type") == "b64" and item.get("value"):
                    return {"type": "b64", "value": item["value"], "mime_type": item.get("mime_type") or "image/png"}
                url = item.get("url") or item.get("fileUrl") or item.get("file_url") or item.get("download_url") or item.get("imageUrl") or item.get("image_url")
                if isinstance(url, list) and url:
                    url = url[0]
                if isinstance(url, str) and url:
                    return {"type": "url", "value": rewrite_runninghub_file_url(url)}
    image = extract_image(raw)
    if image.get("type") == "url":
        image["value"] = rewrite_runninghub_file_url(image.get("value"))
    return image

async def runninghub_upload_reference(client, provider, ref):
    path = local_media_adapter_path(ref.get("url", ""))
    if not path:
        value = ref.get("url", "")
        return value if str(value).startswith(("http://", "https://")) else ""
    upload_url = runninghub_openapi_url(provider, "media/upload/binary")
    headers = {"Authorization": bearer_auth_value(runninghub_api_key(provider, use_wallet=True)), "Accept": "application/json"}
    with open(path, "rb") as fh:
        files = {"file": (os.path.basename(path), fh, content_type_for_path(path))}
        response = await client.post(upload_url, headers=headers, files=files, timeout=120)
    response.raise_for_status()
    raw = response.json()
    data = raw.get("data") if isinstance(raw, dict) else None
    candidates = [raw, data] if isinstance(data, dict) else [raw]
    for item in candidates:
        if not isinstance(item, dict):
            continue
        value = item.get("download_url") or item.get("downloadUrl") or item.get("url") or item.get("fileUrl") or item.get("file_url")
        if value:
            return str(value)
    raise HTTPException(status_code=502, detail=f"RunningHub 上传图片未返回 download_url：{raw}")

async def wait_for_runninghub_image_task(client, provider, task_id):
    query_url = runninghub_openapi_url(provider, "query")
    deadline = time.monotonic() + 1800
    last_payload = None
    while time.monotonic() < deadline:
        await asyncio.sleep(2)
        response = await client.post(query_url, headers=runninghub_api_headers(provider), json={"taskId": task_id})
        response.raise_for_status()
        raw = response.json()
        last_payload = raw
        status = runninghub_query_status(raw)
        if status in {"success", "succeeded", "completed", "complete", "finished", "finish", "done", "3"}:
            return raw
        if status in {"failed", "fail", "error", "canceled", "cancelled", "4"}:
            raise HTTPException(status_code=502, detail=f"RunningHub 任务失败：{raw}")
        try:
            return {"data": {"results": [runninghub_extract_image(raw)]}}
        except HTTPException:
            pass
    raise HTTPException(status_code=504, detail=f"RunningHub 生图任务超时：{last_payload}")

RUNNINGHUB_ENTRY_MODEL_RE = re.compile(r"^app:(.+)$")

def rh_field_kind(field):
    field = field or {}
    t = str(field.get("fieldType") or "").strip().upper()
    if t == "IMAGE":
        return "image"
    if t == "VIDEO":
        return "video"
    if t == "AUDIO":
        return "audio"
    if t == "SLIDER":
        return "slider"
    if t in ("NUMBER", "FLOAT", "INTEGER", "INT"):
        return "number"
    if t in ("BOOLEAN", "BOOL"):
        return "boolean"
    key = f"{field.get('fieldName') or ''} {field.get('fieldValue') or ''}".lower()
    if re.search(r"\b(image|img|mask|photo|picture)\b", key) or re.search(r"\.(png|jpe?g|webp|gif|bmp)(\?|$)", key, re.I):
        return "image"
    if re.search(r"\b(video|movie|mp4)\b", key) or re.search(r"\.(mp4|webm|mov|m4v|mkv)(\?|$)", key, re.I):
        return "video"
    if re.search(r"\b(audio|sound|music|voice)\b", key) or re.search(r"\.(mp3|wav|ogg|m4a|flac|aac)(\?|$)", key, re.I):
        return "audio"
    return "text"

def rh_field_role(field):
    kind = rh_field_kind(field)
    if kind in ("image", "video", "audio", "number", "slider", "boolean"):
        return kind
    field = field or {}
    text = f"{field.get('fieldName') or ''} {field.get('label') or ''} {field.get('group') or ''}".lower()
    if re.search(r"prompt|positive|negative|text|caption|description|关键词|提示词|正向|负向", text):
        return "prompt"
    return "text"

def _rh_natural_cmp(x, y):
    if x == y:
        return 0
    if x.isdigit() and y.isdigit():
        ix, iy = int(x), int(y)
        return (ix > iy) - (ix < iy)
    return (x > y) - (x < y)

def _rh_field_cmp(a, b):
    ak, bk = rh_field_kind(a), rh_field_kind(b)
    if ak == "image" and bk == "image":
        try:
            ao = int(a.get("imageOrder") or 0) or 9999
        except Exception:
            ao = 9999
        try:
            bo = int(b.get("imageOrder") or 0) or 9999
        except Exception:
            bo = 9999
        if ao != bo:
            return ao - bo
    if ak == "image" and bk != "image":
        return -1
    if ak != "image" and bk == "image":
        return 1
    node_cmp = _rh_natural_cmp(str(a.get("nodeId") or ""), str(b.get("nodeId") or ""))
    if node_cmp != 0:
        return node_cmp
    fa, fb = str(a.get("fieldName") or ""), str(b.get("fieldName") or "")
    return (fa > fb) - (fa < fb)

def rh_sort_fields(fields):
    return sorted(list(fields or []), key=functools.cmp_to_key(_rh_field_cmp))

def rh_field_indexes(fields):
    counters = {"image": 0, "video": 0, "audio": 0}
    mapping = {}
    for field in rh_sort_fields(fields):
        kind = rh_field_kind(field)
        if kind in counters:
            mapping[(str(field.get("nodeId") or ""), str(field.get("fieldName") or ""))] = counters[kind]
            counters[kind] += 1
    return mapping

def rh_default_value(field):
    value = (field or {}).get("fieldValue")
    if isinstance(value, list):
        value = value[0] if value else ""
    if value is None or isinstance(value, dict):
        return ""
    return str(value)

SEED_UINT32_MAX = 4294967295

def rh_is_seed_like_name(*parts) -> bool:
    text = " ".join(str(part or "") for part in parts).lower()
    return any(key in text for key in ("seed", "noise", "随机", "种子", "噪"))

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
    safe = ((abs(num) - 1) % SEED_UINT32_MAX) + 1
    return str(safe) if isinstance(value, str) else safe

def sanitize_seed_like_values(value, parent_key=""):
    if isinstance(value, dict):
        result = {}
        for key, item in value.items():
            if rh_is_seed_like_name(key) and not isinstance(item, (dict, list)):
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
        if rh_is_seed_like_name(clean.get("fieldName"), clean.get("label"), clean.get("note")):
            clean["fieldValue"] = normalize_seed_uint32(clean.get("fieldValue"))
        result.append(clean)
    return result

def rh_random_field_value(field):
    def _num(raw, default):
        try:
            s = str(raw).strip()
            if s == "" or s.lower() == "none":
                return default
            return float(s)
        except Exception:
            return default
    looks_seed = rh_is_seed_like_name((field or {}).get("fieldName"), (field or {}).get("label"), (field or {}).get("note"))
    lo = _num((field or {}).get("min"), 0.0)
    hi = _num((field or {}).get("max"), float(SEED_UINT32_MAX) if looks_seed else 999999.0)
    if looks_seed:
        hi = min(hi, float(SEED_UINT32_MAX))
        lo = max(0.0, min(lo, hi))
    if hi < lo:
        lo, hi = hi, lo
    step = _num((field or {}).get("step"), 1.0)
    value = random.uniform(lo, hi)
    if step and step > 0:
        value = lo + round((value - lo) / step) * step
    if float(step).is_integer() and float(lo).is_integer() and float(hi).is_integer():
        return str(int(round(value)))
    return str(value)

def runninghub_entry_config_from_model(provider, model):
    """解析 model=app:ID，返回在线 AI 应用配置。"""
    text = str(model or "").strip()
    match = RUNNINGHUB_ENTRY_MODEL_RE.match(text)
    if not match:
        return None
    entry_id = match.group(1).strip()
    if not entry_id:
        return None
    entry = next(
        (e for e in (provider.get("rh_apps") or []) if runninghub_entry_id(e, "app") == entry_id),
        None,
    )
    if not entry:
        return None
    return {
        "kind": "app",
        "id": entry_id,
        "fields": entry.get("fields") or [],
    }

async def runninghub_upload_local_to_filename(client, provider, url, use_wallet=False):
    """把本地/远程素材上传到 RunningHub /task/openapi/upload，返回 fileName（供 nodeInfoList 使用）。"""
    text = str(url or "").strip()
    if not text:
        return ""
    path = runninghub_local_asset_path(text)
    if path:
        filename = os.path.basename(path)
        content_type = content_type_for_path(path)
        with open(path, "rb") as fh:
            content = fh.read()
    elif text.startswith(("http://", "https://")):
        response = await client.get(text, follow_redirects=True)
        response.raise_for_status()
        content = response.content
        content_type = response.headers.get("content-type") or "application/octet-stream"
        filename = os.path.basename(urllib.parse.urlsplit(text).path) or "asset.bin"
    else:
        return ""
    if not content:
        return ""
    api_key = runninghub_api_key(provider, use_wallet=use_wallet)
    upload_url = runninghub_endpoint_url(provider, "/task/openapi/upload")
    files = {"file": (filename, content, content_type)}
    data = {"apiKey": api_key, "fileType": "input"}
    response = await client.post(upload_url, headers=runninghub_app_headers(False, use_wallet), data=data, files=files)
    raw = response.json()
    if isinstance(raw, dict) and raw.get("code") in (0, "0") and isinstance(raw.get("data"), dict) and raw["data"].get("fileName"):
        return raw["data"]["fileName"]
    raise HTTPException(status_code=502, detail=(raw.get("msg") if isinstance(raw, dict) else "") or f"RunningHub 上传素材失败：{raw}")

async def generate_runninghub_entry_image(prompt, size, model, reference_images, provider, entry, image_parameters=None):
    """运行 RunningHub 在线 AI 应用并返回首张图片结果。"""
    entry_id = entry["id"]
    fields = rh_sort_fields([f for f in (entry.get("fields") or []) if isinstance(f, dict) and f.get("enabled") is True])
    idx_map = rh_field_indexes(fields)
    use_wallet = False
    timeout = httpx.Timeout(connect=20.0, read=1800.0, write=240.0, pool=20.0)
    explicit = image_parameters if isinstance(image_parameters, dict) else {}
    aspect = str(explicit.get("aspect_ratio") or "") or runninghub_aspect_from_size(size, "")
    resolution = str(explicit.get("resolution") or "") or runninghub_resolution_from_size(size, "")
    parsed_width, parsed_height = parse_size_pair(size)
    width = explicit.get("width") if explicit.get("width") is not None else parsed_width
    height = explicit.get("height") if explicit.get("height") is not None else parsed_height
    def requested_size_field_value(field):
        names = {
            str(field.get("fieldName") or "").strip().lower(),
            str(field.get("fieldKey") or "").strip().lower(),
            str(field.get("label") or "").strip().lower(),
        }
        if aspect and names & {"aspectratio", "aspect_ratio", "ratio"}:
            return runninghub_schema_value(field, aspect)
        if resolution and "resolution" in names:
            return runninghub_schema_value(field, resolution)
        if width and "width" in names:
            return width
        if height and "height" in names:
            return height
        return None
    async with httpx.AsyncClient(timeout=timeout) as client:
        uploaded = []
        for ref in (reference_images or [])[:ONLINE_IMAGE_REFERENCE_MAX]:
            ref_url = ref.get("url") if isinstance(ref, dict) else ref
            if not ref_url:
                continue
            file_name = await runninghub_upload_local_to_filename(client, provider, ref_url, use_wallet)
            if file_name:
                uploaded.append(file_name)

        node_info_list = []
        prompt_text = str(prompt or "").strip()
        for field in fields:
            node_id = str(field.get("nodeId") or "").strip()
            field_name = str(field.get("fieldName") or "").strip()
            if not node_id or not field_name:
                continue
            kind_f = rh_field_kind(field)
            if kind_f in ("image", "video", "audio"):
                if kind_f != "image":
                    continue  # 在线生图仅提供图片素材
                index = idx_map.get((node_id, field_name), 0)
                value = uploaded[index] if index < len(uploaded) else ""
                if not value:
                    # 可选图无输入则跳过；必填图回退默认值
                    if field.get("required") is True:
                        value = rh_default_value(field)
                        if not value:
                            continue
                    else:
                        continue
                node_info_list.append({"nodeId": node_id, "fieldName": field_name, "fieldValue": value})
            elif rh_field_role(field) == "prompt":
                value = prompt_text or rh_default_value(field)
                node_info_list.append({"nodeId": node_id, "fieldName": field_name, "fieldValue": value})
            elif kind_f == "number" and field.get("random_enabled") is True:
                node_info_list.append({"nodeId": node_id, "fieldName": field_name, "fieldValue": rh_random_field_value(field)})
            else:
                value = requested_size_field_value(field)
                if value is None:
                    value = rh_default_value(field)
                node_info_list.append({"nodeId": node_id, "fieldName": field_name, "fieldValue": value})

        api_key = runninghub_api_key(provider, use_wallet=use_wallet)
        submit_url = runninghub_endpoint_url(provider, "/task/openapi/ai-app/run")
        body = {"apiKey": api_key, "webappId": entry_id, "nodeInfoList": node_info_list}

        response = await client.post(submit_url, headers=runninghub_app_headers(True, use_wallet), json=body)
        raw = response.json()
        if not (isinstance(raw, dict) and raw.get("code") in (0, "0")):
            raise HTTPException(status_code=502, detail=(raw.get("msg") if isinstance(raw, dict) else "") or f"RunningHub 提交失败：{raw}")
        task_id = raw.get("data", {}).get("taskId") if isinstance(raw.get("data"), dict) else ""
        if not task_id:
            raise HTTPException(status_code=502, detail=f"RunningHub 未返回 taskId：{raw}")

        query_url = runninghub_endpoint_url(provider, "/task/openapi/outputs")
        deadline = time.monotonic() + 1800
        last_payload = None
        while time.monotonic() < deadline:
            await asyncio.sleep(2.5)
            query_response = await client.post(query_url, headers=runninghub_app_headers(True), json={"apiKey": api_key, "taskId": task_id})
            query_raw = query_response.json()
            last_payload = query_raw
            code = query_raw.get("code") if isinstance(query_raw, dict) else None
            if code in (0, "0"):
                outputs = runninghub_extract_outputs(query_raw.get("data"))
                for remote in outputs:
                    if str(remote or "").startswith(("http://", "https://", "/output/", "/assets/")):
                        return {"type": "url", "value": str(remote)}, query_raw
                raise HTTPException(status_code=502, detail=f"RunningHub 任务无图片输出：{query_raw}")
            if code in (805, "805"):
                raise HTTPException(status_code=502, detail=f"RunningHub 任务失败：{runninghub_fail_reason(query_raw) or query_raw}")
            # 804 运行中 / 813 排队中 / 其他状态继续轮询
        raise HTTPException(status_code=504, detail=f"RunningHub 任务超时：{last_payload}")

async def generate_runninghub_provider_image(prompt, size, model, reference_images=None, provider=None, image_parameters=None):
    entry = runninghub_entry_config_from_model(provider, model)
    if entry:
        return await generate_runninghub_entry_image(prompt, size, model, reference_images, provider, entry, image_parameters)
    model_def = await runninghub_model_definition(provider, model)
    endpoint = runninghub_task_endpoint(provider, model_def.get("endpoint") or model)
    params = model_def.get("params") if isinstance(model_def.get("params"), list) else []
    explicit = image_parameters if isinstance(image_parameters, dict) else {}
    aspect = str(explicit.get("aspect_ratio") or "") or runninghub_aspect_from_size(size, "1:1")
    resolution = str(explicit.get("resolution") or "") or runninghub_resolution_from_size(size, "2k")
    body = {"prompt": prompt}
    if runninghub_schema_field(params, "aspectRatio"):
        field = runninghub_schema_field(params, "aspectRatio")
        body["aspectRatio"] = runninghub_schema_value(field, aspect)
    elif runninghub_schema_field(params, "ratio"):
        field = runninghub_schema_field(params, "ratio")
        body["ratio"] = runninghub_schema_value(field, aspect)
    if runninghub_schema_field(params, "resolution"):
        field = runninghub_schema_field(params, "resolution")
        body["resolution"] = runninghub_schema_value(field, resolution)
    parsed_width, parsed_height = parse_size_pair(size)
    width = explicit.get("width") if explicit.get("width") is not None else parsed_width
    height = explicit.get("height") if explicit.get("height") is not None else parsed_height
    if width and height:
        if runninghub_schema_field(params, "width"):
            body["width"] = width
        if runninghub_schema_field(params, "height"):
            body["height"] = height
    quality_field = runninghub_schema_field(params, "quality")
    if quality_field:
        body["quality"] = runninghub_schema_value(quality_field, "medium")
    async with httpx.AsyncClient(timeout=httpx.Timeout(connect=20.0, read=1800.0, write=180.0, pool=20.0)) as client:
        image_urls = []
        for ref in (reference_images or [])[:ONLINE_IMAGE_REFERENCE_MAX]:
            url = await runninghub_upload_reference(client, provider, ref)
            if url:
                image_urls.append(url)
        if image_urls:
            image_field = runninghub_schema_field(params, "imageUrls", "imageUrl", "images", "image")
            key = str((image_field or {}).get("fieldKey") or "imageUrls")
            if key.endswith("s") or (image_field or {}).get("multipleInputs") is True:
                body[key] = image_urls
            else:
                body[key] = image_urls[0]
        runninghub_apply_schema_defaults(body, params)
        response = await client.post(endpoint, headers=runninghub_json_headers(provider), json=body)
        response.raise_for_status()
        raw = response.json()
        try:
            return runninghub_extract_image(raw), raw
        except HTTPException:
            task_id = runninghub_extract_task_id(raw)
            if not task_id:
                raise HTTPException(status_code=502, detail=f"RunningHub 未返回 taskId 或图片结果：{raw}")
        result = await wait_for_runninghub_image_task(client, provider, task_id)
        return runninghub_extract_image(result), result

async def wait_for_runninghub_openapi_task(client, provider, task_id, output_kind=""):
    query_url = runninghub_openapi_url(provider, "query")
    deadline = time.monotonic() + 1800
    last_payload = None
    while time.monotonic() < deadline:
        await asyncio.sleep(3)
        response = await client.post(query_url, headers=runninghub_json_headers(provider), json={"taskId": task_id})
        response.raise_for_status()
        raw = response.json()
        last_payload = raw
        status = runninghub_query_status(raw).upper()
        if status in {"SUCCESS", "SUCCEEDED", "COMPLETED", "COMPLETE", "FINISHED", "DONE", "3"}:
            return raw
        if status in {"FAILED", "FAIL", "ERROR", "CANCEL", "CANCELED", "CANCELLED", "4"}:
            raise HTTPException(status_code=502, detail=f"RunningHub 任务失败：{runninghub_fail_reason(raw) or raw}")
        if output_kind == "video" and video_output_urls(raw):
            return raw
    raise HTTPException(status_code=504, detail=f"RunningHub 任务超时：{last_payload or task_id}")

async def generate_runninghub_video(payload, provider):
    model_def = await runninghub_model_definition(provider, payload.model)
    endpoint = runninghub_task_endpoint(provider, model_def.get("endpoint") or payload.model)
    image_to_video = runninghub_is_image_to_video(payload.model) or runninghub_is_image_to_video(model_def.get("endpoint")) or runninghub_is_image_to_video(endpoint)
    params = model_def.get("params") if isinstance(model_def.get("params"), list) else []
    incompatible = runninghub_video_incompatible_parameters(payload, params, image_to_video=image_to_video)
    if incompatible:
        raise HTTPException(status_code=400, detail={
            "code": "unsupported_video_adapter_parameters",
            "message": f"{provider.get('name') or provider.get('id') or 'RunningHub'} 不支持本次请求的全部参数",
            "incompatible_parameters": incompatible,
        })
    body = {"prompt": str(payload.prompt or "")}
    aspect = str(payload.aspect_ratio or "16:9").strip() or "16:9"
    if runninghub_schema_field(params, "aspectRatio"):
        field = runninghub_schema_field(params, "aspectRatio")
        body["aspectRatio"] = runninghub_schema_value(field, aspect)
    if runninghub_schema_field(params, "ratio"):
        field = runninghub_schema_field(params, "ratio")
        body["ratio"] = runninghub_schema_value(field, aspect)
    if runninghub_schema_field(params, "size"):
        field = runninghub_schema_field(params, "size")
        body["size"] = runninghub_schema_value(field, runninghub_size_for_aspect(aspect))
    if runninghub_schema_field(params, "duration"):
        field = runninghub_schema_field(params, "duration")
        body["duration"] = runninghub_schema_value(field, str(max(1, min(60, int(payload.duration or 5)))))
    if runninghub_schema_field(params, "resolution"):
        field = runninghub_schema_field(params, "resolution")
        body["resolution"] = runninghub_schema_value(field, str(payload.resolution or "720p").lower())
    if runninghub_schema_field(params, "generateAudio"):
        body["generateAudio"] = bool(payload.generate_audio)
    if runninghub_schema_field(params, "watermark"):
        body["watermark"] = bool(payload.watermark)
    async with httpx.AsyncClient(timeout=VIDEO_POLL_TIMEOUT) as client:
        image_refs = []
        for ref in (payload.images or [])[:10]:
            ref_url = getattr(ref, "url", "") or ""
            if ref_url:
                up = await runninghub_upload_reference(client, provider, {"url": ref_url})
                if up:
                    image_refs.append({"url": up, "role": str(getattr(ref, "role", "") or "").strip().lower()})
        image_urls = [item["url"] for item in image_refs if item.get("url")]
        if image_urls:
            first_url = next((item["url"] for item in image_refs if item.get("role") in {"first_frame", "first"}), image_urls[0])
            last_url = next((item["url"] for item in image_refs if item.get("role") in {"last_frame", "last"}), image_urls[1] if len(image_urls) > 1 else "")
            first_field = runninghub_schema_field(params, "firstFrameUrl", "first_frame_url", "firstFrameImage", "first_frame_image")
            last_field = runninghub_schema_field(params, "lastFrameUrl", "last_frame_url", "lastFrameImage", "last_frame_image")
            if first_field and first_url:
                body[str(first_field.get("fieldKey"))] = first_url
            if last_field and last_url:
                body[str(last_field.get("fieldKey"))] = last_url
            image_field = runninghub_schema_field(params, "imageUrls", "image_urls", "imageUrl", "image_url", "referenceImages", "referenceImageUrls")
            key = str((image_field or {}).get("fieldKey") or "")
            if key and key in body:
                pass
            elif key and (key.endswith("s") or (image_field or {}).get("multipleInputs") is True):
                body[key] = image_urls
            elif key:
                body[key] = image_urls[0]
            elif image_to_video and "firstFrameUrl" not in body:
                body["firstFrameUrl"] = first_url
        first_required = runninghub_schema_field(params, "firstFrameUrl", "first_frame_url", "firstFrameImage", "first_frame_image")
        if runninghub_schema_field_required(first_required) and not body.get(str(first_required.get("fieldKey") or "")):
            raise HTTPException(status_code=400, detail="当前 RunningHub 模型是图生视频，需要连接一张首帧图片后再生成。")
        if image_to_video and not body.get("firstFrameUrl") and not image_urls:
            raise HTTPException(status_code=400, detail="当前 RunningHub 模型是图生视频，需要连接一张首帧图片后再生成。")
        runninghub_apply_schema_defaults(body, params)
        response = await client.post(endpoint, headers=runninghub_json_headers(provider), json=body)
        response.raise_for_status()
        raw = response.json()
        task_id = runninghub_extract_task_id(raw)
        if not task_id:
            fail_reason = runninghub_fail_reason(raw)
            if fail_reason:
                raise HTTPException(status_code=502, detail=f"RunningHub 视频接口错误：{fail_reason}")
        result = raw
        if task_id and not video_output_urls(raw):
            result = await wait_for_runninghub_openapi_task(client, provider, task_id, "video")
        urls = video_output_urls(result)
        if not urls:
            outputs = runninghub_extract_outputs(result)
            urls = [url for url in outputs if str(url).startswith(("http://", "https://", "/output/", "/assets/"))]
        if not urls:
            raise HTTPException(status_code=502, detail=f"RunningHub 视频生成成功但没有返回视频：{result}")
        local_urls = [await save_remote_video_to_output(url, prefix="rh_video_") for url in urls]
        return {"videos": local_urls, "task_id": task_id, "raw": result}

async def generate_ai_image(
    prompt,
    size,
    quality,
    model,
    reference_images=None,
    provider_id="comfly",
    *,
    preserve_model=False,
    image_parameters=None,
):
    require_project_media_values_scope([
        str((ref.get("url") if isinstance(ref, dict) else getattr(ref, "url", "")) or "").strip()
        for ref in (reference_images or [])
        if str((ref.get("url") if isinstance(ref, dict) else getattr(ref, "url", "")) or "").strip()
    ])
    provider = get_api_provider_exact(provider_id) if preserve_model else get_api_provider(provider_id)
    if provider["id"] == "modelscope":
        return await generate_modelscope_provider_image(prompt, size, model, reference_images, provider)
    if is_codex_provider(provider):
        return await generate_codex_provider_image(prompt, size, model, reference_images, provider)
    if is_gemini_cli_provider(provider):
        return await generate_gemini_cli_provider_image(prompt, size, model, reference_images, provider)
    if is_jimeng_provider(provider):
        return await generate_jimeng_provider_image(prompt, size, model, reference_images, provider)
    if is_runninghub_provider(provider):
        return await generate_runninghub_provider_image(prompt, size, model, reference_images, provider, image_parameters)
    if effective_protocol(provider, model) == "gemini":
        return await generate_gemini_provider_image(prompt, size, model, reference_images, provider, image_parameters)
    if is_volcengine_provider(provider):
        return await generate_volcengine_provider_image(prompt, size, model, reference_images, provider, preserve_model)
    image_route = {
        "model": selected_model(model, IMAGE_MODEL),
        "params": {
            key: value for key, value in (image_parameters or {}).items()
            if key in {"aspect_ratio", "resolution"} and value not in (None, "")
        },
    } if preserve_model else route_openai_image_request(provider, model, size)
    model = image_route["model"]
    routed_image_params = image_route["params"]
    is_gpt2 = is_gpt_image_2_model(model)
    is_apimart = is_apimart_provider(provider)
    # 不对 GPT 尺寸做任何缩小/拦截：用户选什么尺寸就原样发给上游；
    # 若超过 GPT 的最大像素限制被上游拒绝，再由 friendly_image_error_detail 给出友好的像素上限提示。
    quality = str(quality or "").strip().lower()
    if quality not in {"low", "medium", "high"}:
        quality = ""
    base_url = (provider.get("base_url") or AI_BASE_URL).rstrip("/")
    if not base_url:
        raise HTTPException(status_code=400, detail=f"{provider.get('name') or provider['id']} 未配置 Base URL")
    gen_url = provider_endpoint_url(provider, "image_generation_endpoint", "/v1/images/generations")
    edit_url = provider_endpoint_url(provider, "image_edit_endpoint", "/v1/images/edits")
    refs = [ref for ref in (reference_images or []) if ref.get("url")]
    mask_refs = [ref for ref in refs if str(ref.get("role") or "").strip().lower() == "mask" or str(ref.get("name") or "").lower().endswith("_mask.png")]
    image_refs = [ref for ref in refs if ref not in mask_refs]
    image_request_mode = effective_image_request_mode(provider, model)
    request_timeout = httpx.Timeout(connect=20.0, read=1800.0, write=120.0, pool=20.0) if (is_gpt2 or is_apimart or image_request_mode in {"openai-json", "openai-video-proxy", "openai-responses"}) else AI_REQUEST_TIMEOUT
    async with httpx.AsyncClient(timeout=request_timeout) as client:
        response = None
        async def post_openai_edits(edit_files=None):
            data = {"model": model, "prompt": prompt, "size": size}
            if routed_image_params:
                # OpenAI-compatible Banana gateways otherwise infer a ratio
                # from arbitrary pixel dimensions (for example 3840x1648 ->
                # 240:103), which Gemini rejects. Send the supported preset
                # explicitly while retaining size for gateway compatibility.
                data.update(routed_image_params)
            if quality:
                data["quality"] = quality
            return await client.post(
                edit_url,
                headers=api_headers(json_body=False, provider=provider, model=model),
                data=data,
                files=edit_files if edit_files is not None else {},
            )

        if image_request_mode == "openai-video-proxy":
            body = {
                "model": model,
                "prompt": prompt,
                "aspect_ratio": routed_image_params.get("aspect_ratio") or runninghub_aspect_from_size(size, "1:1"),
            }
            if routed_image_params.get("resolution"):
                body["resolution"] = routed_image_params["resolution"]
            video_url = f"{base_url}/videos" if base_url.endswith("/v1") else f"{base_url}/v1/videos"
            refs_for_proxy = image_refs[:6]
            local_image_paths = [openai_video_proxy_local_image_path(ref) for ref in refs_for_proxy]
            has_local_images = any(local_image_paths)
            if has_local_images:
                form_data = {key: value for key, value in body.items()}
                for ref, local_path in zip(refs_for_proxy, local_image_paths):
                    if local_path:
                        continue
                    url = await openai_video_proxy_public_reference_url(ref)
                    if url:
                        existing_images = form_data.get("images")
                        if isinstance(existing_images, list):
                            existing_images.append(url)
                        elif existing_images:
                            form_data["images"] = [existing_images, url]
                        else:
                            form_data["images"] = url
                files = []
                for local_path in local_image_paths:
                    if not local_path:
                        continue
                    with open(local_path, "rb") as fh:
                        content = fh.read()
                    files.append(("images", (os.path.basename(local_path), content, content_type_for_path(local_path))))
                headers = api_headers(json_body=False, provider=provider, model=model)
                def post_video_proxy_multipart():
                    with httpx.Client(timeout=request_timeout) as sync_client:
                        return sync_client.post(video_url, headers=headers, data=form_data, files=files)
                response = await asyncio.to_thread(post_video_proxy_multipart)
            else:
                if refs_for_proxy:
                    body["images"] = [await openai_video_proxy_public_reference_url(ref) for ref in refs_for_proxy]
                response = await httpx_request_with_transient_retries(
                    client,
                    "POST",
                    video_url,
                    attempts=2,
                    headers=api_headers(provider=provider, model=model),
                    json=body,
                )
        elif image_request_mode == "openai-responses":
            tool = {"type": "image_generation"}
            tool["action"] = "edit" if image_refs else "generate"
            if size and str(size).strip().lower() != "auto":
                tool["size"] = size if preserve_model else responses_proxy_tool_size(size)
            if quality:
                tool["quality"] = quality
            size_instruction = responses_image_size_instruction(size)
            input_text = f"{size_instruction}\n\n{prompt}" if size_instruction else prompt
            content = [{"type": "input_text", "text": input_text}]
            force_public_refs = bool(locked_recommended_provider_rule(provider.get("id"), provider.get("name"), base_url))
            for ref in image_refs[:ONLINE_IMAGE_REFERENCE_MAX]:
                image_url = await responses_input_image_url(ref, require_public_url=force_public_refs)
                if image_url:
                    content.append({"type": "input_image", "image_url": image_url})
            body = {
                "model": model,
                "input": [{"role": "user", "content": content}],
                "tools": [tool],
                "tool_choice": {"type": "image_generation"},
            }
            responses_url = provider_endpoint_url(provider, "image_generation_endpoint", "/v1/responses")
            response = await post_openai_responses(client, responses_url, api_headers(provider=provider, model=model), body)
        elif image_request_mode == "openai-json":
            # Agnes 等“OpenAI JSON 图片接口”统一走 /images/generations：
            # 不使用 /images/edits，不传顶层 response_format/n/quality；
            # 文生图只传 extra_body.response_format，图生图把参考图放进 extra_body.image。
            extra_body = {"response_format": "url"}
            if image_refs:
                extra_body["image"] = [reference_to_data_url(ref, max_size=1536) for ref in image_refs[:ONLINE_IMAGE_REFERENCE_MAX]]
            body = {"model": model, "prompt": prompt, "size": size, "extra_body": extra_body}
            if routed_image_params:
                body.update(routed_image_params)
            response = await client.post(gen_url, headers=api_headers(provider=provider, model=model), json=body)
        elif is_apimart:
            apimart_size, resolution = (size, str((image_parameters or {}).get("resolution") or "")) if preserve_model else apimart_size_resolution(size)
            # APIMart 的 GPT-Image-2 图生图仍走 /images/generations，
            # 通过 image_urls 传参考图，不使用 OpenAI multipart /images/edits。
            body = {
                "model": model,
                "prompt": prompt,
                "n": 1,
                "size": apimart_size,
                "resolution": resolution,
                "official_fallback": False,
            }
            if image_refs:
                body["image_urls"] = [reference_to_data_url(ref, max_size=1536) for ref in image_refs[:ONLINE_IMAGE_REFERENCE_MAX]]
            if preserve_model and routed_image_params.get("aspect_ratio"):
                body["aspect_ratio"] = routed_image_params["aspect_ratio"]
            response = await client.post(gen_url, headers=api_headers(provider=provider, model=model), json=body)
        elif is_gpt2 and not image_refs and not mask_refs:
            body = {"model": model, "prompt": prompt, "size": size}
            if routed_image_params:
                body.update(routed_image_params)
            if quality:
                body["quality"] = quality
            response = await client.post(gen_url, headers=api_headers(provider=provider, model=model), json=body)
            if response.status_code >= 400 and images_api_unsupported(response):
                response = await post_openai_edits()
        elif image_refs:
            # 1) OpenAI 协议的图生图/编辑用 multipart 提交到 /images/edits；
            # GPT-Image-2 参考图不能走 /images/generations JSON，否则部分平台会忽略原图或报 Images API unsupported。
            files = []
            opened = []
            edit_failed_status = None
            edit_failed_text = ""
            try:
                for ref in image_refs[:ONLINE_IMAGE_REFERENCE_MAX]:
                    path = output_file_from_url(ref.get("url", ""))
                    if not path:
                        continue
                    fh = open(path, "rb")
                    opened.append(fh)
                    files.append(("image", (os.path.basename(path), fh, content_type_for_path(path))))
                if mask_refs:
                    mask_path = output_file_from_url(mask_refs[0].get("url", ""))
                    if mask_path:
                        fh = open(mask_path, "rb")
                        opened.append(fh)
                        files.append(("mask", (os.path.basename(mask_path), fh, content_type_for_path(mask_path))))
                try:
                    response = await post_openai_edits(files)
                    if response.status_code >= 400:
                        edit_failed_status = response.status_code
                        edit_failed_text = response.text[:500]
                        response = None
                except httpx.HTTPError as e:
                    edit_failed_status = -1
                    edit_failed_text = str(e)
                    response = None
            finally:
                for fh in opened:
                    fh.close()
            # 2) edits 失败 → 非 GPT-Image-2 可回退到 /images/generations + JSON image:[urls/base64]（grsai 风格）
            if response is None:
                if is_gpt2:
                    raise HTTPException(
                        status_code=502,
                        detail=f"GPT-Image-2 编辑接口 /images/edits 调用失败：{edit_failed_text[:300] or edit_failed_status}。已停止自动重试，避免上游可能已扣费后再次请求。"
                    )
                print(f"/images/edits failed ({edit_failed_status}): {edit_failed_text[:200]} → 回退到 /images/generations + image:[] JSON")
                image_payload = [reference_to_data_url(ref, max_size=1536) for ref in image_refs[:ONLINE_IMAGE_REFERENCE_MAX]]
                body = {
                    "model": model, "prompt": prompt, "size": size,
                    "response_format": "url", "n": 1,
                    "image": image_payload,
                }
                if routed_image_params:
                    body.update(routed_image_params)
                if quality:
                    body["quality"] = quality
                response = await client.post(gen_url, headers=api_headers(provider=provider, model=model), json=body)
                if response.status_code >= 400 and images_api_unsupported(response):
                    raise HTTPException(
                        status_code=502,
                        detail=f"编辑接口 /images/edits 调用失败，且该平台不支持 /images/generations：{edit_failed_text[:300] or edit_failed_status}"
                    )
        else:
            body = {"model": model, "prompt": prompt, "size": size, "response_format": "url", "n": 1}
            if routed_image_params:
                body.update(routed_image_params)
            if quality:
                body["quality"] = quality
            response = await client.post(
                gen_url,
                headers=api_headers(provider=provider, model=model),
                json=body,
            )
            if response.status_code >= 400 and images_api_unsupported(response):
                response = await post_openai_edits()
        try:
            response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            resp_text = (exc.response.text or "")[:800]
            provider_name = provider.get("name") or provider["id"]
            raise HTTPException(
                status_code=exc.response.status_code,
                detail=f"{provider_name} 图片接口错误（HTTP {exc.response.status_code}）：{resp_text or exc.response.reason_phrase}"
            ) from exc
        try:
            raw = response.json()
        except Exception as exc:
            resp_text = (response.text or "")[:800]
            provider_name = provider.get("name") or provider["id"]
            if is_fhl_provider(provider) and "_worker_keepalive" in resp_text:
                raise HTTPException(
                    status_code=502,
                    detail=f"{provider_name} 图片接口返回了未完成的 keepalive 响应，未返回图片数据。请重试或降低尺寸；响应片段：{resp_text[:200]}"
                ) from exc
            raise HTTPException(
                status_code=502,
                detail=f"{provider_name} 图片接口返回非 JSON 响应（状态 {response.status_code}）：{resp_text}"
            ) from exc
        try:
            return extract_image(raw), raw
        except HTTPException as exc:
            if image_request_mode == "openai-responses":
                fallback_image = responses_output_text_image(raw)
                if fallback_image:
                    return fallback_image, raw
                try:
                    print(f"RS 响应中没有图片，原始返回（截断）：{json.dumps(raw, ensure_ascii=False)[:800]}")
                except Exception:
                    pass
                raise HTTPException(status_code=502, detail=responses_no_image_detail(raw) or exc.detail)
            task_id = extract_task_id(raw)
            if not task_id:
                raise
        try:
            task_result = await wait_for_image_task(client, task_id, provider)
            return extract_image(task_result), task_result
        except HTTPException as exc:
            setattr(exc, "upstream_task_id", task_id)
            raise

def upstream_message_from_record(item):
    role = item.get("role")
    if role not in {"user", "assistant"} or item.get("type") == "image":
        return None
    attachments = item.get("attachments") or []
    if attachments and role == "user":
        text = item.get("content", "")
        blocks = attachment_text_blocks(attachments)
        if blocks:
            text = f"{text}\n\n以下是用户上传附件的可读内容，请在回答时参考：\n\n" + "\n\n---\n\n".join(blocks)
        content = [{"type": "text", "text": text}]
        image_urls = []
        for ref in image_references(attachments[:CHAT_ATTACHMENT_MAX]):
            url = reference_to_data_url(ref)
            if url:
                image_urls.append(url)
        image_urls.extend(attachment_embedded_image_data_urls(attachments[:CHAT_ATTACHMENT_MAX], max_images=max(0, CHAT_ATTACHMENT_MAX - len(image_urls))))
        for url in image_urls[:CHAT_ATTACHMENT_MAX]:
            content.append({"type": "image_url", "image_url": {"url": url}})
        return {"role": role, "content": content}
    return {"role": role, "content": item.get("content", "")}

AGENT_ACTIONS = {"chat", "generate_image", "edit_image"}
AGENT_IMAGE_KEYWORDS = [
    "生成", "画", "出图", "生图", "图片", "图像", "海报", "头像", "壁纸",
    "插画", "照片", "photo", "image", "picture", "draw", "generate",
]
AGENT_EDIT_KEYWORDS = [
    "修改", "改成", "换成", "调整", "优化", "编辑", "重绘", "上一张", "刚才",
    "这张", "那张", "参考图", "改图", "edit", "modify", "change", "revise",
]
CN_NUMERAL_MAP = {
    "一": 1, "二": 2, "两": 2, "俩": 2, "三": 3, "四": 4,
}

def latest_chat_image_refs(conversation, limit=1):
    refs = []
    for item in reversed(conversation.get("messages") or []):
        url = item.get("image_url") if isinstance(item, dict) else ""
        if url:
            refs.append({"url": url, "name": item.get("content") or "上一张图片", "role": "source"})
        if len(refs) >= limit:
            break
    return refs

def image_size_from_reference(ref):
    path = output_file_from_url(ref)
    if not path:
        return ""
    try:
        with Image.open(path) as img:
            width, height = img.size
        if width > 0 and height > 0:
            return f"{width}x{height}"
    except Exception as exc:
        print(f"[chat-agent] failed to read reference image size: {exc}")
    return ""

def chat_requested_image_count(message):
    text = str(message or "")
    match = re.search(r"(?<!\d)([1-4])\s*(?:张|幅|个|组|套)(?!\d)", text)
    if match:
        return max(1, min(4, int(match.group(1))))
    match = re.search(r"([一二两俩三四])\s*(?:张|幅|个|组|套)", text)
    if match:
        return max(1, min(4, CN_NUMERAL_MAP.get(match.group(1), 1)))
    return 1

def chat_split_parallel_prompts(prompt, count):
    text = str(prompt or "").strip()
    if count <= 1:
        return [text]
    noun_match = re.search(r"(.+?)(?:的)?(海报|头像|壁纸|插画|照片|图片|图像)\s*$", text)
    if not noun_match:
        return [text] * count
    prefix = noun_match.group(1).strip()
    suffix = noun_match.group(2)
    prefix = re.sub(r"(?:再)?(?:生成|画|绘制|制作|创建)\s*[1-4一二两俩三四]?\s*(?:张|幅|个|组|套)?", "", prefix).strip()
    prefix = re.sub(r"[,，、\s]+$", "", prefix).strip()
    if not prefix:
        return [text] * count
    candidates = [
        item.strip(" ，,、")
        for item in re.split(r"\s*(?:和|与|、|，|,|\+|＋)\s*", prefix)
        if item.strip(" ，,、")
    ]
    if len(candidates) < count:
        return [text] * count
    return [f"{item}的{suffix}" for item in candidates[:count]]

def pick_chat_image_provider(provider_id="", fallback_id=""):
    providers = [p for p in load_api_providers() if p.get("enabled", True) and (p.get("image_models") or [])]
    for target in (provider_id, fallback_id):
        clean = str(target or "").strip().lower()
        if clean:
            matched = next((p for p in providers if p.get("id") == clean), None)
            if matched:
                return matched
    if providers:
        primary = next((p for p in providers if p.get("primary")), None)
        return primary or providers[0]
    return get_api_provider(provider_id or fallback_id or "comfly")

def heuristic_agent_decision(message, refs, has_previous_image):
    text = str(message or "").strip().lower()
    has_image_word = any(key.lower() in text for key in AGENT_IMAGE_KEYWORDS)
    has_edit_word = any(key.lower() in text for key in AGENT_EDIT_KEYWORDS)
    if refs and (has_edit_word or has_image_word):
        return {"action": "edit_image", "prompt": message, "reply": ""}
    if has_previous_image and has_edit_word:
        return {"action": "edit_image", "prompt": message, "reply": ""}
    if has_image_word and not has_edit_word:
        return {"action": "generate_image", "prompt": message, "reply": ""}
    return {"action": "chat", "prompt": message, "reply": ""}

def parse_agent_decision(raw_text, message, refs, has_previous_image):
    text = str(raw_text or "").strip()
    data = None
    if text:
        match = re.search(r"\{[\s\S]*\}", text)
        candidate = match.group(0) if match else text
        try:
            data = json.loads(candidate)
        except Exception:
            data = None
    heuristic = heuristic_agent_decision(message, refs, has_previous_image)
    if not isinstance(data, dict):
        return heuristic
    action = str(data.get("action") or "").strip()
    if action not in AGENT_ACTIONS:
        action = heuristic["action"]
    prompt = str(data.get("prompt") or message).strip() or message
    reply = str(data.get("reply") or "").strip()
    if action == "edit_image" and not (refs or has_previous_image):
        action = "generate_image" if any(key.lower() in str(message).lower() for key in AGENT_IMAGE_KEYWORDS) else "chat"
    return {"action": action, "prompt": prompt, "reply": reply}

async def decide_chat_agent_action(payload, conversation, refs):
    has_previous_image = bool(latest_chat_image_refs(conversation, 1))
    fallback = heuristic_agent_decision(payload.message, refs, has_previous_image)
    provider_cfg = get_api_provider(payload.provider) if payload.provider not in ("modelscope",) else {}
    if is_codex_provider(provider_cfg):
        fallback["router_model"] = selected_model(payload.model, (provider_cfg.get("chat_models") or CODEX_DEFAULT_CHAT_MODELS)[0])
        return fallback
    if is_gemini_cli_provider(provider_cfg):
        fallback["router_model"] = selected_model(payload.model, (provider_cfg.get("chat_models") or GEMINI_CLI_DEFAULT_CHAT_MODELS)[0])
        return fallback
    chat_base, chat_hdrs, model = resolve_chat_provider(payload.provider, payload.model, payload.ms_model)
    history = conversation["messages"][-MAX_HISTORY_MESSAGES:]
    custom_system_prompt = str(getattr(payload, "system_prompt", "") or "").strip()
    system = (
        "你是图片创作聊天 Agent 的意图路由器。只返回 JSON，不要 Markdown。\n"
        "action 只能是 chat、generate_image、edit_image。\n"
        "chat: 普通问答或不需要调用图片工具。\n"
        "generate_image: 用户要求生成、绘制、创建新图片。\n"
        "edit_image: 用户要求修改参考图、上一张图、刚才生成的图，或上传了参考图并要求基于它变化。\n"
        "prompt 是交给生图/改图工具的完整中文提示词；普通聊天时也填用户原话。\n"
        "reply 是可选的短状态文本。"
    )
    upstream_messages = [{"role": "system", "content": system}]
    for item in history[-10:]:
        msg = upstream_message_from_record(item)
        if msg:
            upstream_messages.append(msg)
    upstream_messages.append({
        "role": "user",
        "content": (
            f"当前用户输入：{payload.message}\n"
            f"用户设置的系统提示词：{custom_system_prompt or '无'}\n"
            f"本次上传参考图数量：{len(refs)}\n"
            f"对话中是否已有上一张生成图：{'是' if has_previous_image else '否'}\n"
            "请返回 JSON，例如 {\"action\":\"generate_image\",\"prompt\":\"...\",\"reply\":\"...\"}"
        )
    })
    try:
        async with httpx.AsyncClient(timeout=AI_REQUEST_TIMEOUT) as client:
            req_body = {"model": model, "messages": upstream_messages}
            if is_apimart_provider(provider_cfg):
                req_body["stream"] = False
            response = await client.post(
                f"{chat_base}/chat/completions",
                headers=chat_hdrs,
                json=req_body,
            )
            response.raise_for_status()
            raw = response.json()
            decision = parse_agent_decision(text_from_chat_response(raw), payload.message, refs, has_previous_image)
            decision["router_model"] = model
            return decision
    except Exception as exc:
        print(f"[chat-agent] intent router fallback: {exc}")
        fallback["router_model"] = model
        return fallback

async def build_chat_text_reply(payload, conversation):
    provider_cfg = get_api_provider(payload.provider) if payload.provider not in ("modelscope",) else {}
    if is_codex_provider(provider_cfg):
        model = selected_model(payload.model, (provider_cfg.get("chat_models") or CODEX_DEFAULT_CHAT_MODELS)[0])
        payload.model = model
        text, raw = await codex_chat_text(payload, conversation["messages"][-MAX_HISTORY_MESSAGES:])
        return {
            "id": uuid.uuid4().hex,
            "role": "assistant",
            "content": text,
            "created_at": now_ms(),
            "model": model,
            "raw_usage": None,
            "raw": raw,
        }
    if is_gemini_cli_provider(provider_cfg):
        model = selected_model(payload.model, (provider_cfg.get("chat_models") or GEMINI_CLI_DEFAULT_CHAT_MODELS)[0])
        payload.model = model
        text, raw = await gemini_cli_chat_text(payload, conversation["messages"][-MAX_HISTORY_MESSAGES:])
        return {
            "id": uuid.uuid4().hex,
            "role": "assistant",
            "content": text,
            "created_at": now_ms(),
            "model": model,
            "raw_usage": None,
            "raw": raw,
        }
    chat_base, chat_hdrs, model = resolve_chat_provider(payload.provider, payload.model, payload.ms_model)
    is_apimart = is_apimart_provider(provider_cfg)
    upstream_messages = [{"role": "system", "content": chat_system_prompt(payload)}]
    for item in conversation["messages"][-MAX_HISTORY_MESSAGES:]:
        msg = upstream_message_from_record(item)
        if msg:
            upstream_messages.append(msg)
    try:
        async with httpx.AsyncClient(timeout=AI_REQUEST_TIMEOUT) as client:
            req_body = {"model": model, "messages": upstream_messages}
            if is_apimart:
                req_body["stream"] = False
            response = await client.post(f"{chat_base}/chat/completions", headers=chat_hdrs, json=req_body)
            response.raise_for_status()
            raw = response.json()
    except httpx.HTTPStatusError as exc:
        body = exc.response.text or ""
        friendly = friendly_chat_error_detail(body, model, provider_cfg)
        raise HTTPException(status_code=exc.response.status_code, detail=friendly or f"上游接口错误：{body}") from exc
    except httpx.HTTPError as exc:
        raise HTTPException(status_code=502, detail=f"请求上游接口失败：{exc}") from exc
    raw_data = unwrap_apimart_response(raw) if isinstance(raw, dict) else raw
    return {
        "id": uuid.uuid4().hex,
        "role": "assistant",
        "content": text_from_chat_response(raw).strip() or "接口返回了空回复。",
        "created_at": now_ms(),
        "model": model,
        "raw_usage": raw_data.get("usage") if isinstance(raw_data, dict) else None,
    }

# --- 路由接口 ---

@app.get("/")
async def index():
    return static_html_response("project-list.html")

@app.get("/api/view")
def view_image(filename: str, type: str = "input", subfolder: str = ""):
    # 所有项目媒体都由本机项目库持有；生成请求只发往在线服务商。
    if not subfolder and type in ("input", "output"):
        safe_name = os.path.basename(filename or "")
        if safe_name:
            local_path = output_path_for(safe_name, "input" if type == "input" else "output")
            if os.path.isfile(local_path):
                return FileResponse(local_path, media_type=content_type_for_path(local_path))
    raise HTTPException(status_code=404, detail="Image not found on any available backend")

@app.get("/api/download-output")
def download_output(request: Request, url: str, name: str = "", inline: bool = False):
    url = rewrite_runninghub_file_url(url)
    path = output_file_from_url(url)
    if not path:
        path = local_media_file_by_basename(filename_from_media_url(url, ""))
    if path:
        filename = sanitize_export_filename(os.path.basename(name) if name else os.path.basename(path), os.path.basename(path))
        return FileResponse(path, media_type=content_type_for_path(path), filename=None if inline else filename)
    # 远程文件：流式代理，绝不把整段视频/大文件读进内存（否则多个视频同时代理会撑爆内存、拖垮单进程服务）。
    parsed = urllib.parse.urlparse(str(url or "").strip())
    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        raise HTTPException(status_code=400, detail="无效的下载地址")
    try:
        upstream_headers = {"User-Agent": "Firefly-Canvas/1.0"}
        range_header = request.headers.get("range")
        if range_header:
            upstream_headers["Range"] = range_header
        upstream = requests.get(
            url, stream=True, timeout=(10, 60),
            headers=upstream_headers,
        )
        upstream.raise_for_status()
    except requests.RequestException as exc:
        raise HTTPException(status_code=502, detail=f"远程文件下载失败：{exc}")
    content_type = upstream.headers.get("content-type") or "application/octet-stream"
    fallback = filename_from_media_url(url, "download.bin")
    filename = sanitize_export_filename(os.path.basename(name) if name else fallback, fallback)
    disposition = "inline" if inline else "attachment"
    headers = {"Content-Disposition": f"{disposition}; filename*=UTF-8''{urllib.parse.quote(filename)}"}
    for key in ("content-range", "accept-ranges"):
        value = upstream.headers.get(key)
        if value:
            headers["-".join(part.capitalize() for part in key.split("-"))] = value

    def stream_remote():
        try:
            for chunk in upstream.iter_content(chunk_size=256 * 1024):
                if chunk:
                    yield chunk
        finally:
            upstream.close()

    return StreamingResponse(stream_remote(), media_type=content_type, headers=headers, status_code=upstream.status_code)

@app.post("/api/ai/upload")
async def upload_ai_reference(files: List[UploadFile] = File(...)):
    uploaded = []
    image_exts = {".png", ".jpg", ".jpeg", ".webp", ".gif"}
    video_exts = {".mp4", ".webm", ".mov", ".m4v", ".flv"}
    audio_exts = {".mp3", ".wav", ".m4a", ".aac", ".ogg", ".flac"}
    doc_exts = {".pdf", ".txt", ".md", ".markdown", ".doc", ".docx", ".xls", ".xlsx", ".csv", ".json", ".zip", ".yaml", ".yml", ".log"}
    max_upload_bytes = 50 * 1024 * 1024
    for file in files:
        content = await file.read()
        if not content:
            continue
        if len(content) > max_upload_bytes:
            raise HTTPException(status_code=413, detail=f"{file.filename or '文件'} 超过 50MB，无法上传")
        ext = os.path.splitext(file.filename or "")[1].lower()
        content_type = (file.content_type or "").lower()
        kind = "image"
        if ext in video_exts or content_type.startswith("video/"):
            kind = "video"
            if ext not in video_exts:
                ext = ".webm" if "webm" in content_type else ".mov" if "quicktime" in content_type else ".mp4"
        elif ext in audio_exts or content_type.startswith("audio/"):
            kind = "audio"
            if ext not in audio_exts:
                ext = ".wav" if "wav" in content_type else ".ogg" if "ogg" in content_type else ".m4a" if "mp4" in content_type else ".mp3"
        elif ext in image_exts or content_type.startswith("image/"):
            kind = "image"
            if ext not in image_exts:
                ext = ".jpg" if "jpeg" in content_type else ".webp" if "webp" in content_type else ".gif" if "gif" in content_type else ".png"
        elif ext in doc_exts or content_type.startswith(("text/", "application/")):
            kind = "file"
            if not ext:
                ext = mimetypes.guess_extension(content_type) or ".bin"
        else:
            kind = "file"
            if not ext:
                ext = ".bin"
        filename = f"ai_ref_{uuid.uuid4().hex[:12]}{ext}"
        path = output_path_for(filename, "input")
        with open(path, "wb") as f:
            f.write(content)
        uploaded.append({"url": output_url_for(filename, "input"), "name": file.filename or filename, "kind": kind, "mime": content_type})
    return {"files": uploaded}

class Base64UploadRequest(BaseModel):
    data: str = ""            # 纯 base64 或 data:URL
    name: str = ""
    content_type: str = ""

@app.post("/api/ai/upload-base64")
async def upload_ai_base64(payload: Base64UploadRequest):
    """以 base64 JSON 方式上传字节到 assets/input，返回 /assets 地址。
    给不便用 multipart/FormData 的客户端（如 PS UXP 面板）用——UXP 的 fetch+FormData 经常发不出有效 multipart。"""
    raw = (payload.data or "").strip()
    ct = (payload.content_type or "").split(";", 1)[0].strip().lower()
    if raw.startswith("data:"):
        header, _, raw = raw.partition(",")
        if not ct:
            ct = header[5:].split(";", 1)[0].strip().lower()
    try:
        content = base64.b64decode(raw, validate=False)
    except Exception:
        raise HTTPException(status_code=400, detail="数据无法解码")
    if not content:
        raise HTTPException(status_code=400, detail="内容为空")
    if len(content) > 50 * 1024 * 1024:
        raise HTTPException(status_code=413, detail="超过 50MB")
    kind, ext = _local_upload_kind_ext(payload.name or "", ct or "image/png")
    if kind is None:
        kind, ext = "image", ".png"
    filename = f"ai_ref_{uuid.uuid4().hex[:12]}{ext}"
    path = output_path_for(filename, "input")
    with open(path, "wb") as f:
        f.write(content)
    return {"files": [{"url": output_url_for(filename, "input"), "name": payload.name or filename, "kind": kind}]}

def _local_upload_kind_ext(filename, content_type):
    image_exts = {".png", ".jpg", ".jpeg", ".webp", ".gif"}
    video_exts = {".mp4", ".webm", ".mov", ".m4v", ".flv"}
    audio_exts = {".mp3", ".wav", ".m4a", ".aac", ".ogg", ".flac"}
    ext = os.path.splitext(filename or "")[1].lower()
    ct = (content_type or "").lower()
    if ext in video_exts or ct.startswith("video/"):
        if ext not in video_exts:
            ext = ".webm" if "webm" in ct else ".mov" if "quicktime" in ct else ".mp4"
        return "video", ext
    if ext in audio_exts or ct.startswith("audio/"):
        if ext not in audio_exts:
            ext = ".wav" if "wav" in ct else ".ogg" if "ogg" in ct else ".m4a" if "mp4" in ct else ".mp3"
        return "audio", ext
    if ext in image_exts or ct.startswith("image/"):
        if ext not in image_exts:
            ext = ".jpg" if "jpeg" in ct else ".webp" if "webp" in ct else ".gif" if "gif" in ct else ".png"
        return "image", ext
    return None, ext

def _local_upload_display_name(filename):
    # 文件名形如 up_<hex>_<原始名>；去掉前缀还原展示名
    base = os.path.basename(str(filename or ""))
    m = re.match(r"^up_[0-9a-f]{12}_(.+)$", base)
    return m.group(1) if m else base

def _local_upload_rel_path(value):
    text = str(value or "").replace("\\", "/").strip().lstrip("/")
    if not text:
        return ""
    norm = os.path.normpath(text).replace("\\", "/")
    if norm in {".", ""}:
        return ""
    if norm.startswith("../") or norm == ".." or os.path.isabs(norm):
        raise HTTPException(status_code=400, detail="非法路径")
    return norm

def _local_upload_abs(rel):
    rel_path = _local_upload_rel_path(rel)
    path = os.path.abspath(os.path.join(LOCAL_UPLOAD_DIR, rel_path))
    root = os.path.abspath(LOCAL_UPLOAD_DIR)
    try:
        common = os.path.commonpath([root, path])
    except ValueError:
        raise HTTPException(status_code=400, detail="非法路径")
    if common != root:
        raise HTTPException(status_code=400, detail="非法路径")
    return rel_path, path

def _local_upload_safe_path(name):
    filename, path = _local_upload_abs(name)
    if not filename:
        raise HTTPException(status_code=400, detail="文件名不能为空")
    return filename, path

def _local_upload_safe_folder(path_value):
    rel, path = _local_upload_abs(path_value)
    return rel, path

def _local_upload_safe_folder_name(name):
    cleaned = sanitize_asset_name(os.path.basename(str(name or "").strip()), "")
    cleaned = re.sub(r"[\\/]+", "_", cleaned).strip(" ._")
    if not cleaned:
        raise HTTPException(status_code=400, detail="文件夹名称不能为空")
    return cleaned[:60]

def _local_upload_safe_file_stem(name):
    raw = os.path.splitext(os.path.basename(str(name or "").strip()))[0]
    cleaned = sanitize_asset_name(raw, "")
    cleaned = re.sub(r"[\\/]+", "_", cleaned).strip(" ._")
    if not cleaned:
        raise HTTPException(status_code=400, detail="文件名称不能为空")
    return cleaned[:120]

def _local_upload_caption_path(filename):
    return os.path.splitext(os.path.join(LOCAL_UPLOAD_DIR, filename))[0] + ".txt"

def _read_local_upload_caption(filename):
    caption_path = _local_upload_caption_path(filename)
    if not os.path.isfile(caption_path):
        return "", ""
    try:
        with open(caption_path, "r", encoding="utf-8-sig") as f:
            text = f.read()
    except UnicodeDecodeError:
        with open(caption_path, "r", encoding="gb18030", errors="replace") as f:
            text = f.read()
    except OSError:
        return "", ""
    return text, os.path.basename(caption_path)

def _local_upload_item(filename):
    path = os.path.join(LOCAL_UPLOAD_DIR, filename)
    rel = _local_upload_rel_path(filename)
    try:
        stat = os.stat(path)
        size = stat.st_size
        created_at = stat.st_mtime
    except OSError:
        size = 0
        created_at = 0
    kind, _ = _local_upload_kind_ext(filename, "")
    item = {
        "id": rel,
        "file": rel,
        "name": _local_upload_display_name(rel),
        "url": f"/api/storage-files/local/{urllib.parse.quote(rel, safe='/')}",
        "kind": kind or "image",
        "size": size,
        "created_at": created_at,
        "folder": os.path.dirname(rel).replace("\\", "/"),
    }
    if kind == "image":
        try:
            with Image.open(path) as img:
                item["natural_w"], item["natural_h"] = img.size
                item["width"], item["height"] = img.size
        except Exception:
            pass
        caption, caption_file = _read_local_upload_caption(filename)
        item["caption"] = caption
        item["caption_file"] = caption_file
        classification = _read_local_upload_classification(filename)
        if classification:
            item["classification"] = classification
    return item

def _local_upload_folder_node(path="", name="全部上传"):
    rel = _local_upload_rel_path(path)
    return {
        "id": rel or "__root__",
        "path": rel,
        "name": name if not rel else os.path.basename(rel),
        "items": [],
        "children": [],
    }

def _local_upload_tree_and_items():
    root_node = _local_upload_folder_node("", "全部上传")
    folder_map = {"": root_node}
    items = []
    for current, dirs, files in os.walk(LOCAL_UPLOAD_DIR):
        dirs[:] = sorted([d for d in dirs if not d.startswith(".") and not d.startswith("._")], key=str.lower)
        rel_dir = os.path.relpath(current, LOCAL_UPLOAD_DIR).replace("\\", "/")
        if rel_dir == ".":
            rel_dir = ""
        node = folder_map.get(rel_dir)
        if node is None:
            node = _local_upload_folder_node(rel_dir)
            folder_map[rel_dir] = node
        for dirname in dirs:
            child_rel = f"{rel_dir}/{dirname}".lstrip("/")
            child = _local_upload_folder_node(child_rel)
            folder_map[child_rel] = child
            node["children"].append(child)
        for name in sorted(files, key=str.lower):
            if name.startswith(".") or name.startswith("._"):
                continue
            rel_file = f"{rel_dir}/{name}".lstrip("/")
            kind, _ = _local_upload_kind_ext(name, "")
            if kind is None:
                continue
            item = _local_upload_item(rel_file)
            node["items"].append(item)
            items.append(item)
    def fill_counts(node):
        total = len(node.get("items") or [])
        for child in node.get("children") or []:
            total += fill_counts(child)
        node["count"] = total
        return total
    fill_counts(root_node)
    items.sort(key=lambda it: it.get("created_at") or 0, reverse=True)
    return root_node, items

_DOUBLE_EXT_RE = re.compile(r'(\.[A-Za-z0-9]{1,5})\1$', re.IGNORECASE)
_DOUBLE_EXT_MEDIA = {".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp", ".avif",
                     ".mp4", ".webm", ".mov", ".m4v", ".flv"}

def migrate_double_extension_uploads():
    """修复历史遗留的双重扩展名（如 foo.png.png）：去掉重复的一层，并同步重命名 caption/classification 旁车文件。
    旧版 URL 导入会把自带扩展名的 entry.name 又拼一次 ext，导致文件名重复后缀、URL 对不上而无法显示。"""
    if not os.path.isdir(LOCAL_UPLOAD_DIR):
        return
    renamed = 0
    for current, _dirs, files in os.walk(LOCAL_UPLOAD_DIR):
        for name in files:
            m = _DOUBLE_EXT_RE.search(name)
            if not m or m.group(1).lower() not in _DOUBLE_EXT_MEDIA:
                continue
            old_path = os.path.join(current, name)
            new_path = os.path.join(current, name[:-len(m.group(1))])  # 去掉末尾重复的一层扩展名
            if os.path.exists(new_path):
                continue
            try:
                os.rename(old_path, new_path)
            except OSError:
                continue
            renamed += 1
            # caption/classification 旁车以「去掉一层扩展名」为基名，需同步改名以保留标注
            old_base = os.path.splitext(old_path)[0]
            new_base = os.path.splitext(new_path)[0]
            for suffix in (".classification.json", ".txt"):
                src_side, dst_side = old_base + suffix, new_base + suffix
                if os.path.exists(src_side) and not os.path.exists(dst_side):
                    try:
                        os.rename(src_side, dst_side)
                    except OSError:
                        pass
    if renamed:
        print(f"修复双重扩展名素材: {renamed} 个")

def _sniff_image_ext_bytes(head):
    """按文件头魔数判断真实图片格式，返回规范扩展名（含点），无法识别返回 None。"""
    head = head or b""
    if head.startswith(b"\x89PNG\r\n\x1a\n"):
        return ".png"
    if head.startswith(b"\xff\xd8\xff"):
        return ".jpg"
    if head[:4] == b"RIFF" and head[8:12] == b"WEBP":
        return ".webp"
    if head[:6] in (b"GIF87a", b"GIF89a"):
        return ".gif"
    if head[:2] == b"BM":
        return ".bmp"
    return None

def _sniff_image_ext(path):
    try:
        with open(path, "rb") as f:
            return _sniff_image_ext_bytes(f.read(16))
    except OSError:
        return None

def migrate_mislabeled_image_extensions():
    """有些采集来的图片内容与扩展名不符（例如 WebP 内容却叫 .png），导致服务端按错误 content-type 返回、
    严格的客户端（PS UXP）解不出来。这里按真实魔数纠正扩展名，并同步重命名 caption/classification 旁车。"""
    if not os.path.isdir(LOCAL_UPLOAD_DIR):
        return
    img_exts = {".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp"}
    fixed = 0
    for current, _dirs, files in os.walk(LOCAL_UPLOAD_DIR):
        for name in files:
            ext = os.path.splitext(name)[1].lower()
            if ext not in img_exts:
                continue
            path = os.path.join(current, name)
            real = _sniff_image_ext(path)
            if not real:
                continue
            # .jpg/.jpeg 视为同一种，不互相纠正
            if real == ext or (real == ".jpg" and ext == ".jpeg"):
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
            for suffix in (".classification.json", ".txt"):
                src_side, dst_side = old_base + suffix, new_base + suffix
                if os.path.isfile(src_side) and not os.path.exists(dst_side):
                    try:
                        os.rename(src_side, dst_side)
                    except OSError:
                        pass
    if fixed:
        print(f"纠正图片扩展名(内容与后缀不符): {fixed} 个")

@app.post("/api/local-assets/upload")
async def upload_local_assets(files: List[UploadFile] = File(...), folder: str = Form("")):
    uploaded = []
    folder_rel, folder_abs = _local_upload_safe_folder(folder)
    os.makedirs(folder_abs, exist_ok=True)
    for file in files:
        content = await file.read()
        if not content:
            continue
        kind, ext = _local_upload_kind_ext(file.filename, file.content_type)
        if kind is None:
            continue
        base = os.path.splitext(os.path.basename(file.filename or "file"))[0]
        base = re.sub(r"[^0-9A-Za-z一-鿿._-]+", "_", base).strip("_") or "file"
        base = base[:60]
        filename = f"up_{uuid.uuid4().hex[:12]}_{base}{ext}"
        rel_name = f"{folder_rel}/{filename}".lstrip("/")
        path = os.path.join(folder_abs, filename)
        with open(path, "wb") as f:
            f.write(content)
        if kind == "image":
            classification = await classify_asset_image_best_effort(path)
            if classification:
                _write_local_upload_classification(rel_name, classification)
        uploaded.append(_local_upload_item(rel_name))
    return {"files": uploaded}

@app.post("/api/local-assets/import-urls")
async def import_local_assets_from_urls(payload: LocalAssetUrlImportRequest):
    uploaded = []
    results = []
    folder_rel, folder_abs = _local_upload_safe_folder(payload.folder)
    os.makedirs(folder_abs, exist_ok=True)
    timeout = httpx.Timeout(connect=20.0, read=120.0, write=30.0, pool=20.0)
    async with httpx.AsyncClient(timeout=timeout, follow_redirects=True, headers={"User-Agent": "Firefly-Canvas-Asset-Importer/1.0"}) as client:
        for entry in (payload.items or [])[:200]:
            src_url = str(entry.url or "").strip()
            inline_data = str(entry.data or "").strip()
            result = {"url": src_url, "ok": False, "file": "", "error": ""}
            if not inline_data and not src_url.startswith(("http://", "https://")):
                result["error"] = "仅支持 http(s) 素材地址"
                results.append(result)
                continue
            try:
                if inline_data:
                    # 插件已在网页上下文里把字节读成 base64（dataURL 形如 data:<ct>;base64,<payload>）
                    content_type = str(entry.content_type or "").split(";", 1)[0].strip().lower()
                    b64 = inline_data
                    if inline_data.startswith("data:"):
                        header, _, b64 = inline_data.partition(",")
                        if not content_type:
                            content_type = header[5:].split(";", 1)[0].strip().lower()
                    try:
                        content = base64.b64decode(b64, validate=False)
                    except Exception:
                        raise HTTPException(status_code=400, detail="素材数据无法解码")
                    name_path = urllib.parse.urlparse(src_url).path
                else:
                    response = await client.get(src_url)
                    response.raise_for_status()
                    content_type = response.headers.get("Content-Type", "").split(";", 1)[0].strip().lower()
                    content = response.content
                    name_path = urllib.parse.urlparse(src_url).path
                kind, ext = _local_upload_kind_ext(name_path, content_type)
                if kind == "image":
                    real = _sniff_image_ext_bytes(content[:16])   # 以真实内容为准，避免 webp 被叫成 .png 等
                    if real and not (real == ".jpg" and ext == ".jpeg"):
                        ext = real
                if kind not in ("image", "video"):
                    raise HTTPException(status_code=400, detail=f"不是图片或视频资源：{content_type or src_url}")
                if not content:
                    raise HTTPException(status_code=400, detail="素材内容为空")
                # entry.name 可能自带扩展名（采集器常传完整文件名），先 splitext 去掉，否则会和下面拼接的 ext 叠成 .png.png
                if entry.name:
                    base = os.path.splitext(entry.name)[0]
                else:
                    base = os.path.splitext(os.path.basename(urllib.parse.unquote(name_path)))[0]
                base = base or ("web-video" if kind == "video" else "web-image")
                base = re.sub(r"[^0-9A-Za-z一-鿿._-]+", "_", base).strip("_") or ("web-video" if kind == "video" else "web-image")
                base = base[:60]
                # 兜底：若 base 末尾已是同一扩展名，去掉一层再拼，杜绝重复后缀
                if ext and base.lower().endswith(ext.lower()):
                    base = base[:-len(ext)].rstrip(".") or ("web-video" if kind == "video" else "web-image")
                filename = f"up_{uuid.uuid4().hex[:12]}_{base}{ext}"
                rel_name = f"{folder_rel}/{filename}".lstrip("/")
                path = os.path.join(folder_abs, filename)
                with open(path, "wb") as f:
                    f.write(content)
                if payload.classify and kind == "image":
                    classification = await classify_asset_image_best_effort(path, payload.provider, payload.model, payload.ms_model, payload.prompt)
                    if classification:
                        _write_local_upload_classification(rel_name, classification)
                item = _local_upload_item(rel_name)
                uploaded.append(item)
                result.update({"ok": True, "file": rel_name, "item": item})
            except HTTPException as exc:
                result["error"] = str(exc.detail or "导入失败")
            except Exception as exc:
                result["error"] = str(exc) or "导入失败"
            results.append(result)
    return {"ok": True, "count": len(uploaded), "files": uploaded, "items": results}

@app.get("/api/local-assets")
async def list_local_assets():
    tree, items = _local_upload_tree_and_items()
    return {"items": items, "tree": tree}

@app.post("/api/local-assets/folders")
async def create_local_asset_folder(payload: LocalAssetFolderRequest, request: Request):
    ensure_same_origin_request(request)
    parent_rel, parent_abs = _local_upload_safe_folder(payload.parent)
    if not os.path.isdir(parent_abs):
        raise HTTPException(status_code=404, detail="父文件夹不存在")
    name = _local_upload_safe_folder_name(payload.name)
    rel = f"{parent_rel}/{name}".lstrip("/")
    _, abs_path = _local_upload_safe_folder(rel)
    if os.path.exists(abs_path):
        raise HTTPException(status_code=400, detail="同名文件夹已存在")
    os.makedirs(abs_path, exist_ok=False)
    tree, items = _local_upload_tree_and_items()
    return {"ok": True, "folder": {"path": rel, "name": name}, "tree": tree, "items": items}

@app.patch("/api/local-assets/folders")
async def rename_local_asset_folder(payload: LocalAssetFolderRequest, request: Request):
    ensure_same_origin_request(request)
    rel, abs_path = _local_upload_safe_folder(payload.path)
    if not rel:
        raise HTTPException(status_code=400, detail="根目录不能重命名")
    if not os.path.isdir(abs_path):
        raise HTTPException(status_code=404, detail="文件夹不存在")
    name = _local_upload_safe_folder_name(payload.name)
    parent = os.path.dirname(rel).replace("\\", "/")
    new_rel = f"{parent}/{name}".lstrip("/")
    _, new_abs = _local_upload_safe_folder(new_rel)
    if os.path.exists(new_abs):
        raise HTTPException(status_code=400, detail="同名文件夹已存在")
    os.rename(abs_path, new_abs)
    tree, items = _local_upload_tree_and_items()
    return {"ok": True, "folder": {"path": new_rel, "name": name}, "tree": tree, "items": items}

@app.patch("/api/local-assets/items")
async def rename_local_asset_item(payload: LocalAssetRenameRequest, request: Request):
    ensure_same_origin_request(request)
    rel, abs_path = _local_upload_safe_path(payload.path)
    if not os.path.isfile(abs_path):
        raise HTTPException(status_code=404, detail="本地素材不存在")
    kind, ext = _local_upload_kind_ext(rel, "")
    if kind is None:
        raise HTTPException(status_code=400, detail="不支持的素材类型")
    new_stem = _local_upload_safe_file_stem(payload.name)
    old_ext = os.path.splitext(rel)[1] or ext
    parent = os.path.dirname(rel).replace("\\", "/")
    new_rel = f"{parent}/{new_stem}{old_ext}".lstrip("/")
    if new_rel == rel:
        tree, items = _local_upload_tree_and_items()
        return {"ok": True, "item": _local_upload_item(rel), "tree": tree, "items": items}
    _, new_abs = _local_upload_abs(new_rel)
    if os.path.exists(new_abs):
        raise HTTPException(status_code=400, detail="同名素材已存在")
    os.rename(abs_path, new_abs)
    old_caption = _local_upload_caption_path(rel)
    new_caption = _local_upload_caption_path(new_rel)
    if os.path.isfile(old_caption) and not os.path.exists(new_caption):
        os.rename(old_caption, new_caption)
    old_classification = _local_upload_classification_path(rel)
    new_classification = _local_upload_classification_path(new_rel)
    if os.path.isfile(old_classification) and not os.path.exists(new_classification):
        os.rename(old_classification, new_classification)
    tree, items = _local_upload_tree_and_items()
    return {"ok": True, "item": _local_upload_item(new_rel), "old_path": rel, "tree": tree, "items": items}

@app.post("/api/local-assets/delete")
async def delete_local_assets(payload: dict, request: Request):
    ensure_same_origin_request(request)
    names = payload.get("names") if isinstance(payload, dict) else None
    if not isinstance(names, list):
        names = []
    deleted = []
    for name in names:
        try:
            rel, path = _local_upload_safe_path(name)
        except HTTPException:
            continue
        if os.path.isfile(path):
            try:
                os.remove(path)
                txt_path = _local_upload_caption_path(rel)
                if os.path.isfile(txt_path):
                    os.remove(txt_path)
                cls_path = _local_upload_classification_path(rel)
                if os.path.isfile(cls_path):
                    os.remove(cls_path)
                deleted.append(rel)
            except OSError:
                pass
    return {"deleted": deleted}

@app.post("/api/local-assets/move")
async def move_local_assets(payload: dict, request: Request):
    """把选中的本地素材移动到目标文件夹（folder 为空表示根目录）；连同 .txt / .classification.json 兄弟文件一起搬。"""
    ensure_same_origin_request(request)
    names = payload.get("names") if isinstance(payload, dict) else None
    if not isinstance(names, list) or not names:
        raise HTTPException(status_code=400, detail="没有选择素材")
    folder_value = str(payload.get("folder") or "").strip() if isinstance(payload, dict) else ""
    target_rel, target_abs = _local_upload_safe_folder(folder_value)
    if target_rel and not os.path.isdir(target_abs):
        raise HTTPException(status_code=404, detail="目标文件夹不存在")
    moved = 0
    for name in names:
        try:
            rel, abs_path = _local_upload_safe_path(name)
        except HTTPException:
            continue
        if not os.path.isfile(abs_path):
            continue
        base = os.path.basename(rel)
        new_rel = f"{target_rel}/{base}".lstrip("/") if target_rel else base
        if new_rel == rel:
            continue  # 已在目标文件夹，跳过
        _, new_abs = _local_upload_abs(new_rel)
        if os.path.exists(new_abs):
            # 同名冲突：加短随机后缀，避免覆盖已有文件
            stem, ext = os.path.splitext(base)
            base = f"{stem}_{uuid.uuid4().hex[:6]}{ext}"
            new_rel = f"{target_rel}/{base}".lstrip("/") if target_rel else base
            _, new_abs = _local_upload_abs(new_rel)
        try:
            os.makedirs(os.path.dirname(new_abs), exist_ok=True)
            os.rename(abs_path, new_abs)
            for src_sib, dst_sib in (
                (_local_upload_caption_path(rel), _local_upload_caption_path(new_rel)),
                (_local_upload_classification_path(rel), _local_upload_classification_path(new_rel)),
            ):
                if os.path.isfile(src_sib) and not os.path.exists(dst_sib):
                    os.rename(src_sib, dst_sib)
            moved += 1
        except OSError:
            continue
    tree, items = _local_upload_tree_and_items()
    return {"ok": True, "moved": moved, "items": items, "tree": tree}

@app.post("/api/local-assets/caption")
async def caption_local_assets(payload: LocalAssetCaptionRequest):
    prompt = (payload.prompt or "描述图片").strip() or "描述图片"
    items = []
    ok_count = 0
    for name in (payload.names or [])[:100]:
        item = {"name": name, "ok": False, "caption": "", "caption_file": "", "error": ""}
        try:
            filename, path = _local_upload_safe_path(name)
            if not os.path.isfile(path):
                raise HTTPException(status_code=404, detail="文件不存在")
            kind, _ = _local_upload_kind_ext(filename, "")
            if kind != "image":
                raise HTTPException(status_code=400, detail="仅支持图片素材反推提示词")
            caption, resolved_model = await caption_image_with_provider(
                path,
                prompt,
                payload.provider,
                payload.model,
                payload.ms_model,
            )
            txt_path = _local_upload_caption_path(filename)
            with open(txt_path, "w", encoding="utf-8", newline="") as f:
                f.write(caption)
            item.update({
                "ok": True,
                "name": filename,
                "caption": caption,
                "caption_file": os.path.basename(txt_path),
                "model": resolved_model,
            })
            ok_count += 1
        except HTTPException as exc:
            item["error"] = str(exc.detail or "反推失败")
        except Exception as exc:
            item["error"] = str(exc) or "反推失败"
        items.append(item)
    return {"ok": True, "count": ok_count, "items": items}

@app.post("/api/local-assets/classify")
async def classify_local_assets(payload: LocalAssetClassifyRequest):
    items = []
    ok_count = 0
    for name in (payload.names or [])[:80]:
        item = {"name": name, "ok": False, "classification": None, "classification_file": "", "error": ""}
        try:
            filename, path = _local_upload_safe_path(name)
            if not os.path.isfile(path):
                raise HTTPException(status_code=404, detail="文件不存在")
            kind, _ = _local_upload_kind_ext(filename, "")
            if kind != "image":
                raise HTTPException(status_code=400, detail="仅支持图片素材智能分类")
            classification = await classify_image_with_provider(
                path,
                payload.provider,
                payload.model,
                payload.ms_model,
                payload.prompt,
            )
            _write_local_upload_classification(filename, classification)
            item.update({
                "ok": True,
                "name": filename,
                "classification": classification,
                "classification_file": os.path.basename(_local_upload_classification_path(filename)),
                "model": classification.get("model") or "",
            })
            ok_count += 1
        except HTTPException as exc:
            item["error"] = str(exc.detail or "智能分类失败")
        except Exception as exc:
            item["error"] = str(exc) or "智能分类失败"
        items.append(item)
    return {"ok": True, "count": ok_count, "items": items}

@app.patch("/api/local-assets/caption")
async def save_local_asset_caption(payload: LocalAssetCaptionSaveRequest):
    filename, path = _local_upload_safe_path(payload.name)
    if not os.path.isfile(path):
        raise HTTPException(status_code=404, detail="文件不存在")
    kind, _ = _local_upload_kind_ext(filename, "")
    if kind != "image":
        raise HTTPException(status_code=400, detail="仅支持图片素材保存提示词")
    caption = str(payload.caption or "")[:100000]
    txt_path = _local_upload_caption_path(filename)
    with open(txt_path, "w", encoding="utf-8", newline="") as f:
        f.write(caption)
    return {"ok": True, "caption": caption, "caption_file": os.path.basename(txt_path)}

@app.post("/api/temp-sh/upload")
async def temp_sh_upload(payload: TempShUploadRequest, request: Request):
    ensure_same_origin_request(request)
    return await upload_local_video_to_cloud(payload.url, "auto")

@app.post("/api/cloud-video/upload")
async def cloud_video_upload(payload: CloudVideoUploadRequest, request: Request):
    ensure_same_origin_request(request)
    return await upload_local_video_to_cloud(payload.url, payload.service)

@app.post("/api/ai/import-local-image")
async def import_local_ai_reference(payload: LocalImageImportRequest, request: Request):
    ensure_same_origin_request(request)
    requested = [payload.path] if payload.path else []
    requested.extend(payload.paths or [])
    requested = [p for p in requested if str(p or "").strip()][:20]
    if not requested:
        raise HTTPException(status_code=400, detail="没有可导入的本地图片")
    return {"files": [import_local_image_file(normalize_local_image_path(path)) for path in requested]}

@app.get("/api/runninghub/app-info")
async def runninghub_app_info(webappId: str = ""):
    webapp_id = str(webappId or "").strip()
    if not webapp_id:
        raise HTTPException(status_code=400, detail="webappId 必填")
    provider = runninghub_provider()
    api_key = runninghub_api_key(provider)
    url = runninghub_endpoint_url(provider, f"/api/webapp/apiCallDemo?apiKey={urllib.parse.quote(api_key)}&webappId={urllib.parse.quote(webapp_id)}")
    async with httpx.AsyncClient(timeout=httpx.Timeout(connect=20.0, read=120.0, write=30.0, pool=20.0)) as client:
        try:
            response = await client.get(url, headers=runninghub_app_headers(False))
            raw = response.json()
        except httpx.HTTPStatusError as exc:
            raise HTTPException(status_code=exc.response.status_code, detail=exc.response.text[:500]) from exc
        except Exception as exc:
            raise HTTPException(status_code=502, detail=f"请求 RunningHub 应用信息失败：{exc}") from exc
    if response.status_code >= 400:
        raise HTTPException(status_code=response.status_code, detail=json.dumps(raw, ensure_ascii=False)[:500])
    if isinstance(raw, dict) and raw.get("code") not in (0, "0", None):
        raise HTTPException(status_code=400, detail=raw.get("msg") or f"RunningHub 查询失败 code={raw.get('code')}")
    data = raw.get("data") if isinstance(raw, dict) else {}
    return {"success": True, "data": data or {}}

@app.post("/api/runninghub/submit")
async def runninghub_submit(payload: RunningHubSubmitRequest):
    webapp_id = str(payload.webappId or "").strip()
    if not webapp_id:
        raise HTTPException(status_code=400, detail="webappId 必填")
    provider = runninghub_provider()
    api_key = runninghub_api_key(provider, use_wallet=payload.useWallet)
    body = {
        "apiKey": api_key,
        "webappId": webapp_id,
        "nodeInfoList": sanitize_runninghub_node_info_list(payload.nodeInfoList or []),
    }
    instance_type = str(payload.instanceType or "").strip()
    if instance_type:
        body["instanceType"] = instance_type
    url = runninghub_endpoint_url(provider, "/task/openapi/ai-app/run")
    async with httpx.AsyncClient(timeout=httpx.Timeout(connect=20.0, read=180.0, write=120.0, pool=20.0)) as client:
        try:
            response = await client.post(url, headers=runninghub_app_headers(True, payload.useWallet), json=body)
            raw = response.json()
        except Exception as exc:
            raise HTTPException(status_code=502, detail=f"提交 RunningHub 任务失败：{exc}") from exc
    if response.status_code >= 400:
        raise HTTPException(status_code=response.status_code, detail=json.dumps(raw, ensure_ascii=False)[:800])
    if isinstance(raw, dict) and raw.get("code") in (0, "0"):
        task_id = raw.get("data", {}).get("taskId") if isinstance(raw.get("data"), dict) else ""
        if not task_id:
            raise HTTPException(status_code=502, detail=f"RunningHub 未返回 taskId：{raw}")
        return {"success": True, "data": {"taskId": task_id, "raw": raw}}
    raise HTTPException(status_code=400, detail=(raw.get("msg") if isinstance(raw, dict) else "") or f"RunningHub 提交失败：{raw}")

# RunningHub 仅保留在线 AI 应用提交；工作流提交、解析、保存和删除已移除。


@app.get("/api/runninghub/query")
async def runninghub_query(taskId: str = "", useWallet: bool = False):
    task_id = str(taskId or "").strip()
    if not task_id:
        raise HTTPException(status_code=400, detail="taskId 必填")
    provider = runninghub_provider()
    api_key = runninghub_api_key(provider, use_wallet=useWallet)
    url = runninghub_endpoint_url(provider, "/task/openapi/outputs")
    async with httpx.AsyncClient(timeout=httpx.Timeout(connect=20.0, read=240.0, write=30.0, pool=20.0)) as client:
        try:
            response = await client.post(url, headers=runninghub_app_headers(True, useWallet), json={"apiKey": api_key, "taskId": task_id})
            raw = response.json()
        except Exception as exc:
            raise HTTPException(status_code=502, detail=f"查询 RunningHub 任务失败：{exc}") from exc
        if response.status_code >= 400:
            raise HTTPException(status_code=response.status_code, detail=json.dumps(raw, ensure_ascii=False)[:800])
        code = raw.get("code") if isinstance(raw, dict) else None
        status = "PENDING"
        urls = []
        image_items = []
        if code in (0, "0"):
            status = "SUCCESS"
            for remote in runninghub_extract_outputs(raw.get("data")):
                try:
                    local_url = await runninghub_store_remote_output(client, remote)
                except Exception:
                    local_url = remote
                urls.append(local_url)
                image_items.append(image_output_meta(local_url))
        elif code in (804, "804"):
            status = "RUNNING"
        elif code in (813, "813"):
            status = "QUEUED"
        elif code in (805, "805"):
            status = "FAILED"
        else:
            status = "UNKNOWN"
        return {"success": True, "data": {"status": status, "urls": urls, "image_items": image_items, "failReason": runninghub_fail_reason(raw), "code": code, "raw": raw}}

@app.post("/api/runninghub/upload-asset")
async def runninghub_upload_asset(payload: RunningHubUploadAssetRequest):
    source_url = rewrite_runninghub_file_url(str(payload.url or "").strip())
    if not source_url:
        raise HTTPException(status_code=400, detail="url 必填")
    provider = runninghub_provider()
    api_key = runninghub_api_key(provider, use_wallet=payload.useWallet)
    filename = "asset.bin"
    content_type = "application/octet-stream"
    content = b""
    async with httpx.AsyncClient(timeout=httpx.Timeout(connect=20.0, read=240.0, write=240.0, pool=20.0), follow_redirects=True) as client:
        path = runninghub_local_asset_path(source_url)
        if path:
            filename = os.path.basename(path)
            content_type = content_type_for_path(path)
            with open(path, "rb") as f:
                content = f.read()
        elif source_url.startswith(("http://", "https://")):
            response = await client.get(source_url)
            if not response.is_success:
                raise HTTPException(status_code=400, detail=f"下载素材失败 HTTP {response.status_code}")
            content = response.content
            content_type = response.headers.get("content-type") or content_type
            filename = os.path.basename(urllib.parse.urlsplit(source_url).path) or filename
        else:
            raise HTTPException(status_code=400, detail=f"不支持的素材地址：{source_url}")
        if not content:
            raise HTTPException(status_code=400, detail="素材为空，无法上传到 RunningHub")
        upload_url = runninghub_endpoint_url(provider, "/task/openapi/upload")
        files = {"file": (filename, content, content_type)}
        data = {"apiKey": api_key, "fileType": "input"}
        try:
            response = await client.post(upload_url, headers=runninghub_app_headers(False, payload.useWallet), data=data, files=files)
            raw = response.json()
        except Exception as exc:
            raise HTTPException(status_code=502, detail=f"上传素材到 RunningHub 失败：{exc}") from exc
    if response.status_code >= 400:
        raise HTTPException(status_code=response.status_code, detail=json.dumps(raw, ensure_ascii=False)[:800])
    if isinstance(raw, dict) and raw.get("code") in (0, "0") and isinstance(raw.get("data"), dict) and raw["data"].get("fileName"):
        return {"success": True, "data": {"fileName": raw["data"]["fileName"], "fileType": raw["data"].get("fileType") or content_type}}
    raise HTTPException(status_code=400, detail=(raw.get("msg") if isinstance(raw, dict) else "") or f"RunningHub 上传失败：{raw}")

@app.get("/api/codex/status")
async def codex_status():
    exe = codex_cli_executable()
    image2_exe = gpt_image_2_skill_executable()
    if not exe:
        return {
            "installed": False,
            "logged_in": False,
            "image2_helper_installed": bool(image2_exe),
            "image2_helper_path": image2_exe,
            "message": "未找到 OpenAI Codex CLI，请先安装。",
        }
    try:
        proc = await asyncio.create_subprocess_exec(
            exe,
            "--version",
            cwd=BASE_DIR,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=10)
        out_text, err_text = codex_decode_output(stdout, stderr)
        ok = proc.returncode == 0
        logged_in = False
        login_check_error = ""
        if ok:
            try:
                login_proc = await asyncio.create_subprocess_exec(
                    exe,
                    "login",
                    "status",
                    cwd=BASE_DIR,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                )
                await asyncio.wait_for(login_proc.communicate(), timeout=10)
                logged_in = login_proc.returncode == 0
            except Exception as exc:
                login_check_error = str(exc)
        helper_message = "GPT Image 2 helper 已安装，OpenAI CLI 生图会使用 GPT Image 2。" if image2_exe else "未找到 GPT Image 2 helper，OpenAI CLI 生图不可用；已禁用 Codex 内置 $imagegen 回退。"
        return {
            "installed": ok,
            "logged_in": logged_in,
            "version": out_text or err_text,
            "path": exe,
            "image2_helper_installed": bool(image2_exe),
            "image2_helper_path": image2_exe,
            "message": f"OpenAI Codex CLI 已安装，{'登录状态检查失败' if login_check_error else ('已登录' if logged_in else '未登录')}。{helper_message}" if ok else (err_text or out_text or "Codex CLI 检测失败"),
            "raw": {"stdout": out_text, "stderr": err_text, "returncode": proc.returncode, "login_status_checked": not bool(login_check_error)},
        }
    except Exception as exc:
        return {
            "installed": False,
            "logged_in": False,
            "path": exe,
            "image2_helper_installed": bool(image2_exe),
            "image2_helper_path": image2_exe,
            "message": f"Codex CLI 检测失败：{exc}",
        }

@app.post("/api/codex/help")
async def codex_help(payload: CodexHelpRequest):
    exe = codex_cli_executable()
    if not exe:
        raise HTTPException(status_code=400, detail="未找到 OpenAI Codex CLI。")
    allowed = {"", "exec", "login", "logout", "doctor", "mcp", "app", "update"}
    command = str(payload.command or "").strip()
    if command not in allowed:
        raise HTTPException(status_code=400, detail="不允许的 Codex CLI 命令")
    args = [exe]
    if command:
        args.append(command)
    args.append("--help")
    proc = await asyncio.create_subprocess_exec(
        *args,
        cwd=BASE_DIR,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=20)
    out_text, err_text = codex_decode_output(stdout, stderr)
    if proc.returncode != 0:
        raise HTTPException(status_code=502, detail=(err_text or out_text or f"exit={proc.returncode}")[:1000])
    return {"text": out_text or err_text, "raw": {"stdout": out_text, "stderr": err_text}}

@app.get("/api/gemini-cli/status")
async def gemini_cli_status():
    exe = gemini_cli_executable()
    display_name = gemini_cli_display_name(exe)
    if not exe:
        return {
            "installed": False,
            "logged_in": False,
            "provider": "antigravity",
            "message": "未找到 Antigravity CLI，请先安装。",
        }
    try:
        proc = await asyncio.create_subprocess_exec(
            exe,
            "--version",
            cwd=BASE_DIR,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=10)
        out_text, err_text = codex_decode_output(stdout, stderr)
        ok = proc.returncode == 0
        is_agy = is_antigravity_cli(exe)
        login_check_error = ""
        if is_agy and ok:
            try:
                auth_proc = await asyncio.create_subprocess_exec(
                    exe,
                    "models",
                    cwd=BASE_DIR,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                )
                await asyncio.wait_for(auth_proc.communicate(), timeout=15)
                logged_in = auth_proc.returncode == 0
            except Exception as exc:
                logged_in = False
                login_check_error = str(exc)
        else:
            gemini_home = os.path.join(os.path.expanduser("~"), ".gemini")
            credential_files = (
                os.path.join(gemini_home, "oauth_creds.json"),
                os.path.join(gemini_home, "google_accounts.json"),
            )
            logged_in = ok and bool(
                os.getenv("GEMINI_API_KEY")
                or os.getenv("GOOGLE_API_KEY")
                or any(os.path.isfile(path) for path in credential_files)
            )
        return {
            "installed": ok,
            "logged_in": logged_in,
            "version": out_text or err_text,
            "path": exe,
            "provider": "antigravity" if is_agy else "gemini",
            "message": f"{display_name} 已安装，{'登录状态检查失败' if login_check_error else ('已登录' if logged_in else '未登录')}。" if ok else (err_text or out_text or f"{display_name} 检测失败"),
            "raw": {"stdout": out_text, "stderr": err_text, "returncode": proc.returncode, "login_status_checked": not bool(login_check_error)},
        }
    except Exception as exc:
        return {
            "installed": False,
            "logged_in": False,
            "path": exe,
            "provider": "antigravity" if is_antigravity_cli(exe) else "gemini",
            "message": f"{display_name} 检测失败：{exc}",
        }

@app.post("/api/gemini-cli/help")
async def gemini_cli_help(payload: GeminiCliHelpRequest):
    exe = gemini_cli_executable()
    if not exe:
        raise HTTPException(status_code=400, detail="未找到 Antigravity CLI。")
    is_agy = is_antigravity_cli(exe)
    allowed = {"", "help", "install", "models", "plugin", "plugins", "update", "changelog"} if is_agy else {"", "help", "mcp", "extensions"}
    command = str(payload.command or "").strip()
    if command not in allowed:
        raise HTTPException(status_code=400, detail=f"不允许的 {gemini_cli_display_name(exe)} 命令")
    args = [exe]
    if command:
        args.append(command)
    args.append("--help")
    proc = await asyncio.create_subprocess_exec(
        *args,
        cwd=BASE_DIR,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=20)
    out_text, err_text = codex_decode_output(stdout, stderr)
    if proc.returncode != 0:
        raise HTTPException(status_code=502, detail=(err_text or out_text or f"exit={proc.returncode}")[:1000])
    return {"text": out_text or err_text, "raw": {"stdout": out_text, "stderr": err_text}}

@app.get("/api/jimeng/status")
async def jimeng_status():
    exe = jimeng_cli_executable()
    if not exe:
        return {"installed": False, "logged_in": False, "message": "未找到 dreamina CLI"}
    version, version_text = await jimeng_cli_version()
    version_str = ".".join(str(part) for part in version) if version else None
    version_ok = version >= JIMENG_MIN_CLI_VERSION if version else None
    min_version_str = ".".join(str(part) for part in JIMENG_MIN_CLI_VERSION)
    try:
        raw = await run_jimeng_cli(["user_credit"], timeout=30)
        return {
            "installed": True,
            "logged_in": True,
            "raw": raw,
            "cli_version": version_str,
            "version_ok": version_ok,
            "min_version": min_version_str,
        }
    except HTTPException as exc:
        return {
            "installed": True,
            "logged_in": False,
            "message": str(exc.detail),
            "cli_version": version_str,
            "version_ok": version_ok,
            "min_version": min_version_str,
        }

@app.get("/api/jimeng/credit")
async def jimeng_credit():
    raw = await run_jimeng_cli(["user_credit"], timeout=30)
    return {"success": True, "raw": raw}

@app.post("/api/jimeng/logout")
async def jimeng_logout():
    raw = await run_jimeng_cli(["logout"], timeout=30)
    return {"success": True, "raw": raw}

@app.post("/api/jimeng/login/start")
async def jimeng_login_start():
    old_proc = JIMENG_LOGIN_SESSION.get("proc")
    if old_proc and getattr(old_proc, "returncode", None) is None:
        try:
            old_proc.terminate()
        except Exception:
            pass
    exe = jimeng_cli_executable()
    if not exe:
        raise HTTPException(status_code=400, detail="未找到 dreamina CLI")
    JIMENG_LOGIN_SESSION.update({"proc": None, "stdout": "", "stderr": "", "started_at": time.time()})
    args = ["login", "--headless"]
    command = jimeng_command(args, exe)
    try:
        proc = await asyncio.create_subprocess_exec(
            *command,
            cwd=BASE_DIR,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
    except FileNotFoundError as exc:
        raise HTTPException(status_code=400, detail=f"未找到即梦 CLI：{exe}") from exc
    JIMENG_LOGIN_SESSION["proc"] = proc
    asyncio.create_task(jimeng_login_reader(proc))
    await asyncio.sleep(2)
    text = jimeng_login_text()
    if proc.returncode not in (None, 0) and ("unknown" in text.lower() or "no such option" in text.lower()):
        # 旧版 CLI 可能没有 --headless，退回 debug 输出。
        JIMENG_LOGIN_SESSION.update({"proc": None, "stdout": "", "stderr": "", "started_at": time.time()})
        proc = await asyncio.create_subprocess_exec(
            *jimeng_command(["login", "--debug"], exe),
            cwd=BASE_DIR,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        JIMENG_LOGIN_SESSION["proc"] = proc
        asyncio.create_task(jimeng_login_reader(proc))
        await asyncio.sleep(2)
        text = jimeng_login_text()
    return {
        "success": True,
        "running": JIMENG_LOGIN_SESSION.get("proc") is not None and JIMENG_LOGIN_SESSION["proc"].returncode is None,
        "text": text,
        "qr_url": jimeng_login_qr_from_text(text),
        "started_at": JIMENG_LOGIN_SESSION.get("started_at") or 0,
    }

@app.get("/api/jimeng/login/status")
async def jimeng_login_status():
    proc = JIMENG_LOGIN_SESSION.get("proc")
    text = jimeng_login_text()
    running = proc is not None and getattr(proc, "returncode", None) is None
    logged_in = False
    credit_raw = None
    if not running:
        try:
            credit_raw = await run_jimeng_cli(["user_credit"], timeout=20)
            logged_in = True
        except HTTPException:
            logged_in = False
    return {
        "success": True,
        "running": running,
        "logged_in": logged_in,
        "text": text,
        "qr_url": jimeng_login_qr_from_text(text),
        "raw": credit_raw,
    }

@app.post("/api/jimeng/help")
async def jimeng_help(payload: JimengHelpRequest):
    command = str(payload.command or "").strip()
    allowed = {"", "login", "logout", "user_credit", "text2image", "image2image", "image_upscale", "text2video", "image2video", "multimodal2video", "frames2video", "multiframe2video", "list_task", "query_result"}
    if command not in allowed:
        raise HTTPException(status_code=400, detail="不支持的帮助命令")
    args = [command, "-h"] if command else ["-h"]
    raw = await run_jimeng_cli(args, timeout=30, raw_text=True)
    text = raw.get("_stdout") or ""
    if raw.get("_stderr"):
        text = f"{text}\n{raw.get('_stderr')}".strip()
    return {"success": True, "command": command, "text": text, "raw": raw}

@app.post("/api/jimeng/query-media")
async def jimeng_query_media(payload: JimengQueryMediaRequest):
    """按 submit_id 续查即梦任务：出图返回 succeeded+urls；仍排队返回 pending+queue_info；失败返回 failed。
    供画布「排队中」卡片自动轮询与手动查询复用。"""
    submit_id = str(payload.submit_id or "").strip()
    if not submit_id:
        raise HTTPException(status_code=400, detail="缺少 submit_id")
    kind = str(payload.kind or "image").strip().lower()
    if kind not in ("image", "video", "audio"):
        kind = "image"
    queried = await jimeng_query_result(submit_id, kind)
    try:
        urls = await jimeng_store_outputs(queried, kind, allow_query=False)
        return {"status": "succeeded", "submit_id": submit_id, "kind": kind, "urls": urls}
    except JimengPendingError as exc:
        return {"status": "pending", "submit_id": submit_id, "kind": kind, "queue_info": exc.queue_info, "message": jimeng_pending_payload(exc)["message"]}
    except HTTPException as exc:
        return {"status": "failed", "submit_id": submit_id, "kind": kind, "error": str(getattr(exc, "detail", "") or exc)}


def photoshop_connection_status():
    connected_clients = [
        client_id for client_id in manager.connection_clients.values()
        if str(client_id or "").lower().startswith("ps_")
    ]
    connected = bool(connected_clients)
    return {
        "connected": connected,
        "connection_count": len(connected_clients),
        "message": "Photoshop 连接器已连接" if connected else "Photoshop 连接器未连接",
    }


def switch_project_library(path: str, move_existing: bool):
    global FIREFLY_PROJECT_STORE, PROJECT_LIBRARY_MIGRATION_ACTIVE
    with PROJECT_LIBRARY_SWITCH_LOCK:
        PROJECT_LIBRARY_MIGRATION_ACTIVE = True
        old_store = FIREFLY_PROJECT_STORE
        new_store = None
        migration = None
        completed = False
        try:
            with PROJECT_MEDIA_IMPORT_TASKS_LOCK:
                active_imports = [future for future in PROJECT_MEDIA_IMPORT_FUTURES.values() if not future.done()]
            if active_imports:
                raise ProjectStoreError("仍有项目媒体正在导入，请等待完成后再移动项目库")
            projects = old_store.list_projects()
            if projects and not move_existing:
                raise ProjectStoreError("当前项目库不为空，必须显式选择移动已有项目")
            target = Path(path).expanduser()
            if move_existing:
                migration = old_store.copy_library_to(target)
            new_store = ProjectStore(target, snapshot_delay=old_store.snapshot_delay)
            project_count = len(new_store.list_projects())
            if move_existing and project_count != len(projects):
                raise ProjectStoreError("新项目库项目数量校验失败")
            update_env_values({"FIREFLY_PROJECTS_ROOT": str(new_store.root)})
            FIREFLY_PROJECT_STORE = new_store
            new_store = None
            completed = True
            try:
                old_store.close()
            except Exception as exc:
                print(f"关闭原项目库失败（新项目库已安全切换）: {exc}")
            return {
                "project_library": {
                    "path": str(FIREFLY_PROJECT_STORE.root),
                    "project_count": project_count,
                },
                "migration": migration,
            }
        finally:
            if new_store is not None:
                try:
                    new_store.close()
                except Exception as exc:
                    print(f"关闭候选项目库失败: {exc}")
            if not completed and migration:
                shutil.rmtree(migration["target"], ignore_errors=True)
                if migration.get("target_preexisted"):
                    os.makedirs(migration["target"], exist_ok=True)
            PROJECT_LIBRARY_MIGRATION_ACTIVE = False


def project_store_call(method_name: str, *args, **kwargs):
    with PROJECT_LIBRARY_SWITCH_LOCK:
        store = FIREFLY_PROJECT_STORE
        return getattr(store, method_name)(*args, **kwargs)


def project_library_status():
    with PROJECT_LIBRARY_SWITCH_LOCK:
        store = FIREFLY_PROJECT_STORE
        projects = store.list_projects()
        return {
            "path": str(store.root),
            "project_count": len(projects),
            "migration_active": PROJECT_LIBRARY_MIGRATION_ACTIVE,
        }


@app.get("/api/global-settings")
async def global_settings():
    statuses = await asyncio.gather(
        codex_status(), gemini_cli_status(), jimeng_status(), return_exceptions=True
    )

    def safe_cli_status(label, value):
        if isinstance(value, BaseException):
            return {
                "installed": False,
                "logged_in": False,
                "message": f"{label} 状态读取失败：{value}",
            }
        if isinstance(value, dict):
            return value
        return {"installed": False, "logged_in": False, "message": f"{label} 状态不可用"}

    codex = safe_cli_status("Codex", statuses[0])
    gemini = safe_cli_status("Antigravity", statuses[1])
    jimeng = safe_cli_status("即梦", statuses[2])
    library = await asyncio.to_thread(project_library_status)
    return {
        "providers": public_api_providers(),
        "cli": {"codex": codex, "gemini": gemini, "jimeng": jimeng},
        "project_library": library,
        "preferences": load_global_preferences(),
        "photoshop": photoshop_connection_status(),
    }


@app.patch("/api/global-settings/preferences")
async def update_global_preferences(payload: GlobalPreferencesPayload):
    if payload.theme not in {"light", "dark"} or payload.language not in {"zh", "en"}:
        raise HTTPException(status_code=400, detail="主题或语言无效")
    return {"preferences": await asyncio.to_thread(save_global_preferences, payload.theme, payload.language, payload.autosave_interval_minutes)}


@app.get("/api/global-settings/preferences")
async def get_global_preferences():
    return {"preferences": load_global_preferences()}


@app.post("/api/global-settings/project-library")
async def update_project_library(payload: ProjectLibraryChangeRequest):
    try:
        return await asyncio.to_thread(switch_project_library, payload.path, payload.move_existing)
    except ProjectStoreError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except OSError as exc:
        raise HTTPException(status_code=500, detail=f"项目库切换失败: {exc}") from exc

@app.get("/api/config")
async def ai_config():
    preferred_chat_model = next(
        (m for hint in ("gpt-5.4", "chatgpt-5.4") for m in CHAT_MODELS
         if str(m).lower() == hint or str(m).lower().startswith(hint)),
        CHAT_MODELS[0] if CHAT_MODELS else CHAT_MODEL,
    )
    providers = public_api_providers()
    return {
        "base_url": AI_BASE_URL,
        "chat_model": preferred_chat_model,
        "image_model": IMAGE_MODEL,
        "chat_models": CHAT_MODELS,
        "image_models": IMAGE_MODELS,
        "video_models": VIDEO_MODELS,
        "api_providers": providers,
        "has_api_key": bool(AI_API_KEY),
        "ms_chat_models": MODELSCOPE_CHAT_MODELS,
        "has_ms_key": bool(modelscope_api_key()),
    }

@app.get("/api/models")
async def ai_models():
    return {"chat_models": CHAT_MODELS, "image_models": IMAGE_MODELS, "video_models": VIDEO_MODELS}

@app.get("/api/providers")
async def api_providers():
    return {"providers": public_api_providers()}

@app.put("/api/providers")
async def save_providers(payload: List[ApiProviderPayload]):
    providers = []
    env_updates = {}
    for item in payload:
        if str(getattr(item, "id", "") or "").strip().lower() in REMOVED_PROVIDER_IDS:
            continue
        provider_data = item.model_dump(exclude={"api_key"}) if hasattr(item, "model_dump") else item.dict(exclude={"api_key"})
        provider = normalize_provider(provider_data)
        if provider["id"] == "runninghub":
            provider = preserve_runninghub_hidden_overrides(provider)
        if any(existing["id"] == provider["id"] for existing in providers):
            raise HTTPException(status_code=400, detail=f"API 平台 ID 重复：{provider['id']}")
        providers.append(provider)
        key_env = provider_key_env(provider["id"])
        if item.clear_key:
            env_updates[key_env] = ""
        elif item.api_key is not None and item.api_key.strip():
            env_updates[key_env] = item.api_key.strip()
        if provider["id"] == "runninghub":
            wallet_env = runninghub_wallet_key_env()
            if item.clear_wallet_key:
                env_updates[wallet_env] = ""
            elif item.wallet_api_key is not None and item.wallet_api_key.strip():
                env_updates[wallet_env] = item.wallet_api_key.strip()
        if provider["id"] == "volcengine":
            ak_env = volcengine_access_key_env()
            sk_env = volcengine_secret_key_env()
            if item.clear_volcengine_access_key_id:
                env_updates[ak_env] = ""
            elif item.volcengine_access_key_id is not None and item.volcengine_access_key_id.strip():
                env_updates[ak_env] = item.volcengine_access_key_id.strip()
            if item.clear_volcengine_secret_access_key:
                env_updates[sk_env] = ""
            elif item.volcengine_secret_access_key is not None and item.volcengine_secret_access_key.strip():
                env_updates[sk_env] = item.volcengine_secret_access_key.strip()
        if provider["id"] == "comfly":
            env_updates["COMFLY_BASE_URL"] = provider["base_url"]
            env_updates["IMAGE_MODELS"] = ",".join(provider["image_models"])
            env_updates["CHAT_MODELS"] = ",".join(provider["chat_models"])
            env_updates["VIDEO_MODELS"] = ",".join(provider.get("video_models") or [])
        if provider["id"] == "volcengine":
            provider["protocol"] = "volcengine"
    if not providers:
        raise HTTPException(status_code=400, detail="至少保留一个 API 平台")
    # 强制最多一个 primary（取最后被标记的；都没标记则保持原样不强制）
    primary_indices = [i for i, provider in enumerate(providers) if provider.get("primary")]
    if primary_indices:
        winner = primary_indices[-1]
        for i, p in enumerate(providers):
            p["primary"] = (i == winner)
    save_api_providers(providers)
    if env_updates:
        update_env_values(env_updates)
        reload_env_globals()   # 立即将最新 env 值同步回模块全局变量，无需重启
    return {"providers": [public_provider(p) for p in providers]}

# --- ModelScope Token (从 env 读取，不再支持通过 UI 修改) ---

@app.get("/api/config/token")
async def get_global_token():
    # 优先读 env，回退到 global_config.json（兼容旧数据）
    saved_token = modelscope_api_key()
    if saved_token:
        return {"token": saved_token}
    if os.path.exists(GLOBAL_CONFIG_FILE):
        try:
            with open(GLOBAL_CONFIG_FILE, 'r', encoding='utf-8') as f:
                config = json.load(f)
                return {"token": config.get("modelscope_token", "")}
        except:
            pass
    return {"token": ""}

# --- 在线生图 (COMFLY) ---

class TestConnectionPayload(BaseModel):
    base_url: str = ""
    api_key: str = ""
    provider_id: str = ""
    protocol: str = "openai"
    image_request_mode: str = "openai"

def protocol_from_payload(payload):
    provider_id = str(getattr(payload, "provider_id", "") or "").strip().lower()
    if provider_id == "volcengine":
        return "volcengine"
    if provider_id == "runninghub":
        return "runninghub"
    if provider_id == "jimeng":
        return "jimeng"
    base_url = str(getattr(payload, "base_url", "") or "").strip().lower()
    if "runninghub.cn" in base_url or "runninghub.ai" in base_url:
        return "runninghub"
    protocol = str(getattr(payload, "protocol", "") or "openai").strip().lower()
    return protocol if protocol in SUPPORTED_PROVIDER_PROTOCOLS else "openai"

def api_key_from_payload(payload, protocol: str = ""):
    explicit = str(getattr(payload, "api_key", "") or "").strip()
    provider_id = str(getattr(payload, "provider_id", "") or "").strip().lower()
    protocol = str(protocol or protocol_from_payload(payload) or "").strip().lower()
    if explicit:
        return explicit
    if provider_id:
        if provider_id == "runninghub":
            value = os.getenv(runninghub_wallet_key_env(), "")
            if value:
                return value
        value = provider_env_key_value(provider_id)
        if value:
            return value
    if protocol == "volcengine":
        return volcengine_provider_api_key("")
    return ""

def upstream_models_url(base_url: str, protocol: str):
    if protocol == "gemini":
        return f"{base_url}/models" if base_url.endswith("/v1beta") else f"{base_url}/v1beta/models"
    if protocol == "volcengine":
        return f"{base_url}/models" if base_url.endswith("/api/v3") else f"{base_url}/api/v3/models"
    if protocol == "runninghub":
        return runninghub_openapi_url({"base_url": base_url}, "models")
    return f"{base_url}/models" if base_url.endswith("/v1") else f"{base_url}/v1/models"

def upstream_model_headers(api_key: str, protocol: str):
    if protocol == "gemini":
        return {"x-goog-api-key": api_key, "Accept": "application/json"}
    if protocol == "runninghub":
        return {"Authorization": bearer_auth_value(api_key), "Accept": "application/json"}
    return {"Authorization": bearer_auth_value(api_key), "Accept": "application/json"}

def volcengine_default_model_payload(status=200, message="", raw=None):
    return {
        "ok": True,
        "protocol": "volcengine",
        "status": status,
        "message": message or "方舟任务接口可用，模型列表接口未返回模型。请按实际方舟控制台模型名称手动填写视频模型。",
        "model_count": 0,
        "image_models": [],
        "chat_models": [],
        "video_models": [],
        "recommended_models": {},
        "all": [],
        "raw": raw,
    }

def volcengine_task_probe_url(base_url: str):
    base = str(base_url or "").strip().rstrip("/")
    if not base:
        return ""
    if base.endswith("/api/v3"):
        return f"{base}/contents/generations/tasks/healthcheck_probe_do_not_submit"
    return f"{base}/api/v3/contents/generations/tasks/healthcheck_probe_do_not_submit"

async def probe_volcengine_task_endpoint(client, base_url: str, api_key: str):
    probe_url = volcengine_task_probe_url(base_url)
    if not probe_url:
        return False, {"status": 0, "message": "Base URL 为空"}
    response = await client.get(probe_url, headers=upstream_model_headers(api_key, "volcengine"))
    try:
        raw = response.json() if response.text else {}
    except Exception:
        raw = response.text[:500]
    if response.status_code in (401, 403):
        return False, {"status": response.status_code, "message": "方舟 API Key 无效或无权限", "raw": raw}
    if looks_like_html_response(response.text):
        return False, {"status": response.status_code, "message": "任务接口返回 HTML，Base URL 可能不是 API 地址", "raw": raw}
    if response.status_code < 500:
        return True, {"status": response.status_code, "message": "方舟任务查询端点可达", "raw": raw}
    return False, {"status": response.status_code, "message": f"方舟任务接口服务端错误 {response.status_code}", "raw": raw}

def openai_compat_root_for_probe(base_url: str):
    base = str(base_url or "").strip().rstrip("/")
    if base.endswith("/api/v3"):
        base = base[: -len("/api/v3")]
    if base.endswith("/v1"):
        return base
    return f"{base}/v1" if base else ""

async def probe_openai_compat_bearer_endpoint(client, base_url: str, api_key: str):
    root = openai_compat_root_for_probe(base_url)
    if not root:
        return False, {"status": 0, "message": "Base URL 为空"}
    url = f"{root}/chat/completions"
    response = await client.post(
        url,
        headers={**upstream_model_headers(api_key, "openai"), "Content-Type": "application/json"},
        json={"messages": []},
    )
    try:
        raw = response.json() if response.text else {}
    except Exception:
        raw = response.text[:500]
    if response.status_code in (401, 403):
        return False, {"status": response.status_code, "message": "API Key 无效或无权限", "raw": raw}
    if looks_like_html_response(response.text):
        return False, {"status": response.status_code, "message": "OpenAI 兼容入口返回 HTML，Base URL 可能不是 API 地址", "raw": raw}
    if response.status_code < 500:
        return True, {"status": response.status_code, "message": "OpenAI 兼容 Bearer 鉴权入口可达", "raw": raw}
    return False, {"status": response.status_code, "message": f"OpenAI 兼容入口服务端错误 {response.status_code}", "raw": raw}

async def probe_openai_models_endpoint(client, base_url: str, api_key: str):
    url = upstream_models_url(base_url, "openai")
    response = await client.get(url, headers=upstream_model_headers(api_key, "openai"))
    try:
        raw = response.json() if response.text else {}
    except Exception:
        raw = response.text[:500]
    if response.status_code in (301, 302, 303, 307, 308):
        location = response.headers.get("Location") or response.headers.get("location") or ""
        suffix = f"：{location}" if location else ""
        return False, {"status": response.status_code, "message": f"OpenAI /v1/models 发生跳转{suffix}，请填写 API Base URL，不要填写网页登录地址", "raw": raw}
    if response.status_code in (401, 403):
        return False, {"status": response.status_code, "message": "OpenAI API Key 无效或无权限", "raw": raw}
    if looks_like_html_response(response.text):
        return False, {"status": response.status_code, "message": "OpenAI /v1/models 返回网页 HTML，请检查请求地址是否为 API Base URL", "raw": raw}
    if response.status_code < 300:
        grouped, ids = parse_upstream_models(raw, "openai") if isinstance(raw, dict) else ({"image": [], "chat": [], "video": []}, [])
        grouped, ids = apply_agnes_model_defaults(base_url, grouped, ids)
        grouped = apply_locked_recommended_model_rules(base_url, grouped)
        return True, {
            "status": response.status_code,
            "message": f"OpenAI 兼容模型列表端点可用{f'，找到 {len(ids)} 个模型' if ids else ''}",
            "raw": raw,
            "model_count": len(ids),
            "image_models": grouped["image"],
            "chat_models": grouped["chat"],
            "video_models": grouped["video"],
            "all": ids,
        }
    if 400 <= response.status_code < 500:
        return False, {"status": response.status_code, "message": f"OpenAI /v1/models 不可用 (HTTP {response.status_code})", "raw": raw}
    return False, {"status": response.status_code, "message": f"OpenAI /v1/models 服务端错误 {response.status_code}", "raw": raw}

async def probe_volcengine_auto_detect(client, base_url: str, api_key: str):
    task_ok, task_probe = await probe_volcengine_task_endpoint(client, base_url, api_key)
    if task_ok:
        return True, {
            "status": task_probe.get("status") or 200,
            "message": "检测到方舟/Ark 任务协议",
            "raw": {"task_probe": task_probe.get("raw")},
        }
    compat_ok, compat_probe = await probe_openai_compat_bearer_endpoint(client, base_url, api_key)
    if compat_ok:
        return True, {
            "status": compat_probe.get("status") or 200,
            "message": "检测到方舟/Ark Bearer 鉴权入口（OpenAI 兼容透传）",
            "raw": {"task_probe": task_probe, "openai_compat_probe": compat_probe.get("raw")},
        }
    return False, {
        "status": compat_probe.get("status") or task_probe.get("status") or 0,
        "message": compat_probe.get("message") or task_probe.get("message") or "未检测到方舟/Ark 兼容入口",
        "raw": {"task_probe": task_probe, "openai_compat_probe": compat_probe.get("raw")},
    }

def classify_upstream_model(mid):
    lc = str(mid or "").lower()
    video_keys = ["veo", "sora", "wan2", "wanx", "doubao-seedance", "doubao-1", "kling", "hailuo", "video", "t2v-", "i2v-", "s2v"]
    if any(k in lc for k in video_keys):
        return "video"
    image_keys = ["banana", "image", "dalle", "dall-e", "imagen", "flux", "stable", "sdxl", "midjourney", "nano-banana", "ideogram", "fal-ai", "qwen-image", "seedream", "doubao-seedream", "text-to-image", "image-to-image"]
    if any(k in lc for k in image_keys):
        return "image"
    return "chat"

def parse_upstream_models(raw, protocol="openai"):
    items = raw.get("data") if isinstance(raw, dict) else None
    if not items and isinstance(raw, dict):
        items = raw.get("models") or raw.get("list") or []
    if not isinstance(items, list):
        items = []
    ids = []
    for it in items:
        if isinstance(it, str):
            mid = it
        elif isinstance(it, dict):
            mid = it.get("id") or it.get("name") or it.get("model")
        else:
            mid = ""
        if mid:
            mid = str(mid)
            if protocol == "gemini" and mid.startswith("models/"):
                mid = mid[len("models/"):]
            ids.append(mid)
    ids = sorted(set(ids))
    grouped = {"image": [], "chat": [], "video": []}
    for mid in ids:
        grouped[classify_upstream_model(mid)].append(mid)
    return grouped, ids

def recommend_upstream_models(grouped):
    """Choose one stable default per media kind for automatic routing.

    The upstream list is still returned in full; these values only tell the
    canvas which model to select first after a provider is fetched.
    """
    priorities = {
        "image": ("nano-banana-pro", "nano-banana-2", "nano-banana", "gpt-image", "imagen", "seedream", "qwen-image", "dall-e", "flux", "image"),
        "chat": ("gpt-5.4", "chatgpt-5.4", "gpt-5.", "gpt-", "claude", "gemini", "qwen", "deepseek", "glm", "llama", "chat"),
        "video": ("veo", "sora", "seedance", "wan", "kling", "hailuo", "video"),
    }
    result = {}
    for kind, values in (grouped or {}).items():
        models = [str(value or "").strip() for value in (values or []) if str(value or "").strip()]
        if not models:
            continue
        selected = next((model for hint in priorities.get(kind, ()) for model in models if hint in model.lower()), models[0])
        result[kind] = selected
    return result

def apply_agnes_model_defaults(base_url, grouped, ids):
    if "apihub.agnes-ai.com" not in str(base_url or "").strip().lower():
        return grouped, ids
    grouped = {key: list(value or []) for key, value in (grouped or {}).items()}
    ids = list(ids or [])
    for model in AGNES_DEFAULT_VIDEO_MODELS:
        if model not in ids:
            ids.append(model)
        if model not in grouped.setdefault("video", []):
            grouped["video"].append(model)
    ids = sorted(set(ids))
    grouped["video"] = sorted(set(grouped.get("video") or []))
    return grouped, ids

@app.post("/api/providers/test-connection")
async def test_provider_connection(payload: TestConnectionPayload):
    """测试请求地址是否可用：调上游 /v1/models。验证通过时同时把模型清单按类别返回，避免再调一次拉取接口。"""
    protocol = protocol_from_payload(payload)
    if protocol == "codex":
        status = await codex_status()
        payload_models = codex_models_payload(raw={"status": status})
        payload_models.update({
            "ok": bool(status.get("installed")),
            "status": 200 if status.get("installed") else 0,
            "message": status.get("message") or ("OpenAI Codex CLI 可用" if status.get("installed") else "未找到 OpenAI Codex CLI"),
        })
        return payload_models
    if protocol == "gemini-cli":
        status = await gemini_cli_status()
        payload_models = gemini_cli_models_payload(raw={"status": status})
        payload_models.update({
            "ok": bool(status.get("installed")),
            "status": 200 if status.get("installed") else 0,
            "message": status.get("message") or ("Antigravity CLI 可用" if status.get("installed") else "未找到 Antigravity CLI"),
        })
        return payload_models
    if protocol == "jimeng":
        status = await jimeng_status()
        return {
            "ok": bool(status.get("installed") and status.get("logged_in")),
            "status": 200 if status.get("logged_in") else 0,
            "message": status.get("message") or "即梦 CLI 已登录",
            "model_count": len(JIMENG_DEFAULT_IMAGE_MODELS) + len(JIMENG_DEFAULT_VIDEO_MODELS),
            "image_models": JIMENG_DEFAULT_IMAGE_MODELS,
            "chat_models": [],
            "video_models": JIMENG_DEFAULT_VIDEO_MODELS,
            "all": [*JIMENG_DEFAULT_IMAGE_MODELS, *JIMENG_DEFAULT_VIDEO_MODELS],
            "raw": status.get("raw"),
        }
    if protocol == "runninghub":
        provider = {"id": "runninghub", "name": "RunningHub", "base_url": (payload.base_url or RUNNINGHUB_DEFAULT_BASE_URL).strip().rstrip("/"), "protocol": "runninghub", "api_key": api_key_from_payload(payload, protocol)}
        payload_models = await runninghub_models_payload(provider)
        return {
            "ok": True,
            "status": 200,
            "message": "RunningHub OpenAPI 可用，已拉取官方直连模型注册表。",
            "model_count": payload_models["total"],
            "image_models": payload_models["image_models"],
            "chat_models": payload_models["chat_models"],
            "video_models": payload_models["video_models"],
            "all": payload_models["all"],
            "protocol": "runninghub",
            "raw": payload_models.get("raw"),
        }
    base_url = (payload.base_url or "").strip().rstrip("/")
    if not base_url:
        raise HTTPException(status_code=400, detail="请先填写请求地址")
    if not re.match(r"^https?://", base_url):
        raise HTTPException(status_code=400, detail="请求地址必须以 http:// 或 https:// 开头")
    api_key = api_key_from_payload(payload, protocol)
    if not api_key:
        key_name = "方舟 API Key" if protocol == "volcengine" else "API Key"
        raise HTTPException(status_code=400, detail=f"请先填写或保存 {key_name}")
    url = upstream_models_url(base_url, protocol)
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.get(url, headers=upstream_model_headers(api_key, protocol))
            if resp.status_code in (301, 302, 303, 307, 308):
                location = resp.headers.get("Location") or resp.headers.get("location") or ""
                suffix = f"：{location}" if location else ""
                endpoint_label = "/v1beta/models" if protocol == "gemini" else "/api/v3/models" if protocol == "volcengine" else "/openapi/v2/models" if protocol == "runninghub" else "/v1/models"
                return {"ok": False, "status": resp.status_code, "message": f"上游 {endpoint_label} 发生跳转{suffix}，请填写 API Base URL，不要填写网页登录地址"}
            if looks_like_html_response(resp.text):
                endpoint_label = "/v1beta/models" if protocol == "gemini" else "/api/v3/models" if protocol == "volcengine" else "/openapi/v2/models" if protocol == "runninghub" else "/v1/models"
                return {"ok": False, "status": resp.status_code, "message": f"上游 {endpoint_label} 返回网页 HTML，请检查请求地址是否为 API Base URL"}
            if resp.status_code >= 400:
                if protocol == "volcengine":
                    detected, probe = await probe_volcengine_auto_detect(client, base_url, api_key)
                    if detected:
                        message = f"{probe.get('message') or '方舟任务接口可达'}；但 /api/v3/models 不可用。请按实际方舟控制台模型名称手动填写视频模型。"
                        return volcengine_default_model_payload(status=probe.get("status") or resp.status_code, message=message, raw={"models_error": resp.text[:300], **(probe.get("raw") or {})})
                elif protocol == "openai":
                    detected, probe = await probe_volcengine_auto_detect(client, base_url, api_key)
                    if detected:
                        message = f"{probe.get('message') or '检测到方舟/Ark 兼容入口'}；OpenAI /v1/models 不可用，已自动切换为方舟协议。请按实际方舟控制台模型名称手动填写视频模型。"
                        return volcengine_default_model_payload(status=probe.get("status") or resp.status_code, message=message, raw={"models_error": resp.text[:300], **(probe.get("raw") or {})})
                return {"ok": False, "status": resp.status_code, "message": resp.text[:300]}
            data = resp.json() if resp.text else {}
            grouped, ids = parse_upstream_models(data, protocol)
            grouped, ids = apply_agnes_model_defaults(base_url, grouped, ids)
            grouped = apply_locked_recommended_model_rules(base_url, grouped)
            if protocol == "volcengine" and not ids:
                detected, probe = await probe_volcengine_auto_detect(client, base_url, api_key)
                if detected:
                    return volcengine_default_model_payload(status=resp.status_code, raw=data)
            return {
                "ok": True,
                "status": resp.status_code,
                "model_count": len(ids),
                "image_models": grouped["image"],
                "chat_models": grouped["chat"],
                "video_models": grouped["video"],
                "recommended_models": recommend_upstream_models(grouped),
                "all": ids,
                "image_request_mode": detect_image_request_mode(base_url, ids) or normalize_image_request_mode(getattr(payload, "image_request_mode", "")),
            }
    except httpx.HTTPError as e:
        if protocol == "volcengine":
            try:
                async with httpx.AsyncClient(timeout=15) as client:
                    detected, probe = await probe_volcengine_auto_detect(client, base_url, api_key)
                    if detected:
                        message = f"{probe.get('message') or '方舟任务接口可达'}；但模型列表请求失败。请按实际方舟控制台模型名称手动填写视频模型。"
                        return volcengine_default_model_payload(status=probe.get("status") or 0, message=message, raw={"models_error": str(e)[:300], **(probe.get("raw") or {})})
            except Exception:
                pass
        return {"ok": False, "status": 0, "message": str(e)[:300]}

@app.post("/api/providers/probe-async")
async def probe_async_endpoint(payload: TestConnectionPayload):
    """验证异步协议：用假 task_id 请求 GET /v1/tasks/{fake_id}。
    收到 400 Invalid task ID = 端点存在且 Key 有效；401/403 = Key 无效；404/连接失败 = 不支持异步端点。"""
    base_url = (payload.base_url or "").strip().rstrip("/")
    protocol = protocol_from_payload(payload)
    if protocol == "codex":
        status = await codex_status()
        return {
            "ok": bool(status.get("installed")),
            "protocol": "codex",
            "status_code": 200 if status.get("installed") else 0,
            "message": status.get("message") or "OpenAI Codex CLI 本机检测完成",
            "raw": status,
        }
    if protocol == "gemini-cli":
        status = await gemini_cli_status()
        return {
            "ok": bool(status.get("installed")),
            "protocol": "gemini-cli",
            "status_code": 200 if status.get("installed") else 0,
            "message": status.get("message") or "Antigravity CLI 本机检测完成",
            "raw": status,
        }
    if not base_url:
        raise HTTPException(status_code=400, detail="请先填写请求地址")
    api_key = api_key_from_payload(payload, protocol)
    if not api_key:
        raise HTTPException(status_code=400, detail="请先填写或保存 API Key")
    if protocol == "volcengine":
        try:
            async with httpx.AsyncClient(timeout=15) as client:
                task_ok, task_probe = await probe_volcengine_task_endpoint(client, base_url, api_key)
                if task_ok:
                    return {
                        "ok": True,
                        "protocol": "volcengine",
                        "status_code": task_probe.get("status") or 200,
                        "message": "方舟/Ark 任务协议可用",
                        "raw": task_probe.get("raw"),
                    }
                compat_ok, compat_probe = await probe_openai_compat_bearer_endpoint(client, base_url, api_key)
                if compat_ok:
                    return {
                        "ok": True,
                        "protocol": "volcengine",
                        "status_code": compat_probe.get("status") or 200,
                        "message": "方舟/Ark Bearer 鉴权入口可用（OpenAI 兼容透传）",
                        "raw": {"task_probe": task_probe, "openai_compat_probe": compat_probe.get("raw")},
                    }
                return {
                    "ok": False,
                    "protocol": "volcengine",
                    "status_code": compat_probe.get("status") or task_probe.get("status") or 0,
                    "message": compat_probe.get("message") or task_probe.get("message") or "方舟/Ark 任务协议不可用",
                    "raw": {"task_probe": task_probe, "openai_compat_probe": compat_probe.get("raw")},
                }
        except httpx.HTTPError as e:
            raise HTTPException(status_code=502, detail=str(e)[:300])
    tasks_base = base_url if base_url.endswith("/v1") else f"{base_url}/v1"
    probe_url = f"{tasks_base}/tasks/healthcheck_probe_do_not_submit"
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.get(probe_url, headers={"Authorization": bearer_auth_value(api_key), "Accept": "application/json"})
            try:
                body = resp.json()
            except Exception:
                body = resp.text[:500]
            sc = resp.status_code
            # 判断结果
            err_msg = ""
            if isinstance(body, dict):
                err = body.get("error") or {}
                if isinstance(err, dict):
                    err_msg = str(err.get("message") or "").lower()
                else:
                    err_msg = str(err).lower()
            # 400 + "invalid task id" → 端点存在，Key 有效
            if sc == 400 and "invalid task id" in err_msg:
                return {"ok": True, "protocol": "apimart", "status_code": sc, "message": "APIMart 异步任务端点可用，API Key 已通过认证", "raw": body}

            async_probe = {"status": sc, "message": "", "raw": body}
            if sc in (301, 302, 303, 307, 308):
                location = resp.headers.get("Location") or resp.headers.get("location") or ""
                async_probe["message"] = f"/v1/tasks/ 发生跳转{f'：{location}' if location else ''}"
            elif looks_like_html_response(resp.text):
                async_probe["message"] = "/v1/tasks/ 返回网页 HTML"
            elif sc in (401, 403):
                async_probe["message"] = "/v1/tasks/ 返回鉴权失败"
            elif sc == 404:
                async_probe["message"] = "平台不支持 /v1/tasks/ 端点，可能不是 APIMart 异步协议"
            elif 400 <= sc < 500:
                async_probe["message"] = f"/v1/tasks/ 返回 {sc}"
            elif sc < 300:
                async_probe["message"] = f"/v1/tasks/ 返回 {sc}（意外成功）"
            else:
                async_probe["message"] = f"/v1/tasks/ 服务端错误 {sc}"

            if protocol == "apimart":
                return {"ok": False, "protocol": "apimart", "status_code": sc, "message": async_probe["message"], "raw": body}

            openai_ok, openai_probe = await probe_openai_models_endpoint(client, base_url, api_key)
            if not openai_ok and protocol == "openai":
                # /v1/models 不可用，先确认是不是“没实现 models 接口的 OpenAI 兼容站”：探一下 /v1/chat/completions。
                # 可达就判定为 OpenAI 兼容（很多网关不暴露 /v1/models），避免被下面的方舟探测（404 也算可达）误判成方舟。
                compat_ok, compat_probe = await probe_openai_compat_bearer_endpoint(client, base_url, api_key)
                # 仅当 /v1/chat/completions 确实存在（返回 2xx 或我们发空 messages 触发的 400 等，而非 404 路径不存在）
                # 才判为 OpenAI 兼容；404 说明该路径不存在，留给后面的方舟探测。
                if compat_ok and (compat_probe.get("status") or 0) != 404:
                    return {
                        "ok": True,
                        "protocol": "openai",
                        "status_code": compat_probe.get("status") or openai_probe.get("status") or sc,
                        "message": "OpenAI 兼容入口可达（该站未提供 /v1/models，模型请手动填写）",
                        "raw": {"async_probe": async_probe, "openai_probe": openai_probe.get("raw"), "openai_compat_probe": compat_probe.get("raw")},
                        "model_count": 0,
                        "image_models": [],
                        "chat_models": [],
                        "video_models": [],
                        "all": [],
                    }
                detected, volc_probe = await probe_volcengine_auto_detect(client, base_url, api_key)
                if detected:
                    return {
                        "ok": True,
                        "protocol": "volcengine",
                        "status_code": volc_probe.get("status") or openai_probe.get("status") or sc,
                        "message": f"{volc_probe.get('message') or '检测到方舟/Ark 兼容入口'}，已自动切换为方舟/Ark 任务协议",
                        "raw": {"async_probe": async_probe, "openai_probe": openai_probe.get("raw"), **(volc_probe.get("raw") or {})},
                    }
            return {
                "ok": openai_ok,
                "protocol": "openai",
                "status_code": openai_probe.get("status") or sc,
                "message": openai_probe.get("message") or "OpenAI 兼容验证完成",
                "raw": {"async_probe": async_probe, "openai_probe": openai_probe.get("raw")},
                "model_count": openai_probe.get("model_count") or 0,
                "image_models": openai_probe.get("image_models") or [],
                "chat_models": openai_probe.get("chat_models") or [],
                "video_models": openai_probe.get("video_models") or [],
                "all": openai_probe.get("all") or [],
                "image_request_mode": detect_image_request_mode(base_url, openai_probe.get("all") or []) or normalize_image_request_mode(getattr(payload, "image_request_mode", "")),
            }
    except httpx.HTTPError as e:
        raise HTTPException(status_code=502, detail=str(e)[:300])

async def fetch_models_from_upstream(base_url: str, api_key: str, protocol: str = "openai", image_request_mode: str = "openai"):
    """从上游模型列表端点拉取模型，并按名称做轻量分类。"""
    protocol = protocol if protocol in SUPPORTED_PROVIDER_PROTOCOLS else "openai"
    if protocol == "codex":
        status = await codex_status()
        payload = codex_models_payload(raw={"status": status})
        payload["message"] = status.get("message") or payload["message"]
        return payload
    if protocol == "gemini-cli":
        status = await gemini_cli_status()
        payload = gemini_cli_models_payload(raw={"status": status})
        payload["message"] = status.get("message") or payload["message"]
        return payload
    if protocol == "jimeng":
        return {
            "total": len(JIMENG_DEFAULT_IMAGE_MODELS) + len(JIMENG_DEFAULT_VIDEO_MODELS),
            "image_models": JIMENG_DEFAULT_IMAGE_MODELS,
            "chat_models": [],
            "video_models": JIMENG_DEFAULT_VIDEO_MODELS,
            "all": [*JIMENG_DEFAULT_IMAGE_MODELS, *JIMENG_DEFAULT_VIDEO_MODELS],
        }
    if protocol == "runninghub":
        provider = {"id": "runninghub", "name": "RunningHub", "base_url": base_url or RUNNINGHUB_DEFAULT_BASE_URL, "protocol": "runninghub", "api_key": api_key}
        return await runninghub_models_payload(provider)
    base_url = (base_url or "").strip().rstrip("/")
    if not base_url:
        raise HTTPException(status_code=400, detail="请先填写请求地址")
    if not re.match(r"^https?://", base_url):
        raise HTTPException(status_code=400, detail="请求地址必须以 http:// 或 https:// 开头")
    api_key = volcengine_provider_api_key(api_key) if protocol == "volcengine" else (api_key or "").strip()
    if not api_key:
        key_name = "方舟 API Key" if protocol == "volcengine" else "API Key"
        raise HTTPException(status_code=400, detail=f"请先填写或保存 {key_name}")
    url = upstream_models_url(base_url, protocol)
    try:
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.get(url, headers=upstream_model_headers(api_key, protocol))
            endpoint_label = "/v1beta/models" if protocol == "gemini" else "/api/v3/models" if protocol == "volcengine" else "/openapi/v2/models" if protocol == "runninghub" else "/v1/models"
            if resp.status_code in (301, 302, 303, 307, 308):
                location = resp.headers.get("Location") or resp.headers.get("location") or ""
                suffix = f"：{location}" if location else ""
                raise HTTPException(status_code=400, detail=f"上游 {endpoint_label} 发生跳转{suffix}，请填写 API Base URL，不要填写网页登录地址")
            if looks_like_html_response(resp.text):
                raise HTTPException(status_code=400, detail=f"上游 {endpoint_label} 返回网页 HTML，请检查请求地址是否为 API Base URL")
            if resp.status_code >= 400:
                if protocol == "volcengine":
                    detected, probe = await probe_volcengine_auto_detect(client, base_url, api_key)
                    if detected:
                        payload = volcengine_default_model_payload(
                            status=probe.get("status") or resp.status_code,
                            message=f"{probe.get('message') or '方舟任务接口可达'}；但 /api/v3/models 不可用。请按实际方舟控制台模型名称手动填写视频模型。",
                            raw={"models_error": resp.text[:300], **(probe.get("raw") or {})},
                        )
                        return {
                            "total": payload["model_count"],
                            "protocol": payload["protocol"],
                            "image_models": payload["image_models"],
                            "chat_models": payload["chat_models"],
                            "video_models": payload["video_models"],
                            "all": payload["all"],
                            "message": payload["message"],
                            "raw": payload["raw"],
                        }
                elif protocol == "openai":
                    detected, probe = await probe_volcengine_auto_detect(client, base_url, api_key)
                    if detected:
                        payload = volcengine_default_model_payload(
                            status=probe.get("status") or resp.status_code,
                            message=f"{probe.get('message') or '检测到方舟/Ark 兼容入口'}；OpenAI /v1/models 不可用，已自动切换为方舟协议。请按实际方舟控制台模型名称手动填写视频模型。",
                            raw={"models_error": resp.text[:300], **(probe.get("raw") or {})},
                        )
                        return {
                            "total": payload["model_count"],
                            "protocol": payload["protocol"],
                            "image_models": payload["image_models"],
                            "chat_models": payload["chat_models"],
                            "video_models": payload["video_models"],
                            "all": payload["all"],
                            "message": payload["message"],
                            "raw": payload["raw"],
                        }
                raise HTTPException(status_code=resp.status_code, detail=f"上游 {endpoint_label} 失败：{resp.text[:300]}")
            raw = resp.json()
    except httpx.HTTPError as e:
        if protocol == "volcengine":
            try:
                async with httpx.AsyncClient(timeout=15) as client:
                    detected, probe = await probe_volcengine_auto_detect(client, base_url, api_key)
                    if detected:
                        payload = volcengine_default_model_payload(
                            status=probe.get("status") or 0,
                            message=f"{probe.get('message') or '方舟任务接口可达'}；但模型列表请求失败。请按实际方舟控制台模型名称手动填写视频模型。",
                            raw={"models_error": str(e)[:300], **(probe.get("raw") or {})},
                        )
                        return {
                            "total": payload["model_count"],
                            "protocol": payload["protocol"],
                            "image_models": payload["image_models"],
                            "chat_models": payload["chat_models"],
                            "video_models": payload["video_models"],
                            "all": payload["all"],
                            "message": payload["message"],
                            "raw": payload["raw"],
                        }
            except Exception:
                pass
        raise HTTPException(status_code=502, detail=f"请求上游模型列表失败：{e}")
    grouped, ids = parse_upstream_models(raw, protocol)
    grouped, ids = apply_agnes_model_defaults(base_url, grouped, ids)
    grouped = apply_locked_recommended_model_rules(base_url, grouped)
    if protocol == "volcengine" and not ids:
        payload = volcengine_default_model_payload(raw=raw)
        return {
            "total": payload["model_count"],
            "image_models": payload["image_models"],
            "chat_models": payload["chat_models"],
            "video_models": payload["video_models"],
            "all": payload["all"],
            "message": payload["message"],
            "raw": payload["raw"],
        }
    return {
        "total": len(ids),
        "image_models": grouped["image"],
        "chat_models": grouped["chat"],
        "video_models": grouped["video"],
        "recommended_models": recommend_upstream_models(grouped),
        "all": ids,
        "image_request_mode": detect_image_request_mode(base_url, ids) or normalize_image_request_mode(image_request_mode),
    }

@app.post("/api/providers/fetch-models")
async def fetch_upstream_models_from_payload(payload: TestConnectionPayload):
    """按页面当前表单值拉取模型，支持新增平台未保存时直接使用临时 Base URL / Key。"""
    protocol = protocol_from_payload(payload)
    api_key = api_key_from_payload(payload, protocol)
    return await fetch_models_from_upstream(payload.base_url, api_key, protocol, payload.image_request_mode)

@app.get("/api/providers/{provider_id}/fetch-models")
async def fetch_upstream_models(provider_id: str):
    """从已保存的上游 OpenAI 兼容接口拉取 /v1/models 列表，按名称智能分类为 image/chat/video。"""
    provider = get_api_provider_exact(provider_id)
    if is_codex_provider(provider):
        return await fetch_models_from_upstream("", "", "codex", provider.get("image_request_mode") or "openai")
    if is_gemini_cli_provider(provider):
        return await fetch_models_from_upstream("", "", "gemini-cli", provider.get("image_request_mode") or "openai")
    api_key = os.getenv(runninghub_wallet_key_env(), "") if provider["id"] == "runninghub" else ""
    if not api_key:
        api_key = provider_env_key_value(provider["id"])
    if not api_key:
        raise HTTPException(status_code=400, detail=f"{provider.get('name') or provider_id} 未配置 API Key")
    return await fetch_models_from_upstream(provider.get("base_url") or "", api_key, provider_protocol(provider), provider.get("image_request_mode") or "openai")

def require_online_image_project_media_scope(payload: OnlineImageRequest) -> None:
    require_project_media_values_scope([
        str(getattr(ref, "url", "") or "").strip()
        for ref in (payload.reference_images or [])
        if str(getattr(ref, "url", "") or "").strip()
    ])

async def build_online_image_result(payload: OnlineImageRequest):
    require_online_image_project_media_scope(payload)
    provider = get_api_provider(payload.provider_id)
    default_model = (provider.get("image_models") or [IMAGE_MODEL])[0]
    model = selected_model(payload.model, default_model)
    request_size = snap_size_to_multiple(payload.size, 16)
    refs = [ref.dict() for ref in payload.reference_images if ref.url]
    image_refs = image_references(refs)
    count = max(1, min(8, int(payload.n or 1)))
    async def generate_one():
        image_data, raw_item = await generate_ai_image(payload.prompt, request_size, payload.quality, model, image_refs, provider["id"])
        try:
            image_items = extract_images(raw_item) if isinstance(raw_item, dict) else [image_data]
        except HTTPException:
            image_items = [image_data]
        local_urls = []
        local_items = []
        for item in image_items:
            local_url = await save_ai_image_to_output(item, prefix="online_")
            if local_url:
                local_urls.append(local_url)
                local_items.append(image_output_meta(local_url, item))
        return local_urls, local_items, raw_item
    try:
        generated = await asyncio.gather(*(generate_one() for _ in range(count)))
    except httpx.HTTPStatusError as exc:
        log_net_error(f"生图 HTTP状态错误 provider={provider.get('id')} model={model} size={request_size}", exc)
        text = exc.response.text or ''
        friendly = friendly_image_error_detail(text, request_size, model)
        detail = friendly or f"上游生图接口错误：{text[:300]}"
        raise HTTPException(status_code=exc.response.status_code, detail=detail) from exc
    except httpx.HTTPError as exc:
        log_net_error(f"生图 网络/TLS错误 provider={provider.get('id')} model={model}", exc)
        raise HTTPException(status_code=502, detail=f"请求上游生图接口失败：{exc}") from exc

    local_urls = [url for urls, _items, _raw in generated for url in (urls or []) if url]
    local_items = [item for _urls, items, _raw in generated for item in (items or []) if item.get("url")]
    raw = generated[0][2] if generated else {}
    if not local_urls:
        provider_name = provider.get("name") or provider["id"]
        raw_text = json.dumps(raw, ensure_ascii=False)[:800] if isinstance(raw, (dict, list)) else str(raw)[:800]
        raise HTTPException(status_code=502, detail=f"{provider_name} 没有返回图片：{raw_text}")
    result = {
        "prompt": payload.prompt,
        "images": local_urls,
        "image_items": local_items,
        "timestamp": time.time(),
        "type": "online",
        "model": model,
        "provider_id": provider["id"],
        "provider_name": provider.get("name") or provider["id"],
        "task_id": extract_task_id(raw) if isinstance(raw, dict) else None,
        "request_id": raw.get("id") if isinstance(raw, dict) else None,
        "params": {"provider_id": provider["id"], "model": model, "size": request_size, "requested_size": payload.size, "quality": payload.quality, "n": count, "reference_images": refs},
        "raw_usage": raw.get("usage") if isinstance(raw, dict) else None,
    }
    save_to_history(result)
    if GLOBAL_LOOP:
        asyncio.run_coroutine_threadsafe(manager.broadcast_new_image(result), GLOBAL_LOOP)
    return result

def project_image_request(payload: ProjectImageGenerationRequest) -> ImageGenerationRequest:
    if payload.media_type != "image":
        raise HTTPException(status_code=400, detail={
            "code": "unsupported_media_type",
            "message": "图片生成任务的媒体类型必须是 image",
            "incompatible_parameters": ["media_type"],
        })
    if payload.routing_mode not in {"auto", "manual"}:
        raise HTTPException(status_code=400, detail="路由模式必须是 auto 或 manual")
    if payload.routing_mode == "manual" and not (payload.provider_id or payload.model):
        raise HTTPException(status_code=400, detail="手动路由至少需要固定服务商或模型")
    if payload.n < 1 or payload.n > 8:
        raise HTTPException(status_code=400, detail="单次生图数量必须在 1 到 8 之间")
    if len(payload.reference_asset_ids) > ONLINE_IMAGE_REFERENCE_MAX:
        raise HTTPException(status_code=400, detail={
            "code": "no_compatible_image_model",
            "message": "参考图片数量超过应用安全上限",
            "incompatible_parameters": ["reference_count"],
        })
    width, height = payload.width, payload.height
    parsed_width, parsed_height = parse_size_pair(payload.size)
    if width is None:
        width = parsed_width or None
    if height is None:
        height = parsed_height or None
    if (parsed_width and width != parsed_width) or (parsed_height and height != parsed_height):
        raise HTTPException(status_code=400, detail={
            "code": "inconsistent_image_dimensions",
            "message": "size 与 width/height 不一致",
            "incompatible_parameters": ["size", "width", "height"],
        })
    return ImageGenerationRequest(
        prompt=payload.prompt,
        size=payload.size,
        width=width,
        height=height,
        aspect_ratio=payload.aspect_ratio,
        resolution=payload.resolution,
        reference_asset_ids=tuple(payload.reference_asset_ids),
        media_type=payload.media_type,
        quality=payload.quality,
        count=payload.n,
    )

def project_video_request(payload: ProjectVideoGenerationRequest) -> VideoGenerationRequest:
    if payload.media_type != "video":
        raise HTTPException(status_code=400, detail={
            "code": "unsupported_media_type",
            "message": "视频生成任务的媒体类型必须是 video",
            "incompatible_parameters": ["media_type"],
        })
    if payload.routing_mode not in {"auto", "manual"}:
        raise HTTPException(status_code=400, detail="路由模式必须是 auto 或 manual")
    if payload.routing_mode == "manual" and not (payload.provider_id or payload.model):
        raise HTTPException(status_code=400, detail="手动路由至少需要固定服务商或模型")
    if payload.n < 1 or payload.n > 4:
        raise HTTPException(status_code=400, detail="单次视频数量必须在 1 到 4 之间")
    reference_groups = (
        ("image_reference_count", payload.image_reference_asset_ids),
        ("video_reference_count", payload.video_reference_asset_ids),
        ("audio_reference_count", payload.audio_reference_asset_ids),
    )
    for parameter, asset_ids in reference_groups:
        if len(asset_ids) > ONLINE_VIDEO_REFERENCE_MAX:
            raise HTTPException(status_code=400, detail={
                "code": "no_compatible_video_model",
                "message": "参考素材数量超过应用安全上限",
                "incompatible_parameters": [parameter],
            })
    width, height = payload.width, payload.height
    parsed_width, parsed_height = parse_size_pair(payload.size)
    if width is None:
        width = parsed_width or None
    if height is None:
        height = parsed_height or None
    if (parsed_width and width != parsed_width) or (parsed_height and height != parsed_height):
        raise HTTPException(status_code=400, detail={
            "code": "inconsistent_video_dimensions",
            "message": "size 与 width/height 不一致",
            "incompatible_parameters": ["size", "width", "height"],
        })
    return VideoGenerationRequest(
        prompt=payload.prompt,
        size=payload.size,
        width=width,
        height=height,
        aspect_ratio=payload.aspect_ratio,
        resolution=payload.resolution,
        duration=payload.duration,
        image_reference_asset_ids=tuple(payload.image_reference_asset_ids),
        video_reference_asset_ids=tuple(payload.video_reference_asset_ids),
        audio_reference_asset_ids=tuple(payload.audio_reference_asset_ids),
        media_type=payload.media_type,
        quality=payload.quality,
        count=payload.n,
        seed=payload.seed,
        enhance_prompt=payload.enhance_prompt,
        enable_upsample=payload.enable_upsample,
        watermark=payload.watermark,
        camera_fixed=payload.camera_fixed,
        return_last_frame=payload.return_last_frame,
        generate_audio=payload.generate_audio,
    )

def project_image_references(project_id: str, asset_ids: List[str]) -> List[Dict[str, Any]]:
    references = []
    for index, asset_id in enumerate(asset_ids):
        record = project_store_call("media_record", project_id, asset_id)
        if record.get("kind") != "image":
            raise ProjectStoreError(f"参考资源不是图片: {asset_id}")
        references.append({
            "url": f"/api/project-library/{urllib.parse.quote(project_id, safe='')}/media/{urllib.parse.quote(asset_id, safe='')}",
            "name": record.get("name") or f"reference-{index + 1}.png",
            "kind": "image",
            "mime": record.get("mime_type") or "image/png",
            "asset_id": asset_id,
        })
    return references

def project_video_references(project_id: str, asset_ids: List[str], kind: str) -> List[Dict[str, Any]]:
    references = []
    for index, asset_id in enumerate(asset_ids):
        try:
            record = project_store_call("media_record", project_id, asset_id)
        except ProjectNotFound as exc:
            raise ProjectStoreError(f"参考资源不属于当前项目: {asset_id}") from exc
        if record.get("kind") != kind:
            raise ProjectStoreError(f"参考资源不是{ {'image': '图片', 'video': '视频', 'audio': '音频'}[kind] }: {asset_id}")
        source_path = Path(str(record.get("path") or "")).resolve()
        if not source_path.is_file():
            raise ProjectNotFound(f"项目媒体不存在: {asset_id}")
        references.append({
            "asset_id": asset_id,
            "kind": kind,
            "name": record.get("name") or f"reference-{index + 1}",
            "mime": record.get("mime_type") or "application/octet-stream",
            "path": str(source_path),
            "adapter_url": project_media_adapter_reference(project_id, asset_id),
        })
    return references

def project_video_all_references(project_id: str, payload: ProjectVideoGenerationRequest) -> List[Dict[str, Any]]:
    return [
        *project_video_references(project_id, payload.image_reference_asset_ids, "image"),
        *project_video_references(project_id, payload.video_reference_asset_ids, "video"),
        *project_video_references(project_id, payload.audio_reference_asset_ids, "audio"),
    ]


# --- Project Agent node ---------------------------------------------------

def project_agent_execution_lock(project_id: str, node_id: str) -> Lock:
    key = (str(project_id), str(node_id))
    with PROJECT_AGENT_EXECUTION_LOCKS_GUARD:
        return PROJECT_AGENT_EXECUTION_LOCKS.setdefault(key, Lock())


def _agent_nodes_by_id(canvas: Mapping[str, Any]) -> Dict[str, Dict[str, Any]]:
    return {
        str(node.get("id")): node
        for node in (canvas.get("nodes") or [])
        if isinstance(node, dict) and str(node.get("id") or "")
    }


def project_agent_node(canvas: Mapping[str, Any], node_id: str) -> Dict[str, Any]:
    node = _agent_nodes_by_id(canvas).get(str(node_id or ""))
    if not isinstance(node, dict) or str(node.get("type") or "") != "smart-agent":
        raise HTTPException(status_code=400, detail="目标节点不是 Agent 节点")
    return node


def project_agent_connected_inputs(
    project_id: str,
    canvas: Mapping[str, Any],
    node_id: str,
) -> Dict[str, Any]:
    """Read only prompt/media nodes on direct incoming input edges.

    In particular, this intentionally does not walk a graph, inspect the
    project's other nodes, or trust asset URLs supplied by the client.  Every
    media identifier is resolved through the owning ProjectStore before it is
    admitted to the plan.
    """
    nodes = _agent_nodes_by_id(canvas)
    source_ids: List[str] = []
    for edge in canvas.get("connections") or []:
        if not isinstance(edge, dict):
            continue
        target = edge.get("to") or edge.get("target") or edge.get("toNode") or edge.get("targetNodeId")
        source = edge.get("from") or edge.get("source") or edge.get("fromNode") or edge.get("sourceNodeId")
        kind = str(edge.get("kind") or "flow")
        if str(target or "") == str(node_id) and source and kind in {"input", "flow"}:
            source_ids.append(str(source))
    prompts: List[str] = []
    prompt_node_ids: List[str] = []
    media: List[Dict[str, Any]] = []
    seen_assets: set[str] = set()
    for source_id in source_ids:
        source = nodes.get(source_id)
        if not isinstance(source, dict):
            continue
        source_type = str(source.get("type") or "")
        if source_type == "smart-prompt":
            text = str(source.get("text") or source.get("prompt") or "").strip()
            if text:
                prompts.append(text[:ONLINE_IMAGE_PROMPT_MAX_LENGTH])
                prompt_node_ids.append(source_id)
            continue
        if source_type not in {"", "image", "smart-image"}:
            # Conversation, group, Agent and every other node are deliberately
            # not transitive input capabilities.
            continue
        for item in source.get("images") or []:
            if not isinstance(item, dict):
                continue
            asset_id = str(item.get("asset_id") or "").strip()
            if not asset_id or asset_id in seen_assets:
                continue
            try:
                record = project_store_call("media_record", project_id, asset_id)
            except ProjectNotFound as exc:
                raise HTTPException(status_code=400, detail={
                    "code": "agent_input_media_missing",
                    "message": f"Agent 的输入媒体不属于当前项目: {asset_id}",
                }) from exc
            except ProjectStoreError as exc:
                raise HTTPException(status_code=400, detail=str(exc)) from exc
            expected_kind = str(item.get("kind") or record.get("kind") or "").strip().lower()
            actual_kind = str(record.get("kind") or "").strip().lower()
            if expected_kind not in {"image", "video", "audio"} or expected_kind != actual_kind:
                raise HTTPException(status_code=400, detail={
                    "code": "agent_input_media_kind_mismatch",
                    "message": f"Agent 输入媒体类型不匹配: {asset_id}",
                })
            seen_assets.add(asset_id)
            media.append({
                "asset_id": asset_id,
                "kind": actual_kind,
                "name": str(record.get("name") or item.get("name") or asset_id)[:160],
            })
    return {
        "prompt": "\n\n".join(prompts).strip(),
        "prompts": prompts,
        "prompt_node_ids": prompt_node_ids,
        "media": media,
    }


_AGENT_IMAGE_PARAM_SCHEMA: Dict[str, Tuple[type, ...]] = {
    "size": (str,), "width": (int,), "height": (int,), "aspect_ratio": (str,),
    "resolution": (str,), "quality": (str,), "model": (str,), "provider_id": (str,),
    "seed": (int,), "prompt": (str,), "media_type": (str,), "n": (int,),
    "reference_asset_ids": (list,),
}
_AGENT_VIDEO_PARAM_SCHEMA: Dict[str, Tuple[type, ...]] = {
    **_AGENT_IMAGE_PARAM_SCHEMA,
    "duration": (int,), "enhance_prompt": (bool,), "enable_upsample": (bool,),
    "watermark": (bool,), "camera_fixed": (bool,), "return_last_frame": (bool,),
    "generate_audio": (bool,),
}
_AGENT_PROMPT_PARAM_SCHEMA: Dict[str, Tuple[type, ...]] = {
    "prompt": (str,), "media_type": (str,), "n": (int,), "model": (str,),
    "style": (str,), "format": (str,), "language": (str,), "reference_asset_ids": (list,),
}
_AGENT_TASK_INPUT_KEYS = {
    "kind", "media_type", "type", "count", "quantity", "n", "params", "parameters",
    "prompt", "model_type", "modelType", "model", "reference_asset_ids", "references",
    # Durable fields are accepted only while re-validating a task already
    # written by this server; arbitrary command-like fields never enter this set.
    "index", "task_id", "plan_id", "result_node_id", "status", "created_at", "updated_at",
    "started_at", "completed_at", "error", "outputs", "result", "prompt_output",
}
_AGENT_FORBIDDEN_VALUE_TOKENS = (
    "delete", "global_settings", "global-setting", "photoshop", "comfyui", "comfy",
    "local_runtime", "local-runtime", "filesystem", "project_library", "workspace",
    "rm -rf", "rmrf", "api_key", "apikey", "secret", "password", "bearer",
)


def _agent_forbidden_value(value: Any) -> bool:
    """Reject operation/path-like values recursively, including nested lists."""
    if isinstance(value, dict):
        for key, item in value.items():
            key_text = str(key or "").strip().lower().replace("-", "_")
            # Unknown command/control fields are rejected by the typed schema;
            # this explicit check keeps the error stable for security callers.
            if key_text in {"operation", "action", "command", "target", "engine", "projectid", "project_id", "workspace_id", "canvas_id"}:
                return True
            if any(token in key_text for token in ("api_key", "apikey", "secret", "password", "token", "url", "uri", "path", "file", "href", "locator", "project_library", "photoshop", "comfy", "local_runtime")):
                return True
            if _agent_forbidden_value(item):
                return True
    elif isinstance(value, list):
        return any(_agent_forbidden_value(item) for item in value)
    elif isinstance(value, str):
        text = value.strip().lower()
        if (
            any(token in text for token in _AGENT_FORBIDDEN_VALUE_TOKENS)
            or text.startswith(("file:", "\\\\", "/", "../", "..\\", "http://", "https://"))
            or re.match(r"^[a-z]:[\\/]", text)
            or "\\" in text
            or "://" in text
        ):
            return True
    return False


def _agent_clean_params(value: Any, kind: str = "") -> Dict[str, Any]:
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise HTTPException(status_code=400, detail="Agent 任务参数必须是对象")
    if _agent_forbidden_value(value):
        raise HTTPException(status_code=400, detail={
            "code": "agent_forbidden_operation",
            "message": "Agent 参数包含被禁止的删除、全局、跨项目、PS、本地运行时或路径操作",
        })
    schema = _AGENT_PROMPT_PARAM_SCHEMA if kind == "prompt" else _AGENT_VIDEO_PARAM_SCHEMA if kind == "video" else _AGENT_IMAGE_PARAM_SCHEMA
    params: Dict[str, Any] = {}
    for key, item in value.items():
        key_text = str(key)
        if key_text not in schema:
            raise HTTPException(status_code=400, detail={
                "code": "agent_parameter_unknown",
                "message": f"Agent 不允许参数字段: {key_text}",
            })
        expected_types = schema[key_text]
        # bool is a subclass of int in Python; keep numeric controls strict.
        if not isinstance(item, expected_types) or (int in expected_types and isinstance(item, bool)):
            raise HTTPException(status_code=400, detail={
                "code": "agent_parameter_type",
                "message": f"Agent 参数 {key_text} 类型无效",
            })
        if isinstance(item, str) and len(item) > 2000:
            raise HTTPException(status_code=400, detail=f"Agent 参数 {key_text} 过长")
        if key_text in {"width", "height"} and not 1 <= int(item) <= 16384:
            raise HTTPException(status_code=400, detail=f"Agent 参数 {key_text} 超出范围")
        if key_text == "duration" and not 1 <= int(item) <= 60:
            raise HTTPException(status_code=400, detail="Agent 视频时长必须在 1 到 60 秒之间")
        if key_text == "n" and not 1 <= int(item) <= 8:
            raise HTTPException(status_code=400, detail="Agent 参数 n 超出范围")
        if key_text == "reference_asset_ids":
            if any(not isinstance(asset_id, str) or not re.fullmatch(r"[A-Za-z0-9._-]{1,200}", asset_id) for asset_id in item):
                raise HTTPException(status_code=400, detail="Agent 参考素材标识无效")
        params[key_text] = copy.deepcopy(item)
    try:
        encoded = json.dumps(params, ensure_ascii=False)
    except (TypeError, ValueError) as exc:
        raise HTTPException(status_code=400, detail="Agent 任务参数必须可序列化") from exc
    if len(encoded.encode("utf-8")) > 64 * 1024:
        raise HTTPException(status_code=400, detail="Agent 任务参数超过 64KB 限制")
    return params


def _agent_task_specs(payload: ProjectAgentPlanRequest) -> List[Dict[str, Any]]:
    values = payload.tasks if payload.tasks else payload.task_specs
    if not values:
        # A concise goal is enough for the first-run UI: create one image task
        # using the project's connected prompt/media.
        values = [{"kind": "image", "count": 1, "params": {}}]
    if not isinstance(values, list) or len(values) > PROJECT_AGENT_MAX_TASKS_PER_PLAN:
        raise HTTPException(status_code=400, detail={
            "code": "agent_plan_task_limit",
            "message": f"Agent 单个计划最多包含 {PROJECT_AGENT_MAX_TASKS_PER_PLAN} 个任务",
        })
    if any(not isinstance(item, dict) for item in values):
        raise HTTPException(status_code=400, detail="Agent 计划任务必须是对象")
    return [copy.deepcopy(item) for item in values]


def _agent_normalize_task(
    raw: Dict[str, Any],
    index: int,
    *,
    goal: str,
    inputs: Dict[str, Any],
) -> Dict[str, Any]:
    unknown_task_keys = set(raw.keys()) - _AGENT_TASK_INPUT_KEYS
    if unknown_task_keys:
        raise HTTPException(status_code=400, detail={
            "code": "agent_task_field_unknown",
            "message": f"Agent 不允许任务字段: {sorted(str(key) for key in unknown_task_keys)[0]}",
        })
    kind = str(raw.get("kind") or raw.get("media_type") or raw.get("type") or "").strip().lower()
    if kind in {"prompt-create", "prompt_creation", "prompt-node", "text-prompt"}:
        kind = "prompt"
    if kind in {"image-generation", "online-image", "picture"}:
        kind = "image"
    if kind in {"video-generation", "online-video", "movie"}:
        kind = "video"
    if kind not in {"prompt", "image", "video"}:
        raise HTTPException(status_code=400, detail={
            "code": "agent_task_kind_forbidden",
            "message": "Agent 第一阶段只允许创建提示词节点或在线图片/在线视频任务",
        })
    raw_count = raw.get("count", raw.get("quantity", raw.get("n", 1)))
    if isinstance(raw_count, bool):
        raise HTTPException(status_code=400, detail="Agent 任务数量无效")
    try:
        count = int(raw_count or 1)
    except (TypeError, ValueError) as exc:
        raise HTTPException(status_code=400, detail="Agent 任务数量无效") from exc
    maximum = 1 if kind == "prompt" else 8 if kind == "image" else 4
    if count < 1 or count > maximum:
        raise HTTPException(status_code=400, detail=f"Agent {kind} 任务数量必须在 1 到 {maximum} 之间")
    params = _agent_clean_params(raw.get("params") if "params" in raw else raw.get("parameters"), kind)
    prompt = str(raw.get("prompt") or params.get("prompt") or inputs.get("prompt") or goal or "").strip()
    if not prompt:
        raise HTTPException(status_code=400, detail="Agent 任务缺少提示词输入")
    prompt_limit = VIDEO_PROMPT_MAX_LENGTH if kind == "video" else ONLINE_IMAGE_PROMPT_MAX_LENGTH
    if len(prompt) > prompt_limit:
        raise HTTPException(status_code=400, detail="Agent 任务提示词超过安全限制")
    params.setdefault("prompt", prompt)
    params["media_type"] = kind
    params["n"] = count
    model_type = str(raw.get("model_type") or raw.get("modelType") or raw.get("model") or params.get("model") or (
        "agent-prompt-node" if kind == "prompt" else "online-image" if kind == "image" else "online-video"
    )).strip()[:160]
    if not model_type:
        model_type = "agent-prompt-node" if kind == "prompt" else "online-image" if kind == "image" else "online-video"
    model_type_lower = model_type.lower()
    if any(token in model_type_lower for token in ("comfy", "local", "photoshop", "_ps", "ps-", "filesystem", "runtime")):
        raise HTTPException(status_code=400, detail={
            "code": "agent_model_forbidden",
            "message": "Agent 只允许在线图片/视频模型或本地提示词节点",
        })
    if kind == "prompt":
        if model_type != "agent-prompt-node":
            raise HTTPException(status_code=400, detail={"code": "agent_model_forbidden", "message": "提示词任务模型类型无效"})
    elif model_type in {"agent-prompt-node", "prompt"}:
        raise HTTPException(status_code=400, detail={"code": "agent_model_forbidden", "message": "媒体任务必须使用在线模型"})
    if kind == "prompt":
        references = []
    else:
        requested_ids = raw.get("reference_asset_ids")
        if requested_ids is None:
            requested_ids = raw.get("references")
        if requested_ids is None:
            allowed_kinds = {"image"} if kind == "image" else {"image", "video", "audio"}
            references = [item for item in inputs.get("media") or [] if item.get("kind") in allowed_kinds]
        else:
            if not isinstance(requested_ids, list):
                raise HTTPException(status_code=400, detail="Agent 参考素材必须是数组")
            requested_ids = [str(item.get("asset_id") if isinstance(item, dict) else item).strip() for item in requested_ids]
            connected = {str(item.get("asset_id")) for item in inputs.get("media") or []}
            if any(item not in connected for item in requested_ids):
                raise HTTPException(status_code=400, detail={
                    "code": "agent_reference_not_connected",
                    "message": "Agent 只能使用直接连接到当前节点的项目媒体",
                })
            references = [item for item in inputs.get("media") or [] if item.get("asset_id") in requested_ids]
            order = {asset_id: idx for idx, asset_id in enumerate(requested_ids)}
            references.sort(key=lambda item: order.get(str(item.get("asset_id")), 10_000))
    if kind == "image" and any(item.get("kind") != "image" for item in references):
        raise HTTPException(status_code=400, detail="图片任务只能使用直接连接的图片参考")
    params["reference_asset_ids"] = [str(item["asset_id"]) for item in references]
    if kind == "video":
        params.setdefault("duration", 5)
    return {
        "index": index,
        "kind": kind,
        "model_type": model_type,
        "count": count,
        "prompt": prompt,
        "params": params,
        "references": copy.deepcopy(references),
        "reference_asset_ids": [str(item["asset_id"]) for item in references],
    }


def _agent_plan_hash(plan: Mapping[str, Any]) -> str:
    value = {
        "goal": plan.get("goal"),
        "inputs": plan.get("inputs"),
        # Status/timestamps/results are runtime state and must not invalidate
        # the confirmation commitment.  IDs remain committed so a PATCH cannot
        # swap an executable task or result target under the same hash.
        "tasks": [
            {
                **{
                    key: copy.deepcopy(item.get(key))
                    for key in ("task_id", "result_node_id", "kind", "model_type", "count", "prompt", "params", "references", "reference_asset_ids")
                }
            }
            for item in (plan.get("tasks") or [])
            if isinstance(item, dict)
        ],
    }
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()


def _agent_task_canonical(task: Mapping[str, Any]) -> Dict[str, Any]:
    """Return the only task fields that are user-confirmed and executable."""
    return {
        key: copy.deepcopy(task.get(key))
        for key in ("kind", "model_type", "count", "prompt", "params", "references", "reference_asset_ids")
    }


def _agent_plan_from_node(node: Mapping[str, Any], plan_id: str) -> Optional[Dict[str, Any]]:
    plan = node.get("agent_plan")
    if not isinstance(plan, dict) or str(plan.get("plan_id") or "") != str(plan_id):
        return None
    return copy.deepcopy(plan)


def _agent_public_task(task: Mapping[str, Any]) -> Dict[str, Any]:
    return {
        key: copy.deepcopy(value)
        for key, value in task.items()
        if key not in {"adapter_request", "raw_result"}
    }


def _agent_history_output(project_id: str, item: Mapping[str, Any], kind: str) -> Dict[str, Any]:
    asset_id = str(item.get("asset_id") or "")
    output = {
        "asset_id": asset_id,
        "kind": kind,
        "name": str(item.get("name") or asset_id),
    }
    return output


def _agent_apply_mutations(project_id: str, mutations: List[Dict[str, Any]], *, retries: int = 8) -> Dict[str, Any]:
    """Apply agent-visible state changes against the latest project revision."""
    last_conflict: Optional[RevisionConflict] = None
    for _attempt in range(max(1, retries)):
        opened = project_store_call("open_project", project_id)
        try:
            result = project_store_call(
                "apply_mutations",
                project_id,
                mutations,
                base_revision=int(opened["canvas"].get("revision") or 0),
            )
            project_store_call("flush", project_id)
            return result
        except RevisionConflict as exc:
            last_conflict = exc
            continue
    if last_conflict is not None:
        raise last_conflict
    raise ProjectStoreError("Agent 项目状态无法保存")


def _agent_find_task_node(canvas: Mapping[str, Any], task_id: str) -> Optional[Dict[str, Any]]:
    return next(
        (
            node for node in canvas.get("nodes") or []
            if isinstance(node, dict)
            and str(node.get("type") or "") == "agent-task"
            and str(node.get("task_id") or node.get("id") or "") == str(task_id)
        ),
        None,
    )


def _agent_task_node(
    task: Mapping[str, Any],
    *,
    agent_id: str,
    execution_id: str,
    status: str = "queued",
    x: Optional[float] = None,
    y: Optional[float] = None,
    w: int = 300,
    h: int = 236,
) -> Dict[str, Any]:
    task_id = str(task.get("task_id") or "")
    return {
        "id": task_id,
        "type": "agent-task",
        "title": f"Agent 任务 {int(task.get('index') or 0) + 1}",
        "agent_id": agent_id,
        "task_id": task_id,
        "plan_id": str(task.get("plan_id") or ""),
        "execution_id": execution_id,
        "kind": str(task.get("kind") or "image"),
        "model_type": str(task.get("model_type") or ""),
        "count": int(task.get("count") or 1),
        "prompt": str(task.get("prompt") or ""),
        "params": copy.deepcopy(task.get("params") or {}),
        "reference_asset_ids": copy.deepcopy(task.get("reference_asset_ids") or []),
        "status": status,
        "created_at": int(task.get("created_at") or now_ms()),
        "updated_at": now_ms(),
        "result_node_id": str(task.get("result_node_id") or f"{task_id}-result"),
        "idempotency_key": str(task.get("idempotency_key") or task_id),
        "dispatch_state": str(task.get("dispatch_state") or "queued"),
        "x": float(x if x is not None else task.get("x") or 0),
        "y": float(y if y is not None else task.get("y") or 0),
        "w": int(w or task.get("w") or 300),
        "h": int(h or task.get("h") or 236),
    }


def _agent_result_node(
    task: Mapping[str, Any], *, agent_id: str, execution_id: str,
    x: Optional[float] = None, y: Optional[float] = None, w: int = 300, h: int = 236,
) -> Dict[str, Any]:
    kind = str(task.get("kind") or "image")
    result_node_id = str(task.get("result_node_id") or f"{task.get('task_id')}-result")
    if kind == "prompt":
        return {
            "id": result_node_id,
            "type": "smart-prompt",
            "title": "Agent 提示词结果",
            "x": 0,
            "y": 0,
            "text": "",
            "source_agent_task_id": str(task.get("task_id") or ""),
            "outputKind": "prompt",
            "generationState": "queued",
            "agent_id": agent_id,
            "agent_task_id": str(task.get("task_id") or ""),
            "execution_id": execution_id,
            "created_at": now_ms(),
            "x": float(x if x is not None else task.get("result_x") or 0),
            "y": float(y if y is not None else task.get("result_y") or 0),
            "w": int(w or task.get("result_w") or 300),
            "h": int(h or task.get("result_h") or 236),
        }
    return {
        "id": result_node_id,
        "type": "smart-image",
        "title": "Agent 图片结果" if kind == "image" else "Agent 视频结果",
        "x": 0,
        "y": 0,
        "images": [],
        "outputKind": kind,
        "pending": int(task.get("count") or 1),
        "running": False,
        "agent_id": agent_id,
        "agent_task_id": str(task.get("task_id") or ""),
        "execution_id": execution_id,
        "created_at": now_ms(),
        "x": float(x if x is not None else task.get("result_x") or 0),
        "y": float(y if y is not None else task.get("result_y") or 0),
        "w": int(w or task.get("result_w") or 300),
        "h": int(h or task.get("result_h") or 236),
    }


def _agent_task_layout(canvas: Mapping[str, Any], agent: Mapping[str, Any], count: int) -> List[Dict[str, float]]:
    """Place task/result pairs next to the Agent without covering canvas nodes."""
    task_w = result_w = 300
    node_h = 236
    gap = 24
    agent_x = float(agent.get("x") or 0)
    agent_y = float(agent.get("y") or 0)
    agent_w = float(agent.get("w") or 330)
    agent_h = float(agent.get("h") or 360)
    occupied = []
    for node in canvas.get("nodes") or []:
        if not isinstance(node, dict) or str(node.get("id") or "") == str(agent.get("id") or ""):
            continue
        node_type = str(node.get("type") or "")
        width = float(node.get("w") or (300 if node_type == "agent-task" else 340 if node_type == "smart-agent" else 226))
        height = float(node.get("h") or (236 if node_type == "agent-task" else 360 if node_type == "smart-agent" else 180))
        occupied.append((float(node.get("x") or 0), float(node.get("y") or 0), width, height))
    # Start below the Agent, then try left/right columns if a neighboring
    # Agent or user node occupies that row.
    x_candidates = [agent_x, agent_x - (task_w + result_w + gap * 2), agent_x + agent_w + gap]
    layouts: List[Dict[str, float]] = []
    for index in range(max(0, int(count))):
        placed = None
        for row in range(0, 128):
            y = agent_y + agent_h + gap + row * (node_h + gap)
            for x in x_candidates:
                pair = [(x, y, task_w, node_h), (x + task_w + gap, y, result_w, node_h)]
                all_rects = [*occupied, *[(item["x"], item["y"], task_w, node_h) for item in layouts], *[(item["result_x"], item["y"], result_w, node_h) for item in layouts]]
                if all(
                    not (candidate[0] < existing[0] + existing[2] and candidate[0] + candidate[2] > existing[0] and candidate[1] < existing[1] + existing[3] and candidate[1] + candidate[3] > existing[1])
                    for candidate in pair for existing in all_rects
                ):
                    placed = {"x": x, "y": y, "result_x": x + task_w + gap, "result_y": y}
                    break
            if placed:
                break
        if placed is None:
            placed = {"x": agent_x, "y": agent_y + agent_h + gap + index * (node_h + gap), "result_x": agent_x + task_w + gap, "result_y": agent_y + agent_h + gap + index * (node_h + gap)}
        layouts.append(placed)
    return layouts


def _agent_int_param(params: Mapping[str, Any], key: str, default: Optional[int] = None) -> Optional[int]:
    value = params.get(key)
    if value in (None, ""):
        return default
    try:
        return int(value)
    except (TypeError, ValueError) as exc:
        raise ProjectStoreError(f"Agent 参数 {key} 必须是整数") from exc


async def project_agent_default_image_adapter(task_request: Dict[str, Any]) -> Dict[str, Any]:
    """Route one Agent image task through the existing online image boundary.

    This wrapper deliberately returns adapter candidates only.  The Agent
    worker remains the single owner of project-media archiving, result-node
    completion, and Agent history, so a provider failure cannot silently look
    successful or create a second generation record.
    """
    project_id = str(task_request.get("project_id") or "")
    params = task_request.get("params") if isinstance(task_request.get("params"), dict) else {}
    prompt = str(task_request.get("prompt") or "").strip()
    reference_ids = [str(value).strip() for value in params.get("reference_asset_ids") or [] if str(value).strip()]
    request_snapshot = ImageGenerationRequest(
        prompt=prompt,
        size=str(params.get("size") or "1024x1024"),
        width=_agent_int_param(params, "width"),
        height=_agent_int_param(params, "height"),
        aspect_ratio=str(params.get("aspect_ratio") or "1:1"),
        resolution=str(params.get("resolution") or "1k"),
        reference_asset_ids=tuple(reference_ids),
        media_type="image",
        quality=str(params.get("quality") or "auto"),
        count=max(1, min(8, int(task_request.get("count") or params.get("n") or 1))),
    )
    providers = load_api_providers()
    model_hint = str(params.get("model") or "").strip()
    if not model_hint and str(task_request.get("model_type") or "").strip() not in {"", "online-image", "image"}:
        model_hint = str(task_request.get("model_type") or "").strip()
    route = route_image_generation(
        request_snapshot,
        providers,
        provider_id=str(params.get("provider_id") or ""),
        model=model_hint,
    )
    references = await asyncio.to_thread(project_image_references, project_id, reference_ids)
    image_parameters = {
        "width": request_snapshot.width,
        "height": request_snapshot.height,
        "aspect_ratio": request_snapshot.aspect_ratio,
        "resolution": request_snapshot.resolution,
        "media_type": "image",
        "agent_idempotency_key": str(task_request.get("idempotency_key") or task_request.get("task_id") or ""),
    }
    candidates: List[Dict[str, Any]] = []
    scope_token = PROJECT_MEDIA_ADAPTER_PROJECT.set(project_id)
    try:
        for _ in range(request_snapshot.count):
            image_data, raw = await generate_ai_image(
                prompt,
                request_snapshot.size,
                request_snapshot.quality,
                route.model,
                references,
                route.provider_id,
                preserve_model=True,
                image_parameters=image_parameters,
            )
            try:
                extracted = extract_images(raw) if isinstance(raw, dict) else []
            except HTTPException:
                extracted = []
            candidates.extend(extracted or [image_data])
            if len(candidates) >= request_snapshot.count:
                break
    finally:
        PROJECT_MEDIA_ADAPTER_PROJECT.reset(scope_token)
    candidates = [item for item in candidates if isinstance(item, dict)][:request_snapshot.count]
    if len(candidates) != request_snapshot.count:
        raise ProjectStoreError("在线模型没有返回计划数量的图片")
    return {"images": candidates, "route": route.to_dict()}


async def project_agent_default_video_adapter(task_request: Dict[str, Any]) -> Dict[str, Any]:
    """Route one Agent video task through the existing online video boundary."""
    project_id = str(task_request.get("project_id") or "")
    params = task_request.get("params") if isinstance(task_request.get("params"), dict) else {}
    prompt = str(task_request.get("prompt") or "").strip()
    references = [item for item in task_request.get("references") or [] if isinstance(item, dict)]
    image_ids = [str(item.get("asset_id")) for item in references if item.get("kind") == "image"]
    video_ids = [str(item.get("asset_id")) for item in references if item.get("kind") == "video"]
    audio_ids = [str(item.get("asset_id")) for item in references if item.get("kind") == "audio"]
    request_snapshot = VideoGenerationRequest(
        prompt=prompt,
        size=str(params.get("size") or "1024x576"),
        width=_agent_int_param(params, "width"),
        height=_agent_int_param(params, "height"),
        aspect_ratio=str(params.get("aspect_ratio") or "16:9"),
        resolution=str(params.get("resolution") or "720p"),
        duration=max(1, min(60, int(params.get("duration") or 5))),
        image_reference_asset_ids=tuple(image_ids),
        video_reference_asset_ids=tuple(video_ids),
        audio_reference_asset_ids=tuple(audio_ids),
        media_type="video",
        quality=str(params.get("quality") or "auto"),
        count=max(1, min(4, int(task_request.get("count") or params.get("n") or 1))),
        seed=_agent_int_param(params, "seed"),
        enhance_prompt=bool(params.get("enhance_prompt", False)),
        enable_upsample=bool(params.get("enable_upsample", False)),
        watermark=bool(params.get("watermark", False)),
        camera_fixed=bool(params.get("camera_fixed", False)),
        return_last_frame=bool(params.get("return_last_frame", False)),
        generate_audio=bool(params.get("generate_audio", False)),
    )
    provider_id = str(params.get("provider_id") or "")
    model = str(params.get("model") or "").strip()
    if not model and str(task_request.get("model_type") or "").strip() not in {"", "online-video", "video"}:
        model = str(task_request.get("model_type") or "").strip()
    resolved_references = [
        *await asyncio.to_thread(project_video_references, project_id, image_ids, "image"),
        *await asyncio.to_thread(project_video_references, project_id, video_ids, "video"),
        *await asyncio.to_thread(project_video_references, project_id, audio_ids, "audio"),
    ]
    provider_records = await preflight_project_video_providers(
        request_snapshot,
        resolved_references,
        load_api_providers(),
        provider_id=provider_id,
        model=model,
    )
    route = route_video_generation(
        request_snapshot,
        provider_records,
        provider_id=provider_id,
        model=model,
        adapter_incompatible_parameters=functools.partial(
            project_video_adapter_incompatible_parameters,
            references=resolved_references,
        ),
    )
    provider = next(
        (item for item in provider_records if str(item.get("id") or "").strip().lower() == route.provider_id),
        None,
    )
    candidates, raw = await execute_project_video_generation(
        project_id, request_snapshot, route, resolved_references, provider=provider,
    )
    candidates = [item for item in candidates if isinstance(item, dict)][:request_snapshot.count]
    if len(candidates) != request_snapshot.count:
        raise ProjectStoreError("在线模型没有返回计划数量的视频")
    return {"videos": candidates, "raw": raw if isinstance(raw, dict) else {}, "route": route.to_dict()}


# Production uses the existing project-owned online boundaries by default;
# tests and embedding callers can replace either seam with an offline fake.
agent_image_adapter = project_agent_default_image_adapter
agent_video_adapter = project_agent_default_video_adapter


def _agent_normalize_candidates(raw: Any, kind: str) -> List[Dict[str, Any]]:
    """Normalize fake/online adapter return shapes without accepting tools."""
    if isinstance(raw, dict):
        values = raw.get("images") if kind == "image" else raw.get("videos")
        if values is None:
            values = raw.get("media") or raw.get("outputs")
        if values is None and (raw.get("type") or raw.get("value")):
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
            mime = "image/png" if kind == "image" else "video/mp4"
            item = {"type": "b64", "value": base64.b64encode(bytes(item)).decode("ascii"), "mime_type": mime}
        elif isinstance(item, str):
            item = {"type": "url", "value": item}
        elif isinstance(item, dict):
            item = copy.deepcopy(item)
            if "asset_id" in item and not item.get("type") and not item.get("value"):
                # Adapters may return an existing project asset only when it is
                # one of the explicit inputs; generated output must be archived
                # as a new project-owned original below.
                item = {"asset_id": str(item.get("asset_id")), "kind": kind, "name": item.get("name") or ""}
        else:
            continue
        if isinstance(item, dict):
            normalized.append(item)
    return normalized


async def _agent_call_adapter(task_request: Dict[str, Any], kind: str) -> Any:
    adapter = agent_image_adapter if kind == "image" else agent_video_adapter
    if adapter is None:
        raise RuntimeError(f"Agent {kind} 在线适配器尚未配置")
    result = adapter(task_request)
    if inspect.isawaitable(result):
        try:
            return await asyncio.wait_for(result, timeout=max(1, float(PROJECT_AGENT_TASK_TIMEOUT_SECONDS)))
        except asyncio.TimeoutError as exc:
            raise RuntimeError("Agent 在线适配器超时，任务已安全失败") from exc
    return result


def _agent_set_task_node(project_id: str, task_id: str, **changes: Any) -> Optional[Dict[str, Any]]:
    for _attempt in range(8):
        opened = project_store_call("open_project", project_id)
        current = _agent_find_task_node(opened["canvas"], task_id)
        if not current:
            return None
        updated = copy.deepcopy(current)
        updated.update(copy.deepcopy(changes))
        updated["updated_at"] = now_ms()
        try:
            _agent_apply_mutations(project_id, [{"op": "upsert_node", "node": updated}], retries=1)
            return updated
        except RevisionConflict:
            continue
    raise ProjectStoreError(f"Agent 任务 {task_id} 状态保存冲突，状态需要恢复")


def _agent_update_plan(
    project_id: str,
    agent_id: str,
    plan_id: str,
    *,
    task_updates: Optional[Mapping[str, Mapping[str, Any]]] = None,
    execution_update: Optional[Mapping[str, Any]] = None,
    status: Optional[str] = None,
) -> Optional[Dict[str, Any]]:
    for _attempt in range(8):
        opened = project_store_call("open_project", project_id)
        agent = project_agent_node(opened["canvas"], agent_id)
        plan = _agent_plan_from_node(agent, plan_id)
        if not plan:
            return None
        if task_updates:
            next_tasks = []
            for item in plan.get("tasks") or []:
                task_id = str(item.get("task_id") or "")
                replacement = task_updates.get(task_id)
                updated_item = copy.deepcopy(item)
                if replacement:
                    updated_item.update(copy.deepcopy(replacement))
                next_tasks.append(updated_item)
            plan["tasks"] = next_tasks
        if execution_update:
            executions = [item for item in plan.get("executions") or [] if isinstance(item, dict)]
            execution_id = str(execution_update.get("execution_id") or "")
            found = False
            for item in executions:
                if str(item.get("execution_id") or "") == execution_id:
                    item.update(copy.deepcopy(execution_update))
                    found = True
                    break
            if not found and execution_id:
                executions.append(copy.deepcopy(dict(execution_update)))
            plan["executions"] = executions[-32:]
        if status:
            plan["status"] = status
        plan["updated_at"] = now_ms()
        replacement_agent = copy.deepcopy(agent)
        replacement_agent["agent_plan"] = plan
        try:
            _agent_apply_mutations(project_id, [{"op": "upsert_node", "node": replacement_agent}], retries=1)
            return plan
        except RevisionConflict:
            continue
    raise ProjectStoreError(f"Agent 计划 {plan_id} 状态保存冲突，状态需要恢复")


def _agent_mark_task_terminal(
    project_id: str,
    agent_id: str,
    plan_id: str,
    execution_id: str,
    task_id: str,
    status: str,
    **fields: Any,
) -> None:
    """Merge a task result and close the execution when every task is terminal."""
    plan = _agent_update_plan(
        project_id,
        agent_id,
        plan_id,
        task_updates={task_id: {"status": status, **copy.deepcopy(fields)}},
    )
    if not plan:
        return
    execution = next(
        (item for item in plan.get("executions") or [] if str(item.get("execution_id") or "") == str(execution_id)),
        None,
    )
    if not isinstance(execution, dict):
        return
    task_statuses = []
    for item in execution.get("tasks") or []:
        item_id = str(item.get("task_id") or "")
        plan_item = next((candidate for candidate in plan.get("tasks") or [] if str(candidate.get("task_id") or "") == item_id), {})
        task_statuses.append({
            "task_id": item_id,
            "result_node_id": str(plan_item.get("result_node_id") or item.get("result_node_id") or ""),
            "kind": str(plan_item.get("kind") or item.get("kind") or ""),
            "status": str(plan_item.get("status") or "queued"),
            **({"error": str(plan_item.get("error") or "")[:1000]} if plan_item.get("error") else {}),
        })
    terminal = all(item.get("status") in {"succeeded", "failed", "uncertain"} for item in task_statuses)
    execution_status = "completed" if terminal and all(item.get("status") == "succeeded" for item in task_statuses) else "failed" if terminal else "running"
    _agent_update_plan(
        project_id,
        agent_id,
        plan_id,
        execution_update={
            "execution_id": execution_id,
            "status": execution_status,
            "updated_at": now_ms(),
            "tasks": task_statuses,
        },
        status="completed" if terminal and all(item.get("status") == "succeeded" for item in task_statuses) else "failed" if terminal else "running",
    )


def _agent_recovery_payload(node: Mapping[str, Any], plan: Mapping[str, Any]) -> Optional[Dict[str, Any]]:
    task_id = str(node.get("task_id") or node.get("id") or "")
    if not task_id:
        return None
    plan_task = next((item for item in plan.get("tasks") or [] if isinstance(item, dict) and str(item.get("task_id") or "") == task_id), None)
    if not isinstance(plan_task, dict):
        return None
    execution_id = str(node.get("execution_id") or "")
    if not execution_id:
        execution_id = next((str(execution.get("execution_id") or "") for execution in plan.get("executions") or [] if any(str(item.get("task_id") or "") == task_id for item in execution.get("tasks") or [])), "")
    if not execution_id:
        return None
    task = copy.deepcopy(plan_task)
    # Durable task nodes carry the execution-visible fields and layout; the
    # plan remains the immutable source for prompt/references/params.
    for key in ("kind", "model_type", "count", "prompt", "params", "reference_asset_ids", "result_node_id", "plan_id"):
        if key in node and key not in {"params", "reference_asset_ids"}:
            task[key] = copy.deepcopy(node[key])
    task["execution_id"] = execution_id
    return task


def _agent_mark_recovered_uncertain(project_id: str, agent_id: str, plan: Mapping[str, Any], node: Mapping[str, Any]) -> None:
    """Close an in-flight provider call after a process crash without retrying it."""
    task_id = str(node.get("task_id") or node.get("id") or "")
    execution_id = str(node.get("execution_id") or "")
    plan_id = str(plan.get("plan_id") or node.get("plan_id") or "")
    if not task_id or not execution_id or not plan_id:
        return
    detail = "应用重启时在线调用状态未知；为避免重复计费，未自动重试，请重新生成计划"
    try:
        _agent_set_task_node(project_id, task_id, status="uncertain", uncertain=True, completed_at=now_ms(), error=detail)
        _agent_mark_task_terminal(
            project_id, agent_id, plan_id, execution_id, task_id, "uncertain", uncertain=True, error=detail,
        )
        project_store_call("flush", project_id)
    except Exception:
        # Recovery is best effort; leave the durable error for the next startup
        # scan rather than silently presenting a successful task.
        pass


def _agent_submit_task(task: Mapping[str, Any], project_id: str, agent_id: str, execution_id: str) -> Optional[Future]:
    """Register and submit one durable task exactly once per process."""
    task_id = str(task.get("task_id") or task.get("id") or "")
    if not task_id:
        return None
    with PROJECT_AGENT_TASKS_LOCK:
        current = PROJECT_AGENT_TASKS.get(task_id)
        if current and current.get("status") in {"succeeded", "failed", "uncertain"}:
            return None
        existing_future = PROJECT_AGENT_FUTURES.get(task_id)
        if task_id in PROJECT_AGENT_RUNNING_IDS or (existing_future is not None and not existing_future.done()):
            return existing_future
        PROJECT_AGENT_TASKS[task_id] = {
            "id": task_id,
            "task_id": task_id,
            "project_id": str(project_id),
            "agent_id": str(agent_id),
            "execution_id": str(execution_id),
            "status": "queued",
            "created_at": int(task.get("created_at") or now_ms()),
            "updated_at": time.time(),
            "result": None,
            "error": "",
        }
    future = ensure_project_image_generation_executor().submit(
        asyncio.run,
        run_project_agent_task(task_id, project_id, agent_id, copy.deepcopy(dict(task)), execution_id),
    )
    with PROJECT_AGENT_TASKS_LOCK:
        PROJECT_AGENT_FUTURES[task_id] = future

    def _forget(done_future: Future, task_id=task_id):
        with PROJECT_AGENT_TASKS_LOCK:
            if PROJECT_AGENT_FUTURES.get(task_id) is done_future:
                PROJECT_AGENT_FUTURES.pop(task_id, None)
            PROJECT_AGENT_RUNNING_IDS.discard(task_id)
            terminal_ids = [
                key for key, value in PROJECT_AGENT_TASKS.items()
                if value.get("status") in {"succeeded", "failed", "uncertain"}
            ]
            if len(terminal_ids) > 512:
                terminal_ids.sort(key=lambda key: float(PROJECT_AGENT_TASKS[key].get("updated_at") or 0))
                for old_id in terminal_ids[:-512]:
                    PROJECT_AGENT_TASKS.pop(old_id, None)
    future.add_done_callback(_forget)
    return future


def _agent_memory_task_active(task_id: str) -> bool:
    """Return whether this process still owns a live task future.

    A durable ``running`` node by itself is not evidence that the provider
    call is still ours: the process may have restarted after dispatch.  Only
    a live in-memory future/running marker lets an idempotent execute retry
    safely reuse that call.
    """
    task_id = str(task_id or "")
    if not task_id:
        return False
    with PROJECT_AGENT_TASKS_LOCK:
        memory = PROJECT_AGENT_TASKS.get(task_id) or {}
        future = PROJECT_AGENT_FUTURES.get(task_id)
        return (
            str(memory.get("status") or "") in {"queued", "running"}
            and (
                task_id in PROJECT_AGENT_RUNNING_IDS
                or (future is not None and not future.done())
            )
        )


def recover_project_agent_tasks() -> int:
    """Rehydrate confirmed queued Agent tasks after an app reload.

    The canvas is authoritative.  Queued work is submitted with its stable
    task ID.  A running task may already have reached the provider when the
    process died, so it is closed as ``uncertain`` rather than blindly retried
    and potentially billed twice.
    """
    recovered = 0
    with PROJECT_AGENT_RECOVERY_LOCK:
        try:
            projects = project_store_call("list_projects")
        except Exception:
            return 0
        for project in projects or []:
            project_id = str(project.get("id") or "")
            if not project_id:
                continue
            try:
                opened = project_store_call("open_project", project_id)
            except Exception:
                continue
            canvas = opened.get("canvas") or {}
            for agent in canvas.get("nodes") or []:
                if not isinstance(agent, dict) or str(agent.get("type") or "") != "smart-agent":
                    continue
                plan = agent.get("agent_plan")
                if not isinstance(plan, dict) or str(plan.get("status") or "") not in {"running", "confirmed"}:
                    continue
                for node in canvas.get("nodes") or []:
                    if not isinstance(node, dict) or str(node.get("type") or "") != "agent-task":
                        continue
                    if str(node.get("agent_id") or "") != str(agent.get("id") or "") or str(node.get("status") or "") not in {"queued", "running"}:
                        continue
                    if str(node.get("status") or "") == "running":
                        # A provider may already have accepted the request when
                        # the process died.  Never charge twice on a blind
                        # restart: make the uncertainty explicit and require a
                        # fresh user-confirmed plan.
                        _agent_mark_recovered_uncertain(project_id, str(agent.get("id") or ""), plan, node)
                        continue
                    task = _agent_recovery_payload(node, plan)
                    if not task:
                        continue
                    execution_id = str(task.get("execution_id") or node.get("execution_id") or "")
                    if _agent_submit_task(task, project_id, str(agent.get("id") or ""), execution_id):
                        recovered += 1
    return recovered


async def run_project_agent_task(task_id: str, project_id: str, agent_id: str, task: Dict[str, Any], execution_id: str) -> None:
    """Run one confirmed, persisted Agent task through its injected adapter."""
    task_id = str(task_id)
    started_at = now_ms()
    with PROJECT_AGENT_TASKS_LOCK:
        if task_id in PROJECT_AGENT_RUNNING_IDS:
            return
        memory = PROJECT_AGENT_TASKS.get(task_id)
        if memory and memory.get("status") in {"succeeded", "failed"}:
            return
        PROJECT_AGENT_RUNNING_IDS.add(task_id)
        PROJECT_AGENT_TASKS[task_id] = {
            "id": task_id,
            "task_id": task_id,
            "project_id": project_id,
            "agent_id": agent_id,
            "execution_id": execution_id,
            "status": "running",
            "created_at": started_at,
            "updated_at": time.time(),
            "result": None,
            "error": "",
        }
    kind = str(task.get("kind") or "image")
    request = {
        "project_id": project_id,
        "agent_id": agent_id,
        "plan_id": str(task.get("plan_id") or ""),
        "execution_id": execution_id,
        "task_id": task_id,
        "kind": kind,
        "model_type": str(task.get("model_type") or ""),
        "prompt": str(task.get("prompt") or ""),
        "params": copy.deepcopy(task.get("params") or {}),
        "references": copy.deepcopy(task.get("references") or []),
    }
    generated_items: List[Dict[str, Any]] = []
    history_record: Dict[str, Any]
    try:
        idempotency_key = str(task.get("idempotency_key") or task_id)
        _agent_set_task_node(
            project_id,
            task_id,
            status="running",
            started_at=started_at,
            idempotency_key=idempotency_key,
            dispatch_state="dispatched" if kind != "prompt" else "local",
        )
        if kind == "prompt":
            # Prompt creation is a local, explicit Agent capability.  It does
            # not cross an adapter boundary, does not import media, and does
            # not create graph connections to the new prompt node.
            completed_at = now_ms()
            generated_items = [{"kind": "prompt", "text": str(task.get("prompt") or "")}]
            history_record = {
                "id": f"agent-generation-{task_id}",
                "type": "agent-prompt",
                "status": "succeeded",
                "createdAt": started_at,
                "completedAt": completed_at,
                "runMs": max(0, completed_at - started_at),
                "platform": "Agent",
                "model": str(task.get("model_type") or "agent-prompt-node"),
                "parameters": copy.deepcopy(task.get("params") or {}),
                "input_references": [],
                "request": {"task_id": task_id, "plan_id": str(task.get("plan_id") or ""), "execution_id": execution_id},
                "agent_id": agent_id,
                "task_id": task_id,
                "plan_id": str(task.get("plan_id") or ""),
                "execution_id": execution_id,
                "prompt": str(task.get("prompt") or ""),
                "created_at": started_at,
                "completed_at": completed_at,
                "model_type": str(task.get("model_type") or "agent-prompt-node"),
                "params": copy.deepcopy(task.get("params") or {}),
                "references": [],
                # `outputs` is reserved for project media references by
                # ProjectStore.  A prompt result is text, so keep it in an
                # explicit non-media field instead of pretending it is an
                # asset record.
                "outputs": [],
                "prompt_output": {"text": str(task.get("prompt") or "")},
                "result_node_id": str(task.get("result_node_id") or f"{task_id}-result"),
            }
            result_node_id = str(task.get("result_node_id") or f"{task_id}-result")
            for _attempt in range(8):
                opened = project_store_call("open_project", project_id)
                result_node = next(
                    (node for node in opened["canvas"].get("nodes") or []
                     if isinstance(node, dict) and str(node.get("id") or "") == result_node_id),
                    None,
                )
                if not result_node or str(result_node.get("type") or "") != "smart-prompt":
                    raise ProjectStoreError("Agent 提示词结果节点不存在")
                replacement_result = copy.deepcopy(result_node)
                replacement_result.update({
                    "text": str(task.get("prompt") or ""),
                    "outputKind": "prompt",
                    "generationState": "succeeded",
                    "updated_at": completed_at,
                })
                history = [
                    item for item in (opened["canvas"].get("logs") or [])
                    if str(item.get("id") or "") != history_record["id"]
                ]
                history.append(copy.deepcopy(history_record))
                try:
                    canvas = _agent_apply_mutations(project_id, [
                        {"op": "upsert_node", "node": replacement_result},
                        {"op": "set_canvas_meta", "logs": history[-5000:]},
                    ], retries=1)
                    break
                except RevisionConflict:
                    continue
            else:
                raise ProjectStoreError("Agent 提示词结果无法保存")
            await asyncio.to_thread(project_store_call, "flush", project_id)
            _agent_set_task_node(
                project_id,
                task_id,
                status="succeeded",
                completed_at=completed_at,
                result={"prompt": str(task.get("prompt") or ""), "history_id": history_record["id"]},
                error="",
            )
            _agent_mark_task_terminal(
                project_id,
                agent_id,
                str(task.get("plan_id") or ""),
                execution_id,
                task_id,
                "succeeded",
                completed_at=completed_at,
                prompt_output={"text": str(task.get("prompt") or "")},
            )
            result = {
                "status": "succeeded",
                "task_id": task_id,
                "prompt": str(task.get("prompt") or ""),
                "history_id": history_record["id"],
                "result_node_id": result_node_id,
                "project_revision": int(canvas.get("revision") or 0),
            }
            with PROJECT_AGENT_TASKS_LOCK:
                PROJECT_AGENT_TASKS[task_id].update({"status": "succeeded", "result": result, "updated_at": time.time()})
            return
        raw_result = await _agent_call_adapter(request, kind)
        candidates = _agent_normalize_candidates(raw_result, kind)
        expected_count = int(task.get("count") or 1)
        if len(candidates) != expected_count:
            raise RuntimeError(f"Agent 适配器返回 {len(candidates)} 个结果，计划要求 {expected_count} 个")
        for index, candidate in enumerate(candidates):
            if candidate.get("asset_id"):
                # Agent outputs must be new project-owned originals.  Reusing
                # a connected input would make result/history point at the
                # producer and would also let an adapter smuggle foreign media.
                raise RuntimeError("Agent 适配器不能把现有项目媒体作为生成结果")
            elif kind == "image":
                archived = await archive_project_generated_image(project_id, candidate, index)
                if str(archived.get("asset_id") or "") in {str(item.get("asset_id") or "") for item in task.get("references") or []}:
                    raise RuntimeError("Agent 生成内容与参考原件重复，不能把输入原件作为结果")
                generated_items.append(archived)
            else:
                archived = await archive_project_generated_video(project_id, candidate, index)
                if str(archived.get("asset_id") or "") in {str(item.get("asset_id") or "") for item in task.get("references") or []}:
                    raise RuntimeError("Agent 生成内容与参考原件重复，不能把输入原件作为结果")
                generated_items.append(archived)
        completed_at = now_ms()
        history_record = {
            "id": f"agent-generation-{task_id}",
            "type": "agent-prompt" if kind == "prompt" else "agent-image" if kind == "image" else "agent-video",
            "status": "succeeded",
            "createdAt": started_at,
            "completedAt": completed_at,
            "runMs": max(0, completed_at - started_at),
            "platform": "Agent",
            "model": str(task.get("model_type") or ""),
            "parameters": copy.deepcopy(task.get("params") or {}),
            "input_references": [
                _agent_history_output(project_id, item, str(item.get("kind") or "image"))
                for item in task.get("references") or []
            ],
            "request": {"task_id": task_id, "plan_id": str(task.get("plan_id") or ""), "execution_id": execution_id},
            "agent_id": agent_id,
            "task_id": task_id,
            "plan_id": str(task.get("plan_id") or ""),
            "execution_id": execution_id,
            "prompt": str(task.get("prompt") or ""),
            "created_at": started_at,
            "completed_at": completed_at,
            "model_type": str(task.get("model_type") or ""),
            "params": copy.deepcopy(task.get("params") or {}),
            "references": [
                {"asset_id": str(item.get("asset_id")), "kind": str(item.get("kind") or "")}
                for item in task.get("references") or []
            ],
            "outputs": [_agent_history_output(project_id, item, kind) for item in generated_items],
            "result_node_id": str(task.get("result_node_id") or f"{task_id}-result"),
        }
        canvas = await asyncio.to_thread(
            project_store_call,
            "complete_media_generation",
            project_id,
            str(task.get("result_node_id") or f"{task_id}-result"),
            generated_items,
            history_record,
            media_kind=kind,
        )
        await asyncio.to_thread(project_store_call, "flush", project_id)
        _agent_set_task_node(
            project_id,
            task_id,
            status="succeeded",
            completed_at=completed_at,
            result={"media": copy.deepcopy(generated_items), "history_id": history_record["id"]},
            error="",
        )
        _agent_mark_task_terminal(
            project_id,
            agent_id,
            str(task.get("plan_id") or ""),
            execution_id,
            task_id,
            "succeeded",
            completed_at=completed_at,
            outputs=copy.deepcopy(generated_items),
        )
        result = {
            "status": "succeeded",
            "task_id": task_id,
            "media": copy.deepcopy(generated_items),
            "history_id": history_record["id"],
            "result_node_id": str(task.get("result_node_id") or f"{task_id}-result"),
            "project_revision": int(canvas.get("revision") or 0),
        }
        with PROJECT_AGENT_TASKS_LOCK:
            PROJECT_AGENT_TASKS[task_id].update({"status": "succeeded", "result": result, "updated_at": time.time()})
    except Exception as exc:
        detail = str(getattr(exc, "detail", None) or exc)
        failed_at = now_ms()
        # A multi-output adapter may yield one valid candidate before a later
        # candidate fails validation.  Those originals have not been attached
        # atomically yet; remove only the unreferenced media so a failed Agent
        # task cannot leak partial assets or history outputs.
        for generated in list(generated_items):
            asset_id = str(generated.get("asset_id") or "") if isinstance(generated, dict) else ""
            if asset_id:
                try:
                    project_store_call("delete_media_if_unreferenced", project_id, asset_id)
                except Exception:
                    pass
        generated_items = []
        history_record = {
            "id": f"agent-generation-{task_id}",
            "type": "agent-prompt" if kind == "prompt" else "agent-image" if kind == "image" else "agent-video",
            "status": "failed",
            "createdAt": started_at,
            "completedAt": failed_at,
            "runMs": max(0, failed_at - started_at),
            "platform": "Agent",
            "model": str(task.get("model_type") or ""),
            "parameters": copy.deepcopy(task.get("params") or {}),
            "input_references": [
                _agent_history_output(project_id, item, str(item.get("kind") or "image"))
                for item in task.get("references") or []
            ],
            "request": {"task_id": task_id, "plan_id": str(task.get("plan_id") or ""), "execution_id": execution_id},
            "agent_id": agent_id,
            "task_id": task_id,
            "plan_id": str(task.get("plan_id") or ""),
            "execution_id": execution_id,
            "prompt": str(task.get("prompt") or ""),
            "created_at": started_at,
            "completed_at": failed_at,
            "model_type": str(task.get("model_type") or ""),
            "params": copy.deepcopy(task.get("params") or {}),
            "references": [
                {"asset_id": str(item.get("asset_id")), "kind": str(item.get("kind") or "")}
                for item in task.get("references") or []
            ],
            "outputs": [],
            "result_node_id": str(task.get("result_node_id") or f"{task_id}-result"),
            "error": detail[:1000],
        }
        try:
            # Failure is visible in both the task node and project generation
            # history; no output media is created or deleted as compensation.
            _agent_set_task_node(project_id, task_id, status="failed", completed_at=failed_at, result=None, error=detail[:1000])
            for _attempt in range(8):
                opened = project_store_call("open_project", project_id)
                result_node = next((node for node in opened["canvas"].get("nodes") or [] if str(node.get("id") or "") == str(task.get("result_node_id") or f"{task_id}-result")), None)
                if not result_node:
                    break
                replacement_result = copy.deepcopy(result_node)
                replacement_result.update({"pending": 0, "running": False, "generationState": "failed", "error": detail[:1000]})
                history = copy.deepcopy(opened["canvas"].get("logs") or [])
                history = [item for item in history if str(item.get("id") or "") != history_record["id"]]
                history.append(history_record)
                try:
                    _agent_apply_mutations(project_id, [
                        {"op": "upsert_node", "node": replacement_result},
                        {"op": "set_canvas_meta", "logs": history[-5000:]},
                    ], retries=1)
                    break
                except RevisionConflict:
                    continue
            _agent_mark_task_terminal(
                project_id,
                agent_id,
                str(task.get("plan_id") or ""),
                execution_id,
                task_id,
                "failed",
                completed_at=failed_at,
                error=detail[:1000],
            )
            project_store_call("flush", project_id)
        except Exception:
            # Keep the in-memory status useful to the task endpoint; the next
            # project open can still recover the durable plan/task node.
            pass
        result = {"status": "failed", "task_id": task_id, "error": detail[:1000]}
        with PROJECT_AGENT_TASKS_LOCK:
            PROJECT_AGENT_TASKS[task_id].update({"status": "failed", "result": result, "error": detail[:1000], "updated_at": time.time()})


def _agent_plan_response(project_id: str, canvas: Dict[str, Any], agent: Dict[str, Any], plan: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "ok": True,
        "project_id": project_id,
        "agent": copy.deepcopy(agent),
        "plan": copy.deepcopy(plan),
        "canvas": copy.deepcopy(canvas),
    }


@app.post("/api/project-library/{project_id}/agent/{node_id}/plan")
@app.post("/api/project-library/{project_id}/agent/{node_id}/plans")
async def create_project_agent_plan(
    project_id: str,
    node_id: str,
    payload: ProjectAgentPlanRequest,
    request: Request,
):
    ensure_same_origin_request(request)
    request_id = str(payload.request_id or "").strip()
    if not request_id:
        raise HTTPException(status_code=400, detail={"code": "agent_request_id_required", "message": "Agent 计划需要稳定 request_id 以保证幂等"})
    try:
        opened = await asyncio.to_thread(project_store_call, "open_project", project_id)
        canvas = opened["canvas"]
        agent = project_agent_node(canvas, node_id)
        current_revision = int(canvas.get("revision") or 0)
        fields_set = getattr(payload, "model_fields_set", None)
        if fields_set is None:
            fields_set = getattr(payload, "__fields_set__", set())
        requested_revision = int(payload.base_revision or 0)
        if "base_revision" not in fields_set:
            requested_revision = current_revision
        if requested_revision != current_revision:
            raise RevisionConflict(canvas)
        previous_plan = agent.get("agent_plan")
        if request_id:
            durable_plans = []
            if isinstance(previous_plan, dict):
                durable_plans.append(previous_plan)
            durable_plans.extend(item for item in (agent.get("agent_plans") or []) if isinstance(item, dict))
            original = next((item for item in reversed(durable_plans) if str(item.get("request_id") or "") == request_id), None)
            if original is not None:
                agent_response = copy.deepcopy(agent)
                agent_response["agent_plan"] = copy.deepcopy(original)
                return _agent_plan_response(project_id, canvas, agent_response, copy.deepcopy(original))
        inputs = await asyncio.to_thread(project_agent_connected_inputs, project_id, canvas, node_id)
        goal = str(payload.goal or payload.prompt or "").strip()[:VIDEO_PROMPT_MAX_LENGTH]
        tasks = [
            _agent_normalize_task(item, index, goal=goal, inputs=inputs)
            for index, item in enumerate(_agent_task_specs(payload))
        ]
        if not tasks:
            raise HTTPException(status_code=400, detail="Agent 计划至少需要一个在线任务")
        now = now_ms()
        plan_id = f"agent-plan-{uuid.uuid4().hex}"
        planned_tasks = []
        for task in tasks:
            task_id = f"agent-task-{uuid.uuid4().hex}"
            planned_tasks.append({
                **copy.deepcopy(task),
                "task_id": task_id,
                "plan_id": plan_id,
                "result_node_id": f"{task_id}-result",
                "status": "planned",
                "created_at": now,
            })
        plan = {
            "plan_id": plan_id,
            "request_id": request_id,
            "status": "awaiting_confirmation",
            "goal": goal,
            "inputs": copy.deepcopy(inputs),
            "tasks": planned_tasks,
            "created_at": now,
            "updated_at": now,
            "planned_revision": current_revision,
            "executions": [],
        }
        plan["plan_hash"] = _agent_plan_hash(plan)
        replacement = copy.deepcopy(agent)
        replacement["agent_plan"] = plan
        history_plans = [item for item in replacement.get("agent_plans") or [] if isinstance(item, dict)]
        history_plans.append(copy.deepcopy(plan))
        replacement["agent_plans"] = history_plans[-32:]
        result = await asyncio.to_thread(
            project_store_call,
            "apply_mutations",
            project_id,
            [{"op": "upsert_node", "node": replacement}],
            base_revision=current_revision,
        )
        await asyncio.to_thread(project_store_call, "flush", project_id)
        saved = await asyncio.to_thread(project_store_call, "open_project", project_id)
        saved_agent = project_agent_node(saved["canvas"], node_id)
        return _agent_plan_response(project_id, saved["canvas"], saved_agent, saved_agent["agent_plan"])
    except RevisionConflict as exc:
        # Two browser retries with the same request id may race at the canvas
        # CAS.  The winner's durable plan is the idempotent response for the
        # loser; a different request still receives the normal stale 409.
        if request_id:
            try:
                reopened = await asyncio.to_thread(project_store_call, "open_project", project_id)
                reopened_agent = project_agent_node(reopened["canvas"], node_id)
                durable_plan = reopened_agent.get("agent_plan")
                if isinstance(durable_plan, dict) and str(durable_plan.get("request_id") or "") == request_id:
                    return _agent_plan_response(project_id, reopened["canvas"], reopened_agent, durable_plan)
            except (ProjectNotFound, ProjectStoreError, HTTPException):
                pass
        raise HTTPException(status_code=409, detail={"message": str(exc), "canvas": exc.canvas}) from exc
    except ProjectNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except HTTPException:
        raise
    except (ProjectStoreError, InvalidMutation, ValueError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.get("/api/project-library/{project_id}/agent/{node_id}/plan")
@app.get("/api/project-library/{project_id}/agent/{node_id}/plans")
async def get_project_agent_plan(project_id: str, node_id: str):
    try:
        opened = await asyncio.to_thread(project_store_call, "open_project", project_id)
        agent = project_agent_node(opened["canvas"], node_id)
        plan = agent.get("agent_plan")
        if not isinstance(plan, dict):
            raise HTTPException(status_code=404, detail="Agent 尚未生成计划")
        return _agent_plan_response(project_id, opened["canvas"], agent, plan)
    except ProjectNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


def _agent_execution_tasks_from_plan(plan: Mapping[str, Any], execution_id: str) -> List[Dict[str, Any]]:
    values = []
    for item in plan.get("tasks") or []:
        if not isinstance(item, dict):
            continue
        task = copy.deepcopy(item)
        task["execution_id"] = execution_id
        task["status"] = "queued"
        task["idempotency_key"] = str(task.get("task_id") or "")
        task["dispatch_state"] = "queued"
        values.append(task)
    return values


def _agent_active_counts(project_id: str, agent_id: str = "") -> Tuple[int, int, int]:
    global_count = project_count = node_count = 0
    with PROJECT_AGENT_TASKS_LOCK:
        for task in PROJECT_AGENT_TASKS.values():
            if task.get("status") not in {"queued", "running"}:
                continue
            global_count += 1
            if str(task.get("project_id") or "") == str(project_id):
                project_count += 1
                if str(task.get("agent_id") or "") == str(agent_id):
                    node_count += 1
    try:
        projects = project_store_call("list_projects")
    except Exception:
        projects = []
    # Include durable queued/running tasks after a process reload.
    for project in projects:
        candidate_project = str(project.get("id") or "")
        try:
            opened = project_store_call("open_project", candidate_project)
        except Exception:
            continue
        for node in opened["canvas"].get("nodes") or []:
            if not isinstance(node, dict) or str(node.get("type") or "") != "agent-task":
                continue
            if str(node.get("status") or "") not in {"queued", "running"}:
                continue
            task_id = str(node.get("task_id") or node.get("id") or "")
            with PROJECT_AGENT_TASKS_LOCK:
                if task_id in PROJECT_AGENT_TASKS:
                    continue
            global_count += 1
            if candidate_project == str(project_id):
                project_count += 1
                if str(node.get("agent_id") or "") == str(agent_id):
                    node_count += 1
    return global_count, project_count, node_count


def _agent_admit_execution_sync(
    project_id: str,
    node_id: str,
    payload_data: Mapping[str, Any],
):
    plan_id = str(payload_data.get("plan_id") or "").strip()
    execution_id = str(payload_data.get("execution_id") or "").strip()
    plan_hash = str(payload_data.get("plan_hash") or "").strip()
    payload_tasks = payload_data.get("tasks") or []
    if not execution_id:
        raise HTTPException(status_code=400, detail={"code": "agent_execution_id_required", "message": "Agent 执行需要稳定 execution_id 以保证幂等"})
    lock = project_agent_execution_lock(project_id, node_id)
    # Admission and durable task-node creation are one process-wide critical
    # section.  Per-node locks alone let two Agent nodes oversubscribe the
    # global/project limits between count and commit.
    with PROJECT_AGENT_ADMISSION_LOCK, lock:
        try:
            opened = project_store_call("open_project", project_id)
            canvas = opened["canvas"]
            agent = project_agent_node(canvas, node_id)
            plan = _agent_plan_from_node(agent, plan_id)
            if not plan:
                raise HTTPException(status_code=404, detail="Agent 计划不存在")
            stored_hash = str(plan.get("plan_hash") or "")
            canonical_hash = _agent_plan_hash(plan)
            # The hash is a commitment to the durable plan, not a value copied
            # from the durable node itself.  A generic canvas PATCH that edits
            # tasks/params while leaving the old hash must force a fresh plan.
            if not stored_hash or stored_hash != canonical_hash:
                raise HTTPException(status_code=409, detail={"code": "agent_plan_changed", "message": "Agent 计划已被修改，请重新生成并确认"})
            expected_hash = canonical_hash
            # Older embedded callers may omit the hash, but only a durable
            # request_id-backed plan is allowed to use that compatibility path;
            # a bare execute can never become an implicit confirmation.
            if plan_hash and plan_hash != expected_hash:
                raise HTTPException(status_code=409, detail={"code": "agent_plan_changed", "message": "Agent 计划已变化，请重新确认"})
            if not plan_hash and not str(plan.get("request_id") or ""):
                raise HTTPException(status_code=409, detail={"code": "agent_plan_changed", "message": "确认 Agent 计划需要 plan_hash 或 request_id"})
            current_inputs = project_agent_connected_inputs(project_id, canvas, node_id)
            if json.dumps(plan.get("inputs") or {}, ensure_ascii=False, sort_keys=True, separators=(",", ":")) != json.dumps(current_inputs, ensure_ascii=False, sort_keys=True, separators=(",", ":")):
                raise HTTPException(status_code=409, detail={"code": "agent_plan_changed", "message": "Agent 直连输入已变化，请重新生成并确认"})
            # Re-run the same typed task normalizer used at planning time.  Do
            # not execute a task merely because it was persisted in a canvas.
            validated_tasks = []
            for index, stored_task in enumerate(plan.get("tasks") or []):
                if not isinstance(stored_task, dict):
                    raise HTTPException(status_code=409, detail={"code": "agent_plan_changed", "message": "Agent 计划任务格式已变化"})
                normalized = _agent_normalize_task(stored_task, index, goal=str(plan.get("goal") or ""), inputs=current_inputs)
                if _agent_task_canonical(normalized) != _agent_task_canonical(stored_task):
                    raise HTTPException(status_code=409, detail={"code": "agent_plan_changed", "message": "Agent 计划任务已被修改，请重新生成并确认"})
                validated_tasks.append(normalized)
            if payload_tasks:
                comparable = []
                for item in payload_tasks:
                    if not isinstance(item, dict):
                        raise HTTPException(status_code=400, detail="确认任务必须是对象")
                    unknown_confirm_fields = set(item.keys()) - _AGENT_TASK_INPUT_KEYS
                    if unknown_confirm_fields:
                        raise HTTPException(status_code=400, detail={"code": "agent_task_field_unknown", "message": f"Agent 不允许确认字段: {sorted(str(key) for key in unknown_confirm_fields)[0]}"})
                    comparable.append({key: item.get(key) for key in ("task_id", "kind", "model_type", "count", "prompt", "params", "reference_asset_ids") if key in item})
                expected = [
                    {key: item.get(key) for key in ("task_id", "kind", "model_type", "count", "prompt", "params", "reference_asset_ids") if key in item}
                    for item in plan.get("tasks") or []
                ]
                if comparable != expected:
                    raise HTTPException(status_code=409, detail={"code": "agent_plan_changed", "message": "确认内容不是已展示的 Agent 计划"})
            executions = [item for item in plan.get("executions") or [] if isinstance(item, dict)]
            existing_execution = next((item for item in executions if str(item.get("execution_id") or "") == execution_id), None)
            if existing_execution is not None:
                # Idempotent retries may rehydrate queued work.  A durable
                # running/dispatching node is different: after a process
                # restart the provider may already own that request, so only
                # reuse it when this process still has a live future.  Blindly
                # submitting it again could charge the user twice.
                uncertain_task_ids: List[str] = []
                submit_tasks: List[Dict[str, Any]] = []
                for summary in existing_execution.get("tasks") or []:
                    if not isinstance(summary, dict) or str(summary.get("status") or "") not in {"queued", "running"}:
                        continue
                    task_id = str(summary.get("task_id") or "")
                    durable_node = _agent_find_task_node(canvas, task_id)
                    if not durable_node:
                        continue
                    durable_status = str(durable_node.get("status") or "")
                    if durable_status == "queued":
                        recovery_task = _agent_recovery_payload(durable_node, plan)
                        if recovery_task:
                            submit_tasks.append(recovery_task)
                    elif durable_status == "running":
                        if not _agent_memory_task_active(task_id):
                            _agent_mark_recovered_uncertain(project_id, node_id, plan, durable_node)
                            uncertain_task_ids.append(task_id)
                if uncertain_task_ids:
                    raise HTTPException(status_code=409, detail={
                        "code": "agent_execution_uncertain",
                        "message": "Agent 在线调用状态未知；为避免重复计费，请重新生成计划并确认新的 execution_id",
                        "task_ids": uncertain_task_ids,
                    })
                return {
                    "kind": "duplicate",
                    "ok": True,
                    "project_id": project_id,
                    "agent_id": node_id,
                    "plan_id": plan["plan_id"],
                    "execution_id": execution_id,
                    "status": str(existing_execution.get("status") or "running"),
                    "tasks": [copy.deepcopy(item) for item in existing_execution.get("tasks") or []],
                    "plan": copy.deepcopy(plan),
                    "submit_tasks": submit_tasks,
                }
            if str(plan.get("status") or "") not in {"awaiting_confirmation", "confirmed", "running"}:
                raise HTTPException(status_code=409, detail="Agent 计划已经完成，不能重复确认")
            if str(plan.get("status") or "") == "running":
                raise HTTPException(status_code=409, detail="Agent 计划正在执行，请等待当前执行完成")
            tasks = _agent_execution_tasks_from_plan(plan, execution_id)
            # This whole helper runs in a worker thread; keep the durable scan
            # inside the same synchronous lock as count-and-commit.
            global_count, project_count, node_count = _agent_active_counts(project_id, node_id)
            if global_count + len(tasks) > PROJECT_AGENT_MAX_PENDING:
                raise HTTPException(status_code=429, detail="Agent 任务队列已满")
            if project_count + len(tasks) > PROJECT_AGENT_MAX_PER_PROJECT:
                raise HTTPException(status_code=429, detail="当前项目的 Agent 任务队列已满")
            if node_count + len(tasks) > PROJECT_AGENT_MAX_PER_NODE:
                raise HTTPException(status_code=429, detail="当前 Agent 节点的待处理任务已达上限")
            mutations: List[Dict[str, Any]] = []
            task_w = 300
            result_w = 300
            layouts = _agent_task_layout(canvas, agent, len(tasks))
            for task_index, task in enumerate(tasks):
                task["status"] = "queued"
                layout = layouts[task_index]
                row_y = layout["y"]
                task["x"] = layout["x"]
                task["y"] = row_y
                task["w"] = task_w
                task["h"] = 236
                task["result_x"] = layout["result_x"]
                task["result_y"] = layout["result_y"]
                task["result_w"] = result_w
                task["result_h"] = 236
                mutations.append({"op": "upsert_node", "node": _agent_task_node(task, agent_id=node_id, execution_id=execution_id, x=layout["x"], y=row_y, w=task_w, h=236)})
                mutations.append({"op": "upsert_node", "node": _agent_result_node(task, agent_id=node_id, execution_id=execution_id, x=task["result_x"], y=row_y, w=result_w, h=236)})
                if str(task.get("kind") or "") != "prompt":
                    mutations.append({
                        "op": "upsert_edge",
                        "edge": {"id": f"{node_id}-{task['task_id']}", "from": node_id, "to": task["task_id"], "kind": "agent-task"},
                    })
                    mutations.append({
                        "op": "upsert_edge",
                        "edge": {"id": f"{task['task_id']}-result", "from": task["task_id"], "to": task["result_node_id"], "kind": "agent-result"},
                    })
            execution = {
                "execution_id": execution_id,
                "status": "running",
                "created_at": now_ms(),
                "updated_at": now_ms(),
                "tasks": [
                    {"task_id": task["task_id"], "result_node_id": task["result_node_id"], "kind": task["kind"], "status": "queued"}
                    for task in tasks
                ],
            }
            plan["status"] = "running"
            plan["updated_at"] = now_ms()
            plan["executions"] = [*executions, execution][-32:]
            replacement_agent = copy.deepcopy(agent)
            replacement_agent["agent_plan"] = plan
            mutations.append({"op": "upsert_node", "node": replacement_agent})
            _agent_apply_mutations(project_id, mutations)
            project_store_call("flush", project_id)
        except RevisionConflict as exc:
            raise HTTPException(status_code=409, detail={"message": str(exc), "canvas": exc.canvas}) from exc
        except ProjectNotFound as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except HTTPException:
            raise
        except (ProjectStoreError, InvalidMutation, ValueError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

        return {
            "kind": "new",
            "ok": True,
            "project_id": project_id,
            "agent_id": node_id,
            "plan_id": plan["plan_id"],
            "execution_id": execution_id,
            "status": "running",
            "tasks": [
                {"task_id": task["task_id"], "result_node_id": task["result_node_id"], "kind": task["kind"], "status": "queued"}
                for task in tasks
            ],
            "plan": copy.deepcopy(plan),
            "submit_tasks": copy.deepcopy(tasks),
        }


@app.post("/api/project-library/{project_id}/agent/{node_id}/execute")
@app.post("/api/project-library/{project_id}/agent/{node_id}/confirm")
@app.post("/api/project-library/{project_id}/agent/{node_id}/plan/execute")
async def execute_project_agent_plan(
    project_id: str,
    node_id: str,
    payload: ProjectAgentExecuteRequest,
    request: Request,
):
    ensure_same_origin_request(request)
    execution_id = str(payload.execution_id or "").strip()
    if not execution_id:
        raise HTTPException(status_code=400, detail={"code": "agent_execution_id_required", "message": "Agent 执行需要稳定 execution_id 以保证幂等"})
    payload_data = payload.model_dump() if hasattr(payload, "model_dump") else payload.dict()
    result = await asyncio.to_thread(_agent_admit_execution_sync, project_id, node_id, payload_data)
    submit_tasks = result.pop("submit_tasks", [])
    for task in submit_tasks:
        _agent_submit_task(task, project_id, node_id, execution_id)
    result.pop("kind", None)
    return JSONResponse(status_code=202, content=result)


@app.get("/api/project-library/{project_id}/agent/{node_id}/tasks/{task_id}")
async def get_project_agent_task(project_id: str, node_id: str, task_id: str):
    with PROJECT_AGENT_TASKS_LOCK:
        current = PROJECT_AGENT_TASKS.get(str(task_id))
        if current and str(current.get("project_id") or "") == str(project_id) and str(current.get("agent_id") or "") == str(node_id):
            return _agent_public_task(current)
    try:
        opened = await asyncio.to_thread(project_store_call, "open_project", project_id)
        project_agent_node(opened["canvas"], node_id)
        node = _agent_find_task_node(opened["canvas"], task_id)
        if not node or str(node.get("agent_id") or "") != str(node_id):
            raise HTTPException(status_code=404, detail="Agent 任务不存在")
        result = _agent_public_task(node)
        result_node_id = str(node.get("result_node_id") or "")
        result_node = next((item for item in opened["canvas"].get("nodes") or [] if str(item.get("id") or "") == result_node_id), None)
        if result_node is not None:
            result["result_node"] = copy.deepcopy(result_node)
        history_id = f"agent-generation-{task_id}"
        result["history_record"] = next((item for item in opened["canvas"].get("logs") or [] if str(item.get("id") or "") == history_id), None)
        return result
    except ProjectNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

async def generate_project_video(
    request_snapshot: VideoGenerationRequest,
    route,
    references: List[Dict[str, Any]],
    provider: Optional[Mapping[str, Any]] = None,
) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
    """Adapt project-owned references to the existing online video adapters."""
    legacy_payload = project_canvas_video_payload(
        request_snapshot, route.provider_id, route.model, references,
    )
    requested_count = max(1, int(request_snapshot.count or 1))
    candidates = []
    raw_results = []
    for _ in range(requested_count):
        result = (
            await execute_canvas_video(legacy_payload, provider)
            if provider is not None
            else await canvas_video(legacy_payload)
        )
        videos = [str(value or "").strip() for value in (result.get("videos") or []) if str(value or "").strip()]
        if not videos:
            raise ProjectStoreError("在线模型没有返回视频")
        candidates.extend(
            {"type": "url", "value": value, "mime_type": content_type_for_path(value)}
            for value in videos
        )
        if isinstance(result, dict):
            raw_results.append(result.get("raw") if isinstance(result.get("raw"), dict) else {})
    raw = raw_results[0] if requested_count == 1 else {"results": raw_results}
    return candidates, raw

async def execute_project_video_generation(
    project_id: str,
    request_snapshot: VideoGenerationRequest,
    route,
    references: List[Dict[str, Any]],
    provider: Optional[Mapping[str, Any]] = None,
) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
    """Run one adapter call inside a task-owned project-media capability scope."""
    clean_project_id = str(project_id or "").strip()
    if not clean_project_id:
        raise ProjectStoreError("在线视频任务缺少项目标识")
    scope_token = PROJECT_MEDIA_ADAPTER_PROJECT.set(clean_project_id)
    try:
        for item in references:
            adapter_url = item.get("adapter_url") or ""
            require_project_media_adapter_scope(adapter_url)
            project_media_adapter_path(adapter_url, item.get("kind") or "")
        if provider is None:
            return await generate_project_video(request_snapshot, route, references)
        return await generate_project_video(request_snapshot, route, references, provider=provider)
    finally:
        PROJECT_MEDIA_ADAPTER_PROJECT.reset(scope_token)

def generated_image_filename(index: int, mime_type: str) -> str:
    mime = str(mime_type or "image/png").lower()
    extension = ".jpg" if "jpeg" in mime or "jpg" in mime else ".webp" if "webp" in mime else ".gif" if "gif" in mime else ".png"
    return f"generated-{index + 1}{extension}"

def validate_generated_image(source) -> Tuple[int, int, str]:
    try:
        with Image.open(source) as probe:
            image_format = str(probe.format or "").upper()
            probe.verify()
        if hasattr(source, "seek"):
            source.seek(0)
        with Image.open(source) as decoded:
            decoded.load()
            width, height = decoded.size
            image_format = str(decoded.format or image_format).upper()
    except (OSError, ValueError, Image.DecompressionBombError) as exc:
        raise ProjectStoreError("在线模型返回的内容不是有效图片") from exc
    mime_types = {"PNG": "image/png", "JPEG": "image/jpeg", "WEBP": "image/webp", "GIF": "image/gif"}
    mime_type = mime_types.get(image_format)
    if not mime_type or width <= 0 or height <= 0:
        raise ProjectStoreError("在线模型返回了不支持的图片格式")
    return width, height, mime_type

def validate_generated_video(source) -> Tuple[int, int, str]:
    """Validate a returned video container before it becomes project media."""
    probe_bytes = 8 * 1024 * 1024
    try:
        if isinstance(source, (str, Path)):
            path = Path(source)
            file_size = path.stat().st_size if path.is_file() else 0
            if file_size <= 0:
                raise ProjectStoreError("在线模型返回的视频为空")
            with path.open("rb") as handle:
                sample = handle.read(probe_bytes)
                if file_size > probe_bytes:
                    handle.seek(max(0, file_size - probe_bytes))
                    sample += handle.read(probe_bytes)
        else:
            if hasattr(source, "seek"):
                source.seek(0)
            sample = source.read(probe_bytes)
            if hasattr(source, "seek"):
                current = source.tell()
                source.seek(0, 2)
                file_size = source.tell()
                if file_size > current:
                    source.seek(max(0, file_size - probe_bytes))
                    sample += source.read(probe_bytes)
            if hasattr(source, "seek"):
                source.seek(0)
    except (OSError, ValueError, TypeError) as exc:
        raise ProjectStoreError("在线模型返回的内容不是有效视频") from exc

    is_iso_bmff = len(sample) >= 12 and sample[4:8] == b"ftyp" and b"moov" in sample and b"vide" in sample
    is_webm = sample.startswith(b"\x1a\x45\xdf\xa3")
    is_avi = len(sample) >= 12 and sample[:4] == b"RIFF" and sample[8:12] == b"AVI "
    if is_iso_bmff:
        return 0, 0, "video/mp4"
    if is_webm:
        return 0, 0, "video/webm"
    if is_avi:
        return 0, 0, "video/x-msvideo"
    raise ProjectStoreError("在线模型返回的内容不是有效视频或不支持的视频格式")

async def ensure_public_generation_target(value: str, media_label: str = "图片") -> Tuple[str, Dict[str, str], Dict[str, Any]]:
    parsed = urllib.parse.urlsplit(str(value or ""))
    if parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.username or parsed.password:
        raise ProjectStoreError(f"在线模型返回了不安全的{media_label}地址")
    try:
        addresses = await asyncio.to_thread(
            socket.getaddrinfo, parsed.hostname, parsed.port or (443 if parsed.scheme == "https" else 80),
            type=socket.SOCK_STREAM,
        )
    except OSError as exc:
        raise ProjectStoreError(f"在线模型返回的{media_label}地址无法解析") from exc
    resolved = {item[4][0].split("%", 1)[0] for item in addresses if item[4]}
    if not resolved or any(not ipaddress.ip_address(address).is_global for address in resolved):
        raise ProjectStoreError(f"在线模型返回的{media_label}地址指向本机或私有网络")
    address = sorted(resolved)[0]
    authority_address = f"[{address}]" if ":" in address else address
    if parsed.port:
        authority_address = f"{authority_address}:{parsed.port}"
    pinned_url = urllib.parse.urlunsplit((parsed.scheme, authority_address, parsed.path, parsed.query, ""))
    return pinned_url, {"host": parsed.netloc}, {"sni_hostname": parsed.hostname.encode("idna").decode("ascii")}

async def archive_project_generated_image(project_id: str, image_data: Dict[str, Any], index: int) -> Dict[str, Any]:
    kind = str(image_data.get("type") or "url")
    mime_type = str(image_data.get("mime_type") or "image/png")
    filename = generated_image_filename(index, mime_type)
    source_info = {"type": "online_generation", "name": filename}
    width = height = 0
    if kind == "b64":
        try:
            content = base64.b64decode(str(image_data.get("value") or ""), validate=True)
        except (ValueError, TypeError) as exc:
            raise ProjectStoreError("在线模型返回了无效的图片数据") from exc
        width, height, mime_type = validate_generated_image(BytesIO(content))
        filename = generated_image_filename(index, mime_type)
        record = await asyncio.to_thread(
            project_store_call, "import_media", project_id, content, filename, mime_type, source_info
        )
    else:
        value = str(image_data.get("value") or "").strip()
        local_path = output_file_from_url(value)
        if local_path:
            width, height, mime_type = validate_generated_image(local_path)
            filename = generated_image_filename(index, mime_type)
            record = await asyncio.to_thread(
                project_store_call, "import_media_file", project_id, local_path, filename,
                mime_type, {**source_info, "name": filename, "locator": value},
            )
        elif value.startswith(("http://", "https://")):
            timeout = httpx.Timeout(connect=20.0, read=300.0, write=60.0, pool=20.0)
            descriptor, temporary_name = tempfile.mkstemp(prefix="firefly-generated-", suffix=".tmp")
            os.close(descriptor)
            temporary_path = Path(temporary_name)
            try:
                total = 0
                current_url = value
                async with httpx.AsyncClient(timeout=timeout, follow_redirects=False, trust_env=False) as client:
                    for redirect_count in range(6):
                        request_url, request_headers, request_extensions = await ensure_public_generation_target(current_url)
                        async with client.stream(
                            "GET", request_url, headers=request_headers, extensions=request_extensions,
                        ) as response:
                            if response.status_code in {301, 302, 303, 307, 308}:
                                location = response.headers.get("location")
                                if not location or redirect_count >= 5:
                                    raise ProjectStoreError("在线模型返回的图片地址重定向过多")
                                current_url = urllib.parse.urljoin(current_url, location)
                                continue
                            response.raise_for_status()
                            with temporary_path.open("wb") as handle:
                                async for chunk in response.aiter_bytes(1024 * 1024):
                                    total += len(chunk)
                                    if total > 1024 * 1024 * 1024:
                                        raise ProjectStoreError("在线模型返回的图片超过 1GB 限制")
                                    handle.write(chunk)
                            break
                    else:
                        raise ProjectStoreError("在线模型返回的图片地址重定向过多")
                width, height, mime_type = validate_generated_image(temporary_path)
                filename = generated_image_filename(index, mime_type)
                record = await asyncio.to_thread(
                    project_store_call, "import_media_file", project_id, temporary_path, filename, mime_type,
                    {**source_info, "name": filename, "locator": value},
                )
            finally:
                temporary_path.unlink(missing_ok=True)
        else:
            raise ProjectStoreError("在线模型没有返回可归档的图片")
    node_image = {
        "asset_id": record["asset_id"],
        "kind": "image",
        "name": record.get("name") or filename,
        "natural_w": width,
        "natural_h": height,
    }
    return node_image

async def archive_project_generated_video(project_id: str, video_data: Dict[str, Any], index: int) -> Dict[str, Any]:
    """Verify and atomically archive one online-video result as project media."""
    kind = str(video_data.get("type") or "url")
    declared_mime = str(video_data.get("mime_type") or "video/mp4")
    filename = f"generated-{index + 1}.mp4"
    source_info = {"type": "online_generation", "name": filename}
    record = None
    verified_mime = declared_mime
    if kind == "b64":
        try:
            content = base64.b64decode(str(video_data.get("value") or ""), validate=True)
        except (ValueError, TypeError) as exc:
            raise ProjectStoreError("在线模型返回了无效的视频数据") from exc
        if len(content) > PROJECT_GENERATED_VIDEO_MAX_BYTES:
            raise ProjectStoreError("在线模型返回的视频超过 1GB 限制")
        _width, _height, verified_mime = validate_generated_video(BytesIO(content))
        suffix = mimetypes.guess_extension(verified_mime) or ".mp4"
        filename = f"generated-{index + 1}{suffix}"
        source_info["name"] = filename
        record = await asyncio.to_thread(
            project_store_call, "import_media", project_id, content, filename, verified_mime, source_info
        )
    else:
        value = str(video_data.get("value") or "").strip()
        local_path = output_file_from_url(value)
        if local_path:
            _width, _height, verified_mime = validate_generated_video(local_path)
            suffix = mimetypes.guess_extension(verified_mime) or ".mp4"
            filename = f"generated-{index + 1}{suffix}"
            record = await asyncio.to_thread(
                project_store_call, "import_media_file", project_id, local_path, filename,
                verified_mime, {**source_info, "name": filename, "locator": value},
            )
        elif value.startswith(("http://", "https://")):
            descriptor, temporary_name = tempfile.mkstemp(prefix="firefly-generated-video-", suffix=".tmp")
            os.close(descriptor)
            temporary_path = Path(temporary_name)
            try:
                total = 0
                current_url = value
                timeout = httpx.Timeout(connect=20.0, read=300.0, write=60.0, pool=20.0)
                async with httpx.AsyncClient(timeout=timeout, follow_redirects=False, trust_env=False) as client:
                    for redirect_count in range(6):
                        request_url, request_headers, request_extensions = await ensure_public_generation_target(current_url, "视频")
                        async with client.stream(
                            "GET", request_url, headers=request_headers, extensions=request_extensions,
                        ) as response:
                            if response.status_code in {301, 302, 303, 307, 308}:
                                location = response.headers.get("location")
                                if not location or redirect_count >= 5:
                                    raise ProjectStoreError("在线模型返回的视频地址重定向过多")
                                current_url = urllib.parse.urljoin(current_url, location)
                                continue
                            response.raise_for_status()
                            with temporary_path.open("wb") as handle:
                                async for chunk in response.aiter_bytes(1024 * 1024):
                                    total += len(chunk)
                                    if total > PROJECT_GENERATED_VIDEO_MAX_BYTES:
                                        raise ProjectStoreError("在线模型返回的视频超过 1GB 限制")
                                    handle.write(chunk)
                            break
                    else:
                        raise ProjectStoreError("在线模型返回的视频地址重定向过多")
                _width, _height, verified_mime = validate_generated_video(temporary_path)
                suffix = mimetypes.guess_extension(verified_mime) or ".mp4"
                filename = f"generated-{index + 1}{suffix}"
                record = await asyncio.to_thread(
                    project_store_call, "import_media_file", project_id, temporary_path, filename,
                    verified_mime, {**source_info, "name": filename, "locator": value},
                )
            finally:
                temporary_path.unlink(missing_ok=True)
        else:
            raise ProjectStoreError("在线模型没有返回可归档的视频")
    return {
        "asset_id": record["asset_id"],
        "kind": "video",
        "name": record.get("name") or filename,
        "mime_type": record.get("mime_type") or verified_mime,
    }

async def run_project_image_task(
    task_id: str,
    project_id: str,
    payload: ProjectImageGenerationRequest,
    request_snapshot: ImageGenerationRequest,
    route,
    registered_revision: int,
):
    started_at = now_ms()
    generated_items = []
    preexisting_asset_ids = set()
    with CANVAS_TASK_LOCK:
        CANVAS_TASKS[task_id]["status"] = "running"
        CANVAS_TASKS[task_id]["updated_at"] = time.time()
    try:
        preexisting_asset_ids = {
            item.get("asset_id")
            for item in await asyncio.to_thread(project_store_call, "list_media", project_id)
            if item.get("asset_id")
        }
        references = await asyncio.to_thread(project_image_references, project_id, payload.reference_asset_ids)
        executed_prompt = apply_angle_preset(payload.prompt, payload.angle_preset)
        image_parameters = {
            "width": request_snapshot.width,
            "height": request_snapshot.height,
            "aspect_ratio": request_snapshot.aspect_ratio,
            "resolution": request_snapshot.resolution,
            "media_type": request_snapshot.media_type,
        }
        scope_token = PROJECT_MEDIA_ADAPTER_PROJECT.set(project_id)
        try:
            for _ in range(payload.n):
                image_data, raw = await generate_ai_image(
                    executed_prompt,
                    payload.size,
                    payload.quality,
                    route.model,
                    references,
                    route.provider_id,
                    preserve_model=True,
                    image_parameters=image_parameters,
                )
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
            raise ProjectStoreError("在线模型没有返回图片")
        timestamp = now_ms()
        history_record = {
            "id": f"generation-{uuid.uuid4().hex}",
            "type": "online-image",
            "status": "succeeded",
            "prompt": payload.prompt,
            "executed_prompt": executed_prompt,
            "angle_preset": payload.angle_preset,
            "created_at": started_at,
            "completed_at": timestamp,
            "routing_mode": payload.routing_mode,
            "requested_provider_id": payload.provider_id,
            "requested_model": payload.model,
            "actual_provider_id": route.provider_id,
            "actual_provider_name": route.provider_name,
            "actual_model": route.model,
            "route_reason": route.reason,
            "params": request_snapshot.to_dict(),
            "outputs": [{"asset_id": item["asset_id"], "kind": "image"} for item in generated_items],
            "result_node_id": payload.result_node_id,
        }
        canvas = await asyncio.to_thread(
            project_store_call,
            "complete_image_generation",
            project_id,
            payload.result_node_id,
            generated_items,
            history_record,
        )
        await asyncio.to_thread(project_store_call, "flush", project_id)
        base = f"/api/project-library/{urllib.parse.quote(project_id, safe='')}/media/"
        image_items = [
            {**item, "url": base + urllib.parse.quote(item["asset_id"], safe="")}
            for item in generated_items
        ]
        result = {
            "prompt": payload.prompt,
            "executed_prompt": executed_prompt,
            "images": [item["url"] for item in image_items],
            "image_items": image_items,
            "provider_id": route.provider_id,
            "provider_name": route.provider_name,
            "model": route.model,
            "route": route.to_dict(),
            "history_id": history_record["id"],
            "history_recorded": True,
            "history_record": history_record,
            "project_id": project_id,
            "project_revision": canvas["revision"],
            "registered_revision": registered_revision,
            "result_node_id": payload.result_node_id,
            "final_node": next((item for item in canvas.get("nodes") or [] if item.get("id") == payload.result_node_id), None),
        }
        with CANVAS_TASK_LOCK:
            CANVAS_TASKS[task_id].update({"status": "succeeded", "result": result, "error": "", "updated_at": time.time()})
        await cleanup_project_generation_orphans(project_id)
        await manager.broadcast_canvas_updated(project_id, int(canvas.get("updated_at") or now_ms()))
    except Exception as exc:
        detail = getattr(exc, "detail", None) or str(exc)
        failure_time = now_ms()
        failure_result = None
        failure_record = {
            "id": f"generation-{uuid.uuid4().hex}",
            "type": "online-image",
            "status": "failed",
            "prompt": payload.prompt,
            "executed_prompt": apply_angle_preset(payload.prompt, payload.angle_preset),
            "angle_preset": payload.angle_preset,
            "created_at": started_at,
            "completed_at": failure_time,
            "routing_mode": payload.routing_mode,
            "requested_provider_id": payload.provider_id,
            "requested_model": payload.model,
            "actual_provider_id": route.provider_id,
            "actual_provider_name": route.provider_name,
            "actual_model": route.model,
            "route_reason": route.reason,
            "params": request_snapshot.to_dict(),
            "outputs": [],
            "result_node_id": payload.result_node_id,
            "error": str(detail),
        }
        try:
            failed_canvas = await asyncio.to_thread(
                project_store_call, "fail_image_generation", project_id, payload.result_node_id, task_id, failure_record,
            )
            await asyncio.to_thread(project_store_call, "flush", project_id)
            await manager.broadcast_canvas_updated(project_id, int(failed_canvas.get("updated_at") or failure_time))
            failure_result = {
                "history_recorded": True,
                "history_record": failure_record,
                "project_id": project_id,
                "project_revision": failed_canvas["revision"],
                "registered_revision": registered_revision,
                "result_node_id": payload.result_node_id,
                "final_node": next((item for item in failed_canvas.get("nodes") or [] if item.get("id") == payload.result_node_id), None),
            }
        except Exception:
            pass
        with CANVAS_TASK_LOCK:
            orphan_ids = PROJECT_IMAGE_GENERATION_ORPHANS.setdefault(project_id, set())
            orphan_ids.update(
                item.get("asset_id") for item in generated_items
                if item.get("asset_id") and item.get("asset_id") not in preexisting_asset_ids
            )
            CANVAS_TASKS[task_id].update({
                "status": "failed",
                "error": str(detail),
                "status_code": getattr(exc, "status_code", 500),
                "result": failure_result,
                "updated_at": time.time(),
            })
        await cleanup_project_generation_orphans(project_id)

async def run_project_video_task(
    task_id: str,
    project_id: str,
    payload: ProjectVideoGenerationRequest,
    request_snapshot: VideoGenerationRequest,
    route,
    registered_revision: int,
    references: Optional[List[Dict[str, Any]]] = None,
    provider: Optional[Mapping[str, Any]] = None,
):
    started_at = now_ms()
    generated_items = []
    preexisting_asset_ids = set()
    with CANVAS_TASK_LOCK:
        CANVAS_TASKS[task_id]["status"] = "running"
        CANVAS_TASKS[task_id]["updated_at"] = time.time()
    try:
        preexisting_asset_ids = {
            item.get("asset_id")
            for item in await asyncio.to_thread(project_store_call, "list_media", project_id)
            if item.get("asset_id")
        }
        references = copy.deepcopy(references) if references is not None else await asyncio.to_thread(
            project_video_all_references, project_id, payload,
        )
        candidates, raw = await execute_project_video_generation(
            project_id, request_snapshot, route, references, provider,
        )
        for candidate in candidates:
            generated_items.append(await archive_project_generated_video(project_id, candidate, len(generated_items)))
        if not generated_items:
            raise ProjectStoreError("在线模型没有返回视频")
        completed_at = now_ms()
        history_record = {
            "id": f"generation-{uuid.uuid4().hex}",
            "type": "online-video",
            "status": "succeeded",
            "prompt": payload.prompt,
            "executed_prompt": payload.prompt,
            "created_at": started_at,
            "completed_at": completed_at,
            "routing_mode": payload.routing_mode,
            "requested_provider_id": payload.provider_id,
            "requested_model": payload.model,
            "actual_provider_id": route.provider_id,
            "actual_provider_name": route.provider_name,
            "actual_model": route.model,
            "route_reason": route.reason,
            "params": request_snapshot.to_dict(),
            "references": [
                {"asset_id": item["asset_id"], "kind": item["kind"]}
                for item in references
            ],
            "outputs": [{"asset_id": item["asset_id"], "kind": "video"} for item in generated_items],
            "result_node_id": payload.result_node_id,
        }
        canvas = await asyncio.to_thread(
            project_store_call,
            "complete_media_generation",
            project_id,
            payload.result_node_id,
            generated_items,
            history_record,
            media_kind="video",
        )
        await asyncio.to_thread(project_store_call, "flush", project_id)
        base = f"/api/project-library/{urllib.parse.quote(project_id, safe='')}/media/"
        video_items = [
            {**item, "url": base + urllib.parse.quote(item["asset_id"], safe="")}
            for item in generated_items
        ]
        result = {
            "prompt": payload.prompt,
            "videos": [item["url"] for item in video_items],
            "video_items": video_items,
            "media_items": video_items,
            "provider_id": route.provider_id,
            "provider_name": route.provider_name,
            "model": route.model,
            "route": route.to_dict(),
            "history_id": history_record["id"],
            "history_recorded": True,
            "history_record": history_record,
            "project_id": project_id,
            "project_revision": canvas["revision"],
            "registered_revision": registered_revision,
            "result_node_id": payload.result_node_id,
            "final_node": next((item for item in canvas.get("nodes") or [] if item.get("id") == payload.result_node_id), None),
            "raw": raw if isinstance(raw, dict) else {},
        }
        with CANVAS_TASK_LOCK:
            CANVAS_TASKS[task_id].update({"status": "succeeded", "result": result, "error": "", "updated_at": time.time()})
        await cleanup_project_generation_orphans(project_id)
        await manager.broadcast_canvas_updated(project_id, int(canvas.get("updated_at") or completed_at))
    except Exception as exc:
        detail = getattr(exc, "detail", None) or str(exc)
        failure_time = now_ms()
        failure_record = {
            "id": f"generation-{uuid.uuid4().hex}",
            "type": "online-video",
            "status": "failed",
            "prompt": payload.prompt,
            "executed_prompt": payload.prompt,
            "created_at": started_at,
            "completed_at": failure_time,
            "routing_mode": payload.routing_mode,
            "requested_provider_id": payload.provider_id,
            "requested_model": payload.model,
            "actual_provider_id": route.provider_id,
            "actual_provider_name": route.provider_name,
            "actual_model": route.model,
            "route_reason": route.reason,
            "params": request_snapshot.to_dict(),
            "outputs": [],
            "result_node_id": payload.result_node_id,
            "error": str(detail),
        }
        failure_result = None
        try:
            failed_canvas = await asyncio.to_thread(
                project_store_call, "fail_media_generation", project_id, payload.result_node_id, task_id, failure_record,
            )
            await asyncio.to_thread(project_store_call, "flush", project_id)
            await manager.broadcast_canvas_updated(project_id, int(failed_canvas.get("updated_at") or failure_time))
            failure_result = {
                "history_recorded": True,
                "history_record": failure_record,
                "project_id": project_id,
                "project_revision": failed_canvas["revision"],
                "registered_revision": registered_revision,
                "result_node_id": payload.result_node_id,
                "final_node": next((item for item in failed_canvas.get("nodes") or [] if item.get("id") == payload.result_node_id), None),
            }
        except Exception:
            pass
        with CANVAS_TASK_LOCK:
            orphan_ids = PROJECT_VIDEO_GENERATION_ORPHANS.setdefault(project_id, set())
            orphan_ids.update(
                item.get("asset_id")
                for item in generated_items
                if item.get("asset_id") and item.get("asset_id") not in preexisting_asset_ids
            )
            CANVAS_TASKS[task_id].update({
                "status": "failed",
                "error": str(detail),
                "status_code": getattr(exc, "status_code", 500),
                "result": failure_result,
                "updated_at": time.time(),
            })
        await cleanup_project_generation_orphans(project_id)

async def cleanup_project_generation_orphans(project_id: str):
    def cleanup():
        with project_lifecycle_lock(project_id):
            with CANVAS_TASK_LOCK:
                if any(
                    item.get("type") in {"project-online-image", "project-online-video"}
                    and item.get("project_id") == project_id
                    and item.get("status") in {"queued", "running"}
                    for item in CANVAS_TASKS.values()
                ):
                    return
                candidates = (
                    PROJECT_IMAGE_GENERATION_ORPHANS.pop(project_id, set())
                    | PROJECT_VIDEO_GENERATION_ORPHANS.pop(project_id, set())
                )
            for asset_id in candidates:
                try:
                    project_store_call("delete_media_if_unreferenced", project_id, asset_id)
                except Exception:
                    # Maintenance failure must not rewrite a committed task.
                    continue
    await asyncio.to_thread(cleanup)

def reserve_project_image_task(project_id: str, task_id: str, payload: ProjectImageGenerationRequest, route):
    with project_lifecycle_lock(project_id):
        with CANVAS_TASK_LOCK:
            expiry = time.time() - 24 * 60 * 60
            for expired_id, expired_task in list(CANVAS_TASKS.items()):
                if (
                    expired_task.get("type") == "project-online-image"
                    and expired_task.get("status") in {"succeeded", "failed"}
                    and float(expired_task.get("updated_at") or 0) < expiry
                ):
                    CANVAS_TASKS.pop(expired_id, None)
            active_tasks = [
                task for task in CANVAS_TASKS.values()
                if task.get("type") == "project-online-image" and task.get("status") in {"queued", "running"}
            ]
            project_active = [task for task in active_tasks if task.get("project_id") == project_id]
            if len(active_tasks) >= PROJECT_IMAGE_GENERATION_MAX_PENDING:
                raise HTTPException(status_code=429, detail="在线生图队列已满，请等待已有任务完成")
            if len(project_active) >= PROJECT_IMAGE_GENERATION_MAX_PER_PROJECT:
                raise HTTPException(status_code=429, detail="当前项目同时最多运行 8 个在线生图任务")
            CANVAS_TASKS[task_id] = {
                "id": task_id,
                "type": "project-online-image",
                "project_id": project_id,
                "result_node_id": payload.result_node_id,
                "status": "queued",
                "created_at": time.time(),
                "updated_at": time.time(),
                "result": None,
                "error": "",
                "provider_id": route.provider_id,
                "model": route.model,
                "route": route.to_dict(),
            }

@app.post("/api/project-library/{project_id}/image-generation-tasks")
async def create_project_image_task(project_id: str, payload: ProjectImageGenerationRequest, request: Request):
    ensure_same_origin_request(request)
    try:
        await asyncio.to_thread(project_store_call, "open_project", project_id)
        request_snapshot = project_image_request(payload)
        for asset_id in payload.reference_asset_ids:
            await asyncio.to_thread(project_store_call, "media_record", project_id, asset_id)
        route = route_image_generation(
            request_snapshot,
            load_api_providers(),
            provider_id=payload.provider_id if payload.routing_mode == "manual" else "",
            model=payload.model if payload.routing_mode == "manual" else "",
        )
        apply_angle_preset(payload.prompt, payload.angle_preset)
    except ModelRoutingError as exc:
        raise HTTPException(status_code=400, detail=exc.to_dict()) from exc
    except ProjectNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except (ProjectStoreError, ValueError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    task_id = f"project_img_{uuid.uuid4().hex}"
    reserve_project_image_task(project_id, task_id, payload, route)
    try:
        registered_canvas = await asyncio.to_thread(
            project_store_call,
            "begin_image_generation",
            project_id,
            payload.result_node_id,
            {
                "taskId": task_id,
                "kind": "image",
                "count": payload.n,
                "providerId": route.provider_id,
                "model": route.model,
                "referenceAssetIds": list(payload.reference_asset_ids),
            },
            base_revision=payload.base_revision,
        )
        await asyncio.to_thread(project_store_call, "flush", project_id)
    except RevisionConflict as exc:
        with CANVAS_TASK_LOCK:
            CANVAS_TASKS.pop(task_id, None)
        await cleanup_project_generation_orphans(project_id)
        raise HTTPException(status_code=409, detail={"message": str(exc), "canvas": exc.canvas}) from exc
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
        future = current_loop.create_task(run_project_image_task(
            task_id, project_id, payload, request_snapshot, route, registered_canvas["revision"],
        ))
    else:
        executor = ensure_project_image_generation_executor()
        future = executor.submit(asyncio.run, run_project_image_task(
            task_id, project_id, payload, request_snapshot, route, registered_canvas["revision"],
        ))
    with CANVAS_TASK_LOCK:
        PROJECT_IMAGE_GENERATION_FUTURES[task_id] = future
    def forget_project_image_future(done_future: Future):
        with CANVAS_TASK_LOCK:
            if PROJECT_IMAGE_GENERATION_FUTURES.get(task_id) is done_future:
                PROJECT_IMAGE_GENERATION_FUTURES.pop(task_id, None)
    future.add_done_callback(forget_project_image_future)
    return JSONResponse(status_code=202, content={
        "task_id": task_id,
        # Legacy project image tasks do not have an Agent task mapping.  The
        # task id is their stable client-visible key; referencing the Agent
        # worker's local ``task`` variable here caused every successful
        # admission to fail with NameError before the response was returned.
        "idempotency_key": task_id,
        "status": "queued",
        "route": route.to_dict(),
        "project_revision": registered_canvas["revision"],
        "registered_node": next((item for item in registered_canvas.get("nodes") or [] if item.get("id") == payload.result_node_id), None),
    })

def reserve_project_video_task(project_id: str, task_id: str, payload: ProjectVideoGenerationRequest, route):
    with project_lifecycle_lock(project_id):
        with CANVAS_TASK_LOCK:
            expiry = time.time() - 24 * 60 * 60
            for expired_id, expired_task in list(CANVAS_TASKS.items()):
                if (
                    expired_task.get("type") == "project-online-video"
                    and expired_task.get("status") in {"succeeded", "failed"}
                    and float(expired_task.get("updated_at") or 0) < expiry
                ):
                    CANVAS_TASKS.pop(expired_id, None)
            active_tasks = [
                task for task in CANVAS_TASKS.values()
                if task.get("type") == "project-online-video" and task.get("status") in {"queued", "running"}
            ]
            project_active = [task for task in active_tasks if task.get("project_id") == project_id]
            if len(active_tasks) >= PROJECT_IMAGE_GENERATION_MAX_PENDING:
                raise HTTPException(status_code=429, detail="在线视频队列已满，请等待已有任务完成")
            if len(project_active) >= PROJECT_IMAGE_GENERATION_MAX_PER_PROJECT:
                raise HTTPException(status_code=429, detail="当前项目同时最多运行 8 个在线视频任务")
            CANVAS_TASKS[task_id] = {
                "id": task_id,
                "type": "project-online-video",
                "kind": "video",
                "project_id": project_id,
                "result_node_id": payload.result_node_id,
                "status": "queued",
                "created_at": time.time(),
                "updated_at": time.time(),
                "result": None,
                "error": "",
                "provider_id": route.provider_id,
                "model": route.model,
                "route": route.to_dict(),
            }

@app.post("/api/project-library/{project_id}/video-generation-tasks")
async def create_project_video_task(project_id: str, payload: ProjectVideoGenerationRequest, request: Request):
    ensure_same_origin_request(request)
    try:
        await asyncio.to_thread(project_store_call, "open_project", project_id)
        request_snapshot = project_video_request(payload)
        references = await asyncio.to_thread(project_video_all_references, project_id, payload)
        providers = await preflight_project_video_providers(
            request_snapshot,
            references,
            load_api_providers(),
            provider_id=payload.provider_id if payload.routing_mode == "manual" else "",
            model=payload.model if payload.routing_mode == "manual" else "",
        )
        route = route_video_generation(
            request_snapshot,
            providers,
            provider_id=payload.provider_id if payload.routing_mode == "manual" else "",
            model=payload.model if payload.routing_mode == "manual" else "",
            adapter_incompatible_parameters=functools.partial(
                project_video_adapter_incompatible_parameters,
                references=references,
            ),
        )
        provider_snapshot = copy.deepcopy(next(
            provider for provider in providers
            if str(provider.get("id") or "").strip().lower() == route.provider_id
        ))
        if is_runninghub_provider(provider_snapshot):
            runninghub_api_key(provider_snapshot, use_wallet=True)
    except ModelRoutingError as exc:
        raise HTTPException(status_code=400, detail=exc.to_dict()) from exc
    except ProjectNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except (ProjectStoreError, ValueError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    task_id = f"project_vid_{uuid.uuid4().hex}"
    reserve_project_video_task(project_id, task_id, payload, route)
    try:
        registered_canvas = await asyncio.to_thread(
            project_store_call,
            "begin_media_generation",
            project_id,
            payload.result_node_id,
            {
                "taskId": task_id,
                "kind": "video",
                "count": payload.n,
                "providerId": route.provider_id,
                "model": route.model,
                "referenceAssetIds": list(dict.fromkeys([
                    *payload.image_reference_asset_ids,
                    *payload.video_reference_asset_ids,
                    *payload.audio_reference_asset_ids,
                ])),
            },
            base_revision=payload.base_revision,
            media_kind="video",
        )
        await asyncio.to_thread(project_store_call, "flush", project_id)
    except RevisionConflict as exc:
        with CANVAS_TASK_LOCK:
            CANVAS_TASKS.pop(task_id, None)
        await cleanup_project_generation_orphans(project_id)
        raise HTTPException(status_code=409, detail={"message": str(exc), "canvas": exc.canvas}) from exc
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
        future = current_loop.create_task(run_project_video_task(
            task_id, project_id, payload, request_snapshot, route, registered_canvas["revision"],
            copy.deepcopy(references), provider_snapshot,
        ))
    else:
        executor = ensure_project_image_generation_executor()
        future = executor.submit(asyncio.run, run_project_video_task(
            task_id, project_id, payload, request_snapshot, route, registered_canvas["revision"],
            copy.deepcopy(references), provider_snapshot,
        ))
    with CANVAS_TASK_LOCK:
        PROJECT_VIDEO_GENERATION_FUTURES[task_id] = future
    def forget_project_video_future(done_future: Future):
        with CANVAS_TASK_LOCK:
            if PROJECT_VIDEO_GENERATION_FUTURES.get(task_id) is done_future:
                PROJECT_VIDEO_GENERATION_FUTURES.pop(task_id, None)
    future.add_done_callback(forget_project_video_future)
    return JSONResponse(status_code=202, content={
        "task_id": task_id,
        "status": "queued",
        "kind": "video",
        "route": route.to_dict(),
        "project_revision": registered_canvas["revision"],
        "registered_node": next((item for item in registered_canvas.get("nodes") or [] if item.get("id") == payload.result_node_id), None),
    })

@app.post("/api/online-image")
async def online_image(payload: OnlineImageRequest):
    return await build_online_image_result(payload)

@app.post("/api/image-task-query")
async def query_image_task(payload: ImageTaskQueryRequest):
    provider = get_api_provider(payload.provider_id)
    task_id = str(payload.task_id or "").strip()
    if is_runninghub_provider(provider):
        api_key = runninghub_api_key(provider)
        url = runninghub_endpoint_url(provider, "/task/openapi/outputs")
        try:
            async with httpx.AsyncClient(timeout=httpx.Timeout(connect=20.0, read=240.0, write=30.0, pool=20.0)) as client:
                response = await client.post(url, headers=runninghub_app_headers(True), json={"apiKey": api_key, "taskId": task_id})
                response.raise_for_status()
                raw = response.json()
                code = raw.get("code") if isinstance(raw, dict) else None
                if code in (0, "0"):
                    local_urls = []
                    local_items = []
                    for remote in runninghub_extract_outputs(raw.get("data")):
                        try:
                            local_url = await runninghub_store_remote_output(client, remote)
                        except Exception:
                            local_url = rewrite_runninghub_file_url(remote)
                        if local_url:
                            local_urls.append(local_url)
                            local_items.append(image_output_meta(local_url))
                    result = {
                        "status": "succeeded",
                        "prompt": "",
                        "images": local_urls,
                        "image_items": local_items,
                        "timestamp": time.time(),
                        "type": "online",
                        "model": "",
                        "provider_id": provider["id"],
                        "provider_name": provider.get("name") or provider["id"],
                        "task_id": task_id,
                        "request_id": "",
                        "params": {"provider_id": provider["id"]},
                        "raw": raw,
                    }
                    save_to_history(result)
                    if GLOBAL_LOOP:
                        asyncio.run_coroutine_threadsafe(manager.broadcast_new_image(result), GLOBAL_LOOP)
                    return result
                if code in (805, "805"):
                    return {
                        "status": "failed",
                        "task_id": task_id,
                        "provider_id": provider["id"],
                        "provider_name": provider.get("name") or provider["id"],
                        "error": runninghub_fail_reason(raw),
                        "raw": raw,
                    }
                return {
                    "status": "running",
                    "task_id": task_id,
                    "provider_id": provider["id"],
                    "provider_name": provider.get("name") or provider["id"],
                    "message": "RunningHub 任务仍在生成中",
                    "raw": raw,
                }
        except httpx.HTTPStatusError as exc:
            text = exc.response.text or ""
            raise HTTPException(status_code=exc.response.status_code, detail=f"查询 RunningHub 任务失败：{text[:300]}") from exc
        except httpx.HTTPError as exc:
            raise HTTPException(status_code=502, detail=f"查询 RunningHub 任务失败：{exc}") from exc
    timeout = httpx.Timeout(connect=20.0, read=300.0, write=60.0, pool=20.0)
    try:
        async with httpx.AsyncClient(timeout=timeout, follow_redirects=True) as client:
            raw = await fetch_image_task_payload(client, task_id, provider)
    except httpx.HTTPStatusError as exc:
        log_net_error(f"查询生图任务 HTTP状态错误 provider={provider.get('id')} task_id={task_id}", exc)
        text = exc.response.text or ""
        raise HTTPException(status_code=exc.response.status_code, detail=f"查询上游生图任务失败：{text[:300]}") from exc
    except httpx.HTTPError as exc:
        log_net_error(f"查询生图任务 网络/TLS错误 provider={provider.get('id')} task_id={task_id}", exc)
        raise HTTPException(status_code=502, detail=f"查询上游生图任务失败：{exc}") from exc

    status = image_task_status(raw)
    image_items = []
    try:
        image_items = extract_images(raw)
    except HTTPException:
        image_items = []
    if image_items:
        local_urls = []
        local_items = []
        for item in image_items:
            local_url = await save_ai_image_to_output(item, prefix="online_")
            if local_url:
                local_urls.append(local_url)
                local_items.append(image_output_meta(local_url, item))
        result = {
            "status": "succeeded",
            "prompt": "",
            "images": local_urls,
            "image_items": local_items,
            "timestamp": time.time(),
            "type": "online",
            "model": "",
            "provider_id": provider["id"],
            "provider_name": provider.get("name") or provider["id"],
            "task_id": task_id,
            "request_id": raw.get("id") if isinstance(raw, dict) else "",
            "params": {"provider_id": provider["id"]},
            "raw": raw,
        }
        save_to_history(result)
        if GLOBAL_LOOP:
            asyncio.run_coroutine_threadsafe(manager.broadcast_new_image(result), GLOBAL_LOOP)
        return result
    if status in IMAGE_TASK_FAILED_STATUSES:
        return {
            "status": "failed",
            "task_id": task_id,
            "provider_id": provider["id"],
            "provider_name": provider.get("name") or provider["id"],
            "error": image_task_fail_reason(raw),
            "raw": raw,
        }
    return {
        "status": "running",
        "task_id": task_id,
        "provider_id": provider["id"],
        "provider_name": provider.get("name") or provider["id"],
        "message": "任务仍在生成中",
        "raw": raw,
    }

async def run_canvas_image_task(task_id: str, payload: OnlineImageRequest):
    with CANVAS_TASK_LOCK:
        if task_id in CANVAS_TASKS:
            CANVAS_TASKS[task_id]["status"] = "running"
            CANVAS_TASKS[task_id]["updated_at"] = time.time()
    try:
        result = await build_online_image_result(payload)
        with CANVAS_TASK_LOCK:
            CANVAS_TASKS[task_id].update({
                "status": "succeeded",
                "result": result,
                "error": "",
                "updated_at": time.time(),
            })
    except JimengPendingError as exc:
        # 即梦云端还在排队：标记为 jimeng_pending，前端据 submit_id 持久续查（任务未丢失）
        info = jimeng_pending_payload(exc)
        with CANVAS_TASK_LOCK:
            CANVAS_TASKS[task_id].update({
                "status": "jimeng_pending",
                "jimeng_pending": True,
                "submit_id": exc.submit_id,
                "kind": exc.kind,
                "queue_info": exc.queue_info,
                "message": info["message"],
                "error": "",
                "updated_at": time.time(),
            })
    except Exception as exc:
        detail = getattr(exc, "detail", None) or str(exc)
        status_code = getattr(exc, "status_code", 500)
        upstream_task_id = getattr(exc, "upstream_task_id", "") or extract_task_id_from_text(detail)
        with CANVAS_TASK_LOCK:
            CANVAS_TASKS[task_id].update({
                "status": "failed",
                "error": str(detail),
                "status_code": status_code,
                "upstream_task_id": upstream_task_id,
                "updated_at": time.time(),
            })

@app.post("/api/canvas-image-tasks")
async def create_canvas_image_task(payload: OnlineImageRequest):
    require_online_image_project_media_scope(payload)
    task_id = f"canvas_img_{uuid.uuid4().hex}"
    with CANVAS_TASK_LOCK:
        CANVAS_TASKS[task_id] = {
            "id": task_id,
            "type": "online-image",
            "status": "queued",
            "created_at": time.time(),
            "updated_at": time.time(),
            "result": None,
            "error": "",
            "provider_id": payload.provider_id,
            "model": payload.model,
        }
    asyncio.create_task(run_canvas_image_task(task_id, payload))
    return {"task_id": task_id, "status": "queued"}

@app.get("/api/canvas-image-tasks/{task_id}")
async def get_canvas_image_task(task_id: str):
    with CANVAS_TASK_LOCK:
        task = dict(CANVAS_TASKS.get(task_id) or {})
    if not task:
        raise HTTPException(status_code=404, detail="画布任务不存在，可能服务已重启或任务已过期")
    return task

# 本地任务创建与轮询已删除；画布只使用在线生成适配器。


# --- 图像生成参数 schema（供客户端动态渲染参数表单，避免把参数写死在前端） ---
IMAGE_PARAM_RATIOS = [
    {"value": "1:1", "label": "1:1"},
    {"value": "3:4", "label": "3:4"},
    {"value": "4:3", "label": "4:3"},
    {"value": "16:9", "label": "16:9"},
    {"value": "9:16", "label": "9:16"},
    {"value": "2:3", "label": "2:3"},
    {"value": "3:2", "label": "3:2"},
]
IMAGE_PARAM_RESOLUTIONS = [
    {"value": "1k", "label": "1K"},
    {"value": "2k", "label": "2K"},
    {"value": "4k", "label": "4K"},
]

def build_image_param_fields(engine: str, provider: dict, model: str):
    """返回某平台/引擎的图像生成参数字段定义。客户端按 type 动态渲染并回填到生成请求。
    字段 key 直接对应 OnlineImageRequest 的字段名（size/quality/n/reference_images）。"""
    gpt_auto_size = engine == "api" and is_gpt_image_2_model(model)
    image_resolutions = ([{"value": "auto", "label": "自动"}] + IMAGE_PARAM_RESOLUTIONS) if gpt_auto_size else IMAGE_PARAM_RESOLUTIONS
    size_field = {
        "key": "size", "type": "size", "label": "尺寸",
        "ratios": IMAGE_PARAM_RATIOS, "resolutions": image_resolutions,
        "default": {"ratio": "1:1", "resolution": "auto" if gpt_auto_size else "1k"},
    }
    count_field = {
        "key": "n", "type": "int", "label": "数量", "control": "chips",
        "options": [1, 2, 3, 4], "default": 1,
    }
    refs_field = {"key": "reference_images", "type": "refs", "label": "参考图", "max": ONLINE_IMAGE_REFERENCE_MAX}

    if engine == "runninghub":
        # RunningHub 应用字段由应用信息接口动态提供；这里保留通用尺寸/参考图字段。
        return [size_field, count_field, refs_field]

    fields = [size_field]
    if engine in ("api", "volcengine"):
        fields.append({
            "key": "quality", "type": "select", "label": "质量", "control": "chips",
            "options": [
                {"value": "auto", "label": "自动"},
                {"value": "low", "label": "低"},
                {"value": "medium", "label": "中"},
                {"value": "high", "label": "高"},
            ],
            "default": "auto",
        })
    fields.append(count_field)
    fields.append(refs_field)
    return fields

@app.get("/api/image-params")
async def image_params(provider_id: str = "", model: str = ""):
    providers = load_api_providers()
    provider = next((p for p in providers if p.get("id") == (provider_id or "").strip().lower()), None) or {}
    if is_runninghub_provider(provider):
        engine = "runninghub"
    elif (provider_id or "").strip().lower() == "modelscope":
        engine = "modelscope"
    elif is_volcengine_provider(provider):
        engine = "volcengine"
    else:
        engine = "api"
    return {
        "engine": engine,
        "submit": "/api/canvas-image-tasks",
        "fields": build_image_param_fields(engine, provider, model),
    }

# --- Canvas Video ---

VIDEO_URL_KEYS = (
    "url", "video_url", "videoUrl", "mp4_url", "mp4Url",
    "output", "output_url", "outputUrl", "download_url", "downloadUrl",
    "video", "src", "uri", "preview_url", "previewUrl", "path",
    "last_frame_url", "lastFrameUrl", "remixed_from_video_id",
)

def _collect_video_url(value, urls):
    if not value:
        return
    if isinstance(value, str):
        if value.startswith("http://") or value.startswith("https://") or value.startswith("/output/") or value.startswith("/assets/"):
            urls.append(value)
        return
    if isinstance(value, list):
        for item in value:
            _collect_video_url(item, urls)
        return
    if isinstance(value, dict):
        for key in ("videos", "outputs", "data", "detail", "result", "results", "content"):
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
    data = raw.get("data")
    detail = raw.get("detail")
    content = raw.get("content")
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
        result = node.get("result") if isinstance(node, dict) else None
        results = node.get("results") if isinstance(node, dict) else None
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
        for key in ("videos", "outputs", "results", "content"):
            value = node.get(key)
            if value:
                _collect_video_url(value, urls)
        for key in VIDEO_URL_KEYS:
            if key in node:
                _collect_video_url(node.get(key), urls)
    deduped = []
    for url in urls:
        if isinstance(url, str) and url and url not in deduped:
            deduped.append(url)
    return deduped

def video_api_root(provider):
    base_url = (provider.get("base_url") or AI_BASE_URL).rstrip("/")
    if is_volcengine_provider(provider):
        if base_url.endswith("/api/v3"):
            base_url = base_url[: -len("/api/v3")]
        return base_url
    if base_url.endswith("/v1") or base_url.endswith("/v2"):
        base_url = base_url.rsplit("/", 1)[0]
    return base_url

def looks_like_html_response(text: str) -> bool:
    sample = str(text or "").lstrip()[:200].lower()
    return sample.startswith("<!doctype html") or sample.startswith("<html") or "<head" in sample

def video_submit_url_candidates(provider, base_url):
    if str(provider.get("video_generation_endpoint") or "").strip():
        return [provider_endpoint_url(provider, "video_generation_endpoint", "/v1/videos/generations")]
    if is_agnes_provider(provider):
        return [f"{base_url}/v1/videos"]
    if is_lingjing_provider(provider):
        return [f"{base_url}/v1/videos"]
    if is_apimart_provider(provider):
        return [f"{base_url}/videos/generations" if base_url.endswith("/v1") else f"{base_url}/v1/videos/generations"]
    if is_volcengine_provider(provider):
        parsed = urllib.parse.urlparse(base_url)
        if parsed.path and parsed.path.rstrip("/"):
            return [base_url]
        return [f"{base_url}/api/v3/contents/generations/tasks"]
    if is_yuli_provider(provider):
        return [f"{base_url}/v1/video/create"]
    return [f"{base_url}/v1/videos/generations", f"{base_url}/v2/videos/generations"]

def video_task_url_candidates(provider, base_url, task_id, submit_url=""):
    if is_agnes_provider(provider):
        quoted_id = urllib.parse.quote(str(task_id), safe="")
        return [
            f"{base_url}/agnesapi?{urllib.parse.urlencode({'video_id': task_id})}",
            f"{base_url}/v1/videos/{quoted_id}",
        ]
    if is_lingjing_provider(provider):
        quoted_id = urllib.parse.quote(str(task_id), safe="")
        return [
            f"{base_url}/v1/videos/{quoted_id}",
            f"{base_url}/v1/video/query?{urllib.parse.urlencode({'id': task_id})}",
        ]
    if is_apimart_provider(provider):
        task_path = f"{base_url}/tasks/{task_id}" if base_url.endswith("/v1") else f"{base_url}/v1/tasks/{task_id}"
        return [f"{task_path}?language=zh"]
    if is_volcengine_provider(provider):
        parsed = urllib.parse.urlparse(base_url)
        if parsed.path and parsed.path.rstrip("/"):
            return [f"{base_url}/{task_id}"]
        return [f"{base_url}/api/v3/contents/generations/tasks/{task_id}"]
    if is_yuli_provider(provider):
        # 玉玉API 两种视频格式：OpenAI（/v1/videos/{id}）与原生（/v1/video/query?id=）。
        # 两个都试，谁返回成功就用谁，兼容 veo OpenAI 路径与 doubao 原生路径。
        return [f"{base_url}/v1/videos/{task_id}", f"{base_url}/v1/video/query?id={task_id}"]
    v1_task = f"{base_url}/v1/videos/generations/{task_id}"
    v1_generic_task = f"{base_url}/v1/tasks/{task_id}"
    v2_task = f"{base_url}/v2/videos/generations/{task_id}"
    if "/v2/videos/generations" in str(submit_url or ""):
        return [v2_task, v1_task, v1_generic_task]
    return [v1_task, v1_generic_task, v2_task]

VIDEO_TASK_SUCCESS_STATUSES = {
    "SUCCESS", "SUCCEED", "SUCCEEDED", "COMPLETED", "COMPLETE",
    "DONE", "FINISHED", "FINISH", "OK", "READY",
}
VIDEO_TASK_FAILURE_STATUSES = {
    "FAILURE", "FAILED", "FAIL", "ERROR", "ERRORED",
    "CANCELED", "CANCELLED", "TIMEOUT", "TIMEDOUT", "REJECTED", "EXPIRED",
}

def humanize_video_task_failure(reason) -> str:
    """把上游视频任务的失败原因转成对用户友好的中文提示。
    目前主要处理 veo（Google）的内容安全过滤码。"""
    text = str(reason or "").strip()
    upper = text.upper()
    # veo 知名人物/真人面孔过滤
    if "PROMINENT_PEOPLE_FILTER" in upper or "PROMINENT_PEOPLE" in upper:
        return (
            "视频生成被上游内容安全策略拦截：检测到提示词或参考图里包含知名人物 / 真人面孔"
            f"（错误码：{text}）。\n\n"
            "这不是代码错误，而是 veo（Google）的内容审核规则——它会拒绝生成涉及真实/知名人物的视频。\n\n"
            "建议这样处理：\n"
            "  1. 去掉提示词里的人名、明星、公众人物等指向具体真人的描述；\n"
            "  2. 换用非真人参考图，例如插画、AI 头像、卡通形象、商品图、场景图；\n"
            "  3. 如果用了真人照片做参考图，先做模糊/遮挡/转成明显的二次元插画风，或干脆只用文字提示词测试。"
        )
    # veo 其它常见安全过滤
    if "SAFETY" in upper or "CONTENT_FILTER" in upper or "POLICY" in upper:
        return (
            "视频生成被上游内容安全策略拦截"
            f"（错误码：{text}）。\n\n"
            "这是 veo 的内容审核规则，提示词或参考图触发了安全过滤。\n"
            "请调整提示词/参考图后重试，避免涉及真人、暴力、敏感或受限内容。"
        )
    return f"视频生成任务失败：{text}"

async def wait_for_video_task(client, provider, task_id, submit_url=""):
    base_url = video_api_root(provider)
    if not base_url:
        raise HTTPException(status_code=400, detail=f"{provider.get('name') or provider['id']} 未配置 Base URL")
    task_urls = video_task_url_candidates(provider, base_url, task_id, submit_url)
    deadline = time.monotonic() + VIDEO_POLL_TIMEOUT
    delay = max(2.0, IMAGE_POLL_INTERVAL)
    last_payload = {}
    while time.monotonic() < deadline:
        await asyncio.sleep(delay)
        raw = None
        last_error = None
        for task_url in task_urls:
            try:
                response = await client.get(task_url, headers=api_headers(provider=provider))
                response.raise_for_status()
                raw = response.json()
                break
            except Exception as exc:
                last_error = exc
                continue
        if raw is None:
            if last_error:
                raise last_error
            raise HTTPException(status_code=502, detail=f"视频任务查询失败：{task_id}")
        last_payload = raw
        task_data = (
            raw.get("data") if isinstance(raw.get("data"), dict)
            else raw.get("detail") if isinstance(raw.get("detail"), dict)
            else raw
        )
        status = str(task_data.get("status") or task_data.get("task_status") or raw.get("status") or raw.get("task_status") or "").upper()
        if status in VIDEO_TASK_SUCCESS_STATUSES:
            return raw
        # 部分上游（如玉玉API）status 字段非标准或为空，但已经返回了视频 URL ——
        # 只要不是明确的失败状态，且拿到了真实视频地址，就直接当成功处理。
        if status not in VIDEO_TASK_FAILURE_STATUSES and video_output_urls(raw):
            return raw
        if status in VIDEO_TASK_FAILURE_STATUSES:
            error = task_data.get("error") if isinstance(task_data.get("error"), dict) else {}
            reason = task_data.get("fail_reason") or task_data.get("message") or error.get("message") or raw.get("error") or raw.get("message") or str(raw)
            raise HTTPException(status_code=502, detail=humanize_video_task_failure(reason))
        delay = min(delay * 1.6, 12)
    raise HTTPException(status_code=504, detail=f"视频生成任务超时：{last_payload or task_id}")

def apimart_video_size(size):
    value = str(size or "16:9").strip()
    if value == "keep_ratio":
        return "adaptive"
    allowed = {"16:9", "9:16", "1:1", "4:3", "3:4", "21:9", "adaptive"}
    return value if value in allowed else "16:9"

def agnes_video_dimensions(aspect_ratio="", resolution="", width=None, height=None):
    if width is not None and height is not None:
        return int(width), int(height)
    ratio = str(aspect_ratio or "16:9").strip()
    width, height = {
        "16:9": (1152, 648),
        "9:16": (648, 1152),
        "4:3": (1024, 768),
        "3:4": (768, 1024),
        "1:1": (768, 768),
        "21:9": (1280, 544),
        "9:21": (544, 1280),
    }.get(ratio, (1152, 768))
    scale = {"480p": 0.625, "720p": 1.0, "780p": 1.0, "1080p": 1.5}.get(str(resolution or "").strip().lower(), 1.0)
    width = max(64, int(round(width * scale / 8) * 8))
    height = max(64, int(round(height * scale / 8) * 8))
    return width, height

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
    return min(441, max(9, 8 * n + 1)), frame_rate

async def agnes_video_image_url(ref):
    url = str(getattr(ref, "url", "") or "").strip()
    if not url:
        return ""
    if url.startswith("http://") or url.startswith("https://"):
        return url
    uploaded = await upload_local_video_to_cloud(url, "auto")
    return uploaded.get("url") or ""

async def wait_for_agnes_video_task(client, provider, video_id, model):
    base_url = video_api_root(provider)
    if not base_url:
        raise HTTPException(status_code=400, detail=f"{provider.get('name') or provider['id']} 未配置 Base URL")
    query_url = f"{base_url}/agnesapi?{urllib.parse.urlencode({'video_id': video_id, 'model_name': model})}"
    legacy_url = f"{base_url}/v1/videos/{urllib.parse.quote(str(video_id), safe='')}"
    deadline = time.monotonic() + VIDEO_POLL_TIMEOUT
    delay = 5.0
    last_payload = {}
    while time.monotonic() < deadline:
        await asyncio.sleep(delay)
        raw = None
        last_error = None
        for url in (query_url, legacy_url):
            try:
                response = await client.get(url, headers=api_headers(provider=provider, model=model))
                response.raise_for_status()
                raw = response.json()
                break
            except Exception as exc:
                last_error = exc
        if raw is None:
            if last_error:
                raise last_error
            raise HTTPException(status_code=502, detail=f"Agnes 视频任务查询失败：{video_id}")
        last_payload = raw
        task_data = raw.get("data") if isinstance(raw.get("data"), dict) else raw
        status = str(task_data.get("status") or raw.get("status") or "").upper()
        if status in VIDEO_TASK_SUCCESS_STATUSES or video_output_urls(raw):
            return raw
        if status in VIDEO_TASK_FAILURE_STATUSES:
            error = task_data.get("error") if isinstance(task_data.get("error"), dict) else {}
            reason = task_data.get("message") or error.get("message") or raw.get("error") or raw.get("message") or str(raw)
            raise HTTPException(status_code=502, detail=humanize_video_task_failure(reason))
        delay = min(delay * 1.35, 12)
    raise HTTPException(status_code=504, detail=f"Agnes 视频生成任务超时：{last_payload or video_id}")

async def generate_agnes_video(client, payload, provider, base_url, requested_model):
    model = selected_model(requested_model, "agnes-video-v2.0")
    width, height = agnes_video_dimensions(payload.aspect_ratio, payload.resolution, payload.width, payload.height)
    num_frames, frame_rate = agnes_video_frame_count(payload.duration, 24)
    body = {
        "model": model,
        "prompt": str(payload.prompt or ""),
        "width": width,
        "height": height,
        "num_frames": num_frames,
        "frame_rate": frame_rate,
    }
    image_urls = []
    image_roles = []
    for ref in (payload.images or [])[:4]:
        url = await agnes_video_image_url(ref)
        if url:
            image_urls.append(url)
            image_roles.append(str(getattr(ref, "role", "") or "").strip().lower())
    if len(image_urls) == 1:
        body["image"] = image_urls[0]
    elif len(image_urls) > 1:
        body["extra_body"] = {"image": image_urls}
        has_frame_roles = any(role in {"first_frame", "last_frame"} for role in image_roles)
        if payload.multimodal or has_frame_roles:
            body["extra_body"]["mode"] = "keyframes"
    if payload.seed is not None:
        body["seed"] = payload.seed
    submit_url = f"{base_url}/v1/videos"
    response = await client.post(submit_url, headers=api_headers(provider=provider, model=model), json=body)
    response.raise_for_status()
    raw = response.json()
    video_id = str(raw.get("video_id") or "").strip()
    task_id = str(raw.get("task_id") or raw.get("id") or "").strip()
    result = raw
    if video_id and not video_output_urls(raw):
        result = await wait_for_agnes_video_task(client, provider, video_id, model)
    elif task_id and not video_output_urls(raw):
        result = await wait_for_video_task(client, provider, task_id, submit_url)
    urls = video_output_urls(result)
    if not urls:
        raise HTTPException(status_code=502, detail=f"Agnes 视频生成成功但没有返回视频：{result}")
    local_urls = [await save_remote_video_to_output(url) for url in urls]
    return {"videos": local_urls, "task_id": task_id or video_id, "video_id": video_id or None, "raw": result}

# ---- 玉玉API（yuli.host）OpenAI 视频格式：/v1/videos（multipart，支持 seconds 时长）----
def _yuli_model_norm(model: str) -> str:
    return str(model or "").strip().lower().replace("_", "").replace(".", "").replace("-", "")

def yuli_is_veo_openai_model(model: str) -> bool:
    # OpenAI multipart 格式当前只支持 veo_3_1 和 veo_3_1-fast
    return _yuli_model_norm(model) in {"veo31", "veo31fast"}

def yuli_openai_model_name(model: str) -> str:
    return "veo_3_1-fast" if _yuli_model_norm(model) == "veo31fast" else "veo_3_1"

def yuli_openai_size(aspect_ratio: str) -> str:
    value = str(aspect_ratio or "").strip()
    if value == "9:16":
        return "9x16"
    return "16x9"

def lingjing_openai_video_model(model: str) -> str:
    value = str(model or "").strip() or "veo_3_1-fast"
    lower = value.lower()
    if lower.startswith("veo3.1"):
        value = "veo_3_1" + value[len("veo3.1"):]
    elif lower.startswith("veo3_1"):
        value = "veo_3_1" + value[len("veo3_1"):]
    if value.lower().endswith("-4k"):
        value = value[:-2] + "4K"
    return value

def yuli_video_seconds(duration) -> str:
    try:
        value = int(duration)
    except Exception:
        value = 8
    if value <= 0:
        value = 8
    return str(value)

async def yuli_fetch_reference_bytes(client, ref_url):
    """把参考图（input_reference 垫图）取成 (filename, bytes, mime)，
    支持 /output、/assets 本地文件、data URL、http(s) URL。失败返回 None。"""
    ref_url = str(ref_url or "").strip()
    if not ref_url:
        return None
    if ref_url.startswith("data:"):
        header, _, b64 = ref_url.partition(",")
        mime = (header[5:].split(";")[0] or "image/png").strip()
        try:
            raw = base64.b64decode(b64)
        except Exception:
            return None
        ext = (mime.split("/")[-1] or "png").split("+")[0]
        return (f"input_reference.{ext}", raw, mime)
    path = local_media_adapter_path(ref_url, "image")
    if path:
        try:
            with open(path, "rb") as f:
                raw = f.read()
        except Exception:
            return None
        mime = content_type_for_path(path)
        return (os.path.basename(path) or "input_reference", raw, mime)
    if ref_url.startswith("http://") or ref_url.startswith("https://"):
        try:
            resp = await client.get(ref_url)
            resp.raise_for_status()
            raw = resp.content
        except Exception:
            return None
        mime = (resp.headers.get("content-type") or "image/png").split(";")[0].strip()
        ext = (mime.split("/")[-1] or "png").split("+")[0]
        return (f"input_reference.{ext}", raw, mime)
    return None

async def generate_lingjing_openai_video(client, payload, provider, base_url, requested_model):
    """灵境 API OpenAI 视频格式：POST /v1/videos，参考图走 multipart input_reference。"""
    submit_url = f"{base_url}/v1/videos"
    data = {
        "model": lingjing_openai_video_model(selected_model(requested_model, "veo_3_1-fast")),
        "prompt": str(payload.prompt or ""),
        "seconds": yuli_video_seconds(payload.duration),
        "size": yuli_openai_size(payload.aspect_ratio or payload.size),
        "watermark": "true" if payload.watermark else "false",
    }
    files = []
    for ref in (payload.images or [])[:3]:
        ref_file = await yuli_fetch_reference_bytes(client, getattr(ref, "url", ""))
        if ref_file:
            files.append(("input_reference", ref_file))
    headers = api_headers(json_body=False, provider=provider)
    if files:
        response = await client.post(submit_url, headers=headers, data=data, files=files)
    else:
        multipart_fields = [(key, (None, value)) for key, value in data.items()]
        response = await client.post(submit_url, headers=headers, files=multipart_fields)
    response.raise_for_status()
    try:
        raw = response.json()
    except Exception as exc:
        resp_text = (response.text or "")[:500]
        raise HTTPException(status_code=502, detail=f"灵境 API 视频接口返回非 JSON 响应（状态 {response.status_code}）：{resp_text}") from exc
    task_id = str(raw.get("id") or extract_task_id(raw) or raw.get("task_id") or "").strip()
    result = raw
    if task_id and not video_output_urls(raw):
        result = await wait_for_video_task(client, provider, task_id, submit_url)
    urls = video_output_urls(result)
    if not urls:
        raise HTTPException(status_code=502, detail=f"灵境 API 视频生成成功但没有返回视频：{result}")
    local_urls = [await save_remote_video_to_output(url) for url in urls]
    return {"videos": local_urls, "task_id": task_id, "raw": result}

async def generate_yuli_openai_video(client, payload, provider, base_url, requested_model):
    """玉玉API veo3.1 走 OpenAI multipart 格式 /v1/videos，支持 seconds 时长控制。"""
    submit_url = f"{base_url}/v1/videos"
    data = {
        "model": yuli_openai_model_name(requested_model),
        "prompt": str(payload.prompt or ""),
        "seconds": yuli_video_seconds(payload.duration),
        "size": yuli_openai_size(payload.aspect_ratio),
        "watermark": "true" if payload.watermark else "false",
    }
    files = {}
    for ref in (payload.images or [])[:1]:
        ref_file = await yuli_fetch_reference_bytes(client, getattr(ref, "url", ""))
        if ref_file:
            files["input_reference"] = ref_file
            break
    headers = api_headers(json_body=False, provider=provider)
    if files:
        response = await client.post(submit_url, headers=headers, data=data, files=files)
    else:
        # 文生视频无垫图时，仍以 multipart/form-data 提交（把文本字段作为表单分块），
        # 避免 httpx 在只有 data 时退化成 application/x-www-form-urlencoded。
        multipart_fields = {key: (None, value) for key, value in data.items()}
        response = await client.post(submit_url, headers=headers, files=multipart_fields)
    response.raise_for_status()
    try:
        raw = response.json()
    except Exception as exc:
        resp_text = (response.text or "")[:500]
        raise HTTPException(status_code=502, detail=f"玉玉API 视频接口返回非 JSON 响应（状态 {response.status_code}）：{resp_text}") from exc
    task_id = raw.get("id") or extract_task_id(raw) or raw.get("task_id")
    result = raw
    if task_id and not video_output_urls(raw):
        result = await wait_for_video_task(client, provider, task_id, submit_url)
    urls = video_output_urls(result)
    if not urls:
        raise HTTPException(status_code=502, detail=f"视频生成成功但没有返回视频：{result}")
    local_urls = [await save_remote_video_to_output(url) for url in urls]
    return {"videos": local_urls, "task_id": task_id, "raw": result}

def volcengine_video_prompt_text(prompt, aspect_ratio="", duration=None):
    text = str(prompt or "").strip()
    suffixes = []
    ratio = str(aspect_ratio or "").strip()
    if ratio:
        suffixes.append(f"--ratio {ratio}")
    if not suffixes:
        return text
    suffix_text = " ".join(suffixes)
    return f"{text} {suffix_text}".strip() if text else suffix_text

def project_canvas_video_payload(
    request: VideoGenerationRequest,
    provider_id: str,
    model: str,
    references: List[Dict[str, Any]],
) -> CanvasVideoRequest:
    image_refs = [
        AIReference(
            url=item["adapter_url"],
            name=item.get("name") or "reference",
            kind="image",
            mime=item.get("mime") or "",
        )
        for item in references if item.get("kind") == "image"
    ]
    video_refs = [item["adapter_url"] for item in references if item.get("kind") == "video"]
    audio_refs = [item["adapter_url"] for item in references if item.get("kind") == "audio"]
    return CanvasVideoRequest(
        prompt=request.prompt,
        provider_id=provider_id,
        model=model,
        duration=request.duration,
        width=request.width,
        height=request.height,
        aspect_ratio=request.aspect_ratio,
        resolution=request.resolution,
        size=request.size,
        quality=request.quality,
        media_type=request.media_type,
        images=image_refs,
        videos=video_refs,
        audios=audio_refs,
        enhance_prompt=request.enhance_prompt,
        enable_upsample=request.enable_upsample,
        watermark=request.watermark,
        seed=request.seed,
        camera_fixed=request.camera_fixed,
        return_last_frame=request.return_last_frame,
        generate_audio=request.generate_audio,
        multimodal=bool(video_refs or audio_refs or len(image_refs) > 1),
    )

def runninghub_configured_video_model_definition(provider: Mapping[str, Any], model: str) -> Optional[Dict[str, Any]]:
    cached = provider.get("_runninghub_video_model_definitions")
    if isinstance(cached, Mapping) and isinstance(cached.get(model), Mapping):
        return dict(cached[model])
    capabilities = provider.get("video_model_capabilities")
    capability = capabilities.get(model) if isinstance(capabilities, Mapping) else None
    schema = capability.get("adapter_schema") if isinstance(capability, Mapping) else None
    if not isinstance(schema, Mapping):
        return None
    return {
        "name_en": model,
        "endpoint": str(schema.get("endpoint") or model).strip(),
        "output_type": "video",
        "params": [dict(item) for item in (schema.get("params") or []) if isinstance(item, Mapping)],
    }

PROJECT_VIDEO_CANDIDATE_INCOMPATIBLE_KEY = "_project_video_candidate_incompatible_parameters"

def project_video_mark_candidate_incompatible(
    provider: Dict[str, Any], model: str, parameters: List[str],
) -> None:
    if not parameters:
        return
    candidates = provider.setdefault(PROJECT_VIDEO_CANDIDATE_INCOMPATIBLE_KEY, {})
    current = candidates.setdefault(str(model or ""), [])
    current.extend(str(name).strip() for name in parameters if str(name).strip())
    candidates[str(model or "")] = list(dict.fromkeys(current))

def project_video_candidate_preflight_incompatible_parameters(
    provider: Mapping[str, Any], model: str,
) -> List[str]:
    candidates = provider.get(PROJECT_VIDEO_CANDIDATE_INCOMPATIBLE_KEY)
    if not isinstance(candidates, Mapping):
        return []
    values = candidates.get(str(model or ""))
    return [str(name).strip() for name in (values or []) if str(name).strip()]

def project_video_candidate_inline_reference_kinds(
    provider: Mapping[str, Any], model: str,
) -> set[str]:
    if (
        is_runninghub_provider(provider)
        or is_jimeng_provider(provider)
        or is_apimart_provider(provider)
        or is_yuli_provider(provider)
        or is_agnes_provider(provider, model)
    ):
        return set()
    if is_volcengine_provider(provider):
        base_url = video_api_root(provider)
        is_proxy = bool(urllib.parse.urlparse(base_url).path.rstrip("/"))
        return {"video", "audio"} if is_proxy else {"audio"}
    return {"video", "audio"}

def project_video_candidate_execution_incompatible_parameters(
    provider: Mapping[str, Any], model: str, references: List[Dict[str, Any]],
) -> List[str]:
    """Return synchronous execution blockers for one concrete route candidate."""
    incompatible: List[str] = []

    def add(name: str) -> None:
        if name not in incompatible:
            incompatible.append(name)

    if is_runninghub_provider(provider):
        wallet_key = str(provider.get("wallet_api_key") or "").strip() or runninghub_wallet_key_value()
        if not wallet_key:
            add("api_key")
    elif not is_jimeng_provider(provider):
        if not video_api_root(provider):
            add("base_url")
        if not provider_env_key_value(str(provider.get("id") or "")):
            add("api_key")

    inline_kinds = project_video_candidate_inline_reference_kinds(provider, model)
    if inline_kinds:
        for reference in references:
            if str(reference.get("kind") or "").strip().lower() not in inline_kinds:
                continue
            path = Path(str(reference.get("path") or ""))
            try:
                if path.stat().st_size > PROJECT_VIDEO_REFERENCE_INLINE_MAX_BYTES:
                    add("reference_size")
                    break
            except OSError:
                add("reference_size")
                break
    return incompatible

async def preflight_project_video_providers(
    request: VideoGenerationRequest,
    references: List[Dict[str, Any]],
    providers: List[Dict[str, Any]],
    *,
    provider_id: str = "",
    model: str = "",
) -> List[Dict[str, Any]]:
    """Resolve candidate execution blockers before routing can register a task."""
    snapshots = copy.deepcopy(list(providers or []))
    pinned_provider = str(provider_id or "").strip().lower()
    pinned_model = str(model or "").strip()
    for provider_index, provider in enumerate(snapshots):
        current_provider_id = str(provider.get("id") or "").strip().lower()
        if not provider.get("enabled", True) or (pinned_provider and current_provider_id != pinned_provider):
            continue
        remote_schema_failed = False
        for current_model in provider.get("video_models") or []:
            current_model = str(current_model or "").strip()
            if not current_model or (pinned_model and current_model != pinned_model):
                continue
            payload = project_canvas_video_payload(request, current_provider_id, current_model, references)
            execution_incompatible = project_video_candidate_execution_incompatible_parameters(
                provider, current_model, references,
            )
            project_video_mark_candidate_incompatible(provider, current_model, execution_incompatible)
            if video_adapter_incompatible_parameters(payload, provider) or execution_incompatible:
                continue
            if not is_runninghub_provider(provider):
                continue
            configured = runninghub_configured_video_model_definition(provider, current_model)
            if configured is not None:
                definition = configured
            elif remote_schema_failed:
                project_video_mark_candidate_incompatible(provider, current_model, ["adapter_schema"])
                continue
            else:
                try:
                    definition = await runninghub_model_definition(provider, current_model)
                except (HTTPException, httpx.HTTPError, OSError, ValueError, KeyError, TypeError):
                    remote_schema_failed = True
                    project_video_mark_candidate_incompatible(provider, current_model, ["adapter_schema"])
                    continue
            provider.setdefault("_runninghub_video_model_definitions", {})[current_model] = copy.deepcopy(definition)
        provider.pop("_runninghub_model_registry", None)
        if not pinned_provider:
            try:
                route_video_generation(
                    request,
                    snapshots[:provider_index + 1],
                    model=pinned_model,
                    adapter_incompatible_parameters=functools.partial(
                        project_video_adapter_incompatible_parameters,
                        references=references,
                    ),
                )
            except ModelRoutingError:
                pass
            else:
                return snapshots
    return snapshots

def project_video_adapter_incompatible_parameters(
    request: VideoGenerationRequest,
    provider: Mapping[str, Any],
    model: str,
    *,
    references: List[Dict[str, Any]],
) -> List[str]:
    """Evaluate the real adapter and resolved project references before registration."""
    payload = project_canvas_video_payload(request, str(provider.get("id") or ""), model, references)
    incompatible = video_adapter_incompatible_parameters(payload, provider)
    incompatible.extend(project_video_candidate_preflight_incompatible_parameters(provider, model))
    if incompatible or not is_runninghub_provider(provider):
        return list(dict.fromkeys(incompatible))
    model_definition = runninghub_configured_video_model_definition(provider, model) or {}
    endpoint = str(model_definition.get("endpoint") or model)
    image_to_video = runninghub_is_image_to_video(model) or runninghub_is_image_to_video(endpoint)
    params = model_definition.get("params") if isinstance(model_definition.get("params"), list) else []
    incompatible.extend(
        runninghub_video_incompatible_parameters(payload, params, image_to_video=image_to_video)
    )
    return list(dict.fromkeys(incompatible))

def video_adapter_incompatible_parameters(payload: CanvasVideoRequest, provider: Mapping[str, Any]) -> List[str]:
    """Return parameters a specialized video adapter would drop, clamp, or truncate."""
    incompatible: List[str] = []

    def add(name: str) -> None:
        if name not in incompatible:
            incompatible.append(name)

    if str(payload.media_type or "video").strip().lower() != "video":
        add("media_type")

    parsed_width, parsed_height = parse_size_pair(payload.size)
    if parsed_width and parsed_height:
        if payload.width is not None and payload.width != parsed_width:
            add("width")
        if payload.height is not None and payload.height != parsed_height:
            add("height")
    elif payload.width is not None or payload.height is not None:
        add("size")

    quality = str(payload.quality or "auto").strip().lower() or "auto"
    size = str(payload.size or "").strip()
    aspect_ratio = str(payload.aspect_ratio or "").strip()
    resolution = str(payload.resolution or "").strip().lower()
    image_count = len(payload.images or [])
    video_count = len(payload.videos or [])
    audio_count = len(payload.audios or [])
    if effective_protocol(provider, payload.model) not in VIDEO_EXECUTION_PROTOCOLS:
        add("protocol")

    is_jimeng = is_jimeng_provider(provider)
    is_runninghub = is_runninghub_provider(provider)
    is_apimart = is_apimart_provider(provider)
    is_volcengine = is_volcengine_provider(provider)
    volc_is_proxy = bool(is_volcengine and urllib.parse.urlparse(video_api_root(provider)).path.rstrip("/"))
    is_volcengine_direct = is_volcengine and not volc_is_proxy
    is_yuli = is_yuli_provider(provider)
    is_lingjing = is_lingjing_provider(provider)
    is_agnes = is_agnes_provider(provider, payload.model)
    specialized = is_jimeng or is_runninghub or is_apimart or is_volcengine_direct or is_yuli or is_lingjing or is_agnes
    if not specialized:
        return incompatible

    if quality != "auto":
        add("quality")

    if is_jimeng:
        if payload.width is not None:
            add("width")
        if payload.height is not None:
            add("height")
        if size:
            add("size")
        low, high = jimeng_video_duration_range(payload.model)
        if payload.duration < low or payload.duration > high:
            add("duration")
        if image_count > 9:
            add("image_reference_count")
        image_roles = [jimeng_video_ref_role(ref) for ref in (payload.images or [])]
        if (
            image_count > 2
            and "first_frame" in image_roles
            and "last_frame" in image_roles
            and not payload.multimodal
            and not video_count
            and not audio_count
        ):
            add("image_reference_count")
        if video_count > 3:
            add("video_reference_count")
        if audio_count > 3:
            add("audio_reference_count")
        if aspect_ratio and not jimeng_video_ratio_arg(aspect_ratio):
            add("aspect_ratio")
        if (
            aspect_ratio not in {"", "16:9"}
            and image_count >= 2
            and not payload.multimodal
            and not video_count
            and not audio_count
        ):
            add("aspect_ratio")
        if resolution and jimeng_video_resolution(payload.model, resolution).lower() != resolution:
            add("resolution")
        if payload.seed is not None:
            add("seed")
        if payload.enhance_prompt:
            add("enhance_prompt")
        if payload.enable_upsample:
            add("enable_upsample")
        if payload.watermark:
            add("watermark")
        if canvas_video_camera_fixed(payload):
            add("camera_fixed")
        if payload.return_last_frame:
            add("return_last_frame")
        if payload.generate_audio:
            add("generate_audio")
        return incompatible

    if is_runninghub:
        if payload.width is not None:
            add("width")
        if payload.height is not None:
            add("height")
        if size:
            add("size")
        if image_count > 10:
            add("image_reference_count")
        if video_count:
            add("video_reference_count")
        if audio_count:
            add("audio_reference_count")
        if payload.seed is not None:
            add("seed")
        if payload.enhance_prompt:
            add("enhance_prompt")
        if payload.enable_upsample:
            add("enable_upsample")
        if canvas_video_camera_fixed(payload):
            add("camera_fixed")
        if payload.return_last_frame:
            add("return_last_frame")
        return incompatible

    if is_apimart:
        if payload.width is not None:
            add("width")
        if payload.height is not None:
            add("height")
        if size:
            add("size")
        is_veo31 = is_apimart_veo31_model(payload.model)
        if is_veo31:
            if payload.duration < 4 or payload.duration > 8:
                add("duration")
            if aspect_ratio not in {"16:9", "9:16"}:
                add("aspect_ratio")
            if resolution and resolution not in {"720p", "1080p", "4k"}:
                add("resolution")
            veo_model = apimart_veo31_model(payload.model)
            image_limit = 0 if veo_model == "veo3.1-lite" else 2 if veo_model == "veo3.1-quality" else 3
            if video_count:
                add("video_reference_count")
            if audio_count:
                add("audio_reference_count")
            if payload.seed is not None:
                add("seed")
            if payload.return_last_frame:
                add("return_last_frame")
            if payload.generate_audio:
                add("generate_audio")
        else:
            if payload.duration < 4 or payload.duration > 15:
                add("duration")
            if aspect_ratio and apimart_video_size(aspect_ratio) != aspect_ratio and aspect_ratio != "keep_ratio":
                add("aspect_ratio")
            image_limit = 9
            role_count = sum(
                1
                for ref in (payload.images or [])
                if str(getattr(ref, "role", "") or "").strip()
                in {"first_frame", "last_frame", "reference_image"}
            )
            if role_count and role_count != image_count:
                add("image_reference_count")
            if role_count and video_count:
                add("video_reference_count")
        if image_count > image_limit:
            add("image_reference_count")
        if not is_veo31 and video_count > 3:
            add("video_reference_count")
        if not is_veo31 and audio_count > 3:
            add("audio_reference_count")
        if payload.enhance_prompt:
            add("enhance_prompt")
        if payload.enable_upsample:
            add("enable_upsample")
        if payload.watermark:
            add("watermark")
        if canvas_video_camera_fixed(payload):
            add("camera_fixed")
        return incompatible

    if is_volcengine_direct:
        if payload.width is not None:
            add("width")
        if payload.height is not None:
            add("height")
        if size:
            add("size")
        if payload.duration < 1 or payload.duration > 60:
            add("duration")
        if image_count > 9:
            add("image_reference_count")
        accepted_image_count = 0
        frame_roles_used = set()
        for ref in (payload.images or [])[:9]:
            role = volcengine_content_role(getattr(ref, "role", ""), "image")
            if role in {"first_frame", "last_frame"}:
                if role in frame_roles_used:
                    continue
                frame_roles_used.add(role)
                accepted_image_count += 1
            elif payload.multimodal:
                accepted_image_count += 1
            elif "first_frame" not in frame_roles_used:
                frame_roles_used.add("first_frame")
                accepted_image_count += 1
        if accepted_image_count != image_count:
            add("image_reference_count")
        if video_count > 3 or any(
            not is_private_asset_url(str(value or "").strip())
            for value in (payload.videos or [])
        ):
            add("video_reference_count")
        if audio_count > 3:
            add("audio_reference_count")
        if image_count > 1 and not payload.multimodal and not any(
            str(getattr(ref, "role", "") or "").strip().lower() in {"first_frame", "last_frame"}
            for ref in (payload.images or [])
        ):
            add("image_reference_count")
        if resolution and not volcengine_video_resolution(resolution):
            add("resolution")
        if payload.enhance_prompt:
            add("enhance_prompt")
        if payload.enable_upsample:
            add("enable_upsample")
        if payload.return_last_frame:
            add("return_last_frame")
        return incompatible

    if is_agnes:
        if size:
            add("size")
        if payload.duration != 5:
            add("duration")
        if image_count > 4:
            add("image_reference_count")
        if video_count:
            add("video_reference_count")
        if audio_count:
            add("audio_reference_count")
        has_width = payload.width is not None
        has_height = payload.height is not None
        if has_width != has_height:
            add("width" if has_width else "height")
        if has_width and has_height:
            if aspect_ratio not in {"", "16:9"}:
                add("aspect_ratio")
            if resolution:
                add("resolution")
        else:
            if aspect_ratio and aspect_ratio not in {"16:9", "9:16", "4:3", "3:4", "1:1", "21:9", "9:21"}:
                add("aspect_ratio")
            if resolution and resolution not in {"480p", "720p", "780p", "1080p"}:
                add("resolution")
        if payload.enhance_prompt:
            add("enhance_prompt")
        if payload.enable_upsample:
            add("enable_upsample")
        if payload.watermark:
            add("watermark")
        if canvas_video_camera_fixed(payload):
            add("camera_fixed")
        if payload.return_last_frame:
            add("return_last_frame")
        if payload.generate_audio:
            add("generate_audio")
        return incompatible

    if is_yuli or is_lingjing:
        if payload.width is not None:
            add("width")
        if payload.height is not None:
            add("height")
        if size:
            add("size")
        if resolution:
            add("resolution")
        yuli_openai = bool(is_yuli and yuli_is_veo_openai_model(payload.model))
        image_limit = 1 if yuli_openai else 3
        if image_count > image_limit:
            add("image_reference_count")
        if video_count:
            add("video_reference_count")
        if audio_count:
            add("audio_reference_count")
        if aspect_ratio and aspect_ratio not in {"16:9", "9:16"}:
            add("aspect_ratio")
        if is_yuli and not yuli_openai and payload.duration != 5:
            add("duration")
        if payload.seed is not None:
            add("seed")
        if (not is_yuli or yuli_openai) and payload.enhance_prompt:
            add("enhance_prompt")
        if (not is_yuli or yuli_openai) and payload.enable_upsample:
            add("enable_upsample")
        if is_yuli and not yuli_openai and payload.watermark:
            add("watermark")
        if canvas_video_camera_fixed(payload):
            add("camera_fixed")
        if payload.return_last_frame:
            add("return_last_frame")
        if payload.generate_audio:
            add("generate_audio")
    return incompatible

def runninghub_video_incompatible_parameters(
    payload: CanvasVideoRequest,
    params: List[Dict[str, Any]],
    *,
    image_to_video: bool,
) -> List[str]:
    """Validate schema-dependent RunningHub fields before task submission."""
    incompatible: List[str] = []

    def add(name: str) -> None:
        if name not in incompatible:
            incompatible.append(name)

    def usable_field(*keys: str, preferred: Any = None):
        field = runninghub_schema_field(params, *keys)
        if not field:
            return None
        options = runninghub_schema_options(field)
        if preferred is not None and options and str(preferred) not in options:
            return None
        return field

    aspect = str(payload.aspect_ratio or "16:9").strip() or "16:9"
    if aspect != "16:9":
        aspect_field = usable_field("aspectRatio", "ratio", preferred=aspect)
        size_value = runninghub_size_for_aspect(aspect)
        size_field = (
            usable_field("size", preferred=size_value)
            if runninghub_aspect_from_size(size_value, "") == aspect
            else None
        )
        if not aspect_field and not size_field:
            add("aspect_ratio")

    if payload.duration != 5:
        if payload.duration < 1 or payload.duration > 60 or not usable_field("duration", preferred=str(payload.duration)):
            add("duration")

    resolution = str(payload.resolution or "").strip().lower()
    if resolution and not usable_field("resolution", preferred=resolution):
        add("resolution")

    if payload.watermark and not usable_field("watermark"):
        add("watermark")
    if payload.generate_audio and not usable_field("generateAudio"):
        add("generate_audio")

    image_count = len(payload.images or [])
    first_field = runninghub_schema_field(
        params, "firstFrameUrl", "first_frame_url", "firstFrameImage", "first_frame_image",
    )
    if not image_count and (image_to_video or runninghub_schema_field_required(first_field)):
        add("image_reference_count")
    elif image_count:
        image_field = runninghub_schema_field(
            params, "imageUrls", "image_urls", "imageUrl", "image_url", "referenceImages", "referenceImageUrls",
        )
        if image_field:
            key = str(image_field.get("fieldKey") or "")
            multiple = key.endswith("s") or image_field.get("multipleInputs") is True
            if image_count > (10 if multiple else 1):
                add("image_reference_count")
        else:
            last_field = runninghub_schema_field(params, "lastFrameUrl", "last_frame_url", "lastFrameImage", "last_frame_image")
            supported_count = int(bool(first_field or image_to_video)) + int(bool(last_field))
            if not supported_count or image_count > supported_count:
                add("image_reference_count")
    return incompatible

def canvas_video_project_media_values(payload: CanvasVideoRequest) -> List[str]:
    values = [str(getattr(ref, "url", "") or "").strip() for ref in (payload.images or [])]
    values.extend(str(value or "").strip() for value in (payload.videos or []))
    values.extend(str(value or "").strip() for value in (payload.audios or []))
    return [value for value in values if value]

def require_canvas_video_project_media_scope(payload: CanvasVideoRequest) -> None:
    """Reject project-media references unless an internal project task owns the call."""
    require_project_media_values_scope(canvas_video_project_media_values(payload))

@app.post("/api/canvas-video")
async def canvas_video(payload: CanvasVideoRequest):
    require_canvas_video_project_media_scope(payload)
    provider = get_api_provider(payload.provider_id)
    return await execute_canvas_video(payload, provider)

async def execute_canvas_video(payload: CanvasVideoRequest, provider: Mapping[str, Any]):
    incompatible = video_adapter_incompatible_parameters(payload, provider)
    if incompatible:
        raise HTTPException(status_code=400, detail={
            "code": "unsupported_video_adapter_parameters",
            "message": f"{provider.get('name') or provider.get('id') or '当前视频适配器'} 不支持本次请求的全部参数",
            "incompatible_parameters": incompatible,
        })
    if is_jimeng_provider(provider):
        return await generate_jimeng_video(payload, provider)
    if is_runninghub_provider(provider):
        try:
            return await generate_runninghub_video(payload, provider)
        except HTTPException as exc:
            print(f"RunningHub 视频生成失败 model={payload.model}: {exc.detail}")
            raise
        except httpx.HTTPStatusError as exc:
            text = exc.response.text
            raise HTTPException(status_code=exc.response.status_code, detail=f"RunningHub 视频接口错误：{text}") from exc
        except httpx.HTTPError as exc:
            log_net_error(f"视频(RunningHub) 网络/TLS错误 model={payload.model}", exc)
            raise HTTPException(status_code=502, detail=f"请求 RunningHub 视频接口失败：{exc}") from exc
    base_url = video_api_root(provider)
    if not base_url:
        raise HTTPException(status_code=400, detail=f"{provider.get('name') or provider['id']} 未配置 Base URL")
    api_key = provider_env_key_value(provider["id"])
    if not api_key:
        raise HTTPException(status_code=400, detail=f"未配置 {provider.get('name') or provider['id']} 的 API Key，请在 API 设置中填写。")
    is_apimart = is_apimart_provider(provider)
    is_volcengine = is_volcengine_provider(provider)
    is_yuli = is_yuli_provider(provider)
    is_lingjing = is_lingjing_provider(provider)
    is_agnes = is_agnes_provider(provider, payload.model)
    volc_is_proxy = bool(is_volcengine and urllib.parse.urlparse(base_url).path.rstrip("/"))
    submit_urls = video_submit_url_candidates(provider, base_url)
    submit_url = submit_urls[0]
    requested_model = selected_model(payload.model, "agnes-video-v2.0" if is_agnes else "veo3-fast")
    is_veo31 = is_apimart and is_apimart_veo31_model(requested_model)
    if is_agnes:
        try:
            async with httpx.AsyncClient(timeout=VIDEO_POLL_TIMEOUT) as agnes_client:
                return await generate_agnes_video(agnes_client, payload, provider, base_url, requested_model)
        except httpx.HTTPStatusError as exc:
            text = exc.response.text
            raise HTTPException(status_code=exc.response.status_code, detail=f"Agnes 视频接口错误：{text}") from exc
        except httpx.HTTPError as exc:
            log_net_error(f"视频(Agnes) 网络/TLS错误 model={requested_model}", exc)
            raise HTTPException(status_code=502, detail=f"请求 Agnes 视频接口失败：{exc}") from exc
    if is_lingjing:
        try:
            async with httpx.AsyncClient(timeout=VIDEO_POLL_TIMEOUT) as lingjing_client:
                return await generate_lingjing_openai_video(lingjing_client, payload, provider, base_url, requested_model)
        except httpx.HTTPStatusError as exc:
            text = exc.response.text
            raise HTTPException(status_code=exc.response.status_code, detail=f"灵境 API 视频接口错误：{text}") from exc
        except httpx.HTTPError as exc:
            log_net_error(f"视频(灵境) 网络/TLS错误 model={requested_model}", exc)
            raise HTTPException(status_code=502, detail=f"请求灵境 API 视频接口失败：{exc}") from exc
    # 玉玉API veo3.1 走 OpenAI multipart 格式（支持 seconds 时长）；其余模型（doubao 等）
    # 沿用下方原生 /v1/video/create JSON 流程。
    if is_yuli and yuli_is_veo_openai_model(requested_model):
        try:
            async with httpx.AsyncClient(timeout=VIDEO_POLL_TIMEOUT) as yuli_client:
                return await generate_yuli_openai_video(yuli_client, payload, provider, base_url, requested_model)
        except httpx.HTTPStatusError as exc:
            text = exc.response.text
            raise HTTPException(status_code=exc.response.status_code, detail=f"上游视频接口错误：{text}") from exc
        except httpx.HTTPError as exc:
            log_net_error(f"视频(玉玉) 网络/TLS错误 model={requested_model}", exc)
            raise HTTPException(status_code=502, detail=f"请求上游视频接口失败：{exc}") from exc
    try:
        async with httpx.AsyncClient(timeout=VIDEO_POLL_TIMEOUT) as client:
            # --- 构造图片载荷 ---
            if is_apimart:
                # APIMart 只接受 http/https 或 asset:// URL，先上传本地图片取回网络 URL
                image_with_roles = []
                invalid_images = []  # 每项为 (原始 URL, 失败原因)
                video_payload = []
                invalid_videos = []
                for ref_url in payload.videos[:3]:
                    ref_url = str(ref_url or "").strip()
                    if not ref_url:
                        continue
                    normalized_video_url = await upload_video_for_apimart(client, provider, ref_url)
                    if valid_apimart_video_image_input(normalized_video_url):
                        video_payload.append(normalized_video_url)
                    else:
                        reason = normalized_video_url[4:] if isinstance(normalized_video_url, str) and normalized_video_url.startswith("ERR:") else apimart_video_reference_error(ref_url)
                        invalid_videos.append((ref_url, reason))
                if invalid_videos:
                    first_url, first_reason = invalid_videos[0]
                    sample = invalid_video_image_preview(first_url)
                    raise HTTPException(
                        status_code=400,
                        detail=f"输入视频无法转换为 APIMart 支持的格式：{sample}\n原因：{first_reason}"
                    )
                apimart_model = apimart_veo31_model(requested_model) if is_veo31 else ""
                if apimart_model == "veo3.1-lite" and payload.images:
                    raise HTTPException(status_code=400, detail="veo3.1-lite 不支持图片输入，请改用 veo3.1-fast 或 veo3.1-quality。")
                image_limit = 0 if apimart_model == "veo3.1-lite" else (3 if is_veo31 else 9)
                for ref in payload.images[:image_limit]:
                    if not ref.url:
                        continue
                    role = str(ref.role or "").strip()
                    if not is_veo31 and role in {"first_frame", "last_frame", "reference_image"}:
                        up_url = await upload_image_for_apimart(client, provider, ref.url)
                        if valid_apimart_video_image_input(up_url):
                            image_with_roles.append({"url": up_url, "role": role})
                        else:
                            reason = up_url[4:] if isinstance(up_url, str) and up_url.startswith("ERR:") else "未知错误"
                            invalid_images.append((ref.url, reason))
                image_payload = []
                if not image_with_roles:
                    for ref in payload.images[:image_limit]:
                        if not ref.url:
                            continue
                        up_url = await upload_image_for_apimart(client, provider, ref.url)
                        if valid_apimart_video_image_input(up_url):
                            image_payload.append(up_url)
                        else:
                            reason = up_url[4:] if isinstance(up_url, str) and up_url.startswith("ERR:") else "未知错误"
                            invalid_images.append((ref.url, reason))
                if payload.images and not image_with_roles and not image_payload:
                    first_url, first_reason = invalid_images[0] if invalid_images else ("", "未知错误")
                    sample = invalid_video_image_preview(first_url)
                    raise HTTPException(status_code=400, detail=f"输入图片无法转换为视频接口支持的格式：{sample}\n原因：{first_reason}\n请确认本地文件存在且不超过 10MB；VEO3.1 需要图片是 APIMart 可访问的 http/https / asset:// / data URL。")
                # --- APIMart 请求体 ---
                if is_veo31:
                    model = apimart_model
                    body = {
                        "prompt": payload.prompt,
                        "model": model,
                        "duration": apimart_veo31_duration(payload.duration),
                        "aspect_ratio": apimart_veo31_aspect(payload.aspect_ratio),
                        "resolution": apimart_veo31_resolution(payload.resolution),
                    }
                    if image_payload and model != "veo3.1-lite":
                        video_images = image_payload[:3]
                        if model == "veo3.1-quality" and len(video_images) > 2:
                            video_images = video_images[:2]
                        body["image_urls"] = video_images
                        if len(video_images) == 2:
                            body["generation_type"] = "frame"
                        elif len(video_images) >= 3 and model != "veo3.1-quality":
                            body["generation_type"] = "reference"
                    if model != "veo3.1-lite":
                        body["official_fallback"] = False
                else:
                    body = {
                        "prompt": payload.prompt,
                        "model": selected_model(payload.model, "doubao-seedance-2.0"),
                        "duration": apimart_video_duration(payload.duration),
                        "size": apimart_video_size(payload.aspect_ratio or payload.size),
                        "resolution": payload.resolution or "480p",
                    }
                    if image_with_roles and video_payload:
                        raise HTTPException(status_code=400, detail="APIMart Seedance 的 image_with_roles 不能和 video_urls 同时使用，请只保留图片首尾帧或参考视频其中一种。")
                    if image_with_roles:
                        body["image_with_roles"] = image_with_roles
                    elif image_payload:
                        body["image_urls"] = image_payload[:9]
                    if video_payload:
                        body["video_urls"] = video_payload
                    audio_payload = []
                    invalid_audios = []
                    for ref_url in (payload.audios or [])[:3]:
                        ref_url = str(ref_url or "").strip()
                        if not ref_url:
                            continue
                        normalized_audio_url = await upload_audio_for_apimart(client, provider, ref_url)
                        if valid_apimart_video_image_input(normalized_audio_url):
                            audio_payload.append(normalized_audio_url)
                        else:
                            reason = normalized_audio_url[4:] if isinstance(normalized_audio_url, str) and normalized_audio_url.startswith("ERR:") else "未知错误"
                            invalid_audios.append((ref_url, reason))
                    if invalid_audios:
                        first_url, first_reason = invalid_audios[0]
                        raise HTTPException(status_code=400, detail=f"参考音频无法转换为 APIMart 支持的地址：{invalid_video_image_preview(first_url)}\n原因：{first_reason}")
                    if audio_payload:
                        body["audio_urls"] = audio_payload
                    if payload.trusted_asset:
                        img_count = len(body.get("image_urls") or []) or len(image_with_roles)
                        body["prompt"] = apply_trusted_asset_prompt_index(
                            body["prompt"], img_count, len(video_payload), len(audio_payload)
                        )
                    if payload.seed is not None:
                        body["seed"] = payload.seed
                    if payload.return_last_frame:
                        body["return_last_frame"] = True
                    if payload.generate_audio:
                        body["generate_audio"] = True
            else:
                # 非 APIMart：data URL 方式（OpenAI / ComflyAI 接口）
                if is_volcengine and not volc_is_proxy:
                    text = str(payload.prompt or "").strip()
                    volc_model = selected_model(payload.model, "doubao-seedance-2-0-fast-260128")
                    body = {
                        "model": volc_model,
                        "content": [
                            {
                                "type": "text",
                                "text": text,
                            }
                        ],
                    }
                    # 火山方舟视频接口（含 Seedance 2.0 图生视频）均通过 body 的 duration 字段控制时长；
                    # 之前对 seedance-2.0 + 参考图的情况省略了 duration，导致接口回退到默认 5s。
                    body["duration"] = volcengine_video_duration(payload.duration)
                    if payload.aspect_ratio:
                        body["ratio"] = payload.aspect_ratio
                    resolution = volcengine_video_resolution(payload.resolution)
                    if resolution:
                        body["resolution"] = resolution
                    if payload.watermark:
                        body["watermark"] = True
                    if payload.generate_audio:
                        body["generate_audio"] = True
                    if canvas_video_camera_fixed(payload):
                        body["camerafixed"] = True
                    image_like_urls = set()
                    frame_roles_used = {"first_frame": False, "last_frame": False}
                    volc_video_count = 0

                    def append_volcengine_image(url: str, role: str):
                        if role in {"first_frame", "last_frame"}:
                            if frame_roles_used.get(role):
                                return False
                            frame_roles_used[role] = True
                        elif role != "reference_image":
                            return False
                        body["content"].append({
                            "type": "image_url",
                            "image_url": {"url": url},
                            "role": role,
                        })
                        image_like_urls.add(url)
                        return True

                    for ref in payload.images[:9]:
                        url = volcengine_media_reference_url(ref.url, max_image_size=1536)
                        if not url:
                            continue
                        role = volcengine_content_role(ref.role, "image")
                        if role in {"first_frame", "last_frame"}:
                            append_volcengine_image(url, role)
                        elif payload.multimodal:
                            # 智能多帧/多参模式：多张图作为参考图提交，不能全部伪装成首帧。
                            append_volcengine_image(url, "reference_image")
                        elif not frame_roles_used["first_frame"]:
                            # 普通图生视频没有显式 role 时，只取第一张作为首帧。
                            append_volcengine_image(url, "first_frame")
                    for url in (payload.videos or [])[:3]:
                        text_url = str(url or "").strip()
                        if not text_url:
                            continue
                        media_url = volcengine_media_reference_url(text_url, max_image_size=1536 if looks_like_image_media_url(text_url) else None)
                        if not media_url:
                            continue
                        if media_url in image_like_urls or looks_like_image_media_url(media_url):
                            append_volcengine_image(media_url, "reference_image" if payload.multimodal else "first_frame")
                            continue
                        video_items = await volcengine_video_reference_content_items(media_url)
                        body["content"].extend(video_items)
                        volc_video_count += 1
                    for url in (payload.audios or [])[:3]:
                        duration = probe_local_audio_duration_seconds(url)
                        if duration is not None and (duration < 1.8 or duration > 15.2):
                            raise HTTPException(
                                status_code=400,
                                detail=f"参考音频时长 {duration:.2f} 秒超出范围：方舟 Seedance 参考音频要求在 1.8 ~ 15.2 秒之间，请裁剪后再插入。"
                            )
                        audio_url = media_adapter_data_url(url, "audio")
                        if not audio_url:
                            continue
                        body["content"].append({
                            "type": "audio_url",
                            "audio_url": {"url": audio_url},
                            "role": volcengine_content_role("", "audio"),
                        })
                    if payload.trusted_asset and body["content"] and body["content"][0].get("type") == "text":
                        body["content"][0]["text"] = apply_trusted_asset_prompt_index(
                            body["content"][0].get("text") or "", len(image_like_urls), volc_video_count, 0
                        )
                    if payload.seed is not None:
                        body["seed"] = payload.seed
                elif is_yuli:
                    # 玉玉API（yuli.host）视频走自有 veo 统一格式：POST /v1/video/create。
                    # 字段：model / prompt / images[]（http(s) URL）/ enhance_prompt /
                    # enable_upsample / aspect_ratio（仅 16:9、9:16）。无 duration 字段，
                    # 时长由模型本身决定，所以这里不传 duration/seconds。
                    yuli_images = []
                    for ref in payload.images[:3]:
                        ref_url = str(getattr(ref, "url", "") or "").strip()
                        if not ref_url:
                            continue
                        if ref_url.startswith("http://") or ref_url.startswith("https://"):
                            yuli_images.append(ref_url)
                        else:
                            # 本地/dataURL 图片转成 data URL 兜底传递
                            data_url = reference_to_data_url(ref.dict(), max_size=1536)
                            if data_url:
                                yuli_images.append(data_url)
                    prompt_text = str(payload.prompt or "")
                    body = {
                        "model": selected_model(payload.model, "veo3.1-fast"),
                        "prompt": prompt_text,
                        "enhance_prompt": bool(payload.enhance_prompt),
                    }
                    if yuli_images:
                        body["images"] = yuli_images
                    ratio = str(payload.aspect_ratio or "").strip()
                    if ratio in {"16:9", "9:16"}:
                        body["aspect_ratio"] = ratio
                    if payload.enable_upsample:
                        body["enable_upsample"] = True
                else:
                    image_payload = []
                    for ref in payload.images:
                        if ref.url:
                            image_payload.append(reference_to_data_url(ref.dict(), max_size=1536))
                    video_payload = [
                        media_adapter_data_url(value, "video")
                        for value in (payload.videos or [])
                        if str(value or "").strip()
                    ]
                    audio_payload = [
                        media_adapter_data_url(value, "audio")
                        for value in (payload.audios or [])
                        if str(value or "").strip()
                    ]
                    body = {
                        "prompt": payload.prompt,
                        "model": selected_model(payload.model, "veo3-fast"),
                        "duration": payload.duration,
                        "width": payload.width,
                        "height": payload.height,
                        "watermark": payload.watermark,
                        "quality": payload.quality,
                        "media_type": payload.media_type,
                    }
                    if payload.aspect_ratio:
                        body["aspect_ratio"] = payload.aspect_ratio
                        body["ratio"] = payload.aspect_ratio
                    if payload.size:
                        body["size"] = payload.size
                    if payload.resolution:
                        body["resolution"] = payload.resolution
                    if image_payload:
                        body["images"] = image_payload
                    if video_payload:
                        body["videos"] = video_payload
                    if audio_payload:
                        body["audios"] = audio_payload
                    if payload.enhance_prompt:
                        body["enhance_prompt"] = True
                    if payload.enable_upsample:
                        body["enable_upsample"] = True
                    if payload.seed is not None:
                        body["seed"] = payload.seed
                    if canvas_video_camera_fixed(payload):
                        body["camerafixed"] = True
                    if payload.return_last_frame:
                        body["return_last_frame"] = True
                    if payload.generate_audio:
                        body["generate_audio"] = True
            # --- 发起视频生成请求 ---
            raw = None
            html_response = None
            last_response = None
            last_json_error = None
            total_candidates = len(submit_urls)
            for idx, candidate_url in enumerate(submit_urls):
                submit_url = candidate_url
                is_last = idx == total_candidates - 1
                response = await client.post(
                    submit_url,
                    headers=api_headers(provider=provider, model=requested_model),
                    json=body,
                )
                last_response = response
                if response.status_code >= 400:
                    # 404/405（或直接返回网页 HTML）通常表示该平台不支持这个端点路径——
                    # 例如有的站点只实现了统一格式的 /v2/videos/generations，而我们先试了 /v1。
                    # 这种情况要继续尝试下一个候选端点（关键修复：以前在这里直接 raise_for_status，
                    # 第一个 /v1 报错就抛出，永远轮不到 /v2，表现为“接口错误”）。
                    # 其它错误（模型不支持/时长/额度等请求被拒）说明端点是存在的，直接抛出交给外层友好提示。
                    endpoint_missing = response.status_code in (404, 405) or looks_like_html_response(response.text)
                    if endpoint_missing and not is_last:
                        continue
                    response.raise_for_status()
                try:
                    raw = response.json()
                    break
                except Exception as exc:
                    last_json_error = exc
                    if looks_like_html_response(response.text):
                        html_response = response
                        continue
                    if not is_last:
                        continue
                    resp_text = response.text[:500]
                    raise HTTPException(status_code=502, detail=f"上游视频接口返回非 JSON 响应（状态 {response.status_code}）：{resp_text}")
            if raw is None:
                resp = html_response or last_response
                status_code = getattr(resp, "status_code", 200)
                resp_text = (getattr(resp, "text", "") or "")[:500]
                raise HTTPException(
                    status_code=502,
                    detail=(
                        f"上游视频接口返回了网页 HTML，而不是 JSON（状态 {status_code}）。\n\n"
                        f"这通常表示 API 设置里的 Base URL 指到了第三方聚合平台的管理后台/网页入口，"
                        f"或该平台不支持当前视频接口路径。请确认 Base URL 是接口地址，例如以 /v1 结尾的 OpenAI 兼容地址，"
                        f"并确认该平台实际支持视频生成端点。\n\n原始响应：{resp_text}"
                    )
                ) from last_json_error
            task_id = extract_task_id(raw) or raw.get("task_id") or raw.get("id")
            result = raw
            if task_id and not video_output_urls(raw):
                result = await wait_for_video_task(client, provider, task_id, submit_url)
            urls = video_output_urls(result)
            if not urls:
                raise HTTPException(status_code=502, detail=f"视频生成成功但没有返回视频：{result}")
            local_urls = [await save_remote_video_to_output(url) for url in urls]
            return {"videos": local_urls, "task_id": task_id, "raw": result}
    except httpx.HTTPStatusError as exc:
        text = exc.response.text
        try:
            requested_model = body.get("model", "") or payload.model or ""
        except NameError:
            requested_model = payload.model or ""
        provider_name = provider.get('name') or provider['id']
        # 1) 模型名不在上游支持范围 → 从错误信息里抽取合法列表展示
        valid_models_match = re.search(r"not in\s*\[([^\]]+)\]", text)
        if valid_models_match:
            valid_models = [m.strip() for m in valid_models_match.group(1).split(",") if m.strip()]
            sample = valid_models[:30]
            more = f"（共 {len(valid_models)} 个，仅显示前 {len(sample)} 个）" if len(valid_models) > len(sample) else ""
            hint = (
                f"上游「{provider_name}」不识别模型「{requested_model}」。\n\n"
                f"上游支持的视频模型清单{more}：\n  {', '.join(sample)}\n\n"
                f"请到「API 设置」里把视频模型改成上面列表中的一个。"
            )
            raise HTTPException(status_code=exc.response.status_code, detail=hint) from exc
        # 2) 模型名合法但账号没开通通道
        if "channel not found" in text or "model_not_found" in text:
            hint = (
                f"上游「{provider_name}」识别了模型「{requested_model}」，但你的 API Key 账号下**没有该模型的可用通道**。\n\n"
                f"原因：你的账号没开通这个模型的访问权限（付费/订阅相关）。\n\n"
                f"解决方法：\n"
                f"  1. 登录 {provider.get('base_url') or '上游平台'} 控制台，开通该模型 / 充值；\n"
                f"  2. 或在「API 设置」里把视频模型改成你账号已开通的型号（如 veo3-fast / veo2-fast / sora-2 等）。"
            )
            raise HTTPException(status_code=exc.response.status_code, detail=hint) from exc
        if "text.duration" in text or "specified duration is not supported" in text:
            hint = (
                f"上游「{provider_name}」模型「{requested_model}」不支持当前时长参数。\n\n"
                f"不同视频模型支持的时长不一样；如果选择了模型不支持的时长，上游可能报错，"
                f"也可能自动按平台默认时长生成，例如 5 秒。\n\n"
                f"请把视频时长切回该模型支持的值，或改用支持更长时长的视频模型。"
            )
            raise HTTPException(status_code=exc.response.status_code, detail=hint) from exc
        if "audio duration" in text.lower():
            too_long = "less than or equal" in text.lower() or "15.2" in text
            bound_hint = "太长（超过 15.2 秒）" if too_long else "太短（不足 1.8 秒）"
            hint = (
                f"上游「{provider_name}」模型「{requested_model}」拒绝了参考音频：时长{bound_hint}。\n\n"
                f"方舟 Seedance 的参考音频时长必须在 1.8 ~ 15.2 秒之间，"
                f"请把音频裁剪到这个区间后再作为参考音频输入。"
            )
            raise HTTPException(status_code=exc.response.status_code, detail=hint) from exc
        if "inputimagesensitivecontentdetected" in text.lower() or "privacyinformation" in text.lower() or "may contain real person" in text.lower():
            hint = (
                f"上游「{provider_name}」拦截了输入参考图，原因是图片里可能包含真人身份/隐私信息。\n\n"
                f"这不是代码协议错误，而是火山视频模型的内容安全策略。\n\n"
                f"建议你这样处理：\n"
                f"  1. 改用非真人参考图，例如插画、AI 头像、商品图、场景图；\n"
                f"  2. 先把真人脸做模糊、遮挡、裁掉，或转成明显的二次元/插画风；\n"
                f"  3. 如果只是想做文生视频，先去掉参考图只保留文字提示词测试。"
            )
            raise HTTPException(status_code=exc.response.status_code, detail=hint) from exc
        raise HTTPException(status_code=exc.response.status_code, detail=f"上游视频接口错误：{text}") from exc
    except httpx.HTTPError as exc:
        log_net_error(f"视频 网络/TLS错误 provider={provider.get('id')} model={payload.model}", exc)
        raise HTTPException(status_code=502, detail=f"请求上游视频接口失败：{exc}") from exc

# --- Canvas LLM ---

def chat_provider_supports_audio(provider: Mapping[str, Any]) -> bool:
    """Require an explicit provider capability before sending conversation audio."""
    provider = provider if isinstance(provider, Mapping) else {}
    if str(provider.get("protocol") or "").strip().lower() in {"codex", "gemini-cli"}:
        return False
    return bool(
        provider.get("chat_audio_input")
        or provider.get("audio_input")
        or provider.get("supports_audio_input")
        or provider.get("supports_audio")
    )

@app.post("/api/canvas-llm")
async def canvas_llm(payload: CanvasLLMRequest):
    media_values = [
        str(getattr(item, "url", item) or "") for item in (payload.images or [])
    ] + [str(item or "") for item in (payload.videos or [])] + [str(item or "") for item in (payload.audios or [])] + [
        str(item.get("url") or "") for item in (payload.audio_references or []) if isinstance(item, dict)
    ]
    require_project_media_values_scope(media_values)
    _provider = get_api_provider(payload.provider)
    audio_references = [
        item for item in (payload.audio_references or [])
        if isinstance(item, dict) and str(item.get("asset_id") or item.get("url") or "").strip()
    ]
    if (payload.audios or audio_references) and not chat_provider_supports_audio(_provider):
        raise HTTPException(status_code=400, detail={
            "code": "unsupported_audio_input",
            "message": "当前对话适配器不支持音频参考输入",
        })
    if is_codex_provider(_provider):
        model = selected_model(payload.model, (_provider.get("chat_models") or CODEX_DEFAULT_CHAT_MODELS)[0])
        payload.model = model
        text, raw = await codex_chat_text(payload, payload.messages)
        return {"text": text, "model": model, "raw_usage": None, "raw": raw}
    if is_gemini_cli_provider(_provider):
        model = selected_model(payload.model, (_provider.get("chat_models") or GEMINI_CLI_DEFAULT_CHAT_MODELS)[0])
        payload.model = model
        text, raw = await gemini_cli_chat_text(payload, payload.messages)
        return {"text": text, "model": model, "raw_usage": None, "raw": raw}
    chat_base, chat_hdrs, model = resolve_chat_provider(payload.provider, payload.model, payload.ms_model)
    # 判断协议：APIMart 异步 vs 标准 OpenAI
    _llm_provider = get_api_provider(payload.provider) if payload.provider not in ("modelscope",) else {}
    _is_apimart = is_apimart_provider(_llm_provider)
    system_prompt = (payload.system_prompt or "").strip()
    upstream_messages = [{"role": "system", "content": system_prompt}] if system_prompt else []
    for item in payload.messages[-MAX_HISTORY_MESSAGES:]:
        role = item.get("role")
        content = item.get("content")
        if role in {"user", "assistant"} and content:
            upstream_messages.append({"role": role, "content": content})
    # 构造用户消息：有图片/视频时用 OpenAI/Gemini 多模态格式
    image_inputs = [img for img in (payload.images or []) if is_image_reference_value(img)]
    video_inputs = [video for video in (payload.videos or []) if is_video_reference_value(video)]
    audio_inputs = audio_references or [
        {"url": str(value).strip(), "asset_id": "", "kind": "audio"}
        for value in (payload.audios or []) if str(value or "").strip()
    ]
    if image_inputs or video_inputs or audio_inputs:
        content_parts = [{"type": "text", "text": payload.message}]
        ok_imgs = 0
        for img in image_inputs[:8]:
            if not img or not isinstance(img, str):
                continue
            ref_url = media_reference_to_url(img, max_image_size=1024)
            if not ref_url:
                continue
            content_parts.append({"type": "image_url", "image_url": {"url": ref_url}})
            ok_imgs += 1
        ok_videos = 0
        for video in video_inputs[:3]:
            if not video or not isinstance(video, str):
                continue
            frame_urls = await video_reference_to_frame_data_urls(video, max_frames=6, max_size=768)
            if frame_urls:
                ok_videos += 1
                content_parts.append({"type": "text", "text": f"以下是视频 {ok_videos} 按时间顺序抽取的关键帧，请结合这些画面理解视频内容。"})
                for frame_url in frame_urls:
                    content_parts.append({"type": "image_url", "image_url": {"url": frame_url}})
            else:
                ref_url = media_reference_to_url(video)
                if not ref_url:
                    continue
                content_parts.append({"type": "video_url", "video_url": {"url": ref_url}})
                ok_videos += 1
        for audio in audio_inputs[:3]:
            content_parts.append({
                "type": "audio_reference",
                "audio_reference": {
                    "asset_id": str(audio.get("asset_id") or ""),
                    "kind": "audio",
                    "name": str(audio.get("name") or "音频参考"),
                    "url": str(audio.get("url") or ""),
                },
            })
        print(f"[canvas-llm] model={model} provider={payload.provider} text_len={len(payload.message)} images={ok_imgs}/{len(payload.images)} videos={ok_videos}/{len(payload.videos)} audios={len(audio_inputs)}/{len(payload.audios)}")
        upstream_messages.append({"role": "user", "content": content_parts})
    else:
        upstream_messages.append({"role": "user", "content": payload.message})
    raw = None
    try:
        async with httpx.AsyncClient(timeout=AI_REQUEST_TIMEOUT) as client:
            req_body = {"model": model, "messages": upstream_messages}
            if _is_apimart:
                req_body["stream"] = False   # APIMart 默认流式，强制关闭
            response = await client.post(
                f"{chat_base}/chat/completions",
                headers=chat_hdrs,
                json=req_body,
            )
            response.raise_for_status()
            if not response.content:
                raise HTTPException(status_code=502, detail="上游接口返回了空响应")
            raw = response.json()
    except httpx.HTTPStatusError as exc:
        body = exc.response.text or ""
        friendly = friendly_chat_error_detail(body, model, _llm_provider)
        raise HTTPException(status_code=exc.response.status_code, detail=friendly or f"上游接口错误：{body}") from exc
    except httpx.HTTPError as exc:
        raise HTTPException(status_code=502, detail=f"请求上游接口失败：{exc}") from exc
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"解析上游响应失败：{exc}") from exc
    try:
        text = text_from_chat_response(raw).strip() if isinstance(raw, dict) else ""
        text = text or "接口返回了空回复。"
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"解析回复内容失败：{exc}") from exc
    raw_data = unwrap_apimart_response(raw) if isinstance(raw, dict) else {}
    return {"text": text, "model": model, "raw_usage": raw_data.get("usage")}


def project_conversation_media_budget(project_id: str, references: List[Dict[str, Any]]) -> None:
    """Validate the complete provider-consumed media set before reading any bytes."""
    counts = {"image": 0, "video": 0, "audio": 0}
    limits = {
        "image": PROJECT_CONVERSATION_MAX_IMAGE_REFERENCES,
        "video": PROJECT_CONVERSATION_MAX_VIDEO_REFERENCES,
        "audio": PROJECT_CONVERSATION_MAX_AUDIO_REFERENCES,
    }
    total_bytes = 0
    for item in references:
        kind = str(item.get("kind") or "").strip().lower()
        asset_id = str(item.get("asset_id") or "").strip()
        if kind not in counts or not asset_id:
            raise HTTPException(status_code=400, detail={
                "code": "conversation_media_invalid", "message": "对话参考素材缺少有效类型或资源标识",
            })
        counts[kind] += 1
        if counts[kind] > limits[kind]:
            raise HTTPException(status_code=400, detail={
                "code": "conversation_media_count_exceeded",
                "message": f"对话{kind}参考素材数量超过上限 {limits[kind]}",
            })
        record = project_store_call("media_record", project_id, asset_id)
        if str(record.get("kind") or "").strip().lower() != kind:
            raise HTTPException(status_code=400, detail={
                "code": "conversation_media_kind_mismatch", "message": "对话参考素材类型与项目记录不一致",
            })
        size = int(record.get("size") or Path(str(record.get("path") or "")).stat().st_size)
        if size > PROJECT_CONVERSATION_MEDIA_MAX_FILE_BYTES:
            raise HTTPException(status_code=400, detail={
                "code": "conversation_media_file_too_large",
                "message": f"对话参考素材超过单文件 {PROJECT_CONVERSATION_MEDIA_MAX_FILE_BYTES} 字节上限",
            })
        total_bytes += size
        if total_bytes > PROJECT_CONVERSATION_MEDIA_MAX_TOTAL_BYTES:
            raise HTTPException(status_code=400, detail={
                "code": "conversation_media_total_too_large",
                "message": f"对话参考素材总量超过 {PROJECT_CONVERSATION_MEDIA_MAX_TOTAL_BYTES} 字节上限",
            })


async def project_conversation_adapter(request: Dict[str, Any]) -> Dict[str, Any]:
    """Run one project conversation turn through the injectable chat seam.

    The project node owns the durable messages and references.  The adapter
    receives only the current node's messages plus media explicitly connected
    to it, so the legacy global conversation store is never consulted.
    """
    project_id = str(request.get("project_id") or "").strip()
    references = [item for item in request.get("references") or [] if isinstance(item, dict)]
    project_conversation_media_budget(project_id, references)
    messages = [item for item in request.get("messages") or [] if isinstance(item, dict)]
    current_message = str(request.get("message") or "")
    current_message_id = str(request.get("current_message_id") or "")
    history_messages = messages
    if current_message_id:
        current_index = next(
            (index for index, item in enumerate(messages) if str(item.get("id") or "") == current_message_id),
            None,
        )
        if current_index is not None:
            history_messages = messages[:current_index]
    elif messages and messages[-1].get("role") == "user" and str(messages[-1].get("text") or "") == current_message:
        history_messages = messages[:-1]
    llm_messages = [
        {"role": item.get("role"), "content": item.get("text") or ""}
        for item in history_messages
        if item.get("role") in {"user", "assistant"} and str(item.get("text") or "").strip()
    ]
    scope_token = PROJECT_MEDIA_ADAPTER_PROJECT.set(project_id)
    try:
        images = [
            reference_to_data_url(
                {"url": project_media_adapter_reference(project_id, item["asset_id"])},
                max_size=1536, max_bytes=PROJECT_CONVERSATION_MEDIA_MAX_FILE_BYTES,
            )
            for item in references
            if str(item.get("kind") or "").strip().lower() == "image" and item.get("asset_id")
        ]
        videos = [
            media_adapter_data_url(
                project_media_adapter_reference(project_id, item["asset_id"]), "video",
                max_bytes=PROJECT_CONVERSATION_MEDIA_MAX_FILE_BYTES,
            )
            for item in references
            if str(item.get("kind") or "").strip().lower() == "video" and item.get("asset_id")
        ]
        audio_references = [
            {
                "asset_id": str(item["asset_id"]).strip(),
                "kind": "audio",
                "name": str(item.get("name") or item["asset_id"]).strip()[:160],
                "url": media_adapter_data_url(
                    project_media_adapter_reference(project_id, item["asset_id"]), "audio",
                    max_bytes=PROJECT_CONVERSATION_MEDIA_MAX_FILE_BYTES,
                ),
            }
            for item in references
            if str(item.get("kind") or "").strip().lower() == "audio" and item.get("asset_id")
        ]
    finally:
        PROJECT_MEDIA_ADAPTER_PROJECT.reset(scope_token)
    payload = CanvasLLMRequest(
        message=current_message,
        system_prompt=str(request.get("system_prompt") or ""),
        model=str(request.get("model") or ""),
        messages=llm_messages,
        provider=str(request.get("provider") or "comfly"),
        ms_model=str(request.get("ms_model") or ""),
        images=images,
        videos=videos,
        audios=[item["url"] for item in audio_references],
        audio_references=audio_references,
    )
    result = await canvas_llm(payload)
    return {
        "text": str(result.get("text") or "接口返回了空回复。"),
        "model": result.get("model") or str(request.get("model") or ""),
        "raw_usage": result.get("raw_usage"),
    }


# --- 对话管理 ---

@app.get("/api/conversations")
async def conversations(request: Request, x_user_id: str = Header(default="")):
    user_id = safe_user_id(x_user_id, request)
    return {"user_id": user_id, "conversations": list_conversations(user_id)}

@app.post("/api/conversations")
async def create_conversation(payload: ConversationCreateRequest, request: Request, x_user_id: str = Header(default="")):
    user_id = safe_user_id(x_user_id, request)
    return {"conversation": new_conversation(user_id, payload.title)}

@app.get("/api/conversations/{conversation_id}")
async def get_conversation(conversation_id: str, request: Request, x_user_id: str = Header(default="")):
    user_id = safe_user_id(x_user_id, request)
    return {"conversation": load_conversation(user_id, conversation_id)}

@app.delete("/api/conversations/{conversation_id}")
async def delete_conversation(conversation_id: str, request: Request, x_user_id: str = Header(default="")):
    user_id = safe_user_id(x_user_id, request)
    path = conversation_path(user_id, conversation_id)
    if os.path.exists(path):
        os.remove(path)
    return {"ok": True}

# --- 画布管理 ---

@app.get("/api/canvases")
async def canvases():
    return {"canvases": list_canvases()}

@app.get("/api/projects")
async def get_projects(request: Request):
    ensure_same_origin_request(request)
    return {"projects": await asyncio.to_thread(project_store_call, "list_projects")}

@app.get("/api/project-library")
async def get_project_library(request: Request):
    ensure_same_origin_request(request)
    return {"projects": await asyncio.to_thread(project_store_call, "list_projects")}

@app.post("/api/project-library")
async def create_firefly_project(payload: FireflyProjectCreateRequest):
    project = await asyncio.to_thread(project_store_call, "create_project", payload.name)
    return {"project": project}

@app.post("/api/project-library/import")
async def import_firefly_project(request: Request):
    ensure_same_origin_request(request)
    form = None
    await acquire_fly_import_slot()
    try:
        parser = LimitedFlyMultipartParser(
            request.headers,
            request.stream(),
            max_files=1,
            max_fields=0,
            max_file_size=FLY_MAX_PACKAGE_BYTES,
        )
        form = await parser.parse()
        file = form.get("file")
        if not isinstance(file, StarletteUploadFile):
            raise HTTPException(status_code=400, detail="缺少 Fly 项目包")
        package_handle = getattr(file.file, "_file", file.file)
        project = await asyncio.to_thread(project_store_call, "import_fly", package_handle)
    except (KeyError, MultiPartException, ValueError) as exc:
        status_code = 413 if "20GB" in str(exc) else 400
        raise HTTPException(status_code=status_code, detail=str(exc)) from exc
    except ProjectStoreError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    finally:
        try:
            if form is not None:
                await form.close()
        finally:
            FLY_IMPORT_UPLOAD_LOCK.release()
    return {"project": project}

def project_media_source_path(source_url: str) -> Optional[str]:
    text = str(source_url or "").strip()
    if text.startswith(("/output/", "/assets/", "/api/storage-files/")):
        return output_file_from_url(text)
    return None

def reserve_project_media_import(project_id: str, source_key: str):
    with project_lifecycle_lock(project_id):
        project_store_call("open_project", project_id)
        executor = ensure_project_media_import_executor()
        with PROJECT_MEDIA_IMPORT_TASKS_LOCK:
            cutoff = now_ms() - 60 * 60 * 1000
            for stale_id in [
                key for key, value in PROJECT_MEDIA_IMPORT_TASKS.items()
                if value.get("status") in {"completed", "failed"} and int(value.get("updated_at") or 0) < cutoff
            ]:
                PROJECT_MEDIA_IMPORT_TASKS.pop(stale_id, None)
                PROJECT_MEDIA_IMPORT_FUTURES.pop(stale_id, None)
            duplicate = next((
                value for value in PROJECT_MEDIA_IMPORT_TASKS.values()
                if value.get("project_id") == project_id and value.get("source_key") == source_key and value.get("status") == "pending"
            ), None)
            if duplicate is not None:
                return executor, duplicate, False
            pending = [value for value in PROJECT_MEDIA_IMPORT_TASKS.values() if value.get("status") == "pending"]
            if len(pending) >= PROJECT_MEDIA_IMPORT_MAX_PENDING:
                raise HTTPException(status_code=429, detail="媒体导入队列已满，请稍后重试")
            if sum(1 for value in pending if value.get("project_id") == project_id) >= PROJECT_MEDIA_IMPORT_MAX_PER_PROJECT:
                raise HTTPException(status_code=429, detail="当前项目的媒体导入队列已满")
            task_id = uuid.uuid4().hex
            task = {
                "id": task_id,
                "project_id": project_id,
                "source_key": source_key,
                "status": "pending",
                "created_at": now_ms(),
                "updated_at": now_ms(),
            }
            PROJECT_MEDIA_IMPORT_TASKS[task_id] = task
            return executor, task, True

def validate_project_media_source(source_path: str, expected_kind: str, declared_mime: str) -> str:
    """Return a verified MIME for media that has an explicit expected kind."""
    kind = str(expected_kind or "").strip().lower()
    if not kind:
        return declared_mime
    if kind == "image":
        _width, _height, verified_mime = validate_generated_image(source_path)
        return verified_mime
    if kind == "video":
        _width, _height, verified_mime = validate_generated_video(source_path)
        return verified_mime
    if kind == "audio":
        mime = str(declared_mime or content_type_for_path(source_path)).split(";", 1)[0].strip().lower()
        if not mime.startswith("audio/"):
            raise ProjectStoreError("媒体内容不是有效音频")
        return mime
    raise ProjectStoreError("媒体预期类型不受支持")

@app.post("/api/project-library/{project_id}/media/import")
async def import_project_media(project_id: str, payload: ProjectMediaImportRequest, request: Request):
    ensure_same_origin_request(request)
    expected_kind = str(payload.expected_kind or "").strip().lower()
    if expected_kind and expected_kind not in {"image", "video", "audio"}:
        raise HTTPException(status_code=400, detail="媒体预期类型不受支持")
    source_path = project_media_source_path(payload.source_url)
    if not source_path:
        raise HTTPException(status_code=400, detail="媒体必须先成为本机输出或上传文件")
    source_key = f"{os.path.normcase(os.path.realpath(source_path))}\n{expected_kind}"
    try:
        executor, task, created = await asyncio.to_thread(reserve_project_media_import, project_id, source_key)
    except ProjectNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    if not created:
        return JSONResponse(status_code=202, content={"task": public_project_media_import_task(task)})
    task_id = task["id"]

    media_name = payload.name or os.path.basename(source_path)
    media_type = payload.mime_type or content_type_for_path(source_path)
    source_locator = str(payload.source_locator or payload.source_url or "").strip()
    source_type = "remote_url" if source_locator.startswith(("http://", "https://")) else "external_path" if source_locator.startswith("file:") else "local_output"
    def archive_media():
        try:
            verified_media_type = validate_project_media_source(source_path, expected_kind, media_type)
            record = store.import_media_file(
                project_id,
                source_path,
                media_name,
                verified_media_type,
                source_info={"locator": source_locator, "name": media_name, "type": source_type},
            )
            update = {"status": "completed", "media": record}
        except ProjectStoreError as exc:
            update = {"status": "failed", "error": str(exc)}
        except Exception:
            update = {"status": "failed", "error": "项目媒体归档失败"}
        with PROJECT_MEDIA_IMPORT_TASKS_LOCK:
            current = PROJECT_MEDIA_IMPORT_TASKS.get(task_id)
            if current is not None:
                current.update(update)
                current["updated_at"] = now_ms()
            PROJECT_MEDIA_IMPORT_FUTURES.pop(task_id, None)

    with PROJECT_LIBRARY_SWITCH_LOCK:
        store = FIREFLY_PROJECT_STORE
        try:
            future = executor.submit(archive_media)
        except RuntimeError as exc:
            with PROJECT_MEDIA_IMPORT_TASKS_LOCK:
                PROJECT_MEDIA_IMPORT_TASKS.pop(task_id, None)
            raise HTTPException(status_code=503, detail="媒体导入服务正在关闭") from exc
        with PROJECT_MEDIA_IMPORT_TASKS_LOCK:
            PROJECT_MEDIA_IMPORT_FUTURES[task_id] = future
    def forget_future(done_future: Future):
        with PROJECT_MEDIA_IMPORT_TASKS_LOCK:
            if PROJECT_MEDIA_IMPORT_FUTURES.get(task_id) is done_future:
                PROJECT_MEDIA_IMPORT_FUTURES.pop(task_id, None)
    future.add_done_callback(forget_future)
    return JSONResponse(status_code=202, content={"task": public_project_media_import_task(task)})

@app.get("/api/project-library/{project_id}/media/import/{task_id}")
async def project_media_import_status(project_id: str, task_id: str):
    with PROJECT_MEDIA_IMPORT_TASKS_LOCK:
        task = PROJECT_MEDIA_IMPORT_TASKS.get(task_id)
        if not task or task.get("project_id") != project_id:
            raise HTTPException(status_code=404, detail="媒体导入任务不存在")
        return {"task": public_project_media_import_task(task)}

@app.get("/api/project-library/{project_id}/media")
async def list_project_media(project_id: str):
    try:
        records = await asyncio.to_thread(project_store_call, "list_media", project_id)
    except ProjectNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    items = []
    for record in records:
        asset_id = record["asset_id"]
        base = f"/api/project-library/{urllib.parse.quote(project_id, safe='')}/media/{urllib.parse.quote(asset_id, safe='')}"
        item = dict(record)
        item["original_url"] = base
        item["preview_url"] = f"{base}/preview" if item.get("kind") in {"image", "video"} else ""
        items.append(item)
    return {"items": items}

def build_project_media_preview(project_id: str, asset_id: str, width: int) -> str:
    record = project_store_call("media_record", project_id, asset_id)
    if record.get("kind") not in {"image", "video"}:
        raise ProjectStoreError("该媒体没有图像预览")
    source = Path(record["path"])
    preview_root = source.parent.parent / "previews"
    preview_root.mkdir(parents=True, exist_ok=True)
    preview_path = preview_root / f"{asset_id}-{width}.webp"
    if preview_path.is_file():
        return str(preview_path)
    image = generate_video_preview_image(str(source), width) if record.get("kind") == "video" else Image.open(source)
    try:
        image.thumbnail((width, width), Image.Resampling.LANCZOS)
        if image.mode not in {"RGB", "RGBA"}:
            converted = image.convert("RGBA" if "transparency" in image.info else "RGB")
            image.close()
            image = converted
        temporary = preview_root / f".{asset_id}-{width}.{uuid.uuid4().hex}.tmp"
        try:
            image.save(temporary, format="WEBP", quality=82, method=4)
            os.replace(temporary, preview_path)
        finally:
            temporary.unlink(missing_ok=True)
    finally:
        image.close()
    return str(preview_path)

@app.get("/api/project-library/{project_id}/media/{asset_id}/preview")
async def get_project_media_preview(project_id: str, asset_id: str, w: int = 512):
    width = max(64, min(2048, int(w or 512)))
    try:
        path = await asyncio.to_thread(build_project_media_preview, project_id, asset_id, width)
    except ProjectNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except (ProjectStoreError, OSError, ValueError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return FileResponse(path, media_type="image/webp")

def copy_project_media_to_global_library(project_id: str, asset_id: str) -> Dict[str, Any]:
    record = project_store_call("media_record", project_id, asset_id)
    kind, extension = _local_upload_kind_ext(str(record.get("name") or record.get("filename") or ""), str(record.get("mime_type") or ""))
    if kind not in {"image", "video", "audio"}:
        raise ProjectStoreError("该媒体类型不能保存到全局资源库")
    base = os.path.splitext(os.path.basename(str(record.get("name") or "project-media")))[0]
    base = re.sub(r"[^0-9A-Za-z一-鿿._-]+", "_", base).strip("_") or "project-media"
    filename = f"up_{uuid.uuid4().hex[:12]}_{base[:60]}{extension}"
    os.makedirs(LOCAL_UPLOAD_DIR, exist_ok=True)
    destination = Path(LOCAL_UPLOAD_DIR) / filename
    temporary = Path(LOCAL_UPLOAD_DIR) / f".{filename}.{uuid.uuid4().hex}.tmp"
    try:
        with open(record["path"], "rb") as source_handle, temporary.open("wb") as destination_handle:
            shutil.copyfileobj(source_handle, destination_handle, length=1024 * 1024)
            destination_handle.flush()
            os.fsync(destination_handle.fileno())
        os.replace(temporary, destination)
    finally:
        temporary.unlink(missing_ok=True)
    return _local_upload_item(filename)

@app.post("/api/project-library/{project_id}/media/{asset_id}/copy-to-global")
async def copy_project_media_to_global(project_id: str, asset_id: str):
    try:
        item = await asyncio.to_thread(copy_project_media_to_global_library, project_id, asset_id)
    except ProjectNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except (ProjectStoreError, OSError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"item": item, "files": [item], "count": 1}

def project_has_active_media_import(project_id: str) -> bool:
    with PROJECT_MEDIA_IMPORT_TASKS_LOCK:
        return any(
            task.get("project_id") == project_id and task.get("status") == "pending"
            for task in PROJECT_MEDIA_IMPORT_TASKS.values()
        )

def project_has_active_image_generation(project_id: str) -> bool:
    with CANVAS_TASK_LOCK:
        return any(
            task.get("type") == "project-online-image"
            and task.get("project_id") == project_id
            and task.get("status") in {"queued", "running"}
            for task in CANVAS_TASKS.values()
        )

def project_has_active_video_generation(project_id: str) -> bool:
    with CANVAS_TASK_LOCK:
        return any(
            task.get("type") == "project-online-video"
            and task.get("project_id") == project_id
            and task.get("status") in {"queued", "running"}
            for task in CANVAS_TASKS.values()
        )

def canvas_has_pending_conversation(canvas: Dict[str, Any]) -> bool:
    return any(
        node.get("type") == "smart-conversation"
        and any(
            isinstance(message, dict) and message.get("role") == "user" and message.get("status") == "pending"
            for message in (node.get("messages") or [])
        )
        for node in (canvas.get("nodes") or []) if isinstance(node, dict)
    )

def project_has_recoverable_conversation_sidecars(
    store: ProjectStore,
    project_dir: Path,
    project_id: str,
    canvas: Dict[str, Any],
) -> bool:
    sidecar_root = store._safe_project_path(project_dir, ".conversation-tasks")
    if not sidecar_root.is_dir():
        return False
    active = False
    for entry in list(sidecar_root.iterdir()):
        try:
            path = store._safe_project_path(project_dir, f".conversation-tasks/{entry.name}")
            # save_project_conversation_durable writes through a sibling
            # ``*.tmp`` and removes it in normal operation.  A crash can leave
            # that partial file behind; it is not an official recovery record
            # and must not block export/trash or be parsed as JSON.
            if entry.name.lower().endswith(".tmp"):
                if path.is_file():
                    path.unlink()
                elif path.exists():
                    active = True
                continue
            if not path.is_file():
                active = True
                continue
            payload = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(payload, dict) or str(payload.get("project_id") or "") != str(project_id):
                active = True
                continue
            node_id = str(payload.get("node_id") or "")
            request_id = str(payload.get("request_id") or "")
            ready = payload.get("ready")
            if payload.get("state") != "ready" or not node_id or not request_id or not isinstance(ready, dict):
                active = True
                continue
            snapshot = conversation_task_snapshot(canvas, node_id, request_id)
            if not snapshot or snapshot.get("status") not in {"completed", "failed"}:
                active = True
                continue
            expected_user_id = str(ready.get("user_message_id") or "")
            actual_user_id = str((snapshot.get("message") or {}).get("id") or "")
            if expected_user_id and expected_user_id != actual_user_id:
                active = True
                continue
            ready_assistant = ready.get("assistant")
            actual_assistant = snapshot.get("assistant")
            if isinstance(ready_assistant, dict):
                if not isinstance(actual_assistant, dict) or any(
                    str(ready_assistant.get(key) or "") != str(actual_assistant.get(key) or "")
                    for key in ("id", "request_id")
                ):
                    active = True
                    continue
            elif not ready.get("error") or snapshot.get("status") != "failed":
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

def project_has_active_conversation(project_id: str, *, trashed: bool = False) -> bool:
    """Conservatively block lifecycle moves while a paid turn can still be recovered."""
    if not trashed:
        with PROJECT_CONVERSATION_TASKS_LOCK:
            if any(key[0] == str(project_id) and not handle.done() for key, handle in PROJECT_CONVERSATION_TASKS.items()):
                return True
    with PROJECT_LIBRARY_SWITCH_LOCK:
        store = FIREFLY_PROJECT_STORE
    if store is None:
        return False
    project_dir = (Path(store.project_trash_root) if trashed else Path(store.root)) / str(project_id)
    if not project_dir.is_dir():
        return False
    try:
        if trashed:
            canvas = json.loads(store._safe_project_path(project_dir, "canvas.json").read_text(encoding="utf-8"))
        else:
            canvas = store.open_project(project_id)["canvas"]
        return (
            project_has_recoverable_conversation_sidecars(store, project_dir, project_id, canvas)
            or canvas_has_pending_conversation(canvas)
        )
    except (ProjectStoreError, OSError, ValueError, json.JSONDecodeError):
        return True

def export_project_package(project_id: str, path: str):
    with project_lifecycle_lock(project_id):
        if project_has_active_media_import(project_id):
            raise ProjectMediaImportActive("项目仍有媒体正在导入，请稍后再导出")
        if project_has_active_image_generation(project_id):
            raise ProjectMediaImportActive("项目仍有图片正在生成，请稍后再导出")
        if project_has_active_video_generation(project_id):
            raise ProjectMediaImportActive("项目仍有视频正在生成，请稍后再导出")
        if project_has_active_conversation(project_id):
            raise ProjectMediaImportActive("项目仍有对话任务待完成或待恢复，请稍后再导出")
        with PROJECT_LIBRARY_SWITCH_LOCK:
            store = FIREFLY_PROJECT_STORE
        return store.export_fly_to(project_id, path)

def project_lifecycle_store_call(method_name: str, project_id: str, *, block_active_import: bool = False):
    with project_lifecycle_lock(project_id):
        if block_active_import and project_has_active_media_import(project_id):
            raise ProjectMediaImportActive("项目仍有媒体正在导入，请等待完成后再删除")
        if block_active_import and project_has_active_image_generation(project_id):
            raise ProjectMediaImportActive("项目仍有图片正在生成，请等待完成后再删除")
        if block_active_import and project_has_active_video_generation(project_id):
            raise ProjectMediaImportActive("项目仍有视频正在生成，请等待完成后再删除")
        if block_active_import and project_has_active_conversation(
            project_id, trashed=method_name == "purge_trashed_project",
        ):
            raise ProjectMediaImportActive("项目仍有对话任务待完成或待恢复，不能删除")
        return project_store_call(method_name, project_id)

@app.get("/api/project-library/{project_id}/export")
async def export_firefly_project(project_id: str, request: Request):
    ensure_same_origin_request(request)
    descriptor, path = tempfile.mkstemp(prefix="firefly-export-", suffix=".fly")
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
    return FileResponse(
        path,
        media_type="application/zip",
        filename=filename,
        background=BackgroundTask(lambda: Path(path).unlink(missing_ok=True)),
    )

@app.get("/api/project-library/trash")
async def list_project_trash(request: Request):
    ensure_same_origin_request(request)
    return {"projects": await asyncio.to_thread(project_store_call, "list_trashed_projects")}

@app.post("/api/project-library/trash/{project_id}/restore")
async def restore_trashed_project(project_id: str, request: Request):
    ensure_same_origin_request(request)
    try:
        project = await asyncio.to_thread(project_lifecycle_store_call, "restore_project", project_id)
    except ProjectStoreError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return {"project": project}

@app.delete("/api/project-library/trash/{project_id}")
async def purge_trashed_project(project_id: str, request: Request):
    ensure_same_origin_request(request)
    try:
        return await asyncio.to_thread(
            project_lifecycle_store_call,
            "purge_trashed_project",
            project_id,
            block_active_import=True,
        )
    except ProjectMediaImportActive as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except ProjectStoreError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

@app.delete("/api/project-library/{project_id}")
async def trash_firefly_project(project_id: str, request: Request):
    ensure_same_origin_request(request)
    try:
        return await asyncio.to_thread(
            project_lifecycle_store_call,
            "trash_project",
            project_id,
            block_active_import=True,
        )
    except ProjectMediaImportActive as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except ProjectNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ProjectStoreError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc

@app.get("/api/project-library/{project_id}/media/{asset_id}")
async def get_project_media(project_id: str, asset_id: str):
    try:
        record = await asyncio.to_thread(project_store_call, "media_record", project_id, asset_id)
    except ProjectNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ProjectStoreError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return FileResponse(record["path"], media_type=record.get("mime_type") or "application/octet-stream")

@app.get("/api/project-library/{project_id}")
async def open_firefly_project(project_id: str):
    try:
        return await asyncio.to_thread(project_store_call, "open_project", project_id)
    except ProjectNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ProjectStoreError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


def _photoshop_protocol_error(status: int, code: str, message: str) -> HTTPException:
    return HTTPException(status_code=status, detail={"code": code, "message": message})


def _photoshop_prune_requests_locked(now: Optional[int] = None) -> None:
    now_value = int(now if now is not None else now_ms())
    for request_id, record in list(PHOTOSHOP_ROUND_TRIP_REQUESTS.items()):
        expires_at = int(record.get("expires_at_ms") or 0)
        if record.get("state") == "pending" and now_value >= expires_at:
            record["state"] = "expired"
        terminal_at = int(record.get("returned_at") or 0) * 1000 or expires_at
        if record.get("state") in {"returned", "expired"} and now_value - terminal_at > PHOTOSHOP_ROUND_TRIP_RETENTION_MS:
            PHOTOSHOP_ROUND_TRIP_REQUESTS.pop(request_id, None)
    if len(PHOTOSHOP_ROUND_TRIP_REQUESTS) <= PHOTOSHOP_ROUND_TRIP_MAX_REQUESTS:
        return
    terminal = sorted(
        ((int(record.get("returned_at") or 0) * 1000 or int(record.get("expires_at_ms") or 0), request_id)
         for request_id, record in PHOTOSHOP_ROUND_TRIP_REQUESTS.items()
         if record.get("state") in {"returned", "expired"}),
    )
    for _, request_id in terminal[:max(0, len(PHOTOSHOP_ROUND_TRIP_REQUESTS) - PHOTOSHOP_ROUND_TRIP_MAX_REQUESTS)]:
        PHOTOSHOP_ROUND_TRIP_REQUESTS.pop(request_id, None)


def _ensure_photoshop_loopback(request: Request) -> None:
    # Trust the transport peer, never the user-controlled Host header.  The URL
    # hostname is only a fallback for ASGI test doubles that omit ``client``.
    client_host = str(getattr(getattr(request, "client", None), "host", "") or "").strip().lower()
    host = client_host or str(getattr(request.url, "hostname", "") or "").strip().lower()
    if host not in {"127.0.0.1", "::1", "localhost"}:
        raise _photoshop_protocol_error(403, "LOOPBACK_ONLY", "Photoshop 连接器仅允许本机 127.0.0.1")


def _photoshop_request_snapshot(request_id: str) -> Optional[Dict[str, Any]]:
    with PHOTOSHOP_ROUND_TRIP_LOCK:
        _photoshop_prune_requests_locked()
        record = PHOTOSHOP_ROUND_TRIP_REQUESTS.get(str(request_id or ""))
        if not record:
            return None
        result = copy.deepcopy(record)
        if result.get("state") == "pending" and now_ms() >= int(result.get("expires_at_ms") or 0):
            record["state"] = "expired"
            result["state"] = "expired"
        # ``processing`` is an internal concurrency guard.  Keep the public
        # protocol's state vocabulary limited to pending/returned/expired.
        if result.get("state") == "processing":
            result["state"] = "pending"
        result.pop("expires_at_ms", None)
        return result


def _photoshop_public_request(record: Dict[str, Any]) -> Dict[str, Any]:
    result = copy.deepcopy(record)
    if result.get("state") == "processing":
        result["state"] = "pending"
    result.pop("expires_at_ms", None)
    return result


def _photoshop_mark_returned(request_id: str, committed: Dict[str, Any]) -> Dict[str, Any]:
    """Publish the one-time result after durable project commit."""
    with PHOTOSHOP_ROUND_TRIP_LOCK:
        current = PHOTOSHOP_ROUND_TRIP_REQUESTS.get(str(request_id or ""))
        if not current:
            raise _photoshop_protocol_error(404, "REQUEST_NOT_FOUND", "Photoshop 往返请求不存在")
        current.update({
            "state": "returned",
            "returned_at": now_ms() // 1000,
            "result_node_id": committed["result_node"]["id"],
            "result_media_id": committed["media"]["asset_id"],
        })
        return _photoshop_public_request(current)


def _photoshop_reset_processing(request_id: str) -> None:
    with PHOTOSHOP_ROUND_TRIP_LOCK:
        current = PHOTOSHOP_ROUND_TRIP_REQUESTS.get(str(request_id or ""))
        if current and current.get("state") == "processing":
            current["state"] = "pending"


def _photoshop_validate_display_name(value: str) -> str:
    name = str(value or "").strip()
    if not name or name in {".", ".."} or any(char in name for char in ("/", "\\", "\x00", ":")):
        raise _photoshop_protocol_error(400, "INVALID_RETURN_PATH", "返回文件名必须是纯显示名称")
    if Path(name).name != name:
        raise _photoshop_protocol_error(400, "INVALID_RETURN_PATH", "返回文件名不能包含路径")
    return name[:240]


def _photoshop_validate_media_type(value: str) -> str:
    mime_type = str(value or "").split(";", 1)[0].strip().lower()
    if not re.fullmatch(r"image/[a-z0-9.+-]+", mime_type):
        raise _photoshop_protocol_error(400, "INVALID_IDENTIFIER", "Photoshop 返回必须是图片媒体")
    return mime_type


@app.post("/api/project-library/{project_id}/photoshop/round-trips")
@app.post("/api/project-library/{project_id}/photoshop-round-trips")
async def create_photoshop_round_trip(
    project_id: str,
    payload: PhotoshopRoundTripCreateRequest,
    request: Request,
):
    _ensure_photoshop_loopback(request)
    try:
        opened = await asyncio.to_thread(project_store_call, "open_project", project_id)
        source = conversation_node_by_id(opened["canvas"], payload.source_node_id)
        if not source:
            raise _photoshop_protocol_error(404, "SOURCE_NODE_NOT_FOUND", "来源节点不存在")
        source_assets = {
            str(image.get("asset_id") or "")
            for image in source.get("images") or []
            if isinstance(image, dict) and image.get("asset_id")
        }
        if payload.source_media_id not in source_assets:
            raise _photoshop_protocol_error(409, "SOURCE_NODE_MISMATCH", "来源媒体不属于来源节点")
        media = await asyncio.to_thread(project_store_call, "media_record", project_id, payload.source_media_id)
        if media.get("kind") != "image":
            raise _photoshop_protocol_error(400, "INVALID_IDENTIFIER", "Photoshop 往返来源必须是图片")
        created_at_ms = now_ms()
        request_id = uuid.uuid4().hex
        record = {
            "request_id": request_id,
            "project_id": str(project_id),
            "source_node_id": payload.source_node_id,
            "source_media_id": payload.source_media_id,
            "created_at": created_at_ms // 1000,
            "expires_at": (created_at_ms + payload.ttl_seconds * 1000) // 1000,
            "expires_at_ms": created_at_ms + payload.ttl_seconds * 1000,
            "state": "pending",
        }
        with PHOTOSHOP_ROUND_TRIP_LOCK:
            _photoshop_prune_requests_locked(created_at_ms)
            PHOTOSHOP_ROUND_TRIP_REQUESTS[request_id] = copy.deepcopy(record)
        await manager.broadcast_photoshop_round_trip(_photoshop_public_request(record))
        return _photoshop_public_request(record)
    except HTTPException:
        raise
    except ProjectNotFound as exc:
        raise _photoshop_protocol_error(404, "PROJECT_NOT_FOUND", str(exc)) from exc
    except ProjectStoreError as exc:
        raise _photoshop_protocol_error(400, "INVALID_IDENTIFIER", str(exc)) from exc


@app.get("/api/project-library/{project_id}/photoshop/round-trips/{request_id}")
@app.get("/api/project-library/{project_id}/photoshop-round-trips/{request_id}")
async def get_photoshop_round_trip(project_id: str, request_id: str, request: Request):
    _ensure_photoshop_loopback(request)
    record = _photoshop_request_snapshot(request_id)
    if record is None:
        raise _photoshop_protocol_error(404, "REQUEST_NOT_FOUND", "Photoshop 往返请求不存在")
    if str(record.get("project_id")) != str(project_id):
        raise _photoshop_protocol_error(409, "PROJECT_MISMATCH", "Photoshop 往返项目不匹配")
    return record


@app.get("/api/project-library/{project_id}/photoshop/round-trips/{request_id}/source")
@app.get("/api/project-library/{project_id}/photoshop-round-trips/{request_id}/source")
async def get_photoshop_round_trip_source(project_id: str, request_id: str, request: Request):
    _ensure_photoshop_loopback(request)
    record = _photoshop_request_snapshot(request_id)
    if record is None:
        raise _photoshop_protocol_error(404, "REQUEST_NOT_FOUND", "Photoshop 往返请求不存在")
    if str(record.get("project_id")) != str(project_id):
        raise _photoshop_protocol_error(409, "PROJECT_MISMATCH", "Photoshop 往返项目不匹配")
    if record.get("state") == "expired":
        raise _photoshop_protocol_error(410, "REQUEST_EXPIRED", "Photoshop 往返请求已过期")
    try:
        media = await asyncio.to_thread(project_store_call, "media_record", project_id, record["source_media_id"])
        if Path(media["path"]).stat().st_size > PHOTOSHOP_ROUND_TRIP_MAX_MEDIA_BYTES:
            raise _photoshop_protocol_error(413, "MEDIA_TOO_LARGE", "Photoshop 往返图片超过 128MB 限制")
        content = await asyncio.to_thread(Path(media["path"]).read_bytes)
        actual_sha256 = hashlib.sha256(content).hexdigest()
        if media.get("sha256") and str(media.get("sha256")).lower() != actual_sha256:
            raise _photoshop_protocol_error(409, "SOURCE_NODE_MISMATCH", "来源媒体校验失败")
        return {
            **{key: record[key] for key in ("project_id", "source_node_id", "source_media_id", "request_id", "created_at", "expires_at")},
            "name": media.get("name") or media.get("filename") or record["source_media_id"],
            "mime_type": media.get("mime_type") or "image/png",
            "sha256": actual_sha256,
            "bytes_base64": base64.b64encode(content).decode("ascii"),
        }
    except (ProjectNotFound, OSError) as exc:
        raise _photoshop_protocol_error(404, "SOURCE_NODE_NOT_FOUND", str(exc)) from exc
    except ProjectStoreError as exc:
        raise _photoshop_protocol_error(400, "INVALID_IDENTIFIER", str(exc)) from exc


@app.post("/api/project-library/{project_id}/photoshop/round-trips/{request_id}/return")
@app.post("/api/project-library/{project_id}/photoshop-round-trips/{request_id}/return")
async def return_photoshop_round_trip(
    project_id: str,
    request_id: str,
    payload: PhotoshopRoundTripReturnRequest,
    request: Request,
):
    _ensure_photoshop_loopback(request)
    now = now_ms()
    with PHOTOSHOP_ROUND_TRIP_LOCK:
        _photoshop_prune_requests_locked(now)
        record = PHOTOSHOP_ROUND_TRIP_REQUESTS.get(str(request_id or ""))
        if not record:
            raise _photoshop_protocol_error(404, "REQUEST_NOT_FOUND", "Photoshop 往返请求不存在")
        if str(record.get("project_id")) != str(project_id) or payload.project_id != str(project_id):
            raise _photoshop_protocol_error(409, "PROJECT_MISMATCH", "Photoshop 往返项目不匹配")
        if payload.request_id != request_id:
            raise _photoshop_protocol_error(409, "REQUEST_CONFLICT", "返回请求标识不匹配")
        if payload.source_node_id != str(record.get("source_node_id")):
            raise _photoshop_protocol_error(409, "SOURCE_NODE_MISMATCH", "Photoshop 往返来源节点不匹配")
        if record.get("state") == "returned" or record.get("state") == "processing":
            raise _photoshop_protocol_error(409, "DUPLICATE_RETURN", "Photoshop 往返请求已经消费")
        if now >= int(record.get("expires_at_ms") or 0):
            record["state"] = "expired"
            raise _photoshop_protocol_error(410, "REQUEST_EXPIRED", "Photoshop 往返请求已过期")

    # Validate connector display metadata before claiming the one-time request.
    # A rejected path must leave the binding pending for a corrected retry,
    # while the lookup/order above still returns REQUEST_NOT_FOUND first.
    name = _photoshop_validate_display_name(payload.name)
    mime_type = _photoshop_validate_media_type(payload.mime_type)
    expected_suffix = mimetypes.guess_extension(mime_type) or ""
    if expected_suffix and Path(name).suffix.lower() != expected_suffix.lower():
        name = f"{Path(name).stem}{expected_suffix}"
    with PHOTOSHOP_ROUND_TRIP_LOCK:
        record = PHOTOSHOP_ROUND_TRIP_REQUESTS.get(str(request_id or ""))
        if not record:
            raise _photoshop_protocol_error(404, "REQUEST_NOT_FOUND", "Photoshop 往返请求不存在")
        if record.get("state") == "returned" or record.get("state") == "processing":
            raise _photoshop_protocol_error(409, "DUPLICATE_RETURN", "Photoshop 往返请求已经消费")
        if now_ms() >= int(record.get("expires_at_ms") or 0):
            record["state"] = "expired"
            raise _photoshop_protocol_error(410, "REQUEST_EXPIRED", "Photoshop 往返请求已过期")
        record["state"] = "processing"

    encoded = str(payload.bytes_base64 or payload.data or "").strip()
    if not encoded:
        with PHOTOSHOP_ROUND_TRIP_LOCK:
            PHOTOSHOP_ROUND_TRIP_REQUESTS[request_id]["state"] = "pending"
        raise _photoshop_protocol_error(422, "EMPTY_MEDIA", "未提供返回媒体")
    try:
        content = base64.b64decode(encoded, validate=True)
    except (ValueError, TypeError, base64.binascii.Error) as exc:
        with PHOTOSHOP_ROUND_TRIP_LOCK:
            PHOTOSHOP_ROUND_TRIP_REQUESTS[request_id]["state"] = "pending"
        raise _photoshop_protocol_error(422, "EMPTY_MEDIA", "返回媒体编码无效") from exc
    if not content:
        with PHOTOSHOP_ROUND_TRIP_LOCK:
            PHOTOSHOP_ROUND_TRIP_REQUESTS[request_id]["state"] = "pending"
        raise _photoshop_protocol_error(422, "EMPTY_MEDIA", "返回媒体为空")
    if len(content) > PHOTOSHOP_ROUND_TRIP_MAX_MEDIA_BYTES:
        with PHOTOSHOP_ROUND_TRIP_LOCK:
            PHOTOSHOP_ROUND_TRIP_REQUESTS[request_id]["state"] = "pending"
        raise _photoshop_protocol_error(413, "MEDIA_TOO_LARGE", "Photoshop 返回图片超过 128MB 限制")
    declared = str(payload.sha256 or "").lower()
    if not re.fullmatch(r"[0-9a-f]{64}", declared) or hashlib.sha256(content).hexdigest() != declared:
        with PHOTOSHOP_ROUND_TRIP_LOCK:
            PHOTOSHOP_ROUND_TRIP_REQUESTS[request_id]["state"] = "pending"
        raise _photoshop_protocol_error(422, "MEDIA_HASH_MISMATCH", "返回媒体 SHA-256 校验失败")
    with PHOTOSHOP_ROUND_TRIP_LOCK:
        current = PHOTOSHOP_ROUND_TRIP_REQUESTS.get(request_id)
        if not current or now_ms() >= int(current.get("expires_at_ms") or 0):
            if current:
                current["state"] = "expired"
            raise _photoshop_protocol_error(410, "REQUEST_EXPIRED", "Photoshop 往返请求已过期")
    commit_task = asyncio.create_task(asyncio.to_thread(
            project_store_call,
            "complete_photoshop_round_trip",
            project_id,
            str(record["source_node_id"]),
            str(record["source_media_id"]),
            request_id,
            content,
            name,
            mime_type,
        ))
    try:
        # Shield the worker from request cancellation.  A client disconnect
        # must not turn a durable commit into an invisible `processing` state
        # or allow a retry to create a second result node.
        committed = await asyncio.shield(commit_task)
    except asyncio.CancelledError:
        try:
            committed = await asyncio.shield(commit_task)
        except asyncio.CancelledError:
            # A second cancellation still leaves the worker running; keep the
            # binding claimed until it finishes rather than falsely reopening
            # it. Reconcile either outcome from the completion callback.
            def reconcile_cancelled_commit(task):
                if task.cancelled():
                    _photoshop_reset_processing(request_id)
                    return
                try:
                    completed = task.result()
                    _photoshop_mark_returned(request_id, completed)
                    asyncio.create_task(manager.broadcast_canvas_updated(
                        project_id,
                        int(completed["canvas"].get("updated_at") or now_ms()),
                    ))
                except BaseException:
                    _photoshop_reset_processing(request_id)
            commit_task.add_done_callback(reconcile_cancelled_commit)
            raise
        except Exception:
            _photoshop_reset_processing(request_id)
            raise
        else:
            public_record = _photoshop_mark_returned(request_id, committed)
            await manager.broadcast_canvas_updated(project_id, int(committed["canvas"].get("updated_at") or now_ms()))
            raise
    except ProjectNotFound as exc:
        _photoshop_reset_processing(request_id)
        raise _photoshop_protocol_error(404, "SOURCE_NODE_NOT_FOUND", str(exc)) from exc
    except (ProjectStoreError, ValueError, TypeError) as exc:
        _photoshop_reset_processing(request_id)
        raise _photoshop_protocol_error(400, "INVALID_IDENTIFIER", str(exc)) from exc
    except Exception as exc:
        # Never strand a binding in the internal processing state if an
        # unexpected persistence/runtime failure occurs.
        _photoshop_reset_processing(request_id)
        raise _photoshop_protocol_error(500, "ROUND_TRIP_FAILED", "Photoshop 返回暂未写入项目") from exc
    public_record = _photoshop_mark_returned(request_id, committed)
    await manager.broadcast_canvas_updated(project_id, int(committed["canvas"].get("updated_at") or now_ms()))
    return {
        **public_record,
        "media": committed["media"],
        "result_node": committed["result_node"],
        "edge": committed["edge"],
        "canvas": committed["canvas"],
    }


def project_conversation_connected_references(project_id: str, canvas: Dict[str, Any], node_id: str) -> List[Dict[str, Any]]:
    """Resolve only media nodes directly connected into one conversation node."""
    nodes_by_id = {
        str(node.get("id")): node
        for node in canvas.get("nodes") or []
        if isinstance(node, dict) and str(node.get("id") or "")
    }
    source_ids = []
    for edge in canvas.get("connections") or []:
        if not isinstance(edge, dict):
            continue
        target = edge.get("to") or edge.get("target") or edge.get("toNode") or edge.get("targetNodeId")
        kind = str(edge.get("kind") or "flow")
        source = edge.get("from") or edge.get("source") or edge.get("fromNode") or edge.get("sourceNodeId")
        if str(target or "") == node_id and source and kind in {"input", "flow"}:
            source_ids.append(str(source))

    candidates: List[Tuple[str, Dict[str, Any]]] = []
    seen_assets = set()
    for source_id in source_ids:
        source = nodes_by_id.get(source_id)
        if not isinstance(source, dict):
            continue
        source_type = str(source.get("type") or "")
        if source_type not in {"", "image", "smart-image"}:
            continue
        for item in source.get("images") or []:
            if not isinstance(item, dict) or not str(item.get("asset_id") or "").strip():
                continue
            asset_id = str(item["asset_id"]).strip()
            if asset_id in seen_assets:
                continue
            seen_assets.add(asset_id)
            candidates.append((asset_id, item))
    maximum_total = (
        PROJECT_CONVERSATION_MAX_IMAGE_REFERENCES
        + PROJECT_CONVERSATION_MAX_VIDEO_REFERENCES
        + PROJECT_CONVERSATION_MAX_AUDIO_REFERENCES
    )
    if len(candidates) > maximum_total:
        raise HTTPException(status_code=400, detail={
            "code": "conversation_media_count_exceeded",
            "message": f"对话参考素材总数量超过上限 {maximum_total}",
        })
    hint_counts = {"image": 0, "video": 0, "audio": 0}
    hint_limits = {
        "image": PROJECT_CONVERSATION_MAX_IMAGE_REFERENCES,
        "video": PROJECT_CONVERSATION_MAX_VIDEO_REFERENCES,
        "audio": PROJECT_CONVERSATION_MAX_AUDIO_REFERENCES,
    }
    for _asset_id, item in candidates:
        kind = str(item.get("kind") or "").strip().lower()
        if kind in hint_counts:
            hint_counts[kind] += 1
            if hint_counts[kind] > hint_limits[kind]:
                raise HTTPException(status_code=400, detail={
                    "code": "conversation_media_count_exceeded",
                    "message": f"对话{kind}参考素材数量超过上限 {hint_limits[kind]}",
                })

    references: List[Dict[str, Any]] = []
    actual_counts = {"image": 0, "video": 0, "audio": 0}
    for asset_id, item in candidates:
        record = project_store_call("media_record", project_id, asset_id)
        kind = str(record.get("kind") or item.get("kind") or "").strip().lower()
        if kind not in actual_counts:
            raise HTTPException(status_code=400, detail={
                "code": "conversation_media_invalid", "message": "直连对话参考素材类型不受支持",
            })
        actual_counts[kind] += 1
        if actual_counts[kind] > hint_limits[kind]:
            raise HTTPException(status_code=400, detail={
                "code": "conversation_media_count_exceeded",
                "message": f"对话{kind}参考素材数量超过上限 {hint_limits[kind]}",
            })
        references.append({
                "asset_id": asset_id,
                "kind": kind,
                "name": str(item.get("name") or record.get("name") or record.get("filename") or asset_id)[:160],
            })
    return references


def conversation_node_by_id(canvas: Dict[str, Any], node_id: str) -> Optional[Dict[str, Any]]:
    return next(
        (
            node for node in canvas.get("nodes") or []
            if isinstance(node, dict) and str(node.get("id") or "") == str(node_id)
        ),
        None,
    )


def conversation_task_snapshot(
    canvas: Dict[str, Any], node_id: str, request_id: str,
) -> Optional[Dict[str, Any]]:
    node = conversation_node_by_id(canvas, node_id)
    if not isinstance(node, dict) or node.get("type") != "smart-conversation":
        return None
    messages = [item for item in node.get("messages") or [] if isinstance(item, dict)]
    user_message = next(
        (
            item for item in messages
            if str(item.get("request_id") or "") == request_id and item.get("role") == "user"
        ),
        None,
    )
    if user_message is None:
        return None
    assistant_message = next(
        (
            item for item in messages
            if str(item.get("request_id") or "") == request_id and item.get("role") == "assistant"
        ),
        None,
    )
    failed = bool(user_message.get("status") == "failed" or assistant_message and assistant_message.get("status") == "failed")
    status = "failed" if failed else "completed" if assistant_message else "pending"
    return {
        "status": status,
        "request_id": request_id,
        "message": copy.deepcopy(user_message),
        "assistant": copy.deepcopy(assistant_message) if assistant_message else None,
    }


def forget_project_conversation_task(
    project_id: str,
    node_id: str,
    request_id: str,
    *,
    handle: Optional[asyncio.Future] = None,
) -> None:
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


def project_conversation_durable_path(project_id: str, node_id: str, request_id: str) -> Path:
    """Keep paid adapter outcomes outside canvas JSON but inside the owning project."""
    with PROJECT_LIBRARY_SWITCH_LOCK:
        store = FIREFLY_PROJECT_STORE
    if store is None:
        raise ProjectStoreError("项目存储尚未初始化")
    project_root = store._project_directory(str(project_id))
    digest = hashlib.sha256(f"{project_id}\0{node_id}\0{request_id}".encode("utf-8")).hexdigest()
    return store._safe_project_path(project_root, f".conversation-tasks/{digest}.json")


def load_project_conversation_durable(project_id: str, node_id: str, request_id: str) -> Optional[Dict[str, Any]]:
    with project_lifecycle_lock(str(project_id)):
        path = project_conversation_durable_path(project_id, node_id, request_id)
        if not path.is_file():
            return None
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise ProjectStoreError("对话任务恢复记录损坏") from exc
    if not isinstance(value, dict) or any(
        str(value.get(key) or "") != expected
        for key, expected in (("project_id", str(project_id)), ("node_id", str(node_id)), ("request_id", str(request_id)))
    ):
        raise ProjectStoreError("对话任务恢复记录不匹配")
    return value


def save_project_conversation_durable(
    project_id: str, node_id: str, request_id: str, state: Dict[str, Any],
) -> None:
    with project_lifecycle_lock(str(project_id)):
        path = project_conversation_durable_path(project_id, node_id, request_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        path = project_conversation_durable_path(project_id, node_id, request_id)
        payload = {
            "project_id": str(project_id), "node_id": str(node_id), "request_id": str(request_id),
            "updated_at": now_ms(), **copy.deepcopy(state),
        }
        temp = path.with_suffix(f".{uuid.uuid4().hex}.tmp")
        try:
            with open(temp, "w", encoding="utf-8", newline="\n") as handle:
                json.dump(payload, handle, ensure_ascii=False, separators=(",", ":"))
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temp, path)
        finally:
            try:
                temp.unlink(missing_ok=True)
            except OSError:
                pass


def forget_project_conversation_durable(project_id: str, node_id: str, request_id: str) -> None:
    try:
        with project_lifecycle_lock(str(project_id)):
            path = project_conversation_durable_path(project_id, node_id, request_id)
            path.unlink(missing_ok=True)
            try:
                path.parent.rmdir()
            except OSError:
                pass
    except (ProjectStoreError, OSError):
        pass


def persist_project_conversation_uncertain(
    project_id: str,
    node_id: str,
    request_id: str,
    *,
    error: Exception,
) -> bool:
    """Persist a non-retryable-in-place marker when the worker failure path breaks."""
    try:
        opened = project_store_call("open_project", project_id)
        snapshot = conversation_task_snapshot(opened["canvas"], node_id, request_id)
        user_message_id = str((snapshot or {}).get("message", {}).get("id") or "")
        message = conversation_error_message(error)
        ready = {
            "user_message_id": user_message_id,
            "error": f"对话任务状态不确定，暂不自动重试：{message}",
        }
        save_project_conversation_durable(
            project_id,
            node_id,
            request_id,
            {"state": "ready", "ready": ready},
        )
        remember_project_conversation_ready_result(project_id, node_id, request_id, ready)
        return True
    except (ProjectNotFound, ProjectStoreError, OSError, ValueError):
        return False


def remember_project_conversation_ready_result(
    project_id: str,
    node_id: str,
    request_id: str,
    ready: Dict[str, Any],
) -> None:
    task_key = (str(project_id), str(node_id), str(request_id))
    with PROJECT_CONVERSATION_TASKS_LOCK:
        PROJECT_CONVERSATION_READY_RESULTS[task_key] = copy.deepcopy(ready)


def forget_project_conversation_ready_result(project_id: str, node_id: str, request_id: str) -> None:
    task_key = (str(project_id), str(node_id), str(request_id))
    with PROJECT_CONVERSATION_TASKS_LOCK:
        PROJECT_CONVERSATION_READY_RESULTS.pop(task_key, None)


def schedule_project_conversation_task(
    project_id: str,
    node_id: str,
    request_id: str,
    *,
    adapter=None,
    recover_fifo: bool = False,
    canvas: Optional[Dict[str, Any]] = None,
) -> bool:
    runner = PROJECT_CONVERSATION_RUNNER
    if runner is None:
        return False
    if recover_fifo:
        try:
            opened = project_store_call("open_project", project_id)
            canvas = opened["canvas"]
            conversation = conversation_node_by_id(canvas, node_id)
            pending = next((
                item for item in (conversation or {}).get("messages") or []
                if isinstance(item, dict)
                and item.get("role") == "user"
                and item.get("status") == "pending"
                and str(item.get("request_id") or "")
            ), None)
            if pending is None:
                return False
            request_id = str(pending["request_id"])
        except (ProjectNotFound, ProjectStoreError):
            return False
    captured_adapter = adapter if adapter is not None else project_conversation_adapter
    return runner.submit(
        project_id,
        node_id,
        request_id,
        adapter=captured_adapter,
        allow_persisted_overage=recover_fifo,
        pending_keys=conversation_pending_task_keys(project_id, canvas=canvas) if canvas is not None else None,
    )


def conversation_error_message(error: Exception) -> str:
    detail = getattr(error, "detail", None)
    if isinstance(detail, dict):
        return str(detail.get("message") or detail.get("detail") or json.dumps(detail, ensure_ascii=False))[:LLM_MESSAGE_MAX_LENGTH]
    return str(detail or error or "对话适配器失败")[:LLM_MESSAGE_MAX_LENGTH]


async def persist_project_conversation_pending(
    project_id: str,
    node_id: str,
    user_message: Dict[str, Any],
    *,
    node_adapter: Optional[Dict[str, str]] = None,
    enforce_admission: bool = False,
) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    request_id = str(user_message.get("request_id") or "")
    for _attempt in range(8):
        try:
            def persist_once():
                with (PROJECT_CONVERSATION_ADMISSION_LOCK if enforce_admission else nullcontext()):
                    with project_lifecycle_lock(str(project_id)):
                        opened = project_store_call("open_project", project_id)
                        current_canvas = opened["canvas"]
                        conversation = conversation_node_by_id(current_canvas, node_id)
                        if not isinstance(conversation, dict) or conversation.get("type") != "smart-conversation":
                            raise HTTPException(status_code=400, detail="目标节点不是对话节点")
                        existing = conversation_task_snapshot(current_canvas, node_id, request_id)
                        if existing:
                            return current_canvas, existing
                        if enforce_admission:
                            runner = PROJECT_CONVERSATION_RUNNER
                            if runner is not None:
                                pending_keys = conversation_pending_task_keys(
                                    project_id,
                                    canvas=current_canvas,
                                    include_all_projects=True,
                                )
                                admission_error = runner.admission_error(
                                    project_id,
                                    node_id,
                                    request_id,
                                    canvas=current_canvas,
                                    pending_keys=pending_keys,
                                )
                                if admission_error:
                                    raise HTTPException(status_code=429, detail=admission_error)
                        updated_node = copy.deepcopy(conversation)
                        messages = [item for item in updated_node.get("messages") or [] if isinstance(item, dict)]
                        updated_node["messages"] = (messages + [copy.deepcopy(user_message)])[-500:]
                        if node_adapter:
                            updated_node["conversation_adapter"] = copy.deepcopy(node_adapter)
                        project_store_call(
                            "apply_mutations",
                            project_id,
                            [{"op": "upsert_node", "node": updated_node}],
                            base_revision=int(current_canvas.get("revision") or 0),
                        )
                        project_store_call("flush", project_id)
                        saved = project_store_call("open_project", project_id)
                        snapshot = conversation_task_snapshot(saved["canvas"], node_id, request_id)
                        if not snapshot:
                            raise ProjectStoreError("对话任务登记后无法读取")
                        return saved["canvas"], snapshot
            return await asyncio.to_thread(persist_once)
        except RevisionConflict:
            continue
    raise HTTPException(status_code=409, detail="项目持续被修改，对话任务尚未登记")


async def merge_project_conversation_result(
    project_id: str,
    node_id: str,
    request_id: str,
    *,
    user_message_id: str = "",
    assistant: Optional[Dict[str, Any]] = None,
    error: Optional[Exception] = None,
) -> Optional[Dict[str, Any]]:
    for _attempt in range(8):
        opened = await asyncio.to_thread(project_store_call, "open_project", project_id)
        current_canvas = opened["canvas"]
        conversation = conversation_node_by_id(current_canvas, node_id)
        if not isinstance(conversation, dict):
            return {}
        current_messages = [item for item in conversation.get("messages") or [] if isinstance(item, dict)]
        def is_target_user(item):
            if item.get("role") != "user":
                return False
            if user_message_id:
                return str(item.get("id") or "") == str(user_message_id)
            return str(item.get("request_id") or "") == request_id
        user_index = next(
            (
                index for index, item in enumerate(current_messages)
                if is_target_user(item)
            ),
            None,
        )
        if user_index is None:
            return {}
        if any(item.get("role") == "assistant" and str(item.get("request_id") or "") == request_id for item in current_messages):
            return conversation_task_snapshot(current_canvas, node_id, request_id)
        updated_node = copy.deepcopy(conversation)
        updated_messages = [item for item in updated_node.get("messages") or [] if isinstance(item, dict)]
        user = updated_messages[user_index]
        if error is not None:
            message = conversation_error_message(error)
            user.update({"status": "failed", "error": message, "failed_at": now_ms()})
            assistant_message = {
                "id": uuid.uuid4().hex,
                "role": "assistant",
                "status": "failed",
                "request_id": request_id,
                "text": f"对话任务失败：{message}",
                "error": message,
                "references": [],
                "created_at": now_ms(),
            }
        else:
            user.update({"status": "completed", "completed_at": now_ms()})
            assistant_message = copy.deepcopy(assistant or {})
        updated_messages.insert(user_index + 1, assistant_message)
        updated_node["messages"] = updated_messages[-500:]
        try:
            await asyncio.to_thread(
                project_store_call,
                "apply_mutations",
                project_id,
                [{"op": "upsert_node", "node": updated_node}],
                base_revision=int(current_canvas.get("revision") or 0),
            )
            await asyncio.to_thread(project_store_call, "flush", project_id)
            saved = await asyncio.to_thread(project_store_call, "open_project", project_id)
            await manager.broadcast_canvas_updated(project_id, int(saved["canvas"].get("updated_at") or now_ms()))
            return conversation_task_snapshot(saved["canvas"], node_id, request_id)
        except RevisionConflict:
            continue
    return None


async def run_project_conversation_task(project_id: str, node_id: str, request_id: str, *, adapter=None) -> bool:
    user_message_id = ""
    ready = project_conversation_ready_result(project_id, node_id, request_id)
    if ready is None:
        durable = await asyncio.to_thread(load_project_conversation_durable, project_id, node_id, request_id)
        if durable and durable.get("state") == "ready" and isinstance(durable.get("ready"), dict):
            ready = copy.deepcopy(durable["ready"])
            remember_project_conversation_ready_result(project_id, node_id, request_id, ready)
        elif durable and durable.get("state") == "dispatching":
            ready = {
                "user_message_id": str(durable.get("user_message_id") or ""),
                "error": "上次调用在服务中断前已发往服务商，结果状态不确定；为避免重复计费，本次不会自动重试，请确认账单后使用新请求显式重试",
            }
            await asyncio.to_thread(
                save_project_conversation_durable, project_id, node_id, request_id,
                {"state": "ready", "ready": ready},
            )
            remember_project_conversation_ready_result(project_id, node_id, request_id, ready)
    try:
        opened = await asyncio.to_thread(project_store_call, "open_project", project_id)
        current_canvas = opened["canvas"]
        conversation = conversation_node_by_id(current_canvas, node_id)
        if not isinstance(conversation, dict):
            forget_project_conversation_ready_result(project_id, node_id, request_id)
            await asyncio.to_thread(forget_project_conversation_durable, project_id, node_id, request_id)
            return True
        messages = [item for item in conversation.get("messages") or [] if isinstance(item, dict)]
        user_index = next(
            (
                index for index, item in enumerate(messages)
                if item.get("role") == "user" and str(item.get("request_id") or "") == request_id
            ),
            None,
        )
        if user_index is None:
            forget_project_conversation_ready_result(project_id, node_id, request_id)
            await asyncio.to_thread(forget_project_conversation_durable, project_id, node_id, request_id)
            return True
        user_message = messages[user_index]
        user_message_id = str(user_message.get("id") or "")
        if user_message.get("status") != "pending":
            forget_project_conversation_ready_result(project_id, node_id, request_id)
            await asyncio.to_thread(forget_project_conversation_durable, project_id, node_id, request_id)
            return True
        if ready is not None and str(ready.get("user_message_id") or "") != user_message_id:
            # The original optimistic user may have been removed before restart;
            # the request identity is still stable, so bind the paid result to
            # the surviving pending user instead of silently dropping it.
            ready = copy.deepcopy(ready)
            ready["user_message_id"] = user_message_id
        earlier_pending = next((
            item for item in messages[:user_index]
            if item.get("role") == "user" and item.get("status") == "pending" and str(item.get("request_id") or "")
        ), None)
        if earlier_pending is not None:
            schedule_project_conversation_task(
                project_id, node_id, str(earlier_pending["request_id"]), adapter=adapter,
                recover_fifo=True,
            )
            return False
        if ready is None:
            adapter_config = (
                conversation.get("conversation_adapter")
                if isinstance(conversation.get("conversation_adapter"), dict)
                else user_message.get("adapter") if isinstance(user_message.get("adapter"), dict)
                else {}
            )
            adapter_request = {
                "project_id": project_id,
                "node_id": node_id,
                "request_id": request_id,
                "message": str(user_message.get("text") or ""),
                "messages": copy.deepcopy(messages[:user_index + 1]),
                "current_message_id": str(user_message.get("id") or ""),
                "references": copy.deepcopy(user_message.get("references") or []),
                "provider": str(adapter_config.get("provider") or "comfly"),
                "model": str(adapter_config.get("model") or ""),
                "ms_model": str(adapter_config.get("ms_model") or ""),
                "system_prompt": str(adapter_config.get("system_prompt") or ""),
            }
            conversation_adapter = adapter if adapter is not None else project_conversation_adapter
            await asyncio.to_thread(
                save_project_conversation_durable, project_id, node_id, request_id,
                {"state": "dispatching", "user_message_id": user_message_id},
            )
            adapter_result = await conversation_adapter(adapter_request)
            if isinstance(adapter_result, str):
                assistant_text = adapter_result.strip()
                adapter_meta = {}
            elif isinstance(adapter_result, dict):
                assistant_text = str(adapter_result.get("text") or adapter_result.get("content") or "").strip()
                adapter_meta = adapter_result
            else:
                assistant_text = ""
                adapter_meta = {}
            if not assistant_text:
                raise RuntimeError("对话适配器返回了空回复")
            assistant_message = {
                "id": uuid.uuid4().hex,
                "role": "assistant",
                "status": "completed",
                "request_id": request_id,
                "text": assistant_text[:LLM_MESSAGE_MAX_LENGTH],
                "references": [],
                "created_at": now_ms(),
            }
            if adapter_meta.get("model"):
                assistant_message["model"] = str(adapter_meta["model"])[:160]
            ready = {"user_message_id": user_message_id, "assistant": assistant_message}
            await asyncio.to_thread(
                save_project_conversation_durable, project_id, node_id, request_id,
                {"state": "ready", "ready": ready},
            )
            remember_project_conversation_ready_result(project_id, node_id, request_id, ready)
        merged = await merge_project_conversation_result(
            project_id,
            node_id,
            request_id,
            user_message_id=str(ready.get("user_message_id") or user_message_id),
            assistant=ready.get("assistant"),
            error=RuntimeError(str(ready["error"])) if ready.get("error") else None,
        )
    except Exception as exc:
        if ready is not None:
            return False
        if ready is None:
            ready = {"user_message_id": user_message_id, "error": conversation_error_message(exc)}
            await asyncio.to_thread(
                save_project_conversation_durable, project_id, node_id, request_id,
                {"state": "ready", "ready": ready},
            )
            remember_project_conversation_ready_result(project_id, node_id, request_id, ready)
        merged = await merge_project_conversation_result(
            project_id, node_id, request_id, user_message_id=user_message_id, error=exc,
        )
    if merged is None:
        return False
    forget_project_conversation_ready_result(project_id, node_id, request_id)
    await asyncio.to_thread(forget_project_conversation_durable, project_id, node_id, request_id)
    return True


def conversation_task_response(canvas: Dict[str, Any], snapshot: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "ok": True,
        "canvas": canvas,
        "status": snapshot["status"],
        "request_id": snapshot["request_id"],
        "message": snapshot["message"],
        "assistant": snapshot.get("assistant"),
        "saved": True,
    }


async def finalize_visible_project_conversation_terminal(
    project_id: str, node_id: str, request_id: str,
) -> None:
    """Make terminal API visibility include all recovery-state cleanup."""
    forget_project_conversation_task(project_id, node_id, request_id)
    forget_project_conversation_ready_result(project_id, node_id, request_id)
    await asyncio.to_thread(forget_project_conversation_durable, project_id, node_id, request_id)


@app.post("/api/project-library/{project_id}/conversation/{node_id}/messages")
async def append_project_conversation_message(
    project_id: str,
    node_id: str,
    payload: ProjectConversationMessageRequest,
):
    text = str(payload.text or "").strip()
    if not text:
        raise HTTPException(status_code=400, detail="对话消息不能为空")
    request_id = str(payload.request_id or "").strip() or uuid.uuid4().hex
    runner = PROJECT_CONVERSATION_RUNNER
    if runner is None or not runner.accepting:
        raise HTTPException(status_code=503, detail={"code": "conversation_queue_unavailable", "message": "对话任务服务尚未就绪"})
    user_message = {
        "id": uuid.uuid4().hex,
        "role": "user",
        "status": "pending",
        "request_id": request_id,
        "text": text,
        "references": [],
        "created_at": now_ms(),
    }
    try:
        opened = await asyncio.to_thread(project_store_call, "open_project", project_id)
        current_canvas = opened["canvas"]
        conversation = conversation_node_by_id(current_canvas, node_id)
        if not isinstance(conversation, dict) or conversation.get("type") != "smart-conversation":
            raise HTTPException(status_code=400, detail="目标节点不是对话节点")
        stored_adapter = (
            conversation.get("conversation_adapter")
            if isinstance(conversation.get("conversation_adapter"), dict)
            else {}
        )
        node_adapter = {
            "provider": str(payload.provider if payload.provider is not None else stored_adapter.get("provider") or "comfly"),
            "model": str(payload.model if payload.model is not None else stored_adapter.get("model") or ""),
            "ms_model": str(payload.ms_model if payload.ms_model is not None else stored_adapter.get("ms_model") or ""),
            "system_prompt": str(payload.system_prompt if payload.system_prompt is not None else stored_adapter.get("system_prompt") or ""),
        }
        existing = conversation_task_snapshot(current_canvas, node_id, request_id)
        if existing:
            if existing["status"] == "pending":
                schedule_project_conversation_task(project_id, node_id, request_id, recover_fifo=True)
            else:
                await finalize_visible_project_conversation_terminal(project_id, node_id, request_id)
            return conversation_task_response(current_canvas, existing)
        persisted_pending_keys = await asyncio.to_thread(
            conversation_pending_task_keys,
            project_id,
            canvas=current_canvas,
            include_all_projects=True,
        )
        admission_error = runner.admission_error(
            project_id,
            node_id,
            request_id,
            canvas=current_canvas,
            pending_keys=persisted_pending_keys,
        )
        if admission_error:
            raise HTTPException(status_code=429, detail=admission_error)
        references = await asyncio.to_thread(
            project_conversation_connected_references,
            project_id,
            current_canvas,
            node_id,
        )
        user_message["references"] = copy.deepcopy(references)
        saved_canvas, snapshot = await persist_project_conversation_pending(
            project_id,
            node_id,
            user_message,
            node_adapter=node_adapter,
            enforce_admission=True,
        )
    except ProjectNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ProjectStoreError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if str(snapshot.get("message", {}).get("id") or "") == str(user_message.get("id") or ""):
        if not schedule_project_conversation_task(
            project_id,
            node_id,
            request_id,
            canvas=saved_canvas,
        ):
            await merge_project_conversation_result(
                project_id, node_id, request_id, user_message_id=str(user_message.get("id") or ""),
                error=RuntimeError("对话队列已满，任务未调用服务商"),
            )
            raise HTTPException(status_code=429, detail={"code": "conversation_queue_full", "message": "对话队列已满，任务未调用服务商"})
    return conversation_task_response(saved_canvas, snapshot)


@app.get("/api/project-library/{project_id}/conversation/{node_id}/messages/{request_id}")
async def get_project_conversation_message_task(project_id: str, node_id: str, request_id: str):
    clean_request_id = str(request_id or "").strip()
    try:
        opened = await asyncio.to_thread(project_store_call, "open_project", project_id)
        snapshot = conversation_task_snapshot(opened["canvas"], node_id, clean_request_id)
        if snapshot is None:
            raise HTTPException(status_code=404, detail="对话任务不存在")
        if snapshot["status"] == "pending":
            schedule_project_conversation_task(project_id, node_id, clean_request_id, recover_fifo=True)
        else:
            await finalize_visible_project_conversation_terminal(project_id, node_id, clean_request_id)
        return conversation_task_response(opened["canvas"], snapshot)
    except ProjectNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ProjectStoreError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.patch("/api/project-library/{project_id}/canvas")
async def mutate_firefly_canvas(project_id: str, payload: CanvasMutationBatch):
    try:
        result = await asyncio.to_thread(
            project_store_call,
            "apply_mutations",
            project_id,
            payload.mutations,
            base_revision=payload.base_revision,
        )
    except ProjectNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except InvalidMutation as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except RevisionConflict as exc:
        raise HTTPException(status_code=409, detail={"message": str(exc), "canvas": exc.canvas}) from exc
    await manager.broadcast_canvas_updated(project_id, int(result.get("updated_at") or now_ms()), payload.client_id)
    deadline = time.monotonic() + 1.9
    status = await asyncio.to_thread(project_store_call, "save_status", project_id)
    while not status.get("saved") and time.monotonic() < deadline:
        await asyncio.sleep(0.05)
        status = await asyncio.to_thread(project_store_call, "save_status", project_id)
    result["saved"] = bool(status.get("saved"))
    result["saved_revision"] = int(status.get("saved_revision") or 0)
    return result


@app.post("/api/project-library/{project_id}/local-edit")
@app.post("/api/project-library/{project_id}/local-edit-tasks")
async def create_project_local_edit(project_id: str, payload: ProjectLocalEditRequest):
    """Run one local-edit operation through an injectable offline/online adapter."""
    mode = str(payload.mode or "").strip().lower()
    if mode not in {"modify", "retain", "局部修改", "局部保留"}:
        raise HTTPException(status_code=400, detail="局部编辑模式必须是 modify 或 retain")
    mode = "modify" if mode in {"modify", "局部修改"} else "retain"
    try:
        opened = await asyncio.to_thread(project_store_call, "open_project", project_id)
        target = next((item for item in opened["canvas"].get("nodes", []) if str(item.get("id")) == payload.result_node_id), None)
        if not target or target.get("type") != "local-edit":
            raise ProjectStoreError("局部编辑结果节点必须先存在于当前项目")
        incoming = [edge for edge in opened["canvas"].get("connections", []) if str(edge.get("to")) == payload.result_node_id and edge.get("kind", "flow") == "input"]
        role_edges = {role: [edge for edge in incoming if edge.get("role") == role] for role in ("original", "references", "prompt")}
        if len(role_edges["original"]) != 1:
            raise ProjectStoreError("局部编辑必须连接恰好一个原图输入")
        connected_original = next((item for item in opened["canvas"].get("nodes", []) if str(item.get("id")) == str(role_edges["original"][0].get("from"))), None)
        connected_assets = [str(image.get("asset_id")) for image in (connected_original or {}).get("images", []) if image.get("asset_id")]
        if len(connected_assets) != 1:
            raise ProjectStoreError("原图角色必须来自恰好一个图片资源")
        if payload.original_asset_id not in connected_assets:
            raise ProjectStoreError("原图资源必须来自局部编辑的原图角色连线")
        connected_reference_assets = []
        for edge in role_edges["references"]:
            source = next((item for item in opened["canvas"].get("nodes", []) if str(item.get("id")) == str(edge.get("from"))), None)
            connected_reference_assets.extend(str(image.get("asset_id")) for image in (source or {}).get("images", []) if image.get("asset_id"))
        if set(payload.reference_asset_ids) - set(connected_reference_assets):
            raise ProjectStoreError("参考资源必须来自局部编辑的参考角色连线")
        prompt_sources = [next((item for item in opened["canvas"].get("nodes", []) if str(item.get("id")) == str(edge.get("from"))), None) for edge in role_edges["prompt"]]
        prompt_texts = [str(item.get("text") or item.get("prompt") or "").strip() for item in prompt_sources if isinstance(item, dict)]
        prompt_texts = [text for text in prompt_texts if text]
        composed_prompt = "\n\n".join(prompt_texts)
        if composed_prompt and payload.prompt.strip() != composed_prompt:
            raise ProjectStoreError("提示词必须按直接连接的提示词节点顺序组合，或在没有提示词连线时显式输入")
        original = await asyncio.to_thread(project_store_call, "media_record", project_id, payload.original_asset_id)
        if original.get("kind") != "image":
            raise ProjectStoreError("原图必须是项目图片媒体")
        original_bytes = await asyncio.to_thread(Path(original["path"]).read_bytes)
        references: List[Tuple[str, bytes]] = []
        for asset_id in payload.reference_asset_ids:
            record = await asyncio.to_thread(project_store_call, "media_record", project_id, asset_id)
            if record.get("kind") != "image":
                raise ProjectStoreError("参考输入必须是项目图片媒体")
            references.append((asset_id, await asyncio.to_thread(Path(record["path"]).read_bytes)))
        source_image = as_rgba(original_bytes)
        selection_asset: Dict[str, Any]
        model_input_asset: Dict[str, Any]
        persisted_selection: Any = payload.selection
        if mode == "modify":
            raw_rect = payload.selection.get("rect") if isinstance(payload.selection, dict) and payload.selection.get("rect") is not None else payload.selection
            from local_edit import crop_rect, validate_rect
            rect = validate_rect(raw_rect, source_image.size, payload.aspect_ratio or None)
            # Keep the canonical source-pixel half-open rectangle everywhere;
            # the adapter, node and history must not retain viewport/floating
            # input variants after validation.
            persisted_selection = list(rect)
            model_input = encode_png(crop_rect(source_image, rect))
            model_input_asset = await asyncio.to_thread(
                project_store_call, "import_media", project_id, model_input,
                "local-edit-input.png", "image/png", {"type": "local_edit_model_input", "mode": mode},
            )
            selection_asset = await asyncio.to_thread(
                project_store_call, "import_media", project_id, selection_json({"type": "rect", "rect": list(rect), "aspect_ratio": payload.aspect_ratio}),
                "selection.json", "application/json", {"type": "local_edit_selection", "mode": mode},
            )
        else:
            # Persist/rasterize the hard preserve shape once; apply feathering
            # exactly once during the final composite.
            preserve_mask = rasterize_preserve_mask(payload.selection, source_image.size, 0)
            if preserve_mask.getbbox() is None:
                raise ValueError("局部保留选区不能为空")
            model_input = encode_png(source_image)
            model_input_asset = original
            selection_asset = await asyncio.to_thread(
                project_store_call, "import_media", project_id, encode_png(preserve_mask),
                "preserve-mask.png", "image/png", {"type": "local_edit_mask", "mode": mode},
            )
        request_data = {
            "project_id": project_id,
            "input_asset_id": model_input_asset["asset_id"],
            "reference_asset_ids": [model_input_asset["asset_id"], *[asset_id for asset_id, _content in references]],
            "mode": mode,
            "image": model_input,
            "references": [content for _asset_id, content in references],
            "prompt": payload.prompt,
            "selection": persisted_selection,
            "aspect_ratio": payload.aspect_ratio,
            "size": payload.size,
            "resolution": payload.resolution,
            "source_size": list(source_image.size),
        }
        if inspect.iscoroutinefunction(local_edit_adapter):
            adapter_result = local_edit_adapter(request_data)
            adapter_result = await adapter_result
        else:
            adapter_result = await asyncio.to_thread(local_edit_adapter, request_data)
            if inspect.isawaitable(adapter_result):
                adapter_result = await adapter_result
        model_image = as_rgba(adapter_result)
        if mode == "modify":
            result_image = composite_rect(source_image, model_image, rect, payload.feather_radius)
        else:
            result_image = composite_preserve(source_image, model_image, preserve_mask, payload.feather_radius)
        model_asset = await asyncio.to_thread(
            project_store_call, "import_media", project_id, encode_png(model_image),
            "model-result.png", "image/png", {"type": "local_edit_model_result", "mode": mode},
        )
        output_asset = await asyncio.to_thread(
            project_store_call, "import_media", project_id, encode_png(result_image),
            "local-edit-output.png", "image/png", {"type": "local_edit_composite", "mode": mode},
        )
        # Update the existing result node in place.  A local-edit node may have
        # been positioned/resized and may carry UI-only role/selection state;
        # replacing it with a freshly constructed dict would silently move it
        # to the origin and discard those controls after the next render.
        node = copy.deepcopy(target)
        node.update({
            "id": payload.result_node_id,
            "type": "local-edit",
            "mode": mode,
            "inputs": {
                "original": {"asset_id": payload.original_asset_id},
                "references": [{"asset_id": item} for item, _content in references],
                "prompt": payload.prompt,
            },
            "input_roles": {"original": {"asset_id": payload.original_asset_id}, "references": [{"asset_id": item} for item, _content in references], "prompt": payload.prompt},
            "original_asset_id": payload.original_asset_id,
            "reference_asset_ids": [item for item, _content in references],
            "model_input_asset_id": model_input_asset["asset_id"],
            "model_result_asset_id": model_asset["asset_id"],
            "selection_asset_id": selection_asset["asset_id"] if mode == "modify" else "",
            "mask_asset_id": selection_asset["asset_id"] if mode == "retain" else "",
            "selection": persisted_selection,
            "feather_radius": payload.feather_radius,
            "generation_size": payload.size,
            "generation_resolution": payload.resolution,
            "images": [{"asset_id": output_asset["asset_id"], "kind": "image", "name": "local-edit-output.png"}],
            "output_asset_id": output_asset["asset_id"],
            "outputKind": "image",
        })
        history_record = {
            "id": f"local-edit-{uuid.uuid4().hex}",
            "type": "local-edit",
            "status": "succeeded",
            "mode": mode,
            "prompt": payload.prompt,
            "selection": persisted_selection,
            "aspect_ratio": payload.aspect_ratio,
            "feather_radius": payload.feather_radius,
            "generation_size": payload.size,
            "generation_resolution": payload.resolution,
            "original_asset_id": payload.original_asset_id,
            "reference_asset_ids": [item for item, _content in references],
            "model_input_asset_id": model_input_asset["asset_id"],
            "model_result_asset_id": model_asset["asset_id"],
            "selection_asset_id": selection_asset["asset_id"] if mode == "modify" else "",
            "mask_asset_id": selection_asset["asset_id"] if mode == "retain" else "",
            "output_asset_id": output_asset["asset_id"],
            "result_node_id": payload.result_node_id,
        }
        existing_history = opened["canvas"].get("logs") if isinstance(opened.get("canvas"), dict) else []
        history_records = [item for item in (existing_history or []) if isinstance(item, dict)][-4999:] + [history_record]
        changed = await asyncio.to_thread(
            project_store_call, "apply_mutations", project_id,
            [{"op": "upsert_node", "node": node}, {"op": "set_canvas_meta", "logs": history_records}], base_revision=payload.base_revision,
        )
        await asyncio.to_thread(project_store_call, "flush", project_id)
        await manager.broadcast_canvas_updated(project_id, int(changed.get("updated_at") or now_ms()))
        return {
            "ok": True,
            "mode": mode,
            "node": node,
            "assets": {"original": payload.original_asset_id, "model_input": model_input_asset, "model_result": model_asset, "selection": selection_asset, "output": output_asset},
            "canvas": await asyncio.to_thread(project_store_call, "open_project", project_id),
        }
    except RevisionConflict as exc:
        raise HTTPException(status_code=409, detail={"message": str(exc), "canvas": exc.canvas}) from exc
    except ProjectNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except (ProjectStoreError, ValueError, TypeError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

@app.post("/api/project-library/{project_id}/flush")
async def flush_firefly_project(project_id: str):
    try:
        return await asyncio.to_thread(project_store_call, "flush", project_id)
    except ProjectNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

@app.get("/api/project-library/{project_id}/save-status")
async def firefly_project_save_status(project_id: str):
    try:
        return await asyncio.to_thread(project_store_call, "save_status", project_id)
    except ProjectNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

@app.post("/api/project-library/{project_id}/history/delete")
async def delete_project_history(project_id: str, payload: ProjectHistoryDeleteRequest):
    try:
        canvas = await asyncio.to_thread(
            project_store_call,
            "delete_history_record",
            project_id,
            payload.log_id,
            base_revision=payload.base_revision,
        )
    except RevisionConflict as exc:
        raise HTTPException(status_code=409, detail={"message": str(exc), "canvas": exc.canvas}) from exc
    except ProjectNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    await asyncio.to_thread(project_store_call, "flush", project_id)
    await manager.broadcast_canvas_updated(project_id, int(canvas.get("updated_at") or now_ms()))
    return {"ok": True, "canvas": canvas}

@app.post("/api/project-library/{project_id}/original/delete")
async def delete_project_generated_original(project_id: str, payload: ProjectOriginalDeleteRequest):
    try:
        result = await asyncio.to_thread(
            project_store_call,
            "delete_generated_original",
            project_id,
            payload.asset_id,
            base_revision=payload.base_revision,
        )
    except RevisionConflict as exc:
        raise HTTPException(status_code=409, detail={"message": str(exc), "canvas": exc.canvas}) from exc
    except ProjectNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ProjectStoreError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    canvas = result["canvas"]
    await manager.broadcast_canvas_updated(project_id, int(canvas.get("updated_at") or now_ms()))
    return {"ok": True, "canvas": canvas, "deleted": result["deleted"]}

@app.post("/api/project-library/{project_id}/original/restore")
async def restore_project_generated_original(project_id: str, payload: ProjectOriginalDeleteRequest):
    try:
        result = await asyncio.to_thread(
            project_store_call,
            "restore_deleted_original",
            project_id,
            payload.asset_id,
            base_revision=payload.base_revision,
        )
    except RevisionConflict as exc:
        raise HTTPException(status_code=409, detail={"message": str(exc), "canvas": exc.canvas}) from exc
    except ProjectNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ProjectStoreError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    canvas = result["canvas"]
    await manager.broadcast_canvas_updated(project_id, int(canvas.get("updated_at") or now_ms()))
    return {"ok": True, "canvas": canvas, "restored": result["restored"]}

@app.get("/api/project-library/{project_id}/trash/media")
async def list_project_media_trash(project_id: str):
    try:
        items = await asyncio.to_thread(project_store_call, "list_deleted_media", project_id)
    except ProjectNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return {"items": items}

@app.delete("/api/project-library/{project_id}/trash/media")
async def purge_project_media_trash(project_id: str, asset_id: str | None = None):
    try:
        removed = await asyncio.to_thread(project_store_call, "purge_deleted_media", project_id, asset_id)
    except ProjectNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ProjectStoreError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return {"ok": True, "removed": removed}

@app.post("/api/projects")
async def create_project(payload: ProjectCreateRequest):
    raise HTTPException(status_code=410, detail="旧项目入口已停用，请使用 /api/project-library")

@app.post("/api/projects/{project_id}")
async def update_project(project_id: str, payload: ProjectUpdateRequest):
    raise HTTPException(status_code=410, detail="旧项目入口已停用，请使用 /api/project-library")

@app.delete("/api/projects/{project_id}")
async def delete_project(project_id: str):
    raise HTTPException(status_code=410, detail="旧项目入口已停用；项目回收站由后续项目生命周期接口提供")

@app.get("/api/canvases/trash")
async def trashed_canvases():
    return {"canvases": list_deleted_canvases(), "retention_days": 30}

@app.post("/api/canvases")
async def create_canvas(payload: CanvasCreateRequest):
    raise HTTPException(status_code=410, detail="旧画布创建入口已停用；一个项目只拥有一张核心画布")

@app.get("/api/canvases/{canvas_id}/meta")
async def get_canvas_meta(canvas_id: str):
    canvas = load_canvas(canvas_id)
    return {
        "id": canvas.get("id"),
        "updated_at": canvas.get("updated_at", 0),
        "title": canvas.get("title", "未命名画布"),
        "icon": canvas.get("icon", "layers"),
        "kind": normalize_canvas_kind(canvas.get("kind")),
    }

@app.post("/api/canvases/{canvas_id}/meta")
async def update_canvas_meta(canvas_id: str, payload: CanvasMetaUpdate):
    """更新画布的轻量元数据（标题/图标/负责人/颜色/置顶）。
    刻意不走 save_canvas（它会刷新 updated_at），以免打标签/置顶把画布顶到列表最前。"""
    def mutate_meta():
        with CANVAS_LOCK:
            canvas = load_canvas(canvas_id)
            if canvas.get("format") == "firefly-canvas-v1":
                raise HTTPException(status_code=410, detail="核心画布元数据必须通过增量项目接口保存")
            if payload.title is not None:
                canvas["title"] = (payload.title or canvas.get("title") or "未命名画布")[:80]
            if payload.icon is not None:
                canvas["icon"] = (payload.icon or "layers")[:32]
            if payload.owner is not None:
                canvas["owner"] = str(payload.owner).strip()[:40]
            if payload.color is not None:
                canvas["color"] = normalize_canvas_color(payload.color)
            if payload.pinned is not None:
                canvas["pinned"] = bool(payload.pinned)
            if payload.project is not None:
                canvas["project"] = str(payload.project).strip() or DEFAULT_PROJECT_ID
            if payload.board_x is not None:
                canvas["board_x"] = float(payload.board_x)
            if payload.board_y is not None:
                canvas["board_y"] = float(payload.board_y)
            with open(canvas_path(canvas["id"]), 'w', encoding='utf-8') as f:
                json.dump(canvas, f, ensure_ascii=False, indent=2)
            return canvas

    canvas = await asyncio.to_thread(mutate_meta)
    return {"canvas": canvas_record(canvas)}

@app.get("/api/canvases/{canvas_id}")
async def get_canvas(canvas_id: str):
    return {"canvas": load_canvas(canvas_id)}

@app.post("/api/canvases/{canvas_id}/touch")
async def touch_canvas(canvas_id: str):
    def touch():
        with CANVAS_LOCK:
            canvas = load_canvas(canvas_id)
            if canvas.get("format") == "firefly-canvas-v1":
                raise HTTPException(status_code=410, detail="核心画布没有整包触碰接口")
            save_canvas(canvas)
            return canvas

    canvas = await asyncio.to_thread(touch)
    return {"canvas": canvas_record(canvas), "updated_at": canvas.get("updated_at", 0)}

@app.get("/api/canvas-assets")
async def list_canvas_assets():
    # canvas_assets_index 会同步遍历并解析所有画布 JSON，放进线程池避免阻塞事件循环
    # （否则画布多时一次请求就会卡住整个 asyncio loop，连 WebSocket 一起掉线）。
    return await asyncio.to_thread(canvas_assets_index)

@app.get("/api/smart-canvas/prompt-templates")
async def smart_canvas_prompt_templates():
    try:
        template_path = prompt_template_markdown_path()
        source = os.path.relpath(template_path, BASE_DIR).replace("\\", "/") if template_path else ""
        return {"templates": builtin_prompt_templates(), "source": source}
    except Exception as e:
        print(f"读取提示词模板失败: {e}")
        return {"templates": []}

@app.post("/api/canvas-assets/check")
async def check_canvas_assets(payload: CanvasAssetCheckRequest):
    result = {}
    for url in payload.urls[:3000]:
        text = str(url or "").strip()
        if not text:
            continue
        if text.startswith("/output/") or text.startswith("/assets/"):
            result[text] = bool(output_file_from_url(text))
        else:
            result[text] = True
    return {"exists": result}

@app.post("/api/canvas-assets/download")
async def download_canvas_assets(payload: CanvasAssetDownloadRequest):
    buffer = BytesIO()
    used_names = set()
    count = 0
    raw_items = payload.items or [{"url": url} for url in payload.urls]
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as zf:
        for raw in raw_items[:1000]:
            if isinstance(raw, dict):
                text = str(raw.get("url") or "").strip()
                requested_name = str(raw.get("name") or "").strip()
            else:
                text = str(raw or "").strip()
                requested_name = ""
            if not text:
                continue
            path = output_file_from_url(text)
            content = None
            content_type = ""
            if path and os.path.isfile(path):
                base = sanitize_export_filename(requested_name or os.path.basename(path), os.path.basename(path) or f"image-{count + 1}.png")
            else:
                local_by_name = local_media_file_by_basename(filename_from_media_url(text, ""))
                if local_by_name and os.path.isfile(local_by_name):
                    path = local_by_name
                    base = sanitize_export_filename(requested_name or os.path.basename(path), os.path.basename(path) or f"image-{count + 1}.png")
                else:
                    try:
                        remote = fetch_remote_media_bytes(text)
                    except Exception:
                        remote = None
                    if not remote:
                        continue
                    content, content_type = remote
                    base = sanitize_export_filename(requested_name or filename_from_media_url(text, f"image-{count + 1}.bin"), f"image-{count + 1}.bin")
            name, ext = os.path.splitext(base)
            archive_name = base
            suffix = 2
            while archive_name in used_names:
                archive_name = f"{name}-{suffix}{ext}"
                suffix += 1
            used_names.add(archive_name)
            if path and os.path.isfile(path):
                zf.write(path, archive_name)
            else:
                zf.writestr(archive_name, content)
            count += 1
    if count <= 0:
        raise HTTPException(status_code=404, detail="没有可下载的本地图片")
    buffer.seek(0)
    filename = re.sub(r'[\\/:*?"<>|]+', "_", payload.filename or "canvas-output-images.zip")
    if not filename.lower().endswith(".zip"):
        filename += ".zip"
    encoded = urllib.parse.quote(filename)
    headers = {"Content-Disposition": f"attachment; filename*=UTF-8''{encoded}"}
    return Response(buffer.getvalue(), media_type="application/zip", headers=headers)

def sanitize_export_filename(name: str, fallback: str) -> str:
    base = os.path.basename(str(name or "").strip()) or fallback
    base = re.sub(r'[\\/:*?"<>|]+', "_", base)
    return base or fallback

def smart_group_export_folder(folder: str, group_name: str) -> str:
    text = str(folder or "").strip()
    if text:
        path = os.path.abspath(os.path.expanduser(text))
    else:
        stamp = time.strftime("%Y%m%d-%H%M%S")
        safe_group = sanitize_export_filename(group_name or "group", "group")
        path = os.path.abspath(os.path.join(OUTPUT_DIR, "smart-groups", f"{safe_group}-{stamp}"))
    os.makedirs(path, exist_ok=True)
    return path

@app.post("/api/smart-canvas/group-export")
async def export_smart_canvas_group(payload: SmartCanvasGroupExportRequest):
    target_dir = smart_group_export_folder(payload.folder, payload.group_name)
    used_names = set()
    count = 0
    text_index = 1
    for item in payload.items[:2000]:
        kind = str(item.kind or "").lower()
        if kind == "text":
            text = str(item.text or "")
            if not text.strip():
                continue
            base = sanitize_export_filename(item.name or f"{text_index}.txt", f"{text_index}.txt")
            if not base.lower().endswith(".txt"):
                base += ".txt"
            text_index += 1
            name, ext = os.path.splitext(base)
            out_name = base
            suffix = 2
            while out_name in used_names:
                out_name = f"{name}-{suffix}{ext}"
                suffix += 1
            used_names.add(out_name)
            with open(os.path.join(target_dir, out_name), "w", encoding="utf-8") as f:
                f.write(text)
            count += 1
            continue
        src = output_file_from_url(item.url)
        if not src or not os.path.isfile(src):
            continue
        base = sanitize_export_filename(item.name or os.path.basename(src), os.path.basename(src) or f"asset-{count + 1}")
        name, ext = os.path.splitext(base)
        if not ext:
            _, src_ext = os.path.splitext(src)
            ext = src_ext or ".bin"
            base = name + ext
        out_name = base
        suffix = 2
        while out_name in used_names:
            out_name = f"{name}-{suffix}{ext}"
            suffix += 1
        used_names.add(out_name)
        shutil.copy2(src, os.path.join(target_dir, out_name))
        count += 1
    if count <= 0:
        raise HTTPException(status_code=404, detail="没有可导出的内容")
    return {"ok": True, "folder": target_dir, "count": count}

@app.get("/api/asset-library")
async def get_asset_library():
    return {"library": load_asset_library()}

@app.get("/api/prompt-libraries")
async def get_prompt_libraries():
    return {"library": public_prompt_libraries()}

@app.post("/api/prompt-libraries")
async def create_prompt_library(payload: PromptLibraryRequest):
    data = load_prompt_libraries()
    library = {
        "id": f"lib_{uuid.uuid4().hex[:12]}",
        "name": sanitize_asset_name(payload.name, "提示词库"),
        "type": "prompt",
        "categories": [],
        "items": [],
    }
    data.setdefault("libraries", []).append(library)
    data["active_library_id"] = library["id"]
    data = save_prompt_libraries(data)
    new_lib = next((lib for lib in data.get("libraries", []) if lib.get("id") == library["id"]), library)
    return {"library": public_prompt_libraries(data), "prompt_library": new_lib}

@app.patch("/api/prompt-libraries/{library_id}")
async def rename_prompt_library(library_id: str, payload: PromptLibraryRequest):
    data = load_prompt_libraries()
    library = find_prompt_library(data, library_id)
    if not library or library.get("id") != library_id:
        raise HTTPException(status_code=404, detail="提示词库不存在")
    library["name"] = sanitize_asset_name(payload.name, library.get("name") or "提示词库")
    data = save_prompt_libraries(data)
    return {"library": public_prompt_libraries(data), "prompt_library": library}

@app.delete("/api/prompt-libraries/{library_id}")
async def delete_prompt_library(library_id: str):
    if library_id == "system":
        raise HTTPException(status_code=400, detail="系统提示词库不能删除，可以删除其中的提示词")
    data = load_prompt_libraries()
    libraries = data.get("libraries", []) or []
    kept = [lib for lib in libraries if lib.get("id") != library_id]
    if len(kept) == len(libraries):
        raise HTTPException(status_code=404, detail="提示词库不存在")
    data["libraries"] = kept
    if data.get("active_library_id") == library_id:
        data["active_library_id"] = "system"
    data = save_prompt_libraries(data)
    return {"library": public_prompt_libraries(data)}

@app.post("/api/prompt-libraries/items")
async def add_prompt_library_item(payload: PromptLibraryItemRequest):
    data = load_prompt_libraries()
    library = find_prompt_library(data, payload.library_id)
    if not library:
        raise HTTPException(status_code=404, detail="提示词库不存在")
    if not str(payload.positive or "").strip():
        raise HTTPException(status_code=400, detail="提示词内容不能为空")
    item = normalize_prompt_library_item({
        "id": f"tpl_{uuid.uuid4().hex[:12]}",
        "name": payload.name,
        "category": payload.category,
        "positive": payload.positive,
        "negative": payload.negative,
        "scene": payload.scene,
        "created_at": now_ms(),
        "updated_at": now_ms(),
    })
    library.setdefault("items", []).insert(0, item)
    data["active_library_id"] = library.get("id") or data.get("active_library_id")
    data = save_prompt_libraries(data)
    return {"library": public_prompt_libraries(data), "item": item}

@app.patch("/api/prompt-libraries/items/{item_id}")
async def update_prompt_library_item(item_id: str, payload: PromptLibraryItemRequest):
    data = load_prompt_libraries()
    for library in data.get("libraries", []) or []:
        if payload.library_id and library.get("id") != payload.library_id:
            continue
        for index, item in enumerate(library.get("items", []) or []):
            if item.get("id") == item_id:
                next_item = normalize_prompt_library_item({
                    **item,
                    "name": payload.name or item.get("name"),
                    "category": payload.category or item.get("category"),
                    "positive": payload.positive or item.get("positive"),
                    "negative": payload.negative,
                    "scene": payload.scene,
                    "updated_at": now_ms(),
                })
                library["items"][index] = next_item
                data = save_prompt_libraries(data)
                return {"library": public_prompt_libraries(data), "item": next_item}
    raise HTTPException(status_code=404, detail="提示词不存在")

@app.delete("/api/prompt-libraries/items/{item_id}")
async def delete_prompt_library_item(item_id: str):
    data = load_prompt_libraries()
    removed = None
    for library in data.get("libraries", []) or []:
        keep = []
        for item in library.get("items", []) or []:
            if item.get("id") == item_id:
                removed = item
            else:
                keep.append(item)
        library["items"] = keep
    if not removed:
        raise HTTPException(status_code=404, detail="提示词不存在")
    data = save_prompt_libraries(data)
    return {"library": public_prompt_libraries(data), "removed": 1}

@app.post("/api/prompt-libraries/items/delete")
async def batch_delete_prompt_library_items(payload: PromptLibraryBatchDeleteRequest):
    ids = {str(item) for item in (payload.ids or []) if str(item)}
    if not ids:
        raise HTTPException(status_code=400, detail="没有选择提示词")
    data = load_prompt_libraries()
    removed = 0
    for library in data.get("libraries", []) or []:
        keep = []
        for item in library.get("items", []) or []:
            if item.get("id") in ids:
                removed += 1
            else:
                keep.append(item)
        library["items"] = keep
    data = save_prompt_libraries(data)
    return {"library": public_prompt_libraries(data), "removed": removed}

PROMPT_BUILTIN_CATEGORY_IDS = {"view", "storyboard", "character", "product", "lighting", "custom"}

@app.post("/api/prompt-libraries/categories")
async def add_prompt_library_category(payload: PromptLibraryCategoryRequest):
    data = load_prompt_libraries()
    library = find_prompt_library(data, payload.library_id) or find_prompt_library(data, "system")
    if not library:
        raise HTTPException(status_code=404, detail="提示词库不存在")
    name = sanitize_asset_name(payload.name, "新分组")
    existing = {str(c.get("id")) for c in (library.get("categories") or []) if isinstance(c, dict)} | PROMPT_BUILTIN_CATEGORY_IDS
    cat_id = f"pcat_{uuid.uuid4().hex[:10]}"
    while cat_id in existing:
        cat_id = f"pcat_{uuid.uuid4().hex[:10]}"
    category = {"id": cat_id, "name": name}
    library.setdefault("categories", []).append(category)
    data = save_prompt_libraries(data)
    return {"library": public_prompt_libraries(data), "category": category}

@app.patch("/api/prompt-libraries/categories/{category_id}")
async def rename_prompt_library_category(category_id: str, payload: PromptLibraryCategoryRequest):
    # 系统库（内置）分组也允许重命名：分组的 id 不变，只改显示名，
    # 这样画布与素材库管理共用同一份分组数据，重命名两端实时同步。
    name = sanitize_asset_name(payload.name, "")
    if not name:
        raise HTTPException(status_code=400, detail="分组名称不能为空")
    data = load_prompt_libraries()
    updated = False
    for library in data.get("libraries", []) or []:
        for cat in library.get("categories") or []:
            if isinstance(cat, dict) and cat.get("id") == category_id:
                cat["name"] = name
                updated = True
    if not updated:
        raise HTTPException(status_code=404, detail="分组不存在")
    data = save_prompt_libraries(data)
    return {"library": public_prompt_libraries(data)}

@app.delete("/api/prompt-libraries/categories/{category_id}")
async def delete_prompt_library_category(category_id: str):
    # 系统库（内置）分组也允许删除，与素材库管理/画布保持一致。
    data = load_prompt_libraries()
    found = False
    for library in data.get("libraries", []) or []:
        cats = library.get("categories") or []
        kept = [c for c in cats if not (isinstance(c, dict) and c.get("id") == category_id)]
        if len(kept) != len(cats):
            found = True
            library["categories"] = kept
            # 被删分组下的条目改挂到剩余的第一个分组；若已无分组则归到“未分类”。
            fallback = next((str(c.get("id")) for c in kept if isinstance(c, dict) and c.get("id")), "")
            for item in library.get("items", []) or []:
                if isinstance(item, dict) and item.get("category") == category_id:
                    item["category"] = fallback
    if not found:
        raise HTTPException(status_code=404, detail="分组不存在")
    data = save_prompt_libraries(data)
    return {"library": public_prompt_libraries(data)}

@app.post("/api/asset-library/libraries")
async def create_asset_library(payload: AssetLibraryRequest):
    lib = load_asset_library()
    library = {"id": f"lib_{uuid.uuid4().hex[:12]}", "name": sanitize_asset_name(payload.name, "资产库"), "type": "asset", "categories": []}
    library["categories"].append({"id": f"cat_{uuid.uuid4().hex[:12]}", "name": "默认分组", "type": "image", "items": []})
    lib.setdefault("libraries", []).append(library)
    lib["active_library_id"] = library["id"]
    save_asset_library(lib)
    return {"library": lib, "asset_library": library}

@app.patch("/api/asset-library/libraries/{library_id}")
async def rename_asset_library(library_id: str, payload: AssetLibraryRenameRequest):
    lib = load_asset_library()
    library = find_asset_library(lib, library_id)
    if not library or library.get("id") != library_id:
        raise HTTPException(status_code=404, detail="资产库不存在")
    library["name"] = sanitize_asset_name(payload.name, library.get("name") or "资产库")
    save_asset_library(lib)
    return {"library": lib, "asset_library": library}

@app.delete("/api/asset-library/libraries/{library_id}")
async def delete_asset_library(library_id: str):
    lib = load_asset_library()
    libraries = lib.get("libraries") or []
    if len(libraries) <= 1:
        raise HTTPException(status_code=400, detail="至少保留一个资产库")
    if not any(item.get("id") == library_id for item in libraries):
        raise HTTPException(status_code=404, detail="资产库不存在")
    lib["libraries"] = [item for item in libraries if item.get("id") != library_id]
    if lib.get("active_library_id") == library_id:
        lib["active_library_id"] = lib["libraries"][0].get("id")
    save_asset_library(lib)
    return {"library": lib}

@app.post("/api/asset-library/categories")
async def create_asset_library_category(payload: AssetLibraryCategoryRequest):
    lib = load_asset_library()
    library = find_asset_library(lib, payload.library_id)
    if not library:
        raise HTTPException(status_code=404, detail="资产库不存在")
    category = {"id": f"cat_{uuid.uuid4().hex[:12]}", "name": sanitize_asset_name(payload.name, "新文件夹"), "type": "image", "items": []}
    # 图片分组在 library/ 下建一个真实文件夹，之后该分组的资产都存进这个文件夹，便于在磁盘上管理。
    category["dir"] = unique_asset_category_dir(library, payload.name)
    try:
        os.makedirs(os.path.join(ASSET_LIBRARY_DIR, category["dir"]), exist_ok=True)
    except Exception as exc:
        print(f"创建分组文件夹失败: {exc}")
    library.setdefault("categories", []).append(category)
    lib["active_library_id"] = library.get("id") or lib.get("active_library_id")
    save_asset_library(lib)
    return {"library": lib, "category": category}

@app.patch("/api/asset-library/categories/{category_id}")
async def rename_asset_library_category(category_id: str, payload: AssetLibraryRenameRequest):
    lib = load_asset_library()
    _, cat = find_asset_category_with_library(lib, category_id, payload.library_id)
    if not cat:
        raise HTTPException(status_code=404, detail="分类不存在")
    cat["name"] = sanitize_asset_name(payload.name, cat.get("name") or "新文件夹")
    save_asset_library(lib)
    return {"library": lib, "category": cat}

@app.delete("/api/asset-library/categories/{category_id}")
async def delete_asset_library_category(category_id: str, library_id: str = ""):
    lib = load_asset_library()
    library, cat = find_asset_category_with_library(lib, category_id, library_id)
    if not cat:
        raise HTTPException(status_code=404, detail="分类不存在")
    # 删除分组时一并清理该分组下的本地文件 + 分组文件夹，避免磁盘残留。
    for item in (cat.get("items") or []):
        remove_asset_library_file(item)
    cat_dir = str(cat.get("dir") or "").strip("/").strip()
    if cat_dir:
        try:
            target = os.path.join(ASSET_LIBRARY_DIR, cat_dir)
            if os.path.isdir(target) and os.path.abspath(target).startswith(os.path.abspath(ASSET_LIBRARY_DIR) + os.sep):
                shutil.rmtree(target, ignore_errors=True)
        except Exception as exc:
            print(f"删除分组文件夹失败: {exc}")
    library["categories"] = [c for c in library.get("categories", []) if c.get("id") != category_id]
    save_asset_library(lib)
    return {"library": lib}

@app.post("/api/asset-library/items")
async def add_asset_library_item(payload: AssetLibraryAddRequest):
    lib = load_asset_library()
    cat = find_asset_category_in_library(lib, payload.category_id, payload.library_id)
    if not cat:
        raise HTTPException(status_code=404, detail="分类不存在")
    if cat.get("type") != "image":
        raise HTTPException(status_code=400, detail="该分类暂不支持添加媒体")
    src = output_file_from_url(payload.url)
    if not src:
        raise HTTPException(status_code=400, detail="只支持保存本地 /assets 或 /output 媒体")
    _, item = make_asset_library_item(src, payload.name or os.path.basename(src), subdir=cat.get("dir") or "")
    if item.get("kind") == "image":
        classification = await classify_asset_image_best_effort(output_file_from_url(item.get("url") or "") or src)
        if classification:
            item["classification"] = classification
    cat.setdefault("items", []).append(item)
    save_asset_library(lib)
    return {"library": lib, "item": item}

@app.post("/api/asset-library/items/batch")
async def batch_add_asset_library_items(payload: AssetLibraryBatchAddRequest):
    added = []
    lib = load_asset_library()
    cat = find_asset_category_in_library(lib, payload.category_id, payload.library_id)
    if not cat:
        raise HTTPException(status_code=404, detail="分类不存在")
    if cat.get("type") != "image":
        raise HTTPException(status_code=400, detail="该分类暂不支持添加媒体")
    for entry in (payload.items or [])[:200]:
        entry.category_id = payload.category_id
        entry.library_id = payload.library_id
        src = output_file_from_url(entry.url)
        if not src:
            continue
        _, item = make_asset_library_item(src, entry.name or os.path.basename(src), subdir=cat.get("dir") or "")
        if item.get("kind") == "image":
            classification = await classify_asset_image_best_effort(output_file_from_url(item.get("url") or "") or src)
            if classification:
                item["classification"] = classification
        cat.setdefault("items", []).append(item)
        added.append(item)
    save_asset_library(lib)
    return {"library": lib, "items": added}

@app.get("/api/shared-folders")
async def list_shared_folders():
    data = shared_folders_load()
    folders = []
    for entry in data.get("folders", []):
        abs_path = shared_folder_abs(entry)
        folders.append({
            "id": entry.get("id"),
            "name": entry.get("name") or os.path.basename(abs_path) or abs_path,
            "rel": entry.get("rel") or "",
            "path": abs_path,
            "exists": os.path.isdir(abs_path),
            "created_at": entry.get("created_at"),
        })
    return {"folders": folders}

@app.post("/api/shared-folders")
async def register_shared_folder(payload: SharedFolderRegister):
    abs_path, rel = shared_resolve_register(payload.path)
    name = sanitize_asset_name(payload.name or os.path.basename(abs_path), "共享文件夹")
    with SHARED_FOLDERS_LOCK:
        data = shared_folders_load()
        for entry in data.get("folders", []):
            if os.path.normpath(shared_folder_abs(entry)) == os.path.normpath(abs_path):
                entry["name"] = name
                shared_folders_save(data)
                return {"folder": {**entry, "path": abs_path, "exists": True}}
        entry = {
            "id": f"shared_{uuid.uuid4().hex[:12]}",
            "name": name,
            "rel": rel,
            "created_at": now_ms(),
        }
        data.setdefault("folders", []).append(entry)
        shared_folders_save(data)
    return {"folder": {**entry, "path": abs_path, "exists": True}}

@app.delete("/api/shared-folders/{folder_id}")
async def unregister_shared_folder(folder_id: str):
    with SHARED_FOLDERS_LOCK:
        data = shared_folders_load()
        before = len(data.get("folders", []))
        data["folders"] = [f for f in data.get("folders", []) if f.get("id") != folder_id]
        if len(data["folders"]) == before:
            raise HTTPException(status_code=404, detail="共享文件夹不存在")
        shared_folders_save(data)
    return {"ok": True}

@app.get("/api/shared-folders/{folder_id}/tree")
async def get_shared_folder_tree(folder_id: str):
    entry = shared_folder_by_id(folder_id)
    if not entry:
        raise HTTPException(status_code=404, detail="共享文件夹不存在")
    abs_path = shared_folder_abs(entry)
    if not os.path.isdir(abs_path):
        raise HTTPException(status_code=404, detail="文件夹已不存在")
    tree = scan_shared_tree(folder_id, abs_path, "", entry.get("name") or os.path.basename(abs_path))
    return {"folder": {"id": folder_id, "name": entry.get("name"), "path": abs_path}, "tree": tree}

@app.get("/api/shared-folders/{folder_id}/file")
async def get_shared_folder_file(folder_id: str, path: str = ""):
    entry = shared_folder_by_id(folder_id)
    if not entry:
        raise HTTPException(status_code=404, detail="共享文件夹不存在")
    folder_abs = shared_folder_abs(entry)
    abs_path = shared_child_abs(folder_abs, path)
    if not os.path.isfile(abs_path):
        raise HTTPException(status_code=404, detail="文件不存在")
    ext = os.path.splitext(abs_path)[1].lower()
    if ext not in SHARED_MEDIA_EXTS:
        raise HTTPException(status_code=400, detail="不支持的文件类型")
    return FileResponse(abs_path, media_type=content_type_for_path(abs_path))

@app.post("/api/shared-folders/import")
async def import_shared_folder_files(payload: SharedFolderImport):
    entry = shared_folder_by_id(payload.folder_id)
    if not entry:
        raise HTTPException(status_code=404, detail="共享文件夹不存在")
    folder_abs = shared_folder_abs(entry)
    lib = load_asset_library()
    cat = find_asset_category_in_library(lib, payload.category_id, payload.library_id)
    if not cat:
        raise HTTPException(status_code=404, detail="分类不存在")
    if cat.get("type") != "image":
        raise HTTPException(status_code=400, detail="该分类暂不支持添加媒体")
    added = []
    for rel in (payload.paths or [])[:200]:
        abs_path = shared_child_abs(folder_abs, rel)
        if not os.path.isfile(abs_path):
            continue
        ext = os.path.splitext(abs_path)[1].lower()
        if ext not in SHARED_MEDIA_EXTS:
            continue
        _, item = make_asset_library_item(abs_path, os.path.basename(abs_path), subdir=cat.get("dir") or "")
        if item.get("kind") == "image":
            classification = await classify_asset_image_best_effort(output_file_from_url(item.get("url") or "") or abs_path)
            if classification:
                item["classification"] = classification
        cat.setdefault("items", []).append(item)
        added.append(item)
    save_asset_library(lib)
    return {"library": lib, "items": added}

async def caption_image_with_provider(abs_path, prompt, provider_id, model, ms_model=""):
    llm_provider = get_api_provider(provider_id) if provider_id not in ("modelscope",) else {}
    if is_codex_provider(llm_provider):
        resolved_model = selected_model(model, (llm_provider.get("chat_models") or CODEX_DEFAULT_CHAT_MODELS)[0])
        payload = CanvasLLMRequest(
            message=(prompt or "描述图片").strip() or "描述图片",
            provider=provider_id or "codex",
            model=resolved_model,
            images=[abs_path],
        )
        text, _raw = await codex_chat_text(payload, [])
        return text, resolved_model
    if is_gemini_cli_provider(llm_provider):
        resolved_model = selected_model(model, (llm_provider.get("chat_models") or GEMINI_CLI_DEFAULT_CHAT_MODELS)[0])
        payload = CanvasLLMRequest(
            message=(prompt or "描述图片").strip() or "描述图片",
            provider=provider_id or "gemini-cli",
            model=resolved_model,
            images=[abs_path],
        )
        text, _raw = await gemini_cli_chat_text(payload, [])
        return text, resolved_model
    chat_base, chat_hdrs, resolved_model = resolve_chat_provider(provider_id, model, ms_model)
    is_apimart = is_apimart_provider(llm_provider)
    prompt_text = (prompt or "描述图片").strip() or "描述图片"
    data_url = image_path_to_data_url(abs_path, max_size=1024)
    messages = [{
        "role": "user",
        "content": [
            {"type": "text", "text": prompt_text},
            {"type": "image_url", "image_url": {"url": data_url}},
        ],
    }]
    raw = None
    try:
        async with httpx.AsyncClient(timeout=AI_REQUEST_TIMEOUT) as client:
            req_body = {"model": resolved_model, "messages": messages}
            if is_apimart:
                req_body["stream"] = False
            response = await client.post(
                f"{chat_base}/chat/completions",
                headers=chat_hdrs,
                json=req_body,
            )
            response.raise_for_status()
            raw = response.json()
    except httpx.HTTPStatusError as exc:
        body = exc.response.text or ""
        friendly = friendly_chat_error_detail(body, resolved_model, llm_provider)
        raise HTTPException(status_code=exc.response.status_code, detail=friendly or f"上游接口错误：{body}") from exc
    except httpx.HTTPError as exc:
        log_net_error(f"对话 网络/TLS错误 provider={llm_provider} model={resolved_model}", exc)
        raise HTTPException(status_code=502, detail=f"请求上游接口失败：{exc}") from exc
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"解析上游响应失败：{exc}") from exc
    text = text_from_chat_response(raw).strip() if isinstance(raw, dict) else ""
    return text or "接口返回了空回复。", resolved_model

@app.patch("/api/asset-library/items/{item_id}")
async def rename_asset_library_item(item_id: str, payload: AssetLibraryRenameRequest):
    lib = load_asset_library()
    for library in lib.get("libraries", []):
        for cat in library.get("categories", []):
            for item in cat.get("items", []):
                if item.get("id") == item_id:
                    item["name"] = sanitize_asset_name(payload.name, item.get("name") or "asset")
                    save_asset_library(lib)
                    return {"library": lib, "item": item}
    raise HTTPException(status_code=404, detail="资产不存在")

def find_asset_item_in_library(lib, item_id, library_id=""):
    for library in lib.get("libraries", []):
        if library_id and library.get("id") != library_id:
            continue
        for cat in library.get("categories", []):
            for item in cat.get("items", []):
                if item.get("id") == item_id:
                    return item
    return None

@app.post("/api/asset-library/items/classify")
async def classify_asset_library_items(payload: AssetLibraryClassifyRequest):
    lib = load_asset_library()
    results = []
    changed = False
    for item_id in (payload.ids or [])[:80]:
        item = find_asset_item_in_library(lib, item_id, payload.library_id)
        result = {"id": item_id, "ok": False, "classification": None, "error": ""}
        if not item:
            result["error"] = "资产不存在"
            results.append(result)
            continue
        if asset_library_media_kind(item.get("url") or "") != "image" and item.get("kind") != "image":
            result["error"] = "仅支持图片素材智能分类"
            results.append(result)
            continue
        path = output_file_from_url(item.get("url") or "")
        if not path or not os.path.isfile(path):
            result["error"] = "文件不存在"
            results.append(result)
            continue
        try:
            classification = await classify_image_with_provider(path, payload.provider, payload.model, payload.ms_model, payload.prompt)
            item["classification"] = classification
            changed = True
            result.update({"ok": True, "classification": classification})
        except Exception as exc:
            result["error"] = str(getattr(exc, "detail", "") or exc)
        results.append(result)
    if changed:
        save_asset_library(lib)
    return {"library": lib, "count": sum(1 for item in results if item.get("ok")), "items": results}

@app.post("/api/asset-library/items/{item_id}/register-avatar")
async def register_asset_library_avatar(item_id: str, payload: AssetAvatarRegisterRequest):
    lib = load_asset_library()
    target_item = find_asset_item_in_library(lib, item_id, payload.library_id)
    if not target_item:
        raise HTTPException(status_code=404, detail="资产不存在")
    provider = get_api_provider(payload.provider_id)
    platform = avatar_platform_for_provider(provider)
    if platform not in AVATAR_SUPPORTED_PLATFORMS:
        name = (provider or {}).get("name") or (provider or {}).get("id") or "该平台"
        raise HTTPException(status_code=400, detail=f"「{name}」暂不支持数字人/真人认证（目前仅 APIMart 可用，火山等平台待接入官方资产 API）。")
    kind = str(target_item.get("kind") or "image").lower()
    if kind not in ("image", "video", "audio"):
        kind = "image"
    if platform == "apimart":
        project_name = str(payload.project_name or "default").strip() or "default"
        async with httpx.AsyncClient(timeout=VIDEO_POLL_TIMEOUT) as client:
            public_url = await upload_media_for_apimart(client, provider, target_item.get("url") or "", kind)
        if not valid_apimart_video_image_input(public_url):
            reason = public_url[4:] if isinstance(public_url, str) and public_url.startswith("ERR:") else "无法获取公网可访问地址"
            raise HTTPException(status_code=400, detail=f"素材无法提交到 APIMart：{reason}\n请配置 PUBLIC_BASE_URL，或确认本地文件存在。")
        task_id = await submit_apimart_avatar_asset(
            provider, public_url, target_item.get("name") or "asset", kind,
            project_name=project_name, group_name=payload.group_name,
        )
    elif platform == "volcengine":
        # 火山以 API 设置里配置的 ProjectName 为准（必须与视频生成 key 的项目一致）
        project_name = str(provider.get("volcengine_project_name") or VOLCENGINE_DEFAULT_PROJECT_NAME).strip() or VOLCENGINE_DEFAULT_PROJECT_NAME
        public_url = volcengine_public_asset_url(target_item.get("url") or "")
        if public_url.startswith("ERR:"):
            raise HTTPException(status_code=400, detail=public_url[4:])
        task_id = await submit_volcengine_avatar_asset(
            public_url, target_item.get("name") or "asset", kind,
            project_name=project_name, group_name=payload.group_name or "",
        )
    else:
        raise HTTPException(status_code=400, detail="该平台的认证后端尚未接入。")
    regs = target_item.get("registrations")
    if not isinstance(regs, dict):
        regs = {}
    regs[platform] = {
        "provider_id": provider["id"],
        "project_name": project_name,
        "task_id": task_id,
        "status": "Processing",
        "detail": "已提交，审核中",
        "asset_uri": "",
        "asset_id": "",
        "registered_at": now_ms(),
    }
    target_item["registrations"] = regs
    save_asset_library(lib)
    return {"library": lib, "item": target_item}

@app.post("/api/asset-library/items/{item_id}/avatar-status")
async def check_asset_library_avatar(item_id: str, payload: AssetAvatarRegisterRequest):
    lib = load_asset_library()
    target_item = find_asset_item_in_library(lib, item_id, payload.library_id)
    if not target_item:
        raise HTTPException(status_code=404, detail="资产不存在")
    regs = target_item.get("registrations") if isinstance(target_item.get("registrations"), dict) else {}
    provider = get_api_provider(payload.provider_id or "")
    platform = avatar_platform_for_provider(provider)
    if platform not in AVATAR_SUPPORTED_PLATFORMS:
        raise HTTPException(status_code=400, detail="该平台暂不支持数字人/真人认证审核。")
    reg = regs.get(platform) if isinstance(regs.get(platform), dict) else {}
    task_id = str(reg.get("task_id") or "").strip()
    if not task_id:
        raise HTTPException(status_code=400, detail="该素材还没有提交到这个平台的认证审核。")
    if platform == "apimart":
        result = await check_apimart_avatar_task(provider, task_id)
    elif platform == "volcengine":
        result = await check_volcengine_avatar_task(
            task_id, str(reg.get("project_name") or VOLCENGINE_DEFAULT_PROJECT_NAME).strip() or VOLCENGINE_DEFAULT_PROJECT_NAME,
        )
    else:
        raise HTTPException(status_code=400, detail="该平台的认证后端尚未接入。")
    reg["status"] = result["status"]
    reg["detail"] = result.get("detail") or ""
    if result["status"] == "Active" and result.get("asset_uri"):
        reg["asset_uri"] = result["asset_uri"]
        reg["asset_id"] = result["asset_uri"].replace("asset://", "")
    regs[platform] = reg
    target_item["registrations"] = regs
    save_asset_library(lib)
    return {"library": lib, "item": target_item}

@app.delete("/api/asset-library/items/{item_id}")
async def delete_asset_library_item(item_id: str):
    lib = load_asset_library()
    removed = None
    for library in lib.get("libraries", []):
        for cat in library.get("categories", []):
            keep = []
            for item in cat.get("items", []):
                if item.get("id") == item_id:
                    removed = item
                else:
                    keep.append(item)
            cat["items"] = keep
    if not removed:
        raise HTTPException(status_code=404, detail="资产不存在")
    remove_asset_library_file(removed)  # 同时删除本地文件，避免磁盘上堆积
    save_asset_library(lib)
    return {"library": lib}

@app.post("/api/asset-library/items/delete")
async def batch_delete_asset_library_items(payload: AssetLibraryBatchDeleteRequest):
    ids = {str(item) for item in (payload.ids or []) if str(item)}
    if not ids:
        raise HTTPException(status_code=400, detail="没有选择资产")
    lib = load_asset_library()
    removed = 0
    removed_items = []
    for library in lib.get("libraries", []):
        if payload.library_id and library.get("id") != payload.library_id:
            continue
        for cat in library.get("categories", []):
            keep = []
            for item in cat.get("items", []):
                if item.get("id") in ids:
                    removed += 1
                    removed_items.append(item)
                else:
                    keep.append(item)
            cat["items"] = keep
    for item in removed_items:  # 批量删除同时清理本地文件
        remove_asset_library_file(item)
    save_asset_library(lib)
    return {"library": lib, "removed": removed}

@app.post("/api/asset-library/items/move")
async def batch_move_asset_library_items(payload: AssetLibraryBatchMoveRequest):
    ids = {str(item) for item in (payload.ids or []) if str(item)}
    if not ids:
        raise HTTPException(status_code=400, detail="没有选择资产")
    lib = load_asset_library()
    target_cat = find_asset_category_in_library(lib, payload.target_category_id, payload.target_library_id)
    if not target_cat:
        raise HTTPException(status_code=404, detail="目标分组不存在")
    target_type = target_cat.get("type") or "image"
    moved = []
    for library in lib.get("libraries", []):
        if payload.library_id and library.get("id") != payload.library_id:
            continue
        for cat in library.get("categories", []):
            if (cat.get("type") or "image") != target_type:
                continue
            keep = []
            for item in cat.get("items", []):
                if item.get("id") in ids:
                    moved.append(item)
                else:
                    keep.append(item)
            cat["items"] = keep
    existing_ids = {item.get("id") for item in target_cat.get("items", [])}
    for item in moved:
        if item.get("id") not in existing_ids:
            target_cat.setdefault("items", []).append(item)
            existing_ids.add(item.get("id"))
    save_asset_library(lib)
    return {"library": lib, "moved": len(moved)}

@app.post("/api/asset-library/items/crop")
async def batch_crop_asset_library_items(payload: AssetLibraryBatchCropRequest):
    ids = {str(item) for item in (payload.ids or []) if str(item)}
    if not ids:
        raise HTTPException(status_code=400, detail="没有选择资产")
    lib = load_asset_library()
    target_cat = None
    if payload.target_category_id:
        target_cat = find_asset_category_in_library(lib, payload.target_category_id, payload.target_library_id)
        if not target_cat:
            raise HTTPException(status_code=404, detail="目标分组不存在")
        if target_cat.get("type") != "image":
            raise HTTPException(status_code=400, detail="目标分组不支持媒体")
    added = []
    for library in lib.get("libraries", []):
        if payload.library_id and library.get("id") != payload.library_id:
            continue
        for cat in library.get("categories", []):
            if cat.get("type") != "image":
                continue
            source_items = [item for item in (cat.get("items", []) or []) if item.get("id") in ids]
            for item in source_items:
                src = output_file_from_url(item.get("url") or "")
                if not src or not os.path.isfile(src):
                    continue
                try:
                    with Image.open(src) as img:
                        img = img.convert("RGBA")
                        w, h = img.size
                        side = min(w, h)
                        if side <= 0:
                            continue
                        left = max(0, (w - side) // 2)
                        top = max(0, (h - side) // 2)
                        cropped = img.crop((left, top, left + side, top + side))
                        tmp = tempfile.NamedTemporaryFile(delete=False, suffix=".png")
                        tmp_path = tmp.name
                        tmp.close()
                        try:
                            cropped.save(tmp_path, "PNG")
                            base_name = os.path.splitext(item.get("name") or "asset")[0] + "_crop.png"
                            dest_cat = target_cat or cat
                            _, next_item = make_asset_library_item(tmp_path, base_name, subdir=dest_cat.get("dir") or "")
                            dest_cat.setdefault("items", []).append(next_item)
                            added.append(next_item)
                        finally:
                            try:
                                os.remove(tmp_path)
                            except Exception:
                                pass
                except Exception:
                    continue
    save_asset_library(lib)
    return {"library": lib, "added": len(added), "items": added}

@app.put("/api/canvases/{canvas_id}")
async def update_canvas(canvas_id: str, payload: CanvasSaveRequest):
    def mutate_canvas():
        with CANVAS_LOCK:
            canvas = load_canvas(canvas_id)
            if canvas.get("format") == "firefly-canvas-v1":
                raise HTTPException(status_code=410, detail="核心画布必须通过增量项目接口保存")
            current_updated_at = int(canvas.get("updated_at") or 0)
            if payload.base_updated_at and current_updated_at and int(payload.base_updated_at) < current_updated_at:
                raise HTTPException(status_code=409, detail={
                    "message": "画布已被其他页面更新，已拒绝旧版本覆盖。",
                    "canvas": canvas,
                    "updated_at": current_updated_at,
                })
            canvas["title"] = (payload.title or canvas.get("title") or "未命名画布")[:80]
            canvas["icon"] = (payload.icon or canvas.get("icon") or "layers")[:32]
            canvas["kind"] = normalize_canvas_kind(canvas.get("kind"))
            canvas["nodes"] = payload.nodes
            canvas["connections"] = payload.connections
            if canvas["kind"] == "smart":
                canvas["viewport"] = payload.viewport
            else:
                canvas["viewport"] = canvas.get("viewport") or {"x": 0, "y": 0, "scale": 1}
            canvas["logs"] = payload.logs[-500:]
            canvas["settings"] = payload.settings or {}
            save_canvas(canvas)
            return canvas

    canvas = await asyncio.to_thread(mutate_canvas)
    await manager.broadcast_canvas_updated(canvas_id, int(canvas.get("updated_at") or now_ms()), payload.client_id)
    return {"canvas": canvas}

@app.post("/api/canvases/{canvas_id}/logs/delete")
async def delete_canvas_log(canvas_id: str, payload: DeleteCanvasLogRequest):
    log_id = str(payload.log_id or "").strip()
    if not log_id:
        raise HTTPException(status_code=400, detail="缺少日志 ID")

    def remove_log_record():
        with CANVAS_LOCK:
            canvas = load_canvas(canvas_id)
            if canvas.get("format") == "firefly-canvas-v1":
                raise HTTPException(status_code=410, detail="核心画布历史必须通过项目历史接口修改")
            current_updated_at = int(canvas.get("updated_at") or 0)
            if payload.base_updated_at and current_updated_at and int(payload.base_updated_at) < current_updated_at:
                raise HTTPException(status_code=409, detail={
                    "message": "画布已被其他页面更新，请刷新后重试。",
                    "canvas": canvas,
                    "updated_at": current_updated_at,
                })
            logs = list(canvas.get("logs") or [])
            target = next((item for item in logs if str(item.get("id") or "") == log_id), None)
            if not target:
                raise HTTPException(status_code=404, detail="生成日志不存在")

            candidate_paths = []
            if payload.delete_unreferenced_media:
                for url in collect_local_media_urls(target.get("outputs") or []):
                    path = generated_media_path_from_url(url)
                    if path and path not in candidate_paths:
                        candidate_paths.append(path)

            reset_node_ids = []
            if payload.reset_referencing_nodes and candidate_paths:
                candidate_paths = expand_canvas_generated_media_paths(canvas, candidate_paths)
                reset_node_ids = reset_canvas_result_nodes_for_media(canvas, candidate_paths)

            canvas["logs"] = [item for item in logs if str(item.get("id") or "") != log_id]
            save_canvas(canvas)
            return canvas, candidate_paths, reset_node_ids

    canvas, candidate_paths, reset_node_ids = await asyncio.to_thread(remove_log_record)

    def cleanup_unreferenced_media():
        removed_files = []
        skipped_referenced = []
        removed_previews = 0
        deletable_paths = []
        for path in candidate_paths:
            if persisted_json_references_media_path(path):
                skipped_referenced.append(os.path.basename(path))
                continue
            deletable_paths.append(path)
        prune_generation_history_for_media(deletable_paths)
        for path in deletable_paths:
            try:
                removed_previews += delete_media_preview_cache(path)
                os.remove(path)
                removed_files.append(os.path.basename(path))
            except OSError:
                skipped_referenced.append(os.path.basename(path))
        return removed_files, skipped_referenced, removed_previews

    removed_files = []
    skipped_referenced = []
    removed_previews = 0
    if payload.delete_unreferenced_media:
        def locked_cleanup():
            with CANVAS_LOCK:
                return cleanup_unreferenced_media()
        removed_files, skipped_referenced, removed_previews = await asyncio.to_thread(locked_cleanup)

    await manager.broadcast_canvas_updated(canvas_id, int(canvas.get("updated_at") or now_ms()))
    return {
        "ok": True,
        "canvas": canvas,
        "removed_files": removed_files,
        "removed_previews": removed_previews,
        "reset_node_ids": reset_node_ids,
        "skipped_referenced": skipped_referenced,
    }

@app.delete("/api/canvases/{canvas_id}")
async def delete_canvas(canvas_id: str):
    def soft_delete():
        with CANVAS_LOCK:
            canvas = load_canvas_any(canvas_id)
            if not canvas.get("deleted_at"):
                canvas["deleted_at"] = now_ms()
                save_canvas(canvas)

    await asyncio.to_thread(soft_delete)
    return {"ok": True}

@app.post("/api/canvases/{canvas_id}/restore")
async def restore_canvas(canvas_id: str):
    def restore():
        with CANVAS_LOCK:
            canvas = load_canvas_any(canvas_id)
            if canvas.get("deleted_at"):
                canvas.pop("deleted_at", None)
                save_canvas(canvas)
            return canvas

    canvas = await asyncio.to_thread(restore)
    return {"canvas": canvas}

@app.delete("/api/canvases/{canvas_id}/purge")
async def purge_canvas(canvas_id: str):
    def purge():
        with CANVAS_LOCK:
            path = canvas_path(canvas_id)
            if os.path.exists(path):
                os.remove(path)

    await asyncio.to_thread(purge)
    return {"ok": True}

# --- GPT 对话 ---

@app.post("/api/chat")
async def chat(payload: ChatRequest, request: Request, x_user_id: str = Header(default="")):
    user_id = safe_user_id(x_user_id, request)
    conversation = (
        load_conversation(user_id, payload.conversation_id)
        if payload.conversation_id
        else new_conversation(user_id, display_title(payload.message))
    )
    if not conversation.get("messages"):
        conversation["title"] = display_title(payload.message)

    refs = [ref.dict() for ref in payload.reference_images if ref.url]
    image_refs = image_references(refs)
    user_message = {
        "id": uuid.uuid4().hex,
        "role": "user",
        "content": payload.message,
        "created_at": now_ms(),
        "attachments": refs,
        "mode": payload.mode,
    }
    conversation["messages"].append(user_message)
    conversation["updated_at"] = now_ms()
    save_conversation(user_id, conversation)

    if payload.mode == "image":
        image_provider_id = payload.provider if payload.provider not in {"modelscope"} else "comfly"
        provider = get_api_provider(image_provider_id)
        default_model = (provider.get("image_models") or [IMAGE_MODEL])[0]
        model = selected_model(payload.image_model or payload.model, default_model)
        image_size = chat_prompt_size_override(payload.message, payload.size) or payload.size
        try:
            image_data, raw = await generate_ai_image(payload.message, image_size, payload.quality, model, image_refs, provider["id"])
            local_url = await save_ai_image_to_output(image_data, prefix="chat_")
        except httpx.HTTPStatusError as exc:
            text = exc.response.text or ""
            detail = friendly_image_error_detail(text, image_size, model) or f"上游生图接口错误：{text[:300]}"
            raise HTTPException(status_code=exc.response.status_code, detail=detail) from exc
        except httpx.HTTPError as exc:
            log_net_error(f"对话生图 网络/TLS错误 model={model}", exc)
            raise HTTPException(status_code=502, detail=f"请求上游生图接口失败：{exc}") from exc
        assistant_message = {
            "id": uuid.uuid4().hex,
            "role": "assistant",
            "type": "image",
            "content": payload.message,
            "image_url": local_url,
            "created_at": now_ms(),
            "model": model,
            "size": image_size,
            "raw_usage": raw.get("usage") if isinstance(raw, dict) else None,
        }
    else:
        _codex_provider = get_api_provider(payload.provider)
        if is_codex_provider(_codex_provider):
            model = selected_model(payload.model, (_codex_provider.get("chat_models") or CODEX_DEFAULT_CHAT_MODELS)[0])
            payload.model = model
            text, raw = await codex_chat_text(payload, conversation["messages"][-MAX_HISTORY_MESSAGES:])
            assistant_message = {
                "id": uuid.uuid4().hex,
                "role": "assistant",
                "content": text,
                "created_at": now_ms(),
                "model": model,
                "raw_usage": None,
                "raw": raw,
            }
            conversation["messages"].append(assistant_message)
            conversation["updated_at"] = now_ms()
            save_conversation(user_id, conversation)
            return {"conversation": conversation, "message": assistant_message}
        if is_gemini_cli_provider(_codex_provider):
            model = selected_model(payload.model, (_codex_provider.get("chat_models") or GEMINI_CLI_DEFAULT_CHAT_MODELS)[0])
            payload.model = model
            text, raw = await gemini_cli_chat_text(payload, conversation["messages"][-MAX_HISTORY_MESSAGES:])
            assistant_message = {
                "id": uuid.uuid4().hex,
                "role": "assistant",
                "content": text,
                "created_at": now_ms(),
                "model": model,
                "raw_usage": None,
                "raw": raw,
            }
            conversation["messages"].append(assistant_message)
            conversation["updated_at"] = now_ms()
            save_conversation(user_id, conversation)
            return {"conversation": conversation, "message": assistant_message}
        chat_base, chat_hdrs, model = resolve_chat_provider(payload.provider, payload.model, payload.ms_model)
        _conv_provider = get_api_provider(payload.provider) if payload.provider not in ("modelscope",) else {}
        _conv_is_apimart = is_apimart_provider(_conv_provider)
        history = conversation["messages"][-MAX_HISTORY_MESSAGES:]
        upstream_messages = [{"role": "system", "content": chat_system_prompt(payload)}]
        for item in history:
            msg = upstream_message_from_record(item)
            if msg:
                upstream_messages.append(msg)
        try:
            async with httpx.AsyncClient(timeout=AI_REQUEST_TIMEOUT) as client:
                conv_req_body = {"model": model, "messages": upstream_messages}
                if _conv_is_apimart:
                    conv_req_body["stream"] = False
                response = await client.post(
                    f"{chat_base}/chat/completions",
                    headers=chat_hdrs,
                    json=conv_req_body,
                )
                response.raise_for_status()
                raw = response.json()
        except httpx.HTTPStatusError as exc:
            body = exc.response.text or ""
            friendly = friendly_chat_error_detail(body, model, _conv_provider)
            raise HTTPException(status_code=exc.response.status_code, detail=friendly or f"上游接口错误：{body}") from exc
        except httpx.HTTPError as exc:
            raise HTTPException(status_code=502, detail=f"请求上游接口失败：{exc}") from exc
        raw_data = unwrap_apimart_response(raw) if isinstance(raw, dict) else raw
        assistant_message = {
            "id": uuid.uuid4().hex,
            "role": "assistant",
            "content": text_from_chat_response(raw).strip() or "接口返回了空回复。",
            "created_at": now_ms(),
            "model": model,
            "raw_usage": raw_data.get("usage") if isinstance(raw_data, dict) else None,
        }

    conversation["messages"].append(assistant_message)
    conversation["updated_at"] = now_ms()
    save_conversation(user_id, conversation)
    return {"conversation": conversation, "message": assistant_message}

@app.post("/api/chat/agent")
async def chat_agent(payload: ChatRequest, request: Request, x_user_id: str = Header(default="")):
    user_id = safe_user_id(x_user_id, request)
    conversation = (
        load_conversation(user_id, payload.conversation_id)
        if payload.conversation_id
        else new_conversation(user_id, display_title(payload.message))
    )
    if not conversation.get("messages"):
        conversation["title"] = display_title(payload.message)

    refs = [ref.dict() for ref in payload.reference_images if ref.url]
    image_refs = image_references(refs)
    user_message = {
        "id": uuid.uuid4().hex,
        "role": "user",
        "content": payload.message,
        "created_at": now_ms(),
        "attachments": refs,
        "mode": "agent",
    }
    conversation["messages"].append(user_message)
    conversation["updated_at"] = now_ms()
    save_conversation(user_id, conversation)

    decision = await decide_chat_agent_action(payload, conversation, image_refs)
    action = decision.get("action") or "chat"
    tool_refs = image_refs[:]
    inherited_size = ""
    if action == "edit_image" and not tool_refs:
        tool_refs = latest_chat_image_refs(conversation, 1)
        inherited_size = image_size_from_reference(tool_refs[0]) if tool_refs else ""
    if action == "edit_image" and not tool_refs:
        action = "generate_image"

    if action in {"generate_image", "edit_image"}:
        image_provider = pick_chat_image_provider(payload.image_provider or payload.provider, payload.provider)
        default_model = (image_provider.get("image_models") or [IMAGE_MODEL])[0]
        model = selected_model(payload.image_model or default_model, default_model)
        prompt = decision.get("prompt") or payload.message
        prompt_size = chat_prompt_size_override(payload.message, payload.size) or chat_prompt_size_override(prompt, payload.size)
        image_size = snap_size_to_multiple(prompt_size or inherited_size or payload.size, 16)
        requested_count = 1 if action == "edit_image" else chat_requested_image_count(payload.message)
        prompts = chat_split_parallel_prompts(prompt, requested_count)
        local_urls = []
        raw_items = []
        try:
            for item_prompt in prompts:
                image_data, raw = await generate_ai_image(item_prompt, image_size, payload.quality, model, tool_refs, image_provider["id"])
                local_urls.append(await save_ai_image_to_output(image_data, prefix="chat_"))
                raw_items.append(raw)
        except httpx.HTTPStatusError as exc:
            text = exc.response.text or ""
            detail = friendly_image_error_detail(text, image_size, model) or f"上游生图接口错误：{text[:300]}"
            raise HTTPException(status_code=exc.response.status_code, detail=detail) from exc
        except httpx.HTTPError as exc:
            log_net_error(f"对话生图 网络/TLS错误 model={model}", exc)
            raise HTTPException(status_code=502, detail=f"请求上游生图接口失败：{exc}") from exc
        local_url = local_urls[0] if local_urls else ""
        assistant_message = {
            "id": uuid.uuid4().hex,
            "role": "assistant",
            "type": "image",
            "content": prompt,
            "image_url": local_url,
            "image_urls": local_urls,
            "created_at": now_ms(),
            "model": model,
            "provider": image_provider["id"],
            "size": image_size,
            "image_count": len(local_urls),
            "prompts": prompts,
            "agent_action": action,
            "agent_reply": decision.get("reply") or "",
            "used_references": tool_refs,
            "raw_usage": raw_items[0].get("usage") if raw_items and isinstance(raw_items[0], dict) else None,
        }
    else:
        assistant_message = await build_chat_text_reply(payload, conversation)
        assistant_message["agent_action"] = "chat"

    conversation["messages"].append(assistant_message)
    conversation["updated_at"] = now_ms()
    save_conversation(user_id, conversation)
    return {"conversation": conversation, "message": assistant_message, "agent": {"action": action, "decision": decision}}

@app.post("/api/chat/stream")
async def chat_stream(payload: ChatRequest, request: Request, x_user_id: str = Header(default="")):
    if payload.mode == "image":
        raise HTTPException(status_code=400, detail="图片模式请使用 /api/chat")

    user_id = safe_user_id(x_user_id, request)
    conversation = (
        load_conversation(user_id, payload.conversation_id)
        if payload.conversation_id
        else new_conversation(user_id, display_title(payload.message))
    )
    if not conversation.get("messages"):
        conversation["title"] = display_title(payload.message)

    refs = [ref.dict() for ref in payload.reference_images if ref.url]
    user_message = {
        "id": uuid.uuid4().hex,
        "role": "user",
        "content": payload.message,
        "created_at": now_ms(),
        "attachments": refs,
        "mode": payload.mode,
    }
    conversation["messages"].append(user_message)
    conversation["updated_at"] = now_ms()
    save_conversation(user_id, conversation)

    _codex_provider = get_api_provider(payload.provider)
    if is_codex_provider(_codex_provider):
        model = selected_model(payload.model, (_codex_provider.get("chat_models") or CODEX_DEFAULT_CHAT_MODELS)[0])
        payload.model = model

        async def codex_stream():
            yield sse_event({"type": "meta", "conversation": conversation})
            try:
                text, raw = await codex_chat_text(payload, conversation["messages"][-MAX_HISTORY_MESSAGES:])
            except HTTPException as exc:
                yield sse_event({"type": "error", "detail": exc.detail})
                return
            assistant_message = {
                "id": uuid.uuid4().hex,
                "role": "assistant",
                "content": text,
                "created_at": now_ms(),
                "model": model,
                "raw_usage": None,
                "raw": raw,
            }
            conversation["messages"].append(assistant_message)
            conversation["updated_at"] = now_ms()
            save_conversation(user_id, conversation)
            yield sse_event({"type": "delta", "delta": text})
            yield sse_event({"type": "done", "conversation": conversation, "message": assistant_message})

        return StreamingResponse(codex_stream(), media_type="text/event-stream")

    if is_gemini_cli_provider(_codex_provider):
        model = selected_model(payload.model, (_codex_provider.get("chat_models") or GEMINI_CLI_DEFAULT_CHAT_MODELS)[0])
        payload.model = model

        async def gemini_cli_stream():
            yield sse_event({"type": "meta", "conversation": conversation})
            try:
                text, raw = await gemini_cli_chat_text(payload, conversation["messages"][-MAX_HISTORY_MESSAGES:])
            except HTTPException as exc:
                yield sse_event({"type": "error", "detail": exc.detail})
                return
            assistant_message = {
                "id": uuid.uuid4().hex,
                "role": "assistant",
                "content": text,
                "created_at": now_ms(),
                "model": model,
                "raw_usage": None,
                "raw": raw,
            }
            conversation["messages"].append(assistant_message)
            conversation["updated_at"] = now_ms()
            save_conversation(user_id, conversation)
            yield sse_event({"type": "delta", "delta": text})
            yield sse_event({"type": "done", "conversation": conversation, "message": assistant_message})

        return StreamingResponse(gemini_cli_stream(), media_type="text/event-stream")

    chat_base, chat_hdrs, model = resolve_chat_provider(payload.provider, payload.model, payload.ms_model)
    _stream_provider = get_api_provider(payload.provider) if payload.provider not in ("modelscope",) else {}
    history = conversation["messages"][-MAX_HISTORY_MESSAGES:]
    upstream_messages = [{"role": "system", "content": chat_system_prompt(payload)}]
    for item in history:
        msg = upstream_message_from_record(item)
        if msg:
            upstream_messages.append(msg)

    async def stream():
        content_parts = []
        raw_usage = None
        yield sse_event({"type": "meta", "conversation": conversation})
        try:
            async with httpx.AsyncClient(timeout=AI_REQUEST_TIMEOUT) as client:
                async with client.stream(
                    "POST",
                    f"{chat_base}/chat/completions",
                    headers=chat_hdrs,
                    json={"model": model, "messages": upstream_messages, "stream": True},
                ) as response:
                    if response.status_code >= 400:
                        detail = await response.aread()
                        body = detail.decode("utf-8", errors="ignore")
                        friendly = friendly_chat_error_detail(body, model, _stream_provider)
                        yield sse_event({"type": "error", "detail": friendly or f"上游接口错误：{body}"})
                        return
                    async for line in response.aiter_lines():
                        if not line:
                            continue
                        if line.startswith("data:"):
                            line = line[5:].strip()
                        if line == "[DONE]":
                            break
                        try:
                            chunk = json.loads(line)
                        except json.JSONDecodeError:
                            continue
                        if isinstance(chunk, dict) and chunk.get("usage"):
                            raw_usage = chunk.get("usage")
                        delta = text_delta_from_chat_chunk(chunk)
                        if delta:
                            content_parts.append(delta)
                            yield sse_event({"type": "delta", "delta": delta})
        except httpx.HTTPError as exc:
            log_net_error("对话(流式) 网络/TLS错误", exc)
            yield sse_event({"type": "error", "detail": f"请求上游接口失败：{exc}"})
            return

        assistant_message = {
            "id": uuid.uuid4().hex,
            "role": "assistant",
            "content": "".join(content_parts).strip() or "接口返回了空回复。",
            "created_at": now_ms(),
            "model": model,
            "raw_usage": raw_usage,
        }
        conversation["messages"].append(assistant_message)
        conversation["updated_at"] = now_ms()
        save_conversation(user_id, conversation)
        yield sse_event({"type": "done", "conversation": conversation, "message": assistant_message})

    return StreamingResponse(stream(), media_type="text/event-stream")

# --- 历史记录 ---

@app.get("/api/history")
async def get_history_api(type: str = None):
    if os.path.exists(HISTORY_FILE):
        try:
            with open(HISTORY_FILE, 'r', encoding='utf-8') as f:
                data = json.load(f)
                if type:
                    data = [item for item in data if item.get("type", "zimage") == type]
                data = [item for item in data if item.get("images") and len(item["images"]) > 0]

                def sort_key(item):
                    ts = item.get("timestamp", 0)
                    if isinstance(ts, (int, float)):
                        return float(ts)
                    return 0

                data.sort(key=sort_key, reverse=True)
                return data
        except Exception as e:
            print(f"读取历史文件失败: {e}")
            return []
    return []

@app.get("/api/queue_status")
async def get_queue_status(client_id: str):
    with QUEUE_LOCK:
        total = len(QUEUE)
        positions = [i + 1 for i, t in enumerate(QUEUE) if t["client_id"] == client_id]
        position = positions[0] if positions else 0
    return {"total": total, "position": position}

@app.post("/api/history/delete")
async def delete_history(req: DeleteHistoryRequest):
    if not os.path.exists(HISTORY_FILE):
        return {"success": False, "message": "History file not found"}
    try:
        with HISTORY_LOCK:
            with open(HISTORY_FILE, 'r', encoding='utf-8') as f:
                history = json.load(f)
            target_record = None
            new_history = []
            for item in history:
                is_match = False
                item_ts = item.get("timestamp", 0)
                if isinstance(req.timestamp, (int, float)) and isinstance(item_ts, (int, float)):
                    if abs(float(item_ts) - float(req.timestamp)) < 0.001:
                        is_match = True
                elif str(item_ts) == str(req.timestamp):
                    is_match = True
                if is_match:
                    target_record = item
                else:
                    new_history.append(item)
            if target_record:
                with open(HISTORY_FILE, 'w', encoding='utf-8') as f:
                    json.dump(new_history, f, ensure_ascii=False, indent=4)

        if target_record:
            for img_url in target_record.get("images", []):
                file_path = output_file_from_url(img_url)
                if file_path and os.path.exists(file_path):
                    try:
                        os.remove(file_path)
                    except Exception as e:
                        print(f"Failed to delete file {file_path}: {e}")
            return {"success": True}
        else:
            return {"success": False, "message": "Record not found"}
    except Exception as e:
        print(f"Delete history error: {e}")
        return {"success": False, "message": str(e)}

# ModelScope 旧角度/专用任务接口已删除；角度提示词统一走在线图片生成。


# ModelScope 旧直连生图接口已删除；统一使用在线图片适配器。


# --- 在线生成适配器结束 ---

# 本地运行时与旧专用生成入口已删除；画布只使用在线适配器。


if __name__ == "__main__":
    import uvicorn
    # 关闭服务端协议级 WebSocket ping：部分客户端（如 PS UXP 面板）不会自动回 pong，
    # 默认 20s ping/20s 超时会把这些连接每隔一会儿就踢掉造成"频繁断连"。
    # 客户端有自己的应用层心跳 + 断线重连兜底，这里禁用协议 ping 更稳。
    uvicorn.run(app, host="127.0.0.1", port=3000,
                ws_ping_interval=None, ws_ping_timeout=None)
