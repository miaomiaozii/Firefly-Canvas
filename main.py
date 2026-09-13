"""Firefly Canvas server - app assembly (auto-split from monolith)."""
import logging
import sys

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    stream=sys.stdout,
)

logger = logging.getLogger(__name__)

from contextlib import asynccontextmanager

from app.core import *  # noqa: F401,F403
import app.core as _core_runtime

import os as _os_boot
import sys as _sys_boot
_os_boot.environ.setdefault("FIREFLY_MAIN_MODULE", __name__)
import app.services as _svc  # W1c services layer
from app.services import *
api_headers = _svc.api_headers
get_api_provider_exact = _svc.get_api_provider_exact

codex_cli_executable = _svc.codex_cli_executable
fetch_runninghub_model_registry = _svc.fetch_runninghub_model_registry
generate_ai_image = _svc.generate_ai_image
generate_project_video = _svc.generate_project_video
get_api_provider = _svc.get_api_provider
load_api_providers = _svc.load_api_providers
migrate_mislabeled_image_extensions = _svc.migrate_mislabeled_image_extensions
preflight_project_video_providers = _svc.preflight_project_video_providers
project_conversation_adapter = _svc.project_conversation_adapter
provider_env_key_value = _svc.provider_env_key_value
resolve_chat_provider = _svc.resolve_chat_provider
save_remote_video_to_output = _svc.save_remote_video_to_output
update_env_values = _svc.update_env_values

from app.services import (_agent_call_adapter, _agent_mark_task_terminal, _agent_result_node, _agent_submit_task, _agent_task_node, _local_upload_abs, _local_upload_caption_path, _local_upload_classification_path, _local_upload_item, _local_upload_safe_folder, _local_upload_safe_path, _local_upload_tree_and_items, _photoshop_mark_returned, _photoshop_request_snapshot, _read_local_upload_caption, _read_local_upload_classification, _update_env_values_unlocked, _write_local_upload_classification)

from app.core import _AGENT_FORBIDDEN_VALUE_TOKENS, _AGENT_IMAGE_PARAM_SCHEMA, _AGENT_PROMPT_PARAM_SCHEMA, _AGENT_TASK_INPUT_KEYS, _AGENT_VIDEO_PARAM_SCHEMA, _DOUBLE_EXT_MEDIA, _DOUBLE_EXT_RE, _MODELSCOPE_CONFIGURED_CHAT_MODELS, _agent_clean_params, _agent_execution_tasks_from_plan, _agent_find_task_node, _agent_forbidden_value, _agent_history_output, _agent_int_param, _agent_memory_task_active, _agent_nodes_by_id, _agent_normalize_candidates, _agent_normalize_task, _agent_plan_from_node, _agent_plan_hash, _agent_plan_response, _agent_public_task, _agent_recovery_payload, _agent_task_canonical, _agent_task_layout, _agent_task_specs, _collect_video_url, _conversation_pending_task_keys_from_canvas, _ensure_photoshop_loopback, _local_upload_display_name, _local_upload_folder_node, _local_upload_kind_ext, _local_upload_rel_path, _local_upload_safe_file_stem, _local_upload_safe_folder_name, _offline_local_edit_adapter, _photoshop_protocol_error, _photoshop_public_request, _photoshop_reset_processing, _photoshop_validate_display_name, _photoshop_validate_media_type, _responses_wrap, _rh_field_cmp, _rh_natural_cmp, _safe_asset_tag, _sniff_image_ext, _sniff_image_ext_bytes, _storage_abs_path, _volc_hmac, _xlsx_cell_text, _xlsx_join_text, _xlsx_shared_strings, _xlsx_sheet_paths, _xml_local_name, _yuli_model_norm  # noqa: F401

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

app.add_exception_handler(RequestValidationError, request_validation_exception_handler)
app.add_exception_handler(JimengPendingError, jimeng_pending_exception_handler)

DIAGNOSTICS_LOG = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'data', 'canvas-diagnostics.log')

@app.post('/api/canvas-diagnostics')
async def canvas_diagnostics(request: Request):
    ensure_same_origin_request(request)
    try:
        body = await request.json()
    except Exception:
        body = {}
    try:
        line = json.dumps(body, ensure_ascii=False)
        with open(DIAGNOSTICS_LOG, 'a', encoding='utf-8') as handle:
            handle.write(line + '\n')
    except Exception:
        pass
    return {'ok': True}

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
        logger.info(f"WS Connected. Total: {len(self.active_connections)}, Online: {self.online_count()}")
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
                    logger.warning(f"Photoshop pending replay error: {exc}")
                    break

    async def disconnect(self, websocket: WebSocket, client_id: str = None):
        if websocket in self.active_connections:
            self.active_connections.remove(websocket)
        self.connection_clients.pop(websocket, None)
        if client_id and self.user_connections.get(client_id) is websocket:
            del self.user_connections[client_id]
        logger.info(f"WS Disconnected. Total: {len(self.active_connections)}, Online: {self.online_count()}")
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
                logger.warning(f"Broadcast error: {e}")
                self.active_connections.remove(connection)

    async def broadcast_new_image(self, image_data: dict):
        data = json.dumps({"type": "new_image", "data": image_data})
        for connection in self.active_connections[:]:
            try:
                await connection.send_text(data)
            except Exception as e:
                logger.warning(f"Broadcast image error: {e}")
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
                logger.warning(f"Broadcast canvas error: {e}")
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
                logger.warning(f"Broadcast asset library error: {e}")
                self.active_connections.remove(connection)

    async def send_personal_message(self, message: dict, client_id: str):
        ws = self.user_connections.get(client_id)
        if ws:
            try:
                await ws.send_text(json.dumps(message))
            except Exception as e:
                logger.warning(f"Personal message error for {client_id}: {e}")

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
                logger.warning(f"Broadcast Photoshop round-trip error: {exc}")
                if connection in self.active_connections:
                    self.active_connections.remove(connection)

async def startup_event():
    global GLOBAL_LOOP, PROJECT_MEDIA_IMPORT_ACCEPTING, PROJECT_MEDIA_IMPORT_SHUTTING_DOWN, PROJECT_CONVERSATION_RUNNER
    GLOBAL_LOOP = asyncio.get_running_loop()
    _core_runtime.GLOBAL_LOOP = GLOBAL_LOOP
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
        logger.warning(f"资产库分组整理失败: {exc}")
    # 修复历史遗留的双重扩展名素材（foo.png.png → foo.png），否则这些卡片无法显示
    try:
        await asyncio.to_thread(migrate_double_extension_uploads)
    except Exception as exc:
        logger.warning(f"修复双重扩展名素材失败: {exc}")
    # 纠正内容与扩展名不符的图片（如 WebP 内容却叫 .png），否则严格客户端解不出来
    try:
        await asyncio.to_thread(migrate_mislabeled_image_extensions)
    except Exception as exc:
        logger.warning(f"纠正图片扩展名失败: {exc}")
    await PROJECT_CONVERSATION_RUNNER.start()
    try:
        recovered = await asyncio.to_thread(recover_project_agent_tasks)
        if recovered:
            logger.info(f"Agent 任务恢复提交: {recovered}")
    except Exception as exc:
        logger.error(f"Agent 任务恢复失败: {exc}")
    # issue #95：启动对账 Photoshop 往返请求表（持久化 + 崩溃状态修复）。
    try:
        recovered_tables = await asyncio.to_thread(_photoshop_recover_round_trips)
        if recovered_tables:
            logger.info(f"Photoshop 往返请求表对账: {recovered_tables} 个项目表")
    except Exception as exc:
        logger.error(f"Photoshop 往返请求表对账失败: {exc}")

async def shutdown_event():
    global GLOBAL_LOOP, PROJECT_MEDIA_IMPORT_EXECUTOR, PROJECT_MEDIA_IMPORT_ACCEPTING, PROJECT_MEDIA_IMPORT_SHUTTING_DOWN, PROJECT_CONVERSATION_RUNNER
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
    # Persist an explicit terminal while the store is still open, then stop
    # the actual shared runtime before allowing ProjectStore to close.
    await _r_api_project_library.shutdown_project_generation_tasks()
    with CANVAS_TASK_LOCK:
        generation_executor = _core_runtime.PROJECT_IMAGE_GENERATION_EXECUTOR
        _core_runtime.PROJECT_IMAGE_GENERATION_EXECUTOR = None
        generation_futures = [
            *PROJECT_IMAGE_GENERATION_FUTURES.values(),
            *PROJECT_VIDEO_GENERATION_FUTURES.values(),
        ]
    async_generation_tasks = [future for future in generation_futures if isinstance(future, asyncio.Task)]
    for future in generation_futures:
        if not future.done():
            future.cancel()
    if async_generation_tasks:
        await asyncio.gather(*async_generation_tasks, return_exceptions=True)
    if generation_executor is not None:
        await asyncio.to_thread(generation_executor.shutdown, wait=True, cancel_futures=True)
    with CANVAS_TASK_LOCK:
        PROJECT_IMAGE_GENERATION_FUTURES.clear()
        PROJECT_VIDEO_GENERATION_FUTURES.clear()
    GLOBAL_LOOP = None
    _core_runtime.GLOBAL_LOOP = None
    await asyncio.to_thread(project_store_call, "close")


@asynccontextmanager
async def lifespan_context(_app):
    await startup_event()
    yield
    await shutdown_event()


app.router.lifespan_context = lifespan_context

def load_storage_settings():
    raw = {}
    try:
        if os.path.exists(STORAGE_SETTINGS_FILE):
            with open(STORAGE_SETTINGS_FILE, "r", encoding="utf-8-sig") as f:
                raw = json.load(f) if f else {}
    except Exception as exc:
        logger.warning(f"加载存储目录设置失败: {exc}")
        raw = {}
    dirs = {}
    for key, fallback in DEFAULT_STORAGE_DIRS.items():
        dirs[key] = _storage_abs_path((raw or {}).get(key), fallback)
    return {"dirs": dirs}


def apply_storage_settings(dirs=None):
    global OUTPUT_INPUT_DIR, OUTPUT_OUTPUT_DIR, LOCAL_UPLOAD_DIR
    dirs = dirs or load_storage_settings().get("dirs") or {}
    OUTPUT_INPUT_DIR = dirs.get("upload") or OUTPUT_INPUT_DIR
    OUTPUT_OUTPUT_DIR = dirs.get("generated") or OUTPUT_OUTPUT_DIR
    LOCAL_UPLOAD_DIR = dirs.get("local") or LOCAL_UPLOAD_DIR

def ensure_runtime_config_files():
    """首次运行时提前创建配置目录，避免第一次保存 API Key 时才创建目录/文件。"""
    try:
        os.makedirs(os.path.dirname(API_ENV_FILE), exist_ok=True)
        os.makedirs(DATA_DIR, exist_ok=True)
        if not os.path.exists(API_ENV_FILE):
            with open(API_ENV_FILE, "a", encoding="utf-8"):
                pass
    except Exception as e:
        logger.warning(f"初始化 API 配置目录失败: {e}")

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
                if value.startswith("dpapi:"):
                    continue
                os.environ.setdefault(key, value)
    except Exception as e:
        logger.warning(f"加载 API/.env 失败: {e}")

@app.exception_handler(RequestValidationError)
async def request_validation_exception_handler(request: Request, exc: RequestValidationError):
    return JSONResponse(
        status_code=422,
        content={"detail": friendly_validation_error(exc.errors()), "errors": exc.errors()},
    )














def now_ms():
    return int(time.time() * 1000)






























@app.exception_handler(JimengPendingError)
async def jimeng_pending_exception_handler(request: Request, exc: JimengPendingError):
    # 轮询超时但任务还在云端排队：返回 202 + submit_id，让前端保持「排队中」卡片并续查
    return JSONResponse(status_code=202, content=jimeng_pending_payload(exc))

























@app.middleware("http")
async def no_cache_static_assets(request: Request, call_next):
    # 前端迭代频繁，静态资源一律 no-cache，避免浏览器缓存旧 JS/CSS（画布 UI 反复出旧布局的根因）。
    response = await call_next(request)
    if request.url.path.startswith("/static/"):
        response.headers["Cache-Control"] = "no-cache"
    return response


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



def migrate_asset_library_into_dirs():
    """一次性整理：给所有图片分组（含默认的角色/场景）补上真实文件夹，并把仍在 library/ 根目录的
    素材文件搬进各自分组的文件夹、同步更新 URL。幂等：已经在子文件夹里的不动；可安全反复执行。"""
    try:
        lib = load_asset_library()
    except Exception as exc:
        logger.warning(f"资产库分组整理：加载失败 {exc}")
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
                logger.warning(f"资产库分组整理：建文件夹失败 {exc}")
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
                    logger.warning(f"资产库分组整理：搬运 {fname} 失败 {exc}")
    if changed:
        try:
            save_asset_library(lib)
        except Exception as exc:
            logger.warning(f"资产库分组整理：保存失败 {exc}")

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









































def project_store_call(method_name: str, *args, **kwargs):
    with PROJECT_LIBRARY_SWITCH_LOCK:
        store = FIREFLY_PROJECT_STORE
        return getattr(store, method_name)(*args, **kwargs)






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





























def _photoshop_prune_requests_locked(now: Optional[int] = None) -> None:
    now_value = int(now if now is not None else now_ms())
    changed_projects = set()
    for request_id, record in list(PHOTOSHOP_ROUND_TRIP_REQUESTS.items()):
        expires_at = int(record.get("expires_at_ms") or 0)
        if record.get("state") == "pending" and now_value >= expires_at:
            record["state"] = "expired"
            changed_projects.add(str(record.get("project_id") or ""))
        terminal_at = int(record.get("returned_at") or 0) * 1000 or expires_at
        if record.get("state") in {"returned", "expired"} and now_value - terminal_at > PHOTOSHOP_ROUND_TRIP_RETENTION_MS:
            PHOTOSHOP_ROUND_TRIP_REQUESTS.pop(request_id, None)
            changed_projects.add(str(record.get("project_id") or ""))
    overflow = max(0, len(PHOTOSHOP_ROUND_TRIP_REQUESTS) - PHOTOSHOP_ROUND_TRIP_MAX_REQUESTS)
    if overflow > 0:
        terminal = sorted(
            ((int(record.get("returned_at") or 0) * 1000 or int(record.get("expires_at_ms") or 0), request_id)
             for request_id, record in PHOTOSHOP_ROUND_TRIP_REQUESTS.items()
             if record.get("state") in {"returned", "expired"}),
        )
        for _, request_id in terminal[:overflow]:
            removed = PHOTOSHOP_ROUND_TRIP_REQUESTS.pop(request_id, None)
            if removed:
                changed_projects.add(str(removed.get("project_id") or ""))
    # issue #95：状态变化（过期/清除/容量淘汰）后同步落盘，避免重启后状态回退。
    for project_id in sorted(p for p in changed_projects if p):
        _photoshop_persist_table_locked(project_id)


def _photoshop_persist_table_locked(project_id: str) -> None:
    """把项目的当前往返请求表落盘（issue #95）。调用方必须持有 PHOTOSHOP_ROUND_TRIP_LOCK。"""
    with PROJECT_LIBRARY_SWITCH_LOCK:
        store = FIREFLY_PROJECT_STORE
    if store is None:
        return
    table = {
        request_id: copy.deepcopy(record)
        for request_id, record in PHOTOSHOP_ROUND_TRIP_REQUESTS.items()
        if str(record.get("project_id")) == str(project_id)
    }
    try:
        store.save_photoshop_round_trips(str(project_id), table)
    except (OSError, ValueError, ProjectStoreError) as exc:
        logger.warning(f"Photoshop 往返请求表落盘失败: {exc}")


def _photoshop_recover_round_trips() -> int:
    """启动对账（issue #95）：把各项目的往返请求表加载回内存并修复状态。

    - 未过期 pending：原样恢复（PS 端可继续调 return）；
    - processing（进程在提交中途崩溃）：若画布已存在该 request_id 的结果节点
      → 补记 returned（提交成功但状态未及更新）；否则回退 pending 允许 PS 端重试；
    - 超龄 pending 标记 expired；超保留期的终态丢弃。
    返回有落盘修正的项目表数量。
    """
    with PROJECT_LIBRARY_SWITCH_LOCK:
        store = FIREFLY_PROJECT_STORE
    if store is None:
        return 0
    now_value = now_ms()
    recovered = 0
    with PHOTOSHOP_ROUND_TRIP_LOCK:
        for project in store.list_projects():
            project_id = str(project.get("id") or "")
            if not project_id:
                continue
            try:
                table = store.load_photoshop_round_trips(project_id)
            except (OSError, ValueError) as exc:
                logger.warning(f"项目 {project_id} 往返请求表加载失败: {exc}")
                continue
            if not table:
                continue
            # 直接读画布文件（不走 open_project）：避免启动时预热全部画布缓存、
            # 也避免触发画布损坏自动重写副作用。
            committed_by_request: Dict[str, Dict[str, Any]] = {}
            try:
                canvas = json.loads((store.root / project_id / "canvas.json").read_text(encoding="utf-8"))
                for node in canvas.get("nodes") or []:
                    if isinstance(node, dict) and str(node.get("type") or "") == "ps_edit_result" and node.get("request_id"):
                        committed_by_request[str(node["request_id"])] = node
            except (OSError, ValueError, json.JSONDecodeError):
                pass
            keep: Dict[str, Dict[str, Any]] = {}
            dirty = False
            for request_id, raw_record in table.items():
                record = copy.deepcopy(raw_record)
                record["project_id"] = project_id
                record["request_id"] = request_id
                state = record.get("state")
                if state == "processing":
                    node = committed_by_request.get(request_id)
                    if node is not None:
                        image = next((im for im in node.get("images") or [] if isinstance(im, dict) and im.get("asset_id")), None)
                        record.update({
                            "state": "returned",
                            "returned_at": now_value // 1000,
                            "result_node_id": str(node.get("id") or ""),
                            "result_media_id": str(image.get("asset_id") or "") if image else "",
                        })
                    else:
                        record["state"] = "pending"
                    state = record["state"]
                    dirty = True
                if state == "pending" and now_value >= int(record.get("expires_at_ms") or 0):
                    record["state"] = "expired"
                    state = "expired"
                    dirty = True
                if state == "returned":
                    terminal_at = int(record.get("returned_at") or 0) * 1000 or int(record.get("expires_at_ms") or 0)
                else:
                    terminal_at = int(record.get("expires_at_ms") or 0)
                if state in {"returned", "expired"} and now_value - terminal_at > PHOTOSHOP_ROUND_TRIP_RETENTION_MS:
                    dirty = True
                    continue
                if state != raw_record.get("state"):
                    dirty = True
                keep[request_id] = record
            if dirty or len(keep) != len(table):
                store.save_photoshop_round_trips(project_id, keep)
            PHOTOSHOP_ROUND_TRIP_REQUESTS.update({key: copy.deepcopy(value) for key, value in keep.items()})
            if dirty or len(keep) != len(table):
                recovered += 1
        _photoshop_prune_requests_locked(now_value)
    return recovered


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



logging.getLogger("uvicorn.access").addFilter(QuietAccessLogFilter())

FIREFLY_PROJECT_STORE: Optional[ProjectStore] = None

PROJECT_MEDIA_IMPORT_MAX_PER_PROJECT = 8

PROJECT_CONVERSATION_RUNNER: Optional["ProjectConversationTaskRunner"] = None

PROJECT_CONVERSATION_MAX_PENDING = 32

PROJECT_CONVERSATION_MAX_PER_PROJECT = 8

PROJECT_CONVERSATION_MAX_PER_NODE = 4

PROJECT_CONVERSATION_MAX_IMAGE_REFERENCES = 8

PROJECT_CONVERSATION_MEDIA_MAX_FILE_BYTES = 16 * 1024 * 1024

PROJECT_CONVERSATION_MEDIA_MAX_TOTAL_BYTES = 32 * 1024 * 1024

PROJECT_CONVERSATION_IMAGE_MAX_PIXELS = 24_000_000

PROJECT_GENERATED_VIDEO_MAX_BYTES = 1024 * 1024 * 1024

PROJECT_AGENT_MAX_PENDING = 32

PROJECT_AGENT_MAX_PER_NODE = 8

PROJECT_AGENT_TASK_TIMEOUT_SECONDS = 15 * 60

agent_image_adapter = None

agent_video_adapter = None

local_edit_adapter = project_local_edit_online_adapter

FLY_MAX_PACKAGE_BYTES = 20 * 1024 * 1024 * 1024

FLY_MAX_REQUEST_BYTES = FLY_MAX_PACKAGE_BYTES + 1024 * 1024

manager = ConnectionManager()

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

STORAGE_SETTINGS_FILE = os.path.join(DATA_DIR, "storage_settings.json")

DEFAULT_STORAGE_DIRS = {
    "upload": OUTPUT_INPUT_DIR,
    "generated": OUTPUT_OUTPUT_DIR,
    "local": LOCAL_UPLOAD_DIR,
}

apply_storage_settings()

ensure_runtime_config_files()

load_env_file()

FIREFLY_PROJECT_STORE = ProjectStore()

AI_BASE_URL = os.getenv("COMFLY_BASE_URL", "https://ai.comfly.chat").rstrip("/")

PROJECT_VIDEO_REFERENCE_INLINE_MAX_BYTES = int(os.getenv("PROJECT_VIDEO_REFERENCE_INLINE_MAX_BYTES", str(64 * 1024 * 1024)))

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

os.makedirs(OUTPUT_DIR, exist_ok=True)

os.makedirs(ASSETS_DIR, exist_ok=True)

os.makedirs(OUTPUT_INPUT_DIR, exist_ok=True)

os.makedirs(OUTPUT_OUTPUT_DIR, exist_ok=True)

os.makedirs(ASSET_LIBRARY_DIR, exist_ok=True)

os.makedirs(LOCAL_UPLOAD_DIR, exist_ok=True)

os.makedirs(CONVERSATION_DIR, exist_ok=True)

os.makedirs(CANVAS_DIR, exist_ok=True)

app.mount("/output", StaticFiles(directory=OUTPUT_DIR), name="output")

app.mount("/assets", StaticFiles(directory=ASSETS_DIR), name="assets")

PROJECTS_PATH = os.path.join(DATA_DIR, "projects.json")

ASSET_CLASSIFICATION_PROMPT_FILE = os.path.join(DATA_DIR, "asset_classification_prompt.txt")

agent_image_adapter = project_agent_default_image_adapter

agent_video_adapter = project_agent_default_video_adapter


# ---- router registration ----
from app.routers import root as _r_root
app.include_router(_r_root.router)
from app.routers import api_ai as _r_api_ai
app.include_router(_r_api_ai.router)
from app.routers import api_app_info as _r_api_app_info
app.include_router(_r_api_app_info.router)
from app.routers import api_asset_classification_prompt as _r_api_asset_classification_prompt
app.include_router(_r_api_asset_classification_prompt.router)
from app.routers import api_asset_library as _r_api_asset_library
app.include_router(_r_api_asset_library.router)
from app.routers import api_canvas_assets as _r_api_canvas_assets
app.include_router(_r_api_canvas_assets.router)
from app.routers import api_canvas_image_tasks as _r_api_canvas_image_tasks
app.include_router(_r_api_canvas_image_tasks.router)
from app.routers import api_canvas_llm as _r_api_canvas_llm
app.include_router(_r_api_canvas_llm.router)
from app.routers import api_canvas_video as _r_api_canvas_video
app.include_router(_r_api_canvas_video.router)
from app.routers import api_canvases as _r_api_canvases
app.include_router(_r_api_canvases.router)
from app.routers import api_chat as _r_api_chat
app.include_router(_r_api_chat.router)
from app.routers import api_excel_import as _r_api_excel_import
app.include_router(_r_api_excel_import.router)
from app.routers import api_excel_expand as _r_api_excel_expand
app.include_router(_r_api_excel_expand.router)
from app.routers import api_excel_collect as _r_api_excel_collect
app.include_router(_r_api_excel_collect.router)
from app.routers import api_cloud_video as _r_api_cloud_video
app.include_router(_r_api_cloud_video.router)
from app.routers import api_codex as _r_api_codex
app.include_router(_r_api_codex.router)
from app.routers import api_config as _r_api_config
app.include_router(_r_api_config.router)
from app.routers import api_conversations as _r_api_conversations
app.include_router(_r_api_conversations.router)
from app.routers import api_download_output as _r_api_download_output
app.include_router(_r_api_download_output.router)
from app.routers import api_gemini_cli as _r_api_gemini_cli
app.include_router(_r_api_gemini_cli.router)
from app.routers import api_global_settings as _r_api_global_settings
app.include_router(_r_api_global_settings.router)
from app.routers import api_history as _r_api_history
app.include_router(_r_api_history.router)
from app.routers import api_image_jpeg as _r_api_image_jpeg
app.include_router(_r_api_image_jpeg.router)
from app.routers import api_image_params as _r_api_image_params
app.include_router(_r_api_image_params.router)
from app.routers import api_image_task_query as _r_api_image_task_query
app.include_router(_r_api_image_task_query.router)
from app.routers import api_jimeng as _r_api_jimeng
app.include_router(_r_api_jimeng.router)
from app.routers import api_local_assets as _r_api_local_assets
app.include_router(_r_api_local_assets.router)
from app.routers import api_media_preview as _r_api_media_preview
app.include_router(_r_api_media_preview.router)
from app.routers import api_models as _r_api_models
app.include_router(_r_api_models.router)
from app.routers import api_online_image as _r_api_online_image
app.include_router(_r_api_online_image.router)
from app.routers import api_project_library as _r_api_project_library
app.include_router(_r_api_project_library.router)
from app.routers import api_projects as _r_api_projects
app.include_router(_r_api_projects.router)
from app.routers import api_prompt_libraries as _r_api_prompt_libraries
app.include_router(_r_api_prompt_libraries.router)
from app.routers import api_providers as _r_api_providers
app.include_router(_r_api_providers.router)
from app.routers import api_queue_status as _r_api_queue_status
app.include_router(_r_api_queue_status.router)
from app.routers import api_runninghub as _r_api_runninghub
app.include_router(_r_api_runninghub.router)
from app.routers import api_shared_folders as _r_api_shared_folders
app.include_router(_r_api_shared_folders.router)
from app.routers import api_smart_canvas as _r_api_smart_canvas
app.include_router(_r_api_smart_canvas.router)
from app.routers import api_storage_files as _r_api_storage_files
app.include_router(_r_api_storage_files.router)
from app.routers import api_storage_settings as _r_api_storage_settings
app.include_router(_r_api_storage_settings.router)
from app.routers import api_temp_sh as _r_api_temp_sh
app.include_router(_r_api_temp_sh.router)
from app.routers import api_view as _r_api_view
app.include_router(_r_api_view.router)
from app.routers import startup as _r_startup
app.include_router(_r_startup.router)
from app.routers import unknown as _r_unknown
app.include_router(_r_unknown.router)
from app.routers import ws_stats as _r_ws_stats
app.include_router(_r_ws_stats.router)

# ---- compatibility re-exports from app.core (auto-generated) ----
from app.core import (AGENT_ACTIONS, AGENT_EDIT_KEYWORDS, AGENT_IMAGE_KEYWORDS, AGNES_DEFAULT_VIDEO_MODELS, AIReference, AI_API_KEY, AI_REQUEST_TIMEOUT, APIMART_IMAGE_INITIAL_POLL_DELAY, APIMART_IMAGE_POLL_INTERVAL, APIMART_IMAGE_TASK_TIMEOUT, APIMART_UPLOAD_RETRY_ATTEMPTS, API_ENV_LOCK, APP_VERSION, ASSET_CLASSIFICATION_DIMENSION_NAMES, ASSET_CLASSIFICATION_PROMPT, AVATAR_LEGACY_FLAT_FIELDS, AVATAR_SUPPORTED_PLATFORMS, AVATAR_TASK_DONE_STATUSES, AVATAR_TASK_FAIL_STATUSES, ApiProviderPayload, AssetAvatarRegisterRequest, AssetLibraryAddRequest, AssetLibraryBatchAddRequest, AssetLibraryBatchCropRequest, AssetLibraryBatchDeleteRequest, AssetLibraryBatchMoveRequest, AssetLibraryCategoryRequest, AssetLibraryClassifyRequest, AssetLibraryRenameRequest, AssetLibraryRequest, BASE_DIR, Base64UploadRequest, CANVAS_COLORS, CANVAS_LOCK, CANVAS_TASKS, CANVAS_TASK_LOCK, CANVAS_TRASH_RETENTION_MS, CHAT_ATTACHMENT_MAX, CHAT_MODEL, CHAT_RATIO_SIZE_OPTIONS, CLIENT_ID, CN_NUMERAL_MAP, CODEX_DEFAULT_CHAT_MODELS, CODEX_DEFAULT_IMAGE_MODELS, CONVERSATION_LOCK, CanvasAssetCheckRequest, CanvasAssetDownloadRequest, CanvasCreateRequest, CanvasLLMRequest, CanvasMetaUpdate, CanvasMutationBatch, CanvasSaveRequest, CanvasVideoRequest, ChatRequest, CloudVideoUploadRequest, CodexHelpRequest, ConversationCreateRequest, DEFAULT_PROJECT_ID, DeleteCanvasLogRequest, DeleteHistoryRequest, EXCEL_MAX_COLS_PER_ROW, EXCEL_MAX_ROWS_PER_SHEET, EXCEL_MAX_SHEETS, FIELD_LABELS, FIREFLY_REPO_URL, FIXED_PROTOCOL_PROVIDER_IDS, FLY_IMPORT_UPLOAD_LOCK, FireflyProjectCreateRequest, GEMINI_CLI_DEFAULT_CHAT_MODELS, GEMINI_CLI_DEFAULT_IMAGE_MODELS, GEMINI_IMAGE_ASPECT_RATIOS, GLOBAL_CONFIG_LOCK, GLOBAL_LOOP, GPT_IMAGE2_MAX_EDGE, GPT_IMAGE2_MAX_PIXELS, GPT_IMAGE2_MIN_PIXELS, GeminiCliHelpRequest, GlobalPreferencesPayload, HISTORY_LOCK, IMAGE_BASE64_KEY_HINTS, IMAGE_CONTAINER_KEY_HINTS, IMAGE_MODEL, IMAGE_OUTPUT_KEY_HINTS, IMAGE_PARAM_RATIOS, IMAGE_PARAM_RESOLUTIONS, IMAGE_POLL_INTERVAL, IMAGE_TASK_FAILED_STATUSES, IMAGE_TASK_SUCCESS_STATUSES, IMAGE_TASK_TIMEOUT, ImageTaskQueryRequest, JIMENG_DEFAULT_IMAGE_MODELS, JIMENG_DEFAULT_VIDEO_MODELS, JIMENG_IMAGE2IMAGE_MODELS, JIMENG_LEGACY_IMAGE_MODELS, JIMENG_LEGACY_VIDEO_MODELS, JIMENG_LOGIN_SESSION, JIMENG_MIN_CLI_VERSION, JIMENG_RATIO_CHOICES, JIMENG_TEXT2IMAGE_MODELS, JIMENG_VIDEO_1080P_MODELS, JimengHelpRequest, JimengPendingError, JimengQueryMediaRequest, LINGJING_DEFAULT_BASE_URL, LLM_MESSAGE_MAX_LENGTH, LOAD_LOCK, LOCAL_IMAGE_IMPORT_EXTS, LOCAL_IMAGE_IMPORT_MAX_BYTES, LOCKED_RECOMMENDED_PROVIDER_RULES, LimitedFlyMultipartParser, LocalAssetCaptionRequest, LocalAssetCaptionSaveRequest, LocalAssetClassifyRequest, LocalAssetFolderRequest, LocalAssetRenameRequest, LocalAssetUrlImportItem, LocalAssetUrlImportRequest, LocalImageImportRequest, MAX_ATTACHMENT_TEXT_CHARS, MAX_HISTORY_MESSAGES, MODELSCOPE_API_KEY, MODELSCOPE_CHAT_BASE_URL, MODELSCOPE_CHAT_MODELS, MODELSCOPE_DEFAULTS_VERSION, MODELSCOPE_DEFAULT_CHAT_MODEL, MODELSCOPE_DEFAULT_CHAT_MODELS, MODELSCOPE_DEFAULT_IMAGE_MODEL, MODELSCOPE_DEFAULT_IMAGE_MODELS, MODELSCOPE_DEFAULT_LORAS, NEXT_TASK_ID, ONLINE_IMAGE_PROMPT_MAX_LENGTH, ONLINE_IMAGE_REFERENCE_MAX, ONLINE_VIDEO_REFERENCE_MAX, OnlineImageRequest, PER_MODEL_PROTOCOL_OPTIONS, PHOTOSHOP_ROUND_TRIP_LOCK, PHOTOSHOP_ROUND_TRIP_MAX_MEDIA_BYTES, PHOTOSHOP_ROUND_TRIP_MAX_REQUESTS, PHOTOSHOP_ROUND_TRIP_REQUESTS, PHOTOSHOP_ROUND_TRIP_RETENTION_MS, PROJECT_AGENT_ADMISSION_LOCK, PROJECT_AGENT_EXECUTION_LOCKS, PROJECT_AGENT_EXECUTION_LOCKS_GUARD, PROJECT_AGENT_FUTURES, PROJECT_AGENT_MAX_PER_PROJECT, PROJECT_AGENT_MAX_TASKS_PER_PLAN, PROJECT_AGENT_RECOVERY_LOCK, PROJECT_AGENT_RUNNING_IDS, PROJECT_AGENT_TASKS, PROJECT_AGENT_TASKS_LOCK, PROJECT_CONVERSATION_ADMISSION_LOCK, PROJECT_CONVERSATION_MAX_AUDIO_REFERENCES, PROJECT_CONVERSATION_MAX_VIDEO_REFERENCES, PROJECT_CONVERSATION_READY_RESULTS, PROJECT_CONVERSATION_TASKS, PROJECT_CONVERSATION_TASKS_LOCK, PROJECT_IMAGE_GENERATION_EXECUTOR, PROJECT_IMAGE_GENERATION_FUTURES, PROJECT_IMAGE_GENERATION_MAX_PENDING, PROJECT_IMAGE_GENERATION_MAX_PER_PROJECT, PROJECT_IMAGE_GENERATION_ORPHANS, PROJECT_LIBRARY_MIGRATION_ACTIVE, PROJECT_LIBRARY_SWITCH_LOCK, PROJECT_LIFECYCLE_LOCKS, PROJECT_LIFECYCLE_LOCKS_GUARD, PROJECT_MEDIA_ADAPTER_PROJECT, PROJECT_MEDIA_ADAPTER_SCHEME, PROJECT_MEDIA_IMPORT_ACCEPTING, PROJECT_MEDIA_IMPORT_EXECUTOR, PROJECT_MEDIA_IMPORT_FUTURES, PROJECT_MEDIA_IMPORT_MAX_PENDING, PROJECT_MEDIA_IMPORT_SHUTTING_DOWN, PROJECT_MEDIA_IMPORT_TASKS, PROJECT_MEDIA_IMPORT_TASKS_LOCK, PROJECT_VIDEO_CANDIDATE_INCOMPATIBLE_KEY, PROJECT_VIDEO_GENERATION_FUTURES, PROJECT_VIDEO_GENERATION_ORPHANS, PROMPT_BUILTIN_CATEGORY_IDS, PROMPT_TEMPLATE_EN, PROMPT_TEMPLATE_PATHS, PROVIDER_ID_RE, PUBLIC_BASE_URL, PUBLIC_MEDIA_BASE_URL, PhotoshopRoundTripCreateRequest, PhotoshopRoundTripReturnRequest, ProjectAgentExecuteRequest, ProjectAgentPlanRequest, ProjectConversationMessageRequest, ProjectCreateRequest, ProjectHistoryDeleteRequest, ProjectImageGenerationRequest, ProjectLibraryChangeRequest, ProjectLocalEditRequest, ProjectMediaImportActive, ProjectMediaImportRequest, ProjectOriginalDeleteRequest, ProjectUpdateRequest, ProjectVideoGenerationRequest, PromptLibraryBatchDeleteRequest, PromptLibraryCategoryRequest, PromptLibraryItemRequest, PromptLibraryRequest, QUEUE, QUEUE_LOCK, QUIET_ACCESS_PATHS, QUIET_ACCESS_PREFIXES, QuietAccessLogFilter, REMOVED_PROVIDER_IDS, RESPONSES_POLL_INTERVAL, RESPONSES_POLL_MAX_SECONDS, RESPONSES_REJECT_STATUSES, RUNNINGHUB_DEFAULT_APPS, RUNNINGHUB_DEFAULT_BASE_URL, RUNNINGHUB_DEFAULT_IMAGE_MODELS, RUNNINGHUB_DEFAULT_VIDEO_MODELS, RUNNINGHUB_ENTRY_MODEL_RE, RUNNINGHUB_FALLBACK_CHAT_MODELS, RUNNINGHUB_FILE_HOST_REWRITES, RUNNINGHUB_LLM_BASE_URL, RUNNINGHUB_LLM_MODELS_URLS, RUNNINGHUB_MODEL_ENDPOINT_ALIASES, RUNNINGHUB_OPENAPI_BASE_URL, RUNNINGHUB_THUMBNAIL_EXTS, RUNTIME_ROOT, RunningHubSubmitRequest, RunningHubUploadAssetRequest, SEED_UINT32_MAX, SHARED_FOLDERS_LOCK, SHARED_MEDIA_EXTS, SHARED_SCAN_MAX_ENTRIES, STATIC_DIR, STATIC_PROMPT_TEMPLATE_MD, STATIC_RUNNINGHUB_API_PROVIDERS_FILE, STATIC_RUNNINGHUB_DIR, STATIC_RUNNINGHUB_MODEL_REGISTRY_FILE, STATIC_RUNNINGHUB_THUMBNAIL_DIR, STORAGE_IMAGE_EXTS, SUPPORTED_IMAGE_REQUEST_MODES, SUPPORTED_PROVIDER_PROTOCOLS, SYSTEM_PROMPT, SharedFolderImport, SharedFolderRegister, SmartCanvasGroupExportItem, SmartCanvasGroupExportRequest, TEXT_ATTACHMENT_EXTS, TempShUploadRequest, TestConnectionPayload, UPDATE_LOCK, VIDEO_POLL_TIMEOUT, VIDEO_PROMPT_MAX_LENGTH, VIDEO_TASK_FAILURE_STATUSES, VIDEO_TASK_SUCCESS_STATUSES, VIDEO_URL_KEYS, VOLCENGINE_ARK_ASSET_HOST, VOLCENGINE_ARK_ASSET_REGION, VOLCENGINE_ARK_ASSET_SERVICE, VOLCENGINE_ARK_ASSET_VERSION, VOLCENGINE_DEFAULT_BASE_URL, VOLCENGINE_DEFAULT_PROJECT_NAME, VOLCENGINE_DEFAULT_REGION, VOLCENGINE_MAX_EDGE, VOLCENGINE_MIN_EDGE, VOLCENGINE_MIN_PIXELS, VOLCENGINE_RATIO_CHOICES, XLSX_IMAGE_EXTS, _AGENT_FORBIDDEN_VALUE_TOKENS, _AGENT_IMAGE_PARAM_SCHEMA, _AGENT_PROMPT_PARAM_SCHEMA, _AGENT_TASK_INPUT_KEYS, _AGENT_VIDEO_PARAM_SCHEMA, _DOUBLE_EXT_MEDIA, _DOUBLE_EXT_RE, _MODELSCOPE_CONFIGURED_CHAT_MODELS, _agent_clean_params, _agent_execution_tasks_from_plan, _agent_find_task_node, _agent_forbidden_value, _agent_history_output, _agent_int_param, _agent_memory_task_active, _agent_nodes_by_id, _agent_normalize_candidates, _agent_normalize_task, _agent_plan_from_node, _agent_plan_hash, _agent_plan_response, _agent_public_task, _agent_recovery_payload, _agent_task_canonical, _agent_task_layout, _agent_task_specs, _app_main, _collect_video_url, _conversation_pending_task_keys_from_canvas, _ensure_photoshop_loopback, _local_upload_display_name, _local_upload_folder_node, _local_upload_kind_ext, _local_upload_rel_path, _local_upload_safe_file_stem, _local_upload_safe_folder_name, _offline_local_edit_adapter, _photoshop_protocol_error, _photoshop_public_request, _photoshop_reset_processing, _photoshop_validate_display_name, _photoshop_validate_media_type, _responses_wrap, _rh_field_cmp, _rh_natural_cmp, _safe_asset_tag, _sniff_image_ext, _sniff_image_ext_bytes, _storage_abs_path, _volc_hmac, _xlsx_cell_text, _xlsx_join_text, _xlsx_shared_strings, _xlsx_sheet_paths, _xml_local_name, _yuli_model_norm, acquire_fly_import_slot, agnes_video_dimensions, agnes_video_frame_count, antigravity_cli_winget_candidates, apimart_avatar_asset_type, apimart_size_resolution, apimart_upload_file_payload, apimart_upload_payload_from_bytes, apimart_upload_post, apimart_upload_raw_file_payload, apimart_veo31_aspect, apimart_veo31_duration, apimart_veo31_model, apimart_veo31_resolution, apimart_video_duration, apimart_video_size, apply_agnes_model_defaults, apply_locked_recommended_model_rules, apply_runninghub_system_thumbnails, apply_trusted_asset_prompt_index, asset_library_media_kind, asset_library_safe_extension, avatar_platform_for_provider, banana_image_request_params, banana_model_family, bearer_auth_value, build_image_param_fields, builtin_prompt_templates, canvas_asset_downloadable_url, canvas_asset_kind, canvas_asset_name, canvas_asset_url_value, canvas_has_pending_conversation, canvas_node_title, canvas_record, canvas_video_camera_fixed, canvas_video_project_media_values, chat_prompt_size_override, chat_provider_supports_audio, chat_requested_image_count, chat_split_parallel_prompts, chat_system_prompt, classify_upstream_model, codex_chat_prompt, codex_decode_output, codex_model_for_exec, codex_models_payload, codex_postprocess_image_to_requested_size, codex_timeout, collect_local_media_urls, compress_data_url_image, content_type_for_path, conversation_error_message, conversation_node_by_id, conversation_task_response, conversation_task_snapshot, current_app_version, decode_utf16_auto, decode_wsl_output, defaultPromptTemplateCategories, default_api_providers, detect_image_request_mode, display_title, effective_image_request_mode, effective_protocol, ensure_project_image_generation_executor, ensure_project_media_import_executor, ensure_public_generation_target, ensure_same_origin_request, env_quote, extract_apimart_asset_url, extract_apimart_avatar_asset_uri, extract_canvas_assets, extract_image, extract_image_flexible, extract_images, extract_prompt_template_section, extract_task_id, extract_task_id_from_text, fetch_remote_media_bytes, filename_from_media_url, find_asset_category, find_asset_item_in_library, find_prompt_library, forget_project_conversation_ready_result, forget_project_conversation_task, friendly_chat_error_detail, friendly_image_error_detail, friendly_validation_error, gemini_cli_chat_prompt, gemini_cli_image_size_instruction, gemini_cli_image_timeout, gemini_cli_model, gemini_cli_models_payload, gemini_cli_parse_stdout, gemini_cli_reference_note, gemini_cli_text_from_raw, gemini_cli_timeout, gemini_image_config, gemini_model_name, gemini_supported_aspect_ratio, generated_image_filename, gpt_image_2_size_error_message, gpt_image_2_size_exceeds_supported, gpt_image_2_skill_access_token, gpt_image_2_skill_auth_json, gpt_image_2_skill_failure_message, gpt_image_2_skill_model_arg, gpt_image_2_skill_prompt_arg, gpt_image_2_skill_size_arg, heuristic_agent_decision, httpx_request_with_transient_retries, humanize_video_task_failure, image_has_alpha, image_path_to_data_url, image_payload_from_string, image_provider_model_ids, image_references, image_task_data, image_task_fail_reason, image_task_status, images_api_unsupported, invalid_video_image_preview, is_agnes_provider, is_antigravity_cli, is_apimart_provider, is_apimart_veo31_model, is_codex_provider, is_fhl_provider, is_gemini_cli_provider, is_gemini_provider, is_gpt_image_2_model, is_image_reference, is_jimeng_provider, is_lingjing_provider, is_private_asset_url, is_runninghub_provider, is_transient_tls_error, is_video_preview_file, is_volcengine_provider, is_volcengine_seedance2_model, is_volcengine_seedream_model, is_yuli_provider, iter_canvas_asset_values, jimeng_append_model_resolution_args, jimeng_clean_wsl_stderr, jimeng_collect_media_values, jimeng_extract_json, jimeng_failure_reason, jimeng_image_model_version, jimeng_image_resolution, jimeng_login_qr_from_text, jimeng_login_text, jimeng_normalize_image_model, jimeng_output_values, jimeng_parse_version, jimeng_pending_payload, jimeng_poll_seconds, jimeng_queue_info, jimeng_ratio_from_size, jimeng_submit_id, jimeng_transition_duration, jimeng_video_duration, jimeng_video_duration_range, jimeng_video_model_version, jimeng_video_ratio_arg, jimeng_video_ref_role, jimeng_video_ref_url, jimeng_video_resolution, jimeng_video_resolution_arg, latest_chat_image_refs, lingjing_openai_video_model, load_static_runninghub_provider, locked_recommended_provider_rule, log_net_error, looks_like_gemini_image_model, looks_like_generated_image_url, looks_like_html_response, looks_like_image_base64, looks_like_image_media_url, looks_like_vision_chat_model, mask_secret, merge_default_api_providers, merge_runninghub_entry_overlay, merge_runninghub_provider_with_static, merge_runninghub_system_entries, migrate_asset_item_registrations, model_list, model_list_from_values, modelscope_image_api_root, modelscope_size, normalize_available_models, normalize_canvas_color, normalize_canvas_kind, normalize_endpoint_override, normalize_gpt_image_2_size, normalize_image_request_mode, normalize_local_image_path, normalize_model_list, normalize_model_name_map, normalize_model_protocols, normalize_ms_loras, normalize_prompt_category_id, normalize_prompt_template_categories, normalize_provider, normalize_recommended_models, normalize_runninghub_entries, normalize_runninghub_entry, normalize_seed_uint32, normalize_volcengine_size, openai_compat_root_for_probe, origin_from_url, parse_agent_decision, parse_error_payload_text, parse_gpt_image_2_skill_output, parse_prompt_template_markdown, parse_size_pair, parse_upstream_models, post_openai_responses, post_openai_responses_stream, preferred_chat_model, preserve_runninghub_hidden_overrides, probe_openai_compat_bearer_endpoint, probe_openai_models_endpoint, probe_volcengine_auto_detect, probe_volcengine_task_endpoint, project_agent_execution_lock, project_agent_node, project_canvas_video_payload, project_conversation_ready_result, project_has_active_image_generation, project_has_active_media_import, project_has_active_video_generation, project_has_recoverable_conversation_sidecars, project_lifecycle_lock, project_media_adapter_project_id, project_media_adapter_reference, project_media_adapter_reference_from_url, project_record, project_video_candidate_preflight_incompatible_parameters, project_video_mark_candidate_incompatible, prompt_template_category, prompt_template_markdown_path, protocol_from_payload, provider_key_env, provider_protocol, provider_supports_avatar, public_media_url_suffix, public_project_media_import_task, read_text_attachment, read_xlsx_attachment, recommend_upstream_models, reload_env_globals, remember_project_conversation_ready_result, require_canvas_video_project_media_scope, require_online_image_project_media_scope, require_project_media_adapter_scope, require_project_media_values_scope, reserve_project_image_task, reserve_project_video_task, responses_image_size_instruction, responses_no_image_detail, responses_output_text_image, responses_proxy_tool_size, rewrite_runninghub_file_url, rh_default_value, rh_field_indexes, rh_field_kind, rh_field_role, rh_is_seed_like_name, rh_random_field_value, rh_sort_fields, route_openai_image_request, runninghub_apply_schema_defaults, runninghub_aspect_from_size, runninghub_configured_video_model_definition, runninghub_endpoint_alias_for_model, runninghub_endpoint_url, runninghub_entry_config_from_model, runninghub_entry_id, runninghub_extract_image, runninghub_extract_outputs, runninghub_extract_task_id, runninghub_fail_reason, runninghub_is_image_to_video, runninghub_model_display_name, runninghub_model_id, runninghub_openapi_base_url, runninghub_openapi_url, runninghub_output_ext, runninghub_query_status, runninghub_registry_fallback, runninghub_registry_items_from_raw, runninghub_registry_model_from_id, runninghub_registry_payload, runninghub_resolution_from_size, runninghub_schema_field, runninghub_schema_field_required, runninghub_schema_options, runninghub_schema_value, runninghub_size_for_aspect, runninghub_task_endpoint, runninghub_video_incompatible_parameters, runninghub_wallet_key_env, safe_user_id, sanitize_asset_name, sanitize_export_filename, sanitize_runninghub_node_info_list, sanitize_seed_like_values, scan_shared_tree, seed_system_prompt_library, selected_model, shared_child_abs, shared_folder_abs, shared_resolve_register, snap_size_to_multiple, sort_asset_library_items, sse_event, static_html_response, static_runninghub_thumbnail_url, storage_file_item, strip_auth_scheme, text_delta_from_chat_chunk, text_from_chat_response, unwrap_apimart_response, upload_video_to_litterbox, upload_video_to_temp_sh, upstream_model_headers, upstream_models_url, valid_apimart_video_image_input, valid_video_image_input, validate_generated_image, validate_generated_video, validate_project_media_source, video_output_urls, video_task_url_candidates, volcengine_access_key_env, volcengine_content_role, volcengine_default_model_payload, volcengine_secret_key_env, volcengine_sign_v4_headers, volcengine_task_probe_url, volcengine_video_duration, volcengine_video_prompt_text, volcengine_video_resolution, windows_path_to_wsl, wsl_path_to_windows, xlsx_embedded_image_data_urls, yuli_is_veo_openai_model, yuli_openai_model_name, yuli_openai_size, yuli_video_seconds)
# ---- compatibility re-exports from routers (auto-generated) ----
from app.routers.api_ai import Base64UploadRequest, LocalImageImportRequest, import_local_ai_reference, import_local_image_file, normalize_local_image_path, upload_ai_base64, upload_ai_reference  # noqa: F401
from app.routers.api_app_info import app_info, current_app_version  # noqa: F401
from app.routers.api_asset_classification_prompt import get_asset_classification_prompt, save_asset_classification_prompt, update_asset_classification_prompt  # noqa: F401
from app.routers.api_asset_library import AssetAvatarRegisterRequest, AssetLibraryAddRequest, AssetLibraryBatchAddRequest, AssetLibraryBatchCropRequest, AssetLibraryBatchDeleteRequest, AssetLibraryBatchMoveRequest, AssetLibraryCategoryRequest, AssetLibraryClassifyRequest, AssetLibraryRenameRequest, AssetLibraryRequest, _volc_hmac, add_asset_library_item, apimart_avatar_asset_type, avatar_platform_for_provider, batch_add_asset_library_items, batch_crop_asset_library_items, batch_delete_asset_library_items, batch_move_asset_library_items, check_apimart_avatar_task, check_asset_library_avatar, check_volcengine_avatar_task, classify_asset_library_items, create_asset_library, create_asset_library_category, delete_asset_library, delete_asset_library_category, delete_asset_library_item, extract_apimart_avatar_asset_uri, find_asset_category_with_library, find_asset_item_in_library, get_asset_library, register_asset_library_avatar, remove_asset_library_file, rename_asset_library, rename_asset_library_category, rename_asset_library_item, submit_apimart_avatar_asset, submit_volcengine_avatar_asset, upload_media_for_apimart, volcengine_ark_asset_call, volcengine_ensure_asset_group, volcengine_public_asset_url, volcengine_sign_v4_headers  # noqa: F401
from app.routers.api_canvas_assets import CanvasAssetCheckRequest, CanvasAssetDownloadRequest, canvas_asset_downloadable_url, canvas_asset_kind, canvas_asset_name, canvas_asset_url_value, canvas_assets_index, canvas_node_title, check_canvas_assets, download_canvas_assets, extract_canvas_assets, fetch_remote_media_bytes, iter_canvas_asset_values, list_canvas_assets  # noqa: F401
from app.routers.api_canvas_image_tasks import create_canvas_image_task, extract_task_id_from_text, get_canvas_image_task, run_canvas_image_task  # noqa: F401
from app.routers.api_canvas_llm import canvas_llm  # noqa: F401
from app.routers.api_canvas_video import canvas_video  # noqa: F401
from app.routers.api_canvases import CanvasCreateRequest, CanvasMetaUpdate, CanvasSaveRequest, DeleteCanvasLogRequest, canvas_path, canvases, collect_local_media_urls, create_canvas, delete_canvas, delete_canvas_log, delete_media_preview_cache, expand_canvas_generated_media_paths, generated_media_path_from_url, get_canvas, get_canvas_meta, iter_canvas_records, json_references_media_path, list_canvases, list_deleted_canvases, load_canvas, load_canvas_any, local_media_path_from_url, persisted_json_references_media_path, prune_generation_history_for_media, purge_canvas, reset_canvas_result_nodes_for_media, restore_canvas, save_canvas, smart_owned_result_items, touch_canvas, trashed_canvases, update_canvas, update_canvas_meta  # noqa: F401
from app.routers.api_chat import ChatRequest, _xlsx_cell_text, _xlsx_join_text, _xlsx_shared_strings, _xlsx_sheet_paths, _xml_local_name, attachment_embedded_image_data_urls, attachment_text_blocks, build_chat_text_reply, chat, chat_agent, chat_prompt_size_override, chat_requested_image_count, chat_split_parallel_prompts, chat_stream, chat_system_prompt, decide_chat_agent_action, display_title, heuristic_agent_decision, image_size_from_reference, latest_chat_image_refs, parse_agent_decision, pick_chat_image_provider, read_text_attachment, read_xlsx_attachment, sse_event, text_delta_from_chat_chunk, upstream_message_from_record, xlsx_embedded_image_data_urls  # noqa: F401
from app.routers.api_cloud_video import CloudVideoUploadRequest, cloud_video_upload  # noqa: F401
from app.routers.api_codex import CodexHelpRequest, codex_help, codex_status  # noqa: F401
from app.routers.api_config import ai_config, get_global_token  # noqa: F401
from app.routers.api_conversations import ConversationCreateRequest, conversations, create_conversation, delete_conversation, get_conversation, list_conversations  # noqa: F401
from app.routers.api_download_output import download_output  # noqa: F401
from app.routers.api_gemini_cli import GeminiCliHelpRequest, gemini_cli_help, gemini_cli_status  # noqa: F401
from app.routers.api_global_settings import GlobalPreferencesPayload, ProjectLibraryChangeRequest, get_global_preferences, global_settings, load_global_preferences, photoshop_connection_status, project_library_status, save_global_preferences, switch_project_library, update_global_preferences, update_project_library  # noqa: F401
from app.routers.api_history import DeleteHistoryRequest, delete_history, get_history_api  # noqa: F401
from app.routers.api_image_jpeg import image_jpeg  # noqa: F401
from app.routers.api_image_params import build_image_param_fields, image_params  # noqa: F401
from app.routers.api_image_task_query import ImageTaskQueryRequest, query_image_task  # noqa: F401
from app.routers.api_jimeng import JimengHelpRequest, JimengQueryMediaRequest, jimeng_credit, jimeng_help, jimeng_login_qr_from_text, jimeng_login_reader, jimeng_login_start, jimeng_login_status, jimeng_login_text, jimeng_logout, jimeng_query_media, jimeng_status  # noqa: F401
from app.routers.api_local_assets import LocalAssetCaptionRequest, LocalAssetCaptionSaveRequest, LocalAssetClassifyRequest, LocalAssetFolderRequest, LocalAssetRenameRequest, LocalAssetUrlImportItem, LocalAssetUrlImportRequest, _local_upload_abs, _local_upload_folder_node, _local_upload_safe_file_stem, _local_upload_safe_folder, _local_upload_safe_folder_name, _local_upload_safe_path, _local_upload_tree_and_items, _write_local_upload_classification, caption_local_assets, classify_local_assets, create_local_asset_folder, delete_local_assets, import_local_assets_from_urls, list_local_assets, move_local_assets, rename_local_asset_folder, rename_local_asset_item, save_local_asset_caption, upload_local_assets  # noqa: F401
from app.routers.api_media_preview import image_has_alpha, is_video_preview_file, media_preview, media_preview_cache_paths  # noqa: F401
from app.routers.api_models import ai_models  # noqa: F401
from app.routers.api_online_image import online_image  # noqa: F401
from app.routers.api_project_library import CanvasMutationBatch, FireflyProjectCreateRequest, LimitedFlyMultipartParser, PhotoshopRoundTripCreateRequest, PhotoshopRoundTripReturnRequest, ProjectAgentExecuteRequest, ProjectAgentPlanRequest, ProjectConversationMessageRequest, ProjectHistoryDeleteRequest, ProjectImageGenerationRequest, ProjectLocalEditRequest, ProjectMediaImportActive, ProjectMediaImportRequest, ProjectOriginalDeleteRequest, ProjectVideoGenerationRequest, _agent_active_counts, _agent_admit_execution_sync, _agent_clean_params, _agent_execution_tasks_from_plan, _agent_forbidden_value, _agent_memory_task_active, _agent_normalize_task, _agent_plan_hash, _agent_plan_response, _agent_public_task, _agent_result_node, _agent_task_canonical, _agent_task_layout, _agent_task_node, _agent_task_specs, _photoshop_mark_returned, _photoshop_prune_requests_locked, _photoshop_public_request, _photoshop_request_snapshot, _photoshop_reset_processing, _photoshop_validate_display_name, _photoshop_validate_media_type, acquire_fly_import_slot, append_project_conversation_message, build_project_media_preview, canvas_has_pending_conversation, cleanup_project_generation_orphans, conversation_task_response, copy_project_media_to_global, copy_project_media_to_global_library, create_firefly_project, create_photoshop_round_trip, create_project_agent_plan, create_project_image_task, create_project_local_edit, create_project_video_task, delete_project_generated_original, delete_project_history, execute_project_agent_plan, execute_project_video_generation, export_firefly_project, export_project_package, finalize_visible_project_conversation_terminal, firefly_project_save_status, flush_firefly_project, generate_project_video, get_photoshop_round_trip, get_photoshop_round_trip_source, get_project_agent_plan, get_project_agent_task, get_project_conversation_message_task, get_project_library, get_project_media, get_project_media_preview, import_firefly_project, import_project_media, list_project_media, list_project_media_trash, list_project_trash, mutate_firefly_canvas, open_firefly_project, persist_project_conversation_pending, preflight_project_video_providers, project_agent_connected_inputs, project_agent_execution_lock, project_canvas_video_payload, project_conversation_connected_references, project_has_active_conversation, project_has_active_image_generation, project_has_active_media_import, project_has_active_video_generation, project_has_recoverable_conversation_sidecars, project_image_references, project_image_request, project_lifecycle_store_call, project_media_import_status, project_media_source_path, project_video_adapter_incompatible_parameters, project_video_all_references, project_video_candidate_execution_incompatible_parameters, project_video_candidate_inline_reference_kinds, project_video_candidate_preflight_incompatible_parameters, project_video_mark_candidate_incompatible, project_video_references, project_video_request, public_project_media_import_task, purge_project_media_trash, purge_trashed_project, reserve_project_image_task, reserve_project_media_import, reserve_project_video_task, restore_project_generated_original, restore_trashed_project, return_photoshop_round_trip, run_project_image_task, run_project_video_task, trash_firefly_project, validate_project_media_source  # noqa: F401
from app.routers.api_projects import ProjectCreateRequest, ProjectUpdateRequest, create_project, delete_project, get_projects, update_project  # noqa: F401
from app.routers.api_prompt_libraries import PromptLibraryBatchDeleteRequest, PromptLibraryCategoryRequest, PromptLibraryItemRequest, PromptLibraryRequest, add_prompt_library_category, add_prompt_library_item, batch_delete_prompt_library_items, create_prompt_library, defaultPromptTemplateCategories, default_prompt_libraries, delete_prompt_library, delete_prompt_library_category, delete_prompt_library_item, find_prompt_library, get_prompt_libraries, load_prompt_libraries, normalize_prompt_category_id, normalize_prompt_libraries, normalize_prompt_library_item, normalize_prompt_template_categories, public_prompt_libraries, rename_prompt_library, rename_prompt_library_category, save_prompt_libraries, seed_system_prompt_library, update_prompt_library_item  # noqa: F401
from app.routers.api_providers import ApiProviderPayload, TestConnectionPayload, api_key_from_payload, api_providers, apply_agnes_model_defaults, apply_locked_recommended_model_rules, apply_runninghub_system_thumbnails, codex_models_payload, fetch_models_from_upstream, fetch_upstream_models, fetch_upstream_models_from_payload, gemini_cli_models_payload, load_static_runninghub_provider, model_list, openai_compat_root_for_probe, preserve_runninghub_hidden_overrides, probe_async_endpoint, probe_openai_compat_bearer_endpoint, probe_openai_models_endpoint, probe_volcengine_auto_detect, probe_volcengine_task_endpoint, protocol_from_payload, recommend_upstream_models, reload_env_globals, runninghub_model_display_name, runninghub_models_payload, runninghub_registry_payload, save_api_providers, save_providers, static_runninghub_thumbnail_url, test_provider_connection, upstream_model_headers, upstream_models_url, volcengine_default_model_payload, volcengine_provider_api_key, volcengine_task_probe_url  # noqa: F401
from app.routers.api_queue_status import get_queue_status  # noqa: F401
from app.routers.api_runninghub import RunningHubSubmitRequest, RunningHubUploadAssetRequest, normalize_seed_uint32, runninghub_app_info, runninghub_query, runninghub_submit, runninghub_upload_asset, sanitize_runninghub_node_info_list  # noqa: F401
from app.routers.api_shared_folders import SharedFolderImport, SharedFolderRegister, get_shared_folder_file, get_shared_folder_tree, import_shared_folder_files, list_shared_folders, register_shared_folder, scan_shared_tree, shared_child_abs, shared_folder_abs, shared_folder_by_id, shared_folders_load, shared_folders_save, shared_resolve_register, unregister_shared_folder  # noqa: F401
from app.routers.api_smart_canvas import SmartCanvasGroupExportItem, SmartCanvasGroupExportRequest, export_smart_canvas_group, smart_canvas_prompt_templates, smart_group_export_folder  # noqa: F401
from app.routers.api_storage_files import delete_storage_files, get_storage_file, list_storage_files, storage_file_item  # noqa: F401
from app.routers.api_storage_settings import _storage_abs_path, apply_storage_settings, get_storage_settings, load_storage_settings, save_storage_settings, update_storage_settings  # noqa: F401
from app.routers.api_temp_sh import TempShUploadRequest, temp_sh_upload  # noqa: F401
from app.routers.api_view import view_image  # noqa: F401
from app.routers.root import index  # noqa: F401
from app.routers.startup import ProjectConversationTaskRunner, _sniff_image_ext, load_project_conversation_durable, migrate_asset_library_into_dirs, migrate_double_extension_uploads, migrate_mislabeled_image_extensions, persist_project_conversation_uncertain, project_conversation_ready_result, recover_project_agent_tasks, remember_project_conversation_ready_result, run_project_conversation_task, save_project_conversation_durable  # noqa: F401
from app.routers.unknown import friendly_validation_error  # noqa: F401
from app.routers.ws_stats import websocket_endpoint  # noqa: F401


# ---- services binding (W1c) ----
_svc._bind_main(sys.modules[__name__])

if __name__ == "__main__":
    import uvicorn
    # 关闭服务端协议级 WebSocket ping：部分客户端（如 PS UXP 面板）不会自动回 pong，
    # 默认 20s ping/20s 超时会把这些连接每隔一会儿就踢掉造成"频繁断连"。
    # 客户端有自己的应用层心跳 + 断线重连兜底，这里禁用协议 ping 更稳。
    uvicorn.run(app, host="127.0.0.1", port=3000,
                ws_ping_interval=None, ws_ping_timeout=None)