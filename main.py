"""Firefly Canvas server - app assembly (auto-split from monolith)."""
from app.core import *  # noqa: F401,F403

import os as _os_boot
import sys as _sys_boot
_os_boot.environ.setdefault("FIREFLY_MAIN_MODULE", __name__)
import app.services as _svc  # W1c services layer
from app.services import *
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

@app.exception_handler(RequestValidationError)
async def request_validation_exception_handler(request: Request, exc: RequestValidationError):
    return JSONResponse(
        status_code=422,
        content={"detail": friendly_validation_error(exc.errors()), "errors": exc.errors()},
    )

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

def update_env_values(updates):
    with API_ENV_LOCK:
        return _update_env_values_unlocked(updates)

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

@app.exception_handler(JimengPendingError)
async def jimeng_pending_exception_handler(request: Request, exc: JimengPendingError):
    # 轮询超时但任务还在云端排队：返回 202 + submit_id，让前端保持「排队中」卡片并续查
    return JSONResponse(status_code=202, content=jimeng_pending_payload(exc))

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

def image_task_url_for_provider(provider, task_id):
    base_url = (provider.get("base_url") if provider else AI_BASE_URL).rstrip("/")
    # 异步生图（openai-video-proxy）模式优先于 apimart 协议判断：
    # 提交走 /v1/videos，轮询必须走 /v1/videos/{id}；否则 protocol=apimart 的平台会错走 /v1/tasks/{id}
    if normalize_image_request_mode((provider or {}).get("image_request_mode")) == "openai-video-proxy":
        return f"{base_url}/videos/{task_id}" if base_url.endswith("/v1") else f"{base_url}/v1/videos/{task_id}"
    if is_apimart_provider(provider):
        return f"{base_url}/tasks/{task_id}" if base_url.endswith("/v1") else f"{base_url}/v1/tasks/{task_id}"
    return f"{base_url}/images/tasks/{task_id}" if base_url.endswith("/v1") else f"{base_url}/v1/images/tasks/{task_id}"

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

def media_preview_cache_paths(path: str, width: int):
    stat = os.stat(path)
    key = hashlib.sha1(
        f"{os.path.realpath(path)}|{stat.st_mtime_ns}|{stat.st_size}|{width}".encode("utf-8", "ignore")
    ).hexdigest()
    return (
        os.path.join(MEDIA_PREVIEW_DIR, f"{key}.webp"),
        os.path.join(MEDIA_PREVIEW_DIR, f"{key}.png"),
    )

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

def remove_asset_library_file(item) -> None:
    """删除资产对应的本地文件（仅限 library 副本，删了不影响 /output 原图）。日志不影响主流程。"""
    try:
        url = item.get("url") if isinstance(item, dict) else ""
        path = output_file_from_url(url)
        if path and os.path.isfile(path):
            os.remove(path)
    except Exception as exc:
        print(f"删除资产文件失败: {exc}")

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

def shared_folders_save(data):
    os.makedirs(DATA_DIR, exist_ok=True)
    with open(SHARED_FOLDERS_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

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

def gemini_endpoint_url(provider, model):
    model_name = urllib.parse.quote(gemini_model_name(model), safe="")
    return provider_endpoint_url(provider, "image_generation_endpoint", f"/v1beta/models/{model_name}:generateContent")

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

def video_api_root(provider):
    base_url = (provider.get("base_url") or AI_BASE_URL).rstrip("/")
    if is_volcengine_provider(provider):
        if base_url.endswith("/api/v3"):
            base_url = base_url[: -len("/api/v3")]
        return base_url
    if base_url.endswith("/v1") or base_url.endswith("/v2"):
        base_url = base_url.rsplit("/", 1)[0]
    return base_url

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
from app.core import (AGENT_ACTIONS, AGENT_EDIT_KEYWORDS, AGENT_IMAGE_KEYWORDS, AGNES_DEFAULT_VIDEO_MODELS, AIReference, AI_API_KEY, AI_REQUEST_TIMEOUT, APIMART_IMAGE_INITIAL_POLL_DELAY, APIMART_IMAGE_POLL_INTERVAL, APIMART_IMAGE_TASK_TIMEOUT, APIMART_UPLOAD_RETRY_ATTEMPTS, API_ENV_LOCK, APP_VERSION, ASSET_CLASSIFICATION_DIMENSION_NAMES, ASSET_CLASSIFICATION_PROMPT, AVATAR_LEGACY_FLAT_FIELDS, AVATAR_SUPPORTED_PLATFORMS, AVATAR_TASK_DONE_STATUSES, AVATAR_TASK_FAIL_STATUSES, ApiProviderPayload, AssetAvatarRegisterRequest, AssetLibraryAddRequest, AssetLibraryBatchAddRequest, AssetLibraryBatchCropRequest, AssetLibraryBatchDeleteRequest, AssetLibraryBatchMoveRequest, AssetLibraryCategoryRequest, AssetLibraryClassifyRequest, AssetLibraryRenameRequest, AssetLibraryRequest, BASE_DIR, Base64UploadRequest, CANVAS_COLORS, CANVAS_LOCK, CANVAS_TASKS, CANVAS_TASK_LOCK, CANVAS_TRASH_RETENTION_MS, CHAT_ATTACHMENT_MAX, CHAT_MODEL, CHAT_RATIO_SIZE_OPTIONS, CLIENT_ID, CN_NUMERAL_MAP, CODEX_DEFAULT_CHAT_MODELS, CODEX_DEFAULT_IMAGE_MODELS, CONVERSATION_LOCK, CanvasAssetCheckRequest, CanvasAssetDownloadRequest, CanvasCreateRequest, CanvasLLMRequest, CanvasMetaUpdate, CanvasMutationBatch, CanvasSaveRequest, CanvasVideoRequest, ChatRequest, CloudVideoUploadRequest, CodexHelpRequest, ConversationCreateRequest, DEFAULT_PROJECT_ID, DeleteCanvasLogRequest, DeleteHistoryRequest, EXCEL_MAX_COLS_PER_ROW, EXCEL_MAX_ROWS_PER_SHEET, EXCEL_MAX_SHEETS, FIELD_LABELS, FIREFLY_REPO_URL, FIXED_PROTOCOL_PROVIDER_IDS, FLY_IMPORT_UPLOAD_LOCK, FireflyProjectCreateRequest, GEMINI_CLI_DEFAULT_CHAT_MODELS, GEMINI_CLI_DEFAULT_IMAGE_MODELS, GEMINI_IMAGE_ASPECT_RATIOS, GLOBAL_CONFIG_LOCK, GLOBAL_LOOP, GPT_IMAGE2_MAX_EDGE, GPT_IMAGE2_MAX_PIXELS, GPT_IMAGE2_MIN_PIXELS, GeminiCliHelpRequest, GlobalPreferencesPayload, HISTORY_LOCK, IMAGE_BASE64_KEY_HINTS, IMAGE_CONTAINER_KEY_HINTS, IMAGE_MODEL, IMAGE_OUTPUT_KEY_HINTS, IMAGE_PARAM_RATIOS, IMAGE_PARAM_RESOLUTIONS, IMAGE_POLL_INTERVAL, IMAGE_TASK_FAILED_STATUSES, IMAGE_TASK_SUCCESS_STATUSES, IMAGE_TASK_TIMEOUT, ImageTaskQueryRequest, JIMENG_DEFAULT_IMAGE_MODELS, JIMENG_DEFAULT_VIDEO_MODELS, JIMENG_IMAGE2IMAGE_MODELS, JIMENG_LEGACY_IMAGE_MODELS, JIMENG_LEGACY_VIDEO_MODELS, JIMENG_LOGIN_SESSION, JIMENG_MIN_CLI_VERSION, JIMENG_RATIO_CHOICES, JIMENG_TEXT2IMAGE_MODELS, JIMENG_VIDEO_1080P_MODELS, JimengHelpRequest, JimengPendingError, JimengQueryMediaRequest, LINGJING_DEFAULT_BASE_URL, LLM_MESSAGE_MAX_LENGTH, LOAD_LOCK, LOCAL_IMAGE_IMPORT_EXTS, LOCAL_IMAGE_IMPORT_MAX_BYTES, LOCKED_RECOMMENDED_PROVIDER_RULES, LimitedFlyMultipartParser, LocalAssetCaptionRequest, LocalAssetCaptionSaveRequest, LocalAssetClassifyRequest, LocalAssetFolderRequest, LocalAssetRenameRequest, LocalAssetUrlImportItem, LocalAssetUrlImportRequest, LocalImageImportRequest, MAX_ATTACHMENT_TEXT_CHARS, MAX_HISTORY_MESSAGES, MODELSCOPE_API_KEY, MODELSCOPE_CHAT_BASE_URL, MODELSCOPE_CHAT_MODELS, MODELSCOPE_DEFAULTS_VERSION, MODELSCOPE_DEFAULT_CHAT_MODEL, MODELSCOPE_DEFAULT_CHAT_MODELS, MODELSCOPE_DEFAULT_IMAGE_MODEL, MODELSCOPE_DEFAULT_IMAGE_MODELS, MODELSCOPE_DEFAULT_LORAS, NEXT_TASK_ID, ONLINE_IMAGE_PROMPT_MAX_LENGTH, ONLINE_IMAGE_REFERENCE_MAX, ONLINE_VIDEO_REFERENCE_MAX, OnlineImageRequest, PER_MODEL_PROTOCOL_OPTIONS, PHOTOSHOP_ROUND_TRIP_LOCK, PHOTOSHOP_ROUND_TRIP_MAX_MEDIA_BYTES, PHOTOSHOP_ROUND_TRIP_MAX_REQUESTS, PHOTOSHOP_ROUND_TRIP_REQUESTS, PHOTOSHOP_ROUND_TRIP_RETENTION_MS, PROJECT_AGENT_ADMISSION_LOCK, PROJECT_AGENT_EXECUTION_LOCKS, PROJECT_AGENT_EXECUTION_LOCKS_GUARD, PROJECT_AGENT_FUTURES, PROJECT_AGENT_MAX_PER_PROJECT, PROJECT_AGENT_MAX_TASKS_PER_PLAN, PROJECT_AGENT_RECOVERY_LOCK, PROJECT_AGENT_RUNNING_IDS, PROJECT_AGENT_TASKS, PROJECT_AGENT_TASKS_LOCK, PROJECT_CONVERSATION_ADMISSION_LOCK, PROJECT_CONVERSATION_MAX_AUDIO_REFERENCES, PROJECT_CONVERSATION_MAX_VIDEO_REFERENCES, PROJECT_CONVERSATION_READY_RESULTS, PROJECT_CONVERSATION_TASKS, PROJECT_CONVERSATION_TASKS_LOCK, PROJECT_IMAGE_GENERATION_EXECUTOR, PROJECT_IMAGE_GENERATION_FUTURES, PROJECT_IMAGE_GENERATION_MAX_PENDING, PROJECT_IMAGE_GENERATION_MAX_PER_PROJECT, PROJECT_IMAGE_GENERATION_ORPHANS, PROJECT_LIBRARY_MIGRATION_ACTIVE, PROJECT_LIBRARY_SWITCH_LOCK, PROJECT_LIFECYCLE_LOCKS, PROJECT_LIFECYCLE_LOCKS_GUARD, PROJECT_MEDIA_ADAPTER_PROJECT, PROJECT_MEDIA_ADAPTER_SCHEME, PROJECT_MEDIA_IMPORT_ACCEPTING, PROJECT_MEDIA_IMPORT_EXECUTOR, PROJECT_MEDIA_IMPORT_FUTURES, PROJECT_MEDIA_IMPORT_MAX_PENDING, PROJECT_MEDIA_IMPORT_SHUTTING_DOWN, PROJECT_MEDIA_IMPORT_TASKS, PROJECT_MEDIA_IMPORT_TASKS_LOCK, PROJECT_VIDEO_CANDIDATE_INCOMPATIBLE_KEY, PROJECT_VIDEO_GENERATION_FUTURES, PROJECT_VIDEO_GENERATION_ORPHANS, PROMPT_BUILTIN_CATEGORY_IDS, PROMPT_TEMPLATE_EN, PROMPT_TEMPLATE_PATHS, PROVIDER_ID_RE, PUBLIC_BASE_URL, PUBLIC_MEDIA_BASE_URL, PhotoshopRoundTripCreateRequest, PhotoshopRoundTripReturnRequest, ProjectAgentExecuteRequest, ProjectAgentPlanRequest, ProjectConversationMessageRequest, ProjectCreateRequest, ProjectHistoryDeleteRequest, ProjectImageGenerationRequest, ProjectLibraryChangeRequest, ProjectLocalEditRequest, ProjectMediaImportActive, ProjectMediaImportRequest, ProjectOriginalDeleteRequest, ProjectUpdateRequest, ProjectVideoGenerationRequest, PromptLibraryBatchDeleteRequest, PromptLibraryCategoryRequest, PromptLibraryItemRequest, PromptLibraryRequest, QUEUE, QUEUE_LOCK, QUIET_ACCESS_PATHS, QUIET_ACCESS_PREFIXES, QuietAccessLogFilter, REMOVED_PROVIDER_IDS, RESPONSES_POLL_INTERVAL, RESPONSES_POLL_MAX_SECONDS, RESPONSES_REJECT_STATUSES, RUNNINGHUB_DEFAULT_APPS, RUNNINGHUB_DEFAULT_BASE_URL, RUNNINGHUB_DEFAULT_IMAGE_MODELS, RUNNINGHUB_DEFAULT_VIDEO_MODELS, RUNNINGHUB_ENTRY_MODEL_RE, RUNNINGHUB_FALLBACK_CHAT_MODELS, RUNNINGHUB_FILE_HOST_REWRITES, RUNNINGHUB_LLM_BASE_URL, RUNNINGHUB_LLM_MODELS_URLS, RUNNINGHUB_MODEL_ENDPOINT_ALIASES, RUNNINGHUB_OPENAPI_BASE_URL, RUNNINGHUB_THUMBNAIL_EXTS, RUNTIME_ROOT, RunningHubSubmitRequest, RunningHubUploadAssetRequest, SEED_UINT32_MAX, SHARED_FOLDERS_LOCK, SHARED_MEDIA_EXTS, SHARED_SCAN_MAX_ENTRIES, STATIC_DIR, STATIC_PROMPT_TEMPLATE_MD, STATIC_RUNNINGHUB_API_PROVIDERS_FILE, STATIC_RUNNINGHUB_DIR, STATIC_RUNNINGHUB_MODEL_REGISTRY_FILE, STATIC_RUNNINGHUB_THUMBNAIL_DIR, STORAGE_IMAGE_EXTS, SUPPORTED_IMAGE_REQUEST_MODES, SUPPORTED_PROVIDER_PROTOCOLS, SYSTEM_PROMPT, SharedFolderImport, SharedFolderRegister, SmartCanvasGroupExportItem, SmartCanvasGroupExportRequest, TEXT_ATTACHMENT_EXTS, TempShUploadRequest, TestConnectionPayload, UPDATE_LOCK, VIDEO_POLL_TIMEOUT, VIDEO_PROMPT_MAX_LENGTH, VIDEO_TASK_FAILURE_STATUSES, VIDEO_TASK_SUCCESS_STATUSES, VIDEO_URL_KEYS, VOLCENGINE_ARK_ASSET_HOST, VOLCENGINE_ARK_ASSET_REGION, VOLCENGINE_ARK_ASSET_SERVICE, VOLCENGINE_ARK_ASSET_VERSION, VOLCENGINE_DEFAULT_BASE_URL, VOLCENGINE_DEFAULT_PROJECT_NAME, VOLCENGINE_DEFAULT_REGION, VOLCENGINE_MAX_EDGE, VOLCENGINE_MIN_EDGE, VOLCENGINE_MIN_PIXELS, VOLCENGINE_RATIO_CHOICES, XLSX_IMAGE_EXTS, _AGENT_FORBIDDEN_VALUE_TOKENS, _AGENT_IMAGE_PARAM_SCHEMA, _AGENT_PROMPT_PARAM_SCHEMA, _AGENT_TASK_INPUT_KEYS, _AGENT_VIDEO_PARAM_SCHEMA, _DOUBLE_EXT_MEDIA, _DOUBLE_EXT_RE, _MODELSCOPE_CONFIGURED_CHAT_MODELS, _agent_clean_params, _agent_execution_tasks_from_plan, _agent_find_task_node, _agent_forbidden_value, _agent_history_output, _agent_int_param, _agent_memory_task_active, _agent_nodes_by_id, _agent_normalize_candidates, _agent_normalize_task, _agent_plan_from_node, _agent_plan_hash, _agent_plan_response, _agent_public_task, _agent_recovery_payload, _agent_task_canonical, _agent_task_layout, _agent_task_specs, _app_main, _collect_video_url, _conversation_pending_task_keys_from_canvas, _ensure_photoshop_loopback, _local_upload_display_name, _local_upload_folder_node, _local_upload_kind_ext, _local_upload_rel_path, _local_upload_safe_file_stem, _local_upload_safe_folder_name, _offline_local_edit_adapter, _photoshop_protocol_error, _photoshop_public_request, _photoshop_reset_processing, _photoshop_validate_display_name, _photoshop_validate_media_type, _responses_wrap, _rh_field_cmp, _rh_natural_cmp, _safe_asset_tag, _sniff_image_ext, _sniff_image_ext_bytes, _storage_abs_path, _volc_hmac, _xlsx_cell_text, _xlsx_join_text, _xlsx_shared_strings, _xlsx_sheet_paths, _xml_local_name, _yuli_model_norm, acquire_fly_import_slot, agnes_video_dimensions, agnes_video_frame_count, antigravity_cli_winget_candidates, apimart_avatar_asset_type, apimart_size_resolution, apimart_upload_file_payload, apimart_upload_payload_from_bytes, apimart_upload_post, apimart_upload_raw_file_payload, apimart_veo31_aspect, apimart_veo31_duration, apimart_veo31_model, apimart_veo31_resolution, apimart_video_duration, apimart_video_size, apply_agnes_model_defaults, apply_locked_recommended_model_rules, apply_runninghub_system_thumbnails, apply_trusted_asset_prompt_index, asset_library_media_kind, asset_library_safe_extension, avatar_platform_for_provider, banana_image_request_params, banana_model_family, bearer_auth_value, build_image_param_fields, builtin_prompt_templates, canvas_asset_downloadable_url, canvas_asset_kind, canvas_asset_name, canvas_asset_url_value, canvas_has_pending_conversation, canvas_node_title, canvas_record, canvas_video_camera_fixed, canvas_video_project_media_values, chat_prompt_size_override, chat_provider_supports_audio, chat_requested_image_count, chat_split_parallel_prompts, chat_system_prompt, classify_upstream_model, codex_chat_prompt, codex_decode_output, codex_model_for_exec, codex_models_payload, codex_postprocess_image_to_requested_size, codex_timeout, collect_local_media_urls, compress_data_url_image, content_type_for_path, conversation_error_message, conversation_node_by_id, conversation_task_response, conversation_task_snapshot, current_app_version, decode_utf16_auto, decode_wsl_output, defaultPromptTemplateCategories, default_api_providers, detect_image_request_mode, display_title, effective_image_request_mode, effective_protocol, ensure_project_image_generation_executor, ensure_project_media_import_executor, ensure_public_generation_target, ensure_same_origin_request, env_quote, extract_apimart_asset_url, extract_apimart_avatar_asset_uri, extract_canvas_assets, extract_image, extract_image_flexible, extract_images, extract_prompt_template_section, extract_task_id, extract_task_id_from_text, fetch_remote_media_bytes, filename_from_media_url, find_asset_category, find_asset_item_in_library, find_prompt_library, forget_project_conversation_ready_result, forget_project_conversation_task, friendly_chat_error_detail, friendly_image_error_detail, friendly_validation_error, gemini_cli_chat_prompt, gemini_cli_image_size_instruction, gemini_cli_image_timeout, gemini_cli_model, gemini_cli_models_payload, gemini_cli_parse_stdout, gemini_cli_reference_note, gemini_cli_text_from_raw, gemini_cli_timeout, gemini_image_config, gemini_model_name, gemini_supported_aspect_ratio, generated_image_filename, gpt_image_2_size_error_message, gpt_image_2_size_exceeds_supported, gpt_image_2_skill_access_token, gpt_image_2_skill_auth_json, gpt_image_2_skill_failure_message, gpt_image_2_skill_model_arg, gpt_image_2_skill_prompt_arg, gpt_image_2_skill_size_arg, heuristic_agent_decision, httpx_request_with_transient_retries, humanize_video_task_failure, image_has_alpha, image_path_to_data_url, image_payload_from_string, image_provider_model_ids, image_references, image_task_data, image_task_fail_reason, image_task_status, images_api_unsupported, invalid_video_image_preview, is_agnes_provider, is_antigravity_cli, is_apimart_provider, is_apimart_veo31_model, is_codex_provider, is_fhl_provider, is_gemini_cli_provider, is_gemini_provider, is_gpt_image_2_model, is_image_reference, is_jimeng_provider, is_lingjing_provider, is_private_asset_url, is_runninghub_provider, is_transient_tls_error, is_video_preview_file, is_volcengine_provider, is_volcengine_seedance2_model, is_volcengine_seedream_model, is_yuli_provider, iter_canvas_asset_values, jimeng_append_model_resolution_args, jimeng_clean_wsl_stderr, jimeng_collect_media_values, jimeng_extract_json, jimeng_failure_reason, jimeng_image_model_version, jimeng_image_resolution, jimeng_login_qr_from_text, jimeng_login_text, jimeng_normalize_image_model, jimeng_output_values, jimeng_parse_version, jimeng_pending_payload, jimeng_poll_seconds, jimeng_queue_info, jimeng_ratio_from_size, jimeng_submit_id, jimeng_transition_duration, jimeng_video_duration, jimeng_video_duration_range, jimeng_video_model_version, jimeng_video_ratio_arg, jimeng_video_ref_role, jimeng_video_ref_url, jimeng_video_resolution, jimeng_video_resolution_arg, latest_chat_image_refs, lingjing_openai_video_model, load_static_runninghub_provider, locked_recommended_provider_rule, log_net_error, looks_like_gemini_image_model, looks_like_generated_image_url, looks_like_html_response, looks_like_image_base64, looks_like_image_media_url, looks_like_vision_chat_model, mask_secret, merge_default_api_providers, merge_runninghub_entry_overlay, merge_runninghub_provider_with_static, merge_runninghub_system_entries, migrate_asset_item_registrations, model_list, model_list_from_values, modelscope_image_api_root, modelscope_size, normalize_available_models, normalize_canvas_color, normalize_canvas_kind, normalize_endpoint_override, normalize_gpt_image_2_size, normalize_image_request_mode, normalize_local_image_path, normalize_model_list, normalize_model_name_map, normalize_model_protocols, normalize_ms_loras, normalize_prompt_category_id, normalize_prompt_template_categories, normalize_provider, normalize_recommended_models, normalize_runninghub_entries, normalize_runninghub_entry, normalize_seed_uint32, normalize_volcengine_size, openai_compat_root_for_probe, origin_from_url, parse_agent_decision, parse_error_payload_text, parse_gpt_image_2_skill_output, parse_prompt_template_markdown, parse_size_pair, parse_upstream_models, post_openai_responses, post_openai_responses_stream, preferred_chat_model, preserve_runninghub_hidden_overrides, probe_openai_compat_bearer_endpoint, probe_openai_models_endpoint, probe_volcengine_auto_detect, probe_volcengine_task_endpoint, project_agent_execution_lock, project_agent_node, project_canvas_video_payload, project_conversation_ready_result, project_has_active_image_generation, project_has_active_media_import, project_has_active_video_generation, project_has_recoverable_conversation_sidecars, project_image_request, project_lifecycle_lock, project_media_adapter_project_id, project_media_adapter_reference, project_media_adapter_reference_from_url, project_record, project_video_candidate_preflight_incompatible_parameters, project_video_mark_candidate_incompatible, project_video_request, prompt_template_category, prompt_template_markdown_path, protocol_from_payload, provider_key_env, provider_protocol, provider_supports_avatar, public_media_url_suffix, public_project_media_import_task, read_text_attachment, read_xlsx_attachment, recommend_upstream_models, reload_env_globals, remember_project_conversation_ready_result, require_canvas_video_project_media_scope, require_online_image_project_media_scope, require_project_media_adapter_scope, require_project_media_values_scope, reserve_project_image_task, reserve_project_video_task, responses_image_size_instruction, responses_no_image_detail, responses_output_text_image, responses_proxy_tool_size, rewrite_runninghub_file_url, rh_default_value, rh_field_indexes, rh_field_kind, rh_field_role, rh_is_seed_like_name, rh_random_field_value, rh_sort_fields, route_openai_image_request, runninghub_apply_schema_defaults, runninghub_aspect_from_size, runninghub_configured_video_model_definition, runninghub_endpoint_alias_for_model, runninghub_endpoint_url, runninghub_entry_config_from_model, runninghub_entry_id, runninghub_extract_image, runninghub_extract_outputs, runninghub_extract_task_id, runninghub_fail_reason, runninghub_is_image_to_video, runninghub_model_display_name, runninghub_model_id, runninghub_openapi_base_url, runninghub_openapi_url, runninghub_output_ext, runninghub_query_status, runninghub_registry_fallback, runninghub_registry_items_from_raw, runninghub_registry_model_from_id, runninghub_registry_payload, runninghub_resolution_from_size, runninghub_schema_field, runninghub_schema_field_required, runninghub_schema_options, runninghub_schema_value, runninghub_size_for_aspect, runninghub_task_endpoint, runninghub_video_incompatible_parameters, runninghub_wallet_key_env, safe_user_id, sanitize_asset_name, sanitize_export_filename, sanitize_runninghub_node_info_list, sanitize_seed_like_values, scan_shared_tree, seed_system_prompt_library, selected_model, shared_child_abs, shared_folder_abs, shared_resolve_register, snap_size_to_multiple, sort_asset_library_items, sse_event, static_html_response, static_runninghub_thumbnail_url, storage_file_item, strip_auth_scheme, text_delta_from_chat_chunk, text_from_chat_response, unwrap_apimart_response, upload_video_to_litterbox, upload_video_to_temp_sh, upstream_model_headers, upstream_models_url, valid_apimart_video_image_input, valid_video_image_input, validate_generated_image, validate_generated_video, validate_project_media_source, video_output_urls, video_task_url_candidates, volcengine_access_key_env, volcengine_content_role, volcengine_default_model_payload, volcengine_secret_key_env, volcengine_sign_v4_headers, volcengine_task_probe_url, volcengine_video_duration, volcengine_video_prompt_text, volcengine_video_resolution, windows_path_to_wsl, wsl_path_to_windows, xlsx_embedded_image_data_urls, yuli_is_veo_openai_model, yuli_openai_model_name, yuli_openai_size, yuli_video_seconds)
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